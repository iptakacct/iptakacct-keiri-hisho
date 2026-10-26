# kessan 段階3a：課税事業者対応の帳簿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 帳簿の各行に税区分・インボイス・個別対応を持たせ、課税事業者の年度で税区分を自動で埋め・検算し、消費税の集計表（と概算納付税額）を出す。免税の年度と既存の帳簿は今までどおり動く。

**Architecture:** 列を JOURNAL_COLUMNS の末尾に足し、古い列構成のCSVは追記時に自動で列を広げる。税区分の定義・インボイス経過措置の判定・年度の消費税設定は新しい `tax.py` に集め、税区分の自動補完は `tax_fill.py` の冪等な1パス（空欄だけ埋める）にして、取り込み・科目候補・確認・登録の各コマンドの後に呼ぶ。集計は `tax_summary.py`。

**Tech Stack:** Python 3、pyyaml、openpyxl、pytest（既存と同じ。新しい依存は足さない）

**Spec:** `docs/superpowers/specs/2026-10-26-kessan-stage3-closing-design.md`（§3 が本計画の範囲）

## Global Constraints

- コードは `.claude/scripts/kessan/`、テストは `.claude/scripts/kessan/tests/`。テストは `cd .claude/scripts/kessan && python -m pytest -q` で全件（開始時 382 件）が通ること
- 既存の書き方に合わせる：利用者に伝えるエラーは `KessanError`（日本語）、CSVはBOM付きUTF-8、書き込みは `ensure_writable` → `replace_rows`／`append_rows`、全件検証してから一括反映
- 金額は**税込**のまま帳簿に持つ（税込経理のみ）。税抜額・税額は列に持たず集計時に計算する。端数は切り捨て
- 免税の年度（`period.yaml` に `消費税` が無い、または `区分: 免税`）では、税の列を埋めず、税の検算もしない
- 旧税率（8%・5%・3%）・輸入・特定課税仕入・税抜経理・売上の積上げは扱わない
- 税率・経過措置の割合はコードに書かず設定ファイルから読む（根拠URL・確認日付き）
- 実クライアントの情報をテスト・fixture に入れない（leak-scan 対象）
- 各タスクの最後に commit（メッセージは `kessan: ` で始める日本語。末尾に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）し、`git push origin master`
- 全タスク完了後、commit の前に `codex-review` スキルを通す（`~/.claude/CLAUDE.md`）

## Review Focus

1. **段階2で作った既存の年度フォルダ**（税の列が無い journal.csv・staging.csv・evidence.csv）で、取り込み・登録・検算がそのまま動くこと → Task 1 のテスト `test_append_upgrades_old_header`・`test_post_with_old_journal`
2. **免税の年度で税区分が空のまま**でも検算にNGが出ないこと（今までの全テストが免税の年度） → Task 6 のテスト `test_exempt_year_skips_tax_checks`
3. **返品・取消（売上科目が借方、仕入科目が貸方の行）**が集計でマイナスとして相殺されること → Task 8 のテスト `test_summary_nets_returns`
4. **年度の途中で課税に切り替えた（init で消費税の設定を後から入れた）**とき、登録済みの journal の行は自動では埋まらず、検算のNGと `set-tax` で直せること → Task 7 のテスト `test_set_tax_updates_journal_rows`
5. **日付が経過措置の表の範囲外**（2031-10-01 以降）・**1万円ちょうど**の少額特例の境界 → Task 3 のテスト `test_invoice_class_boundaries`

---

### Task 1: 税の列の追加と、古い列構成のCSVの自動拡張

**Files:**
- Modify: `.claude/scripts/kessan/common.py`
- Test: `.claude/scripts/kessan/tests/test_common.py`、`tests/test_post.py`

