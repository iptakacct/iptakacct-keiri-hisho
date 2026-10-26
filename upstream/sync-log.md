# upstream 同期ログ

取り込み履歴と、汎用化できずに見送った変更の記録。新しいものを上に。

## 2026-10-26 upstream-sync（317de9b..3c46b56b、約200コミット。対象パスの変更90ファイル）

**取り込み**
- 共通ルール：`automation.md`（無人claude -p・--chrome・タスクスケジューラーの型・排他ロック・ローテーションするリフレッシュトークン・定期commitのパス限定）／`communication.md`（横罫線禁止・下書きのクリップボード・答えやすい確認の聞き方・質問前に資金の動きを調べる・結果だけ返す・報告範囲・人名を推測しない）／`data-import.md`（マイナス精算書・「重複」と言う前の7項目）／`monthly-closing-checklist.md`（項目12〜18と3節を追加。既存1〜11の番号は不変）／`output-format.md`（全体通知を少なく重ねない・要確認一覧の列・リマインドにURL・MFはmf-api-post読み取り＋MCP登録・freee自動登録ルールの即時作成）／`security.md`（共有リポジトリへのPRの例外）
- 新規ルール：`journal-entry-quality.md`・`source-materials.md`・`keiei-summary.md`・`mcp-connection.md`（529行→約80行に圧縮）・`client-connection-setup.md`・`monthly-onboarding.md`（後2つは上流でも本文の大半がconsulting-kit側にあり、「常に守ること」のみ）。`CLAUDE.md`の参照先に追記
- 新規スキル `mf-api-post`：**読み取り専用**で移植（認可・ヘルスチェック・read・show-journal・find-journals・open-items・画面手順書）。これに合わせ `mf-fee-post`は「読み取り＝mf-api-post、登録＝MCP」、`mf-monthly-check`は取得をreadに変更し、経路違いの二重計上・繰越のずれ・損益の推移・variance_exempt_accountsを追加
- `freee-api-post`：監視（freee_monitor）・預金利息の源泉振替（interest_withholding）・給与の確定リマインド（payroll_check）・給与の未対応検知（payroll_todo、検知と直接投稿のみ）・タイムアウトとトークン更新ロック。`freee-monthly-check`：variance_exempt_accounts・手作業追記セクションの保持
- `invox-api`：apply/update/delete-invoice・export-journal・prepare-journals・yayoi_journal（部門→会社はaccounts.jsonで設定）、トークン更新ロック
- hooks・scripts：`guard.py`の構造整理（壊れた入力で止めない）、`weekly-template-review.sh`の二重起動防止・候補なしは投稿しない・メンションIDの読み込み不具合修正
- `テンプレート/`：CLAUDE.md・accounting-rulesのMF節更新、新規ひな形12件（tax-profile〈縮約版〉・税務調査対策メモ・設立書類の確認メモ・経理のデータの流れ・source/README・依頼文の文例集・自動化の範囲・引き継ぎチェックリスト・月次レポート・決算整理・経営サマリー・経理の流れ.html）
- スキル：`setup`（税賠保険・設立書類の読み取り・期限届出のヒアリング・MFはmf-api-post）・`new-instance`・`daily-log`（削減時間の記録）・`daily-schedule`・`knowledge-team`

- Codexレビューで上流にもある不具合を直した（上流への還元候補）：invox `export-journal`が上限到達でも成功扱い→終了コード2／`prepare-journals`が部門不明・貸借不一致でも一部の会社分を書き出す→何も書き出さない／freeeのトークン更新ロックが見つからないと黙ってロックなしで続ける→起動時に失敗／給与チェックの明細・経費申請が1ページ目しか取らない→全ページ取得／freee-interest・freee-monitorに二重起動防止を追加／`mf_monthly_check_cli`が繰越のずれ用の前期末・当期首BSを渡しておらずチェックが動かない→引き継ぐ

**見送り**
- `long-task-notify.py`のしきい値5分→10分：オーナー判断で5分のまま
- 仕訳ガード一式（hooks/journal_guard 等）と、それに依存する mf-api-post の書き込み系・自動登録・夜間処理：ガード無しで書き込みを入れるのは安全側でないため
- 上流の運用基盤（general_spool・run_lock・各種収集/ダイジェストスクリプト）：こちらはWebhook直接投稿・インラインのmkdirロックで同等にした
- 上流専用・個人運用のルール4件（tasks・vendor-notices・scan-sort・meeting-notes）、consulting-kit専用のテンプレート5件、給与PDF保存・給与入力案（スマレジ連携）、両月次チェックの「税務の節」（tax_checks.py依存）
- 特定クライアント・特定銀行の記録、特定リポジトリの許可リスト追加、新規スキル群（tax-deadlines・tax-onboarding・tax-planning・income-tax-prep・cashflow・consulting-kit・bpo-morning・smaregi-timecard）
- leak-scanの短い固有語がコード中の英単語（「スキップした」の過去分詞）に誤反応するため、freee-api-postの結果キーを`excluded`に改名（上流との差分になる）

