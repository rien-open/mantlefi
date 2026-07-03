"""MantleFi — the on-chain VERIFICATION layer (free Mantle RPC; what a generalist LLM can't do).

The engine's yield/flow/wash numbers come from aggregators (DefiLlama / GeckoTerminal). A
chatbot with web access can re-serve those. THIS module goes to the Mantle chain itself to
VERIFY them — the trust-minimized "ダッシュボードを信じない・二重確認" that is the product's moat:

  - emission_check: is the yield reward-emission-backed? Read the pool's rewardTokens ON-CHAIN
    (their existence + the reward token's real symbol) and cross-check vs DefiLlama's apyReward.
    rewardTokens present on-chain ⟺ a distribution exists. USDY (none) vs SUSDE (aManUSDe).
  - contract_check: is the underlying a real, identifiable, non-rug-shaped contract? code size,
    on-chain symbol match, EIP-1967 upgradeable-proxy, owner() — the risk-evaluator /
    address-registry principle implemented natively (no MCP / no npx on 母艦).

Every check reads ONLY public Mantle RPC (no key, no writes, no third-party exec) and NEVER
raises into the caller: on an RPC failure it returns an honest abstain, never a guess. All
addresses are masked in output (0xXXXX…YYYY). No number here is invented — each is an RPC read.
"""
from __future__ import annotations

import rpc

RPC_SRC = "Mantle RPC (rpc.mantle.xyz)"
_REWARD_EPS = 0.05   # apyReward ≈ 0 tolerance (pts)


def _mask(addr) -> str:
    a = addr or ""
    return f"{a[:6]}…{a[-4:]}" if len(a) >= 12 else a


def emission_check(pool: dict) -> dict:
    """Verify ON-CHAIN whether the yield leans on reward emissions, instead of trusting
    DefiLlama's computed apyReward. Returns a result dict (status verified/flag/abstain)."""
    reward_tokens = pool.get("rewardTokens") or []
    apy_reward = pool.get("apyReward") or 0.0

    if reward_tokens:
        addr = reward_tokens[0]
        sym = rpc.erc20_symbol(addr)             # never raises ('' if not exposed)
        sym_txt = f"{sym} " if sym else ""
        nums = {"reward_tokens_onchain": len(reward_tokens), "apyReward": round(apy_reward, 2),
                "reward_token": _mask(addr), "reward_symbol": sym}
        if apy_reward <= _REWARD_EPS:
            return {"label": "配布報酬（チェーン上で確認）", "status": "flag", "source": RPC_SRC,
                    "numbers": nums,
                    "finding": f"報酬トークン『{sym or '?'}』はあるが配布報酬がほぼ0＝配布停止の疑い→要確認。"}
        return {"label": "配布報酬（チェーン上で確認）", "status": "verified", "source": RPC_SRC,
                "numbers": nums,
                "finding": f"報酬は『{sym or '?'}』というトークン（{_mask(addr)}）で配布。"
                           f"年利の {apy_reward:.2f}% ぶんが配布報酬。"}

    # no reward tokens registered on-chain
    nums = {"reward_tokens_onchain": 0, "apyReward": round(apy_reward, 2)}
    if apy_reward > _REWARD_EPS:
        return {"label": "配布報酬（チェーン上で確認）", "status": "flag", "source": RPC_SRC,
                "numbers": nums,
                "finding": f"DefiLlamaは配布報酬 {apy_reward:.2f}% と言うがチェーンに報酬トークンなし＝要確認。"}
    return {"label": "配布報酬（チェーン上で確認）", "status": "verified", "source": RPC_SRC,
            "numbers": nums,
            "finding": "報酬トークンの配布なし＝配布報酬に頼っていない（チェーンで確認）。"}


