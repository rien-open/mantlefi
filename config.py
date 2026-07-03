"""MantleFi — central config. All thresholds live here (no hardcoding in logic).

Thresholds are calibrated from the LIVE Mantle DeFi distribution observed 2026-06-20
(see BRIEF.md "勝てる根拠"). Change only via user approval (see CLAUDE.md).
"""

from pathlib import Path

# --- Chain ---
MANTLE_CHAIN_NAME = "Mantle"   # DefiLlama chain key
MANTLE_CHAIN_ID = 5000

# --- Free, no-key data endpoints (read-only). No paid tiers. ---
DEFILLAMA_YIELDS_POOLS = "https://yields.llama.fi/pools"
DEFILLAMA_PROTOCOL = "https://api.llama.fi/protocol/{slug}"
DEFILLAMA_FEES_CHAIN = "https://api.llama.fi/overview/fees/Mantle"
DEFILLAMA_DEXS_CHAIN = "https://api.llama.fi/overview/dexs/Mantle"
DEFILLAMA_STABLECOINCHART = "https://stablecoins.llama.fi/stablecoincharts/Mantle"
GECKO_MANTLE_POOLS = "https://api.geckoterminal.com/api/v2/networks/mantle/pools"
GECKO_TOKENS_MULTI = "https://api.geckoterminal.com/api/v2/networks/mantle/tokens/multi/{addrs}"
GECKO_SEARCH = "https://api.geckoterminal.com/api/v2/search/pools"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"

# Token finder
DUST_LIQUIDITY_USD = 1000          # liquidity below this = ⚫ effectively dead market
SMALL_TVL_USD = 250_000            # a pool below this is "thin": a retail-size position moves
                                   # it and exit is hard. A display flag only — NOT a class change.

# Verifiable links — every number clicks through to its public source (the "receipt").
# Public-contract addresses go in links in full (they ARE the verification target); wallet/owner
# addresses stay masked in prose (see onchain._mask).
DEFILLAMA_POOL_URL = "https://defillama.com/yields/pool/{id}"
MANTLESCAN_TOKEN_URL = "https://mantlescan.xyz/token/{addr}"
MANTLESCAN_ADDRESS_URL = "https://mantlescan.xyz/address/{addr}"
# Protocol directory — each pool's venue (DEX/protocol) official site, sourced from DefiLlama
# (never hand-typed: a wrong DeFi URL is a security hazard). url field comes from /protocols.
DEFILLAMA_PROTOCOLS = "https://api.llama.fi/protocols"
DEFILLAMA_PROTOCOL_PAGE = "https://defillama.com/protocol/{slug}"
DEFILLAMA_MANTLE_PAGE = "https://defillama.com/chain/Mantle"   # Mantle DeFi directory — always-available fallback link (describe/none + scan footer)

# Mantle RPC (free, no key) — independent on-chain ground-truth for cross-verification
MANTLE_RPC = "https://rpc.mantle.xyz"

# --- Aave V3 on Mantle: reconstruct the honest yield from PRIMARY sources (the on-chain agent's core) ---
# The canonical Aave V3 Pool. getReserveData(asset).currentLiquidityRate = the LIVE base supply APR
# every supplier earns (matches Aave's own "Protocol APY"). See aave.py.
AAVE_V3_POOL = "0x458F293454fE0d67EC0655f3672301301DD51422"
# Merkl (free, no key) — the ACTUAL distributor of Aave Mantle reward incentives. We compute the
# reward from its REAL distribution (dailyRewards ÷ supplied TVL = the blended rate a typical supplier
# sees), NOT its self-reported headline `apr` (the conditional max, which DefiLlama copies → overstates).
MERKL_OPPORTUNITIES = "https://api.merkl.xyz/v4/opportunities"
MERKL_CACHE_TTL = 300        # reward campaigns move slowly → 5-min in-process cache
AAVE_RESERVE_CACHE_TTL = 60  # base supply rate moves slowly → brief cache so repeated scans stay fast
# DefiLlama coins price API (free, no key) — to value the REAL pool size: gross supplied =
# aToken.totalSupply × price (chain truth), vs DefiLlama's lending tvlUsd which is NET (supplied−borrowed).
DEFILLAMA_COINS_PRICES = "https://coins.llama.fi/prices/current/"

