---
name: freee-api-post
description: 「freeeの認証セットアップして」「〇〇社のfreee連携を追加して」「freee APIのヘルスチェックして」のように言われたら使う。claude.aiのfreee MCPコネクタを都度手動接続する方式ではなく、会社ごとにOAuthプライベートアプリ＋自前スクリプトでfreee APIを直接呼び出す。
---

# 概要

freee会計のAPIを、会社ごとに発行したOAuthプライベートアプリ経由で自前スクリプトが直接呼び出す仕組み。claude.aiのfreee MCPコネクタ（`/mcp`で都度手動接続）と異なり、無人のスケジュール実行が可能。

**現在のスコープ**：認証基盤（OAuth認可・トークン自動更新・疎通確認・90日失効防止のヘルスチェック）、`freee-monthly-check`からのデータ取得、未処理明細・未添付証憑・口座連携の無人監視、預金利息の源泉振替の自動起票、給与（freee人事労務）の確定リマインドと未対応検知。取引の自動仕訳登録（`POST /api/1/deals`）は下記の制約により行わない（書き込みは、確立済みの定型である預金利息の源泉振替伝票に限る）。

## 会社→インスタンス対応表

`companies.json`（雛形は`companies.example.json`）に会社ごとの設定（`company_id`・認証情報の保存パス・Slackチャンネル・月次チェックのしきい値）を持つ。新しい会社を追加する際は、このファイルにエントリを追加し、`setup-guide.md`の手順で認可を行う。

## 使い方

- **新規会社の追加・再認可**：`setup-guide.md`の手順に従う
- **疎通確認（手動）**：`bash .claude/skills/freee-api-post/scripts/freee-healthcheck.sh`
- **90日失効防止の定期実行**：`freee-healthcheck.sh`をスケジューラーに週次で登録する（月次トリガーの不具合を避けるため週次を推奨）。失敗時は全体通知チャンネル（`.env`の`GENERAL_SLACK_WEBHOOK_URL`、メンションは`SLACK_MENTION_USER_ID`）にBot経由で通知が飛ぶ

## ファイル構成

- `companies.json`：会社ごとの設定（`companies.example.json`からコピー）。任意項目：
  - `ignored_company_ids`（配列）：対象社と同じfreeeアカウントに紐づいていて自分では削除できない事業所IDを列挙すると、疎通確認の「対象1社だけか」検証でその事業所を無視する
  - `auto_post`：`false`でこちらから仕訳を書き込む自動処理（預金利息の源泉振替）の対象外にする（監視・読み取りは続く）
  - `monitor`：`{"enabled": false}`で監視対象外、`{"receipts": false}`でファイルボックスの未添付証憑のチェックだけ止める
  - `notify_webhook_env`：監視・預金利息の通知先Webhookの`.env`キー名（無ければ`GENERAL_SLACK_WEBHOOK_URL`）
  - `payroll`：給与確定リマインド（`lib/payroll_check.py`）の設定
- `lib/freee_api.py`：freee APIとのやりとり（トークン管理・API呼び出し。トークン更新は同じ認証情報を使う処理同士でロックし、HTTPは60秒で打ち切る）
- `lib/freee_cli.py`：companies.json引き当て・ログ書き込み・CLIサブコマンド（`healthcheck`／`monitor`／`interest`ほか）
- `lib/freee_monitor.py`・`lib/interest_withholding.py`：監視・預金利息の判定ロジック（純粋関数）
- `lib/payroll_check.py`・`lib/payroll_todo.py`：給与（人事労務）の確定リマインドと未対応検知
- `lib/find_python.sh`：`python3`/`python`のどちらが実行可能かを判定するヘルパー
- `scripts/freee-authorize-start.sh`・`scripts/freee-authorize-finish.sh`：セットアップ（認可）用
- `scripts/freee-healthcheck.sh`：定期ヘルスチェック用
- `scripts/freee-monitor.sh`・`scripts/freee-interest.sh`：監視・預金利息の定期実行用ラッパー
- `payroll-todo.example.json`：給与の未対応検知ルールの雛形（`payroll-todo.json`にコピーして使う）
- `lib/test_*.py`：pytest

