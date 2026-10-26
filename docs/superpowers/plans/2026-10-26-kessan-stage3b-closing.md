# kessan 段階3b：決算整理（kessan-close）と宿題の解消 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 検算の通った帳簿から決算整理仕訳の候補（未払・未収・前払・減価償却・消費税・法人税等）を作り、承認後に adjustments.csv（伝票番号 A1…）へ登録できるようにする。あわせて 3a の持ち越し3件と、段階2からの宿題（証憑の解決コマンド・証憑の照合警告・カナ／漢字の照合・会社別の科目）を片づける。

**Architecture:** 決算整理の候補は専用の `adjustments-staging.csv`（列は STAGING_COLUMNS）に置き、`close-candidates` → `close-approve` → `close-post` の流れで adjustments.csv に登録する（journal 側の staging・post には手を入れない）。候補の取り込み元IDは `close:<項目>:<キー>` とし、何度実行しても二重にならない。チェックリストの各項目は `close_*.py` の独立したモジュールで、`close.py` が順に呼んで候補と所見（`output/close-report.md`）をまとめる。候補の税の列は各項目が明示して書く（`fill_tax` は staging.csv だけを触るので干渉しない）。

**Tech Stack:** Python 3、pyyaml、openpyxl、pytest（既存と同じ）

**Spec:** `docs/superpowers/specs/2026-10-26-kessan-stage3-closing-design.md`（§4 が本計画の範囲。3a の持ち越しは本計画の Task 1〜2）

## Global Constraints

- コードは `.claude/scripts/kessan/`、テストは `.claude/scripts/kessan/tests/`。`cd .claude/scripts/kessan && python -m pytest -q` で全件（開始時 561 件）が通ること
- 既存の書き方に合わせる：利用者に伝えるエラーは `KessanError`（日本語）、CSVはBOM付きUTF-8、書き込みは `ensure_writable` → `replace_rows`／`append_rows`、全件検証してから一括反映
- **検算（`check.run_checks`）にNGが1件でもあれば `close-candidates`・`close-post` は実行を拒否する**（NGの件数と `output/check-result.md` を示す）
- 数字はすべてスクリプトで確定させる（AIは判断と承認の取り次ぎだけ）。端数は円未満切り捨て（減価償却も同じ。最終年度は備忘価額1円を残す）
- 耐用年数ごとの償却率はコードに書かず `config/depreciation/rates.yaml` から読む（根拠URL・確認日付き。国税庁の別表で確かめる）
- 候補はすべて「要確認」で staging に入れ、自動承認しない（決算整理は全件オーナーが承認する）
- 決算整理仕訳の `登録区分` は `決算整理`、伝票番号は adjustments.csv で `A1, A2…`（journal.csv と重ならない。既存の検算「伝票番号」が見る）
- 実クライアントの情報をテスト・fixture に入れない
- 各タスクの最後に、全件テスト → `codex-review` → commit（`kessan: ` で始める日本語、末尾に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）→ `git push origin master`

## Review Focus

1. **close-candidates を2回実行**しても候補が二重にならず、承認済み・登録済みの候補は作り直さないこと → Task 5 `test_candidates_idempotent`
2. **翌期の年度フォルダが無い**（`--next-year-dir` を渡さない）ときも、未払候補は「翌期の支払が見つからない」として候補になり、止まらないこと → Task 6 `test_unpaid_without_next_year`
3. **減価償却の最終年度**で帳簿価額が1円を下回らないこと、期の途中で取得した資産の月割り → Task 8 `test_straight_line_final_year_keeps_one_yen`・`test_first_year_months`
4. **簡易課税・免税の年度**で消費税の候補を作らず、所見だけを出すこと → Task 5 `test_consumption_tax_not_for_kanni_and_exempt`
5. **前期から繰り越した前払費用**（期首残高）を当期の費用に戻す候補が出ること → Task 7 `test_opening_prepaid_reversed`

---

### Task 1: インボイスと証憑の登録番号の食い違いを止める（3a 持ち越し1）