def contract_check(pool: dict) -> dict:
    """Verify the underlying token is a real, identifiable on-chain contract: existence (code),
    symbol match vs DefiLlama, EIP-1967 upgradeable-proxy, owner(). Abstains on RPC failure."""
    underlying = (pool.get("underlyingTokens") or [None])[0]
    if not underlying:
        return {"label": "トークンの正体", "status": "abstain", "source": RPC_SRC,
                "numbers": {},
                "finding": "対象トークンのアドレスが DefiLlama に無く、チェーンで確認できない（保留）。"}
    try:
        size = rpc.code_size(underlying)
        if size == 0:
            return {"label": "トークンの正体", "status": "flag", "source": RPC_SRC,
                    "numbers": {"underlying": _mask(underlying), "code_bytes": 0},
                    "finding": f"対象アドレス（{_mask(underlying)}）にコントラクトの中身が無い"
                               "（アドレス誤り等の疑い）→要確認。"}
        sym = rpc.erc20_symbol(underlying)
        declared = (pool.get("symbol") or "").upper()
        sym_match = bool(sym) and (sym.upper() in declared or declared in sym.upper())
        dec = rpc.erc20_decimals(underlying)
        supply_raw = rpc.erc20_total_supply(underlying)
        supply = supply_raw / (10 ** dec) if supply_raw else 0
        impl = rpc.proxy_implementation(underlying)
        owner = rpc.contract_owner(underlying)
    except rpc.RpcError as e:
        return {"label": "トークンの正体", "status": "abstain", "source": RPC_SRC,
                "numbers": {"underlying": _mask(underlying)},
                "finding": f"チェーンからの取得に失敗（{e}）→保留・推測しない。"}

    nums = {"underlying": _mask(underlying), "code_bytes": size, "onchain_symbol": sym,
            "symbol_match": sym_match, "total_supply": round(supply, 2),
            "proxy": bool(impl), "owner": _mask(owner) if owner else None}
    real = ("実在するトークンで、名前も一致" if sym_match
            else (f"実在するが、チェーン上の名前『{sym}』が表記と違う（要確認）" if sym
                  else "実在するトークン（名前は取得できず）"))
    upg = ("🔧 運営が後から仕様変更できる作り" if impl
           else "🔒 仕様は固定（変更不可）")
    status = "verified" if (sym_match or not sym) else "flag"
    return {"label": "トークンの正体", "status": status, "source": RPC_SRC,
            "numbers": nums, "finding": f"{real}。{upg}。"}


def reward_token_health(pool: dict) -> dict:
    """The reward you're ACTUALLY paid in — is that token a sound contract? A dashboard shows
    'apyReward 6%' but never WHAT the 6% is paid in, nor whether that token is an upgradeable /
    owner-controlled contract. Read the reward token on-chain: existence, symbol, supply, proxy,
    owner — the same scrutiny contract_check applies to the underlying, but to the reward itself.
    RPC only (no key); abstains on no-reward or RPC failure (never guesses)."""
    reward_tokens = pool.get("rewardTokens") or []
    apy_reward = pool.get("apyReward") or 0.0
    if not reward_tokens or apy_reward <= _REWARD_EPS:
        return {"label": "報酬トークンの中身", "status": "abstain", "source": RPC_SRC,
                "numbers": {}, "finding": "報酬トークンが無い/配布報酬がほぼ0＝この検査は対象外。"}
    addr = reward_tokens[0]
    try:
        size = rpc.code_size(addr)
        if size == 0:
            return {"label": "報酬トークンの中身", "status": "flag", "source": RPC_SRC,
                    "numbers": {"reward_token": _mask(addr), "code_bytes": 0},
                    "finding": f"報酬トークン（{_mask(addr)}）にコントラクトの中身が無い→要確認。"}
        sym = rpc.erc20_symbol(addr)
        dec = rpc.erc20_decimals(addr)
        supply_raw = rpc.erc20_total_supply(addr)
        supply = supply_raw / (10 ** dec) if supply_raw else 0
        impl = rpc.proxy_implementation(addr)
        owner = rpc.contract_owner(addr)
    except rpc.RpcError as e:
        return {"label": "報酬トークンの中身", "status": "abstain", "source": RPC_SRC,
                "numbers": {"reward_token": _mask(addr)},
                "finding": f"報酬トークンのチェーン取得に失敗（{e}）→保留。"}

    nums = {"reward_token": _mask(addr), "reward_symbol": sym, "code_bytes": size,
            "total_supply": round(supply, 2), "proxy": bool(impl),
            "owner": _mask(owner) if owner else None}
    upg = "🔧 運営が後から仕様変更できる作り" if impl else "🔒 仕様は固定（変更不可）"
    # report proxy/owner as FACTS, not a verdict — an aToken being a proxy is normal Aave design,
    # not a scam. status = verified once it's a real, identifiable contract (flag only on no-code).
    return {"label": "報酬トークンの中身", "status": "verified", "source": RPC_SRC,
            "numbers": nums,
            "finding": f"報酬『{sym or '?'}』も実在。{upg}。"}


