# 決算（会計システムを使わない会社の帳簿）

会計システムを使っていない会社の帳簿・決算は、事業年度ごとにこのフォルダの下に `<年度>/`（例：`2026-03期/`）を作って管理する。

- 年度フォルダは `init --year-dir <年度> --start <期首日> --end <期末日>` で作る（事業年度の期間は `<年度>/period.yaml` に記録される）
- 資料から帳簿を作る手順はスキル `kessan-books`（`../../../.claude/skills/kessan-books/SKILL.md`）。コマンドの説明：`../../../.claude/scripts/kessan/README.md`
- 口座・明細形式・領収書の既定の支払方法の設定：`../../context/company/kessan-sources.yaml`
- 確立済みパターン（自動承認の根拠。オーナーの承認後に追加）：`../../context/company/kessan-patterns.yaml`
- `<年度>/inbox/`（受領資料の原本）はgitに入らない。原本の保管場所は `../../context/company/systems.md` の「ストレージ」行に書く
- `<年度>/extracted/`（AIの読み取り結果）・`evidence.csv`（証憑と明細行の対応）・`output/review-YYYYMMDD.xlsx`（確認用Excel）はgitで管理する
