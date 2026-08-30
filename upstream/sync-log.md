# upstream 同期ログ

取り込み履歴と、汎用化できずに見送った変更の記録。新しいものを上に。

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
