#!/usr/bin/env python3
"""MantleFi — heartbeat monitor (定点観測 agent crew). One scheduled (cron) run does:

    fetch Mantle yields  →  detect what's notable vs the last snapshot  →  a fan-out of
    "investigator" agents (maverick) chain-verifies each notable pool  →  one "editor" agent
    (glm-5.1) merges their findings into a plain daily digest  →  push to Telegram  →  save
    the snapshot + the digest artifact.

This is the "agent 群" (not a single pipeline): N investigator agents each run their OWN ReAct
loop (deciding which engine tools to call to verify one pool), then one editor agent synthesizes.
Right model per job — maverick for the high-volume fan-out, glm-5.1 for the single smart edit.

Invariants (same as the rest of MantleFi):
  - read-only, free data, free models; works WITHOUT any key (degrades to an engine-only digest).
  - no-fabrication: every number/verdict comes from the deterministic engine; the LLMs route and
    narrate only. The editor's prose is checked by the fabrication guard and dropped if it invents.
  - judge-not-FOMO: surfaces real yield AND traps; never buy/sell advice; never downgrades Mantle.

Run:  python3 monitor.py          # full run: build digest, push to known Telegram chats, persist
      python3 monitor.py --dry    # build + print the digest only (no push, no state writes)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import config
import data_sources as ds
import classify
import report
import tools
import agent
import nim
import aave
import telegram_bot as tg


# ---------------------------------------------------------------- snapshot state (between runs)
def _load_snapshot() -> tuple[dict, str | None]:
    """Return (pools_by_id, prev_as_of). Handles the legacy flat format (no as_of)."""
    p = config.MONITOR_STATE_PATH
    try:
        obj = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}, None
    if isinstance(obj, dict) and "pools" in obj:
        return obj.get("pools") or {}, obj.get("as_of")
    return obj, None                          # legacy flat {id: {...}} → no baseline time


def _save_snapshot(snap: dict, as_of: str) -> None:
    p = config.MONITOR_STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"as_of": as_of, "pools": snap}, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- fetch + classify (pure engine)
def _scan_pools():
    """Every Mantle yield pool, pre-classified on the yield axis (deterministic, no LLM).
    Returns a list of compact dicts. Raises ds.FetchError if the pools list can't be fetched."""
    pools, url = ds.mantle_yield_pools()
    pools = aave.correct_pools(pools)   # Aave rows: chain base + Merkl real reward (not the headline)
    out = []
    for p in pools:
        res = classify.classify_yield(p, url)
        proj = p.get("project") or ""
        out.append({
            "id": p.get("pool") or f"{proj}-{p.get('symbol')}",
            "project": proj,
            "symbol": p.get("symbol"),
            "label": f"{p.get('symbol') or proj}·{proj.split('-')[0].title()}",
            "class": res["class"],
            "apy": res["numbers"].get("apy") or 0.0,
            "base": res["numbers"].get("apyBase"),
            "reward": res["numbers"].get("apyReward"),
            "tvl": res["numbers"].get("tvlUsd") or 0.0,
            "chain_derived": bool(p.get("_aave")),   # True = Aave row re-computed from chain (getReserveData); else DefiLlama
        })
    return out


# ---------------------------------------------------------------- watchlist (what to investigate)
_REAL_ISH = (config.CLASS_REAL_YIELD, config.CLASS_REWARD_DEP)


def _plain(cls) -> str:
    return config.CLASS_PLAIN.get(cls, cls)


def _short(cls) -> str:
    """A compact class label (no parenthetical) for the '前回は…今回は…' change notes."""
    return _plain(cls).split("（")[0].strip()


