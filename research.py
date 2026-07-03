#!/usr/bin/env python3
"""MantleFi — CLI orchestrator (read-only research, no trading).

Usage:
  python research.py scan                      # classify all Mantle yield pools (no key)
  python research.py judge <project> [symbol]  # full 釣り場レポート for a target (no key)
  python research.py token <SYM>               # find a token's Mantle pools by exact identity
  python research.py report <project> [symbol] # write a plain-language research report (Markdown) to examples/
  python research.py example                    # reproduce the USDY🟢 / SUSDE🔴 live example
  python research.py correspondence             # all-pool DeFiLlama↔Mantle verified table -> examples/
  python research.py validation                 # (re)generate the blind-test worksheet + answer key -> examples/
  python research.py agent "<自然文の質問>"      # the self-hosted research AGENT (needs NIM key)

scan/judge/token/example are the deterministic ENGINE (stdlib only, no key). `agent` wraps the
same engine as TOOLS under a NIM-backed ReAct loop — the LLM only routes the question and
narrates engine output; it never originates a number (see agent.py). Every number is fetched
live; missing data => ❔不明 (abstain), never guessed.
"""
from __future__ import annotations

import os
import sys

import config
import data_sources as ds
import report
import tools

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")


def cmd_scan():
    print(tools.tool_sonar())


def cmd_judge(project, symbol=None):
    print(tools.tool_judge(project, symbol))


def cmd_token(symbol):
    print(tools.tool_find_token(symbol))