**Files:**
- Modify: `check.py`、`tax_fill.py`、`patterns.py`
- Test: `tests/test_check.py`、`tests/test_tax_fill.py`、`tests/test_patterns.py`

**Interfaces:**
- Consumes: `evidence.evidence_by_source(evidence_rows)`（取り込み元ID → 対応が1つに決まった証憑行）、`TaxSettings.method`
- 規則（原則課税の年度だけ。簡易・2割特例・免税は対象外）：
  - check：行の取り込み元IDに対応する証憑があり、証憑の `登録番号` が空なのに、その行の課税仕入の側のインボイスが `適格` → NG「伝票N: 借方 インボイスが適格だが証憑（<証憑ファイル>）に登録番号が無い」。証憑に登録番号があるのにインボイスが `経過XX`／`なし` → WARN「…証憑に登録番号がある（適格ではないか確認）」
  - tax_fill：承認済み（未登録）の staging の行で上のNGと同じ食い違いがあれば、承認を外し、`判定=要確認`、要確認理由「インボイスが適格だが証憑に登録番号が無い（登録番号を確認）」。`FillResult.unapproved` に数える
  - auto_approve：一致した証憑に登録番号が無く、パターンのインボイスが `適格` なら承認しない（衝突に数え、同じ理由を付ける）

- [ ] **Step 1: 失敗するテスト** — `test_check.py`：`test_qualified_invoice_without_registration_is_ng`（原則・証憑の登録番号が空・行は適格 → NG）、`test_registration_but_transitional_warns`、`test_registration_rule_not_for_kanni`。`test_tax_fill.py`：`test_unapproves_qualified_without_registration`。`test_patterns.py`：`test_auto_approve_refuses_qualified_pattern_without_registration`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**（3a の `_check_evidence_rates`・`_approved_conflicts` と同じ場所・同じ書き方に足す）
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 2: 登録済み伝票の税率の分割と、科目の追加（3a 持ち越し2・3、宿題「会社別の科目」）

**Files:**
- Modify: `tax_edit.py`（`split_tax`）、`cli.py`（`split-tax` の引数）、`accounts.py`、`accounts-master.csv`
- Create: `テンプレート/context/company/kessan-accounts.yaml`（雛形。コメントだけ）
- Test: `tests/test_tax_edit.py`、`tests/test_accounts.py`、`tests/test_cli.py`

**Interfaces:**
- Produces: `tax_edit.split_posted(year_dir, voucher_no: str, line: int, parts: list[tuple[int, int]], accounts) -> int`：journal.csv の登録済み伝票（`伝票番号`、伝票内の `行`）の1行を、税率ごとの複数行に分ける。分ける行は「税区分に税率がある側」の行（両側に税率があればエラー）。分けた行は同じ伝票番号・登録日時・取り込み元IDのまま、元の行の位置に入れる。各行の税区分は `with_rate`。金額の合計がその側の金額と一致しなければエラー。伝票の借方合計＝貸方合計は変わらない。分けた後の行数を返す。課税の年度だけ（免税ならエラー）。書き込みは `ensure_writable` → `replace_rows`。CLI：`split-tax --voucher <伝票番号> --line <行> --part 10:1080 --part 8:540`（`--id` とは同時に使えない）。実行後に検算を流す（`set-tax` と同じ）
- 科目マスタに2行足す：`未払消費税等,負債,貸,流動負債,`（既定税区分は空）、`仮払税金,資産,借,流動資産,`
- Produces: `accounts.load_accounts(path=MASTER_PATH, extra_path=None) -> dict[str, Account]`：`extra_path`（`context/company/kessan-accounts.yaml`）があれば科目を足す。yamlの形：

```yaml
accounts:
  - {科目: 業務委託料, 区分: 費用, 正常残高: 借, 表示区分: 販売費及び一般管理費, 既定税区分: 課税仕入10%}
```

  検証：区分・正常残高・既定税区分の値はマスタと同じ規則。マスタと同名の科目はエラー「科目マスタと同じ名前の科目は足せない」。追加科目はマスタの後ろに並ぶ（順序を保つ）
