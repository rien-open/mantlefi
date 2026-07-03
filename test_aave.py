#!/usr/bin/env python3
"""MantleFi — OFFLINE unit tests for the Aave chain+Merkl yield reconstruction (mocked, no network).

Proves the load-bearing guarantees of aave.py:
  - base supply APY is decoded correctly from a getReserveData blob (RAY APR → per-second APY),
  - Merkl's REAL distribution (dailyRewards÷TVL = blended) is used, NOT its headline `apr`,
  - looping-required campaigns are NOT folded into a plain-supply APY,
  - non-aave pools and any RPC failure leave the row UNCHANGED (graceful, no fabrication),
  - on-chain symbol casing (sUSDe) is applied,
  - onchain.aave_breakdown / audit tell the reward story from chain+Merkl, not the aggregator.
Run: python3 test_aave.py
"""
from __future__ import annotations

import sys

import aave
import onchain
import rpc

_fails = []


def check(name, cond):
    print(f"  {'✅' if cond else '❌'} {name}")
    if not cond:
        _fails.append(name)


def _word(hexint: int) -> str:
    return f"{hexint:064x}"


def _reserve_blob(liq_ray: int, bor_ray: int, atoken: str) -> str:
    """A minimal ABI-encoded Aave getReserveData return (≥9 words): w2=liqRate, w4=borrowRate,
    w8=aToken. Other words are filler — the decoder only reads 2, 4, 8."""
    w = [_word(0)] * 9
    w[2] = _word(liq_ray)
    w[4] = _word(bor_ray)
    w[8] = atoken.lower().replace("0x", "").rjust(64, "0")
    return "0x" + "".join(w)


class _MockRPC:
    """Monkeypatch rpc.* for one scenario (same pattern as test_onchain.py)."""
    def __init__(self, **kw):
        self.kw = kw
        self._orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self._orig[k] = getattr(rpc, k)
            setattr(rpc, k, v)
        self._clear()                 # don't let a cached reserve/supply/decimals leak across scenarios
        return self

    def __exit__(self, *a):
        for k, v in self._orig.items():
            setattr(rpc, k, v)
        self._clear()

    @staticmethod
    def _clear():
        aave._RESERVE_CACHE.clear()
        aave._SUPPLY_CACHE.clear()
        aave._DEC_CACHE.clear()


# ---------------------------------------------------------------- base APY decode
print("[1] chain_reserve — decode base/borrow APY from a getReserveData blob")
# liquidityRate ≈ 2.91% APR (RAY) → ~2.95% APY; borrow ≈ 3.80% APR → ~3.87% APY
liq_ray = int(0.0291 * aave.RAY)
bor_ray = int(0.0380 * aave.RAY)
ATOKEN = "0x7053bad224f0c021839f6ac645bdae5f8b585b69"
with _MockRPC(eth_call=lambda to, data: _reserve_blob(liq_ray, bor_ray, ATOKEN)):
    base, borrow, atoken = aave.chain_reserve("0x779Ded0c9e1022225f8E0630b35a9b54bE713736")
    check("base APY ≈ 2.95% (per-second compounded)", 2.9 < base < 3.0)
    check("borrow APY ≈ 3.87%", 3.8 < borrow < 3.95)
    check("aToken decoded from word[8]", atoken.lower() == ATOKEN.lower())


# ---------------------------------------------------------------- Merkl parser
print("\n[2] parse_merkl_opportunities — blended (real distribution), not the headline apr")
SAMPLE = [
    {"protocol": {"name": "Aave"}, "name": "Lend USDT0 on Aave", "apr": 3.82,
     "tvl": 258_728_332, "dailyRewards": 6147.79, "depositUrl": "https://app.aave.com/x"},
    {"protocol": {"name": "Aave"}, "name": "Borrow USDT0 on Aave ", "apr": 1.0,
     "tvl": 220_000_000, "dailyRewards": 6000.0},                       # supply-only: must be skipped
    {"protocol": {"name": "Aave"}, "name": "Lend sUSDe and USDe on Aave (looping required)",
     "apr": 3.75, "tvl": 725_873, "dailyRewards": 59.41},               # looping
    {"protocol": {"name": "Fluxion"}, "name": "Lend FOO on Aave", "apr": 99, "tvl": 1, "dailyRewards": 1},
]
m = aave.parse_merkl_opportunities(SAMPLE)
check("USDT0 present", "USDT0" in m)
check("USDT0 blended ≈ 0.87% (daily×365÷tvl), NOT the headline 3.82", abs(m["USDT0"]["blended"] - 0.87) < 0.05)
check("USDT0 headline kept (3.82) for the footnote", m["USDT0"]["headline"] == 3.82)
check("Borrow campaign skipped", all("Borrow" not in v["name"] for v in m.values()))
check("looping flagged for SUSDE/USDE", m.get("SUSDE", {}).get("looping") and m.get("USDE", {}).get("looping"))
check("non-Aave protocol skipped (FOO absent)", "FOO" not in m)


