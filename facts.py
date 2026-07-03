"""MantleFi — deterministic fast-path for the web face (serve.py /facts) + one-shot narration (/say).

WHY: a full ReAct chat is dominated by the free LLM (3 calls, 15–22s each, high variance), even
though the actual chain-reading DD is fast (~6s). So we split the answer:

  /facts  → answers WITHOUT the LLM. Routes the question deterministically (token match → judge;
            yield keywords → scan; else → none) and runs the engine, so the verdict + numbers +
            Mantlescan receipts appear in ~6s. This is the part that must feel instant.
  /say    → ONE LLM call (no tool round-trips) that rephrases the engine `evidence` into a friendly
            sentence, OR (type c) plainly describes a concept. Optional polish: the /facts card
            already stands on its own (it carries the engine's deterministic one-line `note`).

no-fabrication holds throughout: every number/verdict here is engine-derived; the LLM in /say only
rephrases and is checked by the fabrication guard, falling back to "" (card keeps the engine note).
"""
from __future__ import annotations

import re

import config
import data_sources as ds
import classify
import tools
import report
import nim
import agent
import aave
import onchain
from datetime import datetime, timezone


# ---------------------------------------------------------------- deterministic router
# Japanese / English hints that a question is a broad "where's the good yield?" ask (no token named).
_YIELD_HINT_JA = ("利回り", "利率", "リターン", "おすすめ", "オススメ", "良い", "いい", "高い",
                  "稼", "本物", "儲", "増や", "リアルイールド", "人気", "ランキング", "一覧")
_YIELD_HINT_EN = ("real yield", "apy", "yield", "best")
# A question that ASKS WHAT SOMETHING IS wants an explanation first — even when it names a token.
# "GHOって何？" must explain GHO, not dump its APY (まず説明から). STRONG hints OUTRANK the token→judge
# match. WEAK hints (教えて/説明) are ambiguous — "Aaveを教えて"=explain but "人気のDeFi教えて"=list — so
# they only describe as a LAST resort, AFTER a yield/popularity ask routes to scan (ren FB: 「人気の
# Defi教えて」が一覧でなく説明に行く誤ルートを解消).
_DESCRIBE_STRONG = ("って何", "ってなに", "とは", "どういう", "どんな",
                    "なんですか", "何ですか", "なに？", "何？", "なに?", "何?")
_DESCRIBE_WEAK = ("教えて", "説明")
_MIN_SYM = 3   # symbols shorter than this are too collision-prone to substring-match safely


def _pool_symbol_index(pools):
    """symbol(upper) -> the highest-TVL pool row carrying it (deterministic token match)."""
    idx = {}
    for p in pools:
        sym = (p.get("symbol") or "").upper()
        if len(sym) < _MIN_SYM:
            continue
        cur = idx.get(sym)
        if cur is None or (p.get("tvlUsd") or 0) > (cur.get("tvlUsd") or 0):
            idx[sym] = p
    return idx


def _match_token(q: str, pools):
    """The longest pool-symbol that appears in `q` as a whole token (case-insensitive), or None.
    ASCII symbols are matched on word boundaries so 'USDC' doesn't fire inside 'USDCx' and a bare
    'USDT' query doesn't grab 'USDT0' (longest-match still prefers USDT0 when both truly appear)."""
    up = q.upper()
    hits = [sym for sym in _pool_symbol_index(pools)
            if re.search(r"(?<![A-Z0-9])" + re.escape(sym) + r"(?![A-Z0-9])", up)]
    if not hits:
        return None
    return _pool_symbol_index(pools)[max(hits, key=len)]


def resolve(q: str, pools) -> dict:
    """Route a free-text question deterministically:
       strong explain ("〜って何？")  → describe   ← outranks the token match: explain BEFORE you judge
       a named token                → judge       (「GHOは本物？」)
       a yield/popularity ask        → scan        (「利回りいいのは？」「人気のは？」)
       weak explain (教えて) or else  → describe.
    WEAK 教えて is checked AFTER the yield ask so "人気のDeFi教えて" lists pools instead of lecturing."""
    low = q.lower()
    if any(h in q for h in _DESCRIBE_STRONG) or "what is" in low or "explain" in low:
        return {"kind": "none"}            # 「〜って何？」はまず説明（銘柄名があっても即judgeしない）
    pool = _match_token(q, pools)
    if pool:
        return {"kind": "judge", "pool": pool,
                "slug": pool.get("project"), "symbol": pool.get("symbol")}
    if any(h in q for h in _YIELD_HINT_JA) or any(h in low for h in _YIELD_HINT_EN):
        return {"kind": "scan"}
    return {"kind": "none"}            # weak 教えて/説明 or anything unmatched → describe


# ---------------------------------------------------------------- structured facts (engine, no LLM)
def _pct(v):
    return f"{v:.2f}%" if isinstance(v, (int, float)) else "不明"


def _title(target: str) -> str:
    """'aave-v3 GHO' -> 'GHO · aave' (symbol first, short protocol name)."""
    proj, _, sym = (target or "").partition(" ")
    return f"{sym or proj} · {proj.split('-')[0]}"


