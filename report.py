"""MantleFi — build a transparent, VERIFIABLE due-diligence report from the analyses.

A report = a verdict + the 5-question interrogation + an ON-CHAIN VERIFICATION block (the
trust-minimized part a generalist LLM can't do) + sources + limitations, stamped with the
time it was produced. It NEVER says buy/sell and NEVER calls a legit product a scam (see
CLAUDE.md). Unknowns are stated as unknown, not filled with guesses.

One builder (`build_report`) is the single source of truth; `render` (text, for the agent
observation/CLI) and `render_md` (Markdown artifact) both derive from it, so the surfaces
can never drift. (No JSON surface: a human report, not a machine dump.)
"""
from __future__ import annotations

from datetime import datetime, timezone

import config
import onchain


def _num(v):
    if v is None:
        return "不明"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _mask_addr(a) -> str:
    a = a or ""
    return f"{a[:6]}…{a[-4:]}" if len(a) >= 12 else a


def _mantle_links(pool) -> dict:
    """Verifiable links for a pool: DefiLlama (where the number came from) + Mantlescan
    (verify the contract on-chain). Public-contract addresses go in the URL in full — they
    ARE the verification target; the human label shows the masked address."""
    if not pool:
        return {}
    links = {}
    pid = pool.get("pool")
    if pid:
        links["defillama"] = config.DEFILLAMA_POOL_URL.format(id=pid)
    # link BOTH the underlying token and the reward token (e.g. aManGHO) on Mantlescan, so the
    # "報酬トークンの正体を確認済み" claim ships with a clickable receipt anyone can re-verify.
    addrs = (pool.get("underlyingTokens") or []) + (pool.get("rewardTokens") or [])
    scans = [config.MANTLESCAN_TOKEN_URL.format(addr=a) for a in dict.fromkeys(a for a in addrs if a)]
    if scans:
        links["mantlescan"] = scans
    return links


def _flatten_links(links: dict):
    """(label, url) pairs — masked address in the human label, full address in the URL."""
    out = []
    if links.get("defillama"):
        # "集計元" not "数値の出典": for Aave pools the displayed numbers are reconstructed from
        # chain + Merkl (see onchain.aave_breakdown), so DefiLlama is the pool aggregator, not the
        # source of the figure. Accurate for every pool either way.
        out.append(("DefiLlama（集計元）", links["defillama"]))
    for url in links.get("mantlescan", []):
        addr = url.rstrip("/").split("/")[-1]
        out.append((f"Mantlescan {_mask_addr(addr)}（コントラクト確認）", url))
    return out


# ---------------------------------------------------------------- 5 questions
def five_questions(yield_res=None, flow_res=None, wash_res=None) -> list[str]:
    """このプールに実際に答えの出る問診だけを、初見の人がパッと分かる平易な日本語で返す。
    当てはまらない軸は『対象外』を並べず省く（Aaveは預金・借入の相殺で資金フローが測れない／
    貸付プールには取引が無くwashは起きない）＝5問の体裁より、答えの出る問いに絞る方が誠実。
    判定の表示は config.CLASS_PLAIN（平易な言い換え・engine 由来）を通す。"""
    qs = []
    P = config.CLASS_PLAIN

    # 資金の動き（実際の入出金か / 値上がりの錯覚か）。価格が動くプールでだけ意味を持つ問い。
    # Aave(相殺で量が測れない)やステーブル(価格≈$1で錯覚が起きない)では測れないので省く。
    if flow_res and flow_res["class"] not in (config.CLASS_UNKNOWN, None):
        d = flow_res["numbers"]
        h = d.get("headline") or d.get("30d") or d.get("7d") or {}
        win = h.get("window_days", "?")
        qs.append(f"資金の動きは？: {P.get(flow_res['class'], flow_res['class'])} "
                  f"〔この{win}日で お金の量 {h.get('flow%')}% / 金額 {h.get('usd%')}%〕")

    # 取引は自然か（大勢の自然な売買か、少数に偏った出来高か）。取引所(DEX)プールでだけ起きる現象。
    # 貸付/預入プールには取引が無いので『対象外』を並べず省く（washデータがある時だけ出す）。
    if wash_res and wash_res["class"] not in (config.CLASS_UNKNOWN, None):
        n = wash_res["numbers"]
        qs.append(f"取引は自然？: {P.get(wash_res['class'], wash_res['class'])} "
                  f"〔買い手 {n.get('buyers')}人 / 売り手 {n.get('sellers')}人、出来高÷預かり額 {n.get('vol/liq')}〕")

    # この利回りは続くか（実需の金利か / 配布報酬頼みか）＝どのプールにも当てはまる中核なので常に出す。
    if yield_res and yield_res["class"] not in (config.CLASS_UNKNOWN, None):
        n = yield_res["numbers"]
        qs.append(f"利回りは続く？: {P.get(yield_res['class'], yield_res['class'])} "
                  f"〔年利 {_num(n.get('apy'))}%＝実需の金利 {_num(n.get('apyBase'))}%＋運営の配布報酬 {_num(n.get('apyReward'))}%〕")
    else:
        qs.append("利回りは続く？: ❔分からない（内訳が取れていない）")

    # 利回りの原資は？続く？（旧「なぜ稼げる」＝出どころ と 旧「損する側は誰」＝誰が払うか は
    # 実は同じ問い。被害者めいた『損する』語を廃し『原資＝続くか』に一本化して統合する）
    mech = yield_res or flow_res or wash_res
    if wash_res and wash_res["class"] == config.CLASS_WASH:
        ans = "出来高の大半が少数の身内の行き来の可能性（実需の裏付けが薄い）"
    else:
        src = (mech.get("persist_condition") if mech else None) or "❔分からない"
        lasts = {config.CLASS_REAL_YIELD:    " → 実需が払うので続きやすい",
                 config.CLASS_REWARD_DEP:    " → 配布報酬ぶんは配布しだい",
                 config.CLASS_EMISSION_TRAP: " → 大半が配布報酬"
                 }.get(yield_res["class"] if yield_res else None, "")
        ans = f"{src}{lasts}"
    qs.append(f"利回りの原資は？続く？: {ans}")
    return qs