**保留（オーナー確認）**
- mf-api-post に書き込みと仕訳ガードを入れるか（入れるならガードの汎用版を先に設計）
- 口座連携の無人チェック（bank-link-check／daily、`claude -p --chrome`をbypassPermissionsで動かす）を入れるか
- 月次チェックの税務の節（tax_checks.py・.claude/tax-rules/）を移植するか
- setup 0-a 税賠保険の期限の具体値（上流の値は特定団体の制度の可能性があり一般的な注意に弱めた）
- tax-profile.md を tax-deadlines 無しの縮約メモとして置くことでよいか
- payroll_check／payroll_todo のスケジューラー用ラッパー（未整備）、給与機能に要る人事労務APIの権限（setup-guideは会計のみ）
- communication.md のクリップボード手順がWindows前提（macOS並行運用時の書き分け）

## 2026-08-31 追加（先行反映）

- `freee-monthly-check`：増減チェックのバグ修正を上流から先行取り込み（freee `/reports/trial_pl` の `closing_balance` は期首からの累計→当月発生額 `closing - opening` ベースに修正、テスト2件追加、SKILL.mdに制約を記載）。`last_synced_commit`は据え置き（次回の通常syncで差分は適用済みとして流れる）

## 2026-08-31 upstream-sync（5833693..317de9b、23コミット。うち対象パスの変更は20）

**取り込み（24ファイル中22）**
- 共通ルール：`data-import.md`（新規、データ取り込みの二重取り込み防止）／`monthly-closing-checklist.md`（項目8注記：補助科目の付け忘れ、項目9注記：出金申請型決済先、項目11：取り込み単位の二重）／`security.md`（共有リポジトリの読み取り専用参照）／`communication.md`（デスクトップ通知は既定オフ）
- `CLAUDE.md`：「テンプレート・共通への還流」節、参照先にdata-import.md
- 新規移植：`.claude/scripts/weekly-template-review.sh`（週次の還流候補レビュー、bash）／`.claude/skills/template-sync/`。manifestにmappingsを追加
- `hooks/long-task-notify.py`：`LONG_TASK_DESKTOP_NOTIFY`で既定オフ（手動マージ）
- `freee-api-post`：④証憑の取得・添付の手順（fd94789、社名除去）、`ignored_company_ids`（lib・CLI・テスト3件は汎用IDで手動追加）、SKILL.md③登録済み取引のPUT修正（社名・取引ID除去）、setup-guide「開発用テスト事業所が消せない場合」
- `mf-monthly-check`／`freee-monthly-check`：補助科目（freeeは取引先・品目）の付け忘れ検出を追加（lib・tests・SKILL.md。テストデータの取引先名は汎用名に置換）
- `new-instance`／`setup`：MF/freee両パターンの片付け手順
- `テンプレート/`：CLAUDE.md「会計システム接続時の動き」「連携ツール接続時の動き」、accounting-rules.md「会計システム」節をMF/freee並記に置換＋「飲食費の会議費／交際費の判定」「速報締め→確定締め」節、`systems.md`（新規）

**見送り**
- `.claude/skills/tax-return-prep/`（317de9b、新規・開発途中）：安定後に移植。manifestのexcludedに保留として記録
- `.claude/rules/output-format.md`：変更箇所が特定クライアントのSlack Bot（振込承認リマインドの猶予・月末定型リマインド）の記述のみ。excluded対象（クライアント固有Bot）
- `.claude/skills/freee-api-post/companies.json`：実データ。`companies.example.json`に`ignored_company_ids`の説明は追加せず、SKILL.mdの説明で代替

## 初回作成

- 上流コミット `5833693` 時点の内容から汎用版を作成。`manifest.json` の `excluded` に記載したものは意図的に取り込んでいない
- 見送り（次回以降の候補）：
  - 定時サマリー・Slack返信反映・モデル定期レビューの各スクリプト（PowerShell依存。bash版を作る際に移植）
  - 請求書管理システムの承認自動化（判断基準が特定クライアント固有。汎用の型が抽出できたら）
  - クライアントSlackの常設Bot（収集・リマインド）
