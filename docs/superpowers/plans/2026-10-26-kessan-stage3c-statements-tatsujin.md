# kessan 段階3c：決算書・内訳書／概況書の材料・達人用XML4種 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 決算整理の済んだ帳簿から、決算書（貸借対照表・損益計算書・株主資本等変動計算書）と個別注記表の雛形、内訳書・概況書の材料を Excel で出し、達人シリーズが取り込める中間ファイル（決算書 HJ21・内訳書 UG13・概況書 UG16・消費税 SY35 の Shift_JIS XML）を出す。最初に 3b の持ち越し3件を片づける。

**Architecture:** 集計は `financials.py`（決算書）、`uchiwake.py`（内訳書）、`gaikyo.py`（概況書）が `ledger.compute_balances` と journal＋adjustments の行から行い、Excel の材料と XML の両方がその結果を使う（数字の出どころを1か所にする）。XML は `tatsujin_xml.py` が書く：要素の並びが固定の SY35・UG16 は `config/tatsujin/*-skeleton.xml`（会計ソフトの実物から値を消した雛形、コミット済み）を読んで値を差し込み、科目で中身が変わる HJ21・UG13 はコードで組み立てる。書き出しは文字列で組み立てて実物の書式（`<X></X>`、項目の無い区分は `Amount="0"/>`、CRLF、2字下げ）に合わせ、cp932 で厳密にエンコードする。CLI は `statements` と `export-tatsujin` の2つ。

**Tech Stack:** Python 3、pyyaml、openpyxl、pytest（既存と同じ。XMLの書き出しに外部ライブラリは足さない。雛形の読み込みは `xml.etree.ElementTree`）

**Spec:** `docs/superpowers/specs/2026-10-26-kessan-stage3-closing-design.md`（§5・§6・§7・§9 が本計画の範囲）。XMLの実物の構造は `docs/kessan/tatsujin-import.md` §3.5 と `config/tatsujin/hj21-skeleton.xml`・`sy35-skeleton.xml`・`ug16-skeleton.xml`

## Global Constraints

- コードは `.claude/scripts/kessan/`、テストは `.claude/scripts/kessan/tests/`。`cd .claude/scripts/kessan && python -m pytest -q` で全件（開始時 796 件）が通ること。**commit の直前に全件をもう一度流す**（最後のテストの後に直した行で構文エラーのまま commit した事故が3bであった）
- 既存の書き方に合わせる：利用者に伝えるエラーは `KessanError`（日本語）、CSVはBOM付きUTF-8、書き込みは `ensure_writable` → 書き込み、全部組み立ててから一括で書く（途中まで書いた状態を作らない）。Excel は openpyxl、保存失敗は「Excelで開いていたら閉じてから再実行」
- **検算（`check.run_checks`）にNGが1件でもあれば `statements`・`export-tatsujin` は実行を拒否する**（NGの件数と `output/check-result.md` を示す。spec §7）
- 金額は円の整数。0・該当なしの値は空要素（実物どおり）。XML の文字コードは cp932 で、表せない文字があれば何も書かずに `KessanError`（どの科目・補助・取引先か、どの文字かを示す。勝手に置き換えない）
- `SYSTEM`：`GYOUMUPREFIX`（HJ21／UG13／UG16／SY35）、`VERSION` 1、`FUNCTIONID`（3／1／2／1）、`CONNECTEDPROGRAMVER`・`PROGRAMVER` は `1.0.0`、`PROGRAMNAME` は `kessan`。`BASIC`：`KOMONSAKINAME` に会社名、`JIGYOUNENDOFROM`／`TO` に期首日・期末日（YYYYMMDD）
- MF・freee の実物XMLをリポジトリ・テストに置かない（構造は雛形と tatsujin-import.md だけ）。実クライアントの情報をテスト・fixture に入れない（leak-scan 対象。英単語の一部が固有語に当たることがあるので、テスト名は leak-scan を通してから commit）
- 各タスクの最後に、全件テスト → `codex-review` → commit（`kessan: ` で始める日本語、末尾に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）→ `git push origin master`

## Review Focus