HTTP_TIMEOUT = 30
HTTP_UA = "MantleFi-Research/0.1 (+read-only; Mantle Research Challenge Track2)"

# --- 6-class thresholds (BRIEF.md / 概念定義シート) ---
# Yield: real-yield vs reward-dependent vs emission-trap vs dead
REAL_YIELD_BASE_SHARE = 0.50      # 🟢 apyBase >= 50% of apy
REWARD_DEPENDENT_SHARE = 0.40     # 🟡 apyReward share >= 40% (but base exists)
EMISSION_TRAP_BASE_ABS = 0.50     # 🔴 apyBase < 0.5% (absolute APY pts) & reward dominant
DEAD_APY_EPS = 1e-9               # ⚫ apy == 0 => dead, not a trap (excluded)
MEANINGFUL_APY_PCT = 1.0          # ⚫ apy < 1% => "利回りゼロ相当": a real-but-trivial 0.1–0.8% yield
APY_VOLATILE_PCT = 25.0           # ⚠️ apy ≥ 25% => "変動が大きい": abnormally high (tiny pool whose
                                  # utilization spiked → transient chain rate). Flag the outlier, don't
                                  # dress a spike up as clean 🟢実利回り (ren案・judge-not-FOMO).
                                  # is not a "real yield" worth surfacing (it flooded the 🟢 list)

# Flow (constant-price index): real capital vs price illusion vs outflow
FLOW_INFLOW_PCT = 3.0             # token-amount Δ > +3% => real inflow
FLOW_OUTFLOW_PCT = -3.0           # token-amount Δ < -3% => outflow
PRICE_ILLUSION_FLOW_ABS = 0.5     # |token-amount Δ| <= 0.5% (units ~flat) ...
PRICE_ILLUSION_USD_PCT = 3.0      # ... while USD-TVL Δ > +3% => 🔴 price illusion
FLOW_WINDOWS_DAYS = (7, 30)       # report both

# Wash / concentration (GeckoTerminal)
WASH_VOL_LIQ = 1.0                # 🔴 24h volume / liquidity >= 1.0
WASH_TX_PER_WALLET = 50           # 🔴 tx / unique-wallet >= 50
WASH_LOPSIDED_MIN_UNIQUE = 3      # one-sided flow from <= this many unique wallets => flag

# --- Mantle DeFi protocol slugs (DefiLlama) for flow analysis ---
MANTLE_PROTOCOL_SLUGS = [
    "aave-v3",
    "merchant-moe-liquidity-book",
    "agni-finance",
    "fluxion-network",
    "ondo-yield-assets",
    "cian-yield-layer",
    "init-capital",
    "lendle-pooled-markets",
    "mantle-index-four-fund",
]

# DEX protocols only — their pool tokens trade on-chain, so wash/concentration analysis applies.
# Lending/RWA/aggregator protocols (aave-v3, ondo, cian, init, lendle, mi4) have NO DEX volume,
# so a GeckoTerminal "match" would be a DIFFERENT pool entirely → matching one is a FALSE wash
# flag (and a Mantle downgrade). Wash is N/A for them (judged 対象外). See tools.gather_judge.
MANTLE_DEX_SLUGS = {"merchant-moe-liquidity-book", "agni-finance", "fluxion-network"}

# Class labels (emoji + name) — output is a transparent classification, NOT scam/safe.
CLASS_REAL_YIELD = "🟢 実利回り (real yield)"
CLASS_REWARD_DEP = "🟡 報酬依存 (reward-dependent)"
CLASS_EMISSION_TRAP = "🔴 emission罠 (emission-only)"
CLASS_PRICE_ILLUSION = "🔴 価格錯覚 (price illusion)"
CLASS_WASH = "🔴 wash/集中 (wash or concentrated)"
CLASS_OUTFLOW = "⚪ 流出 (capital outflow)"
CLASS_DEAD = "⚫ 利回りゼロ (apy=0 — not a trap)"
CLASS_INACTIVE = "⚫ 無活動 (funded but no 24h trading)"
CLASS_REAL_INFLOW = "🟢 実流入 (real inflow)"
CLASS_FLAT = "⚪ 横ばい (flat)"
CLASS_UNKNOWN = "❔ 不明 (insufficient free data — abstain)"
CLASS_VOLATILE = "⚠️ 変動が大きい (abnormally high / volatile APY — likely a transient spike)"

