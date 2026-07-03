---
name: mantlefi-research
description: >-
  Use when a user asks whether a Mantle DeFi pool or protocol is "real yield" or a
  "trap" — i.e. is an attractive APY/TVL backed by genuine capital, or is it
  emission-propped, a price-illusion, or wash-traded. Verifies the verdict against
  Mantle RPC itself (reward-token reality + contract truth), not just the dashboard.
  Judge-not-FOMO. Read-only, free data only, Mantle (chainid 5000) only. NEVER gives
  buy/sell advice.
---

# MantleFi — DeFi 真贋判定リサーチャー (judge-not-FOMO)

A research skill that classifies a Mantle DeFi target into one transparent class and
shows the sourced numbers behind it. It tells you whether a fishing spot is real or a
decoy. **It does not tell you to fish (no buy/sell/timing advice).**

## When to trigger
- "Is <Mantle pool/protocol> real yield or a trap?"
- "Why is <pool> paying X% APY?" / "Is this TVL real?"
- "What looks real vs fake in Mantle DeFi right now?" (use `scan`)

## Workflow (the research loop)
1. **Sonar (broad, cheap)** — `python research.py scan` lists all Mantle yield pools,
   each pre-classified. Use it to pick a few candidates worth a deep look.
2. **Basic safety (layer 1)** — before trusting a contract, check it is not an outright
   scam/honeypot (Mantle `address-registry-navigator` / `risk-evaluator` skills, or an
   explorer). This skill focuses on layer 2 below.
3. **Judge (layer 2, deep)** — `python research.py judge <project> [symbol]` runs the
   5-question interrogation across orthogonal axes:
   - **Yield axis** (DefiLlama `apyBase` vs `apyReward`): is the yield organic or printed?
   - **Flow axis** (DefiLlama `tokens` raw amount vs `tokensInUsd`): real capital or price?
   - **Wash axis** (GeckoTerminal vol/liquidity, tx-per-unique-wallet): organic or wash?
   - **🔗 On-chain verification** (Mantle RPC, no key — `onchain.py`): does the reward token
     actually exist on-chain (emission reality vs DefiLlama's *computed* `apyReward`)? is the
     underlying a real, identifiable contract (code size / on-chain symbol match / EIP-1967
     upgradeable-proxy / `owner()`)? This is the trust-minimized 二重確認 a generalist LLM can't
     do — aggregator says X, the chain says Y → agree = high confidence, diverge = flag.
4. **Adversarial** — the most cautionary axis wins the headline (a 🟢 yield can be
   overridden by a 🔴 wash flag, e.g. Fluxion BSB's fee yield looks 100% real (apyBase) but its
   volume is wash from a handful of wallets doing 100+ trades each).
5. **Report** — `python research.py report <project> [symbol]` emits a verifiable DD report
   (verdict + 5Q + 🔗 on-chain verification + sources + limitations + timestamp) as **Markdown
   + JSON** to `examples/`. Abstain ("❔不明") whenever free data can't decide.

## The 6 classes (thresholds in `config.py`, calibrated to live Mantle data)
| class | rule |
|---|---|
| 🟢 実利回り real yield | apyBase ≥ 50% of apy, from a real source (interest/fees/RWA coupon) |
| 🟡 報酬依存 reward-dependent | real base exists but apyReward share ≥ 40% |
| 🔴 emission罠 emission-only | apyBase < 0.5% and reward dominates (headline collapses if rewards stop) |
| 🔴 価格錯覚 price illusion | token-amount ~flat (|Δ|≤0.5%) while USD-TVL moves >3% (TVL ≠ deposits) |
| 🔴 wash/集中 wash | vol/liq ≥ 1.0, or tx/unique-wallet ≥ 50, or one-sided few-wallet flow |
| ⚪ 流出 outflow | token-amount Δ < −3% (capital leaving) |
| ⚫ 利回りゼロ zero-yield | apy = 0 — NOT a trap, just pays nothing (excluded from both lists) |

## Guardrails (hard rules — see CLAUDE.md)
- **Read-only, free data only.** No keys, no write tx, no paid APIs, no third-party
  package execution.
- **No recommendation.** Never say buy/sell/now. Surface the spot; the human decides.
- **Abstain, don't guess.** Missing/sign-mixed data (e.g. Aave borrow-netting breaks the
  flow index) ⇒ output "❔不明" with the reason, never a fabricated number.