1. **cp932 で表せない文字**（科目名・補助・取引先に絵文字や `𠮷`）があると、XML を1つも書かずに、どの科目・補助のどの文字かを示して止まること → Task 4 `test_cp932_unencodable_names_abort`
2. **逆残高・控除科目**：減価償却累計額は資産の部でマイナスの項目として出し区分合計から引く、未払金などが逆残高ならマイナスで出す、貸借一致の検算がそれでも通ること → Task 3 `test_contra_and_negative_balances`
3. **期首が4月以外・12か月未満の事業年度**：概況書の月別は期首月から数え（`TUKIBETU_01` が期首の月の数字）、期間外の月は空にする → Task 5 `test_monthly_short_year_and_odd_start`
4. **法人税等が未計上・決算整理の候補が残っている**状態でも、決算書と XML は出せる（達人で税額を計算するのに要る）が、警告を出す → Task 3 `test_statements_warn_pending_and_no_income_tax`
5. **名前に `&`・`<`・`"` を含む科目・補助**が XML でエスケープされ、雛形にない要素名へ値を入れようとしたらプログラムの誤りとして止まること → Task 4 `test_xml_escapes_names`、`test_fill_skeleton_rejects_unknown_tag`

---

### Task 1: 3b の持ち越し3件

**Files:**
- Modify: `evidence_resolve.py`、`close_payable.py`（所見の文言）、`close_depreciation.py`、`close.py`
- Test: `tests/test_evidence_resolve.py`、`tests/test_close_payable.py`、`tests/test_close_depreciation.py`、`tests/test_close.py`

**決めたこと:**
- 持ち越し1（税率混在の未払の請求書）：`resolve-evidence --as-new` は、状態が `未払候補` で支払方法が決まらない（`credit_for_new_entry` が None：後払い・不明）とき、拒否せずに貸方を `未払金`（補助＝証憑の取引先）にした新規仕訳の候補を staging に作る。日付は証憑の日付。状態は従来どおり `新規仕訳`（以後 close_payable の未払の候補にならない）。`複数候補` の証憑で支払方法が決まらないときは従来どおり拒否。close_payable の混在税率の所見は「resolve-evidence --as-new で未払金の仕訳にしてから split-tax --id で分け、approve・post する（翌期の支払は未払金の消込にする）」に直す
- 持ち越し2（無形固定資産の1円）：表示区分が `無形固定資産` の科目の定額法は、上限を `取得価額 − 前期末償却累計額`（0円まで）にする。無形固定資産に定率法が書かれていたら台帳の検証で problem（「無形固定資産は定額法だけ」）
- 持ち越し3：①報告書（close-report.md）の登録済みの候補の表は adjustments.csv の行の内容を出し、要確認理由の欄に再計算の理由を混ぜない（再計算の結果は従来どおり下に別表で並べる）②登録済みと再計算の比較（`_shape`）から税の列（`TAX_COLUMNS`）を外す。`set-tax` で税の列だけ直した決算整理仕訳に「違います」の所見を出し続けないため。科目・補助・金額・日付が違えば従来どおり所見を出す

- [ ] **Step 1: 失敗するテスト** — `test_as_new_unpaid_deferred_payment_credits_payable`（未払候補・支払方法「後払い」の証憑 → staging に 貸方 未払金（補助＝取引先）、証憑の状態 新規仕訳、続けて `split_tax` で 10%／8% に分けられる）、`test_as_new_multiple_without_payment_still_refused`、`test_mixed_rate_payable_note_points_to_as_new_payable`、`test_intangible_straight_line_to_zero`（ソフトウエア 取得価額 300,000・定額法5年・前期末累計 240,000、12か月 → 当期 60,000、帳簿価額0）、`test_intangible_declining_rejected`、`test_report_posted_table_shows_adjustments_rows`、`test_set_tax_on_posted_close_voucher_no_mismatch_note`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 2: 科目マスタの表示区分の検証・仕入高・概況書項目、会社名

**Files:**
- Modify: `accounts.py`、`accounts-master.csv`、`common.py`（`init_year_dir`）、`cli.py`（`init --company-name`）、`テンプレート/context/company/kessan-accounts.yaml`（コメントに `概況書項目` の書き方）
- Test: `tests/test_accounts.py`、`tests/test_common.py`、`tests/test_cli.py`

**Interfaces:**
- Produces: `accounts.SECTIONS`（表示区分 → 科目の区分の対応。dict）：
  - 資産：`流動資産`、`有形固定資産`、`無形固定資産`、`投資その他の資産`、`繰延資産`
  - 負債：`流動負債`、`固定負債`
  - 純資産：`株主資本`、`評価・換算差額等`
  - 収益：`売上高`、`営業外収益`、`特別利益`
  - 費用：`売上原価`、`販売費及び一般管理費`、`営業外費用`、`特別損失`、`法人税等`
  `_validate` で表示区分が `SECTIONS` に無い・区分と合わない（例：区分 資産 で表示区分 流動負債）ものをエラーにする（マスタも会社別の科目も）
