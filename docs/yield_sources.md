# 利回り確認チェックリスト — どの数字をどこで確かめるか

> Mantle DeFi の利回りを「毎度ブレずに」読むための、出所の固定表。
> これは **エンジン（`aave.py` / `onchain.py` / `rpc.py`）が実際にやっている手順そのもの**＝コードと1対1。
> 人手でも、コードでも、同じ場所を見れば同じ数字になる。スナップショット＝2026-06-28 JST。

---

## 大原則：利回りは3つに分解する

アグリゲーター（DefiLlama）が見せる「年利 ◯%」という1つの数字を鵜呑みにしない。利回りは必ず

1. **本体の金利**（プールに預けて誰でも得る金利）
2. **上乗せ報酬**（インセンティブ＝止まれば消える）
3. **規模 (TVL)**（どれだけ資金が入っているか）

の3つに分けて、**それぞれ別の場所**で確かめる。

アグリゲーターは特に②を**「見出しの最大値」で水増しする**ことがある（条件を満たした一部だけが得る最大率を、全員が得るかのように表示）。今回の実例：DefiLlama は USDT0 を 3.82% と表示するが、典型的な預け手が実際に受け取る上乗せは **0.87%**。

---

## 確認表（構成要素 → 出所 → 勝つルール）

| 構成要素 | ①一次ソース（オンチェーン＝主役） | ②公式UI（突き合わせ） | ③アグリゲーター（補助） | 食い違ったらどれが勝つか |
|---|---|---|---|---|
| **本体の金利**（Aave供給） | `Pool.getReserveData(asset)` の `currentLiquidityRate`（RAY・selector `0x35ea6a75`・word[2]）→ 秒複利でAPY化 | Aave UI の "Protocol APY" | DefiLlama `apyBase` | チェーン=公式UI なら信頼。DefiLlamaだけ高い＝**報酬を本体に混ぜている**疑い→チェーンを採る |
| **上乗せ報酬**（Aave/Merkl） | Merkl API `dailyRewards × 365 ÷ tvl` ＝ **実配布(blended)** | Aave UI の "a◯◯ +Y%" インセンティブ行 | DefiLlama `apyReward`（＝Merklの `apr` 見出しのコピー） | **見出し(`apr`)でなく blended を採る。** looping等の条件付きは率を畳んだ上で「条件付き」と注記 |
| **規模 (TVL)** | `aToken.totalSupply() × 価格` ＝ **供給総額(gross)** | Aave UI の "Total supplied" | DefiLlama `tvlUsd`（＝net＝供給−借入） | gross＝「供給総額」、net＝「預入」と**ラベルで正体を明示**（同じ"TVL"でも別物） |
| **価格** | DefiLlama coins API `coins.llama.fi/prices/current/mantle:<addr>` | — | — | 取れなければ TVL は net のまま（**捏造しない**） |
| **報酬トークンの健全性** | チェーン `getCode`(実在) / `symbol` / `totalSupply` | — | — | 報酬トークン自体が薄い／停止＝「報酬を現金化できない」 |
| **流動性（DEXプール）** | — | — | GeckoTerminal `reserve_in_usd` | depth が薄い＝出口でスリッページ |
| **非Aaveの報酬** | プロトコル個別（自前トークン排出） | プロトコルのdApp | DefiLlama `apyReward` のみ | チェーン自動補正の**対象外＝「未検証」と明示**（推測で埋めない） |

---

## なぜ blended（実配布）が正で、見出し(apr)が誤か

Merkl の `apr` フィールドは**条件を満たした預け手だけが得る最大率**。例：USDT0 は「ステーブル負債を持たずに供給した分」だけが対象で、供給TVLの約23%しか条件を満たさない。残り77%は上乗せゼロ。

- 見出し(apr) ＝ 条件クリア組の最大率（DefiLlama がそのままコピー＝水増し）
- 実配布(blended) ＝ `その日のばらまき総額 × 365 ÷ プール全体のTVL` ＝ **平均的な預け手が実際に受け取る率**

blended は Aave 公式UIの "a◯◯ +Y%" 行と一致する（USDT0：blended 0.87% ＝ Aave UI "aWMNT +0.93%"）。だから **公式の再現でなく、公式と同じ実額を一次データから組み直している**＝オンチェーンエージェントの値打ちはここ。

