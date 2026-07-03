"""MantleFi — the classifier. Maps a target to one of the transparent 6 classes
using ONLY fetched numbers. When data is missing/ambiguous it returns ❔不明 (abstain)
rather than guessing. Every result carries the numbers and source URLs it used.

Three orthogonal analyses (a target can be looked at by more than one):
  - classify_yield(pool)      : real-yield vs reward-dependent vs emission-trap vs dead
  - classify_flow(slug)       : real capital vs price-illusion vs outflow (tokens vs USD)
  - classify_wash(gecko_pool) : organic vs wash/concentrated
"""
from __future__ import annotations

import config
import data_sources as ds


def _result(target, klass, numbers, sources, persist, limitations, note=""):
    return {
        "target": target,
        "class": klass,
        "numbers": numbers,
        "sources": sources,
        "persist_condition": persist,   # "持続するには何が真であるべきか"
        "limitations": limitations,
        "note": note,
    }


# ---------------------------------------------------------------- yield
def classify_yield(pool: dict, source_url: str) -> dict:
    """Classify a single DefiLlama yield-pool row by where its yield comes from."""
    name = f"{pool.get('project')} {pool.get('symbol')}"
    apy = pool.get("apy") or 0.0
    tvl = pool.get("tvlUsd") or 0.0
    # None (DefiLlama did NOT report the split) ≠ 0.0 (reported as zero). Conflating them
    # silently mislabels a pool: a null apyBase reads as an emission trap, a null apyReward
    # reads as clean real yield. So: both-null => abstain (can't tell real from emission);
    # one-null => solve from the identity apy = apyBase + apyReward (determined, not guessed)
    # and disclose it; neither-null => use as reported.
    base_raw, reward_raw = pool.get("apyBase"), pool.get("apyReward")

    if apy < config.MEANINGFUL_APY_PCT:
        nums = {"apy": apy, "apyBase": base_raw, "apyReward": reward_raw, "tvlUsd": tvl,
                "apyPct7D": pool.get("apyPct7D")}
        return _result(name, config.CLASS_DEAD, nums, [source_url],
                       "—（年利1%未満は利回りゼロ相当。危険という意味ではなく、両リストから除外）",
                       "年利が1%未満＝実質的に利回りなし。0.1〜0.8%のような微利回りは実利回りとして扱わない。")

    # ⚠️ abnormally high APY = 変動が大きい: an outlier well above any sustainable yield (e.g. a tiny
    # Aave pool whose utilization spiked to ~100%, so the instantaneous chain rate is a transient
    # number far above the aggregator's smoothed value). We do NOT dress a spike up as clean 🟢実利回り
    # — the tool flags the outlier itself (judge-not-FOMO). Numbers stay engine-real; the verdict warns.
    if apy >= config.APY_VOLATILE_PCT:
        nums = {"apy": apy, "apyBase": base_raw, "apyReward": reward_raw, "tvlUsd": tvl,
                "apyPct7D": pool.get("apyPct7D")}
        # Measured caution, NOT alarm: a high APY on a small pool isn't "異常", but it swings a lot and
        # may not last — flag the risk without fear-mongering (ren: 過剰に不安を煽らない).
        note = "年利 {:.1f}% と高め。小さな池は変動が大きく、続くとは限りません（要注意）。".format(apy)
        return _result(name, config.CLASS_VOLATILE, nums, [source_url],
                       "高い利用率が続くこと（小さな池は金利が動きやすい）",
                       "利回りは本物でも急変しやすく、続くとは限らない。",
                       note=note)

    if base_raw is None and reward_raw is None:
        nums = {"apy": apy, "apyBase": None, "apyReward": None, "tvlUsd": tvl,
                "apyPct7D": pool.get("apyPct7D")}
        return _result(name, config.CLASS_UNKNOWN, nums, [source_url],
                       "実需の金利と配布報酬の内訳が取れること（無いと実利回りか報酬頼みか判断できない）。",
                       "DefiLlama が実需の金利・配布報酬の内訳を両方とも出していない＝判断を保留（推測しない）。")

    inferred = []
    if base_raw is None:
        reward = reward_raw or 0.0
        base = max(0.0, apy - reward)
        inferred.append(f"実需の金利が欠損→年利−配布報酬={base:.2f}% で計算")
    elif reward_raw is None:
        base = base_raw or 0.0
        reward = max(0.0, apy - base)
        inferred.append(f"配布報酬が欠損→年利−実需の金利={reward:.2f}% で計算")
    else:
        base, reward = base_raw, reward_raw

    nums = {"apy": apy, "apyBase": base, "apyReward": reward, "tvlUsd": tvl,
            "apyPct7D": pool.get("apyPct7D")}
    if inferred:
        nums["inferred"] = inferred

    base_share = base / apy if apy else 0.0
    reward_share = reward / apy if apy else 0.0
    nums["base_share"] = round(base_share, 4)
    nums["reward_share"] = round(reward_share, 4)

    # 🔴 emission-only: base is negligible in absolute terms and reward dominates
    if base < config.EMISSION_TRAP_BASE_ABS and reward > base:
        return _result(
            name, config.CLASS_EMISSION_TRAP, nums, [source_url],
            "ほぼ運営の配布報酬（実需の金利はわずか）",
            "配布報酬がいつまで続くかは不明",
            note=f"見かけ {apy:.2f}% のうち {reward_share*100:.0f}% が配布報酬。")

    # 🟢 clean real yield: base dominant AND reward not a big part of headline
    if base_share >= config.REAL_YIELD_BASE_SHARE and reward_share < config.REWARD_DEPENDENT_SHARE:
        return _result(
            name, config.CLASS_REAL_YIELD, nums, [source_url],
            "金利・手数料・米国債の利息など（実需の金利が主体）",
            "実需の金利の出どころは無料データでは独立に確認できない",
            note=f"利回りの {base_share*100:.0f}% を実需の金利（金利・手数料）が占めています。")

    # 🟡 reward-dependent headline: real base exists but reward is a big slice
    if base >= config.EMISSION_TRAP_BASE_ABS and reward_share >= config.REWARD_DEPENDENT_SHARE:
        return _result(
            name, config.CLASS_REWARD_DEP, nums, [source_url],
            "借入金利・手数料＋運営の配布報酬",
            "配布報酬がいつまで続くかは不明",
            note=f"実需の金利 {base:.2f}% はあるが、見かけの {reward_share*100:.0f}% は配布報酬ぶん。")

    # fallback: low base share, some base — treat as reward-dependent (honest caution)
    return _result(
        name, config.CLASS_REWARD_DEP, nums, [source_url],
        "実需の金利は薄め、多くが運営の配布報酬",
        "配布報酬の継続が要確認",
        note="実需の金利が薄く、配布報酬に寄っている。")


