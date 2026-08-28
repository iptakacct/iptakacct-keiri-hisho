# keiri-hisho（経理秘書）

会計事務所・経理BPO事業者が、**Claude Code上で動く「AI経理秘書」**を、クライアントごとに立ち上げて運用するためのリポジトリ雛形です。

複数クライアントの記帳・月次チェック・Slack対応・日報を、1人＋AIで回すために実運用の中で作られた仕組みを、クライアント固有情報を取り除いて汎用化したものです。

## できること

| 領域 | 内容 | 主なスキル |
|---|---|---|
| 未仕訳の自動処理（マネーフォワード クラウド会計） | 確認済みパターンに一致する明細だけを自動登録し、それ以外は「要確認」として報告 | `mf-fee-post` |
| 月次チェック | 重複取引・定例取引の計上漏れ・科目の異常増減・BSマイナス残高・滞留・前払費用の償却漏れをA/B/C重要度付きで検出（読み取り専用） | `mf-monthly-check` / `freee-monthly-check` |
| freee API直接連携 | 会社ごとのOAuthアプリで無人ヘルスチェック・データ取得 | `freee-api-post` |
| 請求書管理（invox）API連携 | 認証基盤・ヘルスチェック | `invox-api` |
| 日々の秘書業務 | 朝の工程表・日報・週報・Slack未読整理・打合せ準備・メール下書き・壁打ち | `daily-schedule` `daily-log` `weekly-report` `slack-digest` `mtg-prep` `meeting-email` `sparring` ほか |
| 経理ルールの整備・月次まとめ | サブエージェントのチームで収集→分類→文書化→レビュー | `knowledge-team` `monthly-report-team` |
| 安全装置 | 危険操作のブロック（hooks）、長時間作業の完了通知、固有情報混入の検査 | `.claude/hooks/` `tools/leak-scan.py` |

## 設計思想

1. **確立済みパターンのみ自動化する**。新しい取引・未確定のルールは自動登録せず「要確認」に回す。自動化率より誤登録リスク回避を優先する
2. **ルールは「お願い」、hooksは「強制」**。CLAUDE.md・rulesで振る舞いを定め、hooksで危険操作を機械的に止める二重の守り
3. **クライアント情報はローカルから出さない**。自動化はすべてこのPC上で完結させ、クラウド実行しない
4. **ミスは原因まで追究し、仕組みに反映する**。教訓はSKILL.md・rules・チェックリストに蓄積する（`.claude/rules/error-handling.md`）

## 構成

```
keiri-hisho/
├── CLAUDE.md               全体共通の行動規範（オーナー・秘書名を /setup で設定）
├── .claude/
│   ├── rules/              文体・書式・機密・自動化の型・月次チェックリスト・法人略語・ミス対応
│   ├── hooks/              guard.py（git許可リスト）・long-task-notify.py（完了通知）
│   ├── skills/             全インスタンス共通のスキル
│   ├── agents/             knowledge-* / monthly-* サブエージェント
│   └── scripts/.env.example  Slack Webhook等の環境変数の雛形
├── テンプレート/             クライアントインスタンスの雛形（コピーして使う）
├── config/                 マネーフォワード事業者→インスタンス対応表の例
├── tools/                  leak-scan（固有情報検査）・upstream-diff（開発元からの差分）
├── upstream/               開発元リポジトリからの更新取り込み管理
└── docs/                   設計メモ
```

## 導入手順

1. このリポジトリをclone（または自社のプライベートリポジトリにコピー）する
2. `bash tools/install-git-hooks.sh` を実行し、コミット前の固有情報検査を有効にする
3. `.claude/hooks/guard-allowlist.txt` に自社のGitHubアカウント等を書く（push/clone先の許可リスト）
4. `.claude/scripts/.env.example` を `.env` にコピーし、Slack Incoming Webhook URL・メンション先ユーザーIDを書く
5. Claude Codeを `keiri-hisho/` で起動し、`/setup` でオーナー呼称・秘書名・事務所名を設定する
6. クライアントを追加するときは `/new-instance` → そのフォルダで `/setup`
7. 会計システムに応じて `mf-fee-post`（マネーフォワード、`/mcp`で接続）または `freee-api-post`（freee、`setup-guide.md`）をセットアップする

## 前提

- Claude Code（Max等のサブスクリプション、またはAPI）。日本語での運用を前提にしている
- Python 3.10以上、Git、Bash（Windowsの場合はGit Bash）
- Slack（Incoming Webhook を1つ作れる権限）
- 会計システム：マネーフォワード クラウド会計（公式MCP `mfc_ca`）／freee会計（OAuthプライベートアプリ）

## 開発元からの更新

このリポジトリは開発元の実運用リポジトリから派生しています。開発元での改善を取り込む仕組みは `upstream/README.md` を参照してください（導入先で使わない場合は無視して構いません）。

## ライセンス・利用範囲

（未設定。導入契約に従う）