---

## 今のMantleの実態（2026-06-28スナップショット）

**上乗せ報酬は全部Aave・全部Merkl経由。** 非Aaveのインセンティブ・キャンペーンは現状ゼロ（Merchant Moe / Agni / Init / Fluxion 等に LIVE キャンペーン無し）。Merkl LIVE は7本＝Aave供給4本＋借入リベート3本。

供給4本すべてで「見出し ＞ 実配布」：

| プール | 見出し(apr) | 実配布(blended) | 水増し | 種別 | エンジン補正 |
|---|---|---|---|---|---|
| USDT0 | 3.82% | 0.87% | 4.4倍 | 最大値表示 | ✅ blended採用 |
| USDC | 5.21% | 2.01% | 2.6倍 | 最大値表示 | ✅ blended採用 |
| GHO | 6.00% | 2.80% | 2.1倍 | 最大値表示 | ✅ blended採用 |
| sUSDe+USDe | 3.75% | 2.98% | 1.3倍 | **looping条件付き** | ✅ blended採用＋条件注記 |

借入側3本（Borrow GHO/USDC/USDT0）＝借り手へのリベートであって供給利回りでない→**供給側の解析からは正しく除外**。

**唯一の非Aave報酬＝Clearpool USDT**（TVL $12.5K・apy 12.30%・報酬1.27%＝自前トークン `0x0c89…79d8` の排出）。Aave以外はチェーン自動補正の対象外＝この行は「報酬はDefiLlama値・チェーン未検証」と扱う。規模が極小なので実害は小さいが、隠さず明示する。

---

## 休眠プールのスキップ（RPC節約・誤読でない）

DefiLlama が apy < 1%（`MEANINGFUL_APY_PCT`）と報告するプールは、エンジンが**チェーンを引かずにスキップ**する（死んだプールに毎回RPCを撃たないため）。現状 SYRUPUSDT / FBTC / WMNT の3本が該当。

- これは「読み失敗」ではない。手で `getReserveData` を撃つと**3本とも正常に返り `currentLiquidityRate = 0` ＝本当に供給金利0%**（借り手がいない休眠プール）。チェーンと DefiLlama がここでは一致する。
- ⚠️ 注目：**SYRUPUSDT は TVL $90M もあるのに金利0%**＝大きいが眠っているだけ（罠ではない）。「TVLが大きい＝稼げる」ではない好例。
- Aave の場合 DefiLlama の `apyBase` はチェーンの `getReserveData` と同じ出所なので、「DefiLlamaが0%と言うのにチェーンでは稼いでいる」取りこぼしは起きにくい（フィルタは安全）。

## 棄権ルール（読めない時に捏造しない）

- **チェーンが読めない**（RPCエラー・revert・word不足） → DefiLlama の行をそのまま使う（補正しない・推測しない）。`correct_pool` は `rpc.RpcError` を捕まえて未補正の行を返す。
- **価格が取れない** → TVL は net（預入）のまま。gross に膨らませない。
- **報酬トークンが解決できない** → 報酬の健全性チェックは「不明」と出す。

---

## 普段のリサーチ手順（このチェックリストの使い方）

1. DefiLlama で対象プールの `apy / apyBase / apyReward / tvlUsd` を取る（出発点・鵜呑みにしない）。
2. **本体の金利**をチェーン（`getReserveData`）で引き直す。Aave公式UIの "Protocol APY" と一致するか。
3. **上乗せ報酬**を Merkl の `dailyRewards×365÷tvl`（blended）で計算。DefiLlama の `apyReward`（＝見出し）と差があれば blended を採る。looping等の条件付きか確認。
4. **規模**を `aToken.totalSupply×価格`（gross＝供給総額）で出す。DefiLlama の `tvlUsd`（net＝預入）と別物としてラベルする。
5. 報酬トークンの実在・流動性をチェーンで確認（薄ければ「現金化できない報酬」）。
6. 読めない要素は「不明」。推測で埋めない。

→ この6手順は `aave.correct_pool()` ＋ `onchain.audit()` が自動でやっている。手で追う時も同じ場所を見れば同じ数字になる。