def cmd_correspondence():
    """All-pool correspondence table: every Mantle yield pool's DeFiLlama claim cross-checked
    against each token's REAL on-chain identity on Mantle (symbol/contract) with a Mantlescan
    link per token, PLUS a Mantle DeFi protocol/DEX directory so every pool clicks through to its
    actual venue (Fluxion / Merchant Moe / Aave …). Official sites are sourced from DefiLlama —
    never hand-typed (a wrong DeFi URL is a security hazard). Saves examples/correspondence.md."""
    import onchain
    from collections import defaultdict
    from datetime import datetime, timezone
    try:
        pools, _ = ds.mantle_yield_pools()
    except ds.FetchError as e:
        print(f"❔ 取得失敗（推測しない・要再試行）: {e}")
        return
    try:
        directory, _ = ds.protocol_directory()
    except ds.FetchError:
        directory = {}   # links degrade to plain names; never block the artifact
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    glyph = {"verified": "✅", "flag": "⚠", "abstain": "❔"}

    def site(slug):
        return (directory.get(slug) or {}).get("url") or ""

    def proj_link(slug):
        url = site(slug)
        return f"[{slug}]({url})" if url else (slug or "?")

    def cell(addr, declared):
        idt = onchain.token_identity(addr, declared)
        if not idt.get("present"):
            return "—"
        note = "（表記相違・要確認）" if idt.get("match") is False else ""
        return f"{glyph.get(idt['status'], '・')} {idt.get('onchain_symbol') or '?'} " \
               f"[{idt['addr']}]({idt['link']}){note}"

    # Mantle DeFi protocol/DEX directory — each pool's venue (incl. known DEXes even at 0 pools today)
    agg = defaultdict(lambda: {"pools": 0, "tvl": 0.0})
    for p in pools:
        a = agg[p.get("project")]
        a["pools"] += 1
        a["tvl"] += (p.get("tvlUsd") or 0)
    for s in config.MANTLE_DEX_SLUGS:
        agg.setdefault(s, {"pools": 0, "tvl": 0.0})
    prot = sorted(agg.items(), key=lambda kv: (kv[1]["pools"], kv[1]["tvl"]), reverse=True)

    md = ["# MantleFi — 全プール対応表 ＋ Mantle DeFi プロトコル/DEX 一覧",
          f"_{as_of} 時点 · Mantle チェーン直読み確認 · 公式サイトは DefiLlama 由来（推測なし）· 無料・読み取り専用_",
          "",
          "## Mantle の DeFi プロトコル/DEX 一覧（各プールの提供元・公式サイト）",
          "各プールが実際に動いている Mantle 上のプロトコル。🔄=DEX（取引所）。公式サイトのリンクから実物を確認できます。",
          "",
          "| プロトコル | 種別 | プール数 | 合計TVL | 公式サイト | DefiLlama |",
          "|---|---|---|---|---|---|"]
    for slug, a in prot:
        name = (directory.get(slug) or {}).get("name") or slug
        kind = "🔄 DEX" if slug in config.MANTLE_DEX_SLUGS else "—"
        url = site(slug)
        site_cell = f"[公式サイト↗]({url})" if url else "—"
        page = config.DEFILLAMA_PROTOCOL_PAGE.format(slug=slug)
        md.append(f"| {name} | {kind} | {a['pools']} | {tools._short_usd(a['tvl'])} | "
                  f"{site_cell} | [DefiLlama]({page}) |")

    rows = sorted(pools, key=lambda p: p.get("tvlUsd") or 0, reverse=True)
    md += ["",
           "## 全プール対応表（DeFiLlama ↔ Mantle チェーン）",
           "DeFiLlama が示す各プールのトークンを Mantle チェーンの実アドレスで直接読み、シンボル/コントラクトが",
           "実在・一致するか確認した表。プール名は提供元の公式サイト、トークンは Mantlescan へリンク（誰でも再確認可）。",
           "✅=実在・一致 ／ ⚠=コントラクト無し or 表記相違 ／ ❔=取得失敗（推測しない）。",
           "",
           "| # | プール | 年利 | TVL | 原資産（チェーン確認） | 報酬トークン（チェーン確認） |",
           "|---|---|---|---|---|---|"]
    for i, p in enumerate(rows, 1):
        und = (p.get("underlyingTokens") or [None])[0]
        rew = (p.get("rewardTokens") or [None])[0]
        md.append(f"| {i} | {proj_link(p.get('project'))}·{p.get('symbol', '?')} | "
                  f"{(p.get('apy') or 0):.1f}% | {tools._short_usd(p.get('tvlUsd') or 0)} | "
                  f"{cell(und, p.get('symbol'))} | {cell(rew, None)} |")
    md += ["", f"_計 {len(rows)} プール。出典: Mantle チェーン（rpc.mantle.xyz）で直接確認 ／ "
               "DefiLlama（集計値・プロトコル公式サイト）。_"]
    out = "\n".join(md)
    print(out)
    os.makedirs(EXAMPLES_DIR, exist_ok=True)
    path = os.path.join(EXAMPLES_DIR, "correspondence.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out + "\n")
    print(f"\n[saved] {path}")


def cmd_report(project, symbol=None):
    """Produce a verifiable research report (verdict + 5 checks + on-chain verification + sources
    + limitations) and save a plain-language Markdown artifact to examples/ — the persistent
    deliverable a judge reads. Every number is sourced and (where possible) cross-checked on-chain."""
    try:
        target, yield_res, flow_res, wash_res, pool = tools.gather_judge(project, symbol)
    except ds.FetchError as e:
        print(f"❔ レポート生成失敗（要再試行・推測しない）: {e}")
        return
    rep = report.build_report(target, yield_res=yield_res, flow_res=flow_res,
                              wash_res=wash_res, pool=pool)
    md = report.render_md(rep)
    print(md)
    os.makedirs(EXAMPLES_DIR, exist_ok=True)
    slug = (symbol or project).replace("/", "_").replace(" ", "_")
    base = os.path.join(EXAMPLES_DIR, slug)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(f"\n[saved] {base}.md")


def cmd_example():
    """The 'friend' walkthrough: ask what's good yield → overview → two deep dives, one clean,
    one where the chain shows what the dashboard hides. Numbers are LIVE (drift over time);
    the classifications and the on-chain facts are the point."""
    print("=" * 60)
    print("「Mantle で利回り良いのは？」— まず一覧（利回り軸で分類／薄いプールは末尾『小』）")
    print("=" * 60)
    cmd_scan()
    print("\n" + "=" * 60)
    print("① 🟢 地味だが本物: Ondo USDY（報酬ゼロでも残る実利回り＝米国債クーポン）")
    print("=" * 60)
    cmd_judge("ondo-yield-assets", "USDY")
    print("\n" + "=" * 60)
    print("② 利回りの中身をチェーンで実測: Aave USDT0（実需の金利＋実際の報酬を一次データから再構成）")
    print("=" * 60)
    cmd_judge("aave-v3", "USDT0")
    print("\n" + "-" * 60)
    print("これが普通のチャットボットとの差：実需の金利は Mantle チェーンの貸付契約から直接実測し、")
    print("報酬は Merkl の実際の配布量（日次報酬÷供給額）から自分で計算する。集計サイトの読み上げでも、")
    print("配布側の自己申告（条件を満たした人向けの最大値）でもなく、一次データからの再構成。/report で保存できる。")
    print("-" * 60)


# Curated 10-pool blind-test set (covers every class). Symbols are DefiLlama's; the engine corrects
# Aave (chain base + real reward) and resolves the on-chain casing (sUSDe/USDe) at generation time.
_VALIDATION_SET = [
    ("ondo-yield-assets", "USDY"), ("aave-v3", "GHO"), ("aave-v3", "SUSDE"),
    ("fluxion-network", "USDT0-BSB"), ("aave-v3", "USDC"), ("aave-v3", "SYRUPUSDT"),
    ("aave-v3", "WETH"), ("aave-v3", "USDE"), ("aave-v3", "USDT0"), ("fluxion-network", "OPG-USDT0"),
]


def _vpct(v):
    return f"{v:.2f}%" if isinstance(v, (int, float)) else "—"


def cmd_validation():
    """(Re)generate the blind-test worksheet + answer key from the LIVE corrected engine, so the two
    files never drift from what MantleFi actually judges (Aave shows chain base + real reward + GROSS
    supplied; casing is on-chain). The worksheet hides the verdict; the key holds it. ≥8/10 human-vs-
    engine agreement = the accuracy criterion (BRIEF.md). Saves examples/validation_{worksheet,answerkey}.md."""
    rows, any_gross = [], False
    for i, (proj, sym) in enumerate(_VALIDATION_SET, 1):
        try:
            _t, y, f, w, pool = tools.gather_judge(proj, sym)
        except ds.FetchError as e:
            print(f"❔ {proj} {sym}: 取得失敗 {e}"); continue
        if not y:
            print(f"❔ {proj} {sym}: プールが見つからず — skip"); continue
        n = y["numbers"]
        basis = (pool.get("_aave") or {}).get("tvl_basis")
        any_gross = any_gross or basis == "gross"
        trade = "—"
        if w and w.get("numbers", {}).get("tx/unique_wallet") is not None:
            wn = w["numbers"]
            trade = f"買{wn.get('buyers')}/売{wn.get('sellers')}・1人{wn.get('tx/unique_wallet')}回"
        rows.append({
            "i": i, "label": f"{pool.get('symbol') or sym}·{proj.split('-')[0]}",
            "apy": _vpct(n.get("apy")), "base": _vpct(n.get("apyBase")), "reward": _vpct(n.get("apyReward")),
            "tvl": tools._short_usd(n.get("tvlUsd")) + ("＊" if basis == "gross" else ""),
            "trade": trade, "verdict": config.CLASS_PLAIN.get(report._headline_class(y, f, w), "❔"),
        })
    foot = "\n＊ Aave の規模はチェーンの**供給総額**（実際に預けられた総額）。それ以外は DefiLlama の預入。\n" if any_gross else "\n"
    ws = ["# MantleFi 精度チェック（盲検シート）— ren 記入用", "",
          "**目的**：MantleFi の判定が人間の直感と一致するかを確かめる（≥8/10一致で「正確」を実証）。", "",
          "## やり方（5分）",
          "1. 下の表は **engineの判定を隠して**、生の数字だけ並べています。",
          "2. 各プールを見て、**自分の直感で**「判定」欄に次のどれかを書く：",
          "   - 🟢**本物**（金利・手数料など実需の利回り）",
          "   - 🟡**報酬頼み**（配布報酬が大きい＝止まれば減る）",
          "   - 🔴**罠**（ほぼ全部が配布報酬／取引が数人に偏る 等）",
          "   - ⚫**ゼロ/死**（利回りがほぼ無い）",
          "   - ❔**わからない**",
          "3. ヒント：実需の金利が年利の半分以上＝本物寄り／配布報酬が大半＝報酬頼み or 罠／「1人〇〇回」が極端＝自作自演の疑い／年利1%未満＝実質ゼロ。",
          "4. **全部書き終えてから** `examples/validation_answerkey.md` を開いて答え合わせ。一致数を数える。", "",
          "## 判定シート（engine判定は伏せてあります）",
          "| # | プール | 年利 | 実需の金利 | 配布報酬 | 規模(TVL) | 取引(DEXのみ) | あなたの判定 |",
          "|---|--------|------|-----------|----------|-----------|---------------|-------------|"]
    ws += [f"| {r['i']} | {r['label']} | {r['apy']} | {r['base']} | {r['reward']} | {r['tvl']} | {r['trade']} | ____________ |"
           for r in rows]
    ws += [foot, "記入後 → `examples/validation_answerkey.md` で答え合わせ。一致 ___ / 10。"]
    ak = ["# 答え合わせ（先に見ないこと）— MantleFi engine の判定", "",
          "盲検シートを全部記入してから開く。あなたの判定と下を突き合わせ、一致数を数える。", "",
          "| # | プール | engineの判定 |", "|---|--------|-------------|"]
    ak += [f"| {r['i']} | {r['label']} | {r['verdict']} |" for r in rows]
    ak += ["", "**8件以上一致＝MantleFi は人間の直感と整合（正確性を実証）。** 不一致は理由をメモ（engineが厳しすぎ/甘すぎ/人間の見落とし）。"]
    os.makedirs(EXAMPLES_DIR, exist_ok=True)
    with open(os.path.join(EXAMPLES_DIR, "validation_worksheet.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(ws) + "\n")
    with open(os.path.join(EXAMPLES_DIR, "validation_answerkey.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(ak) + "\n")
    print(f"[saved] {len(rows)}/10 行で validation_worksheet.md + validation_answerkey.md を再生成")


def cmd_agent(question):
    import agent  # lazy import: only the agent path needs the LLM layer
    # Same LLM routing as the web (Groq primary, NIM auto-fallback) so the whole product is coherent;
    # numbers/verdicts stay engine-owned (no-fab) regardless of which model phrases the answer.
    print(agent.run(question,
                    loop_backend=config.WEB_AGENT_LOOP_BACKEND,
                    final_backend=config.WEB_AGENT_FINAL_BACKEND,
                    final_model=config.WEB_AGENT_FINAL_MODEL)["full"])   # CLI shows the full 根拠; stateless


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "scan":
        cmd_scan()
    elif cmd == "judge" and len(args) >= 2:
        cmd_judge(args[1], args[2] if len(args) >= 3 else None)
    elif cmd == "token" and len(args) >= 2:
        cmd_token(args[1])
    elif cmd == "report" and len(args) >= 2:
        cmd_report(args[1], args[2] if len(args) >= 3 else None)
    elif cmd == "example":
        cmd_example()
    elif cmd == "correspondence":
        cmd_correspondence()
    elif cmd == "validation":
        cmd_validation()
    elif cmd == "agent" and len(args) >= 2:
        cmd_agent(" ".join(args[1:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
