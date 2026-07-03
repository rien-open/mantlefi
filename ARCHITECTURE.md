# MantleFi — システム概要と設計ノート

> ユーザー向けの導入・使い方は [`README.md`](README.md) に、開発ルールは [`CLAUDE.md`](CLAUDE.md) に、
> 完成品の定義は [`BRIEF.md`](BRIEF.md) にある。本書は **中身（システム構成）と設計判断の記録** をまとめる。
> 対象時点: 2026-06〜07。

---

## 1. これは何か

**MantleFi は Mantle DeFi の利回りを調べるリサーチエージェント**である。「今 Mantle で利回りが良いのは？」
と聞くと、年利・内訳（本体の金利／配布報酬）・規模（TVL）・**利回りの出どころ**を平易な日本語で返し、
深掘りを頼めばチェーンを直接読んで裏を取る。

中核の価値は **judge-not-FOMO**（あおらず、出どころを透明にする）。
「本物か偽物か」を断定せず、**利回りの出どころと持続条件を事実として示す**ことで、誰も下げず・嘘をつかず・
FOMO だけを殺す。

### 絶対に守ること（製品のアイデンティティ）
- **read-only**（書き込み tx・秘密鍵を一切扱わない）
- **無料データのみ**（Pro/Enterprise/有料 API を使わない）
- **no-fabrication**：数値と判定は決定論エンジンだけが確定し、LLM は言い換えるだけ（捏造ガードで担保）
- **Mantle も他プールも下げない**：「罠」「詐欺」等の決めつけをしない。罠 ≠ スキャム
- **売買助言をしない**（分類と根拠を出すだけ）

---

## 2. 全体像 — 3 層 ＋ 面

```
 データ源（無料）        エンジン（決定論・LLM不使用）      頭脳（LLM）              面（配信）
 ─────────────────      ────────────────────────────      ──────────────────      ──────────────
 DefiLlama（背骨）        classify.py   6クラス分類          nim.py   無料NIM         serve.py    Web API
 GeckoTerminal           aave.py       チェーン直読み再計算  agent.py ReActループ    web/index   3タブの顔
 DexScreener             onchain.py    トークン実在等の確認  facts.py 2フェーズ会話   monitor.py  監視クルー
 Mantle RPC              rpc.py        eth_call/ERC20 補助
 Merkl（Aave実配布）      report.py     レポート生成・出典
                         tools.py      エンジンを道具化
```

**設計の芯は層の分離**：数字と判定は必ずエンジン（決定論）から出る。LLM は「何を調べるか」を選び、
エンジンの出力を会話調に言い換えるだけ。これが「金融で幻覚しない」堀であり、no-fabrication の実体。

---

## 3. データ源（すべて無料・鍵不要 or 無料鍵）

| 源 | 役割 | 備考 |
|---|---|---|
| **DefiLlama** `yields.llama.fi/pools` | 背骨。プール一覧と年利/内訳/TVL | Mantle に「公式の利回り一覧」は無いので、事実上の集約先を発見層に使う |
| **GeckoTerminal** | wash 検出（出来高・ユニーク wallet）・トークン画像 | DEX プールのみ対象 |
| **DexScreener** | トークン検索の裏取り（偽物排除） | find_token 用 |
| **Mantle RPC** `rpc.mantle.xyz` | チェーン直読み（Aave 金利・トークン実在・アップグレード型か） | no-key |
| **Merkl** | Aave の配布報酬の**実配布額** | DefiLlama の見出し報酬でなく実額 |

---

## 4. エンジン層（決定論・LLM 不使用）

数値と判定はすべてここで確定する。

- **`classify.py`** — 6 クラス分類器（🟢実利回り / 🟡報酬頼み / 🔴ほぼ報酬頼み / 🔴wash / ⚫利回りゼロ / ❔不明）。
  `apyReward or 0.0` は使わず、欠損(None)と停止(0)を厳格に分離する。