- Produces: `Account.gaikyo: str`（`概況書項目` 列。空可）。値は `accounts.GAIKYO_TAGS` のどれか：`現金預金`、`受取手形`、`売掛金`、`棚卸資産`、`貸付金`、`建物`、`機械装置`、`車両船舶`、`土地`、`支払手形`、`買掛金`、`個人借入金`、`その他借入金`、`仕入`、`労務費`、`外注費`、`役員報酬`、`従業員給料`、`人件費`、`交際費`、`減価償却費`、`地代家賃`、`租税公課`、`支払利息`。違う値はエラー。会社別の科目（kessan-accounts.yaml）も `概況書項目:` で書ける
- マスタに足す行：`仕入高,費用,借,売上原価,課税仕入10%`（`売上高` の次、`役員報酬` の前に置く）
- マスタの `概況書項目`：現金・普通預金・定期預金→現金預金、売掛金→売掛金、役員貸付金→貸付金、建物・建物附属設備→建物、車両運搬具→車両船舶、土地→土地、買掛金→買掛金、短期借入金・長期借入金→その他借入金、役員借入金→個人借入金、仕入高→仕入、役員報酬→役員報酬、給料手当→従業員給料、法定福利費・福利厚生費→人件費、外注費→外注費、交際費→交際費、減価償却費→減価償却費、地代家賃→地代家賃、租税公課→租税公課、支払利息→支払利息
- Produces: `period.yaml` の `会社名`（任意の文字列）。`init --company-name <名前>` で書く（既存の period.yaml にも足せる。省略時は変えない）。`common.load_company_name(year_dir) -> str`（無ければ `""`）

- [ ] **Step 1: 失敗するテスト** — `test_section_must_be_known`、`test_section_must_match_category`、`test_extra_account_section_validated`、`test_gaikyo_tag_validated`、`test_master_has_purchase_and_tags`、`test_init_company_name`（新規・既存の period.yaml の両方、他のキーは変えない）、`test_load_company_name_missing_is_blank`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**（既存の会社別科目のテストで表示区分が不正なものがあれば直す）
- [ ] **Step 5: codex-review → Commit → push**

### Task 3: 決算書（financials.py）と `statements` コマンド

**Files:**
- Create: `.claude/scripts/kessan/financials.py`
- Modify: `cli.py`
- Test: `tests/test_financials.py`（新規）、`tests/test_cli.py`

**Interfaces:**
- Consumes: `ledger.compute_balances`、`accounts.SECTIONS`、`common.load_period`、`common.load_company_name`
- Produces: `financials.build_statements(year_dir, accounts) -> Statements`
  - `Statements`：`period`（期首日, 期末日）、`company`、`bs: list[Section]`、`pl: list[Section]`、`profits: dict[str, int]`（`売上総利益`・`営業利益`・`経常利益`・`税引前当期純利益`・`当期純利益`）、`equity: list[EquityRow]`、`warnings: list[str]`
  - `Section`：`name`（表示区分）、`items: list[tuple[str, int]]`（科目名, 金額）、`total: int`
  - `EquityRow`：`account`、`opening`、`changes: list[tuple[str, int]]`（理由, 金額）、`closing`
- 集計の規則：
  - 科目ごとに補助を合計した期末残高（正常残高側を＋、逆残高はマイナスのまま）。期末残高0の科目は `items` に出さない。並びは科目マスタの順
  - 控除科目（科目の区分と正常残高が逆：資産で正常残高が貸＝減価償却累計額）は、マイナスにして同じ区分の `items` に入れる。区分合計は items の合計
  - BS の区分は `SECTIONS` の資産・負債・純資産の順、PL は 売上高 → 売上原価 → 販売費及び一般管理費 → 営業外収益 → 営業外費用 → 特別利益 → 特別損失 → 法人税等
  - 段階利益：売上総利益＝売上高−売上原価、営業利益＝売上総利益−販管費、経常利益＝営業利益＋営業外収益−営業外費用、税引前当期純利益＝経常利益＋特別利益−特別損失、当期純利益＝税引前−法人税等
  - BS の純資産の `繰越利益剰余金` は、帳簿の期末残高（期首＋仕訳での増減）に当期純利益を足した額（損益の科目は振り替えないため）
  - 株主資本等変動計算書：純資産の科目ごとに、期首残高（opening-balances.csv）、変動（`繰越利益剰余金` は理由 `当期純利益`。他に純資産の科目を動かした仕訳があれば、同じ摘要ごとに合計して理由＝摘要。摘要が空なら `その他`）、期末残高。期首・変動・期末がすべて0の科目は出さない
  - 検算：資産の部合計＝負債の部合計＋純資産の部合計、PL の当期純利益＝変動計算書の `繰越利益剰余金` の `当期純利益`。合わなければ `KessanError`（差額を示す）で何も出さない
  - 警告：`adjustments-staging.csv` に未登録・未却下の候補が残っている（件数）、`法人税等` の科目が0（「法人税等が未計上。達人で計算した額を close-set-amount で入れて close-post した後に出し直す」）、会社名が空
