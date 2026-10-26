# 補助科目を新しく作る（画面操作、claude-in-chrome）

MF会計APIには補助科目を作る口が無い（`sub_accounts` は GET だけ）ので、画面で作る。
画面操作の共通の注意は `mf-screen-ops.md` を参照。

## 始める前に

1. **作ってよいか、オーナーに一言確認する**（同じ相手が別名で二重に登録されると名寄せが壊れる）
2. 既に無いかを API で確かめる：

       python .claude/skills/mf-api-post/lib/mf_cli.py --companies-json .claude/skills/mf-api-post/companies.json --repo-root . \
         read <会社> "/sub_accounts"

   保存先は既定で `<インスタンス>/work/mf-read/`（`read` の既定の保存先。出力の最後に保存したパスが出る）。一時的に置くだけなら `--out` でスクラッチパッドを指定する。

3. 同じ科目の税区分の内部値（画面の `data-value`）を後で使うので、ぶら下げる勘定科目の名前を決めておく

## 1. 画面の事業者を確かめる

`https://accounting.moneyforward.com/pl_items`（損益計算書側）か `/bs_items`（貸借対照表側）を開き、画面上部の事業者名を読む。
指定した会社と違えば `/offices` から切り替える。切り替えの「切替」を押すのと**同じJSの先頭で**ダイアログを仕込む（仕込まないとタブごと固まる）：

```js
window.confirm=()=>true;window.alert=()=>{};(()=>{
const name='株式会社サンプルA';
const n=s=>(s||'').normalize('NFKC').replace(/\s+/g,'');
// 会社名セルを前方一致＋直後の"("で探す（部分一致だと「株式会社サンプル」が「株式会社サンプルテクノロジーズ」に当たる）
const cell=[...document.querySelectorAll('td,th')].find(c=>n(c.innerText).startsWith(n(name)+'('));
if(!cell)return 'NOT_LISTED';
// 会社名セルは rowspan で年度分の行をまとめて持つ。その塊だけを候補にする
const span=parseInt(cell.getAttribute('rowspan')||'1',10);
let tr=cell.closest('tr');const trs=[tr];
while(trs.length<span&&tr.nextElementSibling){tr=tr.nextElementSibling;trs.push(tr);}
// 「次年度」の切替は新しい年度を作ってしまうので押さない
const rows=trs.filter(r=>!n(r.innerText).includes(n('次年度')));
if(rows.some(r=>n(r.innerText).includes(n('現在の事業者・年度'))))return 'ALREADY_CURRENT';
const target=rows[rows.length-1];
const a=target?[...target.querySelectorAll('a,button')].find(x=>n(x.innerText)==='切替'):null;
if(!a)return 'NO_SWITCH_LINK';
a.click();return 'SWITCHED';})()
```

押す条件は、会社名の前方一致＋「(」、rowspan の塊、「次年度」を除外、「現在の事業者・年度」なら押さない。
`NOT_LISTED` なら、MFにログインしているアカウントでは見えない会社（その会社が見えるアカウントでログインし直してもらう）。

切り替えたら、もう一度 `/pl_items`（または `/bs_items`）を開いて事業者名を読み直す。**部分一致ではなく、正規化して完全一致（または直後が「(」）で比べる**：

```js
const name='株式会社サンプルA';
const n=s=>(s||'').normalize('NFKC').replace(/\s+/g,'');
const el=document.querySelector('.js-header-dropdown-office .truncate-with-ellipsis');
const office=n(el?el.innerText:'');
(office===n(name)||office.startsWith(n(name)+'('))?'OK':'MISMATCH '+office;
```

`MISMATCH` なら先へ進まない（別の会社に補助科目を作ってしまう）。`/items` のタブのリンクから `cti` 付きのURLが取れる。

## 2. 税区分の内部値を取る

同じ勘定科目の行の `span.js-select-item-list-item-excise-id` の `data-value` を読む：

```js
const acc='支払手数料';
const row=[...document.querySelectorAll('li.item, tr')].find(r=>(r.querySelector('input[name="name"]')||{}).value===acc);
row&&row.querySelector('span.js-select-item-list-item-excise-id')?.dataset.value;
```

## 3. モーダルを開いて入力し、登録する

画面下の固定フッター（`.ca-fixed-footer-content`）の「補助科目追加」を押し、モーダルに入れて「登録」を押す。
**1回のJSで、入力と hidden の税区分の書き換えと登録までを続けて行う**（税区分はReactのセレクトで、実体は hidden の `input[name="sub_item[excise_id]"]`）：

```js
[...document.querySelectorAll('.ca-fixed-footer-content div')].find(d=>d.innerText.trim()==='補助科目追加').click();
```

```js
const setv=(el,v)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,v);
  el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};
const ACCOUNT_ID='<勘定科目の data-id>', NAME='<補助科目名>', CODE='<検索キー>', EXCISE='<手順2の data-value>';
jQuery('#sub-item-item-id').val(ACCOUNT_ID).trigger('change');
setv(document.querySelector('#sub-item-name'), NAME);
setv(document.querySelector('#sub-item-code'), CODE);
document.querySelector('input[name="sub_item[excise_id]"]').value=EXCISE;
document.querySelector('input[type=submit][value=登録]').click();
'SUBMITTED';
```

画面には成功表示が出ない。

## 4. できたことを API で確かめる

    python .claude/skills/mf-api-post/lib/mf_cli.py --companies-json .claude/skills/mf-api-post/companies.json --repo-root . \
      read <会社> "/sub_accounts" --out <スクラッチパッド>/subs-after.json
    python -c "import json,sys; d=json.load(open(sys.argv[2],encoding='utf-8')); hits=[s for s in d.get('sub_accounts',[]) if s.get('name')==sys.argv[1]]; print(len(hits), hits)" "<補助科目名>" "<スクラッチパッド>/subs-after.json"

確かめること：

- ちょうど1件（0件なら登録に失敗、2件以上なら重複。重複は削除が要るので、オーナーに報告する）
- `account_id` がぶら下げたい勘定科目の ID
- `tax_id` が狙いの税区分（`read <会社> "/taxes"` で名前を確かめる）

## 固まったとき

スクリーンショットもJSも45秒でタイムアウトしたら、ネイティブダイアログが出ている。そのタブは諦めて新しいタブを開き直し、
**いまの状態を読み直してから**続ける（固まる直前に登録が成立していることがある）。
