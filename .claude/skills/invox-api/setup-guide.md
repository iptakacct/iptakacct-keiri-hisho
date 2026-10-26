# invox API連携セットアップ手順

新しいinvoxアカウント（企業）をAPI連携に追加する際の、一回限りの手順。

## 事前準備

1. invox側で、API接続に使うユーザーが対象企業に登録されていることを確認する（未登録だと認可は通っても企業IDでのAPI呼び出しが403になる）
2. invoxの管理画面でAPI連携用の認証情報（Client ID / Client Secret）を発行し、`accounts.json`の対象エントリの`credentials_path`が指す場所に保存する（`credentials*`は`.gitignore`対象。AIは読み書きできないので**オーナー本人がテキストエディタで作成する**）

```json
{
  "client_id": "（ここに貼る）",
  "client_secret": "（ここに貼る）"
}
```

## 認可

3. `bash .claude/skills/invox-api/scripts/invox-authorize-start.sh <account_key>` を実行し、表示されたURLをブラウザで開いて認可する。認可コードをコピーする
4. **すぐに**（有効期限は1分程度）`bash .claude/skills/invox-api/scripts/invox-authorize-finish.sh <account_key> <認可コード>` を実行する

## 完了後

5. `accounts.json`にエントリが無ければ追加する（`accounts.example.json`参照）
6. `invox-healthcheck.sh`を1回手動実行し、正常終了することを確認してからスケジューラーに登録する

## スコープについて

- `lib/invox_api.py`の`SCOPE`は`read write receive_invoice_update`。**`read write`は全エンドポイント共通の包括スコープではない**：更新系（承認API等）は個別スコープ（例：`receive_invoice_update`）が別途必要で、足りないと403（`You do not have permission`）になる
- 必要スコープは、対象エンドポイントの公式APIドキュメントのページ→説明文下の「Authorizations:」を開いた「Required scopes:」欄で確認できる。存在しないスコープ名を指定すると`invalid_scope`になる
- スコープを変えたら、認可コードは1回限りなので新しいURLで再度認可する

## ログインアカウントの罠

認可画面で対象会社を選んでも、ブラウザのinvoxログインセッション自体が別アカウントのままだと、トークンはそのログインアカウントに紐づく（別会社の企業IDで403になり、そのアカウントが登録されている会社だけ成功する）。複数アカウントを順に認可するときは、**一度invoxからログアウトして目的のアカウントで再ログインした状態**（またはシークレットウィンドウ）で認可URLを開く。
