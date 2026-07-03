#!/usr/bin/env python3
"""MantleFi stress test — hunt for blind-spots, not confirm success.

Runs the engine broadly and FLAGS anything inconsistent:
  A. classify every Mantle yield pool + sanity-assert no class contradicts its numbers
  B. flow-judge every major protocol (crash / abstain check)
  C. token finder battery incl. a fake token (must abstain) + xStocks + crypto

Read-only (DefiLlama/GeckoTerminal/DexScreener). Reuses one pools fetch.
Run: PYTHONPATH=. python3 stress_test.py
"""
from __future__ import annotations

import time
from collections import Counter

import config
import data_sources as ds
import classify

print("=" * 70)
print("STRESS TEST — MantleFi")
print("=" * 70)

# ---- A. classify all yield pools + anomaly hunt ----
pools, purl = ds.mantle_yield_pools()
print(f"\n[A] 利回りプール scan: {len(pools)} 件")
tally = Counter()
anomalies = []
for p in pools:
    r = classify.classify_yield(p, purl)
    tally[r["class"]] += 1
    n = r["numbers"]
    apy = n.get("apy") or 0
    base = n.get("apyBase") or 0
    cls = r["class"]
    if cls == config.CLASS_REAL_YIELD and apy > 0 and base < 0.5 * apy - 1e-6:
        anomalies.append(f"REAL but base<50%: {r['target']} base={base} apy={apy}")
    if cls == config.CLASS_EMISSION_TRAP and base >= config.EMISSION_TRAP_BASE_ABS:
        anomalies.append(f"EMISSION but base>=0.5: {r['target']} base={base}")
    if cls == config.CLASS_DEAD and apy > config.DEAD_APY_EPS:
        anomalies.append(f"DEAD but apy>0: {r['target']} apy={apy}")
for cls, c in tally.most_common():
    print(f"   {cls}: {c}")
print(f"   ⚠ 分類矛盾 anomalies: {len(anomalies)}")
for a in anomalies:
    print(f"     - {a}")

# ---- B. flow-judge every major protocol ----
print(f"\n[B] protocol flow 判定 ({len(config.MANTLE_PROTOCOL_SLUGS)} 件):")
for slug in config.MANTLE_PROTOCOL_SLUGS:
    try:
        r = classify.classify_flow(slug)
        h = r["numbers"].get("headline", {}) if isinstance(r.get("numbers"), dict) else {}
        print(f"   {slug:30} {r['class']:32} flow={h.get('flow%')} usd={h.get('usd%')}")
    except Exception as e:  # noqa: BLE001 - stress test wants to see any crash
        print(f"   {slug:30} ❌ ERROR {type(e).__name__}: {e}")
    time.sleep(0.4)

# ---- C. token finder battery (identity + abstain) ----
print(f"\n[C] token finder battery:")
battery = ["SPCX", "NVDA", "AAPL", "MSTR", "TSLA", "HOOD", "CRCL", "META", "SPY",
           "USDe", "USDC", "WMNT", "mETH", "FAKETOKENXYZ123", ""]
for sym in battery:
    try:
        matches, rejected, errors = ds.find_token_pools(sym)
        if matches:
            summary = [f"{m['name']}=${m['liquidity_usd']:,.0f}" for m in matches][:3]
        elif errors:
            summary = f"→ ⚠検索失敗{len(errors)}件(要再試行・不在断定せず)"
        else:
            summary = "→ ❔不明(0件=不在)"
        print(f"   {sym:16} 一致{len(matches)}/除外{len(rejected):>3}  {summary}")
    except Exception as e:  # noqa: BLE001
        print(f"   {sym:16} ❌ ERROR {type(e).__name__}: {e}")
    time.sleep(3)   # respect GeckoTerminal free rate limit

print("\nDONE")