# Plain-Japanese one-line read per class — the "friend" explanation a beginner gets at a glance.
# DERIVED from the engine's class (the LLM never writes this); shared by report.render and the
# agent chat badge so the human-facing read is one source, never drifts, never fabricated.
CLASS_PLAIN = {
    CLASS_REAL_YIELD:     "🟢 実利回り（金利・手数料が主体）",
    CLASS_REAL_INFLOW:    "🟢 資金が流入",
    CLASS_REWARD_DEP:     "🟡 報酬頼み（配布報酬が大きめ）",
    CLASS_EMISSION_TRAP:  "🔴 ほぼ報酬頼み（配布報酬が大半）",
    CLASS_PRICE_ILLUSION: "🔴 価格錯覚（値上がりで膨らんだだけ）",
    CLASS_WASH:           "🔴 出来高が少数に集中（実需は薄い可能性）",
    CLASS_OUTFLOW:        "⚪ 資金流出",
    CLASS_FLAT:           "⚪ 横ばい",
    CLASS_DEAD:           "⚫ 利回りゼロ",
    CLASS_INACTIVE:       "⚫ 取引なし",
    CLASS_UNKNOWN:        "❔ 判断材料が足りない",
    CLASS_VOLATILE:       "⚠️ 変動が大きい（高利回り・急変しやすい）",
}

# verdict/class -> vault badge color class (.jb-ok/-am/-rd/-de). COLOR ONLY — the verdict itself is
# engine-decided (classify.py); this just re-expresses it as a hue (no new verdict is introduced).
CLASS_JB = {
    CLASS_REAL_YIELD:     "jb-ok",
    CLASS_REAL_INFLOW:    "jb-ok",
    CLASS_REWARD_DEP:     "jb-am",
    CLASS_EMISSION_TRAP:  "jb-rd",
    CLASS_PRICE_ILLUSION: "jb-rd",
    CLASS_WASH:           "jb-rd",
    CLASS_DEAD:           "jb-de",
    CLASS_INACTIVE:       "jb-de",
    CLASS_OUTFLOW:        "jb-de",
    CLASS_FLAT:           "jb-de",
    CLASS_UNKNOWN:        "jb-de",
    CLASS_VOLATILE:       "jb-warn",
}

# --- NIM agent layer (ONLY part that needs a key; scan/judge/token work WITHOUT it) ---
# The LLM only routes the question to the deterministic engine tools and narrates their
# output — it never originates a number or a verdict (see agent.py). Backed by NVIDIA NIM
# (OpenAI-compatible REST) called via stdlib urllib so MantleFi stays zero-dependency.
MANTLEFI_DIR = Path(__file__).resolve().parent
MANTLEFI_ENV = MANTLEFI_DIR / ".env"        # optional KEY=VALUE file (gitignored)
NIM_ENV_KEY = "NVIDIA_NIM_API_KEY"          # env var name only — never a default value
NIM_BASE_URL = "https://integrate.api.nvidia.com"
NIM_CHAT_PATH = "/v1/chat/completions"
NIM_MODEL_PRIMARY = "deepseek-ai/deepseek-v4-pro"   # free + smart + fast (3.6-4.5s, burst 3/3 OK — verified 2026-07-03). glm-5.1 was removed from the free catalog (HTTP 410 Gone, 2026-07-03); deepseek's June instant-429 streak has cleared (re-verified before this swap)
NIM_MODEL_FALLBACK = "meta/llama-4-maverick-17b-128e-instruct"   # fast free fallback (nim.py also falls through on 404/410 = catalog rotation)
NIM_RPM_DELAY = 1.6     # 40 RPM free-tier cap → ≥1.5s spacing between calls
NIM_TIMEOUT = 90        # deepseek-v4-pro is slow on free tier (matches rinrin bench)
# The INTERACTIVE chat one-liner (/say → facts.narrate/describe) only rephrases engine facts, so it
# runs on the FAST model — NOT deepseek. From a datacenter IP (e.g. the Render deploy) deepseek-v4-pro
# hangs all the way to NIM_TIMEOUT before the fallback fires, which made every chat reply ~92s;
# maverick returns in ~2-5s and the fabrication guard protects accuracy regardless. The crew/monitor
# (MONITOR_*_MODEL) keeps deepseek for quality — this only affects the interactive one-liner.
CHAT_NARRATE_MODEL = NIM_MODEL_FALLBACK
NIM_MAX_STEPS = 6       # ReAct loop hard cap (bounds cost/rate-limit)
NIM_TEMPERATURE = 0.0   # deterministic routing
NIM_MAX_TOKENS = 800

