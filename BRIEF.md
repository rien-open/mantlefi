# MantleFi — Brief

> A4 1 枚で**完成品の姿**を書く。「計画」ではなく「**何を作るか**」。
> Mantle Research Challenge Track 2（research agent）への提出物。

## 何を作るか
(1 文で)

Mantle DeFi の「本物の利回り/資金」か「罠（合法だが誤解を生む）」かを、**無料の一次オンチェーンデータ**で判定し、**出典つきで透明に分類提示する**リサーチエージェント（judge-not-FOMO）。釣り場は教える、釣る（タイミング）のは人間＝**売買推奨はしない**。

**形態＝自前の思考ループを持つ self-hosted agent**：自然文の質問→無料 LLM が engine ツール（sonar/judge/find_token/flow）を選んで呼ぶ ReAct ループ（`agent.py`+`nim.py`、stdlib urllib・`openai` 不使用）。頭脳は **Groq を主**（gpt-oss-120b／llama-3.3-70b）・**NVIDIA NIM を自動フォールバック**。**LLM は数字も判定も創作せず**、決定論 engine の出力を route して説明するだけ（捏造ガード＋根拠ブロックで担保）。外部 agent host に依存しない。`scan/judge/token` は鍵なしで動作、`agent` は Groq/NIM いずれかの無料鍵で動く。

## 戦略 (1 つだけ)
(核心ロジックを 3 行で。複数戦略の並走は NG)

1. **観測**: Mantle DeFi のプール/プロトコルを無料データで走査（ステーブル枚数フロー・`tokens`生数量・apyBase/apyReward・手数料・出来高・tx/wallet）
2. **分類**: 対象に問診（このプールに当てはまる項目のみ。出どころと持続は1問に統合）を当て、**6クラス**【🟢実利回り / 🟡報酬依存 / 🔴emission罠 / 🔴価格錯覚 / 🔴wash / ⚪流出 / ⚫利回りゼロ】に分ける（閾値は下記＝6/20実測較正）
3. **🔗 オンチェーン検証（汎用LLM不可の堀）**: aggregator 数値を鵜呑みにせず Mantle RPC で直接検証（`onchain.py`）＝報酬トークンの実在(emission実体 vs DefiLlama計算値 apyReward)＋ underlying コントラクト真贋(code/symbol一致/EIP-1967 proxy/owner)。一致=高信頼/乖離=flag の二重確認。`risk-evaluator`/`address-registry` 原理をネイティブ実装(npx不要)
4. **提示**: 調査レポート（判定＋チェック項目＋🔗チェーンで直接確認＋根拠数値＋出典＋timestamp＋「続く条件」＋注意点）を平易な日本語の text/Markdown で出力（`research.py report`→`examples/`）。反証を当てて生存したものだけ🟢

## 勝てる根拠
- データ: Mantle DeFi 全 37 利回りプール + 主要 8 プロトコル、LIVE 2026-06-20、全数値 DefiLlama/GeckoTerminal 由来
- 結果: 既知の対を無料データだけで正しく分離できることを実証
  - 🟢 **Ondo USDY**: apyBase 100%(3.55%)・報酬0・$28.7M・フロー≈USD＝実資本の実利回り（emission 停止でも生存）
  - 🟡 **Aave GHO**: 見かけ 9% だが base 3%＋報酬 6%＝66%が配布報酬。チェーンが「報酬は aManGHO で配布・原資産 GHO は upgradeable proxy」まで開示＝ダッシュボードに無い裏側
  - 🔴 **MI4**: 枚数 0%（不変）なのに USD プラス＝価格錯覚 ／ 🔴 **Fluxion BSB**: 数〜十数 wallet が高頻度往復(tx/wallet≫50)＝wash
  - ※ $/% の具体値は日次で変動する。固定する事実は「base 0%／枚数 0%／100%報酬」等の構造であり、数値は実行時のスナップショット
- 注: これは「分類器の妥当性」の実証。**売買 PnL は測らない（研究 tool であり trade bot でない）**