def _build_watchlist(current, snapshot):
    """The BOUNDED set worth an investigator agent this run (so the crew never spins emptily):
      (a) the top-N sizeable real-yield pools (the 本物候補 worth re-verifying on-chain), and
      (b) any pool whose class CHANGED, or whose apy/tvl moved a lot, vs the last snapshot (news).
    Deduped, changes first, hard-capped at MONITOR_MAX_INVESTIGATE. Each entry carries a reason."""
    by_id = {c["id"]: c for c in current}
    reason = {}   # id -> why it's on the list

    # (b) notable changes first (they're the "news") — 最小規模以上のみ（極小DEXの出来高ノイズを「変化」に出さない）
    for c in current:
        if c["tvl"] < config.MONITOR_MIN_TVL_USD:
            continue
        prev = snapshot.get(c["id"])
        if prev is None:
            # "新規" only makes sense against a baseline; on the very first run (empty snapshot)
            # every pool is unseen, so skip — these get picked up as routine 本物候補 below.
            if snapshot and c["class"] in _REAL_ISH and c["tvl"] >= config.MONITOR_MIN_TVL_USD:
                reason[c["id"]] = "新規の利回りプール"
            continue
        if prev.get("class") != c["class"]:
            reason[c["id"]] = f"前回は{_short(prev.get('class'))}、今回は{_short(c['class'])}に変化"
            continue
        prev_apy, prev_tvl = prev.get("apy") or 0.0, prev.get("tvl") or 0.0
        if abs(c["apy"] - prev_apy) >= config.MONITOR_APY_MOVE_PT:
            arrow = "上昇" if c["apy"] > prev_apy else "低下"
            reason[c["id"]] = f"利回りが{abs(c['apy']-prev_apy):.1f}pt {arrow}"
        elif prev_tvl and abs(c["tvl"] - prev_tvl) / prev_tvl * 100 >= config.MONITOR_TVL_MOVE_PCT \
                and c["tvl"] >= config.MONITOR_MIN_TVL_USD:
            reason[c["id"]] = "資金が大きく動いた"

    changed_ids = list(reason.keys())

    # (a) top-N sizeable real yields (routine 本物候補)
    reals = sorted((c for c in current if c["class"] in _REAL_ISH and c["tvl"] >= config.MONITOR_MIN_TVL_USD),
                   key=lambda c: c["tvl"], reverse=True)
    for c in reals[:config.MONITOR_TOP_N]:
        reason.setdefault(c["id"], "利回り上位（規模あり）")

    # (c) a few sizeable DEX pools — Mantle の池は小さく、厚みは Fluxion 等の DEX が担う。年利は
    #     24h 出来高ベースで振れる（web は * つき）ので、ここは wash/薄さの確認対象として入れる。
    dexs = sorted((c for c in current if c["project"] in config.MANTLE_DEX_SLUGS
                   and c["tvl"] >= config.MONITOR_MIN_TVL_USD
                   and (c["apy"] or 0) >= config.MEANINGFUL_APY_PCT),   # 利回りゼロの DEX（死んだ池）は除く
                  key=lambda c: c["tvl"], reverse=True)
    for c in dexs[:config.MONITOR_TOP_DEX]:
        reason.setdefault(c["id"], "規模の大きい DEX プール")

    # compose within the cap, RESERVING slots for DEX variety (Mantle の大型は Aave/Ondo に偏り、
    # 放っておくと DEX が毎回押し出される): news(capped) → DEX → real-yield backbone → leftover news.
    dex_ids  = [c["id"] for c in dexs[:config.MONITOR_TOP_DEX]]
    real_ids = [c["id"] for c in reals[:config.MONITOR_TOP_N]]
    cap = config.MONITOR_MAX_INVESTIGATE
    changes_by_size = sorted(changed_ids, key=lambda i: -by_id[i]["tvl"])
    picked = list(changes_by_size[:max(0, cap - config.MONITOR_TOP_DEX)])
    for group in (dex_ids, real_ids, changes_by_size):
        for i in group:
            if i not in picked:
                picked.append(i)
    return [(by_id[i], reason[i]) for i in picked[:cap]]