# protocol slug -> (display name, pool kind) for a richer judge-card heading ("Aaveの貸付プール")
_PROTO = {
    "aave-v3": ("Aave", "貸付プール"),
    "lendle-pooled-markets": ("Lendle", "貸付プール"),
    "init-capital": ("INIT", "貸付プール"),
    "merchant-moe-liquidity-book": ("Merchant Moe", "DEXプール"),
    "agni-finance": ("Agni", "DEXプール"),
    "fluxion-network": ("Fluxion", "DEXプール"),
    "ondo-yield-assets": ("Ondo", "米国債RWA"),
    "mantle-index-four-fund": ("Mantle Index", "指数ファンド"),
    "cian-yield-layer": ("CIAN", "運用プール"),
}


def _heading(target: str):
    """'aave-v3 GHO' -> ('GHO', 'Aaveの貸付プール'). Falls back to the short slug name + 'プール'."""
    proj, _, sym = (target or "").partition(" ")
    name, kind = _PROTO.get(proj, (proj.split("-")[0].capitalize(), "プール"))
    return (sym or proj), f"{name}の{kind}"


# What the underlying coin actually IS — a curated, deterministic one-liner (human-authored, NOT
# LLM-generated, so no-fabrication holds). Conservative, verifiable facts only; an unknown token
# gets no line (honest — never guess). Keyed by upper-case symbol.
_TOKEN_DESC = {
    "USDT0":  "米ドルに連動するステーブルコインです（Tether の USDT を複数チェーンで使えるようにした版）。",
    "USDT":   "Tether が発行する米ドル連動のステーブルコインです。",
    "USDC":   "Circle が発行する米ドル連動のステーブルコインです。",
    "GHO":    "Aave が発行する米ドル連動の分散型ステーブルコインです。",
    "USDE":   "Ethena の「合成ドル」です。現金の裏付けではなく、ヘッジ取引で 1 ドルに近づける設計のステーブルです。",
    "SUSDE":  "USDe を預けて利回りが付く版です（Ethena）。",
    "SYRUPUSDT": "USDT をベースにした利回り付きトークン（syrupUSDT）です。",
    "USDY":   "Ondo が米国債を裏付けに発行する、利回り付きのドル建てトークンです（RWA）。",
    "USD1":   "米ドルに連動するステーブルコインです。",
    "WETH":   "イーサリアム（ETH）を 1:1 で包んだトークンです。中身は ETH です。",
    "WMNT":   "Mantle のネイティブ通貨 MNT を 1:1 で包んだトークンです。",
    "METH":   "Mantle の流動ステーキング ETH です。ETH を預けた預り証で、ステーキング報酬が乗ります。",
    "CMETH":  "mETH をさらに再ステーク（リステーク）したトークンです（Mantle）。",
    "FBTC":   "ビットコイン（BTC）を裏付けにしたトークンです。",
}


def _coin_identity(symbol) -> str:
    """One-line 'what is this coin' from the curated map. LP pairs (SYM-SYM) → a generic pool line.
    Unknown → "" (the card shows no identity line rather than guessing)."""
    s = (symbol or "").upper().strip()
    if not s:
        return ""
    if s in _TOKEN_DESC:
        return _TOKEN_DESC[s]
    if "-" in s:   # an LP pair like USDT0-BSB
        return "2 種類のトークンを組み合わせた流動性ペアです（DEX に預けて取引手数料を得ます）。"
    return ""


def _proto_logo(proj: str):
    """(logo_url, official_site_url) for a protocol slug — SOURCED from DefiLlama /protocols
    (ds.protocol_directory, TTL-cached, no key). ('', '') if unknown → caller draws a monogram.
    Never hand-typed (a wrong DeFi URL is a security hazard — see config.py)."""
    try:
        diry, _ = ds.protocol_directory()
    except ds.FetchError:
        return "", ""
    d = diry.get(proj) or {}
    return d.get("logo") or "", d.get("url") or ""


def _assets_from(pool: dict, imgmap: dict):
    """[{img, sym}] for a pool's underlying tokens, from a PRE-BATCHED token_images map (imgmap).
    img='' → render an .ai.mono monogram. Engine passes contract addresses; nothing is guessed."""
    out = []
    for a in (pool.get("underlyingTokens") or [])[:3]:
        m = imgmap.get((a or "").lower()) or {}
        out.append({"img": m.get("image") or "", "sym": (m.get("symbol") or (a or "")[2:5]).upper()})
    return out


def _none_facts() -> dict:
    """describe/none card: text comes from /say, but ALWAYS ship one relevant link (the Mantle DeFi
    directory) so even a free-form answer ends with something clickable — never a dead-end bubble."""
    return {"kind": "none",
            "links": [{"label": "Mantle の DeFi 一覧（DefiLlama）", "url": config.DEFILLAMA_MANTLE_PAGE}]}


