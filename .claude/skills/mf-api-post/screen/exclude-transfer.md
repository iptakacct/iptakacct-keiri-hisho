# 口座間振替のもう片方の明細を「対象外」にする（画面操作）

自社の口座Aから口座Bへ資金を移すと、**両方の口座に明細が出る**。片方を
「借 普通預金(B)／貸 普通預金(A)」で登録すると、もう片方は登録してはいけない
（登録すると預金が二重に増える）。

**MFの「自動で仕訳」に「別の明細と振替として紐付ける」機能は無い。**
もう片方を「対象外」にするのが標準の処理。APIにも口が無いので画面で行う
（このスキルは読み取り専用）。

## 手順（claude-in-chrome）

1. `https://accounting.moneyforward.com/offices` を開き、対象の会社・年度へ「切替」
   - ⚠️ **同じJS本文の先頭で `window.confirm=()=>true; window.alert=()=>{}` を仕込む**
     （ネイティブダイアログでタブが固まる。`mf-screen-ops.md`）
   - 明細の日付が属する**会計年度**を選ぶ（9月決算なら9/25の明細は「2025年度」）
2. `https://accounting.moneyforward.com/transaction_journals?cti=<切替後のcti>` を開く
   （未仕訳の一覧。`/transactions` は404）
3. 対象の行を**金額の input の value から辿る**。行のテキスト検索は効かない
   （金額・摘要が input の中にあり `innerText` に出ないため）

   ```js
   const inp = [...document.querySelectorAll('input')].find(i => i.value === '18,000,000');
   const row = inp.closest('tr');
   ```

4. 行の「詳細」を押して編集パネルを開く
5. ⛔ **「自動仕訳ルールとして保存」のチェックを外す**。既定でオンになっており、
   外さずに「対象外」を押すと**今後その摘要の明細がすべて対象外になる**
   （本物の入金まで消えてしまう）

   ```js
   const l = [...document.querySelectorAll('label')].find(x => x.innerText.includes('自動仕訳ルールとして保存'));
   const cb = l.querySelector('input[type=checkbox]');
   if (cb.checked) cb.click();
   ```

6. パネル右下の「対象外」を押す。**`button[class*="ignoreButton"]` で特定する**

   ```js
   document.querySelector('button[class*="ignoreButton"]').click();
   ```

   ⚠️ 「対象外」という文字列でボタンを探すと、**税区分の選択ボタン（値が「対象外」）**に
   当たる。幅が最大のものを選ぶ実装にしたら税区分のドロップダウンを開いてしまった
   （Escで閉じて実害なし）。クラス名で特定すること
7. **APIで確認する**：`read <会社> "/transactions?start_date=...&end_date=..." --all-pages` を取り、
   その明細の `journalizing_status` が `excluded` になっていること

## 実例

ある会社で、同日に別口座から同額が出金され、その明細が両建てで登録済みだった入金（摘要が自社名義の振込）について、
受け側を「対象外」にして未仕訳をゼロにした。
