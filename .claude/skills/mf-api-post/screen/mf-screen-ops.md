# マネーフォワード会計の画面を claude-in-chrome で操作する手順

## マネーフォワード会計の画面を claude-in-chrome で操作するとき

APIに口が無い操作（勘定科目・決算書科目・補助科目の設定、仕訳の削除、Box に既にある証憑の付け替え）は画面操作しかない。
`accounting.moneyforward.com` は `javascript_tool` が使えるので、`find`のrefクリックより**JSで直接操作するのが速くて確実**。

### ⚠️ ネイティブダイアログが出るとページごと固まる（最重要）

削除や並べ替えの確定で `confirm()` が出ると、**スクリーンショットもJSも全てタイムアウトし、Claude側からは何もできなくなる**。
`Page.captureScreenshot` が `Script injection timed out`、`Runtime.evaluate` が45秒でタイムアウトしたら、まずこれを疑う。

- **復旧はオーナーにボタンを押してもらうしかない。** ダイアログの文面を聞いてから依頼する
- **「固まった＝操作が失敗した」ではない。** 決算書科目のドラッグが、固まる直前に成立していた例がある。
  復帰したら**必ず現在の状態を読み直してから**次を打つ（再実行すると二重に適用される）
- **予防策：クリックの直前に同じJSの中で `window.confirm = () => true; window.alert = () => {};` を仕込む**（事業者切替で検証済み。仕込まずに `/offices` の「切替」を押したらタブごと固まり、仕込んで押し直したら一発で通った）。
  **別のJS呼び出しで仕込んでも効かないことがある**（ページ遷移でwindowが作り直されるため）。押すJSと同じ本文の先頭に書く
- **画面操作を始める前に、その画面で毎回仕込んでおく**。対象は削除・並べ替え・事業者切替・インポートの確定など、確認ダイアログが出うる操作全般。
  ただし**押す対象を1つに特定してから仕込む**こと（自動でOKを返すので、意図しないダイアログまで承認してしまわないように）
- 固まってしまった後は仕込めない（JSが届かない）。そのタブは諦めて**新しいタブを開き直す**のが速い（ダイアログはタブ単位なので他のタブは動く）。
  固まったタブは `tabs_close_mcp` で閉じてよい

### 証憑の添付・付け替え（仕訳帳 `/books`）

証憑の添付は、APIの `POST /vouchers`（1件5MB・1回5件・1仕訳5件まで）にも口があるが、このスキルは読み取り専用なので **画面で行う**。
上げた証憑は電子帳簿保存法の要件で削除できない。証憑の一覧を取るAPIは無く、仕訳の `voucher_file_ids` で件数が分かるだけ。

**Box に既にある証憑を別の仕訳へ付け替えるときは、APIで上げ直さず下の画面の手順を使う**（Box に同じファイルが2つ残るため）。

**必ず「元の仕訳を削除 → 付け直し」の順で行う。** 削除すると証憑はクラウドBoxへ戻って「未添付」になり、
既定の絞り込みに入るので1クリックで選べる。逆順だと「添付済み」の中から探すことになり手数が何倍にもなる。

1. 対象行の `svg[data-icon=paperclip]` があるセルの `button` をクリック → 「クラウドBoxから選択」タブ
2. ⚠️ **アップロード日の既定は直近2ヶ月**。古い証憑は出てこないので開始日を広げる。
   jQuery datepicker なので `input.value` 代入＋`input`/`change`/`blur` の dispatch で効く
   （ReactのnativeInputValueSetterは `Illegal invocation` になる）
3. ファイル名はタイムスタンプ（`2026_07_12_21_18_16_394.jpeg`）で中身が分からない。
   行をクリックしてプレビューを出し、`computer` の `zoom` で右ペインを読んで特定する
4. チェックボックスを `click()` →「〇件を選択中」を確認 →「添付」を `click()`
5. **仕訳を読み直して `voucher_file_ids` が入ったことを確認する**（画面だけで判断しない）。`read <会社> "/journals/<仕訳ID>"`（MCPなら `getJournals`）

### 勘定科目を別の決算書科目へ移す（`/items`、URLは `/account_items` ではない）

決算書で独立表示させたい科目が別の決算書科目にぶら下がっているときに使う。**勘定科目を作り直す必要はない。**

- 構造：`ul.js-report-unit-ul[data-report-unit-item-id]`（＝決算書科目）の中に `li.item[data-id]`（＝勘定科目）が並ぶ。
  決算書科目名は各グループ先頭行の `input[name=report-unit-item-name]`