### 給与（freee人事労務）：payroll_check.py／payroll_todo.py

給与は人事労務APIで読み取り専用に扱う（金額を更新するAPIは無い）。OAuthアプリに人事労務の参照権限を付けておく（セットアップ時に`[会計]`だけでなく`[人事労務]`の参照も付与する）。実行前に`.env`の`GENERAL_SLACK_WEBHOOK_URL`等を確認し、スケジューラー登録の前に必ず`--dry-run`で手動実行して1回成功を確認する（`automation.md`）。

- `lib/payroll_check.py`：給与明細を取得し、確定リマインド（`remind`、毎月20日ごろ）／事後チェック（`check`、25日ごろ）の本文を生成してWebhook投稿する。全員確定済みなら通知しない。`check`は会計側に給与仕訳が見当たらない場合も知らせる。会社ごとの設定は`companies.json`の`payroll`ブロック（`pay_month_offset`・`slack_webhook_env`・`fix_url`〈`{year}`/`{month}`を展開〉）。`python lib/payroll_check.py remind --dry-run --date YYYY-MM-DD`で手元確認
- `lib/payroll_todo.py`：「毎月人が入れないと誤ったまま確定してしまう項目」の未対応を、`payroll-todo.json`のルールで検知してWebhook投稿する。ルールの`type`は`zero_pay`（休業中で0のはず）／`require_payment`（成果報酬など特定の支給項目が必須）／`require_expense`（承認済み経費精算が給与に乗っているか）／`hourly_zero`（時給者の支給が未入力でないか）／`require_midnight`（月給者の深夜割増）／`must_absent`（明細が出てはいけない人）。締め日〜支払日の期間だけ動き、支払日3日前から未確定も言い、メンションを付ける。前回と同じ内容は翌日以降投稿しない（支払日3日前以内は毎日出す）。`python lib/payroll_todo.py --dry-run`で手元確認
- 従業員ごとのルールの根拠は、各インスタンスの`context/company/`の給与マスタに人手向けに書き、食い違ったらマスタを正とする

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
- ただし、一度freee画面で作成すれば、以降は明細が来るたびに自動適用される（都度の操作は不要）

### ②-b 消込ルールは画面でなら作れる。claude-in-chromeで代行できる

APIで作れないだけで、**freee画面には「未決済取引の消込をする」ルールが存在し、claude-in-chromeで設定を代行できる**（実運用で実証済み）。オーナーに手動でお願いする必要はない。

**画面での設定内容**（`secure.freee.co.jp/user_matchers` →「新規作成」）

| 項目 | 内容 |
|---|---|
| 収支区分・取引口座・取引内容 | 通常のルールと同じ（部分一致の摘要が使いやすい） |
| 行う処理 | **未決済取引の消込をする** |
| 取引先（必須） | この取引先の未決済取引から、**決済期日と合計金額が明細と近いもの**を探して消し込む |
| 消込の自動実行 | 「実行する」＝`act=7`（自動実行）／「実行しない（推測にとどめる）」＝`act=6` |
| 不足金額 | **金額一致のみで実行したいなら`0`を入力する**。空欄でも作成できてしまうが、一覧に「差額が(空欄)円なら自動で登録」と出る中途半端な状態になる。差額を許容する場合は金額と差額用の勘定科目を指定する |

**取引先で引き当てるので、同額の入金が複数社あっても取り違えない。**これが消込ルールを使う最大の理由。

**claude-in-chromeでの操作のコツ**