# ---------------------------------------------------------------- flow (tokens vs USD)
def _by_date(series: list[dict]) -> list[tuple[int, dict]]:
    out = []
    for e in series:
        try:
            d = int(e.get("date"))
        except (TypeError, ValueError):
            continue
        out.append((d, e.get("tokens", {}) if "tokens" in e else e))
    out.sort(key=lambda x: x[0])
    return out


def _closest(entries: list[tuple[int, dict]], target_ts: int):
    return min(entries, key=lambda x: abs(x[0] - target_ts)) if entries else None


def constant_price_flow(series: dict, window_days: int):
    """Constant-price index: isolate quantity (flow) from price.

    Returns dict with tokenAmount_delta_pct, tokenUsd_delta_pct, verdict, or abstain.
    """
    tok = _by_date(series["tokens"])
    usd = _by_date(series["tokensInUsd"])
    if len(tok) < 2 or len(usd) < 2:
        return {"verdict": config.CLASS_UNKNOWN, "reason": "データ期間が短すぎる"}

    latest_ts, tok_latest = tok[-1]
    _, usd_latest = usd[-1]

    # latest prices per symbol (USD per token unit)
    price = {}
    for sym, amt in tok_latest.items():
        if amt is None:
            continue
        if amt < 0:
            return {"verdict": config.CLASS_UNKNOWN,
                    "reason": "Aaveは預金と借入が相殺された数しか公開されず、純粋な入出金を量で測れない＝金額が増えたか減ったかの向きだけ参考"}
        u = usd_latest.get(sym)
        if amt > 0 and u is not None:
            price[sym] = u / amt
    if not price:
        return {"verdict": config.CLASS_UNKNOWN, "reason": "価格を出せるトークンが無い"}

    def flow_value(entry_tokens):
        return sum(entry_tokens.get(s, 0) * p for s, p in price.items()
                   if isinstance(entry_tokens.get(s, 0), (int, float)))

    def usd_value(entry_usd):
        return sum(v for v in entry_usd.values() if isinstance(v, (int, float)))

    target_ts = latest_ts - window_days * 86400
    past_tok = _closest(tok, target_ts)
    past_usd = _closest(usd, target_ts)
    if not past_tok or not past_usd:
        return {"verdict": config.CLASS_UNKNOWN, "reason": "過去の比較データが無い"}

    f_now, f_past = flow_value(tok_latest), flow_value(past_tok[1])
    u_now, u_past = usd_value(usd_latest), usd_value(past_usd[1])
    if f_past == 0 or u_past == 0:
        return {"verdict": config.CLASS_UNKNOWN, "reason": "比較の基準値がゼロ"}

    flow_d = (f_now - f_past) / f_past * 100
    usd_d = (u_now - u_past) / u_past * 100

    if abs(flow_d) <= config.PRICE_ILLUSION_FLOW_ABS and usd_d > config.PRICE_ILLUSION_USD_PCT:
        verdict = config.CLASS_PRICE_ILLUSION
    elif flow_d > config.FLOW_INFLOW_PCT:
        verdict = config.CLASS_REAL_INFLOW
    elif flow_d < config.FLOW_OUTFLOW_PCT:
        verdict = config.CLASS_OUTFLOW
    else:
        verdict = config.CLASS_FLAT

    return {"verdict": verdict, "window_days": window_days,
            "tokenAmount_delta_pct": round(flow_d, 2), "tokenUsd_delta_pct": round(usd_d, 2),
            "usd_now": round(u_now, 0)}