- `cli.py` の科目の読み込みを1か所の helper（`_load_accounts(year_dir)`：年度フォルダから見たインスタンスの `context/company/kessan-accounts.yaml` を `_company_file` と同じ探し方で渡す）に寄せ、全コマンドで使う

- [ ] **Step 1: 失敗するテスト** — `test_split_posted_voucher`（伝票3：消耗品費 1620 課税仕入10% 適格／現金 1620 を 行1・`10:1080, 8:540` → 3行、伝票番号3のまま、検算の貸借一致・税区分・税率食い違い（証憑が混在）のNGが消える）、`test_split_posted_rejects_exempt_year`、`test_split_posted_amount_mismatch`。`test_accounts.py`：`test_new_master_accounts`（未払消費税等・仮払税金がある）、`test_extra_accounts_loaded`、`test_extra_account_same_name_rejected`、`test_extra_account_bad_value_rejected`。`test_cli.py`：`test_cli_uses_company_accounts`（kessan-accounts.yaml の科目を使う仕訳が post で通る）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 3: 取引先名の照合（カナ／漢字・読みの正規形・支払先の対応表）

**Files:**
- Modify: `match.py`、`extracted_import.py`、`cli.py`
- Create: `テンプレート/context/company/kessan-payees.yaml`（雛形）
- Test: `tests/test_match.py`、`tests/test_extracted_import.py`、`tests/test_cli.py`

**Interfaces:**
- Produces: `match.reading_key(text, abbreviations) -> str`：`expand_abbreviations` の結果に、ひらがな→カタカナ、小書きカナ→並字（ァィゥェォッャュョヮ→アイウエオツヤユヨワ）、長音記号（ー・ｰ・－）と中黒・記号の除去をかけた文字列
- Produces: `match.load_payees(path) -> dict[str, list[str]]`（`context/company/kessan-payees.yaml`。無ければ空）。形：

```yaml
payees:
  - {取引先: テスト商事, 名義: [テストシヨウジ, テストシヨウジ（カ]}
```

- Changes: `match.name_in_description(name, description, abbreviations, payees=None) -> bool`：`reading_key(name)` が `reading_key(description)` に含まれる、または `payees` の `name` の名義のどれかの `reading_key` が含まれれば True。既存の呼び出しは `payees` 省略で今までどおり（ただし比較は `reading_key` に変わる）
- `extracted_import` と `patterns` の照合で `payees` を渡す（cli が `kessan-payees.yaml` を読んで渡す）
- Produces: CLI `add-payee --year-dir <Y> --name <取引先> --kana <名義>`：yaml に1件足す（同じ組がすでにあれば何もしない。取引先が既にあれば名義を追記）。AIはオーナーの承認を得てから実行する（SKILL.md に書く。Task 9）

- [ ] **Step 1: 失敗するテスト** — `test_reading_key_normalizes`（`"ｶ)ﾃｽﾄｼｮｳｼﾞ"` と `"株式会社テストショウジ"` と `"カ）テストシヨウジ"` が同じ正規形を含む関係になる、`"テスト・サービス"` と `"テストサ-ビス"`）、`test_name_matches_via_payees`（`テスト商事` が `フリコミ テストシヨウジ` に一致、payees なしでは不一致）、`test_add_payee_cli`、`test_receipt_match_uses_payees`（漢字の取引先の領収書を取り込んでも「取引先名が摘要に無い」が付かない）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 4: 証憑の解決コマンドと、検算の証憑照合

**Files:**
- Create: `.claude/scripts/kessan/evidence_resolve.py`
- Modify: `evidence.py`、`check.py`、`cli.py`
- Test: `tests/test_evidence_resolve.py`（新規）、`tests/test_check.py`

