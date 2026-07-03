"""MantleFi — minimal Mantle RPC reader (free, no key, stdlib only).

Provides an INDEPENDENT on-chain ground-truth to cross-verify aggregator data — the
"二重確認" second source. Reads a pool's actual ERC-20 reserves straight from chain via
eth_call, so we can confirm an aggregator's reported pool is real on-chain (or flag it
as stale/wrong if the on-chain balance is ~0). No third-party package execution (母艦-clean).
"""
from __future__ import annotations

import urllib.request
import json
import time

import config


class RpcError(Exception):
    """Mantle RPC call failed — caller must treat as 'could not verify', not 'confirmed'."""


def _rpc(method: str, params: list, retries: int = 3):
    """One JSON-RPC call WITH retry+backoff on transient transport failure. Without this, a single
    timeout under a burst (e.g. aave.correct_pools reading ~18 reserves/supplies at once) makes that
    pool's correction silently fall back to the DefiLlama row — so the digest would show the
    aggregator's number for a flaky pool while its neighbours show the chain-corrected one. A
    node-level JSON-RPC error (deterministic) is NOT retried; only transport/timeout/parse are."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        config.MANTLE_RPC, data=body,
        headers={"Content-Type": "application/json", "User-Agent": config.HTTP_UA},
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            if isinstance(out, dict) and out.get("error"):
                raise RpcError(str(out["error"]))   # node says no — deterministic, don't retry
            return out.get("result")
        except RpcError:
            raise
        except Exception as e:  # noqa: BLE001 — transport/timeout/parse: transient → retry
            last = e
            if attempt < retries - 1:
                time.sleep(0.6 * (attempt + 1))   # 0.6s, 1.2s backoff
                continue
            raise RpcError(str(e)) from e
    raise RpcError(str(last))


def eth_call(to: str, data: str):
    return _rpc("eth_call", [{"to": to, "data": data}, "latest"])


def _addr32(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


# ERC-20 function selectors (keccak4 of signature) — well-known constants
SEL_DECIMALS = "0x313ce567"    # decimals()
SEL_BALANCEOF = "0x70a08231"   # balanceOf(address)
SEL_TOTALSUPPLY = "0x18160ddd" # totalSupply()


def erc20_decimals(token: str) -> int:
    r = eth_call(token, SEL_DECIMALS)
    return int(r, 16) if r and r != "0x" else 18


def erc20_total_supply(token: str) -> int:
    r = eth_call(token, SEL_TOTALSUPPLY)
    return int(r, 16) if r and r != "0x" else 0


def erc20_balance_of(token: str, holder: str) -> int:
    r = eth_call(token, SEL_BALANCEOF + _addr32(holder))
    return int(r, 16) if r and r != "0x" else 0


def pool_token_balance(token_address: str, pool_address: str):
    """Pool's on-chain balance of `token_address` (decimals-scaled).

    Returns (scaled_balance, decimals, raw). `token_address` may carry a chain prefix
    like 'mantle_0x...' (GeckoTerminal) — stripped here.
    """
    tok = (token_address or "").split("_")[-1]
    dec = erc20_decimals(tok)
    raw = erc20_balance_of(tok, pool_address)
    return raw / (10 ** dec), dec, raw


# --- contract-truth primitives (for the on-chain verification layer; see onchain.py) ---
SEL_SYMBOL = "0x95d89b41"   # symbol()
SEL_OWNER = "0x8da5cb5b"    # owner()
# EIP-1967 implementation slot = keccak256("eip1967.proxy.implementation") - 1
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


def eth_get_code(addr: str) -> str:
    return _rpc("eth_getCode", [addr, "latest"]) or "0x"


def eth_get_storage_at(addr: str, slot: str) -> str:
    return _rpc("eth_getStorageAt", [addr, slot, "latest"]) or "0x"


def code_size(addr: str) -> int:
    """Deployed-code size in bytes (0 = EOA / no code / nonexistent). Raises on RPC failure."""
    c = eth_get_code(addr)
    return (len(c) - 2) // 2 if c and c != "0x" else 0


def _decode_abi_string(hexs: str) -> str:
    """Decode an ABI-encoded string (offset+len+data) or a bytes32-style symbol."""
    if not hexs or hexs == "0x":
        return ""
    try:
        b = bytes.fromhex(hexs[2:])
        if len(b) >= 64:
            length = int.from_bytes(b[32:64], "big")
            if 0 < length <= len(b) - 64:
                return b[64:64 + length].decode("utf-8", "replace")
        return b.rstrip(b"\x00").decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def _word_to_address(hexs: str):
    """Last 20 bytes of a 32-byte word as an address; None if zero/empty."""
    if not hexs or hexs == "0x":
        return None
    h = hexs[2:].rjust(64, "0")
    addr = "0x" + h[-40:]
    return None if int(addr, 16) == 0 else addr


def erc20_symbol(token: str) -> str:
    """Best-effort on-chain symbol; '' if not exposed (reverts) — never raises."""
    try:
        return _decode_abi_string(eth_call(token, SEL_SYMBOL)).strip()
    except RpcError:
        return ""


def contract_owner(addr: str):
    """Best-effort owner() address; None if the contract doesn't expose it (reverts)."""
    try:
        return _word_to_address(eth_call(addr, SEL_OWNER))
    except RpcError:
        return None


def proxy_implementation(addr: str):
    """EIP-1967 implementation address if `addr` is an upgradeable proxy, else None."""
    try:
        return _word_to_address(eth_get_storage_at(addr, EIP1967_IMPL_SLOT))
    except RpcError:
        return None
