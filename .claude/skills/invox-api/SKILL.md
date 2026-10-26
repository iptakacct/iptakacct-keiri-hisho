---
name: invox-api
description: 「invoxの認証セットアップして」「invox APIのヘルスチェックして」「〇〇社のinvox連携を追加して」のように言われたら使う。請求書管理システムinvox（受取請求書）のAPIを、会社（アカウント）ごとのOAuth認可＋自前スクリプトで直接呼び出す認証基盤。
---

# 概要

invox受取請求書のAPIを、アカウントごとに発行した認証情報経由で自前スクリプトが直接呼び出す仕組み。`freee-api-post`と同型（認可→トークン保存→定期ヘルスチェック）。

**現在のスコープ**：認証基盤（認可・トークン更新・疎通確認・ヘルスチェック）と、請求書一覧・明細の取得（`lib/invox_cli.py`）。承認の自動化（承認基準に沿って請求書を承認し会計システムへ反映する）は、判断基準がクライアントごとに異なるため、各インスタンスの`accounting-rules.md`に基準を確立してから個別に実装する。

## アカウント→インスタンス対応表

`accounts.json`（雛形は`accounts.example.json`）に、アカウントごとの設定（invox企業コード・認証情報の保存パス・Slackチャンネル、任意で`apply_staff_code`・`department_to_company`）を持つ。1つのinvoxアカウントで複数社を部門で区別する構成もあるため、`freee-api-post`の`companies.json`とは別ファイルにしている。

## 使い方

- **新規アカウントの追加・再認可**：`setup-guide.md`
- **疎通確認（手動）**：`bash .claude/skills/invox-api/scripts/invox-healthcheck.sh`
- **定期ヘルスチェック**：週次でスケジューラーに登録。失敗時は全体通知チャンネルにBot経由で通知

## CLIサブコマンド（`lib/invox_cli.py`）

`python lib/invox_cli.py --accounts-json accounts.json --repo-root <root> <サブコマンド> <account_key> ...`

- 参照系：`healthcheck`・請求書一覧/明細の取得
- `apply-invoice <account_key> <invoice_id> [staff_code] [appr_path_id]`：請求書を申請する（未申請→承認待ち）。`staff_code`省略時は`accounts.json`の`apply_staff_code`
- `approve-invoice`：承認（承認基準の判断は各インスタンスの運用メモに従う）
- `update-invoice <account_key> <invoice_id> [--exclude-export-invoice/-payment/-expense-journal true|false] [--memo-append 文言]`：出力設定・メモの部分更新（指定した項目だけ変わる。メモは既存の末尾に追記）
- `delete-invoice <account_key> <invoice_id> [--force]`：「データ化できません」の注意が付き、仕入先・金額・タイトルがいずれも空の書類だけ削除する（取り消せない。`--force`は個別指示があるときだけ）
- `export-journal <account_key> [--journal-type expense|payment] ...`：仕訳データ（弥生形式）を取得して無加工で保存。**invox側は「返した分は出力済み」になり再出力できない**ので、試し打ち禁止・保存に失敗したら即中断・204が返るまで繰り返す
- `prepare-journals <account_key> <ファイル...>`：export-journalで保存した弥生形式を、請求書の部門名から会社別の`prepared-<会社名>-<時刻>.json`に変換する（貸借不一致・部門不明は会社を推測せず非0終了）。会社の対応表は`accounts.json`の`department_to_company`。会計システムへの登録はここでは行わない

## ファイル構成

- `accounts.json`：アカウントごとの設定
- `lib/invox_api.py`：invox APIとのやりとり（トークン管理・API呼び出し）
- `lib/invox_cli.py`：accounts.json引き当て・ログ・CLIサブコマンド
- `lib/yayoi_journal.py`：弥生形式仕訳データの解析・会社別振り分け
- `scripts/invox-authorize-start.sh`・`invox-authorize-finish.sh`：認可
- `scripts/invox-healthcheck.sh`：ヘルスチェック
- `lib/test_*.py`：pytest
- トークン更新は`mf-api-post/lib/mf_lock.py`のロックを使う（使い捨てrefresh_tokenを複数の定期処理が取り合わないため）。`mf-api-post`スキルが必要

## 既知の注意点

- 認可コードの有効期限は短い（1分程度）。`authorize-start`でURLを開いたら、すぐに`authorize-finish`まで進める
- 認可は成功するのに自社の企業IDで403になる場合、**接続に使っているinvoxユーザーがその企業に紐づいていない**ことが原因のことがある。API接続用ユーザーを企業側に登録してもらう