- Produces: `financials.write_statements(year_dir, st) -> list[Path]`：
  - `output/financial-statements.xlsx`：シート `貸借対照表`・`損益計算書`・`株主資本等変動計算書`。1行目に会社名と期間、区分の見出し行・科目行・区分合計行（太字）、PL は区分の後に段階利益の行
  - `output/notes-draft.md`（個別注記表の雛形）：重要な会計方針（固定資産の減価償却の方法＝`fixed-assets.csv` に出てくる償却方法の一覧、無ければ「該当なし」。消費税等の会計処理＝課税の年度は「税込方式」、免税の年度は「消費税の納税義務がないため、税込みで処理」）、株主資本等変動計算書に関する注記（発行済株式の種類・数は「（記入）」）、「最終はオーナーが直す」旨
- CLI：`statements --year-dir <Y> [--prev-year-dir]` → 検算NGなら拒否（終了コード1）、警告を表示して出力のパスを出す

- [ ] **Step 1: 失敗するテスト** — 架空の会社（期首残高：普通預金・資本金・繰越利益剰余金、売上・外注費・減価償却・法人税等の仕訳、減価償却累計額あり）で手計算の正解をテストの docstring に書いて照合：`test_balance_sheet_sections_and_totals`、`test_income_statement_stage_profits`、`test_retained_earnings_includes_net_income`、`test_equity_statement_changes`（配当の仕訳 摘要「剰余金の配当」を含む）、`test_contra_and_negative_balances`、`test_zero_accounts_omitted`、`test_mismatch_raises`（検算が合わない状態をモンキーパッチで作る）、`test_statements_warn_pending_and_no_income_tax`、`test_write_statements_sheets`（3シートの見出しと合計行を openpyxl で読む）、`test_notes_draft_methods`、`test_cli_statements_refuses_on_ng`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 4: 達人XMLの書き出し（tatsujin_xml.py）と決算書XML（HJ21）、`export-tatsujin`

**Files:**
- Create: `.claude/scripts/kessan/tatsujin_xml.py`
- Modify: `cli.py`
- Test: `tests/test_tatsujin_xml.py`（新規）、`tests/test_cli.py`

**Interfaces:**
- Produces: `tatsujin_xml.Node`（`tag: str`、`attrs: dict[str, str]`（並び順を保つ）、`text: str | None`、`children: list[Node]`）と `render(root: Node) -> bytes`：
  - 先頭 `<?xml version="1.0" encoding="Shift_JIS"?>`、改行は CRLF、2字下げ
  - 子もテキストも無い要素：属性があれば `<Tag A="…"/>`（例 `<IntangibleAssets Label="無形固定資産" Amount="0"/>`）、属性が無ければ `<Tag></Tag>`
  - テキスト・属性値の `&` `<` `>` `"` をエスケープ
  - cp932 に厳密エンコード。失敗したら `KessanError`（`render` は呼び出し側が付けた `where`＝科目・補助の名前を出せるよう、エンコード失敗した文字とその文字を含むテキストを示す）
