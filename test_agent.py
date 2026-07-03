#!/usr/bin/env python3
"""MantleFi — OFFLINE unit tests for the agent's safety machinery (no LLM, no network).

Proves the load-bearing guarantee: the fabrication guard catches an invented number while
passing real ones across formats; the action parser is robust; bad actions don't crash.
Run: python3 test_agent.py
"""
from __future__ import annotations

import sys

import agent
import config

_fails = []


def check(name, cond):
    print(f"  {'✅' if cond else '❌'} {name}")
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------- fabrication guard
print("[1] fabrication guard — catch invented, pass real (format-tolerant)")
corpus = ("[judge aave-v3 SUSDE]\napy 3.55% = base 3.55% + reward 0.00%\n"
          "TVL 28,700,000\nvol/liq 1.92, buyers 6 / sellers 7, tx/unique_wallet 255.8\n"
          "利回りの 100% が apyBase")

# (a) the headline test: only the fabricated 42% is flagged
ans_a = "USDY は 3.55% の実利回り、TVL $28.7M、tx/wallet 255.8。だが FooPool は 42% を払う。"
bad_a = agent._untraceable_numbers(ans_a, corpus)
check("42% flagged", "42%" in bad_a)
check("3.55% passes (vs corpus 3.55%)", "3.55%" not in bad_a)
check("$28.7M passes (vs corpus 28,700,000)", "$28.7M" not in bad_a)
check("255.8 passes", "255.8" not in bad_a)
check("ONLY 42% flagged (len==1)", bad_a == ["42%"])

# (b) all-traceable -> empty
check("all-traceable answer -> no flags",
      agent._untraceable_numbers("base 3.55%、vol/liq 1.92、100% が apyBase", corpus) == [])

# (c) structural small ints and years are not flagged as data
check("structural '5問' / '6 buyers' not flagged",
      agent._untraceable_numbers("5問の問診で buyers 6 を確認", "") == [])
check("year 2026 not flagged",
      agent._untraceable_numbers("as of 2026 のデータ", "") == [])

# (d) a fabricated $ amount is caught
check("fabricated $999M caught",
      "$999M" in agent._untraceable_numbers("FooPool の TVL は $999M", corpus))

# ---------------------------------------------------------------- advice detector
print("[2] advice detector (no buy/sell recommendation)")
check("'今すぐ買うべき' detected", bool(agent._ADVICE_RE.search("今すぐ買うべき")))
check("'should buy' detected", bool(agent._ADVICE_RE.search("you should buy this")))
check("'推奨なし' NOT detected", not agent._ADVICE_RE.search("推奨なし・タイミングは人間が決める"))

# ---------------------------------------------------------------- action parser
print("[3] action parser robustness")
check("plain json", agent._parse_action('{"thought":"x","action":"judge","args":{"project":"aave-v3","symbol":"SUSDE"}}')["action"] == "judge")
check("```json fenced", agent._parse_action('```json\n{"action":"sonar","args":{}}\n```')["action"] == "sonar")
check("prose + json", agent._parse_action('まず調べます。{"action":"final","args":{"answer":"hi"}}')["action"] == "final")
check("no json -> None", agent._parse_action("ここに JSON はありません") is None)
check("missing action -> None", agent._parse_action('{"foo":1}') is None)
_a = agent._parse_action('{"action":"sonar","args":"oops"}')
check("non-dict args coerced to {}", _a is not None and _a["args"] == {})

# ---------------------------------------------------------------- dispatch (no-network branches only)
print("[4] dispatch guards (no network)")
check("bad action -> guidance", "不正なアクション" in agent._dispatch({"action": "frobnicate", "args": {}}))
check("judge w/o project -> guidance", "args.project" in agent._dispatch({"action": "judge", "args": {}}))
check("find_token w/o symbol -> guidance", "args.symbol" in agent._dispatch({"action": "find_token", "args": {}}))
check("flow w/o slug -> guidance", "args.slug" in agent._dispatch({"action": "flow", "args": {}}))

