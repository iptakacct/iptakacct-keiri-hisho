---
name: mf-api-post
description: 「マネーフォワードの認証セットアップして」「○○社のマネフォAPI連携を追加して」「マネフォAPIのヘルスチェックして」「マネフォの仕訳・未仕訳・試算表を読んで」のように言われたら使う。claude.aiのマネフォMCPコネクタ（mfc_ca）を都度手動接続する方式ではなく、会社ごとにOAuthアプリ＋自前スクリプトでマネーフォワード クラウド会計APIを直接呼び出す。現状は読み取り専用（認証基盤・ヘルスチェック・読み取り）。
---

# 概要

マネーフォワード クラウド会計のAPIを、会社ごとに発行したOAuthアプリ経由で自前スクリプトが直接呼び出す仕組み。claude.aiのマネフォMCPコネクタ（`mfc_ca`、`/mcp`で都度手動接続、「1接続＝1事業者」）と異なり、**会社名を指定するだけで切り替え操作なしに複数社を扱え**、無人のスケジュール実行もできる。`mf-fee-post`・`mf-monthly-check` のデータ取得はこのスキルの `read` を使う。

**現在のスコープ（読み取り専用）**：認証（OAuth認可・トークン自動更新）、ヘルスチェック、読み取り（`read`・`show-journal`・`find-journals`・`open-items`）、画面操作の手順書。**書き込み（仕訳登録・書き換え・取引先作成・証憑添付）は未実装**で、`lib/mf_api.py` の `api_call` はGET以外を拒否する。書き込みは当面 `mfc_ca` MCP（`mf-fee-post` の手順）で行う。自前APIでの書き込みを入れる場合は、書き込み前の安全確認（資本取引・役員との貸借・会計期間外・異常値・消込の判定を通すガード）とセットで設計すること（下記「未実装の範囲」）。

## 会社→インスタンス対応表

`companies.json`（雛形は `companies.example.json`）に会社ごとの設定（`name`・`office_id`〔事業者コード〕・`instance`〔ログの置き場になるインスタンスのフォルダ〕・`credentials_path`〔認証情報の保存パス〕・`slack_channel`）を持つ。新しい会社を追加する際は、このファイルにエントリを追加し、`setup-guide.md` の手順で認可する。`companies.json` と認証情報ファイル（`credentials*`）は実データなので `.gitignore` 対象にする。

## 使い方

以下、`python .claude/skills/mf-api-post/lib/mf_cli.py --companies-json .claude/skills/mf-api-post/companies.json --repo-root .` を `MF` と略す（`--repo-root` はこのリポジトリのルート）。

- **新規会社の追加・再認可**：`setup-guide.md` の手順に従う（`scripts/mf-authorize-start.sh` → `scripts/mf-authorize-finish.sh`）。
- **疎通確認（手動）**：`bash .claude/skills/mf-api-post/scripts/mf-healthcheck.sh`
- **失効防止の定期実行**：`mf-healthcheck.sh` を週1回程度スケジューラーから起動する（`automation.md` のルール通り、登録前に手動実行で1回成功を確認する）。失敗時は全体通知チャンネルへBot（Incoming Webhook）経由で通知する。
- **読み取り**：`MF read <会社> "<GETのパス?クエリ>" [--all-pages] [--out <ファイル>]`
- **仕訳1件を名前付きで読む**：`MF show-journal <会社> <仕訳番号> --date YYYY-MM-DD`（科目・補助科目・税区分を名前で、本体・税額・税込を並べる）
- **仕訳を金額・摘要の文字で探す**：`MF find-journals <会社> --from YYYY-MM-DD --to YYYY-MM-DD [--amount N] [--text 文字列] [--account 科目名]`（期間は会計期間をまたげない）
- **科目の未消込を補助科目ごとに並べる**：`MF open-items <会社> --account 売掛金 --date YYYY-MM-DD`

`<会社>` は `companies.json` の `name`（完全一致）か事業者コードのどちらでもよい。

## `read` の仕様

- MCPの `getJournals`・`getTransactions`・`getConnectedAccounts`・`getAccounts`・`getSubAccounts`・`getTaxes`・`getTradePartners`・`getJournalById`・`getDepartments`・`getTermSettings`・`getReportsTrialBalance*`・`getReportsTransition*` の代わり。**戻り値の形はMCPと同じ**（同じMF公式APIを叩いているため）
- 読めるのは一覧・1件取得・レポートだけ（`lib/mf_read.py` の許可リスト）。書き込み系のパス（`/transactions/{id}/journalize` 等）は読ませない
- 呼ぶたびに `/offices` で接続先の事業者コードを確認し、`companies.json` と食い違えば止まる（会社名と事業者コードを画面に出す）。MCPの `currentOffice` は不要
- 既定の保存先：`<インスタンス>/work/mf-read/<事業者コード>_<日時>.json`。画面には件数と保存先だけを出す（大きな取得でターミナルが埋まらないように）。`--out` で保存先を指定できる。保存先のJSONはPythonで読む
- `--all-pages` は一覧を最後まで取る。**`/journals`・`/transactions` だけに使うこと**。`/trade_partners`・`/departments`・`/sub_accounts` には付けない（実際のMF APIが `page`・`per_page` を受け付けず `unsupported_query_parameter` でHTTP 400になる。この3つは1回のGETで全件返る）
- パス例：
  - 未仕訳の明細：`read <会社> "/transactions?start_date=2026-09-01&end_date=2026-09-30" --all-pages` を取り、`journalizing_status == "none"` で絞る（`journalizing_statuses[]` はHTTP 400 `unsupported_query_parameter`）
  - 仕訳：`read <会社> "/journals?start_date=2026-09-01&end_date=2026-09-30" --all-pages`
  - 連携口座：`read <会社> "/connected_accounts"`
  - 科目・補助科目・税区分・取引先・部門：`/accounts`・`/sub_accounts`・`/taxes`・`/trade_partners`・`/departments`
  - 試算表：`/reports/trial_balance_pl?start_date=...&end_date=...`（BSは `trial_balance_bs`、`with_sub_accounts=true` で補助科目別）
  - 月次推移：`/reports/transition_pl?type=monthly&fiscal_year=2026`（`start_date`/`end_date` は受け付けない。`type` 必須、`fiscal_year` は期首の年。BSは `/reports/transition_bs`）
  - 仕訳1件：`/journals/<id>`