- Produces: `tatsujin_xml.load_skeleton(name) -> Node`（`config/tatsujin/<name>-skeleton.xml` を読む。コメントは捨てる）と `fill_skeleton(root, values: dict[str, int | str], section=None) -> Node`：値を要素名で差し込む（同じ要素名が複数の区分にあるSY35は `section` で親要素を限定する形 `{(区分, 要素名): 値}` も受ける）。雛形に無い要素名が来たら `KessanError`（プログラムの誤り）。0 は空にする
- Produces: `tatsujin_xml.header(prefix, function_id, company, period) -> tuple[Node, Node]`（`SYSTEM` と `BASIC` の共通部分）
- Produces: `tatsujin_xml.build_hj21(st: Statements) -> Node`：
  - `BASIC/PATTERN` は hj21 雛形の値（A2・B5・C2・D5・E1・F3・G1）をそのまま使う（`period.yaml` で変える仕組みは、各コードの意味を実機で確かめてから。下の「判断」）
  - `DATA/BalanceSheet`：`Assets`（資産の部）＞`CurrentAssets`・`NoncurrentAssets`（＞`PropertyPlantAndEquipment`・`IntangibleAssets`・`InvestmentsAndOtherAssets`）・`DeferredAssets`、`Liabilities`＞`CurrentLiabilities`・`NoncurrentLiabilities`、`NetAssets`。要素名と Label は hj21 雛形どおり。各区分に `Amount`＝区分合計、その下に `<Item Label="科目名">金額</Item>`。親（Assets・NoncurrentAssets・Liabilities）の Amount は子の合計。`株主資本` と `評価・換算差額等` の科目はどちらも `NetAssets` の Item
  - `DATA/IncomeStatement`：雛形の13要素の順。`NetSales`←売上高、`CostOfSales`←売上原価、`SellingGeneralAndAdministrativeExpenses`←販管費、`NonOperatingIncome`／`NonOperatingExpenses`／`ExtraordinaryIncome`／`ExtraordinaryLoss`／`IncomeTaxes` は同名の区分、`GrossProfit`・`OperatingIncome`・`OrdinaryIncome`・`IncomeBeforeIncomeTaxes`・`NetIncome` は段階利益を Amount に入れる（Item なし）
  - `DATA/StatementChangesNetAssets`：`<Select>1</Select>` と、`Statements.equity` の行ごとの `ChangesNetAssetsItem`（`AccountList`・`BalanceEndPreviousPeriod`・変動があるときだけ `ChangesItemsDuringPeriod`（`TotalChangesItemsDuringPeriod` と `ChangesItem`（`Reason`・`Amount`））・`BalanceEndCurrentPeriod`）。並びは tatsujin-import.md §3.5 の実物どおり
- Produces: `tatsujin_xml.export(year_dir, accounts, kinds: list[str] | None) -> list[Path]`：本タスクでは `kessan` だけ。後のタスクが `uchiwake`・`gaikyo`・`shohizei` を足す。指定された種類を全部組み立ててから、`output/tatsujin/<種類>-<GYOUMUPREFIX>-<期末日YYYYMMDD>.xml`（例 `kessan-HJ21-20260331.xml`）に一時ファイル＋置き換えで書く（1つでも組み立てに失敗したら何も書かない）
- CLI：`export-tatsujin --year-dir <Y> [--kind kessan uchiwake gaikyo shohizei] [--prev-year-dir]`（`--kind` 省略時は該当する全部）。検算NGなら拒否。`Statements.warnings` を表示する

**判断（ledger に Ruling として残す）:** PATTERN は設定にしない（spec §6.2 の「period.yaml で変えられる」は、コードの意味が分からないうちは誤った値を入れる手段にしかならないため。実機確認後に足す）

- [ ] **Step 1: 失敗するテスト** — `test_render_format`（CRLF、`<X></X>`、`Amount="0"/>`、Shift_JIS 宣言、2字下げ。期待するバイト列と完全一致）、`test_xml_escapes_names`（`A&B<商事>"` の補助）、`test_cp932_unencodable_names_abort`（科目 `𠮷田商店` → 何も書かず、科目名と文字を示す）、`test_fill_skeleton_rejects_unknown_tag`、`test_hj21_structure`（Task 3 の会社で、区分の Amount・Item・段階利益・変動計算書が手計算どおり。`ElementTree` で cp932 デコードして読む）、`test_hj21_depreciation_accumulated_is_negative_item`、`test_export_writes_nothing_on_failure`、`test_cli_export_refuses_on_ng`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 5: 内訳書・概況書の材料と XML（UG13・UG16）

**Files:**
- Create: `.claude/scripts/kessan/uchiwake.py`、`gaikyo.py`
- Modify: `tatsujin_xml.py`（`build_ug13`・`build_ug16`、`export` に `uchiwake`・`gaikyo`）、`cli.py`（`statements` が材料も出す）
- Test: `tests/test_uchiwake.py`、`tests/test_gaikyo.py`（新規）、`tests/test_tatsujin_xml.py`

