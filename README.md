# MantleFi — an on-chain research agent for Mantle DeFi

> Mantle Research Challenge · Track 2 (research agent)
>
> **MantleFi is an on-chain research agent that investigates Mantle DeFi yields by reading the
> chain itself — and physically cannot fabricate a number.** Ask it *"what's real yield on Mantle
> right now?"* or *"is GHO's yield real or reward-propped?"*: it routes the question through a
> deterministic on-chain engine, **shows you each step it takes** (identify the pool → split the
> yield into base vs reward → verify the reward token / contract *on-chain*), and returns a sourced
> verdict where **every number clicks through to its on-chain receipt.**
>
> **Two surfaces, both agentic:** a **live chat** — a self-hosted ReAct loop that decides which
> checks to run and *shows its work* — and a **daily multi-agent monitor**, where a crew of
> investigator agents fan out over the notable pools each morning and an editor agent merges their
> findings into one digest. The deterministic engine underneath is the **forensics lab that keeps
> the agent honest** (it owns every number/verdict; the LLM only routes and narrates).
>
> **What it never does:** give buy/sell/timing advice. It shows you the fishing spot; you
> decide whether to fish. Read-only · free data · stdlib-only · Mantle (chainid 5000) only.
>
> **🌐 Try it live (no setup):** **https://mantlefi.onrender.com**
> — works on phones, JP/EN toggle in the nav, and installs as an app via "Add to Home Screen" (PWA).
> Host your own copy in ~5 minutes: [`DEPLOY.md`](DEPLOY.md).

---

## Why it's useful

Every DeFi dashboard shows you green numbers: "TVL $150M!", "APY 39%!". They stop at
*"there's a crowd here."* They never tell you the crowd is fake. MantleFi runs the
interrogation a careful analyst would, on free on-chain data, and returns a transparent
verdict — **including "this looks great but it's a trap."**

On-chain money can't hide. So instead of trusting a USD-TVL headline (which moves with
price) or an APY headline (which can be 100% token emissions), MantleFi separates the
signal price hides:

- **apyBase vs apyReward** — is the yield real (interest/fees/RWA coupon) or printed?
- **token-amount vs token-USD** — did capital actually arrive, or did the price just move?
- **volume / liquidity & tx-per-wallet** — is the activity organic or wash from a few bots?

**Then it does what a chatbot with web access cannot: it goes to the chain.** A generalist
LLM can re-serve DefiLlama's APY number. MantleFi reads **Mantle RPC directly to _verify_** it:

- **emission reality** — does an on-chain reward-token distribution actually back this yield?
  Ondo USDY has **no reward token on-chain** (emission-independent, *verified on-chain*); Aave
  GHO's reward is paid via the `aManGHO` reward token (so the reward portion dies if emissions
  stop). DefiLlama's `apyReward` is a *computed* number — this is the chain's own answer.
- **contract truth** — is the underlying a real, identifiable contract? code size, on-chain
  symbol match, EIP-1967 upgradeable-proxy, `owner()` — the `risk-evaluator` /
  `address-registry-navigator` principle, implemented **natively (no MCP, no npx)**.
- **reward-token soundness** — the token the reward is *paid in*: does it exist on-chain, and is
  it itself an upgradeable / owner-controlled contract? A dashboard shows "apyReward 6%" but never
  what that 6% is paid in nor whether that token is sound.

This is trust-minimized **二重確認**: aggregator says X, the chain says Y → agree = high
confidence, diverge = flag. It is exactly the part a judge cannot get by asking ChatGPT.

## The 6 classes