- **`aave.py`** — Aave プールの**チェーン直読み再計算**。`getReserveData` の `currentLiquidityRate` を本体金利に、
  Merkl 実配布を報酬に、aToken 総供給×価格を規模（供給総額）にして、DefiLlama の見出し値を出し直す。
  **これが製品の一番強い部分**（集計サイトが Merkl 見出しをそのまま載せて過大になるのを、チェーンで正す）。
- **`onchain.py`** — 非数値の確認：報酬トークン/原資産が実在するか（getCode）、名前が一致するか、
  アップグレード型か（proxy）、所有者。数値そのものは再計算しない。
- **`rpc.py`** — eth_call / ERC20（symbol/decimals/total_supply）/ proxy 検出。**例外を投げない**（取得失敗は None）。
- **`report.py`** — レポートの単一の真実源（テキスト/Markdown）。Mantlescan 等の出典リンク生成。
- **`tools.py`** — エンジンを「道具」に（gather_judge / render_scan / tool_*）。str を返し、例外を loop に投げない。

### 利回りの「出どころ」は稼ぎ方で 3 つに分かれる（重要）
- **貸付の金利（Aave）** → チェーンから直読みで再計算できる。集計サイトに勝てる唯一の所。
- **DEX の手数料（Fluxion 等）** → 年利 = 24h 出来高 × 手数料率 ÷ TVL の**推定**。1 回で返る値が無く、
  無料 RPC で再現不能。**DefiLlama が唯一の実務的な出どころ**で、提供元サイトと差が出る。→ 画面では `*` を付す。
- **オフチェーンの利息（Ondo USDY 等）** → クーポンがオフチェーンで決まる。DefiLlama 由来だが**安定**で乖離しない。

---

## 5. 頭脳層（LLM・自作 ReAct ループ）

- **`nim.py`** — 無料 NIM（OpenAI 互換 REST を stdlib urllib で直叩き。`openai` パッケージ不使用＝zero-dep）。
  主モデル `deepseek-v4-pro`、フォールバック `maverick`（nim.py はカタログ変動＝404/410 でも自動切替）。1.6s spacing・429 backoff。鍵は `.env` の `NVIDIA_NIM_API_KEY`。
- **`agent.py`** — ReAct ループ（thought→action→observation を最大 N step）。
  **no-fab の 2 重防止**：① 捏造ガード（回答内の数値が観測 corpus に追跡できるか照合し、未追跡は落とす）
  ② 根拠ブロック（エンジン出力を逐語 append）。判定バッジは engine の CLASS をやさしい日本語に翻訳（`_PLAIN`）。
- **`facts.py`** — チャットの本体（2 フェーズ）。
  `/facts` は **LLM 不使用**で判定＋数値＋Mantlescan 領収書を約 5 秒で返す（決定論ルーター）。
  `/say` がその後 **LLM 1 回**でやさしい一言。`daily_data()` は全プールの表示用スナップショット（要旨・表の単一ソース）。

鍵がなくても `scan / judge / report` は動く（決定論）。鍵が要るのは会話（`/say`・monitor の言い換え）だけ。

---

## 6. 面（配信）

- **`serve.py`**（stdlib http.server・zero-dep）
  - `POST /facts` エンジンのみ即答 ／ `POST /say` LLM 1 回の一言 ／ `POST /ask` フル ReAct（フォールバック）
  - `GET /latest` 監視クルーの結果（要旨＋表＋クルーを同梱）／ `GET /daily` 全プールのライブ集計 ／ `GET /health`
  - `GET /run-crew` クルーをライブ起動（SSE で実況）
  - CORS・per-IP レート制限・グローバル lock 1 つ（無料 NIM は一本道）・本文上限。
  - 既定 `127.0.0.1`（公開は `MANTLEFI_SERVE_HOST=0.0.0.0`・**外向き操作は人間が実行**）。