# ---------------------------------------------------------------- finalize / degrade
print("[5] finalize / degrade structure")
obs = [corpus]
fin = agent._finalize("SUSDE の base 3.55%、これは 100% 報酬。", obs)
check("finalize includes 根拠 block", "根拠" in fin)
check("finalize includes the answer", "100% 報酬" in fin)
check("finalize strips raw [tool] tags", "[judge" not in fin and "▼" in fin)
check("finalize does NOT surface noisy traceability warning", "⚠ 警告" not in agent._finalize("FooPool は 42% 払う。", obs))
fin_adv = agent._finalize("これは今すぐ買うべき。base 3.55%。", obs)
check("finalize: advice -> recommendation notice", "推奨はしません" in fin_adv)
# the guard FUNCTION still works (used internally / for the submission's accuracy story)
check("guard fn still flags fabricated $999M", "$999M" in agent._untraceable_numbers("Foo の TVL は $999M", corpus))
deg = agent._degrade(obs, "テスト理由")
check("degrade includes reason + observations", "テスト理由" in deg and "根拠" not in deg and corpus in deg)
# a long observation (e.g. a 37-row sonar) is capped so /why stays readable
_long = "[sonar {}]\n" + "\n".join(f"行{i}" for i in range(40))
_fin_long = agent._finalize("総括テスト。", [_long])
check("long observation is capped (not all 40 rows dumped)", "行39" not in _fin_long and "省略" in _fin_long)
check("capped /why keeps the head rows", "行0" in _fin_long)
check("short observation is NOT capped", "省略" not in agent._finalize("x", [corpus]))

# verdict badge must derive from the engine's PLAIN 判定 line (render shows CLASS_PLAIN, so a
# badge matcher keyed only on the raw class constant would silently vanish — regression guard)
_jobs = ['[judge {}]\n判定: ' + config.CLASS_PLAIN[config.CLASS_EMISSION_TRAP] + '\napy 3.75%']
check("verdict badge derives from plain 判定 line",
      agent._verdict_badge(_jobs) == config.CLASS_PLAIN[config.CLASS_EMISSION_TRAP])
check("no badge for an open scan (no 判定 headline)",
      agent._verdict_badge(['[sonar {}]\nrow a\nrow b']) == "")
# badge is single-target only: a broad answer (sonar present, or 2+ judges, or a trap surfaced
# next to the good ones) must NOT show one pool's verdict as the headline (anti "判定bot")
_two = ['[judge {"symbol":"USDY"}]\n判定: ' + config.CLASS_PLAIN[config.CLASS_REAL_YIELD],
        '[judge {"symbol":"BSB"}]\n判定: ' + config.CLASS_PLAIN[config.CLASS_WASH]]
check("no badge when several targets judged (broad answer)", agent._verdict_badge(_two) == "")
check("no badge when sonar led the answer even if one pool judged",
      agent._verdict_badge(['[sonar {}]\n…'] + _jobs) == "")

# ---------------------------------------------------------------- run() loop + memory (monkeypatched NIM, no network)
print("[6] agent.run loop + conversation memory (monkeypatched NIM, no network)")
_captured = {}
def _fake_chat(messages, model=None, backend=None):
    _captured["messages"] = messages
    return '{"thought":"done","action":"final","args":{"answer":"記憶テストの結論です。"}}'
_orig_chat = agent.nim.chat
agent.nim.chat = _fake_chat
try:
    res = agent.run("USDYは本物？", history=[("前の質問は何？", "前の答えの要約")])
    check("run returns dict (chat/full/answer)", set(["chat", "full", "answer"]).issubset(res))
    check("answer is the bare synthesis", res["answer"] == "記憶テストの結論です。")
    check("chat reply is readable (no 根拠 dump)", "記憶テストの結論です。" in res["chat"] and "根拠" not in res["chat"])
    check("full has the 根拠 block (for CLI/why)", "根拠" in res["full"] and "記憶テストの結論です。" in res["full"])
    _umsg = _captured["messages"][1]["content"]
    check("history threaded into prompt", "前の質問は何？" in _umsg and "前の答えの要約" in _umsg)
    check("new question present in prompt", "USDYは本物？" in _umsg)
    agent.run("単発の質問")  # stateless (no history)
    check("no-history prompt omits context header", "これまでの会話" not in _captured["messages"][1]["content"])