| class | meaning | rule (thresholds in `config.py`) |
|---|---|---|
| 🟢 実利回り real yield | yield survives if emissions stop | apyBase ≥ 50% of apy, real source |
| 🟡 報酬依存 reward-dependent | real base, but headline leans on rewards | apyReward share ≥ 40% |
| 🔴 emission罠 emission-only | headline collapses without rewards | apyBase < 0.5%, reward dominates |
| 🔴 価格錯覚 price illusion | TVL ≠ deposits | token-amount ~flat, USD-TVL moves >3% |
| 🔴 wash/集中 wash | volume isn't organic | vol/liq ≥ 1.0, or tx/wallet ≥ 50, or one-sided few wallets |
| ⚪ 流出 outflow | capital leaving | token-amount Δ < −3% |
| ⚫ 利回りゼロ zero-yield | not a trap, just pays nothing | apy = 0 (excluded) |

## Quickstart (no install — Python stdlib only)

```bash
cd mantlefi
python3 research.py scan                          # broad sonar: classify every Mantle yield pool (thin pools flagged 小)
python3 research.py judge ondo-yield-assets USDY  # deep judge of one target (+ on-chain verify + links)
python3 research.py report aave-v3 GHO             # write a plain-language research report (Markdown) to examples/
python3 research.py token SPCX                     # find a token's Mantle pools by EXACT identity
python3 research.py example                        # the reference cases below
python3 research.py agent "今のMantleで本物の利回りは？"  # the self-hosted research AGENT
```

`scan/judge/token/example` are the deterministic engine and need **no key**. `agent` is the
self-hosted research agent (below) and needs a free NIM key.

### Agent mode — the research agent (self-hosted, not host-dependent)
`agent "<question>"` runs a ReAct loop (`agent.py`) whose reasoning runs on a free NVIDIA NIM
model called over plain `urllib` (`nim.py`, no `openai` package). The LLM reads your question,
decides *which* engine tool to call (`sonar`/`judge`/`find_token`/`flow`) by emitting a JSON
action, reads the tool's observation, and loops until it can answer — then synthesizes a
sourced verdict. Because the loop is ours, MantleFi is a genuine agent that does not depend on
any external agent host. Set the key first:
```bash
cp .env.example .env        # then put your free key (https://build.nvidia.com) in NVIDIA_NIM_API_KEY
python3 research.py agent "Aave GHO の利回りは本物？それとも報酬頼み？"
```

#### How the agent stays honest (the accuracy guarantee)
The LLM **never originates a number or a verdict** — it only routes the question to the
deterministic engine and narrates what the engine returns. Two enforced layers:
1. **Fabrication guard** (`agent._untraceable_numbers`): every %, $ and ratio in the answer
   is matched (format-tolerant: `$28.7M` ↔ `28,700,000`) against the tool observations; any
   number with no source is flagged ⚠ in the output.
2. **Verbatim 根拠 block**: the raw engine output (with source URLs) is always appended, so the
   ground-truth numbers are visible no matter how the LLM phrased its summary.
On rate-limit/timeout it degrades to the gathered ground-truth + an honest "could not
conclude — no guess", and it refuses buy/sell/timing advice.

### Token finder (anti-impostor, address-verified)
`token <SYM>` searches ticker variants (`SYM`/`wSYM`/`SYMx`) across **both** GeckoTerminal
and DexScreener (each names the same token differently, and thin pools are indexed by only
one), keeps **only Mantle** pools, matches by **exact normalized symbol** (rejects impostors
like `SPCX69`), and prints each hit's **contract address** so you can verify identity. It
also lists what it *excluded* (wrong-chain / impostor) as proof it didn't grab garbage.
This mirrors the principle of Mantle's `address-registry-navigator` skill. Each hit is then
**cross-verified against on-chain reserves (Mantle RPC, no key — `rpc.py`)**: an aggregator's
liquidity is confirmed, or flagged stale if the pool holds ~0 on-chain. Rate-limit/network
failures are reported as *"could not check — retry"*, distinct from a genuine *"not found"*,
so the agent never fakes an absence.

---

## Run the live web demo — watch the agent investigate