- **`web/index.html`** — 静的 3 タブ（💬チャット／📊全体調査／📖説明）。npm 不使用・素の JS。
- **`monitor.py`** — 定点観測クルー（下記 §7）。結果は Web の 📊全体調査タブに反映。

---

## 7. 監視クルー（`monitor.py`）の流れ

```
scan 37プール（決定論）
  → watchlist を抽出（上限つき）
       ・大型の real-yield（Aave/Ondo が背骨）
       ・前回スナップショットから変化したもの（news・最小規模以上のみ）
       ・規模の大きい DEX プール（厚み＋wash 確認・枠を確保）
  → investigator エージェント × N（逐次・無料 NIM は一本道）が 1 プールずつチェーン検証
  → editor エージェント × 1 が 1 つの digest に統合
  → /latest（Web の📊全体調査タブ）＋ reports/（成果物）＋ snapshot 保存
```

- **「4 体」は逐次**（並列でない）。無料 NIM が 40rpm の一本道なので並列にしても速くならない。
  複数エージェントの価値は「速さ」でなく「1 プールごとに文脈を分けた検証＋まとめ役の統合」。
- **比較の基準**：スナップショットに時刻を持たせ、変化に「前回チェック（M/D HH:MM）比」を表示する。
  **毎日の cron だけが基準を進める**（`advance_baseline=True`）。ライブのボタンは進めない（`False`）ので、
  何回押しても比較は「今朝の定点チェックから」で一貫する。初回（履歴なし）は比較なし＝正直。

---

## 8. Web（3 タブ）の描画

- **💬 チャット** — `/facts`（即エンジンカード）→ `/say`（一言を後追い）の 2 フェーズ。
  判定は engine のバッジ＋年利/本体/配布報酬＋Mantlescan 領収書。冒頭の作例は置かない（入力欄のみ）。
- **📊 全体調査** — 要旨（全体像）・全件スキャン表・クルーの深掘りを**すべて同じ run（`/latest`）から**描く。タップで個別をチャットへ。
  走らせるまでは空（まっさら）。毎日の cron が朝の結果を用意するので、開けば「今朝の定点」が一貫した日付で出る。
  DEX の年利には `*`（24h 出来高ベースの推定・提供元と差が出る）。
- **📖 説明** — チャット（1 体の ReAct）と 全体調査（クルー）の仕組み図、普通の bot との違い。
- **🌐 JP/EN** — ブラウザ言語で自動選択＋nav の 🌐 で切替（localStorage 永続）。数値・判定はエンジン由来で言語非依存、決定論テキストは web 内の対訳マップ、LLM の言い換えだけ `lang` をサーバーへ渡して当該言語で生成（捏造ガード不変）。

---

## 9. 不変条件（必守）

read-only ／ 無料データのみ ／ **no-fabrication**（数値・判定は engine・LLM は言い換えのみ）／
罠 ≠ scam ／ **Mantle も他プールも下げない** ／ wallet・contract は `0xXXXX…YYYY` でマスク ／
.env の中身を出さない ／ zero-dep（stdlib のみ）／ **母艦で npx しない**（公式 MCP は隔離環境で）／
bot は二重起動しない（ps → 本体 PID kill → 確認）／ **外向き操作（公開・deploy・port 公開・X 投稿・VPS cron）は人間が実行** ／
閾値・分類・5 問・判定ラベル・データ源の変更は要確認。

---

## 10. 動かし方

```bash
# 決定論 CLI（鍵不要）
python3 research.py scan            # 利回り一覧（規模順・出どころつき）
python3 research.py judge <symbol>  # 1 プールの深掘り（チェーン確認つき）
python3 research.py report <symbol> # 保存用レポート（Markdown）

# 会話・監視（.env に NVIDIA_NIM_API_KEY が要る）
python3 serve.py                    # http://127.0.0.1:8787（チャット＋全体調査）
python3 monitor.py --dry --verbose  # 監視クルーを 1 回（送信・保存なし・実況）
python3 monitor.py                  # 本番（Web の全体調査に反映＋保存＋スナップショット更新）

# テスト（オフライン・ライブに触れない）
python3 test_agent.py
python3 test_onchain.py
```