def _judge_facts(slug: str, symbol) -> dict:
    """Type a: one target, deep judge. verdict + apy/base/reward + chain receipts — all engine.
    `details` carries the deeper report (問診/持続条件/限界/出典) for the web card's 折りたたみ —
    shown only when opened, so the default stays short while the depth is one tap away."""
    target, yres, fres, wres, pool = tools.gather_judge(slug, symbol)
    rep = report.build_report(target, yres, fres, wres, pool)   # onchain.audit runs ONCE here
    logo, site = _proto_logo(slug)                              # SOURCED venue logo + official site
    uaddrs = [a for a in ((pool or {}).get("underlyingTokens") or []) if a][:3]
    try:
        imgmap, _ = ds.token_images(uaddrs) if uaddrs else ({}, "")   # ONE batched call (≤30 internal)
    except ds.FetchError:
        imgmap = {}
    assets = _assets_from(pool or {}, imgmap)
    rows, note = [], ""
    apy = tvl = None
    if yres:
        n = yres["numbers"]
        apy, tvl = n.get("apy"), n.get("tvlUsd")
        rows.append({"k": "年利", "v": _pct(apy)})
        if n.get("apyBase") is not None:
            rows.append({"k": "実需の金利", "v": _pct(n.get("apyBase"))})
        if n.get("apyReward") is not None:
            rows.append({"k": "配布報酬", "v": _pct(n.get("apyReward"))})
        note = yres.get("note", "") or ""
    title, sub = _heading(target)
    # Label the size for what it IS: "供給総額" when it's the chain GROSS supplied (Aave, real total),
    # else "預入" (DefiLlama's conventional net). Same label never carries two meanings (ren 6/28).
    tvl_label = "供給総額" if ((pool or {}).get("_aave") or {}).get("tvl_basis") == "gross" else "預入"
    subtitle = sub + (f" ｜ {tvl_label} {tools._short_usd(tvl)}" if tvl else "")
    # The REAL operations the engine just performed, surfaced so the chat reads as an agent
    # investigating (the web reveals them one-by-one). Nothing is staged — each line happened.
    steps = ["🔍 Mantle の利回りデータを取得", f"📂 {title} ＝ {sub} と特定"]
    if yres and yres["numbers"].get("apyBase") is not None and yres["numbers"].get("apyReward") is not None:
        nn = yres["numbers"]
        steps.append(f"🧮 利回りの内訳を確認：実需の金利 {_pct(nn['apyBase'])} ＋ 配布報酬 {_pct(nn['apyReward'])}")
    _GLY = {"verified": "✅", "flag": "⚠", "abstain": "❔"}
    for c in rep.get("onchain_verification", []):
        steps.append(f"⛓ {c['label'].split('（')[0]}をチェーンで確認 … {_GLY.get(c['status'], '・')}")
    if wres and wres.get("numbers", {}).get("tx/unique_wallet") is not None:
        wn = wres["numbers"]
        uniq = (wn.get("buyers") or 0) + (wn.get("sellers") or 0)
        txs = (wn.get("buys") or 0) + (wn.get("sells") or 0)
        steps.append(f"👥 取引の偏りを確認：{uniq}人で{txs}取引")
    # Per-finding receipt + folded depth come from the SHARED report helpers, so this chat card and
    # the daily report card fold out the IDENTICAL engine-derived evidence (no drift between surfaces).
    chain_items = report.chain_receipts(rep, pool)
    return {
        "kind": "judge",
        "badge": config.CLASS_PLAIN.get(rep["verdict"], rep["verdict"]),
        "badge_cls": config.CLASS_JB.get(rep["verdict"], "jb-de"),   # color class for the .jb pill
        "glyph": tools._CLASS_GLYPH.get(rep["verdict"], "・"),       # engine glyph for the .jb pill
        "title": title,            # e.g. "GHO"
        "subtitle": subtitle,      # e.g. "Aaveの貸付プール ｜ 預入 $2.1M"
        "identity": _coin_identity(title),   # "what is this coin" — curated deterministic line ('' if unknown)
        "steps": steps,            # the real operations performed (animated in the chat)
        "apy": apy,
        "slug": slug,              # project slug → web computes the DEX * caveat (24h出来高ベースの推定)
        "base": (yres["numbers"].get("apyBase") if yres else None),     # for the 実需の金利/報酬 split bar
        "reward": (yres["numbers"].get("apyReward") if yres else None),
        "logo": logo,              # SOURCED venue logo (.plogo); '' → monogram
        "proto": sub.split("の")[0],   # short venue name for .pp / monogram
        "assets": assets,          # underlying-token coin stack (.ai), SOURCED icons
        "rows": rows,
        "note": note,    # engine's deterministic plain one-liner (the fallback if /say is empty)
        "chain": chain_items,   # [{t: "label: finding", u: Mantlescan url, m: masked addr}] — per-finding receipt
        "links": ([{"label": "提供元（公式サイト）", "url": site}] if site else [])
                 + [{"label": l, "url": u} for l, u in report._flatten_links(rep.get("links", {})) if "DefiLlama" not in l],
        "details": report.detail_payload(rep),   # 折りたたみの深掘り（問診/持続条件/限界/出典・shared）
        "evidence": report.render_md(rep),   # reuse the dict (no second onchain pass) — for /say + guard
    }