- `select`は`Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set`で値を入れ、`change`イベントを飛ばす
- 取引先のオートコンプリートは、`input`の`value`をsetterで入れて`input`イベントを飛ばすと候補が出る。候補は`li`要素で、`mousedown`→`click`の順に送ると選択される。**DOMの文字列検索では候補が見つからないことがある**（テキストが複数ノードに分かれる）ので、見つからなければスクリーンショットで出ているか確認する
- 作成後は一覧の行テキストで「差額が◯円なら自動で登録」を読んで検証する

**API側の制約**：`GET /api/1/user_matchers/{id}`は**act=5〜9のルールに対して400を返す**（「指定されたアクション(act)はこのAPIでサポートされていません」）。一覧の`GET /api/1/user_matchers`なら`act=7`のルールも返るので、**作成後の検証・監視は一覧APIで行う**。

### 結論・使い分け

- **新規の定型取引を都度自動登録したいだけ**（毎月定額の振込手数料等）→ `act=1`（取引を登録する）のルールなら`POST /api/1/user_matchers`で作成可能（手順は下記⑤）
- **既存の売掛金・未収入金等への入金消込を自動化したい** → APIでは作成不可だが、**claude-in-chromeでfreee画面から設定できる**（上記②-b）。1回設定すれば以降はfreee側が自動で消し込む

運用方針の全体像は`.claude/rules/output-format.md`の「freee会計の自動処理方針」節を参照。

### ①-b 内部API `deal_settlement_matching` による消込（ホスト・認証方式のみ検証。POST実行は未検証）

社外の経理担当者から「消込をUIで1件ずつクリックする必要はなく、freeeの内部APIで一括消込できる」という情報提供があった。報告された呼び出し形：

```
GET  /api/p/wallet_txns/deal_settlement_matching?wallet_txn_id={id}   ← 消込候補の取得
POST /api/p/wallet_txns/{id}/deal_settlement_matching                 ← 消込の実行
     body: {"transaction_id": "決済行のid", "line_item_id": "決済行id"}
```

**検証済み（実データでGETのみ確認）**：
- ホストは`secure.freee.co.jp`（画面側ドメイン）。公式OAuth API（`api.freee.co.jp`、`/api/1/...`）とは別のAPIで、どちらのホストに対しても**OAuthのBearerトークンでは`401 アクセス権限がありません`で拒否される**
- **認証はブラウザのログインセッション（Cookie）が必要**。`claude-in-chrome`でfreee会計にログイン済みのタブ内で`fetch('/api/p/wallet_txns/deal_settlement_matching?wallet_txn_id=...', {credentials:'include'})`を実行すると`200`で正常応答した
- 結論：**`freee-api-post`が使う無人実行向けのOAuth認証では呼び出せない**。使うなら「ブラウザにログインした状態で`javascript_tool`からfetchする」方式になり、①**無人スケジュール実行はできない**②**非公開・無保証のAPIで公式スキーマに存在せず、予告なく仕様変更・廃止されるリスクがある**という制約が残る
- **POST（実際の消込実行）は未検証**。実データへの書き込みになるので、対話中にオーナーの確認を得てから、対象の明細・会社を明示して1件ずつ試す。`transaction_id`/`line_item_id`の取得元（`GET`のレスポンスのどのフィールドか）も併せて確認し、確認できたらこの節を追記する
- 上記「結論・使い分け」の「APIでは作成不可」は、**公式OAuth APIについては変わらず正しい**。この内部APIは対話中にブラウザ経由で一括処理を代行する程度の用途にとどまる

### ③ 登録済み取引の修正（科目・税区分・取引先・摘要・証憑）は`PUT /api/1/deals/{id}`で可能

- 「自動で経理」から登録された取引（`deal_origin_name: 自動で経理`、明細と紐付き済み）でも、`details`の`tax_code`等を書き換える更新はできる（例：税区分を「課対仕入（控80）10%」から「課対仕入10%」へ修正し、再取得で反映と`payments`の明細紐付き維持を確認済み）
- 手順：`GET /api/1/deals/{id}`で現状を取得 → `details`と`payments`に**既存の`id`を含めて**全体を送る（`id`を省くと明細行が作り直される恐れがある）→ 直後に`GET`で再取得して検証。金額・決済口座は変えない（明細との整合が崩れる）
- ①〈消込ができない〉は「明細→取引の紐付けを**新規に作れない**」という意味で、既に紐付いた取引を直すことは妨げない