# ---------------------------------------------------------------- investigator agent (maverick)
def _narration_issue(text: str, symbol: str | None, verdict: str, lang: str = "ja") -> str | None:
    """Deterministic quality gate on an investigator's narration. Returns a SHORT reason string
    when the answer is unusable (so the single redo can be told EXACTLY what to fix — a generic
    'try again' just brings back the same text), or None when it passes. No LLM here — the check
    is free and can't hallucinate; only the redo costs one extra call. The three failures:
      1. empty / too short,  2. off-topic (no pool name, no yield concept),
      3. CONTRADICTS the engine verdict — the one that matters (engine says 報酬依存 but the prose
         calls it pure 実利回り, or vice-versa). The verdict/number is engine-owned, so 'contradicts
         the verdict' is a deterministic, no-fabrication check, NOT the editor judging.
    `lang="en"`: same three checks with English keyword sets (the verdict string stays engine-JP)."""
    t = (text or "").strip()
    if len(t) < 15 or t.startswith("("):
        return "説明が空か短すぎる"
    sym = (symbol or "").upper()
    tl = t.lower()
    if lang == "en":
        topical_en = ("yield", "reward", "incentive", "rate", "interest", "chain", "tvl",
                      "liquidity", "organic", "apy", "fee")
        if not (sym and sym in t.upper()) and not any(k in tl for k in topical_en):
            return "off-topic: mentions neither the pool nor its yield"
        v = verdict or ""
        if "報酬" in v and ("real yield" in tl or "genuine" in tl) \
                and "reward" not in tl and "incentive" not in tl:
            return "the engine verdict is reward-dependent, but the prose calls it pure real yield"
        if "実利回り" in v and ("reward-dependent" in tl or "mostly incentive" in tl or "mostly reward" in tl):
            return "the engine verdict is real yield, but the prose calls it reward-dependent"
        return None
    topical = ("利回り", "報酬", "本物", "チェーン", "配布", "金利", "TVL", "流動", "出どころ")
    if not (sym and sym in t.upper()) and not any(k in t for k in topical):
        return "プール名にも利回りの話にも触れていない（的外れ）"
    v = verdict or ""
    if "報酬" in v and ("実利回り" in t or "本物の利回り" in t) and "報酬" not in t and "配布" not in t:
        return f"engine の判定は『{v}』なのに、説明が実利回り扱いで矛盾"
    if "実利回り" in v and ("報酬頼み" in t or "ほとんど報酬" in t or "配布報酬が大半" in t):
        return f"engine の判定は『{v}』なのに、説明が報酬頼み扱いで矛盾"
    return None


def _emit(on_event, **ev):
    """Forward a progress event to an optional consumer (serve.py SSE → live web view).
    A broken consumer (client disconnect mid-run) must never kill the crew."""
    if on_event:
        try:
            on_event(ev)
        except Exception:   # noqa: BLE001
            pass


def _investigate(item, verbose: bool = False, on_event=None, lang: str = "ja") -> dict:
    """One investigator agent (maverick) chain-verifies ONE pool via its own ReAct loop.
    Returns {label, reason, verdict, narration}: the verdict is the engine's (deep judge,
    so a 🟢-looking pool can be flagged 🔴 wash); the narration is the agent's plain why.
    Degrades to a deterministic judge for the verdict if the agent can't pin one.
    `lang="en"`: the narration is asked for in English (numbers/verdicts stay engine-owned);
    the stored run is thus single-language and the web shows it only when the UI language matches."""
    c, reason = item
    slug, symbol, label = c["project"], c["symbol"], c["label"]
    if verbose:
        print(f"\n  ③ 調査エージェント（maverick）→ {label}  〔{reason}〕")
    _emit(on_event, t="investigate", label=label, reason=reason)
    # 鑑識（engine・決定論）: the authoritative verdict + evidence links (Mantlescan receipts).
    # No LLM here → fully reproducible, and the "確認済み" claim ships with a clickable proof.
    verdict, links = _plain(c["class"]), []
    try:
        _t, y, f, w, pool = tools.gather_judge(slug, symbol)
        rep = report.build_report(_t, y, f, w, pool)   # onchain.audit runs ONCE → verdict + receipts
        verdict = _plain(rep["verdict"])
        links = report._flatten_links(rep.get("links", {}))
    except Exception as e:   # noqa: BLE001 — engine failure: keep the scan-class verdict, no links
        print(f"  [investigate {label}] engine error: {e}")
    # 探偵（maverick agent）: the plain narration via its OWN ReAct loop — this is the "群".
    # 監査（品質チェック・決定論／無料・LLMではない）: returns the SPECIFIC problem so the single
    # bounded redo (max 1 — the extra LLM call is the only cost) is re-prompted with EXACTLY what to
    # fix (not a generic 'try again', which would just return the same text). Still bad → engine-only
    # line. The editor never orders the redo — this rule does. Two-way team, but a mechanical gate.
    def _ask(issue: str | None) -> str:
        if lang == "en":
            q = (f"For {label} (slug: {slug}{(' symbol: ' + symbol) if symbol else ''}): is the yield "
                 f"driven by organic interest or by distributed incentives, and is trading skewed to a "
                 f"few wallets (wash)? Including the on-chain check, explain where the yield comes from "
                 f"in 1–2 plain English sentences.")
            if issue:
                q += (f" (This is a redo. Previous problem: {issue}. You MUST name the pool, say "
                      f"organic-vs-incentives explicitly, and stay consistent with the engine verdict "
                      f"'{verdict}' — two concrete English sentences.)")
        else:
            q = (f"{label}（slug: {slug}{(' symbol: ' + symbol) if symbol else ''}）は実需の金利が主体か、"
                 f"報酬頼みか、取引が少数に偏っていないか(wash)。チェーン確認も含め、利回りの出どころを1〜2文で簡潔に。")
            if issue:
                q += (f"（これはやり直しです。前回の問題点＝{issue}。必ずプール名と『実需の金利か／配布報酬か』"
                      f"に触れ、engine の判定『{verdict}』と整合する具体的な2文で。）")
        try:
            # "say" (NOT "answer") = the fabrication-guarded plain narration: agent.run drops it to
            # "" if it carries a number not traceable to the tool observations (no-fab). "answer" is
            # the raw draft with no such guard — using it here would let an investigator's invented
            # APY reach the 全体調査 card/digest/push. Empty → _narration_issue redo → engine-only.
            return (agent.run(q, model=config.MONITOR_INVESTIGATOR_MODEL,
                              loop_backend=config.MONITOR_INVESTIGATOR_BACKEND,
                              trace=verbose, lang=lang).get("say") or "").strip()
        except Exception as e:   # noqa: BLE001 — one bad investigator must not kill the run
            print(f"  [investigate {label}] agent error: {e}")
            return ""

    narration = _ask(issue=None)
    issue = _narration_issue(narration, symbol, verdict, lang)
    redo = False
    if issue:                                          # 監査が問題点つきで突き返す → 1回だけやり直し
        redo = True
        if verbose:
            print(f"     ↻ 品質チェック不合格〔{issue}〕 → やり直し（最大1回）")
        narration = _ask(issue=issue)
    if _narration_issue(narration, symbol, verdict, lang):    # それでもダメなら空（判定・バー・出典で足りる）
        narration = ""
    if verbose:
        print(f"     → 判定（engine が確定）: {verdict}  / 証拠リンク {len(links)}件"
              + ("  / 🔁 やり直し1回" if redo else ""))
    _emit(on_event, t="verdict", label=label, verdict=verdict, links=len(links), redo=redo)
    return {"label": label, "reason": reason, "verdict": verdict, "narration": narration,
            "slug": slug, "symbol": symbol,           # タップ深掘り用（web でチャットの judge に渡す）
            "apy": c.get("apy"), "base": c.get("base"), "reward": c.get("reward"),
            "chain_derived": c.get("chain_derived"), "links": links, "redo": redo}