def classify_flow(slug: str) -> dict:
    try:
        series, url = ds.protocol_mantle_series(slug)
    except ds.FetchError as e:
        return _result(slug, config.CLASS_UNKNOWN, {"error": str(e)}, [], "—",
                       "データの取得に失敗→判断を保留（推測しない）。")
    windows = [constant_price_flow(series, w) for w in config.FLOW_WINDOWS_DAYS]
    # headline = most informative verdict across windows (not just the longest window),
    # so a price-illusion in ANY window surfaces instead of being hidden by a 'flat' window.
    priority = [config.CLASS_PRICE_ILLUSION, config.CLASS_OUTFLOW,
                config.CLASS_REAL_INFLOW, config.CLASS_FLAT]
    headline = next((w for v in priority for w in windows if w.get("verdict") == v),
                    windows[-1])
    nums = {f"{w.get('window_days','?')}d": {
                "flow%": w.get("tokenAmount_delta_pct"),
                "usd%": w.get("tokenUsd_delta_pct"),
                "verdict": w.get("verdict"),
                "reason": w.get("reason")} for w in windows}
    nums["headline"] = {"window_days": headline.get("window_days"),
                        "flow%": headline.get("tokenAmount_delta_pct"),
                        "usd%": headline.get("tokenUsd_delta_pct")}
    persist = ("預けた量が金額と乖離せず増えること。"
               "金額だけ上がって量が横ばい＝値上がりの錯覚で、新規の入金は無い。")
    verdict = headline.get("verdict")
    # when flow is UNMEASURABLE (Aave nets deposits vs borrows, stable ≈ $1) we already OMIT the
    # 資金の動き question — so don't surface its "can't measure" reason as a limitation either (ren).
    limit = "" if verdict == config.CLASS_UNKNOWN else (
        headline.get("reason", "") or "日次データのスナップショットなので、単発の大きな入出金で数字が振れることがある。")
    return _result(series.get("name", slug), verdict, nums, [url], persist, limit)