### ④ ファイルボックスの証憑（receipts）の取得・添付

- 一覧：`GET /api/1/receipts?start_date=&end_date=`（`created_at`基準）。スマホ・LINE登録の写真は`receipt_metadatum`（日付・金額・取引先）が空のことが多く、機械照合できない → `GET /api/1/receipts/{id}/download`（`Authorization: Bearer`、`company_id`をクエリで）で画像/PDFを落としてReadツールで目視する
- 「添付済みか」は`receipts`側に項目が無い。`GET /deals`の各取引の`receipts[].id`を集めて差分を取る
- 添付：`PUT /api/1/deals/{id}`に、既存の`details`/`payments`（idを含む）＋`receipt_ids`（既存＋追加）を渡す（税区分・決済は維持されることを検証済み）
- 連携外の支払手段（個人カード・現金・QR決済）の領収書は、取引自体が無いので添付先が無い。役員立替の取引登録が先

### ⑤ 自動登録ルール（user_matchers）の作成・更新手順

- 先に`GET /api/1/user_matchers?limit=100`（レスポンスは`data`配列）で既存ルールを確認し、重複を避ける。`act=0`（推測のみ）の既存ルールは`PUT /api/1/user_matchers/{id}`で`act=1`へ更新する
- `POST /api/1/user_matchers`の必須：`act, active, condition, description, entry_side_str, priority`。科目・税区分・口座・取引先は**IDではなく名前**で指定（`account_item_name`, `tax_name`〈例「課対仕入10%」「対象外」〉, `walletable`〈口座名の完全文字列。`GET /walletables`の`name`〉, `partner_name`〈未登録なら自動作成〉）
- `condition`：0=部分一致、1=前方一致、2=後方一致、3=完全一致、4=指定なし。`act`：0=推測、1=登録、2/3=振替の推測/登録、4=無視を登録、10〜12=無視/プライベートの推測・登録（5〜9のテンプレート・消込系は非対応）
- `qualified_invoice_setting`：`qualified`／`non_qualified`／`depends_on_partner`（後者は`partner_name`必須）。対象外取引は`non_qualified`
- 作成後は一覧を再取得して確認し、インスタンスの`accounting-rules.md`にルールID付きで記録する
- 公式スキーマは`https://raw.githubusercontent.com/freee/freee-api-schema/master/v2020_06_15/open-api-3/api-schema.json`（約2MB）をcurlで落として`paths["/api/1/user_matchers"]`を読む

## 預金利息の源泉振替（freee全社共通）

純額入金型の口座（源泉税を差し引いた後の金額だけが入金される銀行口座）の利息を、**明細の自動登録ルールと振替伝票の2本立て**で自動化する。freeeの自動登録ルールには動的な金額計算が無く、割戻し（総額への戻し）をルールに埋め込めないため。

- **① 明細**：自動登録ルール（`act=1`）で「受取利息1行（入金額そのまま、非課売上）」として自動計上
- **② 源泉分**：`scripts/freee-interest.sh`（スケジューラーで平日朝に実行）が`POST /api/1/manual_journals`で振替伝票を1本足す
  ：借方 法人税・住民税及び事業税（品目「利息源泉」等・対象外）／貸方 受取利息（明細と同じ税区分）
- 合計すると受取利息＝総額になり、従来の「1取引・2明細」と同じ残高になる。振替伝票は銀行明細と紐づく必要がないので、`POST /deals`を避ける理由（①明細が未処理のまま残る）に当たらない

**逆算式：総額 = floor(入金額 ÷ 0.84685)、源泉税 = 総額 − 入金額**（15.315%＝所得税15%＋復興特別所得税0.315%）。実績とも一致することを確認済み。整数演算で計算する（浮動小数点だと1円ずれうる）。