# ---------------------------------------------------------------- editor agent (glm-5.1)
def _edit_digest(findings, as_of, lang: str = "ja") -> str:
    """ONE editor call: a short, plain '今朝のまとめ' built ONLY from the investigator
    findings. Returns "" on no-key / failure / fabrication, so the digest degrades to engine-only.
    `lang="en"`: the summary sentence is asked for in English (same constraints, same guard)."""
    if not findings:
        return ""
    facts = "\n".join(f"- {f['label']}: {f['verdict']}｜{f['narration']}（{f['reason']}）"
                      for f in findings)
    if lang == "en":
        sys_p = ("You are the editor of a Mantle DeFi yield check-up report. Using ONLY the findings "
                 "below (engine-derived verdicts + notes), summarize the overall trend in one sentence "
                 "(two at most), in plain English. Do not list individual pool names or numbers (the "
                 "cards below show them); never invent a number. Touch neutrally on both the organic-"
                 "interest yields and the incentive-dependent cautions. No loaded words like 'trap' or "
                 "'scam'; never disparage Mantle or any pool. No buy/sell advice. No first person.")
        usr_p = (f"Findings (as of {as_of}):\n{facts}\n\n"
                 "From the above, the overall trend only — 1–2 plain-English sentences, "
                 "no pool names, no numbers.")
    else:
        sys_p = ("あなたは Mantle DeFi 利回りの定点観測レポートのまとめ役です。下の調査結果（engine 由来の"
                 "判定＋説明）だけを使い、全体の傾向を 1 文（長くても 2 文）で簡潔にまとめる。"
                 "個別のプール名や数値は並べない（下のカードが出すので重複させない）。新しい数値も作らない。"
                 "実需の金利による利回りと、報酬頼みなどの注意点の両方に中立に触れる。『罠』『詐欺』等の決めつけ語は使わず、"
                 "Mantle やプールを貶めない。売買の推奨はしない。一人称は使わない。"
                 "ですます調の、丁寧だけど堅すぎない大人の言葉で。固い言い回し（『〜に留意』『注意が必要です』"
                 "『〜とされています』『提供しています』『ご認識ください』『〜であり』）は避け、やわらかく。")
        usr_p = f"調査結果（{as_of} 時点）:\n{facts}\n\n上記をふまえ、全体の傾向だけを ですます調で 1〜2 文に。プール名や数値は並べない。"
    try:
        intro = nim.chat([{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                         model=config.MONITOR_EDITOR_MODEL, backend=config.MONITOR_EDITOR_BACKEND).strip()
    except nim.NimError:
        return ""
    if agent._untraceable_numbers(intro, facts):   # invented a number → drop prose, keep engine data
        return ""
    return intro


# ---------------------------------------------------------------- digest render (deterministic)
# digest display order: lead with the genuine real yields; traps come after as cautions (a
# "本物候補" digest must not open on a 🔴). Keyed on the verdict's leading glyph.
_DISPLAY_RANK = {"🟢": 0, "🟡": 1, "⚠": 2, "🔴": 3, "⚪": 4, "⚫": 5, "❔": 6}


def _render_digest(findings, intro, as_of, prev_as_of=None) -> str:
    ranked = sorted(findings, key=lambda f: _DISPLAY_RANK.get(f["verdict"][:1], 9))
    lines = [f"📊 MantleFi 定点観測  {as_of}"]
    if findings:   # make the agent crew visible to the reader, not just in --verbose
        lines.append(f"🤖 調査エージェント{len(findings)}体が各プールを Mantle チェーンで検証 → まとめ役エージェントが1つに統合")
    if prev_as_of:
        lines.append(f"（「変化」は前回チェック {prev_as_of} との比較）")
    lines.append("─" * 28)
    if intro:
        lines += [intro, ""]
    if not findings:
        lines.append("注目すべきプールはありませんでした（利回り候補が基準の規模に届かず）。")
    else:
        lines.append("🔎 注目プール（利回りの中身をチェーンで確認）:")
        for f in ranked:
            apy = f.get("apy")
            apy_txt = f"年利 {apy:.1f}%  " if apy is not None else ""   # the headline rate, up front
            lines.append(f"\n{f['verdict']}  {f['label']}  {apy_txt}〔{f['reason']}〕")
            if f['narration']:
                lines.append(f"   {f['narration']}")
            for lbl, url in [(l, u) for l, u in f.get("links", []) if "mantlescan" in u]:
                lines.append(f"   🔗 {lbl}: {url}")   # the chain receipt — verify it yourself
    lines += ["",
              "出典: DefiLlama（集計値）｜Aave は年利をチェーンで直読み再計算・注目プールはトークンの実在を Mantle チェーンで確認",
              "※ 利回りの中身（実需の金利か・配布報酬頼みか）を確認したものです。売買の推奨ではありません。"]
    return "\n".join(lines)


# ---------------------------------------------------------------- latest.json (web face: GET /latest)
def _write_latest(current, findings, intro, as_of, digest, prev_as_of=None) -> None:
    """Mirror the digest as machine-readable JSON for the web face (serve.py GET /latest).
    Carries ONLY engine-derived facts already in _render_digest — no new numbers (no-fabrication).
    Findings are pre-ranked (🟢 first) so the web shows the same order as the pushed digest."""
    import facts   # lazy: the full display-ready snapshot for the web 要旨/表 (SAME run as the crew)
    from collections import Counter
    try:
        overview = facts.daily_data().get("pools", [])   # every pool: logo/site/assets/chain link 付き
    except Exception:   # noqa: BLE001 — overview is best-effort; the crew digest still ships
        overview = []
    ranked = sorted(findings, key=lambda f: _DISPLAY_RANK.get(f["verdict"][:1], 9))
    payload = {
        "as_of": as_of,
        "prev_as_of": prev_as_of,
        "intro": intro or "",
        "pools_scanned": len(current),
        "pools": overview,
        "class_counts": dict(Counter(_plain(c["class"]) for c in current)),
        "findings": [
            {"label": f["label"], "reason": f["reason"], "verdict": f["verdict"],
             "narration": f["narration"], "apy": f.get("apy"),
             "base": f.get("base"), "reward": f.get("reward"),
             "chain_derived": f.get("chain_derived"),
             "slug": f.get("slug"), "symbol": f.get("symbol"),   # タップ→チャットで深掘り
             "links": [{"label": lbl, "url": url} for lbl, url in f.get("links", [])]}
            for f in ranked
        ],
        "digest": digest,   # the exact pushed text too (parity with Telegram)
    }
    p = config.MONITOR_LATEST_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- push (reuse the bot's sender)
def _known_chats() -> list:
    pinned = os.environ.get(config.ALERT_CHAT_ENV_KEY, "").strip()
    if pinned:
        return [pinned]
    p = config.KNOWN_CHATS_PATH
    try:
        return list(json.loads(p.read_text(encoding="utf-8")).keys()) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


def _push(text: str) -> None:
    try:
        token = tg._token()
    except RuntimeError as e:
        print(f"[push] Telegram 未設定: {e}"); return
    chats = _known_chats()
    if not chats:
        print("[push] 送信先 chat_id が未記録（一度 bot にメッセージを送ってください）。reports/ には保存済み。")
        return
    for cid in chats:
        tg.send(token, cid, text, mono=False)
    print(f"[push] {len(chats)} 件のチャットに送信しました")


# ---------------------------------------------------------------- main
def main(dry: bool = False, verbose: bool = False, on_event=None, push: bool = True,
         advance_baseline: bool = True, lang: str = "ja") -> None:
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    if verbose:
        print(f"=== MantleFi 定点観測 monitor  {as_of} ===")
    try:
        current = _scan_pools()
    except ds.FetchError as e:
        print(f"❔ 取得失敗（推測しない・要再試行）: {e}")
        return

    from collections import Counter
    _classes = dict(Counter(_plain(c["class"]) for c in current).most_common())
    if verbose:
        print(f"\n① scan（決定論・LLM不使用）: {len(current)} プールを取得して分類")
        for cls, n in _classes.items():
            print(f"     {cls}: {n}")
    _emit(on_event, t="scan", n=len(current), classes=_classes)

    snapshot, prev_as_of = _load_snapshot()
    watch = _build_watchlist(current, snapshot)
    _emit(on_event, t="pick", items=[{"label": c["label"], "reason": reason,
                                      "tvl": c.get("tvl"), "apy": c.get("apy")} for c, reason in watch])
    if verbose:
        print(f"\n② 注目を抽出（上限 {config.MONITOR_MAX_INVESTIGATE} 件・空回り防止）: {len(watch)} 件を調査対象に")
        for c, reason in watch:
            print(f"     - {c['label']:<18} 〔{reason}〕 TVL≈${c['tvl']:,.0f} APY {c['apy']:.1f}%")
        print(f"\n③ 調査エージェント群が fan-out（各自が自分の ReAct ループでチェーン検証）")
    else:
        print(f"[monitor] {len(current)} pools scanned, {len(watch)} に調査エージェントを割当")

    findings = [_investigate(item, verbose=verbose, on_event=on_event, lang=lang) for item in watch]   # the crew fans out (sequential, rate-limited)

    _emit(on_event, t="edit", n=len(findings))
    if verbose:
        print(f"\n④ まとめ役エージェントが {len(findings)} 件を1つの digest にまとめる…")
    intro = _edit_digest(findings, as_of, lang)                          # one editor merges
    if verbose:
        print("     → まとめ文: " + ("生成OK" if intro else "（今回は省略：失敗 or 捏造ガード作動）"))
    digest = _render_digest(findings, intro, as_of, prev_as_of)

    if dry:
        if verbose:
            print("\n⑤ Telegram push / ⑥ 保存 …（--dry のため実行しません）")
        print("\n===== 完成した DIGEST =====\n")
        print(digest)
        _emit(on_event, t="done", as_of=as_of)
        return

    # persist: the digest artifact (a judge can read it), the snapshot, and an audit line
    config.MONITOR_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (config.MONITOR_REPORT_DIR / f"{as_of.split()[0]}.md").write_text(digest + "\n", encoding="utf-8")
    _write_latest(current, findings, intro, as_of, digest, prev_as_of)   # web face mirror (GET /latest)
    if advance_baseline:   # ad-hoc なライブ実行（ボタン）は基準を進めない＝比較は「毎日の定点チェック」基準のまま
        _save_snapshot({c["id"]: {"class": c["class"], "apy": c["apy"], "tvl": c["tvl"]} for c in current}, as_of)
    config.MONITOR_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.MONITOR_HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"as_of": as_of, "pools": len(current), "investigated": len(findings),
                             "verdicts": [f["verdict"] for f in findings]}, ensure_ascii=False) + "\n")
    if push:
        if verbose:
            print("\n⑤ Telegram push …")
        _push(digest)
    if verbose:
        print("⑥ snapshot / report / history を保存しました")
    _emit(on_event, t="done", as_of=as_of)


if __name__ == "__main__":
    main(dry="--dry" in sys.argv, verbose=("--verbose" in sys.argv or "-v" in sys.argv))
