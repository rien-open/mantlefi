#!/usr/bin/env python3
"""MantleFi — OFFLINE unit tests for the on-chain verification layer (mocked RPC, no network).

Proves the load-bearing guarantee: each check reads the chain to verify/flag, NEVER raises into
the caller (RPC failure -> honest abstain), and never invents a number. Run: python3 test_onchain.py
"""
from __future__ import annotations

import sys

import config
import onchain
import report
import rpc

_fails = []


def check(name, cond):
    print(f"  {'✅' if cond else '❌'} {name}")
    if not cond:
        _fails.append(name)


class _MockRPC:
    """Monkeypatch rpc.* with deterministic returns for one scenario."""
    def __init__(self, **kw):
        self.kw = kw
        self._orig = {}

    def __enter__(self):
        defaults = dict(
            erc20_symbol=lambda a: "sUSDe",
            code_size=lambda a: 30014,
            erc20_decimals=lambda a: 18,
            erc20_total_supply=lambda a: 137_000_000 * 10 ** 18,
            proxy_implementation=lambda a: None,
            contract_owner=lambda a: None,
        )
        defaults.update(self.kw)
        for k, v in defaults.items():
            self._orig[k] = getattr(rpc, k)
            setattr(rpc, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._orig.items():
            setattr(rpc, k, v)


# ---------------------------------------------------------------- emission_check
print("[1] emission_check — verify on-chain reward-token reality vs DefiLlama apyReward")
with _MockRPC(erc20_symbol=lambda a: "aManUSDe"):
    # (a) reward tokens present + apyReward>0 => verified emission-backed
    r = onchain.emission_check({"rewardTokens": ["0x954eC713a3915B504a6F288563e5218F597e1895"],
                                "apyReward": 3.5, "symbol": "SUSDE"})
    check("rewardTokens + apyReward>0 -> verified", r["status"] == "verified")
    check("finding names the on-chain reward symbol", "aManUSDe" in r["finding"])
    check("reward token address is masked", "…" in r["numbers"]["reward_token"] and "0x954eC713a3915B" not in r["finding"])
    # (b) reward tokens present but apyReward≈0 => flag (distribution停止/未計上)
    r = onchain.emission_check({"rewardTokens": ["0x954eC713a3915B504a6F288563e5218F597e1895"], "apyReward": 0.0})
    check("rewardTokens + apyReward≈0 -> flag", r["status"] == "flag")
# (c) NO reward tokens + apyReward 0 => verified emission-INDEPENDENT (the 🟢 USDY case)
r = onchain.emission_check({"rewardTokens": None, "apyReward": 0.0, "symbol": "USDY"})
check("no rewardTokens + apyReward 0 -> verified (emission非依存)", r["status"] == "verified")
check("finding states emission-independent", "頼っていない" in r["finding"])
# (d) NO reward tokens but apyReward>0 => flag (aggregator inconsistency)
r = onchain.emission_check({"rewardTokens": [], "apyReward": 2.0})
check("no rewardTokens + apyReward>0 -> flag (要確認)", r["status"] == "flag")

# ---------------------------------------------------------------- contract_check
print("[2] contract_check — underlying contract truth (existence/symbol/proxy/owner)")
pool_susde = {"underlyingTokens": ["0x211Cc4DD073734dA055fbF44a2b4667d5E5fE5d2"], "symbol": "SUSDE"}
with _MockRPC(erc20_symbol=lambda a: "sUSDe"):
    r = onchain.contract_check(pool_susde)
    check("real contract + symbol match -> verified", r["status"] == "verified")
    check("finding reports code size", "実在するトークン" in r["finding"])
    check("symbol match noted", r["numbers"]["symbol_match"] is True)
    check("underlying address masked", "…" in r["numbers"]["underlying"])
with _MockRPC(proxy_implementation=lambda a: "0x1111111111111111111111111111111111111111"):
    r = onchain.contract_check(pool_susde)
    check("EIP-1967 proxy detected -> finding says proxy", r["numbers"]["proxy"] is True and "変更できる" in r["finding"])
with _MockRPC(contract_owner=lambda a: "0x799a2cd46cbc7fb53949072257e6331054a060bb"):
    r = onchain.contract_check(pool_susde)
    check("owner surfaced + masked", r["numbers"]["owner"] and "…" in r["numbers"]["owner"])
with _MockRPC(code_size=lambda a: 0):
    r = onchain.contract_check(pool_susde)
    check("no code -> flag", r["status"] == "flag")
# no underlying address -> abstain
r = onchain.contract_check({"underlyingTokens": [], "symbol": "X"})
check("no underlying address -> abstain", r["status"] == "abstain")

# ---------------------------------------------------------------- reward_token_health
print("[2b] reward_token_health — read the token the reward is actually paid in (RPC only)")
_AMANGHO = "0x1a23b27aC7775B6220dC4F816b5c6A629E371f19"
_GHO_UNDER = "0x211Cc4DD073734dA055fbF44a2b4667d5E5fE5d2"
pool_reward = {"apyReward": 6.0, "rewardTokens": [_AMANGHO]}
with _MockRPC(erc20_symbol=lambda a: "aManGHO", code_size=lambda a: 1166,
              erc20_total_supply=lambda a: 2_662_813 * 10 ** 18):
    r = onchain.reward_token_health(pool_reward)
    check("real reward token -> verified", r["status"] == "verified")
    check("finding names the reward symbol", "aManGHO" in r["finding"])
    check("reward token address masked", "…" in r["numbers"]["reward_token"] and _AMANGHO not in r["finding"])
# proxy reward token: reported as a FACT, still verified (an aToken proxy is normal, not a scam)
with _MockRPC(erc20_symbol=lambda a: "aManGHO", code_size=lambda a: 1166,
              proxy_implementation=lambda a: "0x1111111111111111111111111111111111111111"):
    r = onchain.reward_token_health(pool_reward)
    check("proxy reward token noted but not condemned", r["numbers"]["proxy"] is True and r["status"] == "verified")
# no reward -> abstain (the check is skipped, never a false flag)
check("no reward -> abstain", onchain.reward_token_health({"apyReward": 0.0})["status"] == "abstain")
# audit wires reward-health in only when a reward is actually paid
with _MockRPC(erc20_symbol=lambda a: "aManGHO", code_size=lambda a: 1166):
    check("audit appends reward-health when apyReward>0 (3 checks)",
          len(onchain.audit({"apyReward": 6.0, "rewardTokens": [_AMANGHO], "underlyingTokens": [_GHO_UNDER]})) == 3)
    check("audit stays 2 checks when no reward paid",
          len(onchain.audit({"underlyingTokens": [_GHO_UNDER]})) == 2)

# ---------------------------------------------------------------- never raises
print("[3] never raises into caller — RPC failure becomes honest abstain")
def _boom(*a, **k):
    raise rpc.RpcError("simulated RPC down")
with _MockRPC(code_size=_boom):
    r = onchain.contract_check(pool_susde)
    check("RpcError in contract_check -> abstain (no exception)", r["status"] == "abstain")
check("audit() returns one result per check", len(onchain.audit(pool_susde)) == 2)

# ---------------------------------------------------------------- report integration
print("[4] report.build_report — structured DD + on-chain block + injectable as_of")
rep = report.build_report("aave-v3 SUSDE", as_of="2026-01-01 00:00Z")
check("injectable as_of (reproducible artifacts)", rep["as_of"] == "2026-01-01 00:00Z")
check("report has the expected sections",
      {"target", "verdict", "five_questions", "onchain_verification", "sources", "limitations"}.issubset(rep))
check("no pool -> no on-chain block", rep["onchain_verification"] == [])
check("render_md emits Markdown", report.render_md(rep).startswith("# MantleFi 調査レポート"))
_pool = {"symbol": "SUSDE", "apyReward": 3.5,
         "rewardTokens": ["0x954eC713a3915B504a6F288563e5218F597e1895"],
         "underlyingTokens": ["0x211Cc4DD073734dA055fbF44a2b4667d5E5fE5d2"]}
with _MockRPC(erc20_symbol=lambda a: "sUSDe"):
    rep2 = report.build_report("aave-v3 SUSDE", pool=_pool, as_of="2026-01-01 00:00Z")
    check("pool -> on-chain block present (3 checks: emission/contract/reward)", len(rep2["onchain_verification"]) == 3)
    check("Mantle RPC added to sources", any("rpc.mantle.xyz" in s for s in rep2["sources"]))
    check("text render shows the on-chain section", "チェーンで直接確認" in
          report.render("aave-v3 SUSDE", pool=_pool, as_of="2026-01-01 00:00Z"))
    check("md render shows the on-chain section", "チェーンで直接確認" in report.render_md(rep2))

# ---------------------------------------------------------------- result
print()
if _fails:
    print(f"❌ {len(_fails)} FAILED: {_fails}")
    sys.exit(1)
print("✅ ALL ON-CHAIN UNIT TESTS PASSED")