- **Every number carries a source URL.**
- **Trap ≠ scam.** Most traps are legitimate-but-misleading. Describe the *source* of the
  yield/flow ("this 3.5% is 100% reward"), do not accuse a product of fraud.
- **Mantle-only, no cross-chain ranking, never frame Mantle as losing.** Speak at the
  pool level.

## Run it
```
python research.py scan                       # broad sonar over all Mantle yield pools (no key)
python research.py judge ondo-yield-assets USDY   # deep judge of one target + on-chain verify (no key)
python research.py report aave-v3 GHO          # write a plain-language research report (Markdown) to examples/ (no key)
python research.py token SPCX                  # find a token's Mantle pools by EXACT identity (no key)
python research.py example                     # live reference cases (no key)
python research.py agent "Aave GHO の 9% は本物？報酬頼み？"  # the SELF-HOSTED research agent (needs a free Groq or NIM key)
```

## Agent mode (self-hosted — this is the *agent*, not just a tool)
`agent "<free-text question>"` is a ReAct loop (`agent.py`) whose thinking runs on a free
LLM via `nim.py` (stdlib urllib, no `openai` package) — Groq by default, NVIDIA NIM as an
automatic fallback — so MantleFi is a
genuine agent that does **not** depend on an external host. The loop: the LLM reads the
question → emits a JSON action `{"action":"judge","args":{...}}` → the engine tool runs →
the observation feeds back → repeat (≤ N steps) → `final`. The model picks *which* tool to
call (`sonar`/`judge`/`find_token`/`flow`) and narrates the result.

**The LLM never originates a number or a verdict** — every class and every number comes only
from the deterministic engine. Two enforced safety layers: (1) a fabrication guard flags any
number in the answer not traceable to a tool observation; (2) the verbatim "根拠（engine 出力）"
block is always appended, so the user sees the real sourced numbers regardless of phrasing.
On rate-limit/timeout the loop degrades to the gathered ground-truth + an honest "could not
conclude — no guess" (never a fabricated answer). Needs `NVIDIA_NIM_API_KEY` in `mantlefi/.env`
(see `.env.example`); `scan/judge/token` keep working without any key.

`token` applies the principle of Mantle's `address-registry-navigator` locally: it searches
ticker variants (`SYM` / `wSYM` / `SYMx`) across BOTH GeckoTerminal and DexScreener (they
name the same token differently), keeps only Mantle pools, matches by **exact normalized
symbol** (so `SPCX69` is rejected when querying `SPCX`), and surfaces the **contract
address** of every hit so identity is verifiable — same display name can be different
contracts (e.g. `SPCXx` on Merchant Moe vs `wSPCXx` on Fluxion are distinct tokens).
Each hit is then **cross-checked against on-chain reserves via Mantle RPC** (independent,
no-key, `rpc.py`): if an aggregator claims liquidity but the pool holds ~0 on-chain, it is
flagged stale/wrong. Search failures (HTTP 429 / network) are reported as *"could not check,
retry"* — never silently as *"not found"* (a lookup failure is not an absence).
Engine: `data_sources.py` (read-only fetchers) → `classify.py` (6-class) → `rpc.py` +
`onchain.py` (on-chain verification: reward-token reality, contract truth) →
`report.py` (text/Markdown/JSON render) → `tools.py` (engine-as-agent-tools) → `research.py` (CLI).
Agent layer: `nim.py` (NIM chat, stdlib) + `agent.py` (ReAct loop + fabrication guard).
Stdlib only, no third-party packages. The on-chain checks implement the
`risk-evaluator` / `address-registry-navigator` principle natively (no MCP, no npx).