**対象にしないもの**（`lib/interest_withholding.py`の`find_targets`が自動判定する）：

| 判定 | 内容 |
|---|---|
| `already_gross` | 取引に法人税系の行が既にある（従来型の1取引・2明細で総額計上済み） |
| `gross_type` | **総額入金型の口座**（利息の入金と税金の出金が**2本の明細**で出てくる型）。割戻し不要。判定は①同口座で±3日以内に「入金額×15.315%（切捨）」と一致する出金明細がある（摘要は問わない）②同口座・同日に摘要が「国税」等の出金明細がある——のいずれか。**金額一致を主にしている**ので、無関係な納税明細（地方税の納付など）では誤爆しない |
| `already_journaled` | 摘要の目印（`対象取引<取引id>`）、または同日・同額の「貸方受取利息×借方法人税系」の伝票が既にある |
| `too_small` | 源泉0円（入金1〜2円） |
| `inconsistent` | 逆算と順算が合わない（安全網。Slackで要確認として報告） |

- **状態ファイルを持たない**。毎回APIで既存の伝票を確認して重複を防ぐ（状態ファイルの消失・巻き戻りで二重起票が起きないようにするため。`data-import.md`の二重取り込み防止と同じ考え方）
- 品目（補助科目相当）は**その会社の過去の利息仕訳から推定する**（`infer_item_id_from_history`）。会社ごとに名前が違うため。履歴が無ければ名前で探す（`WITHHOLDING_ITEM_NAMES`）
- 取引数の多い会社のために、取引の取得は`GET /api/1/deals?account_item_id=<受取利息>`で絞る（絞らないと数分かかる）
- 手元確認：`bash .claude/skills/freee-api-post/scripts/freee-interest.sh --dry-run`（登録・ログ更新なし）。スケジューラー登録の前に必ず手動で1回成功を確認する
- Slack投稿は**起票したもの・要確認があるときだけ**（無ければ投稿しない）
- 実装：`lib/interest_withholding.py`（純粋関数、`lib/test_interest_withholding.py`）、`lib/freee_cli.py`の`interest`サブコマンド、`scripts/freee-interest.sh`

**自動登録ルールの注意**：ルールは**同期時にしか適用されない**（既に取り込み済みの未処理明細には遡って効かない。`wallet_txn`の`rule_matched`が`false`のまま）。ルールを変えた月の明細は、freee画面で1回だけ登録する。**公式APIに「明細から取引を登録する」口は無い**（公式スキーマを走査して確認：`/api/1/wallet_txns`は GET／POST〈手動明細の作成〉／DELETE のみ、`user_matchers`にも「既存明細へ適用」は無い）。また`act=5`（取引テンプレートを推測する）はAPIの`act` enumに無いため、`act=1`に変えると**APIでは元に戻せない**（戻すならfreee画面でルールを作り直す）。

**対象にする会社は`companies.json`の`auto_post`で決める**。`auto_post: false`の会社にはこちらから仕訳を書き込まない（監視・読み取りは対象のまま）。会計を他の担当者が見ている会社などに使う。今後、書き込みを伴う自動化を新しく作るときも、この`auto_post`を見てから書くこと。

会社ごとの口座の型（純額／総額）・ルールid・源泉振替の要否は、各インスタンスの`context/company/accounting-rules.md`に記録する。

## 残高調査・二重計上防止の型

freeeのデータを使って「本当に計上漏れか」「本当に未計上か」を調べる際の鉄則。上記①〜⑤のAPI仕様とセットで踏みやすい罠。