```bash
cd mantlefi && python3 serve.py        # stdlib http.server, binds 127.0.0.1 only
# open http://127.0.0.1:8787  →  ask 「GHOは本物？」
```
The UI is bilingual — it opens in your browser's language (Japanese browsers → 日本語, everything
else → English), and the 🌐 button in the nav toggles JP/EN. Numbers and verdicts are engine-owned
and language-independent; only the words are translated (LLM narration is prompted in the chosen
language and passes the same fabrication guard).

The chat doesn't just answer — it **reveals each real step as it runs** (nothing is staged; every
line is an operation the engine actually performed):
```
🔍 Mantle の利回りデータを取得
📂 GHO ＝ Aaveの貸付プール と特定
🧮 利回りの内訳を確認：実需の金利 3.93% ＋ 配布報酬 2.07%
⛓ 利回りの実測をチェーンで確認 … ✅
⛓ トークンの正体をチェーンで確認 … ✅
⛓ 報酬トークンの中身をチェーンで確認 … ✅
→  verdict card (clickable Mantlescan receipts) + a plain one-line read
```
*(captured live 2026-07-03 — the numbers drift with the chain, the steps are the real trace)*
The instant verdict + numbers come from the deterministic engine (**no LLM, so they cannot be
fabricated**); the friendly sentence is one *guarded* LLM call. `serve.py` routes: `POST /facts`
(engine, no key), `POST /say` (one narration call), `GET /latest` (today's digest JSON), `GET /`
(the face). Public deploy / port-open is a step you run — it's localhost by default; a ready-made
free-tier recipe (Render Blueprint + PWA) is in [`DEPLOY.md`](DEPLOY.md).

## Daily multi-agent monitor — the crew

```bash
python3 monitor.py --verbose           # --dry to build + print without pushing/saving
```
One scheduled run **is the agent crew**: scan the pools (deterministic) → pick the notable ones
(top real-yield + anything that moved vs the last run, capped) → **investigator agents take those
pools one at a time (sequential — the free NIM lane is single-file), each running its own ReAct loop
to chain-verify one pool** → one editor agent merges them into a plain 「まとめ」. Every number stays
engine-owned (the editor's prose is **dropped** if it invents one); with no key it degrades to an
engine-only digest. The run is mirrored to `data/latest.json` and shown in the web **全体調査** tab.
That's the report's job the on-demand chat can't do: the whole-landscape view — every pool
classified, plus **what changed since last time** (you don't have to know what to ask). A saved run
is in [`reports/`](reports).

---

## Worked examples — verifiable research reports (committed artifacts)

`python3 research.py report <project> [symbol]` produces a timestamped, plain-language **Markdown**
report saved to [`examples/`](examples) — committed so a judge can read the deliverable without
running anything. The contrast is the on-chain `🔗` block (with clickable links), which a
generalist LLM cannot produce. Numbers are LIVE (stamped `as of`); the *classification* and the
*on-chain facts* are the point.

### 🟡 High APY — but the chain shows what the dashboard hides — Aave GHO · as of 2026-06-28  ([`examples/GHO.md`](examples/GHO.md))
```
判定: 🟡 報酬依存ぎみ（配布報酬が大きめ）
利回りは続く？: 🟡 〔年利 6.00% = 本体の金利 3.20%（続く） + 運営の配布報酬 2.80%（止まれば消える）〕
🔗 チェーンで直接確認:
  ✅ 利回りの実測: 本体の金利 3.20% はチェーンで実測。上乗せ報酬 約2.80% は実際の配布量から算出＝合計 約6.00%。
     （見出しの最大 6.0% は条件を満たした人だけの値で、平均はこれより低い）
  ✅ トークンの正体: 実在する本物のトークンで、名前も DefiLlama と一致。🔧 ただし運営が後から仕様を変更できる作り。
  ✅ 報酬トークンの中身: 受け取る報酬『aManGHO』も実在する本物のトークン。🔒 仕様は固定。
🔗 リンク: DefiLlama / Mantlescan 0xfc42…9e73
```
That day the aggregator headlined **≈9.2% APY** for this pool (3.2% base + a 6% reward *cap* —
the best-case figure). The agent re-measured the reward **on the chain itself: only ≈2.8% was
actually being distributed**, so the honest total was **≈6.0%** — and that reward slice dies if
the distribution stops (→ 3.2%). It also shows **what the reward is paid in** (aManGHO) and that
**the token is operator-upgradeable** — each with a clickable link to verify yourself. That block
is the part you cannot get by asking ChatGPT.

### 🟢 Quiet but real — Ondo USDY · as of 2026-06-28  ([`examples/USDY.md`](examples/USDY.md))
```
判定: 🟢 実利回り（金利・手数料など本物の収益）
利回りは続く？: 🟢 〔年利 3.55% = 本体 3.55% + 配布報酬 0%〕
利回りの原資は？続く？: 金利・手数料・米国債の利息など（配布報酬に頼らない） → 実需が払うので続きやすい
🔗 チェーンで直接確認:
  ✅ 配布報酬: 報酬トークンの配布なし＝配布報酬に頼っていない（チェーンで確認）。
  ✅ トークンの正体: 実在する本物のトークンで、名前も DefiLlama と一致。🔧 運営が後から仕様を変更できる作り。
```
**GHO's dashboard headline (≈9%) was more than double USDY's (3.5%) — but the chain said USDY's
yield survives even if every reward stops, while almost half of GHO's re-measured 6.0% does not.**
Higher headline ≠ better yield; the agent shows *why*, with receipts.

### 🔴 The volume doesn't add up — Fluxion USDT0-BSB · as of 2026-06-28  ([`examples/USDT0-BSB.md`](examples/USDT0-BSB.md))
```
判定: 🔴 出来高が少数に集中（実需は薄い可能性）
取引は自然？: 🔴 〔買い手 7人 / 売り手 12人、出来高÷預かり額 0.3〕   ← ~19 wallets churning
利回りは続く？: ⚫ 〔年利 0.03%〕                                       ← yield ≈ 0
```
A single "is the APY real?" check just shrugs — the APY is ~0. But the multi-axis agent reads the
chain and flags **the trading itself: the volume is concentrated in ~19 wallets (7 buyers + 12
sellers)** — a handful churning, not broad demand. The kind of "the numbers don't add up" that a
single-axis yield check misses entirely, and that only an on-chain read surfaces.

> 🔎 See also: [`examples/agent_walkthrough.md`](examples/agent_walkthrough.md) — a saved transcript
> of the self-hosted ReAct loop reasoning through the tools (so a judge can read the agent *thinking*
> without a key), and [`examples/correspondence.md`](examples/correspondence.md) — all 37 pools, each
> token resolved to its real Mantle address with a Mantlescan link (a verifiable, growing ledger).

> ℹ️ The blocks above quote the committed artifacts — dated snapshots (as of 2026-06-28). Yields
> *move*: GHO's reward distribution has since dried up, and a live run now classifies it 🟢 organic
> ≈3.9% (checked 2026-07-03). That is exactly the point — **don't trust any static list, including
> this one: the agent re-reads the chain on demand.** The on-chain facts (where the yield comes
> from, what the reward is paid in, proxy/owner) stay verifiable through the links. Honest
> backdrop: Mantle DeFi is quiet — MantleFi's value isn't "find the moon," it's **"find the real
> yield, see what's behind the high APYs, and verify it on-chain."**

---

## How it works

```
research.py (CLI: scan / judge / report / token / example / agent)
   ├─ data_sources.py   read-only fetchers: DefiLlama (yields incl. underlyingTokens/
   │                    rewardTokens, protocol tokens, fees), GeckoTerminal. Free, no key.
   ├─ rpc.py            Mantle RPC reader (stdlib, no key): balances, totalSupply, getCode,
   │                    symbol, EIP-1967 proxy slot, owner() — never-raise on revert
   ├─ onchain.py        the VERIFICATION layer: emission_check (reward-token reality vs
   │                    apyReward) + contract_check (code/symbol/proxy/owner). Abstains, never guesses.
   ├─ classify.py       6-class classifier + constant-price flow index (isolates quantity
   │                    from price) + abstention (returns ❔不明 on missing/sign-mixed data)
   ├─ report.py         one builder → text / Markdown / JSON: verdict + 5Q + 🔗 on-chain
   │                    verification + sources + limitations + timestamp (no buy/sell)
   ├─ tools.py          the engine exposed as agent TOOLS (one code path for CLI + agent)
   └─ agent.py + nim.py the self-hosted ReAct agent + NIM backend (stdlib urllib, no openai)

Same deterministic engine, three surfaces:
   research.py   CLI            scan / judge / report / token / agent
   serve.py      web backend    facts.py fast-path → /facts /say /latest /   (+ web/index.html)
   monitor.py    daily crew     sequential investigator agents + editor → digest → web 全体調査 tab
```

**The flow-vs-price trick** (`classify.constant_price_flow`): hold each token's price at
its latest value, then re-value the historical token *amounts*. The result moves only with
quantity (real deposits/withdrawals). Comparing it to the USD series exposes price illusions
(USD up, quantity flat) and inverse illusions (quantity up, USD down because price fell).

**Abstention by design:** when the data can't decide — e.g. Aave nets borrows as negative
token entries and breaks the flow index — MantleFi outputs "❔不明 (insufficient data)" with
the reason, never a guessed number.

## Limitations (stated, not hidden)
- Emission **end-date** isn't on free data (DefiLlama doesn't expose the incentives-controller
  address); MantleFi verifies emission **existence + reward-token identity + apyReward
  consistency** on-chain instead — reliable and equally on-thesis.
- Wash vs high-frequency market-maker can't be fully separated without per-trade wallets →
  reported as "wash 疑い". Wallet-level holder concentration is out of scope (no free no-key path).
- Single snapshot; daily-grid series means a one-off bridge can skew a 7d/30d delta.
- `apyBase` truthfulness (e.g. an RWA coupon) can't be independently audited on free data.
- On-chain checks need the underlying contract address (DefiLlama `underlyingTokens`); if
  absent, that check abstains rather than guesses.

## Reproduce it
1. `cd mantlefi && python3 research.py example` — reproduces the cases above on *current* data.
2. Numbers will drift (data is live); the *classifications* and the sourced reasoning hold.
3. Thresholds live in `config.py`; change them only deliberately (see `CLAUDE.md`).

## Files
Engine (no key): `config.py` · `data_sources.py` · `rpc.py` · `onchain.py` · `classify.py` ·
`report.py` · `tools.py` · `research.py`
Agent layer: `nim.py` · `agent.py` · `facts.py` (web fast-path) · `.env.example`
Surfaces: `serve.py` (web backend) · `web/index.html` (live face) · `monitor.py` (daily crew → web 全体調査 tab)
Tests (offline): `test_agent.py` (agent safety + fabrication guard) · `test_onchain.py` (on-chain verify + report)
Artifacts: [`examples/`](examples) — committed Markdown reports: USDY 🟢 / GHO 🟡 / sUSDe ⚫ /
USDT0 🟢 / **USDT0-BSB 🔴 wash** · [`agent_walkthrough.md`](examples/agent_walkthrough.md) (ReAct
transcript) · [`correspondence.md`](examples/correspondence.md) (all-37-pool address-verified table) ·
[`validation_worksheet.md`](examples/validation_worksheet.md) (blind accuracy test) · [`reports/`](reports) (saved daily digests)
Docs: `skills/mantlefi-research/SKILL.md` (Mantle AI Agent Skills format) · [`docs/harness.html`](docs/harness.html)
(architecture diagram) · `BRIEF.md` · `CLAUDE.md`
