# Deploy MantleFi — anyone can host this

MantleFi is a single Python-stdlib process (`serve.py`) + one static HTML file. No pip installs,
no database, no build step. Anything that can run `python3 serve.py` can host it.

## Option 1 — Render (free, ~5 minutes)

1. Fork / push this repo to your GitHub.
2. [render.com](https://render.com) → **New → Blueprint** → select the repo.
   Render reads [`render.yaml`](render.yaml) and creates the free web service automatically.
3. When prompted for environment variables, paste your free keys:

   | var | required? | where to get it | what stops without it |
   |---|---|---|---|
   | `NVIDIA_NIM_API_KEY` | recommended | free at [build.nvidia.com](https://build.nvidia.com) | the friendly one-liner + the survey crew narration (engine verdicts/numbers still work — they are LLM-free) |
   | `GROQ_API_KEY` | optional | free at [console.groq.com](https://console.groq.com) | chat falls back to NIM (slower loop) |

4. Open `https://<your-service>.onrender.com`. Done — verdicts, receipts, JP/EN toggle,
   and the 📊 Full Survey all work. On a phone, "Add to Home Screen" installs it as an app (PWA).

### Speed note (important for any hosted deploy)
The smart model (`deepseek-v4-pro`) answers in ~4s locally but **hangs to the request timeout from
datacenter IPs** (Render, most VPSes), which made the 📊 Full Survey take ~10 minutes. `render.yaml`
sets `MANTLEFI_NIM_PRIMARY=meta/llama-4-maverick-17b-128e-instruct` so the deploy uses the fast model
directly — same engine-owned numbers/verdicts, ~10× faster. If you host elsewhere, set that env var
too. Leave it unset locally to keep deepseek where it actually responds.

### Free-tier notes
- Render's free plan sleeps after ~15 min without traffic; the next visitor waits ~30–60 s while
  it wakes. To keep it warm, ping it from any machine you already run cron on:
  ```
  */10 * * * * curl -fsS -m 20 -o /dev/null https://<your-service>.onrender.com/health
  ```
- The filesystem is ephemeral: `data/latest.json` (the survey snapshot) resets to the committed
  seed on each deploy. Pressing **🔄 調査を実行 / Run survey** regenerates it live on-chain.

## Option 2 — any VPS / your own machine

```bash
git clone <this repo> && cd mantlefi
cp .env.example .env        # paste the free key(s)
MANTLEFI_SERVE_HOST=0.0.0.0 python3 serve.py    # binds 127.0.0.1 unless you opt in
```
Put it behind any TLS reverse proxy (Caddy/nginx/Cloudflare Tunnel) and set
`MANTLEFI_TRUST_XFF=1` so per-visitor rate-limiting sees real client IPs.

## Security posture (what a host is exposing)

- **Read-only research tool**: no wallets, no signing, no user accounts, no cookies, no user data
  stored. The only secrets are your own free LLM keys (env vars — never in code or the repo).
- Per-IP rate limiting (`SERVE_RATE_MAX`/`SERVE_RATE_WINDOW` in `config.py`), 64 KB body cap,
  path-traversal-safe static serving, and a single-flight lock on the survey (concurrent clicks
  converge on the one shared run instead of stacking LLM calls).
- All numbers/verdicts come from the deterministic engine; LLM output passes a fabrication guard.