def _scan_facts(pools, url, n: int = 5) -> dict:
    """Type b: broad yield ask. The SIZEABLE real-ish yields (🟢/🟡), with the engine's note.

    Lead by TVL (desc), not by class: MantleFi is a real-yield FINDER, not a high-APY ranker, so a
    tiny dust pool that prints a high % must not top the list (surfacing $0.4M Fluxion dust as
    『本物』 is both misleading and a soft Mantle-credibility risk). The glyph still says real(🟢)
    vs reward-dependent(🟡); thin pools (< SMALL_TVL) are flagged honestly so size is never hidden."""
    pools = aave.correct_pools(pools)   # Aave rows: chain base + Merkl real reward (not the headline)
    pairs = [(p, classify.classify_yield(p, url)) for p in pools]
    reals = [(p, r) for p, r in pairs
             if r["class"] in (config.CLASS_REAL_YIELD, config.CLASS_REWARD_DEP)]
    reals.sort(key=lambda pr: -(pr[1]["numbers"].get("tvlUsd") or 0))
    top = reals[:n]
    # ONE batched logo fetch for the WHOLE list (token_images batches ≤30/req internally), TTL-cached
    # → effectively free, latency flat. Failure → all icons degrade to monograms; numbers/links intact.
    addrs = [a for p, _ in top for a in (p.get("underlyingTokens") or [])[:3] if a]
    try:
        imgmap, _ = ds.token_images(addrs) if addrs else ({}, "")
    except ds.FetchError:
        imgmap = {}
    try:
        diry, _ = ds.protocol_directory()   # ONCE for the whole list (no per-row N+1 even cache-off)
    except ds.FetchError:
        diry = {}
    items = []
    for p, r in top:
        num = r["numbers"]
        tvl = num.get("tvlUsd") or 0
        proj = p.get("project") or ""
        pd = diry.get(proj) or {}
        logo, site = pd.get("logo") or "", pd.get("url") or ""
        und = (p.get("underlyingTokens") or [None])[0]   # underlying address → Mantlescan chain link
        items.append({
            "glyph": tools._CLASS_GLYPH.get(r["class"], "・"),
            "sym": p.get("symbol") or proj,                                   # 名前（トークン）
            "slug": proj,                                                     # project slug (guided judge intent)
            "proto": _PROTO.get(proj, (proj.split("-")[0].capitalize(), ""))[0],  # Defiのプール（運営）
            "logo": logo,                              # SOURCED venue logo (.plogo)
            "assets": _assets_from(p, imgmap),         # underlying coin stack (.ai)
            "site": site,                              # protocol official site (verify-the-venue)
            "apy": num.get("apy"),
            "apyBase": num.get("apyBase"),             # green/amber split bar (.jbar)
            "apyReward": num.get("apyReward"),
            "tvl": tvl,
            "thin": 0 < tvl < config.SMALL_TVL_USD,   # 小=資金が少なく出入りしにくい
            "link": config.DEFILLAMA_POOL_URL.format(id=p["pool"]) if p.get("pool") else None,
            "chain": config.MANTLESCAN_TOKEN_URL.format(addr=und) if und else None,   # チェーン確認(Mantlescan)
        })
    return {"kind": "scan",
            "title": "今の Mantle DeFi の主な利回り（規模の大きい順）",
            "items": items,
            "source_link": config.DEFILLAMA_MANTLE_PAGE,   # footer receipt: the full Mantle list (always present)
            "steps": ["🔍 Mantle の全プールを取得",
                      f"🧮 {len(pairs)} プールを利回り軸で分類",
                      "📊 規模の大きい主な利回りを抽出"],
            "evidence": tools.render_scan([r for _, r in pairs])}


# verdict → short table-badge label (the 全件 vault table cell; the full label lives in CLASS_PLAIN)
_VSHORT = {
    config.CLASS_REAL_YIELD: "🟢 実利回り",
    config.CLASS_REAL_INFLOW: "🟢 資金が流入",
    config.CLASS_REWARD_DEP: "🟡 報酬頼み",
    config.CLASS_EMISSION_TRAP: "🔴 ほぼ報酬頼み",
    config.CLASS_PRICE_ILLUSION: "🔴 価格錯覚",
    config.CLASS_WASH: "🔴 出来高が少数に集中",
    config.CLASS_DEAD: "⚫ 利回りゼロ",
    config.CLASS_INACTIVE: "⚫ 取引なし",
    config.CLASS_OUTFLOW: "⚪ 資金流出",
    config.CLASS_FLAT: "⚪ 横ばい",
    config.CLASS_UNKNOWN: "❔ 判断材料不足",
    config.CLASS_VOLATILE: "⚠️ 変動が大きい",
}