finally:
    agent.nim.chat = _orig_chat

# ---------------------------------------------------------------- tools: narrow scan table
print("[7] tools.render_scan — phone-narrow table + compact USD (no network)")
import tools
check("_short_usd 163M", tools._short_usd(162672336) == "$163M")
check("_short_usd 24.6M", tools._short_usd(24560146) == "$24.6M")
check("_short_usd 950", tools._short_usd(950) == "$950")
check("_short_usd 0", tools._short_usd(0) == "$0")
_rows = [  # deliberately out of order to prove render_scan re-sorts deterministically
    {"class": config.CLASS_EMISSION_TRAP, "target": "aave-v3 SUSDE",
     "numbers": {"tvlUsd": 162672336, "apy": 3.5, "apyBase": 0.0, "apyReward": 3.5}},
    {"class": config.CLASS_REAL_YIELD, "target": "ondo-yield-assets USDY",
     "numbers": {"tvlUsd": 28683044, "apy": 3.55, "apyBase": 3.55, "apyReward": 0.0}},
    {"class": config.CLASS_REAL_YIELD, "target": "aave-v3 USDT0",
     "numbers": {"tvlUsd": 24560146, "apy": 4.52, "apyBase": 3.23, "apyReward": 1.29}},
    {"class": config.CLASS_DEAD, "target": "aave-v3 SYRUPUSDT",
     "numbers": {"tvlUsd": 90458095, "apy": 0.0, "apyBase": 0.0, "apyReward": 0.0}}]
_scan = tools.render_scan(_rows)
check("scan uses glyph, not the wide class name", "🔴" in _scan and "emission罠 (emission-only)" not in _scan)
check("scan shows compact TVL ($163M)", "$163M" in _scan)
check("scan shows symbol·proto label (SUSDE·Aave)", "SUSDE·Aave" in _scan)
check("scan dropped the old wide header", "apy=base+reward" not in _scan)
check("scan data rows are phone-narrow (≤40 cols)",
      max(len(l) for l in _scan.splitlines()) <= 40)
# consistent ordering: 🟢 real yield first, traps lower; within a class, larger TVL first
_data = "\n".join(l for l in _scan.splitlines() if l[:1] in "🟢🟡🔴⚫・")
check("real yield (🟢) grouped above trap (🔴)", _data.index("🟢") < _data.index("🔴"))
check("trap (🔴) above dead (⚫)", _data.index("🔴") < _data.index("⚫"))
check("within 🟢, larger TVL first (USDY $28.7M before USDT0 $24.6M)", _data.index("USDY") < _data.index("USDT0"))
# source = clean name only, no raw /pools JSON URL
check("source is the name (出典: DefiLlama)", "出典: DefiLlama" in _scan)
check("no raw pools JSON URL", "llama.fi" not in _scan and "/pools" not in _scan)

# ---------------------------------------------------------------- telegram: mono only for tables
print("[8] telegram_bot — DAILY-DIGEST surface only (no agent Q&A; points to web)")
import telegram_bot as tg
check("handle returns (text, mono) 2-tuple", len(tg.handle("/start", 1)) == 2)
check("/start → welcome prose (plain)", "デイリー" in tg.handle("/start", 1)[0] and tg.handle("/start", 1)[1] is False)
check("free-text is NOT answered by the agent (subscription instead)", "購読" in tg.handle("利回りいいのは？", 1)[0])
check("free-text points to the web chat for questions", "web" in tg.handle("USDT0は本物？", 1)[0])
check("reply is always plain prose (never a mono table)", tg.handle("利回りいいのは？", 1)[1] is False)

# ---------------------------------------------------------------- web guardrails + binding seed (monkeypatched, no network)
print("[9] agent.run guardrails (G1/G2) + binding seed — web-only kwargs, defaults unchanged")
import config as _cfg
_REAL_PLAIN = _cfg.CLASS_PLAIN[_cfg.CLASS_REAL_YIELD]   # track config exactly (don't hard-code the label)

