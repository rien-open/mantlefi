"""MantleFi — Aave V3 on-chain yield reconstruction (the on-chain agent's core value).

A dashboard (DefiLlama) shows Aave USDT0 at ~6.7% by FOLDING IN a reward headline it copies from
Merkl's self-reported `apr` — the *conditional max*: what you'd earn only if you meet the campaign's
conditions ("supply USDT0 without holding stablecoin debt"; for USDT0 only ~23% of supplied TVL
qualifies). We instead RECONSTRUCT the honest number a typical supplier actually earns, from primary
sources a generalist read-aloud bot cannot reach:

  base   — read STRAIGHT FROM CHAIN: Aave Pool.getReserveData(asset).currentLiquidityRate. The rate
           every supplier earns; matches Aave's own "Protocol APY" (USDT0: chain 2.95% == Aave UI 2.95%).
  reward — computed from Merkl's REAL distribution: dailyRewards × 365 ÷ supplied TVL = the blended rate
           a typical supplier sees (USDT0 ~0.9% == Aave UI's "aWMNT +0.93%"). NOT Merkl's headline `apr`,
           NOT DefiLlama's copy of it. Looping-required campaigns are NOT folded into a plain-supply APY
           (a normal deposit cannot earn them).

So apy = chain_base + merkl_blended_reward ≈ Aave's own UI total, materially below the aggregator
headline. Free + read-only: Mantle RPC (no key) + Merkl public API. NEVER raises — any failure degrades
to the unchanged DefiLlama row (accuracy over coverage; see CLAUDE.md Kill Switch).
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

import config
import rpc

SEL_GET_RESERVE_DATA = "0x35ea6a75"   # Aave V3 Pool.getReserveData(address) (keccak4 of the signature)
RAY = 10 ** 27
SECONDS_PER_YEAR = 31_536_000


def _a32(a: str) -> str:
    return (a or "").lower().replace("0x", "").rjust(64, "0")


def _words(hexs: str):
    h = hexs[2:] if hexs and hexs.startswith("0x") else (hexs or "")
    return [h[i:i + 64] for i in range(0, len(h), 64)]


def _rate_to_apy_pct(rate_ray: int) -> float:
    """Aave currentLiquidityRate (RAY-scaled APR) → APY %, compounded per second (DefiLlama's formula)."""
    apr = rate_ray / RAY
    return ((1 + apr / SECONDS_PER_YEAR) ** SECONDS_PER_YEAR - 1) * 100


# ---------------------------------------------------------------- chain: base supply APY
_RESERVE_CACHE: dict = {}   # asset_lower -> (monotonic_ts, value)


def chain_reserve(asset: str):
    """(base_supply_apy_pct, borrow_apy_pct, aToken_addr) from Aave Pool.getReserveData — LIVE chain.
    Raises rpc.RpcError on failure (caller catches → keeps the DefiLlama row, never guesses).
    Cached AAVE_RESERVE_CACHE_TTL seconds so repeated scans don't re-hit the chain."""
    key = (asset or "").lower()
    now = time.monotonic()
    hit = _RESERVE_CACHE.get(key)
    if hit and now - hit[0] < config.AAVE_RESERVE_CACHE_TTL:
        return hit[1]
    r = rpc.eth_call(config.AAVE_V3_POOL, SEL_GET_RESERVE_DATA + _a32(asset))
    w = _words(r)
    if len(w) < 9:
        raise rpc.RpcError("getReserveData returned too few words")
    base = _rate_to_apy_pct(int(w[2], 16))      # word[2] = currentLiquidityRate (supply APR, RAY)
    borrow = _rate_to_apy_pct(int(w[4], 16))    # word[4] = currentVariableBorrowRate
    atoken = "0x" + w[8][-40:]                   # word[8] = aTokenAddress
    val = (base, borrow, atoken)
    _RESERVE_CACHE[key] = (now, val)
    return val


# ---------------------------------------------------------------- chain: gross supplied (real pool size)
_DEC_CACHE: dict = {}      # token_lower -> decimals (immutable → cache forever)
_SUPPLY_CACHE: dict = {}   # atoken_lower -> (monotonic_ts, supply_units)
_PRICE_CACHE = {"t": 0.0, "v": {}}


def _decimals(token: str) -> int:
    key = (token or "").lower()
    if key not in _DEC_CACHE:
        _DEC_CACHE[key] = rpc.erc20_decimals(token)
    return _DEC_CACHE[key]


def supplied_units(asset: str, atoken: str):
    """Total UNDERLYING supplied to the pool = aToken.totalSupply ÷ 10^decimals — the REAL pool size
    from chain (vs DefiLlama's lending tvlUsd, which is NET supplied−borrowed). None on RPC failure
    (caller then keeps the aggregator TVL — never fabricates a size). Cached AAVE_RESERVE_CACHE_TTL s."""
    key = (atoken or "").lower()
    now = time.monotonic()
    hit = _SUPPLY_CACHE.get(key)
    if hit and now - hit[0] < config.AAVE_RESERVE_CACHE_TTL:
        return hit[1]
    try:
        units = rpc.erc20_total_supply(atoken) / (10 ** _decimals(asset))
    except rpc.RpcError:
        return None
    _SUPPLY_CACHE[key] = (now, units)
    return units


def token_prices(addrs) -> dict:
    """{address_lower: usd_price} from DefiLlama's free coins API (no key), batched. Returns whatever
    is cached on failure (maybe {}) so a price outage never fabricates a size — caller keeps net TVL."""
    addrs = [a for a in dict.fromkeys((x or "").lower() for x in addrs) if a]
    if not addrs:
        return {}
    now = time.monotonic()
    if now - _PRICE_CACHE["t"] < config.MERKL_CACHE_TTL and all(a in _PRICE_CACHE["v"] for a in addrs):
        return _PRICE_CACHE["v"]
    keys = ",".join(f"mantle:{a}" for a in addrs)
    try:
        req = urllib.request.Request(f"{config.DEFILLAMA_COINS_PRICES}{keys}",
                                     headers={"User-Agent": config.HTTP_UA})
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            coins = (json.loads(resp.read().decode("utf-8")) or {}).get("coins", {})
    except Exception:   # noqa: BLE001 — failure → keep whatever we had (no fabrication)
        return _PRICE_CACHE["v"]
    out = dict(_PRICE_CACHE["v"])
    for k, v in coins.items():
        if isinstance(v, dict) and v.get("price") is not None:
            out[k.split(":")[-1].lower()] = float(v["price"])
    _PRICE_CACHE.update(t=now, v=out)
    return out


# ---------------------------------------------------------------- Merkl: the real reward distribution
_MERKL_CACHE = {"t": 0.0, "v": None}


def merkl_supply_rewards() -> dict:
    """{SYMBOL_UPPER: {blended, headline, looping, url, name, daily, tvl}} for LIVE Merkl 'Lend X on
    Aave' (supply) campaigns on Mantle. `blended` = dailyRewards × 365 ÷ campaign TVL (the rate a
    typical supplier sees); `headline` = Merkl's self-reported apr (the conditional max). Returns {}
    on ANY failure → no reward folded in (base-only, never fabricated). Cached MERKL_CACHE_TTL sec."""
    now = time.monotonic()
    if _MERKL_CACHE["v"] is not None and now - _MERKL_CACHE["t"] < config.MERKL_CACHE_TTL:
        return _MERKL_CACHE["v"]
    url = f"{config.MERKL_OPPORTUNITIES}?chainId={config.MANTLE_CHAIN_ID}&status=LIVE&items=100"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": config.HTTP_UA})
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            arr = json.loads(resp.read().decode("utf-8"))
    except Exception:   # noqa: BLE001 — any failure → empty map (base-only, no fabrication)
        return {}
    out = parse_merkl_opportunities(arr)
    _MERKL_CACHE.update(t=now, v=out)
    return out


def parse_merkl_opportunities(arr) -> dict:
    """Pure parser (testable offline): Merkl opportunities list → {SYMBOL_UPPER: reward record}.
    Keeps only Aave supply ('Lend …') campaigns; maps every symbol named in the title to the record."""
    out: dict = {}
    for o in arr if isinstance(arr, list) else []:
        if (o.get("protocol") or {}).get("name") != "Aave":
            continue
        name = o.get("name") or ""
        if not re.match(r"^\s*lend\b", name, re.I):
            continue   # supply side only (skip "Borrow …")
        tvl = float(o.get("tvl") or 0)
        daily = float(o.get("dailyRewards") or 0)
        blended = round(daily * 365 / tvl * 100, 2) if tvl > 0 else 0.0
        rec = {"blended": blended, "headline": round(float(o.get("apr") or 0), 2),
               "looping": "looping" in name.lower(), "url": o.get("depositUrl") or "",
               "name": name, "daily": daily, "tvl": tvl}
        mid = re.split(r"\bon\s+aave\b", name, flags=re.I)[0]
        mid = re.sub(r"^\s*lend\s+", "", mid, flags=re.I)
        for tok in re.split(r"[\s/,]+|\band\b", mid):
            tok = tok.strip()
            if tok and tok.lower() != "and":
                out.setdefault(tok.upper(), rec)
    return out


# ---------------------------------------------------------------- correction (the seam)
def correct_pool(pool: dict, merkl: dict | None = None, prices: dict | None = None,
                 with_symbol: bool = True) -> dict:
    """Return a chain-corrected COPY of an aave-v3 DefiLlama pool row:
      apyBase  ← chain base supply APY (getReserveData),
      apyReward← blended reward from the real distribution (0 when none / looping-required),
      apy      ← base + reward,
      tvlUsd   ← GROSS supplied (aToken.totalSupply × price) = the real pool size, vs DefiLlama's NET,
      symbol   ← on-chain casing (sUSDe, not DefiLlama's SUSDE),
      `_aave`  ← a breakdown dict the display layer turns into a verification line.
    Non-aave, already-corrected, no-underlying, OR any chain failure → returns the pool UNCHANGED
    (graceful degrade to the aggregator; never guesses)."""
    if (pool or {}).get("project") != "aave-v3" or pool.get("_aave"):
        return pool
    asset = (pool.get("underlyingTokens") or [None])[0]
    if not asset:
        return pool
    try:
        base, borrow, atoken = chain_reserve(asset)
    except rpc.RpcError:
        return pool   # could not read chain → keep the aggregator row (no fabrication)
    if merkl is None:
        merkl = merkl_supply_rewards()
    if prices is None:
        prices = token_prices([asset])
    rec = merkl.get((pool.get("symbol") or "").upper())
    onchain_sym = (rpc.erc20_symbol(asset) if with_symbol else "") or pool.get("symbol")

    reward = 0.0
    meta = {"base_apy": round(base, 2), "borrow_apy": round(borrow, 2),
            "reward_blended": 0.0, "reward_headline": 0.0, "looping": False,
            "reward_src": None, "url": "", "onchain_symbol": onchain_sym}
    # Fold the REAL distributed reward whenever there is one — INCLUDING looping/paired campaigns
    # (Aave's own UI does: USDe shows Protocol 0.77% + aUSDe 3.05% = 3.82%). The looping condition is
    # disclosed as a note (see onchain.aave_breakdown), not used to silently zero the reward — earlier
    # excluding it made USDe read 0.77% ⚫ vs the official 3.82% (rien caught the discrepancy).
    if rec and rec["blended"] > 0:
        reward = rec["blended"]
        meta.update(reward_blended=rec["blended"], reward_headline=rec["headline"],
                    looping=rec["looping"], reward_src="Merkl", url=rec["url"])
    elif rec and rec["looping"]:
        # a looping campaign with no current distribution → note it, but there's nothing to add
        meta.update(looping=True, reward_headline=rec["headline"], reward_src="Merkl", url=rec["url"])

    p = dict(pool)
    p["symbol"] = onchain_sym
    p["apyBase"] = round(base, 2)
    p["apyReward"] = round(reward, 2)
    p["apy"] = round(base + reward, 2)
    meta["total_apy"] = p["apy"]
    # real pool size = gross supplied (chain) × price; keep DefiLlama's net only if a piece is missing
    units = supplied_units(asset, atoken)
    price = prices.get((asset or "").lower())
    if units is not None and price is not None:
        gross = round(units * price)
        meta["gross_tvl"] = gross
        meta["net_tvl"] = pool.get("tvlUsd")
        meta["tvl_basis"] = "gross"      # display "供給総額" — the real total deposited
        p["tvlUsd"] = gross
    else:
        meta["tvl_basis"] = "net"        # couldn't value gross → keep DefiLlama's net, labeled "預入"
    p["_aave"] = meta
    return p


def correct_pools(pools: list, with_symbol: bool = False) -> list:
    """Correct every meaningful aave-v3 row in a list (ONE shared Merkl fetch + ONE batched price
    fetch). Bounded: only Aave pools touch the chain, and only those at/above the meaningful-APY floor
    (pure-zero rows skip RPC). `with_symbol=False` skips the per-pool symbol read — in a list the
    displayed real-yield pools are USDT0/GHO/USDC whose casing is already correct; the deep judge
    card reads the symbol (sUSDe)."""
    targets = [p for p in pools
               if p.get("project") == "aave-v3" and (p.get("apy") or 0) >= config.MEANINGFUL_APY_PCT]
    if not targets:
        return pools
    merkl = merkl_supply_rewards()
    prices = token_prices([(p.get("underlyingTokens") or [None])[0] for p in targets])
    tids = {id(p) for p in targets}
    return [correct_pool(p, merkl, prices, with_symbol=with_symbol) if id(p) in tids else p
            for p in pools]