**Interfaces:**
- Produces: `common.TAX_COLUMNS = ["借方税区分", "借方インボイス", "借方個別対応", "貸方税区分", "貸方インボイス", "貸方個別対応"]`。`JOURNAL_COLUMNS` の末尾（`登録日時` の後）に足す。`STAGING_COLUMNS` は今までどおり `JOURNAL_COLUMNS + [...]`
- Produces: `EVIDENCE_COLUMNS` の末尾に `"税率", "登録番号"`
- Produces: `common.upgrade_header(path, columns) -> bool`：ヘッダーが `columns` の部分集合（足りない列があるだけ）なら、全行を `columns` で書き直して True。同じなら False。`columns` に無い列があれば `KessanError`（今のメッセージ「…の列が想定と違います」）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_append_upgrades_old_header(tmp_path):
    old = [c for c in JOURNAL_COLUMNS if c not in TAX_COLUMNS]
    path = tmp_path / "journal.csv"
    write_rows(path, old, [dict.fromkeys(old, "x")])
    append_rows(path, JOURNAL_COLUMNS, [dict.fromkeys(JOURNAL_COLUMNS, "y")])
    rows = read_rows(path)
    assert list(rows[0].keys()) == JOURNAL_COLUMNS
    assert rows[0]["借方税区分"] == "" and rows[0]["摘要"] == "x"
    assert rows[1]["借方税区分"] == "y"

def test_append_rejects_unknown_column(tmp_path):
    path = tmp_path / "journal.csv"
    write_rows(path, JOURNAL_COLUMNS + ["余計"], [])
    with pytest.raises(KessanError, match="列が想定と違います"):
        append_rows(path, JOURNAL_COLUMNS, [])
```

`test_init_upgrades_existing_files`：古い列で journal.csv・staging.csv・evidence.csv を書いた年度フォルダに、同じ期間で `init_year_dir` を呼ぶ → 3ファイルのヘッダーが新しい列構成、既存の行の値はそのまま。

`tests/test_post.py` に `test_post_with_old_journal`：古い列の journal.csv（1伝票）がある年度で staging の承認済み1行を `post_approved` → journal.csv のヘッダーが `JOURNAL_COLUMNS`、旧伝票の税の列は空、新伝票番号は 2。

- [ ] **Step 2: 失敗を確認** — `python -m pytest tests/test_common.py tests/test_post.py -q` → 上の新テストが FAIL
- [ ] **Step 3: 実装** — `upgrade_header` を足し、`append_rows` はヘッダー不一致のとき `upgrade_header` を試してから追記する。`init_year_dir` は既存の `YEAR_FILES` すべてに `upgrade_header` をかける。`read_rows` は変えない（呼び出し側は `project()`／`.get()` で読む。税の列を `r["借方税区分"]` で直接読むコードを足さないこと）
- [ ] **Step 4: 全テストが通ることを確認** — `python -m pytest -q` → 全件 PASS
- [ ] **Step 5: Commit**

### Task 2: 年度の消費税設定（period.yaml）と init の引数

**Files:**
- Create: `.claude/scripts/kessan/tax.py`
- Modify: `common.py`（`init_year_dir`）、`cli.py`（init の引数）
- Test: `tests/test_tax.py`（新規）、`tests/test_cli.py`

**Interfaces:**
- Produces: `tax.TaxSettings`（frozen dataclass）：`status: str`（免税／課税）、`method: str`（原則／簡易／2割特例）、`calc: str`（割戻し／積上げ）、`allocation: str`（全額控除／個別対応／一括比例配分）、`small_exemption: bool`（少額特例）。プロパティ `taxable -> bool`（status == "課税"）
- Produces: `tax.load_tax_settings(year_dir) -> TaxSettings`。`period.yaml` に `消費税` が無ければ `TaxSettings("免税", "", "", "", False)`
- Produces: `common.init_year_dir(year_dir, start, end, tax=None)`。`tax` は `{"区分","方式","計算","控除","少額特例"}` の dict。指定があれば `period.yaml` の `消費税` を書き込む（既存の期首日・期末日はそのまま）
- period.yaml の形（キーは日本語。設計書 §3.1 の英語キーはこの日本語キーに読み替える）：

```yaml
期首日: "2025-04-01"
期末日: "2026-03-31"
消費税:
  区分: 課税        # 免税／課税
  方式: 原則        # 原則／簡易／2割特例
  計算: 割戻し      # 割戻し／積上げ
  控除: 全額控除    # 全額控除／個別対応／一括比例配分（原則のときだけ意味を持つ）
  少額特例: false   # 1万円未満の課税仕入をインボイス無しでも控除できる会社か