**Interfaces:**
- Produces: `uchiwake.build(year_dir, accounts) -> list[UchiwakeAccount]`（`code: str`、`name`、`balance: int`、`subs: list[tuple[code, name, balance]]`）
  - 科目マスタ（会社別の科目を含む）の全科目を、マスタの順で出す。`code` は並びの1からの通し番号（`str(i)`）、補助の code は `f"{i}{j:03d}"`（j は補助の名前順の1からの番号）
  - 残高は期末残高（正常残高側を＋、逆残高はマイナス）。損益の科目は当期の発生額（＝期末残高）。補助が空の行は科目の残高にだけ入れる。残高0の科目は出すが金額は空（実物どおり）、残高0の補助は出さない
  - 材料 `output/uchiwake-material.xlsx`：列 `内訳書の種類`・`科目`・`補助`・`期末残高`。内訳書の種類は科目名からの対応表（`現金`・`普通預金`・`定期預金`→預貯金等、`売掛金`・`未収入金`→売掛金（未収入金）、`仮払金`・`前払費用`→仮払金（前渡金）、`役員貸付金`→貸付金及び受取利息、`買掛金`・`未払金`・`未払費用`→買掛金（未払金・未払費用）、`前受金`・`預り金`・`仮受金`→仮受金（前受金・預り金）、`短期借入金`・`長期借入金`・`役員借入金`→借入金及び支払利子、`役員報酬`→役員給与等、`地代家賃`→地代家賃等、`雑収入`・`雑損失`→雑益、雑損失等、`有価証券`・`投資有価証券`→有価証券、建物〜ソフトウエア→固定資産（土地、土地の上に存する権利及び建物に限る）は `土地`・`建物` だけ。それ以外は空）。内訳書に載る科目だけのシートと、全科目のシートの2枚
- Produces: `uchiwake` → `tatsujin_xml.build_ug13(...)`：`DATA` に `KANJYOKAMOKU Count="n"`（`KANJYOKAMOKU_CODE`・`_NAME`・`_ZANDAKA`、`HOJYOKAMOKU Count="m"`（`HOJYOKAMOKU_CODE`・`_NAME`・`_ZANDAKA`））。tatsujin-import.md §3.5 の実物どおり
- Produces: `gaikyo.build(year_dir, accounts, st: Statements) -> Gaikyo`（`main: dict[str, int]`＝ug16 雛形の `SYUYOUKAMOKU` の要素名→金額、`monthly: list[dict[str, int]]`＝12か月分、各要素名→金額）
  - 区分から：`URIAGE_SYUUNYUUDAKA`＝売上高、`URIAGE_SYUUNYUUGENKA`＝売上原価、`URIAGE_SYUUNYUUSOURIEKI`＝売上総利益、`EIGYOUSONEKI`＝営業利益、`TOKUBETURIEKI`・`TOKUBETUSONSITU`＝特別利益・特別損失、`ZEIBIKIMAETOUKISONEKI`＝税引前当期純利益、`SISANNOBU`・`FUSAINOBU`・`SIHONNOBU`＝資産・負債・純資産の部合計
  - 概況書項目から（同じ項目の科目の期末残高の合計）：現金預金→`GENKINYOKIN`、受取手形→`UKETORITEGATA`、売掛金→`URIKAKEKIN`、棚卸資産→`TANAOROSISISAN`、貸付金→`KASITUKEKIN`、建物→`TATEMONO`、機械装置→`KIKAISOUTI`、車両船舶→`SYARYOU_SENPAKU`、土地→`TOTI`、支払手形→`SIHARAITEGATA`、買掛金→`KAIKAKEKIN`、個人借入金→`KOJINKARIIREKIN`、その他借入金→`SONOTAKARIIREKIN`、仕入→`GENZAIRYOUHI_SIIREDAKA`、労務費→`ROUMUHI`、外注費（売上原価の科目）→`GAICHUUHI`、役員報酬→`YAKUINHOUSYUU`、従業員給料→`JYUUGYOUINKYUURYOU`、交際費→`KOUSAIHI`、支払利息→`SIHARAIRISOKUWARIBIKIRYOU`、減価償却費→売上原価の科目なら `GENKASYOUKYAKUHI_URIAGEGENKA`・販管費なら `GENKASYOUKYAKUHI_HANKANHI`、地代家賃→同様に `TIDAIYATIN_URIAGEGENKA`／`TIDAIYATIN_HANKANHI`、地代家賃＋租税公課→`TIDAIYATIN_SOZEIKOUKA_URIAGEGENKA`／`TIDAIYATIN_SOZEIKOUKA_HANKANHI`。他の要素（兼業売上・期首期末棚卸・`人件費`）は空
  - 月別（期首月から `_01`〜`_12`）：`TUKIBETU_nn`＝その月の数字（1〜12）、`URIAGESYUUNYUUKINGAKU1_nn`＝売上高の区分の科目のその月の発生額（貸方−借方）、`SIIREKINGAKU1_nn`＝概況書項目 仕入、`GAICHUUHI_nn`＝外注費、`JINKENHI_nn`＝役員報酬・従業員給料・人件費、`GENSENTYOUSYUUZEIGAKU_nn`＝`預り金` のうち補助に「源泉」を含む行の貸方の合計。費用は借方−貸方。journal と adjustments の両方を日付の月で数える。期間外の月（12か月未満の年度の残り）は `TUKIBETU` も含めて空。`_2` の列と `YOBI` は空
  - 材料 `output/gaikyo-material.xlsx`：シート `主要科目`（要素名・日本語の項目名・金額）と `月別`（月・売上・仕入・外注費・人件費・源泉徴収税額）。日本語の項目名は `gaikyo.MAIN_LABELS` に持つ