**Interfaces:**
- `evidence.py` に状態 `STATE_PAYABLE = "未払計上"`（決算整理で未払に計上した。Task 6 が使う）を足す
- Produces: `evidence_resolve.resolve_evidence(year_dir, evidence_id, *, match=None, as_payable=False, as_new=False, sources, accounts) -> str`（3つのうち1つだけ指定。違えばエラー）。戻り値は新しい状態：
  - `match=<取り込み元ID>`：状態が 複数候補 の証憑を、候補のうちの1つに決める（候補に無いIDはエラー）。状態を 明細に対応、取り込み元IDをそのIDにする。staging の該当行の借方科目が空なら証憑の科目候補を入れ、要確認理由に「証憑と一致」を足す（`extracted_import` の1件一致のときと同じ処理を関数に切り出して共用する）
  - `as_payable=True`：複数候補・明細に該当なしの証憑を 未払候補 にする（決算整理の未払で扱う）
  - `as_new=True`：複数候補・未払候補の証憑から、`credit_for_new_entry` と同じ規則で新しい仕訳の候補を staging に作る（取り込み元ID `receipt:<証憑ID>`、状態 新規仕訳）。支払方法が決まらない（後払い・不明）ならエラー「支払方法が決まらないので新規仕訳にできない（as-payable を使う）」
  - 書き込み順は extracted_import と同じ（staging → evidence）。evidence.csv は全行を書き直す（`replace_rows`）
- CLI：`resolve-evidence --year-dir <Y> --evidence-id <ID> (--match <取り込み元ID> | --as-payable | --as-new)`
- Produces: `check.check_evidence(year_dir, entries) -> list[Finding]`（`run_checks` に足す。項目名 `証憑`）：
  - 状態が 複数候補 → WARN「証憑 <ファイル>（<日付> <金額>円 <取引先>）: 突き合わせ先が複数（resolve-evidence で決める）」
  - 状態が 明細に対応・新規仕訳 なのに、取り込み元IDの行が journal にも staging にも無い → WARN「…対応する仕訳が無い」
  - 状態が 未払候補 → WARN「…未払候補（決算整理で未払に計上する）」
- `close-candidates`（Task 5）は、複数候補と「対応する仕訳が無い」が1件でもあれば拒否する（未払候補は Task 6 が扱うので拒否しない）。そのための `check.unresolved_evidence(year_dir, entries) -> list[str]` も用意する

- [ ] **Step 1: 失敗するテスト** — `test_resolve_match`、`test_resolve_match_rejects_non_candidate`、`test_resolve_as_payable`、`test_resolve_as_new_creates_staging_row`、`test_resolve_as_new_requires_payment_method`、`test_check_warns_unresolved_evidence`（3種類の警告）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 5: 決算整理の枠組み（候補・承認・登録）と、消費税・法人税等

**Files:**
- Create: `.claude/scripts/kessan/close.py`、`close_tax.py`
- Modify: `common.py`（`YEAR_FILES` に `adjustments-staging.csv`）、`cli.py`
- Test: `tests/test_close.py`（新規）、`tests/test_close_tax.py`（新規）

**Interfaces:**
- 候補を表す型：`close.Candidate`（`item: str`、`key: str`、`date: str`、`lines: list[dict]`（各行は JOURNAL_COLUMNS の借方・貸方・金額・税の列・摘要・取引先）、`reason: str`）。取り込み元IDは `f"close:{item}:{key}"`。1候補＝1伝票（複数行なら全行に同じ取り込み元IDと staging 内の伝票番号 `C1`, `C2`…）
- 所見：`close.Note`（`item: str`、`message: str`）
- Produces: `close.run_close(year_dir, accounts, *, next_year_dir=None, prev_year_dir=None, sources=None, payees=None) -> CloseResult`（`candidates_added`、`candidates_kept`、`notes: list[Note]`、`report_path`）
  1. `check.run_checks` にNGがあれば `KessanError`（件数と check-result.md）。`check.unresolved_evidence` があっても `KessanError`
  2. 各項目のモジュールの `collect(ctx) -> tuple[list[Candidate], list[Note]]` を順に呼ぶ（`ctx` は `close.CloseContext`：year_dir、accounts、period、tax_settings、entries（journal＋adjustments）、opening、evidence、next_year_dir、payees、abbreviations）。項目の順：未払・未収（Task 6）→ 前払（Task 7）→ 減価償却（Task 8）→ 役員報酬・残高の所見（Task 9）→ 消費税 → 法人税等。本タスクでは消費税・法人税等だけを登録し、他は後のタスクが `close.ITEMS` に足す
  3. adjustments-staging.csv の既存行と突き合わせる：同じ取り込み元IDが **承認済み** か adjustments.csv に **登録済み** なら作り直さない（kept）。未承認なら新しい内容で置き換える。もう出なくなった未承認の候補は消す
  4. `output/close-report.md` に項目ごとの候補（伝票の形）と所見を書く
