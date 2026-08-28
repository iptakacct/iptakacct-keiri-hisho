---
name: invox-api
description: 「invoxの認証セットアップして」「invox APIのヘルスチェックして」「〇〇社のinvox連携を追加して」のように言われたら使う。請求書管理システムinvox（受取請求書）のAPIを、会社（アカウント）ごとのOAuth認可＋自前スクリプトで直接呼び出す認証基盤。
---

# 概要

invox受取請求書のAPIを、アカウントごとに発行した認証情報経由で自前スクリプトが直接呼び出す仕組み。`freee-api-post`と同型（認可→トークン保存→定期ヘルスチェック）。

**現在のスコープ**：認証基盤（認可・トークン更新・疎通確認・ヘルスチェック）と、請求書一覧・明細の取得（`lib/invox_cli.py`）。承認の自動化（承認基準に沿って請求書を承認し会計システムへ反映する）は、判断基準がクライアントごとに異なるため、各インスタンスの`accounting-rules.md`に基準を確立してから個別に実装する。

## アカウント→インスタンス対応表

`accounts.json`（雛形は`accounts.example.json`）に、アカウントごとの設定（invox企業コード・認証情報の保存パス・Slackチャンネル）を持つ。1つのinvoxアカウントで複数社を部門で区別する構成もあるため、`freee-api-post`の`companies.json`とは別ファイルにしている。

## 使い方

- **新規アカウントの追加・再認可**：`setup-guide.md`
- **疎通確認（手動）**：`bash .claude/skills/invox-api/scripts/invox-healthcheck.sh`
- **定期ヘルスチェック**：週次でスケジューラーに登録。失敗時は全体通知チャンネルにBot経由で通知

## ファイル構成

- `accounts.json`：アカウントごとの設定
- `lib/invox_api.py`：invox APIとのやりとり（トークン管理・API呼び出し）
- `lib/invox_cli.py`：accounts.json引き当て・ログ・CLIサブコマンド
- `scripts/invox-authorize-start.sh`・`invox-authorize-finish.sh`：認可
- `scripts/invox-healthcheck.sh`：ヘルスチェック
- `lib/test_*.py`：pytest

## 既知の注意点

- 認可コードの有効期限は短い（1分程度）。`authorize-start`でURLを開いたら、すぐに`authorize-finish`まで進める
- 認可は成功するのに自社の企業IDで403になる場合、**接続に使っているinvoxユーザーがその企業に紐づいていない**ことが原因のことがある。API接続用ユーザーを企業側に登録してもらう