# --- Groq backend (fast inference; OpenAI-compatible; mirrors the in-repo rinrin_bot setup) ---
# The interactive WEB agent runs its ReAct LOOP on Groq — the many serial tool-routing calls are the
# real latency bottleneck (NIM free tier ≈ 28s of LLM time / question; Groq ≈ 5-9s). The final
# user-facing sentence is then re-narrated on the NIM primary for phrasing quality (ren 2026-06-28
# hybrid: Groq速度 + NIM品質). Either way numbers/verdicts are engine-owned — the LLM never
# originates them, so a faster/lighter model cannot dent accuracy (no-fab; see agent.py).
GROQ_ENV_KEY = "GROQ_API_KEY"                       # env var NAME only — never a default value
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_CHAT_PATH = "/chat/completions"
GROQ_MODEL_PRIMARY = "llama-3.3-70b-versatile"      # fast + strong enough for routing + JP narration
GROQ_MODEL_FALLBACK = "openai/gpt-oss-120b"
GROQ_RPM_DELAY = 2.0                                # 30 RPM free-tier cap → ≥2s spacing

# Backend registry — nim.chat(backend="groq"|"nim") selects one. A missing key OR a full failure on a
# non-nim backend transparently falls back to NIM, so the surface degrades, never breaks.
LLM_BACKENDS = {
    "nim":  {"base_url": NIM_BASE_URL,  "chat_path": NIM_CHAT_PATH,  "env_key": NIM_ENV_KEY,
             "primary": NIM_MODEL_PRIMARY,  "fallback": NIM_MODEL_FALLBACK,  "rpm_delay": NIM_RPM_DELAY},
    "groq": {"base_url": GROQ_BASE_URL, "chat_path": GROQ_CHAT_PATH, "env_key": GROQ_ENV_KEY,
             "primary": GROQ_MODEL_PRIMARY, "fallback": GROQ_MODEL_FALLBACK, "rpm_delay": GROQ_RPM_DELAY},
}
LLM_DEFAULT_BACKEND = "nim"        # unchanged default for every existing caller (monitor/telegram/CLI)
WEB_AGENT_LOOP_BACKEND = "groq"    # the interactive /ask loop → fast
WEB_AGENT_FINAL_BACKEND = "nim"    # the one user-facing sentence → NIM primary quality (set to "groq" for all-Groq)

# --- Telegram surface (optional 面; /scan /judge /token need NO key, free-text uses agent) ---
TELEGRAM_ENV_KEY = "TELEGRAM_BOT_TOKEN"          # from @BotFather; in mantlefi/.env
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MSG_LIMIT = 4000                        # under Telegram's 4096 hard cap (margin)
TELEGRAM_POLL_TIMEOUT = 30                       # long-poll seconds
TELEGRAM_HISTORY_TURNS = 5      # conversation memory: keep last N (question, answer) per chat
TELEGRAM_HISTORY_ANS_CAP = 1000 # chars of each remembered answer kept as context
# Telegram is the DAILY-DIGEST surface only (the digest comes to you each morning); conversational
# Q&A lives on ONE consistent surface — the web chat. Set this to the deployed web URL at deploy time
# (外向き=ren); empty → the redirect message just says "web のチャット" without a link.
WEB_CHAT_URL = ""