- **「未処理明細」≠「未計上」**：`wallet_txns`の`status=1`（未処理、freee画面の「自動で経理」に出てくるもの）の多くは、実際には`deals`（取引）・`manual_journals`（振替伝票）・`transfers`（口座振替）のいずれかで**既に計上済み**で、消込（明細との紐付け）だけが済んでいない状態。「未処理＝未計上」とみなして新規に仕訳を起票すると、ほぼ確実に①の二重計上になる。真に未計上な取引を特定したいときは、着手前に`GET /walletables?with_balance=true`で**帳簿残高と明細残高の差分**を口座ごとに取り、その差分に対応する明細だけを対象に調査する。計上済みかどうかの判定は`deals`だけでなく`manual_journals`・`transfers`の3種類全てを確認する（取引だけを見て「計上漏れ」と断定しない）
- **口座振替APIの罠**：`GET /transfers`のトップレベル`to_walletable_id`は、複数振替先がある場合の**先頭1件しか返さない**。必ず`to_walletables`配列を見る。銀行口座がらみの新規計上は、振替伝票ではなく「取引（deal）」で登録するのが基本
- **`tax_code`だけを変更したいとき**：payloadから`vat`フィールドを除外して送ると自動再計算される（古い`vat`値のまま送ると税額と不整合になる）
- **APIで登録した取引・振替は銀行明細と自動リンクしない**（①参照）ので、放置せず明細側をUIで「消込」または「明細を無視」のどちらかで解消する。1つの明細に対応する振替が複数ある場合はAPI/UIどちらでも消込できない→この場合は「明細を無視」が正解（帳簿側は振替で正しく計上済みのため）
- **`walletables`には同期状態・最終同期日時が無い**。`update_date`は最終同期日時ではなく、同期しても変わらない。口座同期が生きているかを見る・同期を起動するにはfreee画面の「口座」一覧（`/walletables`）の「最終同期日時」列と口座ごとの「同期」ボタンしかない（「全口座同期」はスタータープラン以上限定）。**同期ボタンを押すのはオーナー**で、秘書は最新明細日から連携切れの疑いを報告するまで
- レート制限（429）はリトライ＋待機で対処する

## 前期確定分の納税の登録（全クライアント共通）

決算で計上した未払法人税等・未払事業税等は、翌期の納付時に取り崩す。freeeの明細は摘要が「PE 〇〇税務署 〈整理番号〉」「PE 地方税共同機構 〈整理番号〉」の形で来る。

- **freeeの推測をそのまま使わない**。「預り金」（給与の源泉所得税と誤認）を提案してくることが多い。正しくは**未払法人税等／未払事業税等**、税区分は**対象外**
- 国税（税務署宛）は法人税と地方法人税で明細が2件に分かれる。地方税（地方税共同機構宛）は法人住民税と法人事業税が1件にまとまって来るので、**登録後に`PUT /api/1/deals/{id}`で2行に分割する**（画面の1行登録では分割できない）。`details`を2要素にし、`payments`は元の値をそのまま渡す
- 登録後に対象月の試算表で未払法人税等・未払事業税等が**0円になること**を確認する。ならなければ決算時の計上額と納付額が食い違っている
- **決算時に、未払計上額の内訳（法人税・地方法人税・住民税・事業税それぞれの金額）を`context/company/accounting-rules.md`に控えておく**と、翌期の納付明細と機械的に突き合わせられる。控えが無いと毎年、納付書を探すところからになる

## 監視（フェーズ3）：未処理明細・未添付証憑・口座連携の無人チェック

「登録」はしない読み取り専用の監視。`scripts/freee-monitor.sh`（スケジューラーで平日朝に実行）が`freee_cli.py monitor`を呼び、`companies.json`の全社について以下を検知し、**報告すべきものがある会社だけ**Slackに投稿する（無ければ投稿しない）。