def daily_data() -> dict:
    """The corrected, FULL Mantle DeFi snapshot for the web デイリー — every yield pool with the
    chain-corrected numbers (Aave: chain base + real reward + GROSS supplied TVL + on-chain casing)
    and the display metadata the table + hero cards need (logo, assets, official site, receipts).

    ONE source of truth for BOTH serve.py `GET /daily` (live) AND the baked fallback in index.html,
    so the デイリー can never silently drift from the chat again. Engine-only (no LLM) → every number
    is engine-derived (no-fabrication). Sorted by size (TVL desc)."""
    pools, url = ds.mantle_yield_pools()
    pools = aave.correct_pools(pools, with_symbol=True)   # Aave casing (sUSDe) + chain base/reward/TVL
    try:
        diry, _ = ds.protocol_directory()
    except ds.FetchError:
        diry = {}
    addrs = [a for p in pools for a in (p.get("underlyingTokens") or [])[:3] if a]
    try:
        imgmap, _ = ds.token_images(addrs) if addrs else ({}, "")
    except ds.FetchError:
        imgmap = {}
    rows = []
    for p in pools:
        r = classify.classify_yield(p, url)
        num = r["numbers"]
        proj = p.get("project") or ""
        pd = diry.get(proj) or {}
        und = (p.get("underlyingTokens") or [None])[0]
        basis = (p.get("_aave") or {}).get("tvl_basis")
        # hero note = the chain breakdown for Aave (cheap — reads _aave, no extra RPC), else classify note
        note = r.get("note", "") or ""
        if p.get("_aave"):
            ab = onchain.aave_breakdown(p)
            if ab:
                note = ab["finding"]
        rows.append({
            "sym": p.get("symbol") or proj,
            "slug": proj,
            "proto": _PROTO.get(proj, (proj.split("-")[0].capitalize(), ""))[0] or proj,
            "protoName": pd.get("name") or proj,
            "site": pd.get("url") or "",
            "logo": pd.get("logo") or "",
            "apy": num.get("apy"), "base": num.get("apyBase"), "reward": num.get("apyReward"),
            "verdict": config.CLASS_PLAIN.get(r["class"], r["class"]),
            "vshort": _VSHORT.get(r["class"], r["class"]),
            "jb": config.CLASS_JB.get(r["class"], "jb-de"),
            "tvl": num.get("tvlUsd") or 0,
            "tvlLabel": "供給総額" if basis == "gross" else "預入",
            "thin": 0 < (num.get("tvlUsd") or 0) < config.SMALL_TVL_USD,
            "assets": _assets_from(p, imgmap),
            "chain": config.MANTLESCAN_TOKEN_URL.format(addr=und) if und else "",
            "llama": config.DEFILLAMA_POOL_URL.format(id=p["pool"]) if p.get("pool") else "",
            "note": note,
        })
    rows.sort(key=lambda d: -(d["tvl"] or 0))
    return {"as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
            "count": len(rows), "pools": rows}


def _slug_for_symbol(symbol, pools):
    """Guided UI may pass a symbol without its project slug → recover the project from the pool list."""
    s = (symbol or "").upper()
    for p in pools:
        if (p.get("symbol") or "").upper() == s:
            return p.get("project")
    return None


def compute(q: str, intent: dict | None = None) -> dict:
    """The /facts entry point: route → run the engine → structured card. NEVER calls the LLM.
    `intent` (from the guided chat UI) supplies a KNOWN {kind, slug?, symbol?}, so the keyword
    router is skipped entirely — no mis-routing of loose/casual wording. No intent → resolve(q)."""
    try:
        pools, url = ds.mantle_yield_pools()
    except ds.FetchError:
        return {"kind": "error", "message": "いまデータを取得できませんでした。少し待ってもう一度どうぞ。"}
    route = intent if (isinstance(intent, dict) and intent.get("kind")) else resolve(q, pools)
    if route["kind"] == "judge":
        slug, symbol = route.get("slug"), route.get("symbol")
        if not slug and symbol:                 # guided UI sent only a symbol → recover its project
            slug = _slug_for_symbol(symbol, pools)
        try:
            return _judge_facts(slug, symbol)
        except ds.FetchError:
            return _none_facts()      # let /say describe handle the text — but still ship a link
    if route["kind"] == "scan":
        return _scan_facts(pools, url)
    return _none_facts()              # none / describe → /say supplies the text


def seed_for(q: str):
    """WEB-ONLY: deterministically resolve q → (seed, require_tool) for agent.run, so the agent's
    EXTRACTION is locked to the reliable router (a weak LLM can't corrupt the symbol, e.g. USDT0→
    USDT) while the agent still does the reasoning/memory/narration on top. Returns (None, False)
    when the question doesn't resolve to a concrete pool/scan (novel/comparative/follow-up "それ") —
    there the agent runs free. Numbers/verdicts stay engine-owned regardless (no-fab)."""
    try:
        pools, _ = ds.mantle_yield_pools()
    except ds.FetchError:
        return None, False
    route = resolve(q, pools)
    if route["kind"] == "judge":
        return ({"hint": f"対象は確定済み: {route['slug']} の {route['symbol']}。judge はこの project と "
                         f"symbol（{route['slug']} / {route['symbol']}）で呼ぶこと。",
                 "bind": {"project": route["slug"], "symbol": route["symbol"]}}, True)
    if route["kind"] == "scan":
        return ({"hint": "ユーザーは利回りの一覧/おすすめを求めている。まず sonar を使うこと。"}, True)
    return None, False