- Produces: `close.approve_candidates(year_dir, source_ids: list[str]) -> int`、`close.post_candidates(year_dir, accounts, now=None) -> list[str]`：承認済みの候補を adjustments.csv に `A<n>`（既存の最大＋1から）で登録し、staging から消す。検証は `post._validate` と同じ規則（日付が期間内・貸借一致・科目がマスタにある）を共用する。`登録区分=決算整理`。登録の前後で検算を流し、登録前にNGなら拒否
- Produces: `close.set_amount(year_dir, item: str, amount: int) -> None`：`close-overrides.yaml`（年度フォルダ）に `{item: amount}` を保存する。`item` は `消費税`／`法人税等` のどちらか（違えばエラー）。0以上の整数
- CLI：`close-candidates --year-dir <Y> [--next-year-dir <翌期のY>] [--prev-year-dir]`、`close-approve --year-dir <Y> --ids …`（`--all` で全件）、`close-post --year-dir <Y>`、`close-set-amount --year-dir <Y> --item 消費税|法人税等 --amount N`
- `close_tax.collect(ctx)`：
  - 消費税：課税・原則（または2割特例）の年度で、`close-overrides.yaml` に `消費税` があればその額、無ければ `tax_summary.summarize(...).estimate` を「年税額」とする。`仮払税金` の期末残高（中間納付）を引いた残りを未払に計上する。候補（日付は期末日、1伝票）：借方 `租税公課` 年税額（税区分 対象外）／貸方 `仮払税金` 残高（あれば）＋ 貸方 `未払消費税等` 残り。残りがマイナスなら貸方の代わりに借方 `未収入金`（補助 `消費税`）。年税額が0なら候補なし。簡易課税で上書きが無い・免税 → 候補なし、所見「消費税は達人で計算した額を close-set-amount で入れる」（簡易のとき）
  - 法人税等：`close-overrides.yaml` に `法人税等` があるときだけ候補：借方 `法人税等` 金額（対象外）／貸方 `未払法人税等`。無ければ所見「法人税等は達人で計算した額（期末の未払額）を close-set-amount で入れる」
  - 候補の要確認理由に、金額の出どころ（「概算（tax-summary）」「上書き額」）を書く

- [ ] **Step 1: 失敗するテスト** — `test_close.py`：`test_refuses_when_check_ng`、`test_refuses_when_evidence_unresolved`、`test_candidates_idempotent`（2回実行で行数同じ、承認済みは作り直さない、登録済みは出さない）、`test_stale_unapproved_candidate_removed`、`test_post_assigns_A_numbers`（adjustments に A1 がある状態で2候補を登録 → A2・A3、登録区分=決算整理、検算NGなし）、`test_set_amount_validates`。`test_close_tax.py`（3a の `test_warimodoshi_10_and_8` と同じ帳簿＝estimate 7,200）：`test_consumption_tax_candidate`（仮払税金 3,000 → 租税公課 7,200／仮払税金 3,000・未払消費税等 4,200）、`test_consumption_tax_refund`（仮払税金 9,000 → 未収入金 1,800）、`test_consumption_tax_override`、`test_consumption_tax_not_for_kanni_and_exempt`、`test_corporate_tax_only_with_override`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 6: 未払・未収（close_payable）

**Files:**
- Create: `.claude/scripts/kessan/close_payable.py`
- Modify: `close.py`（`ITEMS` に足す）、`evidence.py`（状態の更新）
- Test: `tests/test_close_payable.py`（新規）