- jQuery UI sortable（`connectWith: .js-item`）なので**ドラッグで移せる**。`sortreceive`/`sortstop` は document に委譲されている
- 合成マウスイベント（`mousedown`→ 25px刻みの `mousemove` ×n →`mouseup`、`bubbles:true`・`buttons:1`）で成立する。
  掴む位置は `div.item-container`（`input`の上だと sortable の `cancel` に当たる）
- **確定時にネイティブダイアログが出てページが固まる。**上の最重要の節のとおり、押してもらってから状態を読み直す

### 補助科目を新しく作る

**APIには補助科目を作る口が無い**（`sub_accounts` は GET だけ）。画面で作る。手順書は `.claude/skills/mf-api-post/screen/create-sub-account.md`。

- 勘定科目画面は `/items` ではなく、**損益計算書側 `/pl_items?cti=…`・貸借対照表側 `/bs_items?cti=…`** に分かれている（`/items` のタブのリンクから `cti` 付きURLが取れる）
- 画面下の固定フッター（`.ca-fixed-footer-content`）の **「補助科目追加」（`div`）を `click()`** →モーダルが開く。行ごとの追加アイコンは無い
- モーダルの入力：`select#sub-item-item-id`（勘定科目。option値＝勘定科目の`data-id`）、`input#sub-item-name`、`input#sub-item-code`（検索キー）、
  税区分はReactのセレクトで、実体は hidden の `input[name="sub_item[excise_id]"]`。**値を入れた直後に hidden を書き換え、そのまま「登録」（`input[type=submit][value=登録]`）を押すと反映された**。
  税区分の内部値は、同じ勘定科目の行の `span.js-select-item-list-item-excise-id` の `data-value` から取る（会社ごとに別の値）
- 画面には成功表示が出ないので、**名前と`tax_id`を確認**してから仕訳に使う。`read <会社> "/sub_accounts"`（手順は `.claude/skills/mf-api-post/screen/create-sub-account.md` の手順4。MCPなら `getSubAccounts`）

### 勘定科目・決算書科目を新しく作る

補助科目と同じ `/pl_items`・`/bs_items` のフッターに「決算書科目追加」「勘定科目追加」もある（いずれも `span.mf-ml5`、押すのは親要素）。

- **「決算書科目追加」のモーダルは、決算書科目と勘定科目を1回で作れる**。既存の決算書科目にぶら下げるだけなら「勘定科目追加」でよい
  - `select#report-unit-category-id`（分類。営業外費用・販売費及び一般管理費など）
  - `input#report-unit-item-report-unit-item-name`（決算書科目名）
  - `input#report-unit-item-item-name`（勘定科目名）
  - hidden `input[name="item[excise_id]"]`（税区分）、`input#report-unit-item-item-code`（検索キー）
- 「勘定科目追加」のモーダルは `select#item-report-unit-item-id`（決算書科目を`optgroup`＝分類つきで選ぶ）＋ `input#item-name` ＋ hidden `input[name="item[excise_id]"]`
- テキスト入力はReactのネイティブsetter（`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set`）で入れて `input`/`change` をdispatchする。
  分類のselectは `jQuery(el).val(v).trigger('change')`
- 税区分の内部値は、同じ画面の既存科目の行の `span.js-select-item-list-item-excise-id` の `data-value` から取る（会社ごとに別の値）
- **作成後は必ず `read <会社> "/accounts"`（MCPなら `getAccounts`）で確認する**（存在・`category`・`tax_id`・補助科目の紐付き・重複がないこと）。画面には成功表示が出ない

### ⚠️ インポート画面の「追加」で科目を作ろうとしない

仕訳帳インポートのstep3「勘定科目の設定」には、マッチしなかった科目を「追加」「変換」するUIがあるが、
**「実行」を押しても「追加中」のまま止まって完了しないことがある**（科目は作られなかった例がある）。
原因は、途中でブラウザのMF事業者が別会社に戻っており、リクエストが別事業者へ飛んでいたこと。

- **インポートを始める前に、その画面で事業者名と会計年度を必ず読む**。表示はページを開いた時点のもので、
  **セッション側の事業者はあとから変わりうる**（別タブ・別セッションの操作で変わる）。
  MFは「別のPCやタブでログイン中の会計期間もしくは事業者が変更された可能性があります」と出す
- **不足している勘定科目・補助科目は、インポートを始める前に `/pl_items`・`/bs_items` で先に作っておく**。
  step3では何も追加せず「次へ」だけを押す形にすると事故が減る
- **`fetch('/items')` の `itemNameMaster` でその会社の科目の有無を判定してはいけない**。
  この配列はインポート変換用の共通マスタで、**別会社でも同じ並びが返る**（これで「作成された」と誤判定しかけた例がある）。科目の有無は**`read <会社> "/accounts"`（MCPなら `getAccounts`）**、または `/pl_items` の
  `input[name="name"]` の値で確かめる