# ---------------------------------------------------------------- correct_pool
print("\n[3] correct_pool — fold the blended reward, gross TVL, fix casing, attach _aave")
MERKL = {"USDT0": {"blended": 0.87, "headline": 3.82, "looping": False, "url": "u", "name": "Lend USDT0 on Aave"}}
USDT0_ADDR = "0x779Ded0c9e1022225f8E0630b35a9b54bE713736"
PRICES = {USDT0_ADDR.lower(): 0.9985}
pool = {"project": "aave-v3", "symbol": "USDT0", "apy": 6.74, "apyBase": 2.91, "apyReward": 3.83,
        "tvlUsd": 38_000_000, "underlyingTokens": [USDT0_ADDR],
        "rewardTokens": ["0x09E4C43B"], "pool": "x"}
with _MockRPC(eth_call=lambda to, data: _reserve_blob(liq_ray, bor_ray, ATOKEN),
              erc20_symbol=lambda a: "USDT0", erc20_decimals=lambda a: 6,
              erc20_total_supply=lambda a: 259_096_598 * 10 ** 6):
    cp = aave.correct_pool(dict(pool), MERKL, prices=PRICES, with_symbol=True)
    check("apyBase ← chain (~2.95)", 2.9 < cp["apyBase"] < 3.0)
    check("apyReward ← blended (0.87), not 3.83", cp["apyReward"] == 0.87)
    check("apy = base + blended (~3.82), not 6.74", 3.7 < cp["apy"] < 3.9)
    check("tvlUsd ← GROSS supplied (~$258M, aToken.supply×price), not net $38M",
          250_000_000 < cp["tvlUsd"] < 265_000_000)
    check("_aave keeps net_tvl for reference", cp["_aave"]["net_tvl"] == 38_000_000)
    check("_aave breakdown attached", isinstance(cp.get("_aave"), dict) and cp["_aave"]["reward_headline"] == 3.82)
    check("original pool dict not mutated", pool["apy"] == 6.74 and pool["tvlUsd"] == 38_000_000)

print("\n[4] correct_pool — looping reward IS folded (matches Aave UI), flagged as conditional")
MERKL_LOOP = {"SUSDE": {"blended": 2.99, "headline": 3.75, "looping": True, "url": "u", "name": "..."}}
SUSDE_ADDR = "0x211Cc4DD073734dA055fbF44a2b4667d5E5fE5d2"
spool = {"project": "aave-v3", "symbol": "SUSDE", "apy": 3.75, "apyBase": 0.0, "apyReward": 3.75,
         "tvlUsd": 170_000_000, "underlyingTokens": [SUSDE_ADDR]}
with _MockRPC(eth_call=lambda to, data: _reserve_blob(int(0.0 * aave.RAY), int(0.0 * aave.RAY), ATOKEN),
              erc20_symbol=lambda a: "sUSDe", erc20_decimals=lambda a: 18,
              erc20_total_supply=lambda a: 138_000_000 * 10 ** 18):
    cp = aave.correct_pool(dict(spool), MERKL_LOOP, prices={SUSDE_ADDR.lower(): 1.23}, with_symbol=True)
    check("looping reward FOLDED → apyReward 2.99 (not zeroed)", cp["apyReward"] == 2.99)
    check("apy = base + reward (~2.99)", 2.9 < cp["apy"] < 3.1)
    check("_aave.looping True (so the breakdown notes the condition)", cp["_aave"]["looping"] is True)
    check("on-chain symbol casing applied (sUSDe)", cp["symbol"] == "sUSDe")
    check("gross TVL still valued (~$170M = 138M×$1.23)", 165_000_000 < cp["tvlUsd"] < 175_000_000)

