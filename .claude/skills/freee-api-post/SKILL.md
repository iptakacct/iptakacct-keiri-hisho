---
name: freee-api-post
description: 「freeeの認証セットアップして」「〇〇社のfreee連携を追加して」「freee APIのヘルスチェックして」のように言われたら使う。claude.aiのfreee MCPコネクタを都度手動接続する方式ではなく、会社ごとにOAuthプライベートアプリ＋自前スクリプトでfreee APIを直接呼び出す。
---

# 概要

freee会計のAPIを、会社ごとに発行したOAuthプライベートアプリ経由で自前スクリプトが直接呼び出す仕組み。claude.aiのfreee MCPコネクタ（`/mcp`で都度手動接続）と異なり、無人のスケジュール実行が可能。

**現在のスコープ**：認証基盤（OAuth認可・トークン自動更新・疎通確認・90日失効防止のヘルスチェック）と、`freee-monthly-check`からのデータ取得。会計データの書き込み（自動仕訳登録）は下記の制約により行わない。

## 会社→インスタンス対応表

`companies.json`（雛形は`companies.example.json`）に会社ごとの設定（`company_id`・認証情報の保存パス・Slackチャンネル・月次チェックのしきい値）を持つ。新しい会社を追加する際は、このファイルにエントリを追加し、`setup-guide.md`の手順で認可を行う。

## 使い方

- **新規会社の追加・再認可**：`setup-guide.md`の手順に従う
- **疎通確認（手動）**：`bash .claude/skills/freee-api-post/scripts/freee-healthcheck.sh`
- **90日失効防止の定期実行**：`freee-healthcheck.sh`をスケジューラーに週次で登録する（月次トリガーの不具合を避けるため週次を推奨）。失敗時は全体通知チャンネル（`.env`の`GENERAL_SLACK_WEBHOOK_URL`、メンションは`SLACK_MENTION_USER_ID`）にBot経由で通知が飛ぶ

## ファイル構成

- `companies.json`：会社ごとの設定（`companies.example.json`からコピー）
- `lib/freee_api.py`：freee APIとのやりとり（トークン管理・API呼び出し）
- `lib/freee_cli.py`：companies.json引き当て・ログ書き込み・CLIサブコマンド
- `lib/find_python.sh`：`python3`/`python`のどちらが実行可能かを判定するヘルパー
- `scripts/freee-authorize-start.sh`・`scripts/freee-authorize-finish.sh`：セットアップ（認可）用
- `scripts/freee-healthcheck.sh`：定期ヘルスチェック用
- `lib/test_*.py`：pytest

## ログ

- 会社ごとの処理結果は各インスタンスの`<インスタンス>/work/freee-api-post.log`に、ラッパー側の起動・成否は`.claude/skills/freee-api-post/work/healthcheck-wrapper.log`に記録される

## 重要な制約：freee APIには「明細の消込」を行う方法が無い

未処理の銀行明細（`wallet_txn`、freee画面の「自動で経理」に出てくるもの）を自動仕訳・消込するタスクを検討した際に判明した、freee API自体の仕様上の制約。全会社に共通する。

### ① `POST /api/1/deals`で仕訳を作っても、明細（wallet_txn）とは連携しない

- 新規に`deal`（取引）を作成しても、対応する`wallet_txn`は「未処理」のまま残り続ける。明細のステータスを変更する専用APIは存在しない
- 結果、APIで仕訳を作ると「取引」と「未処理明細」が別々に二重に存在する状態になる。freee公式のfreee-mcpでも同じ制約が報告されている（[freee/freee-mcp#313](https://github.com/freee/freee-mcp/issues/313)、[freee/freee-api-schema#541](https://github.com/freee/freee-api-schema/issues/541)）

### ② 「自動登録ルール」（`user_matchers`）APIは、消込・テンプレート系のルールを作成できない

- `GET/POST/PUT/DELETE /api/1/user_matchers`のうち、`act`（登録タイプ）の5〜9番（取引テンプレート、消込の推測/登録、一括振込ファイルでの消込）は**API非対応**（`enum`は`[0,1,2,3,4,10,11,12]`のみ）
- APIで作れるのは「取引の推測/登録」「振替の推測/登録」「無視取引の推測/登録」「プライベート取引の推測/登録」に限られ、**未決済取引の消込ルールは、freee画面での手動作成しかできない**
- ただし、一度freee画面で手動作成すれば、以降は明細が来るたびに自動適用される（都度の操作は不要）

### 結論・使い分け

- **新規の定型取引を都度自動登録したいだけ**（毎月定額の振込手数料等）→ `act=1`（取引を登録する）のルールなら`POST /api/1/user_matchers`で作成可能
- **既存の売掛金・未収入金等への入金消込を自動化したい** → APIでは不可能。オーナーにfreee画面で1回だけ手動設定してもらう

運用方針の全体像は`.claude/rules/output-format.md`の「freee会計の自動処理方針」節を参照。

## 今後の課題

- `user_matchers`（自動登録ルール）のAPI経由での作成機能（`act=1`等に限る）
- 未処理明細の定期監視・報告（確立パターンに一致しない明細を無人で検知し、Slackに報告する仕組み）