- Produces: `tatsujin_xml.build_ug16(g: Gaikyo, ...)`：ug16 雛形に `fill_skeleton` で差し込む
- `statements` は決算書に加えて内訳書・概況書の材料も出す

- [ ] **Step 1: 失敗するテスト** — `test_uchiwake_all_accounts_with_subs`（補助の合計・空の補助・逆残高・残高0は空）、`test_uchiwake_codes_unique`、`test_uchiwake_material_sheets`、`test_ug13_structure`、`test_gaikyo_main_items`（手計算の正解）、`test_gaikyo_cost_vs_sga_split`、`test_gaikyo_monthly`（4月始まり、決算整理の伝票は期末の月）、`test_monthly_short_year_and_odd_start`（10月始まり・6か月の年度：`TUKIBETU_01`=10、`_07`〜`_12` は空）、`test_withholding_from_deposits`、`test_ug16_structure`（雛形の要素の並びがそのまま、値の入った要素だけ一致）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 6: 消費税XML（SY35）

**Files:**
- Modify: `tatsujin_xml.py`（`build_sy35`、`export` に `shohizei`）
- Test: `tests/test_tatsujin_sy35.py`（新規）

**Interfaces:**
- Consumes: `tax_summary.summarize(...) -> TaxSummary`（`lines: list[SummaryLine]`：`kind`・`category`・`rate`・`invoice`・`allocation`・`net`・`tax` ほか）、`tax.load_tax_settings`
- Produces: `tatsujin_xml.build_sy35(summary, settings, company, period) -> tuple[Node, list[str]]`（XML と警告）。sy35 雛形に `fill_skeleton` で差し込む。`BASIC` の `HOUJINKOJINKUBUN` 1・`KAZEIKUBUN` 1・`KEIKASHOCHIKAZEISHISAN` 2 は雛形の値
- 出す条件：課税・方式 `原則` の年度だけ。免税・簡易・2割特例は `--kind` 省略時は飛ばして理由を表示、`--kind shohizei` を明示したら `KessanError`（「簡易課税・2割特例は集計表を見て達人に手で入れる」）
- 区分（親要素）：税率10% → `D020114`（売上）・`D020124`（仕入）・`D020134`（貸倒）、8%軽 → `D020113`・`D020123`・`D020133`。税率の無い売上（免税・非課税・非課税資産輸出）は `D020114`
- 要素名（`R` は 10 か 8K）。各行の `net` を `…ZEINUKI…`、`tax` を `…ZEIGAKU…` に足し込む（同じ要素に入る行は合計）。`…ZEIKOMI…` は空（MFの実物どおり）
  - 課税売上 → `KAZEIURIAGEZEINUKI{R}_WARIMODOSHI`／`KAZEIURIAGEZEIGAKU{R}_WARIMODOSHI`（売上は常に割戻し：tax_summary と同じ）。課税売上返還 → `KAZEIURIAGEHENKAN…{R}_WARIMODOSHI`
  - 免税売上 → `MENZEIURIAGEZEINUKI10`、非課税売上 → `HIKAZEIURIAGEYUUKAIGAIZEINUKI10`、非課税資産輸出 → `HIKAZEISHISANYUSHUTSUZEINUKI10`（いずれも税抜＝税込）
  - 課税仕入 → `SHIIRE{A}ZEINUKI{R}_{C}`／`SHIIRE{A}ZEIGAKU{R}_{C}`。`A` は個別対応方式なら 個別対応の列（課税対応→`KAZEI`、非課税対応→`HIKAZEI`、共通→`KYOTUU`）、全額控除・一括比例配分なら `KAZEI`。`C` は 割戻し→`WARIMODOSHI`、積上げ→`TSUMIAGE`。インボイスが 適格・少額特例・制度前・空 の行がこれ
  - 経過80・経過50 → `SHIIRE{A}ZEINUKI{R}_INVOICEIGAI_WARIMODOSHI80`／`…50`（ZEIGAKU も同様。税額は控除割合を掛ける前の額）。積上げの年度でもこの要素に入れ、警告「積上げの年度の経過措置分は割戻しの欄に入れた（達人で確認）」
  - 経過70・経過30 → 入れない。警告「経過措置70%・30%分（税込N円）はSY35に欄が無いため達人に手で入れる」。インボイス なし → 入れない（控除できない）
  - 課税仕入返還 → `SHIIREHENKAN{A}…`（課税仕入と同じ規則）
  - 貸倒 → `KASHIDAOREZEINUKI{R}_WARIMODOSHI`／`KASHIDAOREZEIGAKU{R}_WARIMODOSHI`、貸倒回収 → `KASHIDAOREKAISHU…{R}_WARIMODOSHI`
  - 非課税仕入・対象外 → 入れない