**Interfaces:**
- Consumes: `CloseContext`、`match.name_in_description(..., payees)`、`tax.invoice_class`、`evidence.STATE_UNPAID`・`STATE_PAYABLE`
- 翌期の明細：`ctx.next_year_dir` の journal.csv と staging.csv の行のうち、支払口座（`match.payment_accounts(sources)`）の側がある行。無ければ空
- 未払：状態 `未払候補` で日付が期末日以前の証憑ごとに、1候補（item `未払`、key＝証憑ID、日付＝期末日）：借方 証憑の科目候補（補助候補）／貸方 `未払金`（補助なし）、取引先＝証憑の取引先、金額＝証憑の金額。税の列：課税の年度なら借方に科目の既定税区分（証憑の税率8なら `with_rate`）、インボイスは `invoice_class(証憑の日付, 登録番号の有無, 金額, …)`（原則のときだけ）。翌期の明細から、出金額＝証憑の金額・日付が「支払期日の前後10日（支払期日が無ければ期末日の翌日から92日以内）」・取引先名が摘要に一致（payees 込み）の行を探し、1件なら要確認理由「翌期の支払 <日付> <取り込み元ID> と一致」、0件なら「翌期の支払が見つからない」、複数なら「翌期の支払の候補が複数」
- 未収（売上）：翌期の明細のうち、期末日の翌日から60日以内の入金で、当期の 売上高 の行の取引先のどれかが摘要に一致（payees 込み）するものごとに1候補（item `未収`、key＝その明細の取り込み元ID、日付＝期末日）：借方 `売掛金`（取引先）／貸方 `売上高`（課税の年度は 課税売上10%）、要確認理由「当期分の売上か確認（翌期の入金から推定）」
- 登録（`close-post`）の後：未払の候補を登録した証憑の状態を `未払計上`、取り込み元IDを `close:未払:<証憑ID>` にする（`close.post_candidates` から各項目の `after_post(year_dir, posted_source_ids)` を呼ぶ仕組みを足す）

- [ ] **Step 1: 失敗するテスト**（期間 2025-04-01〜2026-03-31、翌期フォルダ 2026-04-01〜2027-03-31）：
  - `test_unpaid_matched_next_year`：証憑 請求書 2026-03-25 55,000 テストシヨウジ 外注費 支払期日 2026-04-30 → 翌期 2026-04-30 出金 55,000「フリコミ テストシヨウジ」→ 候補 外注費 55,000／未払金 55,000、日付 2026-03-31、理由に翌期の取り込み元ID
  - `test_unpaid_without_next_year`：`next_year_dir=None` → 候補あり、理由「翌期の支払が見つからない」
  - `test_unpaid_after_period_ignored`（証憑の日付 2026-04-02 → 候補なし）
  - `test_unpaid_kanji_partner_via_payees`
  - `test_receivable_from_next_year_income`（当期に 売上高 取引先 テストシヨウジ の行あり、翌期 2026-04-20 入金 110,000「フリコミ テストシヨウジ」→ 売掛金／売上高 110,000）
  - `test_evidence_state_updated_after_post`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 7: 前払（close_prepaid）

**Files:**
- Create: `.claude/scripts/kessan/close_prepaid.py`、`テンプレート/context/company/kessan-prepaid.yaml`（雛形）
- Modify: `close.py`
- Test: `tests/test_close_prepaid.py`（新規）

**Interfaces:**
- 登録簿 `context/company/kessan-prepaid.yaml`（cli が `_company_file` と同じ探し方で `ctx` に渡す。無ければ空）：

```yaml
prepaid:
  - {名前: 損害保険, 科目: 保険料, 摘要キーワード: ソンポ, 月数: 12}   # 取引先 でもよい。月数の既定は12
```