# ---------------------------------------------------------------- shared builders
_HEADLINE_ORDER = [config.CLASS_VOLATILE, config.CLASS_EMISSION_TRAP, config.CLASS_PRICE_ILLUSION, config.CLASS_WASH,
                   config.CLASS_OUTFLOW, config.CLASS_REWARD_DEP, config.CLASS_DEAD,
                   config.CLASS_REAL_INFLOW, config.CLASS_REAL_YIELD, config.CLASS_FLAT]


def _headline_class(yield_res, flow_res, wash_res) -> str:
    present = [r["class"] for r in (yield_res, flow_res, wash_res) if r and r.get("class")]
    return next((c for c in _HEADLINE_ORDER if c in present), config.CLASS_UNKNOWN)


def _clean_source(s: str) -> str:
    """Raw fetch URL -> a friendly name a beginner recognizes (no noisy llama.fi/pools URLs)."""
    low = (s or "").lower()
    if "llama.fi" in low or "defillama" in low:
        return "DefiLlama"
    if "gecko" in low:
        return "GeckoTerminal"
    if "dexscreener" in low:
        return "DexScreener"
    return s


def _sources_and_limits(yield_res, flow_res, wash_res):
    sources, limits = [], []
    for r in (yield_res, flow_res, wash_res):
        if not r:
            continue
        for s in r.get("sources", []):
            if s and s not in sources:
                sources.append(s)
        if r.get("limitations"):
            limits.append(r["limitations"])
    return sources, limits


def build_report(target, yield_res=None, flow_res=None, wash_res=None, pool=None, as_of=None) -> dict:
    """Single source of truth for a research report. `pool` (raw DefiLlama row) enables the on-chain
    verification block; `as_of` is injectable for reproducible tests/artifacts. Sources are framed
    chain-first: the Mantle chain is the truth we verify against, the aggregator is the raw claim."""
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    onchain_checks = onchain.audit(pool) if pool else []
    agg_raw, limits = _sources_and_limits(yield_res, flow_res, wash_res)
    agg = list(dict.fromkeys(_clean_source(s) for s in agg_raw))
    sources = []
    if onchain_checks:
        sources.append("Mantle チェーン（rpc.mantle.xyz）で直接確認")
    # cite where the reward distribution data came from (subject = the data, not the distributor)
    if any("Merkl" in (c.get("source") or "") for c in onchain_checks):
        sources.append("報酬の配布データ（Merkl）")
    sources += [f"{name}（集計データ・元の数値）" for name in agg]
    mech = yield_res or flow_res or wash_res
    return {
        "target": target,
        "as_of": as_of,
        "verdict": _headline_class(yield_res, flow_res, wash_res),
        "five_questions": five_questions(yield_res, flow_res, wash_res),
        "onchain_verification": onchain_checks,
        "links": _mantle_links(pool),
        "persist_condition": (mech.get("persist_condition") if mech else "—") or "—",
        "limitations": list(dict.fromkeys(limits)) or ["—"],
        "sources": sources or ["—"],
    }