- 要素名を組み立てたら、必ず雛形にある名前か `fill_skeleton` が確かめる（無ければプログラムの誤りとして止まる）

- [ ] **Step 1: 失敗するテスト** — 3a の `test_tax_summary.py` の帳簿の作り方を使い、手計算の正解で：`test_sy35_sales_and_purchases_10_and_8`、`test_sy35_individual_allocation`、`test_sy35_tsumiage_purchases`、`test_sy35_transition_80_50`、`test_sy35_transition_70_30_warned_not_written`、`test_sy35_returns_and_bad_debts`、`test_sy35_exempt_and_nontaxable_sales`、`test_sy35_not_written_for_kanni_2wari_exempt`（省略時は飛ばす、明示ならエラー）、`test_sy35_skeleton_order_preserved`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 7: 手順書・ドキュメント・通しテスト・実機確認の手順

**Files:**
- Modify: `.claude/skills/kessan-close/SKILL.md`（「決算書と達人への引き渡し」の節：close-post の後に `statements` → 達人へ `export-tatsujin` → 達人で法人税等を計算 → `close-set-amount 法人税等` → `close-post` → `statements`・`export-tatsujin` を出し直す、の順。警告の読み方。簡易・2割特例・経過70/30は手入力）、`.claude/scripts/kessan/README.md`、`cli.py` の docstring（使い方の例）、`docs/kessan/tatsujin-import.md`（§3.5 に SY35・UG16 の構造の要点と雛形の場所、§5 の結論欄は実機確認待ちのまま）、spec §10 に本計画の判断を追記
- Create: `docs/kessan/tatsujin-import-test.md`（オーナーが実機で確かめる手順：通しテストの架空会社で出した XML4種を、法人税の達人「中間ファイル（決算書用）からのインポート」・内訳概況書の達人・消費税の達人で読み込む手順と、確かめること（spec §9：読めるか、PATTERN のコードの意味、取込設定での科目の対応、減価償却累計額のマイナスの項目の扱い、SY35 の経過措置の税額の入れ方・KAZEIKUBUN、使っている製品・エディション）。結果を書く欄）
- Create/Modify: `tests/test_statements_flow.py`（新規）
- `scripts/` に、通しテストの架空会社の XML4種を書き出すコマンドは作らない（実機確認用のファイルは、テストと同じ帳簿を `cli.py` で作る手順を tatsujin-import-test.md に書く）

**通しテスト:** `test_close_flow.py` の架空の課税の会社（期首残高・仕訳・決算整理の候補の承認と登録まで）を流したあと、`statements` と `export-tatsujin` を CLI で実行し、手計算の正解（docstring に計算を書く。コードから正解を作らない）と照合する：BS の各区分の合計、売上総利益〜当期純利益、繰越利益剰余金の期末、SY35 の `KAZEIURIAGEZEINUKI10_WARIMODOSHI`・`SHIIREKAZEIZEIGAKU10_WARIMODOSHI`、UG16 の売上高と月別の売上、UG13 の普通預金の補助の残高。4ファイルが cp932 で読め、`GYOUMUPREFIX` が正しいこと

- [ ] **Step 1: 失敗するテスト** — `test_statements_and_export_flow`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装（足りない結線があれば直す）と文書**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**