## 閾値（6/20 実測較正）
- 🟢実利回りgate: `apyBase ≥ apy×0.5` かつ実源（借入金利/手数料/RWAクーポン）かつ フロー枚数Δ ≥ -3%
- 🟡報酬依存: 実base有るが `apyReward share ≥ 40%`
- 🔴emission罠: `apyBase < 0.5%` で報酬が大半 ／ 🔴価格錯覚: `枚数Δ ≤ 0.5%` かつ `USD-TVL Δ > +3%`
- 🔴wash: `vol/liq ≥ 1.0` or `tx/unique-wallet ≥ 50` or 片側少数wallet ／ ⚪流出: `枚数Δ < -3%` ／ ⚫利回りゼロ: `apy = 0`（罠でない・除外）

## 提出物 / 成果物
- [x] SKILL.md（Mantle 形式）＋ Python スクリプト（sonar / classifier / judge / report）
- [x] 自前 ReAct agent（`agent.py`+`nim.py`、Groq 主／NIM フォールバック・捏造ガード＋根拠ブロック）
- [x] offline 安全テスト（`test_agent.py` 29/29＝捏造ガード/parser/dispatch/finalize）
- [x] 他人が辿れる step-by-step walkthrough（README）
- [x] 動く実例 1 つ（USDY🟢 / GHO🟡 を実走、全数値に出典＋リンク）
- [ ] X 投稿（@Mantle_Official タグ）＋ 提出フォーム

## スケジュール
- Day 1: BRIEF + CLAUDE + api_samples（取得済）
- Day 2-4: エンジン（sonar + 6クラス分類 + 5問 + 反証 + 棄権/捏造ガード）
- Day 5-6: live example 実走 + walkthrough
- Day 7+: (任意) 2枚目（銘柄カード）/ 面（TG or Web）/ X 投稿

## 完成品の定義 (Definition of Done)
- [ ] 既知対(USDY🟢/GHO🟡)を agent が正しく分類＋根拠数値が API 実値と一致
- [ ] 盲検: 他 Mantle プール 10 件で 人手分類 vs agent 分類 ≥ 8/10 一致
- [ ] 各🟢判定に反証（1 wallet / wash / 価格錯覚 / base 真贋）を当てて生存
- [ ] 全数値に出典 URL、未取得は「不明」と明示（**捏造ゼロ**）
- [ ] ドキュメント（BRIEF/README/SKILL.md）とコードが一致

## やらないこと (最重要)
- ❌ 価格予測・売買推奨・タイミング指示（釣り場提示のみ）
- ❌ 自動売買 / 鍵を扱う / write tx（**read-only のみ**）
- ❌ 母艦で未検証の第三者パッケージを npx 実行（mantle-mcp 等。supply-chain）
- ❌ 有料 API 依存（DefiLlama Pro / RWA.xyz Enterprise / Blockworks / Token Terminal）
- ❌ 全網羅・秒単位ティック追跡（on-demand fresh で十分）
- ❌ legit 商品を「スキャム」と呼ぶ（出所の透明化のみ）
- ❌ chain 縮小を見出しにして Mantle を下げる（pool レベルで中立に語る）
- ❌ 横断チェーン比較で Mantle を順位づけ（Mantle 特化を維持）
- ❌ 2 戦略以上の並走（カードは共有ソナー核 + 重み付け差のみ）

## リスク管理（研究 tool 版＝資金リスク無し）
- 資金: read-only・無料データのみ ⇒ position size / daily loss / SL は **N/A**
- データ整合性が "リスク"の本体 ⇒ 下記 Kill Switch（捏造ガード/棄権）で担保
- コスト: API 課金 $0、rate limit は「じょうご」（広く安く探知→深掘りは数件）で吸収

## 想定リスク (research 固有)
1. **無料データの盲点**: emission の**終了日**は無料で不可（DefiLlama が incentives controller アドレス非開示）→ 代わりに報酬トークンの**実在＋identity＋apyReward整合**を on-chain 検証（同等に堅い）／Aave V3 は borrow netting で枚数フロー算出不能（USD 方向のみ信頼）／wallet単位の保有者集中度は対象外（無料no-key経路なし）→ 判定不能は「不明」明示で対処
2. **単一スナップショットの偶発**: 日次グリッドで単発 bridge が 7d/30d を歪める → 複数窓 + 出典明記で緩和
3. **wash vs 正当 MM の識別限界**: unique buyers/sellers は proxy、per-trade wallet 不可視 → 「wash 疑い（要 on-chain trace）」と留保付きで提示
