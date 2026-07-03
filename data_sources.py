"""MantleFi — read-only data sources. All free, no API key, no third-party code execution.

Every fetcher returns (data, source_url) so the caller can cite the source for every
number. On failure it raises FetchError; the classifier turns that into an honest
"❔ 不明 (insufficient data)" rather than guessing (see CLAUDE.md Kill Switch).
"""
from __future__ import annotations

import urllib.request
import urllib.error
import json
import time

import config


class FetchError(Exception):
    """Raised when a free endpoint cannot be read. Caller must abstain, not fabricate."""


def _get_json(url: str, retries: int = 3) -> dict:
    """GET JSON with retry+backoff on HTTP 429 (rate limit). A 429 is a transient
    'could not look', NOT 'absent' — callers must not treat the resulting failure as
    a clean zero result (see lessons: false-negative from rate-limit)."""
    req = urllib.request.Request(url, headers={"User-Agent": config.HTTP_UA})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status} for {url}")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))   # 3s, 6s backoff
                continue
            raise FetchError(f"fetch failed for {url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(1)
                continue
            raise FetchError(f"fetch failed for {url}: {e}") from e
    raise FetchError(f"fetch failed for {url}: {last}")


# --- optional in-process TTL cache (OFF by default = on-demand fresh for the CLI/monitor) ---
# serve.py turns this on (set_cache_ttl) so concurrent web questions don't each re-pull
# DefiLlama's full pools payload. TTL == 0 → every call fetches live (no behavior change for the
# CLI). Failures are never cached (a FetchError propagates and the next call retries live).
_CACHE_TTL = 0.0
_CACHE: dict = {}


def set_cache_ttl(seconds: float) -> None:
    """Enable a short in-process cache for the heavy list fetchers (serve.py uses this)."""
    global _CACHE_TTL
    _CACHE_TTL = max(0.0, float(seconds))


def _cached(key: str, produce):
    if _CACHE_TTL <= 0:
        return produce()
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = produce()          # may raise FetchError → not cached, propagates
    _CACHE[key] = (now, val)
    return val


# --- DefiLlama: yield pools (real-yield vs emission) ---
def mantle_yield_pools() -> tuple[list[dict], str]:
    """All Mantle yield pools with apy/apyBase/apyReward/tvlUsd/volume."""
    def _produce():
        url = config.DEFILLAMA_YIELDS_POOLS
        payload = _get_json(url)
        rows = payload.get("data", [])
        mantle = [p for p in rows if p.get("chain") == config.MANTLE_CHAIN_NAME]
        if not mantle:
            raise FetchError("no Mantle pools in yields payload")
        return mantle, url
    return _cached("yield_pools", _produce)


def protocol_directory() -> tuple[dict, str]:
    """{slug: {"name", "url", "logo"}} for every DefiLlama protocol — official venue site + logo,
    sourced (never hand-typed). Lets each pool link out to the actual DEX/protocol on Mantle and
    show its real logo. Caller filters to the slugs it needs."""
    def _produce():
        url = config.DEFILLAMA_PROTOCOLS
        arr = _get_json(url)
        out = {p["slug"]: {"name": p.get("name") or p["slug"], "url": p.get("url") or "",
                           "logo": p.get("logo") or ""}
               for p in arr if isinstance(p, dict) and p.get("slug")}
        if not out:
            raise FetchError("empty protocols payload")
        return out, url
    return _cached("protocols", _produce)


def token_images(addresses: list[str]) -> tuple[dict, str]:
    """{address_lower: {"image": url|None, "symbol": str|None}} for Mantle tokens, from
    GeckoTerminal (CoinGecko-backed). Sourced official logos — never guessed. Missing logos return
    image=None (caller draws a symbol monogram). Batched to ≤30/call per GeckoTerminal's limit."""
    addrs = list(dict.fromkeys(a for a in addresses if a))
    def _produce():
        url = config.GECKO_TOKENS_MULTI
        out: dict = {}
        for i in range(0, len(addrs), 30):
            chunk = addrs[i:i + 30]
            try:
                payload = _get_json(url.format(addrs=",".join(chunk)))
            except FetchError:
                continue   # a bad chunk shouldn't kill all icons; those become monograms
            for t in payload.get("data", []):
                a = t.get("attributes", {}) or {}
                addr = (a.get("address") or "").lower()
                if addr:
                    out[addr] = {"image": a.get("image_url") or None, "symbol": a.get("symbol")}
        return out, url
    return _cached(f"token_images_{len(addrs)}", _produce)


# --- DefiLlama: per-protocol token series (flow vs price) ---
def protocol_mantle_series(slug: str) -> tuple[dict, str]:
    """Return Mantle chain {tokens:[...], tokensInUsd:[...]} series for a protocol.

    Each list element is {"date": epoch, "tokens": {SYMBOL: amount}}.
    """
    url = config.DEFILLAMA_PROTOCOL.format(slug=slug)
    payload = _get_json(url)
    chain_tvls = payload.get("chainTvls", {}) or {}
    mantle = chain_tvls.get(config.MANTLE_CHAIN_NAME)
    if not mantle:
        raise FetchError(f"{slug}: no Mantle chainTvls")
    tokens = mantle.get("tokens")
    tokens_usd = mantle.get("tokensInUsd")
    if not tokens or not tokens_usd:
        raise FetchError(f"{slug}: missing tokens/tokensInUsd series on Mantle")
    return {"tokens": tokens, "tokensInUsd": tokens_usd, "name": payload.get("name", slug)}, url


# --- DefiLlama: chain fees & dex volume (usage overlay) ---
def chain_fees() -> tuple[dict, str]:
    url = config.DEFILLAMA_FEES_CHAIN
    return _get_json(url), url


def chain_dexs() -> tuple[dict, str]:
    url = config.DEFILLAMA_DEXS_CHAIN
    return _get_json(url), url


# --- DefiLlama: stablecoin chart (chain-level real capital momentum) ---
def stablecoin_chart() -> tuple[list[dict], str]:
    url = config.DEFILLAMA_STABLECOINCHART
    payload = _get_json(url)
    if not isinstance(payload, list) or not payload:
        raise FetchError("empty stablecoin chart")
    return payload, url


# --- GeckoTerminal: Mantle DEX pools (wash / concentration) ---
def gecko_mantle_pools(pages: int = 2) -> tuple[list[dict], str]:
    """Top Mantle DEX pools with reserve_in_usd, h24 volume, h24 buys/sells/buyers/sellers."""
    def _produce():
        url = config.GECKO_MANTLE_POOLS
        out: list[dict] = []
        for page in range(1, pages + 1):
            payload = _get_json(f"{url}?page={page}")
            out.extend(payload.get("data", []))
        if not out:
            raise FetchError("no Mantle pools from GeckoTerminal")
        return out, url
    return _cached(f"gecko_pools_{pages}", _produce)


# --- Token finder: variant search × cross-aggregator × EXACT-symbol × chain-strict ---
# Applies the principle of Mantle's address-registry-navigator locally: match a token by
# its true identity (exact normalized symbol + surface the contract address), NOT by
# substring — so impostors like "SPCX69" when querying "SPCX" are rejected, not picked up.
def _norm_symbol(s: str) -> str:
    s = (s or "").upper()
    return s[1:] if s.startswith("W") else s   # strip one leading W (wrapped token)


def _symbol_matches(token_symbol: str, query: str) -> bool:
    """True only for the same token (wrapped 'w' and xStock trailing 'x' allowed).
    Asymmetric on the 'x': the on-chain token may add an 'x' to the underlying ticker
    (SPCX -> SPCXx), but a bare 'TSLA' must NOT match a 'TSLAx' query, and 'SPCX69'
    must NOT match 'SPCX'."""
    t, q = _norm_symbol(token_symbol), _norm_symbol(query)
    return bool(q) and t in (q, q + "X")


def find_token_pools(symbol: str):
    """Find Mantle pools for `symbol` across GeckoTerminal + DexScreener.

    Returns (matches, rejected). `rejected` lists near-miss impostors / wrong-chain hits
    we deliberately excluded — kept for transparency ("what we did NOT pick up").
    """
    if not (symbol or "").strip():
        return [], [], []
    queries = []
    for q in (symbol, "w" + symbol, symbol + "x", "w" + symbol + "x"):
        if q not in queries:
            queries.append(q)
    matches, rejected, errors, seen = [], [], [], set()

    # GeckoTerminal — strictly network=mantle
    for q in queries:
        try:
            data = _get_json(f"{config.GECKO_SEARCH}?query={q}&network=mantle").get("data", [])
        except FetchError as e:
            errors.append(f"GeckoTerminal '{q}': {e}")
            continue
        for gp in data:
            a = gp.get("attributes", {}) or {}
            name = a.get("name", "")
            toks = [t for t in name.replace("/", " ").split() if t and "%" not in t]
            hit = [t for t in toks if _symbol_matches(t, symbol)]
            addr = a.get("address")
            if hit and addr and ("g", addr) not in seen:
                seen.add(("g", addr))
                rel = gp.get("relationships", {}) or {}
                matches.append({
                    "source": "GeckoTerminal",
                    "dex": ((rel.get("dex") or {}).get("data") or {}).get("id"),
                    "name": name, "matched": hit, "pool_address": addr,
                    "token_id": ((rel.get("base_token") or {}).get("data") or {}).get("id"),
                    "liquidity_usd": float(a.get("reserve_in_usd") or 0),
                    "volume_24h": float((a.get("volume_usd") or {}).get("h24") or 0),
                    "_gp": gp,
                })
            else:
                for t in toks:
                    if symbol.upper() in t.upper() and not _symbol_matches(t, symbol):
                        rejected.append({"src": "GeckoTerminal", "symbol": t, "why": "別トークン(部分一致の偽物)"})

    # DexScreener — keep only chainId == mantle
    for q in queries:
        try:
            pairs = _get_json(f"{config.DEXSCREENER_SEARCH}?q={q}").get("pairs") or []
        except FetchError as e:
            errors.append(f"DexScreener '{q}': {e}")
            continue
        for p in pairs:
            sym = (p.get("baseToken", {}) or {}).get("symbol", "")
            if p.get("chainId") != "mantle":
                if _symbol_matches(sym, symbol):
                    rejected.append({"src": "DexScreener", "symbol": sym, "why": f"別チェーン({p.get('chainId')})"})
                continue
            addr = p.get("pairAddress")
            if _symbol_matches(sym, symbol) and addr and ("d", addr) not in seen:
                seen.add(("d", addr))
                matches.append({
                    "source": "DexScreener",
                    "dex": p.get("dexId"),
                    "name": f"{sym}/{(p.get('quoteToken') or {}).get('symbol')}",
                    "matched": [sym], "pool_address": addr,
                    "token_id": (p.get("baseToken", {}) or {}).get("address"),
                    "liquidity_usd": float((p.get("liquidity") or {}).get("usd") or 0),
                    "volume_24h": float((p.get("volume") or {}).get("h24") or 0),
                })
            elif symbol.upper() in sym.upper() and not _symbol_matches(sym, symbol):
                rejected.append({"src": "DexScreener", "symbol": sym, "why": "別トークン(部分一致の偽物)"})

    # dedup across aggregators by pool contract address: same pool from 2 sources =
    # 1 hit + a 2-source agreement signal (not an inflated count).
    merged = {}
    for m in matches:
        key = (m.get("pool_address") or m.get("name") or "").lower()
        if key in merged:
            ex = merged[key]
            if m["source"] not in ex["sources"]:
                ex["sources"].append(m["source"])
            ex["liq_by_source"][m["source"]] = m["liquidity_usd"]
            if "_gp" not in ex and "_gp" in m:
                ex["_gp"] = m["_gp"]
        else:
            m["sources"] = [m["source"]]
            m["liq_by_source"] = {m["source"]: m["liquidity_usd"]}
            merged[key] = m
    return list(merged.values()), rejected, errors