# ---------------------------------------------------------------- narration (/say — ONE LLM call)
_EVIDENCE_CAP = 4000   # cap the engine context fed to the LLM (a client echoes it back to /say)


def narrate(q: str, evidence: str, lang: str = "ja") -> str:
    """ONE LLM call: a friendly 1–2 sentence read of the engine `evidence` (no tools, so it's fast).
    Returns "" on failure OR if a number can't be traced to the evidence — the card already shows
    the engine's own note, so an empty narration is fine (no-fabrication preserved).
    `lang` changes ONLY the narration language ("en"); numbers/verdicts stay engine-owned."""
    evidence = (evidence or "")[:_EVIDENCE_CAP]
    if not evidence.strip():
        return ""
    if lang == "en":
        sys_p = ("You explain Mantle DeFi yields in plain, friendly English. Use ONLY the findings "
                 "below (engine-derived verdicts + numbers) and give a beginner the takeaway in one "
                 "or two short sentences. Never invent a number (no % or $ that is not below). "
                 "No loaded words like 'trap' or 'scam'; never disparage Mantle or any pool. "
                 "No buy/sell advice. No first person. Calm, clear, adult tone — not stiff, not slangy.")
        usr_p = (f"Question: {q}\n\nFindings:\n{evidence}\n\n"
                 "From the above only: one or two short, plain-English sentences.")
    else:
        sys_p = ("あなたは Mantle DeFi 利回りをやさしく説明する人です。下の調査結果（engine 由来の判定＋数値）"
                 "だけを使い、初心者にひとことで（長くても2文）要点を伝える。新しい数値は作らない（下に無い %・$ は"
                 "書かない）。『罠』『詐欺』等の決めつけ語は使わず、Mantle やプールを貶めない。売買の推奨はしない。"
                 "一人称は使わない。"
                 "ですます調の、丁寧だけど堅すぎない大人の言葉で。固い役所言葉（『〜に留意』『〜とされています』"
                 "『提供しています』『ご認識ください』『〜であり』）も、くだけすぎ（『〜だよ』『〜してね』）も避け、"
                 "短く分かりやすく。")
        usr_p = f"質問: {q}\n\n調査結果:\n{evidence}\n\n上記だけから、ですます調で短く分かりやすく（ひとこと、長くても2文）。"
    try:
        out = nim.chat([{"role": "system", "content": sys_p},
                        {"role": "user", "content": usr_p}],
                       model=config.CHAT_NARRATE_MODEL).strip()   # fast model: this is phrasing, not reasoning
    except nim.NimError:
        return ""
    if agent._untraceable_numbers(out, evidence):   # invented a number → drop it (keep the engine note)
        return ""
    return out


# Deterministic, Mantle-framed 1-liners for the protocols/concepts people actually ask about, so
# "Aaveって何" reads the SAME every time and never drifts to a generic "Ethereum" answer (ren FB:
# 説明が揺れる・汎用に流れる). No numbers (no-fab); the LLM still handles anything not listed here.
_DESC_TAIL = "　続けて「利回りいいのは？」と聞けば、今の利回りを取得して一覧にします。"
_PROTOCOL_DESC = (
    # ── core concepts (this product's vocabulary) — defined DETERMINISTICALLY so a 用語 chip never
    #    drifts into a pool EXAMPLE or a stray number (ren FB:「本物の利回り」→ USDT0 3.8% の例になった).
    #    Specific phrases first (本物の利回り) so they don't get caught by a more general key (利回り).
    (("実利回り", "実需の金利", "実需", "本物の利回り", "本物の実利回り", "real yield", "organic rate", "organic yield"), "お金を借りた人が払う金利や、取引した人が払う手数料など、そのサービスを実際に使う人が払うお金から生まれる利回りのことです。運営がトークンを配る『配布報酬』とは別物で、これが主体だと利回りは続きやすくなります。"),
    (("配布報酬", "報酬頼み", "報酬依存", "エミッション", "incentive", "emission", "reward"), "運営がトークンを配って利回りを底上げするぶんのことです。配布が止まると、利回りは実需の金利の水準まで下がります。"),
    (("ステーブル", "stable"), "価値が 1 ドルなどに連動するよう設計された暗号資産のことです（USDT0・USDC・GHO など）。"),
    (("レンディング", "貸付", "貸し付け", "lending"), "暗号資産を預けて利息を得たり、担保にして借りたりできる仕組みのことです（Aave など）。"),
    (("tvl",), "TVL（Total Value Locked）は、そのプールやプロトコルに預けられている資産の合計額のことです。規模の目安になります。"),
    (("年利", "apy", "利回り", "りまわり", "apr", "yield"), "預けたお金が 1 年でどれくらい増えるかの割合（年率）のことです。『実需の金利』と『配布報酬』に分けて見ると、続くかどうかが分かります。"),
    (("aave",), "Aave は、暗号資産を預けて利息を得たり、預けた資産を担保に借りたりできる代表的な貸付（レンディング）プロトコルです。Mantle 上でも稼働していて、USDT0・USDC・GHO などステーブルコインの貸付が中心です。"),
    (("ondo", "usdy"), "Ondo は米国債などの現実資産（RWA）を裏付けにしたトークンを発行するプロトコルです。Mantle では USDY が代表で、利回りの源は米国債の利息（配布報酬ではなく実需の金利）です。"),
    (("merchant moe", "moe"), "Merchant Moe は Mantle の代表的な分散型取引所（DEX）です。トークンを交換でき、ペアに流動性を出すと取引手数料の一部を受け取れます。"),
    (("agni",), "Agni は Mantle 上の分散型取引所（DEX）です。集中流動性型で、ペアに流動性を出すと取引手数料を得られます。"),
    (("fluxion",), "Fluxion は Mantle の分散型取引所（DEX）で、株価連動トークンなど多様なペアを扱います。流動性を出すと手数料を得られますが、極小プールはスリッページに注意です。"),
    (("clearpool",), "Clearpool は機関向けの貸付を提供するプロトコルです。Mantle にも貸付プールがあり、借り手の信用に基づく利回りが付きます。"),
    (("solv",), "Solv は BTC を中心とした利回り運用（ベーシス取引など）を提供するプロトコルです。"),
    (("woofi",), "WOOFi は取引と運用（Earn）を提供するプロトコルです。Mantle では資産を預けて運用するプールがあります。"),
    (("circuit",), "Circuit は Mantle 上の自動運用（ボールト）プロトコルです。"),
    (("stargate",), "Stargate は異なるチェーン間で資産を移動できるブリッジです。"),
    (("symbiosis",), "Symbiosis はクロスチェーンの交換・ブリッジを提供するプロトコルです。"),
    (("ディーファイ", "defi", "デファイ"), "DeFi（分散型金融）は、銀行のような仲介者なしに暗号資産を貸し借り・交換・運用できる仕組みです。Mantle 上にも貸付・DEX・RWA など様々な DeFi があります。"),
)