- 当期の費用の計上：登録簿の各項目に一致する当期の行（借方が その科目、摘要キーワードか取引先が摘要・取引先に一致）ごとに、支払日の属する月から `月数` か月を役務の期間とし、期末日より後の月数 `m` を数える。前払額 = floor(金額 × m / 月数)。0なら候補なし。候補（item `前払`、key＝行の取り込み元ID、日付＝期末日）：借方 `前払費用`（補助＝名前）／貸方 その科目、両側とも税区分 `対象外`（消費税は支払時の行で扱う）
- 期首の前払費用の戻し：期首残高の `前払費用` の補助ごとに、登録簿の同じ名前の科目へ戻す候補（item `前払戻し`、key＝補助、日付＝**期首日**）：借方 その科目／貸方 `前払費用`（補助）、金額＝期首残高、税区分 `対象外`。登録簿に名前が無ければ候補を作らず所見「期首の前払費用（<補助>）の戻し先が kessan-prepaid.yaml に無い」
- 所見：登録簿に無いが、保険料・地代家賃・リース料・支払手数料・諸会費・通信費で1件5万円以上の当期の支払 → 「年払いなら kessan-prepaid.yaml に登録（<日付> <摘要> <金額>円）」

- [ ] **Step 1: 失敗するテスト** — `test_prepaid_insurance`（保険料 120,000 を 2025-10-01 に支払、月数12 → 期末後 6か月 → 前払費用 60,000）、`test_prepaid_zero_when_within_period`、`test_opening_prepaid_reversed`（期首 前払費用 補助 損害保険 30,000 → 期首日付で 保険料 30,000／前払費用 30,000）、`test_opening_prepaid_without_registry_noted`、`test_large_unregistered_payment_noted`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 8: 減価償却（close_depreciation）

**Files:**
- Create: `.claude/scripts/kessan/close_depreciation.py`、`config/depreciation/rates.yaml`、`テンプレート/context/company/fixed-assets.csv`（ヘッダーだけ）
- Modify: `close.py`
- Test: `tests/test_close_depreciation.py`（新規）

**Interfaces:**
- 台帳 `context/company/fixed-assets.csv` の列：`資産名, 科目, 取得日, 事業供用日, 取得価額, 償却方法, 耐用年数, 前期末償却累計額, 除却日, 備考`。`償却方法` は `定額法`／`定率法`／`一括償却`／`少額特例`。`前期末償却累計額` は前期末までに償却した累計（初年度は0）。読み込み・検証は `close_depreciation.load_assets(path, accounts) -> list[Asset]`（科目は有形・無形固定資産の表示区分であること、日付・金額・耐用年数の検証、全件まとめてエラー）
- `config/depreciation/rates.yaml`：`根拠URL`・`確認日`、`定額法: {2: "0.500", 3: "0.334", 4: "0.250", …}`、`定率法: {4: {償却率: "0.500", 改定償却率: "1.000", 保証率: "0.12499"}, …}`（平成24年4月1日以後取得の200%定率法。耐用年数2〜50年。国税庁「減価償却資産の償却率等表」で確かめる）。`Decimal` で読む
- 当期の償却費（`Asset` ごと）：
  - 月数 `n`：事業供用日が当期内なら、供用月から期末月まで（両端含む）。前期以前なら12（期間が12か月でない年度は期間の月数）。除却日が当期内ならその月まで
  - 定額法：floor(取得価額 × 定額法償却率 × n / 12)。ただし「取得価額 − 前期末償却累計額 − 1」を上限にする（備忘価額1円）
  - 定率法：期首帳簿価額 B＝取得価額−前期末償却累計額。調整前償却額 = floor(B × 償却率 × n / 12)。これが floor(取得価額 × 保証率) 未満なら、改定取得価額（その年度の期首帳簿価額）× 改定償却率 × n / 12（floor）。上限は B − 1
  - 一括償却：floor(取得価額 / 3)（月割りしない）。3年目は残り全額（取得価額 − 前期末償却累計額）。備忘価額は残さない
  - 少額特例：供用年度に取得価額の全額。当期の少額特例の合計が300万円を超えたら所見「少額減価償却資産の特例の年間上限（300万円）を超えている」
- 候補（item `減価償却`、key＝資産名、日付＝期末日）：定額法・定率法は 借方 `減価償却費`（対象外）／貸方 `減価償却累計額`。一括償却・少額特例は 借方 `減価償却費`／貸方 その資産の科目（直接控除）。償却費0なら候補なし
- 所見：当期の journal で、有形・無形固定資産の科目の借方、または 消耗品費・工具器具備品・ソフトウエア の10万円以上の行で、台帳に同じ取得日・取得価額の資産が無いもの → 「固定資産台帳への登録を検討（<日付> <摘要> <金額>円）」