print("\n[5] correct_pool — graceful degrade (non-aave, RPC failure, missing price)")
non = {"project": "ondo-yield-assets", "symbol": "USDY", "apy": 3.55, "apyBase": 3.55}
check("non-aave returned unchanged", aave.correct_pool(dict(non), {}, {}) == non)
def _boom(*a, **k):
    raise rpc.RpcError("rpc down")
with _MockRPC(eth_call=_boom, erc20_symbol=lambda a: "USDT0"):
    cp = aave.correct_pool(dict(pool), MERKL, prices=PRICES, with_symbol=True)
    check("RPC failure → DefiLlama row kept (apy 6.74, no _aave)", cp["apy"] == 6.74 and "_aave" not in cp)
with _MockRPC(eth_call=lambda to, data: _reserve_blob(liq_ray, bor_ray, ATOKEN),
              erc20_symbol=lambda a: "USDT0", erc20_decimals=lambda a: 6,
              erc20_total_supply=lambda a: 259_096_598 * 10 ** 6):
    cp = aave.correct_pool(dict(pool), MERKL, prices={}, with_symbol=True)   # no price → keep net TVL
    check("missing price → APY still corrected but tvl kept at net $38M", cp["apy"] < 4 and cp["tvlUsd"] == 38_000_000)


# ---------------------------------------------------------------- onchain.aave_breakdown / audit
print("\n[6] onchain.aave_breakdown — reward story from chain+Merkl")
with_reward = {"_aave": {"base_apy": 2.95, "reward_blended": 0.87, "reward_headline": 3.82, "looping": False}}
b = onchain.aave_breakdown(with_reward)
check("status verified", b["status"] == "verified")
check("source = Mantle RPC + Merkl", b["source"] == "Mantle RPC + Merkl")
check("finding leads with chain measurement (Merkl de-emphasized)",
      "実需の金利" in b["finding"] and "実測" in b["finding"] and "Merkl" not in b["finding"])
check("finding notes the headline cap (bonus max)", "上限" in b["finding"] and "3.8" in b["finding"])
loop = {"_aave": {"base_apy": 0.77, "reward_blended": 2.98, "reward_headline": 3.75, "looping": True}}
lf = onchain.aave_breakdown(loop)["finding"]
check("looping+reward breakdown folds reward AND flags the condition",
      "2.98" in lf and "条件付き" in lf)
loop0 = {"_aave": {"base_apy": 0.77, "reward_blended": 0.0, "reward_headline": 3.75, "looping": True}}
check("looping with no current distribution → noted, not folded",
      "確認できず" in onchain.aave_breakdown(loop0)["finding"])
check("non-corrected pool → no breakdown", onchain.aave_breakdown({"symbol": "X"}) is None)

print("\n[7] onchain.audit — corrected Aave pool uses aave_breakdown (not emission_check)")
corrected = {"project": "aave-v3", "symbol": "USDT0", "apyReward": 0.87,
             "rewardTokens": ["0x09E4C43B"], "underlyingTokens": ["0xabc"],
             "_aave": {"base_apy": 2.95, "reward_blended": 0.87, "reward_headline": 3.82, "looping": False}}
with _MockRPC(code_size=lambda a: 1000, erc20_symbol=lambda a: "USDT0", erc20_decimals=lambda a: 6,
              erc20_total_supply=lambda a: 0, proxy_implementation=lambda a: None, contract_owner=lambda a: None):
    checks = onchain.audit(corrected)
    labels = [c["label"] for c in checks]
    check("aave_breakdown present", any("利回りの実測" in l for l in labels))
    check("emission_check NOT present (no double reward line)", not any("配布報酬（チェーン上で確認）" == l for l in labels))


print(f"\n{'='*48}")
if _fails:
    print(f"❌ {len(_fails)} FAILED: {_fails}")
    sys.exit(1)
print("✅ ALL AAVE TESTS PASSED")