# The heartbeat monitor pushes PROACTIVELY (a push is not a reply → it needs a destination).
# The bot records every chat_id that messages it here; monitor.py reads this to know where to send.
KNOWN_CHATS_PATH = MANTLEFI_DIR / "data" / "known_chats.json"
ALERT_CHAT_ENV_KEY = "MANTLEFI_ALERT_CHAT_ID"   # optional .env pin; else monitor pushes to all known chats

# --- Heartbeat monitor (定点観測 agent crew; run by cron, see monitor.py) ---
# Crew: N maverick "investigator" agents fan out over the notable pools (each runs its own
# ReAct loop, chain-verifying one pool), then ONE glm-5.1 "editor" merges them into a daily
# digest. The engine still owns every number/verdict (no-fab); the LLMs route and narrate.
MONITOR_STATE_PATH = MANTLEFI_DIR / "data" / "monitor_snapshot.json"   # last run's per-pool classes
MONITOR_HISTORY_PATH = MANTLEFI_DIR / "data" / "monitor_history.jsonl"  # one line per run (audit)
MONITOR_REPORT_DIR = MANTLEFI_DIR / "reports"                           # saved daily digests (artifact)
MONITOR_LATEST_PATH = MANTLEFI_DIR / "data" / "latest.json"            # newest digest as JSON for the web face (serve.py GET /latest)
MONITOR_TOP_N = 4                # real-yield pools to surface & verify each run (sizeable ones)
MONITOR_TOP_DEX = 2              # sizeable DEX pools to also surface (Fluxion 等・厚み＋wash確認・年利は * 推定)
MONITOR_MAX_INVESTIGATE = 6      # HARD cap on investigator agents/run (bounds cost; no empty spin)
MONITOR_MIN_TVL_USD = 250_000    # 拾う最小規模: 極小 DEX の出来高ノイズは「変化」に出さない（Fluxion 等は * 注意書きつきで表示）
MONITOR_APY_MOVE_PT = 2.0        # |Δapy| ≥ 2pt vs last snapshot ⇒ notable change
MONITOR_TVL_MOVE_PCT = 25.0      # |Δtvl| ≥ 25% vs last snapshot ⇒ notable change
MONITOR_INVESTIGATOR_MODEL = NIM_MODEL_PRIMARY    # NIM primary everywhere (モデル一本化 · ren 2026-06-27): the fallback stays only as nim.chat's auto-fallback. Background job → a slightly slower run is fine, and facts are engine-derived so the model only changes phrasing, not numbers.
MONITOR_EDITOR_MODEL = NIM_MODEL_PRIMARY          # NIM primary: smart, for the single digest synthesis

# --- Web face backend (serve.py — the chat-first 顔's small HTTP API) ---
# stdlib http.server. ONLY /ask needs a key (agent→NIM); / (static face), /health, /latest are
# keyless. Binds to localhost by default (NOT public) — public binding/port is a deploy step the
# user performs (plan §10「外向き操作は ren 実行」). Override host/port via MANTLEFI_SERVE_HOST/PORT.
SERVE_HOST = "127.0.0.1"          # localhost only by default (the VPS deploy sets 0.0.0.0 via env)
SERVE_PORT = 8787
SERVE_WEB_DIR = MANTLEFI_DIR / "web"   # serve.py serves web/index.html at "/" (same-origin demo)
SERVE_RATE_MAX = 20               # per-IP request cap …
SERVE_RATE_WINDOW = 60            # … per this many seconds (sliding window; free-tier safety)
SERVE_CACHE_TTL = 90              # in-process scan cache (s) so concurrent /ask don't each re-pull
SERVE_MAX_Q_CHARS = 500           # clamp a question's length before it reaches the agent
SERVE_MAX_BODY = 1 << 16          # 64KB POST-body cap (a question is tiny; larger = malformed/abuse)
SERVE_CREW_COOLDOWN = 10          # min seconds between live /run-crew (SSE) starts — the crew makes many free-tier LLM calls