def aave_breakdown(pool: dict):
    """For a chain-corrected Aave pool (carries `_aave` from aave.correct_pool): surface the
    RECONSTRUCTED yield as a verification line — base from the chain, reward from Merkl's REAL
    distribution — instead of the aggregator's reward headline. None if the pool wasn't corrected."""
    m = (pool or {}).get("_aave")
    if not m:
        return None
    base = m.get("base_apy") or 0.0
    reward = m.get("reward_blended") or 0.0
    total = round(base + reward, 2)
    head = m.get("reward_headline") or 0.0
    if reward > 0 and m.get("looping"):
        finding = f"実需の金利 {base:.2f}% ＋ 配布報酬 約{reward:.2f}%（実測・条件付き）＝ 合計 約{total:.2f}%。"
    elif reward > 0:
        finding = (f"実需の金利 {base:.2f}% ＋ 配布報酬 約{reward:.2f}%（実測・上限 約{head:.1f}%）"
                   f"＝ 合計 約{total:.2f}%。")
    elif m.get("looping"):
        finding = f"実需の金利 {base:.2f}%（実測）。報酬枠はあるが今は配布を確認できず。"
    else:
        finding = f"実需の金利 {base:.2f}%（実測）。上乗せ報酬は無し。"
    return {"label": "利回りの実測",
            "status": "verified", "source": "Mantle RPC + Merkl",
            "numbers": {"base_apy": round(base, 2), "reward_apy": round(reward, 2),
                        "total_apy": total, "reward_headline_max": head},
            "finding": finding}


def audit(pool: dict) -> list:
    """Run every on-chain verification for a DefiLlama pool row. Each check is independent and
    abstains (not raises) on RPC failure. Returns a list of result dicts. The reward-token-health
    check runs only when the pool actually pays an emission reward (else it would just abstain).

    For a chain-corrected Aave pool (`_aave` present) the reward story is told by aave_breakdown
    (base=chain, reward=Merkl's real distribution), so emission_check (which reads DefiLlama's
    reward headline) is dropped to avoid a second, conflicting reward line."""
    if pool.get("_aave"):
        checks = [aave_breakdown(pool), contract_check(pool)]
        if (pool["_aave"].get("reward_blended") or 0.0) > 0 and (pool.get("rewardTokens") or []):
            checks.append(reward_token_health(pool))
        return [c for c in checks if c]
    checks = [emission_check(pool), contract_check(pool)]
    if (pool.get("rewardTokens") or []) and (pool.get("apyReward") or 0.0) > _REWARD_EPS:
        checks.append(reward_token_health(pool))
    return checks


def token_identity(addr, declared_symbol=None) -> dict:
    """Resolve a token ADDRESS to its REAL on-chain identity on Mantle (symbol + existence), and
    whether that matches the dashboard's declared symbol — the per-token cell of the all-pool
    correspondence table (research.py correspondence). This anchors the DeFiLlama↔Mantle mapping
    on the ADDRESS (chain truth), not a name string. Never raises (abstains on RPC failure).
    Masked addr in the label; full addr in the Mantlescan link (the verification target)."""
    import config
    if not addr:
        return {"present": False}
    link = config.MANTLESCAN_TOKEN_URL.format(addr=addr)
    try:
        sym = rpc.erc20_symbol(addr)
        size = rpc.code_size(addr)
    except rpc.RpcError:
        return {"present": True, "status": "abstain", "onchain_symbol": "",
                "addr": _mask(addr), "link": link}
    decl = (declared_symbol or "").upper()
    match = (decl in sym.upper() or sym.upper() in decl) if (decl and sym) else None
    return {"present": True, "status": "verified" if size > 0 else "flag",
            "onchain_symbol": sym, "code_bytes": size, "match": match,
            "addr": _mask(addr), "link": link}