| 検知 | 内容 | データ |
|---|---|---|
| 未処理明細の滞留 | `status=1`（消込待ち）で明細日付から`--min-days`（既定3日）以上経過。新規分を列挙し、残件数・最古日付を添える | `GET /api/1/wallet_txns`（直近`--days`＝既定90日） |
| 初めて見る摘要パターン | 摘要を正規化（空白・数字除去）し、状態ファイルの`known_patterns`に無いもの。`accounting-rules.md`への確立パターン追記候補 | 同上 |
| ファイルボックスの未添付証憑 | どの取引の`receipts[]`にも無い証憑（削除・無視済みは除く）。登録元の内訳（API一括登録／スマホ撮影）付き。メタデータ（金額・日付）があるものは、証憑なし取引と金額一致・日付±3日で**添付候補**を出す（添付自体は行わない。添付は上記④の手順で対話中に） | `GET /api/1/receipts`＋`GET /api/1/deals` |
| 口座連携の異常（要確認） | `walletables`に`sync_status`があり`synced`でないもの（実際のAPI応答にはこの項目が無い）に加え、**API連携口座（`bank_id`あり）で取得期間内に明細があるのに最新明細日が`--stall-days`（既定30日）より古いもの**（動いていた口座が止まった＝連携切れ・同期停止の代替指標。取得期間内に明細が無い口座は未使用と区別できないので報告しない）。同期ボタンは秘書が押さない。報告を受けたらオーナーがfreee画面で同期する | `GET /api/1/walletables`＋`wallet_txns` |

- **会社ごとに監視を止められる**：`companies.json`の任意項目`monitor`。`{"enabled": false}`でその会社を監視対象外にする（経理作業を担当していない会社など）。`{"receipts": false}`でファイルボックスの未添付証憑のチェックだけ止める（LINE撮影分でメタデータが無く機械照合できず、通知しても意味がない会社など）。項目が無ければ全て有効
- 同じものを毎日繰り返さないため、報告済みID・既知パターンを各インスタンスの`work/freee-monitor-state.json`に持つ（初回は全件が「新規」として出る）。ログは`<instance>/work/freee-api-post.log`とスキル側`work/monitor-wrapper.log`
- 投稿先：会社エントリの任意項目`notify_webhook_env`（`.env`のキー名、例：`"notify_webhook_env": "SLACK_WEBHOOK_SAMPLE_A"`）があればそのWebhook、無ければ`GENERAL_SLACK_WEBHOOK_URL`（全体通知チャンネル）。メンションは`.env`の`SLACK_MENTION_USER_ID`（未設定ならメンションなし）。新しいクライアントに導入する際は、会社別チャンネルのIncoming Webhookを`.env`に足してこのキーを設定するだけでよい
- 失敗（トークン失効・API障害）は全体通知チャンネルにメンション付きで通知（`freee-healthcheck.sh`と同じ型）
- 手動確認：`bash .claude/skills/freee-api-post/scripts/freee-monitor.sh --dry-run`（投稿・状態更新なし、本文を標準出力へ）。スケジューラー登録の前に必ず手動で1回成功を確認する
- 初回実行で判明しやすいこと：API経由（`origin: public_api`）で一括登録された証憑が数千件未添付のまま残っている会社がある。件数が大きい会社は「残N件」の推移で減っているかを見る使い方になる
- 実装：`lib/freee_monitor.py`（純粋関数、`lib/test_freee_monitor.py`）、`lib/freee_cli.py`の`monitor`サブコマンド（API取得・状態ファイルI/O）、`scripts/freee-monitor.sh`（Webhook投稿・失敗通知）
- **マネーフォワード側の証憑（ファイルボックス）は、mfc_ca MCPに証憑系ツールが無いため無人監視の対象外**。`mf-monthly-check`の対話手順（ブラウザ確認）で代替する

## 今後の課題

- `user_matchers`（自動登録ルール）のAPI経由での作成機能：`act=1`等、API対応可能なアクションに限り、CLIから安全にルール作成できる仕組みはまだ実装していない（手順は上記⑤）。確立パターンが複数社で増えてきたら着手する
- 監視の会社別チャンネルへの振り分けは`notify_webhook_env`で可能。会社別のIncoming Webhookを用意した時点で`companies.json`に設定する