def _mk_chat(seq):
    st = {"i": 0, "msgs": []}
    def chat(messages, model=None, backend=None):
        st["msgs"] = messages
        r = seq[min(st["i"], len(seq) - 1)]
        st["i"] += 1
        return r
    chat.st = st
    return chat

_disp = []
def _fake_dispatch(act):
    _disp.append({"action": act.get("action"), "args": dict(act.get("args") or {})})
    if act.get("action") == "judge":
        return f"📋 調査レポート: aave-v3 USDT0\n判定: {_REAL_PLAIN}"
    return "❔ 取得失敗"

_oc, _od = agent.nim.chat, agent._dispatch
try:
    agent._dispatch = _fake_dispatch
    # (a) G1: a premature 'final' (no tool yet) is rejected once, then the model is forced to a tool
    _disp.clear()
    agent.nim.chat = _mk_chat(['{"action":"final","args":{"answer":"GHOは本物です"}}',
                               '{"action":"judge","args":{"project":"aave-v3","symbol":"GHO"}}',
                               '{"action":"final","args":{"answer":"結論"}}'])
    ra = agent.run("GHOは本物？", require_tool=True)
    check("G1 forced a real tool before final", any(c["action"] == "judge" for c in _disp))
    check("G1 result is engine-grounded (verdict present)", ra.get("verdict", "").startswith("🟢"))
    # (b) binding seed: a CORRUPTED symbol/project is overridden to the router's exact target
    _disp.clear()
    agent.nim.chat = _mk_chat(['{"action":"judge","args":{"project":"ondo-yield-assets","symbol":"USDT"}}',
                               '{"action":"final","args":{"answer":"結論"}}'])
    rb = agent.run("USDT0は本物？", require_tool=True,
                   seed={"hint": "対象=aave-v3 USDT0", "bind": {"project": "aave-v3", "symbol": "USDT0"}})
    _jb = next(c for c in _disp if c["action"] == "judge")
    check("binding forced project aave-v3 (not ondo)", _jb["args"]["project"] == "aave-v3")
    check("binding forced symbol USDT0 (not corrupted USDT)", _jb["args"]["symbol"] == "USDT0")
    check("seed hint placed in system prompt", "確定情報" in agent.nim.chat.st["msgs"][0]["content"])
    # (c) G2: out of steps with a judge obs present → finalize WITH the engine verdict, not bare degrade
    _disp.clear()
    agent.nim.chat = _mk_chat(['{"action":"judge","args":{"project":"aave-v3","symbol":"USDT0"}}']
                              + ['{"action":"flow","args":{"project":"aave-v3"}}'] * 10)
    rc = agent.run("USDT0は本物？")
    check("G2 step-limit still returns engine verdict", rc.get("verdict", "").startswith("🟢"))
    check("every return path carries a 'verdict' key", "verdict" in rc)
    # (d) regression: no seed/require_tool → no seed block injected, default behavior preserved
    agent.nim.chat = _mk_chat(['{"action":"final","args":{"answer":"x"}}'])
    agent.run("単発の質問")
    check("no seed → system prompt has no 確定情報 block", "確定情報" not in agent.nim.chat.st["msgs"][0]["content"])
    # (e) require_tool=False (describe/none path) → an empty-observation final is LEGAL (not blocked)
    _disp.clear()
    agent.nim.chat = _mk_chat(['{"action":"final","args":{"answer":"DeFiの説明です"}}'])
    re_ = agent.run("DeFiって何？")
    check("require_tool=False allows a zero-tool final (describe path)", not _disp and "DeFi" in re_.get("answer", ""))
finally:
    agent.nim.chat, agent._dispatch = _oc, _od

# ---------------------------------------------------------------- result
print()
if _fails:
    print(f"❌ {len(_fails)} FAILED: {_fails}")
    sys.exit(1)
print("✅ ALL OFFLINE AGENT TESTS PASSED")