# The folded depth (問診/持続条件/限界/出典) + per-finding on-chain receipts. Shared by the chat card
# (facts._judge_facts) AND the daily report card (monitor._investigate) so both fold out the SAME
# engine-derived depth — the two surfaces can't drift (the bug that made the report shallower).
def detail_payload(rep: dict) -> dict:
    return {
        "questions": rep.get("five_questions", []),
        "persist": rep.get("persist_condition", ""),
        "limitations": rep.get("limitations", []),
        "sources": rep.get("sources", []),
    }


def chain_receipts(rep: dict, pool: dict | None) -> list[dict]:
    """Each on-chain verification finding linked to the exact contract the engine read (underlying or
    reward token) on Mantlescan → the reader can re-verify it, not trust it. Masked addr is the text;
    the full addr is only in the clickable href. No prose — a compact chip, not a sentence."""
    under = next((a for a in ((pool or {}).get("underlyingTokens") or []) if a), None)
    reward = next((a for a in ((pool or {}).get("rewardTokens") or []) if a), None)
    items = []
    for c in rep.get("onchain_verification", []):
        label = c.get("label", "")
        addr = (reward or under) if "報酬" in label else under
        kind = ("報酬トークン" if "報酬" in label else "対象トークン") if addr else None
        u = config.MANTLESCAN_TOKEN_URL.format(addr=addr) if addr else None
        m = f"{addr[:6]}…{addr[-4:]}" if addr else None
        items.append({"t": f"{label}: {c['finding']}", "u": u, "m": m, "k": kind})
    return items


_STATUS_GLYPH = {"verified": "✅", "flag": "⚠", "abstain": "❔"}


def _onchain_lines(checks: list) -> list[str]:
    out = []
    for c in checks:
        out.append(f"  {_STATUS_GLYPH.get(c['status'], '・')} {c['label']}: {c['finding']}")
    return out


# ---------------------------------------------------------------- renderers (derive from build_report)
def render(target, yield_res=None, flow_res=None, wash_res=None, pool=None, as_of=None) -> str:
    """Plain-text report (used as the agent observation and by the CLI)."""
    rep = build_report(target, yield_res, flow_res, wash_res, pool, as_of)
    lines = [f"📋 MantleFi 調査レポート: {rep['target']}  ({rep['as_of']} 時点)",
             "─" * 52,
             f"判定: {config.CLASS_PLAIN.get(rep['verdict'], rep['verdict'])}",
             "",
             "チェック項目（このプールに当てはまるもの）:"]
    lines += [f"  {q}" for q in rep["five_questions"]]
    if rep["onchain_verification"]:
        lines += ["", "🔗 チェーンで直接確認:"]
        lines += _onchain_lines(rep["onchain_verification"])
    if rep.get("links"):
        lines += ["", "🔗 リンク:"]
        lines += [f"  {label}: {url}" for label, url in _flatten_links(rep["links"])]
    lines += ["",
              "注意点（このツールで分からないこと）: " + " / ".join(rep["limitations"]),
              "出典: " + " ／ ".join(rep["sources"])]
    return "\n".join(lines)


def render_md(rep: dict) -> str:
    """Markdown artifact (what a contest judge reads). Built from the same report dict."""
    md = [f"# MantleFi 調査レポート — {rep['target']}",
          f"_{rep['as_of']} 時点 · 読み取り専用 · 無料データ · 売買の推奨なし_",
          "",
          f"## 判定: {config.CLASS_PLAIN.get(rep['verdict'], rep['verdict'])}",
          "",
          "## チェック項目（このプールに当てはまるもの）"]
    md += [f"- {q}" for q in rep["five_questions"]]
    if rep["onchain_verification"]:
        md += ["", "## 🔗 チェーンで直接確認"]
        for c in rep["onchain_verification"]:
            md.append(f"- {_STATUS_GLYPH.get(c['status'], '・')} **{c['label']}** — {c['finding']}")
    if rep.get("links"):
        md += ["", "## 🔗 リンク"]
        md += [f"- [{label}]({url})" for label, url in _flatten_links(rep["links"])]
    md += ["", "## この利回りが続く条件", f"- {rep['persist_condition']}",
           "", "## 注意点（このツールで分からないこと）"]
    md += [f"- {x}" for x in rep["limitations"]]
    md += ["", "## 出典"]
    md += [f"- {s}" for s in rep["sources"]]
    return "\n".join(md)
