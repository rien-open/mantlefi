#!/usr/bin/env python3
"""MantleFi — Telegram surface (面).

Lets anyone use MantleFi by messaging a bot — no terminal needed. Commands /scan /judge
/token run the deterministic engine (NO key); free-text messages go to the agent (NIM key).

Plain Telegram Bot API over stdlib urllib (no telethon / no python-telegram-bot) — keeps
MantleFi zero-dependency and avoids the telethon 2-IP session pitfall (see
feedback_telethon_session_ip_lock). Single-process long-poll; one user at a time is fine
for a demo. Run:  put TELEGRAM_BOT_TOKEN in mantlefi/.env, then  python3 telegram_bot.py
"""
from __future__ import annotations

import html
import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse

import config
import nim          # reused only for its .env loader (no key read at import time)


def _web_line() -> str:
    return f"\n👉 {config.WEB_CHAT_URL}" if config.WEB_CHAT_URL else ""


WELCOME = (
    "📰 MantleFi デイリー（朝刊）へようこそ。\n\n"
    "毎朝この場所に、Mantle DeFi の利回りの「今朝のまとめ」が届きます"
    "（本物の実利回りか・配布報酬頼みか・チェーンで確認した事実つき）。"
    "登録は不要で、このやり取りだけで購読が完了します。\n\n"
    "💬 利回りを詳しく質問したい時は、web のチャットへどうぞ"
    "（会話で深掘りでき、全数字に出典がつきます）。売買の助言はしません。"
)

SUBSCRIBED = (
    "✅ 購読しました。毎朝この場所にデイリー（朝刊）をお届けします。\n\n"
    "💬 利回りを詳しく質問したい時は web のチャットへ。会話で深掘りできます（売買の助言はしません）。"
)


# ---------------------------------------------------------------- message routing (pure)
def handle(text: str, chat_id=None):
    """Telegram is the DAILY-DIGEST surface only — the digest comes to you each morning. Any incoming
    message confirms the subscription (the caller records chat_id so monitor.py can push here) and
    points to the web chat for questions. Conversational Q&A deliberately lives on ONE consistent,
    faster surface (the web), so there is no inferior second chat to drift from it. Returns
    (reply, mono=False) — always plain prose."""
    t = (text or "").strip().lower()
    if not t or t.startswith("/start") or t.startswith("/help"):
        return (WELCOME + _web_line(), False)
    return (SUBSCRIBED + _web_line(), False)


def chunks(text: str, limit: int = config.TELEGRAM_MSG_LIMIT):
    """Split into Telegram-safe pieces (<4096), preferring line boundaries."""
    text = text if (text or "").strip() else "(空の応答)"
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                 # a single over-long line: hard-split
            if cur:
                out.append(cur); cur = ""
            out.append(line[:limit]); line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur); cur = line
        else:
            cur = line if not cur else cur + "\n" + line
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- telegram API (urllib)
def _token() -> str:
    nim._load_env()
    t = os.environ.get(config.TELEGRAM_ENV_KEY, "").strip()
    if not t:
        raise RuntimeError(f"{config.TELEGRAM_ENV_KEY} が未設定です。@BotFather で bot を作り、"
                           f"その token を {config.MANTLEFI_ENV} に入れてください。")
    return t


def _api(method: str, token: str, params: dict):
    url = config.TELEGRAM_API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": config.HTTP_UA})
    with urllib.request.urlopen(req, timeout=config.TELEGRAM_POLL_TIMEOUT + 10) as r:
        return json.loads(r.read().decode("utf-8"))


def send(token: str, chat_id, text: str, mono: bool = False):
    # tables go in a <pre> monospace block (HTML parse_mode) so columns align on a phone;
    # escaping <>& expands a bit, so chunk smaller when mono to stay under the 4096 cap.
    limit = 3500 if mono else config.TELEGRAM_MSG_LIMIT
    for chunk in chunks(text, limit):
        params = {"chat_id": chat_id, "text": chunk}
        if mono:
            params["text"] = f"<pre>{html.escape(chunk)}</pre>"
            params["parse_mode"] = "HTML"
        try:
            _api("sendMessage", token, params)
        except Exception as e:   # noqa: BLE001 — never let one bad send kill the loop
            print("send failed:", e)


def _record_chat(chat_id, msg) -> None:
    """Remember chat_ids that message the bot so the heartbeat monitor can push proactively
    (a push is not a reply → it needs a destination). Best-effort: never break message handling."""
    try:
        chat = msg.get("chat", {})
        name = chat.get("username") or chat.get("first_name") or ""
        p = config.KNOWN_CHATS_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        data[str(chat_id)] = {"name": name, "last_seen": time.strftime("%Y-%m-%d %H:%M:%S")}
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:   # noqa: BLE001 — bookkeeping must never crash the bot
        print("record_chat failed:", e)


def main():
    try:
        token = _token()
    except RuntimeError as e:
        print(e)
        return
    print("MantleFi Telegram bot 起動。Ctrl-C で停止。")
    offset = None
    while True:
        try:
            params = {"timeout": config.TELEGRAM_POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset
            resp = _api("getUpdates", token, params)
        except Exception as e:   # noqa: BLE001 — transient network: back off and retry
            print("poll error:", e)
            time.sleep(3)
            continue
        for upd in resp.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            if chat_id is None:
                continue
            _record_chat(chat_id, msg)   # remember where to push the heartbeat report
            try:
                reply, mono = handle(text, chat_id)
            except Exception as e:   # noqa: BLE001 — surface error, keep serving
                reply, mono = f"⚠ エラー: {e}", False
            send(token, chat_id, reply, mono)


if __name__ == "__main__":
    main()
