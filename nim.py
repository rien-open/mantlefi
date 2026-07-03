"""MantleFi — NIM chat backend (stdlib only; NO `openai` package).

NVIDIA NIM exposes an OpenAI-compatible REST endpoint, so we call it with plain urllib —
keeping MantleFi zero-dependency and honoring the "no unvetted third-party package" posture
(see CLAUDE.md). This is the ONLY part of MantleFi that needs a key: scan/judge/token work
without it. The LLM here only routes/synthesizes; it never originates a number (see agent.py).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error

import config


class NimError(Exception):
    """NIM call failed after retries/fallback. Caller degrades gracefully, never guesses."""


class NimKeyMissing(NimError):
    """No NIM API key — agent mode unavailable (scan/judge/token still work keyless)."""


_last_call: dict = {}   # backend name -> monotonic timestamp (per-provider RPM spacing)


def _load_env() -> None:
    """Minimal stdlib KEY=VALUE reader for mantlefi/.env (no python-dotenv).
    Already-set environment variables win (shell/CI override the file)."""
    p = config.MANTLEFI_ENV
    try:
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        return


def _backend(name: str | None):
    """Resolve a backend name → (config dict, canonical name). Unknown/None → the default."""
    name = name or config.LLM_DEFAULT_BACKEND
    b = config.LLM_BACKENDS.get(name)
    if b is None:
        name, b = config.LLM_DEFAULT_BACKEND, config.LLM_BACKENDS[config.LLM_DEFAULT_BACKEND]
    return b, name


def _key_for(env_key: str) -> str:
    _load_env()
    return os.environ.get(env_key, "").strip()


def _api_key() -> str:
    """The NIM key (kept for back-compat / callers that read it directly)."""
    key = _key_for(config.NIM_ENV_KEY)
    if not key:
        raise NimKeyMissing(
            f"{config.NIM_ENV_KEY} が未設定です。{config.MANTLEFI_ENV} に追記するか "
            f"環境変数で渡してください（.env.example 参照）。scan/judge/token は鍵なしで動きます。")
    return key


def _rate_limit(name: str, delay: float) -> None:
    """Space calls to one backend ≥ `delay` apart (per-provider free-tier RPM cap)."""
    now = time.monotonic()
    last = _last_call.get(name, 0.0)
    if now - last < delay:
        time.sleep(delay - (now - last))
    _last_call[name] = time.monotonic()


def _post(model: str, messages: list, key: str, base_url: str, chat_path: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": config.NIM_TEMPERATURE,
        "max_tokens": config.NIM_MAX_TOKENS,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url + chat_path, data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": config.HTTP_UA,
        })
    with urllib.request.urlopen(req, timeout=config.NIM_TIMEOUT) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"]


def chat(messages: list, model: str | None = None, backend: str | None = None) -> str:
    """One chat completion on the chosen backend ("nim" default | "groq"). Rate-limited per
    provider; falls back PRIMARY→FALLBACK within the backend, then cross-falls to NIM if a non-NIM
    backend is keyless or fully fails (the surface degrades, never breaks).

    Raises NimKeyMissing if even NIM has no key; NimError if every attempt fails.
    """
    b, name = _backend(backend)
    key = _key_for(b["env_key"])
    if not key:
        if name != "nim":
            return chat(messages, model=None, backend="nim")   # keyless non-NIM → glm path
        raise NimKeyMissing(
            f"{b['env_key']} が未設定です。{config.MANTLEFI_ENV} に追記するか環境変数で渡してください"
            f"（.env.example 参照）。scan/judge/token は鍵なしで動きます。")

    primary = model or b["primary"]
    chain = [primary]
    if b["fallback"] and primary != b["fallback"]:
        chain.append(b["fallback"])

    last = None
    for m in chain:
        for attempt in range(2):   # 1 retry per model
            _rate_limit(name, b["rpm_delay"])
            try:
                return _post(m, messages, key, b["base_url"], b["chat_path"])
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    if attempt == 0:
                        time.sleep(3)      # rate-limited → back off, retry same model once
                        continue
                    break                  # still 429 → fall through to the next model (don't raise)
                if e.code in (404, 410):
                    break                  # model removed from the free catalog (glm-5.1 went 410
                                           # on 2026-07-03) → don't retry, fall through to the next model
                if 500 <= e.code < 600:
                    break                  # server error → try next model
                raise NimError(f"{name} HTTP {e.code}: {e.reason}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                break                      # timeout/network → try next model
    if name != "nim":
        return chat(messages, model=None, backend="nim")   # whole non-NIM chain failed → glm path
    raise NimError(f"NIM failed across models {chain}: {last}")
