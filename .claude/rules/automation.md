# 自動化スクリプトの型

新しく自動化スクリプト（`.claude/scripts/`配下のスケジューラー用スクリプト等）を作るときは、必ず以下を入れる。

1. **エラーハンドリング**：失敗を握りつぶさない（`set -eu`、`$ErrorActionPreference = "Stop"`等）
2. **実行ログ**：いつ動いて何をしたか分かるようにする（Slack投稿・ログファイルへの記録など）
3. **失敗時の通知先**：失敗した時に何が起きるか・誰にどう伝わるかを明確にする
   - **Slackへの通知は、`claude -p`経由でオーナー本人のSlackアカウントとして投稿する設計にしない**。Slackの仕様で自己送信メッセージは通知が抑制されるため、本人に気づいてもらえない。Bot経由のIncoming Webhook（`.env`の`GENERAL_SLACK_WEBHOOK_URL`等）を使い、必要ならメンション（`<@SLACK_MENTION_USER_ID>`）を付ける

その他の運用ルール:
- cron・タスクスケジューラー等での定期実行を設定する前に、必ず手動実行で1回成功を確認する
- 認証情報はスクリプトに直書きせず、環境変数または設定ファイル（`.gitignore`・`deny`対象）に置く

## PowerShellスクリプトで`claude.exe`の出力を変数に取り込む場合の文字化け対策

`$output = & "claude.exe" -p $prompt ...` のように、`claude -p`の標準出力（UTF-8）をPowerShell変数に取り込んで加工するスクリプトでは、**取り込み前に明示的にUTF-8デコードを指定しないと、日本語部分だけが文字化けする**。

- 原因：PowerShellが子プロセスの標準出力を読み取る際のデコードは`[Console]::OutputEncoding`に従うが、これがシステムのANSIコードページ（日本語Windowsでは932=Shift-JIS）になっている実行環境（特にタスクスケジューラー経由）では、UTF-8バイト列をShift-JISとして誤デコードする
- 症状の見分け方：英数字は無事なのに日本語部分だけ意味不明な記号・半角カタカナ（`ｦｧｨｩｪｫ`等）だらけになっていたら、このパターンを疑う
- 対策：スクリプト冒頭（claude.exeを呼ぶ前）に以下を必ず入れる
  ```powershell
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
  ```
- Slack Webhook送信時のエンコード処理とは別の、その手前の「claude.exeの出力を読み取る」段階の問題なので、送信側だけ直しても再発しうる

## BashスクリプトからPythonを呼ぶ場合の文字化け対策

Bashラッパースクリプトが`python`の標準出力を`> file 2>&1`のようにファイルへリダイレクトして後で読む設計では、**Windows環境でPythonの標準出力エンコーディングがcp932にフォールバックし、日本語部分が文字化けする**ことがある。

- 症状の見分け方：同じログファイル内で、Bashスクリプト自身が直接書いた行（正常なUTF-8）と、Python経由で取り込んだ行（文字化け）が混在する
- 対策：Pythonを呼び出す前に環境変数で明示的にUTF-8を強制する
  ```bash
  export PYTHONIOENCODING=utf-8
  export PYTHONUTF8=1
  ```
- 似た構成（Bash＋Python、標準出力をファイル/変数に取り込む）の新規スクリプトを作る際は、最初からこの2行を入れておく

## クロスプラットフォーム対応

macOSとの並行運用を想定し、**新しく作る自動化スクリプトはWindows固有のパス・PowerShell依存を避け、macOSでも動く形にする**。

- 新規スクリプトはPowerShell（`.ps1`）ではなく、Bash等のクロスプラットフォームな手段を優先する
- パスはWindows固有の区切り文字・ドライブレター表記を避け、OS非依存な書き方にする
- 既存のWindows専用資産は、置き換えのタイミングまで無理に書き直さなくてよい