# ---------------------------------------------------------------- wash / concentration
def classify_wash(gecko_pool: dict, source_url: str) -> dict:
    a = gecko_pool.get("attributes", {})
    name = a.get("name", "?")
    try:
        reserve = float(a.get("reserve_in_usd") or 0)
        vol = float((a.get("volume_usd") or {}).get("h24") or 0)
        tx = a.get("transactions", {}).get("h24", {})
        buys = int(tx.get("buys") or 0); sells = int(tx.get("sells") or 0)
        buyers = int(tx.get("buyers") or 0); sellers = int(tx.get("sellers") or 0)
    except (TypeError, ValueError):
        return _result(name, config.CLASS_UNKNOWN, {}, [source_url], "—",
                       "データの一部が欠けている → 判断を保留。")

    vol_liq = vol / reserve if reserve else None
    total_tx = buys + sells
    unique = buyers + sellers
    tx_per_wallet = total_tx / unique if unique else None
    nums = {"liquidity_usd": reserve, "volume_24h": vol,
            "vol/liq": round(vol_liq, 2) if vol_liq is not None else None,
            "buys": buys, "sells": sells, "buyers": buyers, "sellers": sellers,
            "tx/unique_wallet": round(tx_per_wallet, 1) if tx_per_wallet is not None else None}

    if vol == 0 or total_tx == 0:
        return _result(name, config.CLASS_INACTIVE, nums, [source_url],
                       "出来高が立つこと（資金はあるが24時間の取引がゼロ）。",
                       "24時間のスナップショット。取引なしは危険ではないが、活発でもない。",
                       note=f"預かり額 ${reserve:,.0f} はあるが、24時間の出来高$0・取引0＝お金はあるが使われていない")

    flags = []
    if vol_liq is not None and vol_liq >= config.WASH_VOL_LIQ:
        flags.append(f"出来高÷預かり額 {vol_liq:.2f}（{config.WASH_VOL_LIQ}以上＝回しすぎ）")
    if tx_per_wallet is not None and tx_per_wallet >= config.WASH_TX_PER_WALLET:
        flags.append(f"1人あたり取引 {tx_per_wallet:.0f}回（{config.WASH_TX_PER_WALLET}回以上＝多すぎ）")
    lopsided = ((buyers and buyers <= config.WASH_LOPSIDED_MIN_UNIQUE and buys > sells * 3) or
                (sellers and sellers <= config.WASH_LOPSIDED_MIN_UNIQUE and sells > buys * 3))
    if lopsided:
        flags.append("少人数が一方向に売買を流している")

    if flags:
        return _result(name, config.CLASS_WASH, nums, [source_url],
                       "少人数の行き来でなく、広い参加者の自然な出来高であること。",
                       "一人ひとりの取引は公開されていないため、活発な値付けと取引の偏りを完全には区別できない（あくまで参考）。",
                       note="; ".join(flags))
    return _result(name, "🟢 取引は自然そう", nums, [source_url],
                   "—", "24時間のスナップショット・一人ひとりの取引は非公開、という注意あり。",
                   note=f"出来高÷預かり額 {vol_liq:.2f}、買い手 {buyers}/売り手 {sellers}＝広め。")