```

- CLI：`init` に `--consumption-tax {免税,課税}`、`--tax-method`、`--tax-calc`、`--tax-allocation`、`--small-exemption`（フラグ）を足す。`--consumption-tax 課税` のとき方式・計算は必須（省略はエラー）、控除は 方式=原則 のとき必須

- [ ] **Step 1: 失敗するテスト** — `test_tax.py`：`test_default_is_exempt`（消費税なし→`taxable` False）、`test_load_taxable_settings`（上のyaml→各値）、`test_invalid_value_rejected`（`区分: 課税事業者` → `KessanError` に「区分は 免税／課税」）、`test_kanni_without_allocation_ok`（方式=簡易 で 控除なし → 読める）。`test_cli.py`：`test_init_with_consumption_tax`（init を2回目に同じ期間＋`--consumption-tax 課税 --tax-method 原則 --tax-calc 割戻し --tax-allocation 全額控除` で呼ぶ → period.yaml に反映、journal.csv は消えない）、`test_init_taxable_requires_method`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装** — `period.yaml` の書き込みは yaml.safe_dump（`allow_unicode=True, sort_keys=False`）で全体を書き直す
- [ ] **Step 4: 全テスト PASS を確認**
- [ ] **Step 5: Commit**

### Task 3: 税区分の定義とインボイス経過措置の判定

**Files:**
- Modify: `.claude/scripts/kessan/tax.py`
- Create: `config/consumption-tax/invoice-transition.yaml`（リポジトリ直下の `config/`）
- Test: `tests/test_tax.py`

**Interfaces:**
- Produces: `tax.TaxCategory`（frozen dataclass：`name: str`、`kind: str`、`rate: int | None`）と `tax.TAX_CATEGORIES: dict[str, TaxCategory]`。名前と kind・rate（設計書 §3.2 の表どおり）：

| 名前 | kind | rate |
|---|---|---|
| 課税売上10% / 課税売上8%軽 | 課税売上 | 10 / 8 |
| 免税売上 | 免税売上 | None |
| 非課税売上 | 非課税売上 | None |
| 非課税資産輸出 | 非課税資産輸出 | None |
| 課税売上返還10% / 課税売上返還8%軽 | 課税売上返還 | 10 / 8 |
| 課税仕入10% / 課税仕入8%軽 | 課税仕入 | 10 / 8 |
| 課税仕入返還10% / 課税仕入返還8%軽 | 課税仕入返還 | 10 / 8 |
| 非課税仕入 | 非課税仕入 | None |
| 対象外 | 対象外 | None |
| 貸倒10% / 貸倒8%軽 | 貸倒 | 10 / 8 |
| 貸倒回収10% / 貸倒回収8%軽 | 貸倒回収 | 10 / 8 |

- Produces: `tax.INVOICE_VALUES = ("適格", "経過80", "経過70", "経過50", "経過30", "少額特例", "なし")`、`tax.ALLOCATION_VALUES = ("課税対応", "非課税対応", "共通")`
- Produces: `tax.with_rate(name: str, rate: int) -> str`：同じ kind で税率を替えた名前（`with_rate("課税仕入10%", 8) == "課税仕入8%軽"`）。rate の無い区分はそのまま返す
- Produces: `tax.Transition`（`start: str`、`end: str`、`label: str`、`ratio: int`）、`tax.load_transitions(path=TRANSITION_PATH) -> list[Transition]`、`tax.TRANSITION_PATH`（`Path(__file__).parents[3] / "config/consumption-tax/invoice-transition.yaml"`）
- Produces: `tax.invoice_class(date: str, has_registration: bool, amount: int, small_exemption: bool, transitions) -> str`：登録番号あり→`適格`。無しで `small_exemption` かつ `amount < 10000`（税込）→`少額特例`。それ以外は `date` が入る経過措置の `label`、どれにも入らなければ `なし`
- 設定ファイルの中身（2026-10-26 時点。実装時に国税庁の資料で区切りと割合を確かめ、`根拠URL` に国税庁のページ、`確認日` にその日を書く。食い違えば国税庁を正とし、このプランと設計書を直す）：

```yaml
根拠URL: ""      # 国税庁「インボイス制度に関する経過措置」等のURL
確認日: ""
経過措置:
  - {開始: "2023-10-01", 終了: "2026-09-30", 区分: 経過80, 割合: 80}
  - {開始: "2026-10-01", 終了: "2028-09-30", 区分: 経過70, 割合: 70}
  - {開始: "2028-10-01", 終了: "2030-09-30", 区分: 経過50, 割合: 50}
  - {開始: "2030-10-01", 終了: "2031-09-30", 区分: 経過30, 割合: 30}
