#!/usr/bin/env python3
"""MantleFi — tiny stdlib HTTP backend for the web 顔 (and a one-command local demo).

Routes:
  GET  /                 → the static web face (web/index.html). Serving it here makes the
                           local demo same-origin (no CORS), so `python3 serve.py` + open the
                           printed URL = a working live chat.
  GET  /health           → {"ok": true, ...}   (the face pings this to show "接続済み")
  GET  /latest           → the morning digest as JSON (monitor.py writes data/latest.json)
  GET  /daily            → the CORRECTED full Mantle snapshot (facts.daily_data) — what the web
                           デイリー renders LIVE so it never drifts from the chat (Aave: chain base +
                           real reward + gross TVL). Engine-only, no key, cached.
  GET  /run-crew         → SSE stream: run the monitor crew LIVE (deep research), one event per
                           stage (scan→pick→investigate→verdict→edit→done) so the build is visible.
                           Engine-owned numbers (no-fab); Telegram push suppressed; refreshes latest.json.
  POST /ask {q,history?}  → agent.run(q, history) → {chat, full, answer, verdict}

Invariants (same posture as the rest of MantleFi):
  - stdlib only (http.server) — zero third-party deps.
  - The ONLY endpoint that needs a key is /ask (agent → NIM). /, /health, /latest are keyless.
  - no-fabrication is untouched: /ask just wraps agent.run; numbers/verdicts stay engine-owned.
  - Binds to 127.0.0.1 by default — NOT publicly exposed. Public binding/port is a deploy step
    the user performs (plan §10「外向き操作は rien 実行」); set MANTLEFI_SERVE_HOST=0.0.0.0 there.
  - Free-tier safety: per-IP sliding-window rate-limit + ONE global lock so concurrent /ask calls
    queue (the free NIM tier is a single lane anyway) + an opt-in short scan cache (data_sources).

Run:  python3 serve.py            # then open http://127.0.0.1:8787
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # run from anywhere

import config
import data_sources
import agent
import facts
import nim


HOST = os.environ.get("MANTLEFI_SERVE_HOST", config.SERVE_HOST)
# Hosting platforms (Render etc.) inject the port as PORT — honor it so the start command stays
# a bare `python3 serve.py`. MANTLEFI_SERVE_PORT still wins for explicit local overrides.
PORT = int(os.environ.get("MANTLEFI_SERVE_PORT") or os.environ.get("PORT") or str(config.SERVE_PORT))
# Behind ONE reverse proxy (a public deploy), client_address is the proxy — every visitor would
# share a single rate bucket. MANTLEFI_TRUST_XFF=1 opts in to X-Forwarded-For there. Default OFF:
# a directly-reachable server must never honor XFF (it is client-supplied = a rate-limit bypass).
TRUST_XFF = os.environ.get("MANTLEFI_TRUST_XFF", "") == "1"

_ASK_LOCK = threading.Lock()        # serialize agent.run — the free NIM tier is one lane
_HITS: dict = {}                    # ip -> [recent request monotonic timestamps]
_HITS_LOCK = threading.Lock()
_DAILY_CACHE = {"t": 0.0, "v": None}   # GET /daily: the corrected full snapshot (a few chain reads)
_DAILY_LOCK = threading.Lock()
_CREW_LOCK = threading.Lock()          # only ONE live crew run (GET /run-crew SSE) at a time
_CREW_LAST = {"t": 0.0}                # monotonic of the last crew start (cooldown)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _rate_ok(ip: str) -> bool:
    """Sliding-window per-IP limiter: ≤ SERVE_RATE_MAX requests per SERVE_RATE_WINDOW seconds."""
    now = time.monotonic()
    with _HITS_LOCK:
        q = [t for t in _HITS.get(ip, []) if now - t < config.SERVE_RATE_WINDOW]
        if len(q) >= config.SERVE_RATE_MAX:
            _HITS[ip] = q
            return False
        q.append(now)
        _HITS[ip] = q
        return True


def _clean_history(h):
    """Accept [[q,a], ...] (recent turns) for conversation memory; drop anything malformed.
    agent.run iterates history as (q, a) pairs, so we hand it exactly that (capped/trimmed)."""
    out = []
    if isinstance(h, list):
        for item in h[-config.TELEGRAM_HISTORY_TURNS:]:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                q, a = item
                if isinstance(q, str) and isinstance(a, str):
                    out.append((q, a[:config.TELEGRAM_HISTORY_ANS_CAP]))
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "MantleFi/0.1"
    protocol_version = "HTTP/1.1"
    _CTYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "svg": "image/svg+xml", "webp": "image/webp", "gif": "image/gif", "ico": "image/x-icon",
               "js": "text/javascript; charset=utf-8",                    # sw.js (PWA)
               "webmanifest": "application/manifest+json; charset=utf-8"}  # manifest.webmanifest (PWA)

    # ---- low-level response helpers (always set Content-Length for keep-alive) ----
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send(self, code: int, body, ctype: str) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):   # one tidy line, not the noisy default
        sys.stderr.write(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}\n")

    def _ip(self) -> str:
        if TRUST_XFF:
            # Take the LAST hop: the trusted proxy APPENDS the real client IP, while the first
            # token is whatever the client sent (spoofable → unlimited fresh "IPs" = bypass).
            xff = self.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[-1].strip() or self.client_address[0]
        return self.client_address[0]

    # ---- routes ----
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            # crew_running lets a blocked page poll until the ONE global survey finishes, then pull
            # the SAME shared latest.json — so concurrent clickers converge instead of dead-ending.
            # llm = which providers have a key on THIS host (booleans only — a key VALUE is never
            # returned) + the models each surface uses now. Lets us verify from outside whether the
            # Groq quality models are actually live on the deploy (else it silently ran on maverick).
            nim._load_env()
            groq_key = bool(os.environ.get(config.GROQ_ENV_KEY))
            return self._json(200, {"ok": True, "service": "mantlefi", "as_of": _now(),
                                    "crew_running": _CREW_LOCK.locked(),
                                    "llm": {
                                        "groq_key": groq_key,
                                        "nim_key": bool(os.environ.get(config.NIM_ENV_KEY)),
                                        "chat_reads_on": (config.WEB_AGENT_FINAL_MODEL if groq_key
                                                          else config.NIM_MODEL_FALLBACK),
                                        "say_model": config.CHAT_NARRATE_MODEL,
                                        "crew_editor": config.MONITOR_EDITOR_MODEL,
                                        "loop_model": config.GROQ_MODEL_PRIMARY}})
        if path == "/latest":
            return self._latest()
        if path == "/daily":
            return self._daily()
        if path == "/run-crew":
            from urllib.parse import parse_qs, urlparse
            lang = "en" if parse_qs(urlparse(self.path).query).get("lang", ["ja"])[0] == "en" else "ja"
            return self._run_crew(lang)
        if path in ("/", "/index.html"):
            return self._face()
        if "." in path.rsplit("/", 1)[-1] and path.rsplit(".", 1)[-1].lower() in self._CTYPES:
            return self._static(path)          # web/ 直下の画像（mascot.png 等）を配信
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        # Always consume the request body FIRST. An unread body desyncs the next request on a
        # keep-alive (HTTP/1.1) connection ("Bad request syntax"), so even the 404/429 paths must
        # drain it. An oversized body → close the connection rather than try to stay in sync.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > config.SERVE_MAX_BODY:
            self.close_connection = True
            return self._json(413, {"error": "too_large"})
        raw = self.rfile.read(n) if n > 0 else b""

        path = self.path.split("?")[0]
        if path not in ("/ask", "/facts", "/say", "/chat"):
            return self._json(404, {"error": "not_found"})
        if not _rate_ok(self._ip()):
            return self._json(429, {"error": "rate_limited",
                                    "message": "少し待ってからもう一度どうぞ。"})
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "bad_json"})
        q = (data.get("q") or data.get("question") or "").strip()
        if not q:
            return self._json(400, {"error": "empty_question",
                                    "message": "質問を入力してください。"})
        q = q[:config.SERVE_MAX_Q_CHARS]
        # UI language for LLM narration only ("ja" default). Numbers/verdicts are engine-owned and
        # language-independent; deterministic strings are translated client-side (web tr() map).
        lang = "en" if data.get("lang") == "en" else "ja"

        # /facts — the deterministic fast path: engine only, NO LLM, NO lock (returns in ~6s).
        if path == "/facts":
            try:
                return self._json(200, facts.compute(q, data.get("intent")))
            except Exception as e:            # never a 500/stack — degrade honestly
                return self._json(200, {"kind": "error", "message": "取得に失敗しました。",
                                        "error": str(e)[:200]})

        # /say — ONE friendly sentence. Narrate the engine evidence (a/b) or describe (c). Uses
        # the LLM, so it takes the lock (free NIM tier is one lane). Optional polish: the /facts
        # card already stands on its own, so an empty/failed /say is harmless.
        if path == "/say":
            evidence = data.get("evidence")
            with _ASK_LOCK:
                try:
                    text = facts.narrate(q, evidence, lang) if evidence else facts.describe(q, lang)
                except Exception as e:
                    return self._json(200, {"text": "", "error": str(e)[:200]})
            return self._json(200, {"text": text})

        # /chat — the REAL web research agent (phase-2 of the chat UI; phase-1 /facts already showed
        # the engine card). A ReAct loop on Groq + glm final sentence, WITH conversation memory. Its
        # extraction is locked to the deterministic router (facts.seed_for → seed.bind) so a fast model
        # can't corrupt the symbol (USDT0→USDT); the agent does the reasoning + narration on top.
        # Numbers/verdicts stay engine-owned (no-fab). require_tool forces grounding for resolved Qs.
        if path == "/chat":
            history = _clean_history(data.get("history"))
            # A「〜とは？」about a KNOWN token/concept is a DEFINITION → its FIXED deterministic blurb,
            # NEVER the free agent (a weak model drifts/hallucinates it, e.g. GHO→「原稿担保金利令状」).
            # "" for vague/novel/follow-up questions → falls through to the memory-carrying agent below.
            blurb = facts.describe_if_known(q)
            if blurb:
                return self._json(200, {"chat": blurb, "full": "", "answer": blurb, "say": blurb,
                                        "verdict": "", "ask": "", "chips": [], "describe": True})
            try:
                seed, require_tool = facts.seed_for(q)
            except Exception:                 # routing hiccup → let the agent run free (still safe)
                seed, require_tool = None, False
            with _ASK_LOCK:                   # one agent.run at a time (free LLM tier is one lane)
                try:
                    res = agent.run(q, history=history,
                                    loop_backend=config.WEB_AGENT_LOOP_BACKEND,
                                    final_backend=config.WEB_AGENT_FINAL_BACKEND,
                                    final_model=config.WEB_AGENT_FINAL_MODEL,
                                    seed=seed, require_tool=require_tool, lang=lang)
                except Exception as e:        # never a 500/stack — degrade honestly
                    return self._json(200, {"chat": "⚠ いま調べられませんでした。少し待ってもう一度どうぞ。",
                                            "full": "", "answer": "", "say": "", "verdict": "", "error": str(e)[:200]})
            return self._json(200, {"chat": res.get("chat", ""), "full": res.get("full", ""),
                                    "answer": res.get("answer", ""), "say": res.get("say", ""),
                                    "verdict": res.get("verdict", ""),
                                    "ask": res.get("ask", ""), "chips": res.get("chips", [])})

        # /ask — the full ReAct agent (kept for the CLI/debug path and as a fallback).
        history = _clean_history(data.get("history"))
        with _ASK_LOCK:                       # one agent.run at a time (free NIM tier = one lane)
            try:
                res = agent.run(q, history=history,
                                loop_backend=config.WEB_AGENT_LOOP_BACKEND,
                                final_backend=config.WEB_AGENT_FINAL_BACKEND,
                                final_model=config.WEB_AGENT_FINAL_MODEL, lang=lang)
            except Exception as e:            # never return a 500/stack — degrade honestly
                return self._json(200, {"chat": "⚠ いま調べられませんでした。少し待ってもう一度どうぞ。",
                                        "full": "", "answer": "", "verdict": "",
                                        "error": str(e)[:200]})
        return self._json(200, {"chat": res.get("chat", ""), "full": res.get("full", ""),
                                "answer": res.get("answer", ""), "verdict": res.get("verdict", ""),
                                "ask": res.get("ask", ""), "chips": res.get("chips", [])})

    def _daily(self):
        """The corrected full Mantle DeFi snapshot (facts.daily_data) — what the web デイリー renders
        LIVE so it can never drift from the chat. Engine-only, no key, no LLM. Cached SERVE_CACHE_TTL
        seconds (it does a handful of chain reads) so repeated page loads don't recompute. Honest
        degrade on failure (the web keeps its baked fallback)."""
        now = time.monotonic()
        with _DAILY_LOCK:
            if _DAILY_CACHE["v"] is not None and now - _DAILY_CACHE["t"] < config.SERVE_CACHE_TTL:
                return self._json(200, _DAILY_CACHE["v"])
        try:
            data = facts.daily_data()
        except Exception as e:            # never a 500 — the web falls back to its baked snapshot
            return self._json(200, {"available": False, "error": str(e)[:200]})
        data["available"] = True
        with _DAILY_LOCK:
            _DAILY_CACHE.update(t=now, v=data)
        return self._json(200, data)

    def _latest(self):
        p = config.MONITOR_LATEST_PATH
        if not p.exists():
            return self._json(200, {"available": False,
                                    "message": "まだ定点観測が走っていません（monitor.py を実行してください）。"})
        try:                              # parse INSIDE try; send OUTSIDE — so a client disconnect
            data = json.loads(p.read_text(encoding="utf-8"))   # (BrokenPipe, an OSError) during the
        except (OSError, ValueError):     # write isn't misread as a file error and re-sent on the
            data = {"available": False, "message": "latest.json を読めませんでした。"}   # dead socket.
        return self._json(200, data)

    def _sse_open(self):
        """Open a Server-Sent Events stream. Connection: close → no Content-Length needed; the body
        ends when we close, and the browser's EventSource reads frames until then."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True

    def _sse(self, ev):
        """Write one SSE frame and flush (so the browser sees it immediately). Raises on a
        disconnected client; the caller catches BrokenPipeError to stop streaming."""
        self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _run_crew(self, lang: str = "ja"):
        """SSE: run the monitor crew LIVE, streaming each stage as it happens so the deep research
        is never silent (the 'リアルタイムで作ってる感'). The engine still owns every number/verdict
        (no-fab) — events are just narration of what the crew is doing. Guards: one run at a time +
        a short cooldown (the crew makes many free-tier LLM calls). Telegram push is suppressed
        (web-initiated). On completion latest.json is refreshed so the page's snapshot updates too.
        `lang` (from ?lang=) makes the investigator + editor narrate in that language."""
        now = time.monotonic()
        # The survey is a GLOBAL singleton (one shared latest.json). If one is already running —
        # or started moments ago (cooldown) — reject WITHOUT starting a second run. kind="running"
        # tells the client to wait and then show the SAME shared result (no data race, no dead-end).
        running = (now - _CREW_LAST["t"] < config.SERVE_CREW_COOLDOWN) or not _CREW_LOCK.acquire(blocking=False)
        self._sse_open()
        if running:
            try:
                self._sse({"t": "busy", "kind": "running"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        _CREW_LAST["t"] = now
        try:
            import monitor   # lazy: no import cost/side-effects at server startup
            self._sse({"t": "start", "as_of": _now()})
            # ライブ実行は基準（スナップショット）を進めない＝比較は「毎日の定点チェック」基準のまま
            monitor.main(dry=False, on_event=self._sse, push=False, advance_baseline=False, lang=lang)
        except (BrokenPipeError, ConnectionResetError):
            pass              # client left mid-run; the crew still finishes & writes latest.json
        except Exception as e:   # noqa: BLE001 — never crash the server thread
            try:
                self._sse({"t": "error", "message": "ディープリサーチに失敗しました。", "error": str(e)[:200]})
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            _CREW_LOCK.release()

    def _face(self):
        p = config.SERVE_WEB_DIR / "index.html"
        if not p.exists():
            return self._send(404, "web/index.html が見つかりません。", "text/plain; charset=utf-8")
        return self._send(200, p.read_text(encoding="utf-8"), "text/html; charset=utf-8")

    def _static(self, path):
        # web/ 直下のファイルのみ配信（basename に限定＝パストラバーサル不可）。画像アセット用。
        name = path.split("?")[0].lstrip("/")
        if not name or "/" in name or ".." in name:
            return self._send(404, "not found", "text/plain; charset=utf-8")
        p = config.SERVE_WEB_DIR / name
        if not p.exists() or not p.is_file():
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = name.rsplit(".", 1)[-1].lower()
        return self._send(200, p.read_bytes(), self._CTYPES.get(ext, "application/octet-stream"))


def main():
    data_sources.set_cache_ttl(config.SERVE_CACHE_TTL)   # short scan cache for concurrent /ask
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    shown = "127.0.0.1" if HOST in ("127.0.0.1", "0.0.0.0") else HOST
    print(f"MantleFi 顔バックエンド → http://{shown}:{PORT}   （Ctrl-C で停止）")
    print(f"  GET /  /health  /latest  /daily  /run-crew(SSE)    ·    POST /facts {{q}}  /say {{q,evidence?}}  /ask {{q,history?}}")
    if HOST == "0.0.0.0":
        print("  ⚠ 0.0.0.0 で公開待受中（外部到達可能）。リバースプロキシ/ファイアウォール前提で。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