---

## 11. 主要な設計判断（作業履歴の要約）

過去の探索（forensic 調査員 / Reconciler 三角測量 / 監査役 / 予測市場…）は over-engineering として畳み、
「平易な言葉で利回りの出どころを答える友達」にフラット化した。以下は現在のシステムに効いている決定。

- **製品の芯 = Aave のチェーン直読み再計算**。DefiLlama が Merkl の見出し報酬をそのまま載せて過大になるのを、
  チェーン本体金利＋Merkl 実配布で出し直す（例：GHO/USDT0/USDC）。
- **出どころで分ける**。数字がチェーン由来なのは Aave だけ。非 Aave は「トークン実在の確認」であって
  「数字の裏取り」ではない、と言葉で分離（✅ が数字自体を保証するように見せない）。
- **DEX の `*` 注意書き**。Fluxion 等の年利は 24h 出来高ベースの推定で提供元と差が出るため、
  DEX プールにだけ `*` と脚注を付す。Ondo（安定クーポン）には付けない。
- **クルーの選抜**。Mantle の池は小さく大型 real-yield は Aave/Ondo に偏る。厚みは DEX が担うので、
  背骨（大型 real）＋変化（news）＋大型 DEX を混ぜ、DEX が押し出されないよう枠を確保する。
- **比較の基準は毎日の定点チェック**。ライブのボタンは基準を進めない（§7）。
- **全体調査は 1 つの run に統合**。要旨・表・クルーを `/latest` から出し、走らせるまでは空（焼き込みの
  即描画をやめた）。
- **no-fab を日本語で機能させる修正**。捏造ガードの単語境界が Unicode で日本語を誤判定していたのを ASCII 境界に直し、
  全経路に効かせた。

Mantle DeFi の利回り分析は originality の天井が低い（DefiLlama が内訳を既出＋池が小さく暴けるズレが少ない）と
何度も確認した。だから勝ち筋は originality でなく、**完成度・動く証拠・出どころの誠実さ**に置く。

---

## 12. ファイル早見表

| ファイル | 役割 |
|---|---|
| `config.py` | 定数（閾値・URL・モデル名・クラスラベル）。ハードコード禁止でここ経由 |
| `data_sources.py` | DefiLlama / GeckoTerminal / DexScreener / directory / images の取得 |
| `classify.py` | 6 クラス分類（決定論） |
| `aave.py` | Aave のチェーン直読み再計算（本体金利＋Merkl 実配布＋供給総額） |
| `onchain.py` | トークン実在・アップグレード型か等の非数値確認 |
| `rpc.py` | eth_call / ERC20 補助（never-raise） |
| `report.py` | レポート生成・出典リンク（単一の真実源） |
| `tools.py` | エンジンを道具化（judge / scan / find_token …） |
| `nim.py` | 無料 NIM チャットクライアント（stdlib） |
| `agent.py` | ReAct ループ＋捏造ガード＋やさしい翻訳 |
| `facts.py` | 2 フェーズ会話（/facts・/say・describe）＋ daily_data（全プール） |
| `monitor.py` | 定点観測クルー（scan → watchlist → investigate → edit → /latest） |
| `serve.py` | stdlib HTTP サーバ（/facts /say /latest /run-crew /health） |
| `web/index.html` | 静的 3 タブの顔（チャット / 全体調査 / 説明） |
| `research.py` | 決定論 CLI（scan / judge / report / token） |
| `test_agent.py` / `test_onchain.py` | オフラインテスト（ライブに触れない） |
| `examples/` | 検証済みレポート・全プール対応表・盲検シート |
| `docs/` | 仕組み図（harness）・データ源メモ |