- 仕訳の期間指定は会計期間をまたげない（MFの仕様）

## ファイル構成

- `companies.example.json`：会社ごとの設定の雛形
- `lib/mf_api.py`：マネーフォワードAPIとのやりとり（トークン管理・GET）
- `lib/mf_lock.py`：トークン更新の会社ごとのロック
- `lib/mf_paging.py`：一覧をページの最後まで取る共通ロジック
- `lib/mf_read.py`：`read` の許可リストとページング
- `lib/mf_settle.py`：未消込の計上を同額の消込と1対1で対にする純粋関数（`open-items` が使う）
- `lib/mf_cli.py`：CLIサブコマンド（`authorize-start`・`authorize-finish`・`healthcheck`・`read`・`show-journal`・`find-journals`・`open-items`）。ログは `<instance>/work/mf-api-post.log`
- `lib/find_python.sh`：`python3`/`python` のどちらが実行可能かを判定するヘルパー
- `scripts/mf-authorize-start.sh`・`scripts/mf-authorize-finish.sh`：セットアップ（認可）用
- `scripts/mf-healthcheck.sh`：定期ヘルスチェック用
- `screen/`：APIに口が無い操作の画面手順書（`mf-screen-ops.md`〔画面操作の共通の注意〕・`create-sub-account.md`〔補助科目の作成〕・`exclude-transfer.md`〔口座間振替のもう片方を対象外にする〕）
- テスト：`lib/` で `python -m pytest -q`

## freeeとの技術的な違い（実装・保守時に注意）

- クライアント認証方式：HTTP Basic認証ヘッダー（freeeはPOSTボディ）
- リダイレクトURI：`http://localhost:8765/callback` 固定値、認可コードは手動コピー（freeeは `urn:ietf:wg:oauth:2.0:oob`）
- スコープ指定が認可URLに必須（freeeはアプリ登録時の権限設定のみ）
- アクセストークン1時間・リフレッシュトークン540日（freeeは6時間・90日）
- **リフレッシュトークンは使い捨て**（更新のたびに新しい値が発行され古い値は消える）。同じ認証情報を同時に使う処理が重なると後発は `invalid_grant` になる。`ensure_access_token` は更新の前後で会社ごとのロックを取り、失敗時は読み直して勝った側に乗り換える
- 秒間のAPI呼び出し数に上限があり、429が返る。`_request` が間隔を空けて再試行する。ページ送りの間にも約1.5秒空ける

## 内税入力の読み取りの注意

内税入力の会計システムでは、読み直すと課税行の `value` は税抜、`tax_value` はその消費税額で返る（`value + tax_value` が税込）。借方・貸方の一致は課税科目を `value + tax_value` で合計して比較する（`show-journal` は本体・税額・税込を並べて出す）。

## 画面操作が必要なもの

APIに口が無い操作（補助科目・勘定科目の作成、仕訳の削除、Boxの証憑の付け替え、対象外化）は画面で行う。手順は `screen/` を参照。

## 未実装の範囲（見送り中）

次は上流の実運用版にはあるが、このスキルには**含めていない**。使う場合は先に書き込み前の安全確認の仕組みを整えること。

- 未仕訳の自動登録（確立パターン表の作成〔build-patterns〕・登録〔register・register-one・register-batch〕・夜間の一括処理）、仕訳の登録・書き換え・日付変更・取引先の作成・証憑の添付／解除
- 書き込みの安全確認（ガード）。上流では、書き込みを必ず1つの入口に通し、①許可した書き込み口以外は止める ②資本取引・役員との貸借は止める ③会計期間外は止める ④消込・前払費用・借入金等・税区分・異常値は「要確認」として止める ⑤登録後に読み直して科目・補助科目・税区分・金額・日付を照合し、食い違えばその場で処理を止める、という作りにしている
- 型として参考になる設計：確立パターン表は「摘要（正規化）＋口座」ごとに科目・補助科目・税区分が3件以上一致したものだけを `patterns/<事業者コード>.json` に書き出す（消込を伴う科目・複合仕訳・向き不明は対象外）。摘要にランダムなIDが入るなど学習できない型は、会社ごとの確認済みルール `rules/<事業者コード>.json`（1行＝`match`〔摘要の文字〕・`match_type`〔prefix/contains/exact〕・`side`・`account`・`sub`・`tax`・`partner`）として持ち、学習したパターンに当たらないときだけ使う。どちらも会社別の実データなので共通リポジトリには置かず、各インスタンスの `context/company/accounting-rules.md` に確認済みの型を蓄積する方針（`output-format.md`「確立済みパターンのみ自動化」）
- 口座連携の「取得状態」の画面読み取り（APIに項目が無いため、画面を無人ブラウザ操作で読む仕組み）
