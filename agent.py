"""MantleFi — the self-hosted research AGENT (ReAct loop over the deterministic engine).

This is what makes MantleFi an *agent*, not just a tool+playbook: the thinking loop is ours
(NIM-backed via nim.py), so it does not depend on an external host (Claude Code etc.).

CONTRACT (the accuracy guarantee): the LLM ONLY (a) routes the user's question to engine
tools, (b) reads their text observations, (c) decides the next tool or finalizes, and
(d) writes a synthesis that QUOTES the observations. It never originates a number or a
verdict — those come solely from the deterministic classifier/report (tools.py). Two
safety layers enforce this:
  1. `_untraceable_numbers` flags any number in the final answer not found in the
     observations (best-effort, format-tolerant).
  2. The verbatim "根拠" block always appends the engine output, so the user sees the
     real sourced numbers regardless of how the LLM phrased things.
"""
from __future__ import annotations

import json
import re
import unicodedata

import config
import nim
import tools


# ---------------------------------------------------------------- system prompt
def _system_prompt() -> str:
    slugs = ", ".join(config.MANTLE_PROTOCOL_SLUGS)
    return f"""あなたは MantleFi、Mantle DeFi 専門のリサーチエージェントです。
役割: ある Mantle の利回り/TVL の「出どころ」が本物の収益か・配布報酬頼みかを、下の決定論ツール
だけを使って判定し、出典つきで透明に説明する。Mantle (chain 5000) のみ。judge-not-FOMO。

絶対ルール（違反した回答は無効）:
1. 数値を創作・推定・四捨五入・改変しない。最終回答の全ての %・$・比率は、ツール観測から逐語コピー。
2. クラス判定（🟢/🟡/🔴/⚪/⚫/❔）を上書き・創作しない。判定はツール出力のものだけ。ツールが❔不明なら不明と言う。
3. 売買・タイミングの推奨をしない。「買うべきか?」と聞かれても拒否し、分類のみ返す。
4. 決めつけない。「罠」「詐欺」「見せかけ」等の語を出力に使わず、利回り/資金の「出所」を中立に説明する（例:「この3.5%は100%が配布報酬」）。Mantle やプールを貶めない。
5. Mantle 専門。他チェーンと順位づけしない。Mantle を「負けている」と総括しない。pool レベルで語る。
6. ツールが 不明/取得失敗 を返したら、推測せず、その棄権理由を報告する。
7. 一人称は「私」、または主語を省く。「俺」「僕」は使わない（誰が使っても自然な、丁寧めで落ち着いた口調）。
8. できないこと（範囲外）は、話を逸らさず正直に「それはできません」と言う。曖昧にすり替えない（スルー禁止）。そのうえで、できることを一つ示す。

ツール（下の JSON プロトコルで呼ぶ）:
- sonar(): Mantle の全利回りプールを利回り軸で事前分類して列挙。「今 本物はどこ?」系は最初にこれで候補を絞る。
- judge(project, symbol?): 1 対象を yield+flow+wash の3軸＋問診で深掘り。「Xの利回りの中身は?」用。project は下の有効 slug のみ。
- find_token(symbol): トークン（例 SPCX, USDe）の Mantle プールを厳密同一判定＋on-chain 裏取りで探す。protocol でなく「トークン名」を聞かれたらこれ。
- flow(slug): 資金フロー軸だけ（枚数 vs USD）= 実流入/価格錯覚/流出。

有効な protocol slug（これ以外を発明しない）:
{slugs}

ルーティング指針（まず質問のタイプを見分ける）:
- **説明・雑談**（「〜ってなに？」「仕組みは？」「どういうもの？」など、判定も数値も求めていない）→ **絶対にツールを使わない**（judge も sonar もしない）。対象が具体的な銘柄（例: Ondo の USDY）でも、「なに？」にはまず**言葉で説明する**（本物か・配布報酬頼みかの判定は聞かれていない＝勝手に judge しない・バッジを出さない）。final で、知っている範囲を簡潔に自然な日本語で答える。ただし **具体的な数値（APY/TVL/%/$）は一切言わない**（数値が要るなら judge/sonar で取り直す）。Mantle のプール/トークンの話題なら、最後に**自然な一言**で深掘りを促す（例:「本物の利回りか気になれば『USDYは本物？』と聞いてください」）。**コマンド名（judge 等）を生のまま文に書かない**——会話で続けて聞けば engine が裏を取る、という促し方にする。＝ただの判定機でなく、まず会話する。
- **ある1つの対象の判定**（「Xは本物？報酬頼み？」「Xの利回りは続く？」）→ **まず judge を1回**（yield+flow+wash 一括）。有効 slug が分かれば find_token でなく judge 優先（例「Ondo の USDY」→ judge ondo-yield-assets USDY）。空振り時だけ find_token/flow。
- **広い質問**（「良い利回りある？」「今 面白いのは？」）→ sonar で全体を見て、**まず結論を直答**：「良い（本物の）利回りは ○○（年利X%）です」と1〜2個を名指しで先に答える（本物の実利回り🟢を優先）。その後に短い理由＋**注意点（🟡配布報酬頼み 等）を中立に1つ**添える。緑だけ並べない・決めつけ語は使わない。気になる候補は judge で1つ深掘りして裏を取ってよい。この場合バッジは付かない。
- **実際に使いたい/入金/購入の意図**（「使ってみたい」「入金したい」「どう買う？」など）→ **意図を逸らさず正直に受け止める**: このツールは read-only の調査用なので、入金の代行も「買え/入れろ」も「できません」と正直に言う。その上で "使う前に、その利回りの中身（本物の収益か・配布報酬頼みか）は調べられる" と促し、対象が分かれば（or 文脈にあれば）judge で裏を取って渡す。実際の操作は各プロジェクト公式で、と一言（URL・数値は創作しない）。＝勝手に判定にすり替えず、まず「使いたいんだね」を受け止める。
- **何を知りたいか絞れない曖昧な質問**（「Mantle ってどう？」「何かない？」「教えて」など、利回りを知りたいのか・ある銘柄の判定か・言葉の意味か、意図そのものが絞れないとき）→ 推測で勝手に答えず、ask で**短く1つだけ**聞き返す。chips に1タップで選べる短い候補を2〜4個（例:「利回りの一覧」「ある銘柄の判定」「言葉の意味」や、文脈にある具体的なプール名）。**数値・判定は書かない**。利回り候補が答えになる広い質問（「良い利回りある？」等）は ask でなく sonar で直答する＝ask は意図が絞れない時だけ（多用しない）。

JSON プロトコル: 返答は必ず **JSON オブジェクト1個だけ**（前後に他の文章を書かない）:
{{"thought":"<短い思考1文>","action":"sonar|judge|find_token|flow|ask|final","args":{{...}}}}
- sonar→args は空 {{}}。judge→{{"project":"...","symbol":"..."(任意)}}。find_token→{{"symbol":"..."}}。flow→{{"slug":"..."}}。ask→{{"question":"<数値なしの短い聞き返し1文>","chips":["<候補1>","<候補2>"]}}。
- 結論を出すとき: {{"thought":"...","action":"final","args":{{"answer":"<下の【答え方】に従う平易な総括>"}}}}

【final の answer の書き方 ｜ 読者はクリプトに馴染みのある人。短く・具体的に】
- 短く（1〜2文・全体120字以内）。数字は決め手の1〜2個だけ。
- 平易な日本語で。apyBase/apyReward/emission/wash/RWA/DD 等の専門略語は使わず、実需の金利／配布報酬／出来高の偏り／米国債の利息 に言い換える。TVL・年利・利回り・流動性・出来高 等の一般的なクリプト語はそのまま使ってよい（砕きすぎない・幼稚にしない）。
- 「何が決め手か」を具体的に：例「実需の金利0%＝利回りは全部が配布報酬。止まればほぼ0%」「預けた量は横ばいなのに金額だけ+4%＝値上がりで膨らんだだけ」「買い手が数人で取引回数が異常に多い＝出来高が少数に集中」。
- **判定ラベル（🟢🔴等の結論語）は付けない**（アプリが engine の判定を先頭に出す）。answer は「理由」だけ。
- 数字はツール出力に出てくる値だけを使う。割り算などで新しい数値（◯%の比率など）を自分で作らない。
- トークン名・シンボルもツール出力のものを正確に写す（例: USDY を USDe 等の似た別名に変えない・prefix/suffix を勝手に足さない）。
- 出典・買い手売り手の人数の羅列・プールアドレスは出さない（/why が見せる）。売買助言はしない。
- 観測に「🔗 チェーンで直接確認」があれば、その結論を1つ必ず盛り込む（例:「配布報酬はチェーンに無い＝配布報酬に依存しないと確認」「報酬は◯◯というトークンで配られている」「元になる契約は後から中身を差し替え可能」）。これが本ツールの強み。
最大 {config.NIM_MAX_STEPS} ステップ。十分な観測が得られたら早めに final を返す。
会話の文脈（過去のやり取り）が先頭に付くことがある。文脈は踏まえてよいが、**数値・判定は必ずツールで取り直す**（文脈中の古い数値を再利用しない）。返答は常に JSON 1個だけ。"""