def _protocol_blurb(q: str):
    """A fixed Mantle-framed description if q names a known protocol/concept, else None."""
    low = (q or "").lower()
    for keys, text in _PROTOCOL_DESC:
        if any(k in low for k in keys):
            return text + _DESC_TAIL
    return None


def _token_blurb(q: str):
    """A curated deterministic identity line if q NAMES a known token (GHO/USDY/USDe/USDT0…), reusing
    the SAME human-authored map as the judge card's identity (_TOKEN_DESC) so 「GHOとは？」reads the
    SAME every time instead of drifting to a hallucinated LLM answer (ren FB: GHO→「原稿担保金利令状」＝
    弱モデルの捏造). Whole-token match, longest-first, so 'USDT' doesn't fire inside 'USDT0' and 'USDE'
    not inside 'SUSDE'. None when no known token is named. EN: both the identity line AND _DESC_TAIL
    are already in the web's tr() map, so the fix carries no i18n drift."""
    up = (q or "").upper()
    # an LP pair (SYM-SYM, e.g. USDC-WAAPLX / ELSA-USDT0) → the generic pair line, not one leg's
    # identity (「USDC-WAAPLXとは？」に『USDCはステーブル』だけ返すのは片手落ち＝両トークンの組と示す).
    if re.search(r"(?<![A-Z0-9])[A-Z0-9]{2,}-[A-Z0-9]{2,}(?![A-Z0-9])", up):
        return "2 種類のトークンを組み合わせた流動性ペアです（DEX に預けて取引手数料を得ます）。" + _DESC_TAIL
    hits = [s for s in _TOKEN_DESC
            if re.search(r"(?<![A-Z0-9])" + re.escape(s) + r"(?![A-Z0-9])", up)]
    if not hits:
        return None
    return _TOKEN_DESC[max(hits, key=len)] + _DESC_TAIL


def describe_if_known(q: str) -> str:
    """Free-box (/chat) guard: a「〜とは？/〜って何？」about a KNOWN concept/token → its FIXED blurb, so
    the weak free-agent model can't hallucinate a definition (the exact bug: GHOとは→「原稿担保金利令状」).
    Returns "" when it's not a definition question, or nothing known is named — there the caller runs
    the real memory-carrying agent (novel/comparative/follow-up「それ」). Deterministic; never the LLM.
    Mirrors resolve()'s describe-first rule so the free box matches the guided 📖 path."""
    low = (q or "").lower()
    if not (any(h in q for h in _DESCRIBE_STRONG) or "what is" in low or "explain" in low):
        return ""
    return _protocol_blurb(q) or _token_blurb(q) or ""