- [ ] **Step 1: 失敗するテスト**（期間 2025-04-01〜2026-03-31。手計算の値）：
  - `test_first_year_months`：定額法 4年 400,000 供用 2025-07-01 → 9か月 → 75,000
  - `test_declining_balance_first_year`：定率法 4年 400,000 供用 2025-07-01 → 150,000
  - `test_declining_balance_switch_to_revised`：定率法 4年 400,000、前期末償却累計額 350,000（B=50,000）→ 調整前 25,000 < 保証額 floor(400,000×0.12499)=49,996 → 改定 50,000×1.000=50,000 だが上限 49,999 → 49,999
  - `test_straight_line_final_year_keeps_one_yen`：定額法 4年 400,000、前期末累計 370,000 → 本来 100,000 → 上限 29,999
  - `test_lump_sum_three_years`：一括償却 300,000 → 100,000、3年目（前期末累計 200,000）→ 100,000
  - `test_small_amount_special`：少額特例 250,000 → 250,000（貸方 工具器具備品）
  - `test_small_amount_cap_noted`、`test_unregistered_asset_noted`、`test_invalid_ledger_rejected`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**

### Task 9: 役員報酬・残高の所見、手順書（kessan-close スキル）、通しテスト

**Files:**
- Create: `.claude/scripts/kessan/close_review.py`、`.claude/skills/kessan-close/SKILL.md`、`tests/test_close_flow.py`
- Modify: `close.py`、`.claude/scripts/kessan/README.md`、`.claude/skills/kessan-books/SKILL.md`（payees・resolve-evidence・split-tax --voucher）、`docs/superpowers/specs/2026-10-26-kessan-stage3-closing-design.md`（実装との差）
- Test: `tests/test_close_review.py`（新規）

**Interfaces:**
- `close_review.collect(ctx)`：所見だけ（候補は作らない）
  - 役員報酬：当期の 役員報酬 の借方を月ごとに合計し、額が2種類以上あれば「役員報酬の月額が一定でない（<月>: <額>…）。期首から3か月以内の改定か確認」
  - 仮払金の期末残高が0でない → 「仮払金が残っている（<補助>: <額>円）」
  - 役員借入金・役員貸付金：期首と期末の残高をくらべ、どちらかが100万円以上動いていれば「…が <期首>円 → <期末>円」
  - 役員貸付金の期末残高が0でない → 「役員貸付金があるので認定利息の要否を確認（monthly-closing-checklist.md 項目15）」
- SKILL.md（kessan-close）：手順（検算を0件に → 証憑の未解決を resolve-evidence → `close-candidates`（翌期の明細があれば `--next-year-dir`）→ close-report.md と候補をオーナーに番号付きで提示（communication.md の聞き方）→ `close-set-amount`（消費税・法人税等）→ `close-approve` → `close-post` → 検算）、AIがしてはいけないこと（CSVを直接編集しない・自分で承認しない・推測で金額を作らない）、所見の扱い
- 横罫線（`---`）は使わない

- [ ] **Step 1: 失敗するテスト** — `test_close_review.py`：`test_director_pay_not_fixed`、`test_suspense_remaining`、`test_director_loan_change`、`test_director_lending_interest_note`。`test_close_flow.py`：架空の課税・原則の会社で、journal（売上・仕入・年払保険料・PC購入・役員報酬）、期首残高（前払費用）、固定資産台帳、kessan-prepaid.yaml、未払候補の証憑、翌期フォルダの支払を用意し、`cli.main` で `close-candidates --next-year-dir` → 候補の件数と各金額が手計算どおり → `close-set-amount --item 法人税等 --amount 70000` → `close-approve --all` → `close-post` → adjustments.csv に A1…、`check` にNGなし
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装と手順書**
- [ ] **Step 4: 全件テスト PASS**
- [ ] **Step 5: codex-review → Commit → push**
