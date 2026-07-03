"""MantleFi — the engine exposed as AGENT TOOLS.

Each `tool_*` returns a plain-text OBSERVATION string and NEVER raises into the agent
loop: on a fetch/RPC failure it returns an honest "❔取得失敗 — 要再試行" line instead of
crashing. These same functions back the CLI (research.py), so there is ONE code path for
both the human CLI and the agent — and every number still comes only from the deterministic
classifier/report (the LLM never originates a number; see agent.py).
"""
from __future__ import annotations

import config
import data_sources as ds
import classify
import report
import rpc
import aave


# ---------------------------------------------------------------- shared helpers
def _find_pool(pools, project, symbol=None):
    cands = [p for p in pools if project is None or p.get("project") == project]
    if symbol:
        cands = [p for p in cands if (p.get("symbol") or "").upper() == symbol.upper()]
    cands.sort(key=lambda p: p.get("tvlUsd") or 0, reverse=True)
    return cands[0] if cands else None


def _gecko_match(gecko_pools, project, symbol=None):
    """Match a GeckoTerminal pool to a DEX project+symbol for wash analysis. The pool's name
    must contain ALL the symbol tokens (order-independent, e.g. "USDT0-BSB" matches
    "BSB / USDT0 0.3%"). There is deliberately NO 'first pool on this DEX' fallback — that
    attributed a DIFFERENT pool's buyers/sellers to the queried one (a fatal number bug, see
    config.MANTLE_DEX_SLUGS). No genuine symbol match → None → wash is reported as N/A, never guessed."""
    sym_parts = [s for s in (symbol or "").upper().replace("/", "-").split("-") if s]
    if not sym_parts:
        return None
    for gp in gecko_pools:
        name = (gp.get("attributes", {}).get("name") or "").upper()
        if all(part in name for part in sym_parts):
            return gp
    return None