def describe(q: str, lang: str = "ja") -> str:
    """Type c: explain a Mantle DeFi concept/token, OR honestly decline if out of scope. A known
    protocol/concept returns a FIXED Mantle-framed line (deterministic, never drifts); anything else
    is ONE LLM call, no tools, no specific numbers. Returns "" on failure (page shows a fallback).
    `lang="en"` affects the LLM path's language only; fixed blurbs stay JP (the web's deterministic
    tr() map renders them in EN — same fixed text in both languages, no drift)."""
    fixed = _protocol_blurb(q) or _token_blurb(q)   # known concept OR named token (GHO/USDY…) → fixed, deterministic
    if fixed:
        return fixed
    if lang == "en":
        sys_p = (
            "You are a research agent specialized in Mantle DeFi. "
            "SCOPE: only Mantle DeFi — yields, tokens, pools, and DeFi terms/mechanics. "
            "OUT OF SCOPE: weather, news, small talk, programming, price predictions, investment "
            "advice, anything unrelated to Mantle DeFi. For out-of-scope questions decline honestly "
            "in one sentence (e.g. \"I specialize in Mantle DeFi yield research, so I can't answer that.\") "
            "and add nothing else. "
            "In scope: explain in 2–3 plain sentences a beginner can follow. "
            "If asked 'what is X?' about a specific token/pool (e.g. GHO, USDY), first say what it IS, "
            "with no numbers (never guess an unknown token). "
            "Important: YOU are the research tool — never tell the user to look it up themselves. "
            "End by offering to check the live numbers on-chain next (for a token: ask \"Is USDY's "
            "yield real?\"; in general: ask \"What are the good yields?\"). "
            "No numbers (APY/TVL/%/$) in this explanation. Never disparage Mantle or any pool. "
            "No buy/sell advice. No first person.")
        try:
            out = nim.chat([{"role": "system", "content": sys_p},
                            {"role": "user", "content": q}],
                           model=config.CHAT_NARRATE_MODEL).strip()   # fast model (phrasing only)
        except nim.NimError:
            return ""
        if out and re.search(r"\d[\d.,]*\s*(?:[%％]|percent|million|billion|dollars?)|[$＄]\s*\d",
                             out, re.I):   # definitions carry no stats (no-fab)
            return ""
        if out and re.search(r"(look it up|check (it )?yourself|do your own research)", out, re.I):
            out += " If you're curious, just ask \"What are the good yields?\" and I'll check the live numbers on-chain."
        return out
    # Two doctrines, enforced via the prompt:
    #  1. SCOPE: only Mantle DeFi / yield / tokens / pools. Weather, news, prices, other domains →
    #     decline plainly ("…はお答えできません"), do NOT improvise an answer (絶対ルール8: スルー禁止).
    #  2. NEVER hand the thread back ("go look it up yourself") — looking it up IS its job; it ends
    #     by offering to fetch the live numbers next (「〇〇は本物？」/「利回りいいのは？」と聞いて).
    sys_p = (
        "あなたは Mantle DeFi 専門のリサーチエージェントです。"
        "【対応範囲】Mantle の DeFi・利回り・トークン・プール・DeFi の仕組みや用語の説明だけ。"
        "【範囲外】天気・ニュース・一般雑談・プログラミング・価格予想・投資助言・Mantle DeFi と無関係な話題。"
        "範囲外の質問には、無理に答えず一言で正直に断る"
        "（例:「Mantle DeFi の利回り調査が専門なので、それはお答えできません」）。それ以外の説明はしない。"
        "範囲内なら、初心者にも分かるよう2〜3文でやさしく説明する。"
        "特定の銘柄/プール（例: GHO, USDY）を『〜って何？』と聞かれたら、まずそれが何かを数値なしで簡潔に説明する"
        "（知らない銘柄は無理に決めつけない）。"
        "重要: あなた自身が調べる道具なので、『ご自身で調べて』のように丸投げしない。"
        "範囲内の説明の最後には、続けて聞けばあなたが今の数字をチェーンで確認する、と自然に促す"
        "（銘柄を聞かれたなら『〇〇の利回りは？』、全般なら『利回りいいのは？』と聞くよう促す）。"
        "数値（年利・TVL・%・$）はこの説明では出さない。Mantle やプールを貶めない。売買助言はしない。一人称は使わない。")
    try:
        out = nim.chat([{"role": "system", "content": sys_p},
                        {"role": "user", "content": q}],
                       model=config.CHAT_NARRATE_MODEL).strip()   # fast model (phrasing only)
    except nim.NimError:
        return ""
    # describe must stay NUMBER-FREE (no-fab): a definition carries no specific stat. If the model
    # slipped a %/$/年利 figure in (it example-ized instead of defining), drop it — the page falls back.
    if out and re.search(r"\d[\d.,]*\s*(?:[%％]|万|億|兆|ドル|パーセント)|[$＄]\s*\d|年利[\sは]*\d", out):
        return ""
    # Safety net for the "go look it up yourself" failure mode: if a stray 自分で…調べ slips
    # through, append the agent's own offer to fetch (it pulls the thread forward, never hands it back).
    if out and re.search(r"(ご自身|自分|各自|ご自分).{0,8}(調べ|確認|チェック)", out):
        out += "　気になる利回りがあれば、続けて『利回りいいのは？』や『USDYの利回りは？』と聞いてください。こちらで今の数字をチェーンで確認します。"
    return out