# ---------------------------------------------------------------- action parsing
def _parse_action(raw: str):
    """Extract the single JSON action object from a model reply. Returns dict or None."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "action" not in obj:
        return None
    if not isinstance(obj.get("args"), dict):
        obj["args"] = {}
    return obj


def _dispatch(act: dict) -> str:
    """Execute one whitelisted tool action; return its observation. Never raises."""
    action = act.get("action")
    args = act.get("args", {})
    if action == "sonar":
        return tools.tool_sonar()
    if action == "judge":
        proj = args.get("project") or args.get("slug")
        if not proj:
            return "❔ judge には args.project（DefiLlama slug）が必要です。"
        return tools.tool_judge(proj, args.get("symbol"))
    if action == "find_token":
        sym = args.get("symbol") or args.get("token")
        if not sym:
            return "❔ find_token には args.symbol が必要です。"
        return tools.tool_find_token(sym)
    if action == "flow":
        slug = args.get("slug") or args.get("project")
        if not slug:
            return "❔ flow には args.slug が必要です。"
        return tools.tool_flow(slug)
    return f"❔ 不正なアクション '{action}'。有効: sonar / judge / find_token / flow / final"


# ---------------------------------------------------------------- fabrication guard
# Match $28.7M / 28,700,000 / 3.55% / 255.8 / 100% / 6 / 1.92 / 28.7 million / 1万 ...
# The leading boundary is ASCII-only `(?<![A-Za-z0-9_])`, NOT `\w`: Python's Unicode `\w` treats
# Japanese kana/kanji as word chars, so `(?<![\w])` SKIPPED any number glued to Japanese ("は42%"
# → missed) and mis-parsed decimals after a kana ("本体は3.36%" → "36%"). That silently broke the
# fabrication guard on exactly the Japanese prose the LLM writes (false negatives let invented
# numbers through; false positives dropped correct answers). ASCII boundary still blocks splitting
# inside latin/number tokens (v2, 0x1a23) while matching numbers next to Japanese correctly.
_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(\$?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|[KMBkmb]|million|billion|万|億)?)")
_ADVICE_RE = re.compile(
    r"(買うべき|売るべき|買った方|売った方|今が買い|今が売り|買い時|売り時|今すぐ買|今すぐ売|"
    r"buy now|sell now|should buy|should sell|recommend (buying|selling))", re.I)


def _normalize_num(token: str):
    """('$28.7M') -> (28700000.0, 'amount'); ('3.55%') -> (3.55, 'percent'); ('6') -> (6.0,'bare')."""
    raw = token.strip()
    low = raw.lower()
    has_pct = "%" in raw
    has_dollar = "$" in raw
    mult = 1.0
    if "万" in raw:
        mult = 1e4
    elif "億" in raw:
        mult = 1e8
    elif "billion" in low or low.rstrip().endswith("b"):
        mult = 1e9
    elif "million" in low or low.rstrip().endswith("m"):
        mult = 1e6
    elif low.rstrip().endswith("k"):
        mult = 1e3
    numpart = re.sub(r"[^\d.]", "", raw)
    if numpart in ("", "."):
        return None
    try:
        val = float(numpart) * mult
    except ValueError:
        return None
    kind = "percent" if has_pct else ("amount" if (has_dollar or mult > 1) else "bare")
    return (val, kind)


def _nfkc(s: str) -> str:
    # Fold full-width forms (％→%, ＄→$, ４２→42) and spelled-out 「パーセント」→% so the guard sees
    # the same ASCII shapes _NUM_RE matches, no matter how the JP model typed the number. Without
    # this, a fabricated "42％" / "42パーセント" is captured unitless, demoted to a bare int <100,
    # and silently skipped — a hole in the no-fab guard on exactly the Japanese prose the LLM writes.
    return unicodedata.normalize("NFKC", s or "").replace("パーセント", "%")


def _corpus_numbers(corpus: str):
    out = []
    for tok in _NUM_RE.findall(_nfkc(corpus)):
        n = _normalize_num(tok)
        if n:
            out.append(n)
    return out


def _matches(val: float, kind: str, corpus_nums) -> bool:
    for cval, ckind in corpus_nums:
        if kind == "percent":
            if ckind in ("percent", "bare") and abs(val - cval) <= 0.06:
                return True
        else:  # amount / bare quantity — allow amount<->bare cross match (e.g. $28.7M vs 28,700,000)
            if ckind in ("amount", "bare"):
                if abs(val - cval) <= 0.5:
                    return True
                if max(val, cval) > 0 and abs(val - cval) / max(val, cval) <= 0.02:
                    return True
    return False


def _untraceable_numbers(answer: str, corpus: str):
    """Numbers in `answer` that cannot be traced to `corpus` (the tool observations).

    Conservative on noise: skips small structural integers (<100, e.g. "5問"/"6 buyers")
    and 4-digit years, so it flags fabricated DATA (fake %, fake $) not prose counts.
    """
    corpus_nums = _corpus_numbers(corpus)
    bad, seen = [], set()
    for tok in _NUM_RE.findall(_nfkc(answer)):
        n = _normalize_num(tok)
        if n is None:
            continue
        val, kind = n
        is_int = float(val).is_integer()
        if kind == "bare" and is_int and (val < 100 or 1900 <= val <= 2100):
            continue   # structural small int / year, not a data claim
        if _matches(val, kind, corpus_nums):
            continue
        t = tok.strip()
        if t not in seen:
            seen.add(t)
            bad.append(t)
    return bad


# ---------------------------------------------------------------- finalize / degrade
_RULE = "─" * 52


_TOOL_LABEL = {"judge": "▼ 深掘り判定", "sonar": "▼ プール一覧（利回り軸）",
               "flow": "▼ 資金フロー", "find_token": "▼ トークン検索"}
_OBS_MAX_LINES = 24   # /why & CLI: cap each observation so a 37-row scan stays readable,
# while a bounded judge DD report (~18 lines incl. the on-chain block) shows in full


def _cap_lines(body: str) -> str:
    """Trim a long observation body to a readable head, keeping the source/header line and
    the largest entries (the engine sorts by TVL), with an honest note. The complete list
    is always available via /scan — /why is the evidence FOR an answer, not a data dump."""
    lines = body.split("\n")
    if len(lines) <= _OBS_MAX_LINES:
        return body
    hidden = len(lines) - _OBS_MAX_LINES
    return "\n".join(lines[:_OBS_MAX_LINES] + [f"… 他 {hidden} 行は省略（全件は /scan）"])


def _finalize(answer: str, observations) -> str:
    """Full evidence view (CLI + /why): the synthesis + the engine's sourced output, cleaned
    of raw '[tool {args}]' tags into friendly section headers and capped to a readable size."""
    out = []
    if _ADVICE_RE.search(answer or ""):
        out.append("⚠ 売買の推奨はしません（判定のみ）。")
    out.append((answer or "（回答テキストなし）").strip())
    out += ["", _RULE, "📋 根拠（engine の生データ・出典つき）"]
    if not observations:
        out.append("（tool 未実行）")
    for o in observations:
        first, _, body = o.partition("\n")
        tool = first[1:].split()[0] if first.startswith("[") else ""
        out += ["", _TOOL_LABEL.get(tool, "▼ 結果"), _cap_lines(body or first)]
    return "\n".join(out)


def _degrade(observations, reason: str) -> str:
    out = [f"⚠ agent は結論をまとめきれませんでした（{reason}）。推測はしません。"]
    out.append("以下は取得済みの engine 出力（出典つき・ground truth）です:"
               if observations else "取得済みデータはありません。")
    out += observations
    return "\n".join(out)


# --- chat-friendly rendering (for a human surface like Telegram) ---
# A human chatting wants the readable synthesis + a one-line source note, NOT the full
# 問診 verbatim 根拠 dump (that stays in `full`, viewable via /why and used by the CLI).
# Mantle chain FIRST when an on-chain read ran: MantleFi's Mantle-native layer is the direct
# rpc.mantle.xyz verification, not the aggregator. The numbers come from DefiLlama (labelled
# honestly as 集計値); the chain is what we verified them against — so it leads the footer.
_SRC_NAMES = [("mantle.xyz", "🔗 Mantle チェーンで直接確認"),
              ("llama.fi", "DefiLlama（集計値）"), ("DefiLlama", "DefiLlama（集計値）"),
              ("geckoterminal", "GeckoTerminal"), ("GeckoTerminal", "GeckoTerminal"),
              ("dexscreener", "DexScreener")]


def _source_footer(observations) -> str:
    blob = "\n".join(observations)
    names, seen = [], set()
    for key, name in _SRC_NAMES:
        if key in blob and name not in seen:
            seen.add(name)
            names.append(name)
    return ("📊 出典: " + " / ".join(names)) if names else ""


# The VERDICT is the engine's, not the LLM's (core rule). We translate the engine's class
# (from the tool observations) into plain Japanese here, and prepend it — the LLM only
# supplies the plain "why". Most-cautionary class wins (same spirit as report.render).
_BADGE_ORDER = [config.CLASS_EMISSION_TRAP, config.CLASS_PRICE_ILLUSION, config.CLASS_WASH,
                config.CLASS_INACTIVE, config.CLASS_OUTFLOW, config.CLASS_REWARD_DEP,
                config.CLASS_DEAD, config.CLASS_REAL_INFLOW, config.CLASS_REAL_YIELD,
                config.CLASS_FLAT, config.CLASS_UNKNOWN]
_PLAIN = config.CLASS_PLAIN   # plain-Japanese read per class — shared source (see config); engine-derived


def _verdict_badge(observations) -> str:
    """Plain-Japanese verdict, derived deterministically from the engine's class in the
    observations. PREFER the judge tool's headline (it already merges yield+flow+wash into
    ONE verdict), then flow's, then anything — so a tangential axis from a side tool can't
    eclipse the main verdict. Empty if no class is present (e.g. an open scan)."""
    def headlines(obs):
        out = []
        for o in obs:
            for line in o.splitlines():
                if line.strip().startswith("判定:"):
                    out.append(line)
        return "\n".join(out)

    # A badge is the engine's SINGLE verdict — show it ONLY for a focused single-target
    # judgment. A descriptive/chat answer (no tools) or a broad sonar-led answer (several
    # pools, or a trap surfaced alongside the good ones) must NOT inherit one pool's verdict
    # as THE headline — that's exactly the "ただの判定bot" feel we're removing.
    if any(o.startswith("[sonar") for o in observations):
        return ""
    targets = [o for o in observations if o.startswith("[judge")] or \
              [o for o in observations if o.startswith("[flow")]
    if len(targets) != 1:
        return ""
    # match the raw class OR its plain form: report.render shows CLASS_PLAIN in the 判定 line,
    # so searching only for the raw constant would always miss (the badge would silently vanish).
    blob = headlines(targets)
    for cls in _BADGE_ORDER:
        if cls in blob or _PLAIN.get(cls, "\0") in blob:
            return _PLAIN.get(cls, "")
    return ""


def _chat_finalize(answer: str, observations) -> str:
    """Readable chat reply: engine verdict (plain, single-target only) + the LLM's plain 'why'
    + a one-line source.

    no-fabrication holds HERE too: if the prose carries a number that can't be traced to the
    engine observations, the prose is DROPPED (the engine badge stays) rather than shown — the
    same rule the web /say (facts.narrate) and the monitor editor already enforce. This closes
    the one surface a judge can poke (Telegram reply + the /ask fallback both end here) that
    could otherwise print an unverified figure and break the "can't fabricate" guarantee.
    We drop rather than ⚠-annotate so the chat stays clean (no inline warning); a well-behaved
    answer copies numbers verbatim from the observations, so this only fires on a real violation."""
    parts = []
    badge = _verdict_badge(observations)
    if badge:
        parts.append(badge)
    if _ADVICE_RE.search(answer or ""):
        parts.append("⚠ 売買の推奨はしません（判定のみ）。")
    if answer and answer.strip():
        if _untraceable_numbers(answer, "\n".join(observations)):
            parts.append("（裏の取れた数値だけでお答えします。詳しい内訳は /why をご覧ください。）")
        else:
            parts.append(answer.strip())
    foot = _source_footer(observations)
    if foot:
        parts += ["", foot + "　｜　詳細はこちら /why"]
    return "\n".join(parts) if parts else "（回答なし）"


_NARRATE_CAP = 4000   # engine context fed to the final-narration LLM call


def _narrate_final(draft: str, observations, backend, lang: str = "ja", model=None) -> str:
    """Re-narrate the loop's draft answer into a polished 1-2 sentence reply on a higher-
    quality backend (glm-5.1), grounded ONLY in the engine observations. Returns "" on failure OR if
    it introduces a number not in the observations (caller keeps the Groq draft). This is the hybrid:
    a fast Groq loop, but the one sentence the user reads stays at glm quality — no-fab is preserved
    because every number in the polished line still must trace to the engine corpus.
    `lang="en"` changes ONLY the reply language; the number guard is language-independent."""
    corpus = "\n".join(observations)[:_NARRATE_CAP]
    if not (draft or "").strip() or not corpus.strip():
        return ""
    if lang == "en":
        sys_p = ("You are a friendly Mantle DeFi yield explainer. Using ONLY the findings below "
                 "(engine-derived verdicts + numbers) and the draft, give a beginner the takeaway in "
                 "1–2 short English sentences. Never invent a number (no % or $ not present below). "
                 "No loaded words like 'trap' or 'scam'; never disparage Mantle or any pool. "
                 "No buy/sell advice. No first person. Calm, plain English.")
        usr_p = (f"Draft:\n{draft}\n\nFindings (use ONLY these):\n{corpus}\n\n"
                 "Keep the draft's meaning; polish it into 1–2 plain-English sentences.")
    else:
        sys_p = ("あなたは Mantle DeFi 利回りのやさしい解説者です。下の調査結果（engine 由来の判定＋数値）"
                 "と下書きだけを使い、初心者に向けて1〜2文で要点を伝える。新しい数値は作らない（下に無い %・$ を"
                 "書かない）。『罠』『詐欺』等の決めつけ語は使わず、Mantle やプールを貶めない。売買の推奨は"
                 "しない。一人称は使わない。落ち着いた丁寧な日本語で。")
        usr_p = (f"下書き:\n{draft}\n\n調査結果(これだけ使う):\n{corpus}\n\n"
                 "下書きの意味を保ったまま、やさしい1〜2文に整える。")
    try:
        out = nim.chat([{"role": "system", "content": sys_p},
                        {"role": "user", "content": usr_p}], backend=backend, model=model).strip()
    except nim.NimError:
        return ""
    if _untraceable_numbers(out, corpus):
        return ""
    return out


# ---------------------------------------------------------------- the loop
def _build_user_content(question: str, history) -> str:
    """Prepend prior conversation as context (so the agent remembers the thread).

    History is embedded inside the user message (not as separate assistant turns) so the
    JSON action protocol stays unambiguous. Old numbers in context are NOT to be reused —
    the system prompt tells the model to re-fetch, and the fabrication guard still checks
    every number in the new answer against the new tool observations.
    """
    if not history:
        return question
    ctx = ["【これまでの会話（文脈。数値は必ずツールで取り直す）】"]
    for q, a in history:
        ctx.append(f"ユーザー: {q}")
        ctx.append(f"あなた: {a}")
    return "\n".join(ctx) + f"\n\n【今の質問】\n{question}"


def run(question: str, history=None, model=None, trace: bool = False,
        loop_backend=None, final_backend=None, final_model=None, seed=None,
        require_tool=False, lang: str = "ja") -> dict:
    """Answer a free-text research question by routing it through the engine tools.

    `trace=True` prints each ReAct step (the model's thought + the tool it chose) to stdout —
    the monitor's --verbose mode uses it to make the investigator agents' decisions visible.

    `history` = list of (user_question, assistant_answer) prior turns for conversation
    memory (None/empty = stateless single-shot, as the CLI uses it).
    `model` = LLM model override (None = the backend's primary). The heartbeat monitor passes
    maverick for its high-volume investigator fan-out (see config.MONITOR_INVESTIGATOR_MODEL).
    `loop_backend` = which LLM provider runs the ReAct loop (None = NIM default; "groq" = fast,
    used by the interactive web /ask). `final_backend` = re-narrate the final user-facing sentence
    on this provider for phrasing quality (web passes "nim"=glm); None/== loop_backend skips it.
    The hybrid keeps the loop fast (Groq) while the read sentence stays glm-quality — numbers stay
    engine-owned regardless (no-fab).
    `seed` = WEB-ONLY extraction lock (default None → CLI/Telegram/monitor unaffected). A dict
    {"hint": <system hint str>, "bind": {"project","symbol"}}: the hint pins the exact target the
    deterministic router already resolved; "bind" FORCES those args onto any judge call so a weaker
    model cannot corrupt the symbol (e.g. USDT0→USDT) — extraction accuracy then equals the router's.
    `require_tool` = WEB-ONLY (default False): reject a "final" before any engine tool ran (forces
    grounding, once); set True only when the question resolved to judge/scan.
    Returns a dict with:
      - "chat"    : readable reply for a human surface (synthesis + 1-line source) ← Telegram
      - "full"    : synthesis + verbatim 根拠 ground-truth block ← CLI / /why
      - "answer"  : the bare synthesis, to store in conversation history
      - "verdict" : the engine's plain single-target verdict badge ("" if none) ← monitor
    """
    sys_content = _system_prompt()
    if seed and seed.get("hint"):
        sys_content += "\n\n【今回の確定情報（ルーターが特定済み・これを使う）】\n" + seed["hint"]
    if lang == "en":
        # English UI: every USER-FACING string (final answer / ask / chips) must come out in English.
        # Internal reasoning + tool protocol stay as-is; numbers/verdicts remain engine-owned (no-fab).
        sys_content += ("\n\n【言語】The user reads ENGLISH. Write every user-facing text — the final "
                        "answer, any `ask` question, and `chips` — in natural plain English "
                        "(tool names/args and JSON protocol unchanged).")
    messages = [{"role": "system", "content": sys_content},
                {"role": "user", "content": _build_user_content(question, history)}]
    observations = []
    requested = {}            # (action|args) -> observation, to avoid burning steps on repeats
    dispatched = 0            # engine tool calls so far (G1: no "final" before a real tool ran)
    nudged_empty_final = False
    ask_nudged = False        # 聞き返し: one re-prompt if the model's `ask` is empty / has a number

    for _step in range(config.NIM_MAX_STEPS):
        try:
            raw = nim.chat(messages, model=model, backend=loop_backend)
        except nim.NimKeyMissing as e:
            msg = f"❔ agent モードには NIM 鍵が必要です。\n{e}"
            return {"chat": msg, "full": msg, "answer": msg, "verdict": ""}
        except nim.NimError as e:
            return {"chat": "⚠ いま調べられませんでした（通信エラー）。少し待ってもう一度どうぞ。",
                    "full": _degrade(observations, f"LLM 通信失敗: {e}"),
                    "answer": "(通信失敗で結論を出せませんでした)",
                    "verdict": _verdict_badge(observations)}

        act = _parse_action(raw)
        if act is None:
            if trace:
                print(f"        ↳ step{_step + 1}: 無効なJSON応答 → 再要求")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user",
                             "content": "前の返答が有効な JSON ではありません。指定の JSON オブジェクト1個だけで返答してください。"})
            continue

        messages.append({"role": "assistant", "content": json.dumps(act, ensure_ascii=False)})

        if trace:
            if act.get("action") == "final":
                print(f"        ↳ step{_step + 1}: 十分な観測が得られた → 結論(final)")
            else:
                _th = (act.get("thought") or "").strip().replace("\n", " ")
                print(f"        ↳ step{_step + 1}: 「{_th[:34]}」→ {act.get('action')} を実行 {act.get('args', {})}")

        if act.get("action") == "ask":
            # 聞き返し: a sanctioned clarification turn for genuinely ambiguous questions. Exempt from
            # require_tool — asking never fabricates. The question must be NUMBER-FREE (no tool ran, so
            # any number would be invented); chips = tap-to-reply follow-ups the web renders. Memory
            # carries this turn forward, so the user's chip/answer continues the same investigation.
            aargs = act.get("args") or {}
            question = (aargs.get("question") or aargs.get("answer") or "").strip()
            corpus = "\n".join(observations)
            raw_chips = aargs.get("chips") if isinstance(aargs.get("chips"), list) else []
            chips = [str(c).strip() for c in raw_chips if str(c).strip()][:4]
            chips = [c for c in chips if not _untraceable_numbers(c, corpus)]   # no numbers in chips
            if (not question or _untraceable_numbers(question, corpus)) and not ask_nudged:
                ask_nudged = True
                if trace:
                    print(f"        ↳ step{_step + 1}: ask が空/数値混入 → 数値なしの質問文を再要求")
                messages.append({"role": "user", "content":
                    "聞き返すなら args.question に、数値を入れない短い質問文を入れてください"
                    "（例『どのプールの利回りについて知りたいですか？』）。"})
                continue
            if not question or _untraceable_numbers(question, corpus):
                question = "どの利回り・どのプールについて知りたいですか？"   # safe generic (number-free, not data)
            if trace:
                print(f"        ↳ step{_step + 1}: 聞き返し（ask）→「{question[:30]}」 chips={chips}")
            return {"chat": question, "full": question, "answer": question,
                    "say": question, "verdict": "", "ask": question, "chips": chips}

        if act.get("action") == "final":
            if require_tool and not dispatched and not nudged_empty_final:
                nudged_empty_final = True   # G1 GROUND: force >=1 engine tool before concluding (once)
                if trace:
                    print(f"        ↳ step{_step + 1}: 証拠なし final → 却下（まず道具で確認させる）")
                messages.append({"role": "user", "content":
                    "まだ engine ツールで調べていません。まず judge か sonar で確認し、その観測に基づいて final を返してください。"})
                continue
            answer = (act.get("args") or {}).get("answer", "")
            chat_answer = answer
            # re-narrate when the final differs from the loop by backend OR model (same provider, a
            # stronger model for the sentence the user reads: loop=Groq llama-70b → final=Groq gpt-oss-120b)
            if final_backend and (final_backend != loop_backend or final_model):
                chat_answer = _narrate_final(answer, observations, final_backend, lang, model=final_model) or answer
            # "say" = the guarded plain narration ALONE (no badge/footer), for a surface that already
            # shows the verdict + receipts separately (the web card). Dropped to "" if it carries an
            # untraceable number (no-fab) — the card's own note then stands in.
            _corpus = "\n".join(observations)
            say = chat_answer if (chat_answer.strip() and not _untraceable_numbers(chat_answer, _corpus)) else ""
            return {"chat": _chat_finalize(chat_answer, observations),
                    "full": _finalize(answer, observations),
                    "answer": answer or "(回答テキストなし)",
                    "say": say,
                    "verdict": _verdict_badge(observations)}

        # Binding seed: force the router-resolved target onto any judge call, so a corrupted symbol
        # (USDT0→USDT) or wrong project cannot mis-point the engine — extraction stays as accurate as
        # the deterministic router. Only active when a seed is passed (web); other callers untouched.
        if seed and seed.get("bind") and act.get("action") == "judge":
            a = dict(act.get("args") or {})
            a["project"] = seed["bind"].get("project") or a.get("project")
            a["symbol"] = seed["bind"].get("symbol") or a.get("symbol")
            act["args"] = a
        dispatched += 1
        key = act.get("action", "") + "|" + json.dumps(act.get("args", {}), ensure_ascii=False, sort_keys=True)
        if key in requested:
            obs = requested[key]
            nudge = "（同じ取得は既出。別の対象を調べるか final で結論を。）"
        else:
            obs = _dispatch(act)
            requested[key] = obs
            nudge = ""
        observations.append(f"[{act.get('action')} {act.get('args', {})}]\n{obs}")
        messages.append({"role": "user", "content": f"観測:\n{obs}\n{nudge}次の JSON を返答。"})

    # G2 TERMINATE: out of steps. If a usable engine verdict exists, finalize FROM it (the verdict is
    # engine-derived from the observations, never model text) instead of throwing the work away; else
    # degrade honestly with an explicit empty verdict. Every return path carries the "verdict" key.
    badge = _verdict_badge(observations)
    if badge:
        return {"chat": _chat_finalize("", observations),
                "full": _finalize("（ステップ上限・engine の観測から結論）", observations),
                "answer": "(ステップ上限・engine 観測から結論)",
                "verdict": badge}
    return {"chat": "⚠ 調べきれませんでした。質問を少し絞ってもう一度どうぞ。",
            "full": _degrade(observations, f"ステップ上限({config.NIM_MAX_STEPS})到達"),
            "answer": "(ステップ上限で結論を出せませんでした)",
            "verdict": ""}