# ---------------------------------------------------------------- renderers (pure)
def _short_usd(v) -> str:
    """162,672,336 -> '$163M' ; 1,234,567 -> '$1.2M' ; 950 -> '$950'. Keeps rows phone-narrow."""
    v = float(v or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            q = v / div
            return f"${q:.0f}{suf}" if q >= 100 else f"${q:.1f}{suf}"
    return f"${v:.0f}"


# scan is yield-axis only, so only these 4 classes ever appear → a glyph + legend is enough
# to keep the table narrow (full class names are too wide to align on a phone).
_CLASS_GLYPH = {
    config.CLASS_REAL_YIELD: "🟢", config.CLASS_REWARD_DEP: "🟡",
    config.CLASS_EMISSION_TRAP: "🔴", config.CLASS_DEAD: "⚫", config.CLASS_UNKNOWN: "❔",
    config.CLASS_VOLATILE: "⚠️",
}
# consistent ordering: real yield (🟢) first, traps grouped lower — MantleFi is a real-yield
# FINDER, not a high-APY ranker. (Leading by raw APY would surface emission traps at the top.)
_SCAN_ORDER = [config.CLASS_REAL_YIELD, config.CLASS_REWARD_DEP,
               config.CLASS_EMISSION_TRAP, config.CLASS_DEAD, config.CLASS_UNKNOWN]


def _scan_rank(cls) -> int:
    return _SCAN_ORDER.index(cls) if cls in _SCAN_ORDER else len(_SCAN_ORDER)


def render_scan(rows) -> str:
    """Phone-narrow, monospace-friendly table, consistently ordered: by CLASS (🟢→🟡→🔴→⚫)
    then TVL desc, so real yield clusters at the top and the *significant* pools lead each
    group (sorting by APY would bury the headline real yields under tiny dust pools that
    happen to print a high %). Glyph + legend keeps it narrow; rendered in <pre> on Telegram
    so columns align (CLI aligns too). Source = the name only (DefiLlama); the raw /pools URL
    is reader-noise and lives in the methodology docs and the deep judge/flow reports."""
    rows = sorted(rows, key=lambda r: (_scan_rank(r["class"]),
                                       -(r["numbers"].get("tvlUsd") or 0)))
    out = [
        f"🔍 Mantle 利回りプール {len(rows)} 件 (LIVE)",
        "凡例 🟢実利回り 🟡報酬依存 🔴報酬頼み ⚫ゼロ/微利 ❔不明",
        "同色内はTVL大きい順 / 末尾小=資金少なく出入りしにくい",
        "─" * 30,
    ]
    for r in rows:
        n = r["numbers"]
        proj, _, sym = (r["target"] or "").partition(" ")
        label = f"{sym or proj}·{proj.split('-')[0].title()}"[:15]
        glyph = _CLASS_GLYPH.get(r["class"], "・")
        tvl = n.get("tvlUsd") or 0
        thin = " 小" if 0 < tvl < config.SMALL_TVL_USD else ""
        out.append(f"{glyph} {label:<15}{_short_usd(tvl):>7}{(n.get('apy') or 0):>5.1f}%{thin}")
    out.append("出典: DefiLlama｜深掘りは Mantle チェーン確認")
    return "\n".join(out)


def render_token(symbol, matches, rejected, errors) -> str:
    """Format the token-finder report (extracted verbatim from research.cmd_token)."""
    out = [
        f"🔎 '{symbol}' の Mantle プール検索  (変種総当たり × GeckoTerminal+DexScreener × 厳密同一判定)",
        "─" * 60,
    ]
    if not matches:
        if errors:
            out.append(f"⚠ 検索が一部失敗（rate-limit/network {len(errors)}件）→『不在』と断定しない・要再試行:")
            for e in errors[:6]:
                out.append(f"     ! {e}")
        else:
            out.append(f"❔ Mantle の取引所(DEX)に '{symbol}' のプールは見つからず（DEXで売買されていない銘柄かも。"
                       "利回り/貸付プールなら『scan』や『judge <protocol> {symbol}』で見られます）")
    else:
        out.append(f"✅ {len(matches)} プール一致:")
        for m in matches:
            srcs = "+".join(m.get("sources", [m["source"]]))
            agree = f"  ✓{len(m['sources'])}ソース一致" if len(m.get("sources", [])) > 1 else ""
            out.append(f"\n  [{srcs}/{m['dex']}] {m['name']}{agree}")
            out.append(f"     流動性 ${m['liquidity_usd']:,.0f} / 24h出来高 ${m['volume_24h']:,.0f}")
            out.append(f"     token={m['token_id']}")
            out.append(f"     pool ={m['pool_address']}")
            if m["liquidity_usd"] < config.DUST_LIQUIDITY_USD:
                out.append(f"     → ⚫ 流動性ほぼ無し（< ${config.DUST_LIQUIDITY_USD:,}＝実質取引不可）")
            if "_gp" in m:
                w = classify.classify_wash(m["_gp"], config.GECKO_MANTLE_POOLS)
                out.append(f"     → wash軸: {w['class']}  {w.get('note') or ''}")
            # independent on-chain ground-truth (二重確認 via Mantle RPC; no key, no 3rd-party exec)
            if m.get("token_id") and m.get("pool_address"):
                try:
                    bal, _dec, raw = rpc.pool_token_balance(m["token_id"], m["pool_address"])
                    flag = " ⚠ on-chain残高≈0＝aggregator数字が stale/誤りの疑い" if raw == 0 else ""
                    out.append(f"     ⛓ on-chain裏取り(Mantle RPC): pool は base token を {bal:,.4f} 保有{flag}")
                    tok = (m["token_id"] or "").split("_")[-1]
                    sup = rpc.erc20_total_supply(tok)
                    if sup > 0:
                        pct = raw / sup * 100
                        note = "（高=流動性が1プールに集中＝脆い）" if pct >= 50 else ""
                        out.append(f"     ⛓ 供給集中度: この pool が総供給の {pct:.2f}% を保有{note}")
                except rpc.RpcError as e:
                    out.append(f"     ⛓ on-chain裏取り: 取得失敗（要再試行・確認できず）: {e}")
    if matches:
        out.append(f"\n   集中度(Q2): {len(matches)} プールで取引（少=流動性が狭い）。各行『供給%』も参照。"
                   "\n   ※ wallet単位の top-保有率は無料no-keyで不可（要 Dune/subgraph）→ 供給%・プール数・取引参加者で代替")
    if matches and errors:
        out.append(f"\n⚠ 注意: クエリ {len(errors)}件 失敗（rate-limit等）＝結果は不完全な可能性・要再試行")
    if rejected:
        out.append(f"\n🚫 拾わなかった紛らわしいもの（{len(rejected)}件＝偽物を除外した証拠）:")
        for r in sorted({(r['src'], r['symbol'], r['why']) for r in rejected}):
            out.append(f"  - {r[0]}: {r[1]}  ({r[2]})")
    return "\n".join(out)


# ---------------------------------------------------------------- agent tools
def tool_sonar() -> str:
    """Broad view: every Mantle yield pool, pre-classified by yield axis. Use first to
    pick candidates worth a deep judge."""
    try:
        pools, url = ds.mantle_yield_pools()
    except ds.FetchError as e:
        return f"❔ sonar 取得失敗（推測しない・要再試行）: {e}"
    pools = aave.correct_pools(pools)   # Aave rows: chain base + Merkl real reward (not the headline)
    rows = [classify.classify_yield(p, url) for p in pools]
    return render_scan(rows)   # render_scan orders by class then APY


def gather_judge(project: str, symbol: str | None = None):
    """Collect the raw analyses + the DefiLlama pool row for a target. Used by BOTH tool_judge
    (text observation) and research.cmd_report (structured artifact) so they never diverge.
    Raises ds.FetchError only if the pools list itself can't be fetched."""
    pools, purl = ds.mantle_yield_pools()
    pool = _find_pool(pools, project, symbol)
    if pool is None and symbol:
        # The project guess was wrong (an LLM agent can mis-route, e.g. ondo for USDT0). As long as
        # the SYMBOL is right, resolve by symbol alone (highest-TVL pool) so judge self-corrects to
        # the real pool instead of analyzing the wrong project's flow. Keeps the agent path as
        # reliable as the deterministic /facts router for token questions.
        pool = _find_pool(pools, None, symbol)
    # Aave: replace the aggregator's reward headline with the reconstructed truth (chain base +
    # Merkl's real distribution) BEFORE classifying, so verdict + numbers + report all use it.
    if pool:
        pool = aave.correct_pool(pool)
    # Use the RESOLVED pool's real project for flow/wash (not the caller's possibly-wrong guess).
    eff_project = (pool.get("project") if pool else project)
    yield_res = classify.classify_yield(pool, purl) if pool else None
    flow_res = classify.classify_flow(eff_project)   # never raises (returns ❔不明 on failure)
    # wash/concentration only applies to DEX pools. A lending position (Aave etc.) has no DEX
    # volume, so a GeckoTerminal symbol-match would be a DIFFERENT pool — attributing its
    # buyers/sellers to the lending pool is a false 🔴 (and a Mantle downgrade). Skip for non-DEX.
    wash_res = None
    if eff_project in config.MANTLE_DEX_SLUGS:
        try:
            gpools, gurl = ds.gecko_mantle_pools()
            gp = _gecko_match(gpools, eff_project, symbol)
            if gp:
                wash_res = classify.classify_wash(gp, gurl)
        except ds.FetchError:
            wash_res = None
    target = (pool and pool.get("project") + " " + (pool.get("symbol") or "")) or project
    if symbol and pool is None:
        target = f"{project} {symbol}"
    return target, yield_res, flow_res, wash_res, pool


def tool_judge(project: str, symbol: str | None = None) -> str:
    """Deep 5-question verdict for ONE target across yield+flow+wash axes PLUS on-chain
    verification. `project` is a DefiLlama protocol slug (see config.MANTLE_PROTOCOL_SLUGS).
    For a TOKEN use tool_find_token."""
    try:
        target, yield_res, flow_res, wash_res, pool = gather_judge(project, symbol)
    except ds.FetchError as e:
        return f"❔ judge 取得失敗（要再試行・推測しない）: {e}"
    # pass the raw pool row so report runs the on-chain verification block (rewardTokens /
    # underlying contract truth) — the trust-minimized layer a generalist LLM can't do.
    return report.render(target, yield_res=yield_res, flow_res=flow_res, wash_res=wash_res, pool=pool)


def tool_find_token(symbol: str) -> str:
    """Locate a token's Mantle pools by EXACT identity (anti-impostor) across
    GeckoTerminal+DexScreener + on-chain RPC cross-check. Use when the user names a TOKEN."""
    matches, rejected, errors = ds.find_token_pools(symbol)   # never raises (collects errors)
    return render_token(symbol, matches, rejected, errors)


def tool_flow(slug: str) -> str:
    """Capital-flow axis only (token amount vs USD): real inflow / price illusion / outflow.
    `slug` is a DefiLlama protocol slug (see config.MANTLE_PROTOCOL_SLUGS)."""
    res = classify.classify_flow(slug)            # never raises (returns ❔不明 on failure)
    return report.render(res["target"], flow_res=res)