```

- [ ] **Step 1: 失敗するテスト** — `test_categories_cover_spec`（上の表の22区分がすべてあり、kind・rate が一致）、`test_with_rate`、`test_invoice_class_boundaries`：

```python
def test_invoice_class_boundaries():
    t = load_transitions()
    assert invoice_class("2026-09-30", False, 50000, False, t) == "経過80"
    assert invoice_class("2026-10-01", False, 50000, False, t) == "経過70"
    assert invoice_class("2031-10-01", False, 50000, False, t) == "なし"
    assert invoice_class("2026-10-01", True, 50000, False, t) == "適格"
    assert invoice_class("2026-10-01", False, 9999, True, t) == "少額特例"
    assert invoice_class("2026-10-01", False, 10000, True, t) == "経過70"
    assert invoice_class("2026-10-01", False, 9999, False, t) == "経過70"
```

  `test_transition_file_overlap_rejected`（期間が重なる・開始>終了の設定は `KessanError`。tmp のyamlで）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全テスト PASS**
- [ ] **Step 5: Commit**

### Task 4: 科目マスタの既定税区分と、パターンの税区分・インボイス

**Files:**
- Modify: `accounts-master.csv`、`accounts.py`、`patterns.py`
- Test: `tests/test_accounts.py`、`tests/test_patterns.py`

**Interfaces:**
- Produces: `accounts.Account` に `default_tax: str = ""`（マスタの `既定税区分` 列。空可。値は `TAX_CATEGORIES` のどれか、違えば `KessanError`）
- Produces: `patterns.Pattern` に `tax: str = ""`、`invoice: str = ""`（yaml の `税区分`・`インボイス`。任意。値は `TAX_CATEGORIES`・`INVOICE_VALUES` で検証）。`auto_approve` は、課税の年度（呼び出し側が `tax_settings` を渡す：`auto_approve(..., tax_settings=None)`）で一致したパターンに `税区分`／`インボイス` があれば、相手側の税の列が空のときだけ書く
- マスタの既定税区分（この表のとおり埋める。ここに無い科目は空）：

| 既定税区分 | 科目 |
|---|---|
| 課税売上10% | 売上高、雑収入 |
| 非課税売上 | 受取利息、有価証券売却益 |
| 対象外 | 受取配当金、役員報酬、給料手当、法定福利費、寄付金、租税公課、減価償却費、法人税等、有価証券売却損、雑損失 |
| 非課税仕入 | 保険料、支払利息、土地 |
| 課税仕入10% | 建物、建物附属設備、車両運搬具、工具器具備品、ソフトウエア、外注費、広告宣伝費、旅費交通費、車両費、通信費、消耗品費、修繕費、地代家賃、リース料、水道光熱費、交際費、会議費、福利厚生費、新聞図書費、諸会費、支払手数料、雑費 |

  固定資産売却益・固定資産売却損は空にする（消費税は売却額の総額が課税売上になり、損益の額とは違うため。空のままだと検算がNGにするので、人が判断して `set-accounts`／`set-tax` で入れる）。地代家賃の住宅貸付・諸会費の対価性なし等の例外は、パターンかAIの `set-accounts` で個別に直す

- [ ] **Step 1: 失敗するテスト** — `test_default_tax_loaded`（売上高→課税売上10%、給料手当→対象外、普通預金→空）、`test_invalid_default_tax_rejected`（tmp のマスタで未知の値→エラー）、`test_pattern_tax_fields_validated`（`税区分: 課税10` →エラー）、`test_auto_approve_writes_pattern_tax`（課税の設定で、`税区分: 非課税仕入`・`インボイス: 適格` を持つパターンに一致した行の相手側に書かれる。免税の設定では書かれない）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装** — `cli._auto_approve` で `load_tax_settings(year_dir)` を渡す
- [ ] **Step 4: 全テスト PASS**
- [ ] **Step 5: Commit**

### Task 5: 読み取り結果の税率・登録番号と、税区分の自動補完

**Files:**
- Modify: `extracted.py`（領収書・請求書の任意項目）、`extracted_import.py`（evidence.csv に書く）
- Create: `.claude/scripts/kessan/tax_fill.py`
- Modify: `cli.py`（呼び出し）
- Test: `tests/test_extracted.py`、`tests/test_tax_fill.py`（新規）

**Interfaces:**
- 読み取り結果（領収書・請求書）に任意項目：`"税率"`（`10`／`8`／`"混在"`）、`"登録番号"`（`T` ＋13桁の数字。違う形はエラー）。`_evidence_row` で evidence.csv の `税率`・`登録番号` に入れる（無ければ空）
- Produces: `tax_fill.fill_tax(year_dir, accounts, settings: TaxSettings, transitions) -> FillResult`（`FillResult`：`filled: int`、`flagged: int`）。課税の年度だけ動く（免税なら何もせず 0,0）。staging.csv の**未承認の行**を対象に、各側（借方・貸方）について：
  1. 科目が収益・費用、または表示区分が有形固定資産・無形固定資産で、その側の金額が0でなく、`税区分` が空なら、科目の `default_tax` を入れる
  2. その行の `取り込み元ID` に対応する evidence 行（`状態` が 明細に対応／新規仕訳）があり `税率` が `8` なら、`with_rate(税区分, 8)`。`混在` なら税区分は変えず要確認理由に `税率が複数（split-tax で分ける）`
  3. 税区分の kind が課税仕入（返還を含む）で `インボイス` が空なら：evidence があれば `invoice_class(日付, 登録番号の有無, 金額, settings.small_exemption, transitions)`、無ければ空のままにして要確認理由に `証憑が無い課税仕入（インボイスを確認）`
  4. `settings.allocation == "個別対応"` で課税仕入の `個別対応` が空なら `共通`
  5. 科目が 会議費・交際費・福利厚生費・新聞図書費 で、evidence の `税率` が空なら要確認理由に `軽減税率の可能性（証憑で税率を確認）`
  6. 既に入っている税区分が科目の `default_tax` と違えば要確認理由に `税区分が科目の既定（<既定>）と違う`
  - 空欄だけを埋め、既に入っている値は書き換えない。要確認理由は `add_reasons` で追記（何度呼んでも同じ結果）
  - 変更があれば `ensure_writable` → `replace_rows` で staging.csv を保存
- 呼び出し：`cli` の `import-bank`・`import-extracted`・`set-accounts`・`apply-review`・`approve`・`review`・`post` の処理の後（post は登録の**前**）に `fill_tax` を呼ぶ。課税の年度で結果があれば `税区分を補完: N件／要確認: M件` を表示

- [ ] **Step 1: 失敗するテスト** — `test_extracted.py`：`test_receipt_tax_rate_and_registration`（`税率: 8`・`登録番号: "T1234567890123"` は通る、`登録番号: "1234"` はエラー、`税率: 5` はエラー）。`test_tax_fill.py`（年度は課税・原則・割戻し・全額控除で init）：
  - `test_fills_default_tax_from_account`（消耗品費／普通預金 の行 → 借方税区分=課税仕入10%、貸方は空）
  - `test_receipt_rate_8_sets_reduced`（evidence の税率8 → 課税仕入8%軽）
  - `test_mixed_rate_flagged`
  - `test_invoice_from_evidence`（登録番号ありの証憑 → 適格、無し・2026-10-15・5万円 → 経過70）
  - `test_no_evidence_flagged`（明細だけの課税仕入 → インボイス空・要確認理由あり）
  - `test_does_not_overwrite_and_is_idempotent`（手で入れた税区分は残り、2回呼んでも staging.csv が同じ）
  - `test_exempt_year_does_nothing`
  - `test_allocation_default_common`（控除=個別対応 → 個別対応=共通）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全テスト PASS**
- [ ] **Step 5: Commit**

### Task 6: 検算に税の項目を足す

**Files:**
- Modify: `check.py`
- Test: `tests/test_check.py`

**Interfaces:**
- Consumes: `tax.load_tax_settings`、`TAX_CATEGORIES`、`INVOICE_VALUES`、`ALLOCATION_VALUES`
- Produces: `check.check_tax(entries, accounts, settings) -> list[Finding]`。`run_checks` の最後に足す（`run_checks` の中で `load_tax_settings(year_dir)` を読む。引数は変えない）。課税の年度だけ、journal＋adjustments の各行・各側で：
  - 税区分が `TAX_CATEGORIES` に無い値 → NG「伝票N: 借方税区分「…」は使えない値」
  - 科目が収益・費用で金額があるのに税区分が空 → NG「伝票N: 借方 消耗品費 の税区分が空」
  - 区分の矛盾 → NG。収益科目に使える kind：課税売上・免税売上・非課税売上・非課税資産輸出・課税売上返還・貸倒回収・対象外。費用科目に使える kind：課税仕入・課税仕入返還・非課税仕入・貸倒・対象外。資産・負債・純資産科目には 課税仕入・課税仕入返還・非課税仕入・課税売上・非課税売上・対象外 か空
  - kind が課税仕入（返還含む）でインボイスが空、または `INVOICE_VALUES` に無い → NG
  - `settings.allocation == "個別対応"` で課税仕入の個別対応が空、または `ALLOCATION_VALUES` に無い → NG
  - 免税の年度は何も返さない
- 項目名（`Finding.item`）は `税区分`

- [ ] **Step 1: 失敗するテスト** — `test_exempt_year_skips_tax_checks`（既存の helpers の帳簿で、免税→税のFindingなし）、`test_missing_tax_is_ng`、`test_wrong_kind_is_ng`（売上高 に 課税仕入10%）、`test_missing_invoice_is_ng`、`test_allocation_required_when_kobetsu`、`test_valid_taxable_journal_passes`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全テスト PASS**
- [ ] **Step 5: Commit**

### Task 7: 税の列を直すコマンド（set-accounts の拡張・set-tax・split-tax）

**Files:**
- Modify: `accounts_update.py`、`cli.py`
- Create: `.claude/scripts/kessan/tax_edit.py`
- Test: `tests/test_accounts_update.py`、`tests/test_tax_edit.py`（新規）

**Interfaces:**
- `accounts_update.SET_FIELDS` に `TAX_COLUMNS` の6列を足す。値は空か、税区分は `TAX_CATEGORIES`、インボイスは `INVOICE_VALUES`、個別対応は `ALLOCATION_VALUES`（違えば今の形式で全件エラー）
- Produces: `tax_edit.set_journal_tax(year_dir, updates: list[dict]) -> int`：登録済みの journal.csv・adjustments.csv の税の列だけを直す。`updates` は `[{"伝票番号": "12", "行": 1, "借方税区分": "…", ...}]`（`行` は伝票内の1からの行番号）。書ける列は `TAX_COLUMNS` だけ。全件検証してから一括反映。直した行数を返す。CLI：`set-tax --year-dir <Y> --file <json>`（AIは `output/set-tax-YYYYMMDD.json` に書く）
- Produces: `tax_edit.split_tax(year_dir, source_id: str, parts: list[tuple[int, int]], payment_accounts) -> str`：staging.csv の未承認の1行（取り込み元ID で特定。複数行・承認済み・既に伝票番号ありはエラー）を、税率ごとに分けた複合仕訳にする。`parts` は `[(税率, 税込金額), ...]`（税率は 10／8、合計がその行の相手側の金額と一致しなければエラー）。明細側（`payment_accounts` に入っている側）は1行に残し、相手側を `parts` の数の行に分ける（相手側の行は明細側の金額を空にする）。各行の税区分は相手側の今の税区分（空なら科目の既定）を `with_rate` で替える。全行に同じ `取り込み元ID` と、staging 内で未使用の `伝票番号`（`S1`, `S2`…）を付ける。要確認理由から `税率が複数（split-tax で分ける）` を外す。付けた伝票番号を返す。CLI：`split-tax --year-dir <Y> --id <取り込み元ID> --part 10:1080 --part 8:540`

- [ ] **Step 1: 失敗するテスト** — `test_set_accounts_accepts_tax_fields`・`test_set_accounts_rejects_bad_tax_value`。`test_tax_edit.py`：`test_set_tax_updates_journal_rows`（2行の伝票の2行目だけ直る、他の列は変わらない）、`test_set_tax_rejects_non_tax_column`、`test_split_tax_makes_compound_voucher`（消耗品費 1620／現金 1620 を `10:1080, 8:540` → 3行、借方 消耗品費 1080 課税仕入10%・借方 消耗品費 540 課税仕入8%軽・貸方 現金 1620、伝票番号は同じ S1、取り込み元IDは同じ）、`test_split_tax_amount_mismatch`、`test_split_then_post`（split した行を承認して post → journal に1伝票3行、検算の貸借一致NGなし）
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**
- [ ] **Step 4: 全テスト PASS**
- [ ] **Step 5: Commit**

### Task 8: 消費税の集計表（tax-summary）

**Files:**
- Create: `.claude/scripts/kessan/tax_summary.py`
- Modify: `cli.py`、`tax.py`（税率表の読み込み）
- Create: `config/consumption-tax/rates.yaml`
- Test: `tests/test_tax_summary.py`（新規）

**Interfaces:**
- `config/consumption-tax/rates.yaml`：`根拠URL`・`確認日`、`税率: {10: {国税: "7.8", 地方: "2.2"}, 8: {国税: "6.24", 地方: "1.76"}}`（百分率を文字列で持ち `decimal.Decimal` で読む）。`national = floor(net × 国税 / 100)`、`local = tax − national`。`tax.load_rates()` で読む
- Produces: `tax_summary.SummaryLine`（`kind`、`category`、`rate`、`invoice`、`allocation`、`count`、`gross`（税込）、`net`（税抜）、`tax`（消費税額）、`national`、`local`、`ratio`（控除割合。経過措置以外は100）、`deductible`（控除対象税額））
- Produces: `tax_summary.summarize(year_dir, accounts, settings, transitions, rates) -> TaxSummary`（`lines: list[SummaryLine]`、`taxable_sales_ratio`（課税売上割合。`Decimal`）、`estimate: int | None`（概算納付税額）、`notes: list[str]`）
- 集計の規則：
  - 対象は journal.csv＋adjustments.csv。各行・各側で税区分のあるもの（対象外・空は除く）
  - 符号：売上系の kind（課税売上・免税売上・非課税売上・非課税資産輸出・貸倒回収）は貸方を＋、借方を−。仕入系（課税仕入・非課税仕入・貸倒）と返還（課税売上返還・課税仕入返還）は借方を＋、貸方を−
  - 割戻し：(税区分, インボイス, 個別対応) ごとの税込合計から `tax = floor(gross × rate / (100+rate))`、`net = gross − tax`。マイナスの合計は絶対値で計算して符号を戻す
  - 積上げ（`settings.calc == "積上げ"`）：課税仕入・課税仕入返還だけ行ごとに税額を計算して合計する。売上は割戻しのまま、`notes` に「売上は割戻しで計算（積上げは請求書ごとの税額が要るため未対応）」
  - 経過措置：`invoice` が経過XX のとき `ratio` は Transition の割合、`deductible = floor(tax × ratio / 100)`。`なし` は 0、`適格`・`少額特例` は100%
  - 課税売上割合 =（課税売上の税抜 − 課税売上返還の税抜 ＋ 免税売上 ＋ 非課税資産輸出）÷（その分子 ＋ 非課税売上）。分母0なら 1
  - 概算納付税額（原則）：売上税額（課税売上の税額 − 返還の税額 − 貸倒の税額 ＋ 貸倒回収の税額）− 控除対象仕入税額。控除対象仕入税額は 全額控除＝課税仕入の deductible 合計（返還を引く）、個別対応＝課税対応＋共通×割合、一括比例配分＝全体×割合（各 floor）。2割特例＝売上税額×80%を引いた額（floor）。簡易＝`estimate=None`・`notes` に「簡易課税は事業区分が要るため概算しない（達人で計算）」
  - 全額控除なのに課税売上割合が95%未満 → `notes` に警告
  - 経過70・経過30 の行があれば `notes` に「経過措置70%・30%分は消費税の達人のXMLに欄が無いため、達人に手で入れる」
- Produces: `tax_summary.write_summary(year_dir, summary) -> tuple[Path, Path]`：`output/consumption-tax-summary.csv` と `.xlsx`（同じ内容。末尾に課税売上割合・概算納付税額・notes）。CLI：`tax-summary --year-dir <Y>`。免税の年度は `KessanError`「免税の年度です（period.yaml の消費税）」。検算にNGがあれば出さずにNGの件数を示して止める

- [ ] **Step 1: 失敗するテスト**（課税・原則・割戻し・全額控除の年度に、テスト内で journal.csv を直接書いて作る）：
  - `test_warimodoshi_10_and_8`：売上 110,000（課税売上10%）、仕入 10,800（課税仕入8%軽・適格）、22,000（課税仕入10%・適格）→ 売上 net 100,000 tax 10,000、仕入8% tax 800、10% tax 2,000、estimate 7,200
  - `test_summary_nets_returns`：売上 110,000 と 返品（借方 売上高 11,000 課税売上10%）→ 課税売上10% の gross 99,000
  - `test_transition_ratio`：仕入 55,000 課税仕入10% 経過80 → tax 5,000、deductible 4,000
  - `test_kobetsu_and_ratio`：非課税売上 100,000 を足し、控除=個別対応・共通の仕入 → 課税売上割合と共通×割合
  - `test_niwari`（2割特例 → 売上税額10,000 なら estimate 2,000）、`test_kanni_no_estimate`
  - `test_tsumiage_purchases`：積上げで 仕入 3行 各 1,099（10%）→ 行ごと floor(99.9)=99 ×3=297（割戻しなら floor(3297×10/110)=299）
  - `test_exempt_year_rejected`、`test_writes_csv_and_xlsx`
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装** — 金額の計算は整数と `Decimal`（float を使わない）
- [ ] **Step 4: 全テスト PASS**
- [ ] **Step 5: Commit**

### Task 9: 確認用Excel・通しテスト・手順書

**Files:**
- Modify: `review_xlsx.py`（課税の年度なら確認シートに 借方税区分・貸方税区分・インボイス（借方・貸方をまとめた表示）の列を足す。指紋（`FINGERPRINT` の元の列）に税の6列を足し、出力後に税の列が変わった行は承認を反映しない）
- Modify: `.claude/scripts/kessan/README.md`、`.claude/skills/kessan-books/SKILL.md`（税率・登録番号の読み取り、`split-tax`・`set-tax`・`tax-summary`、課税の年度の要確認理由の扱い、init の消費税の引数）
- Create: `tests/test_taxable_flow.py`
- Modify: `docs/superpowers/specs/2026-10-26-kessan-stage3-closing-design.md`（§3.1 のキーを日本語に、§3.3 の表と実装の差があれば合わせる）

**Interfaces:**
- Consumes: 上の全タスク

- [ ] **Step 1: 失敗するテスト** — `test_review.py` に `test_review_shows_tax_columns_when_taxable`（免税では列が無い）と `test_tax_change_blocks_approval`。`test_taxable_flow.py`：課税の年度で、helpers の銀行CSV（売上入金・手数料）＋8%の領収書（登録番号あり）＋混在の領収書を取り込み → `set-accounts` → `split-tax` → `approve` → `post` → `check` にNGなし → `tax-summary` の estimate と各行が手計算の正解と一致
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装と手順書の更新**
- [ ] **Step 4: 全テスト PASS**（`python -m pytest -q`）
- [ ] **Step 5: `codex-review` スキルを通し、妥当な指摘を直してから Commit・push**
