# 決算の仕組み 段階0・1（達人の取り込み仕様確認／帳簿の基盤）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 銀行CSVを取り込み、承認済みの仕訳を帳簿CSVに登録し、検算と試算表まで出せるPythonの基盤を作る。あわせて達人シリーズの取り込み仕様を確認して記録する。

**Architecture:** `.claude/scripts/kessan/` に責務ごとの小さなPythonモジュール（CSV入出力・科目マスタ・銀行取り込み・登録・残高集計・試算表・検算・CLI）を置く。帳簿データはインスタンスの `work/kessan/<年度>/` 配下のBOM付きUTF-8のCSV。数字はすべてスクリプトで計算し、AIは計算しない。

**Tech Stack:** Python 3.12（標準ライブラリ＋PyYAML）、pytest。いずれもこのPCにインストール済み（`python` で 3.12 が起動し、`pytest 9.1.1`・`PyYAML` が使える）。

**Spec:** `docs/superpowers/specs/2026-09-16-kessan-without-accounting-system-design.md`（段階0・1。§4〜§7、§11〜§13）

## Global Constraints

- CSVはすべてBOM付きUTF-8（`utf-8-sig`）で書く（Excelで文字化けせずに開ける）。追記時は途中にBOMを入れない。
- 金額は円の整数。CSV上は文字列、計算時に `to_int` で整数化する（カンマ・`¥` を許容）。
- 日付は `YYYY-MM-DD`。
- 科目名は `accounts-master.csv` に存在するもののみ有効。
- テストデータ・テンプレートに実在のクライアント名・口座・金額を入れない（コミット時に leak-scan が走る）。
- leak-scan は固有語を部分一致で検出するため、`skipped` のように固有語を含む英単語が識別子に入るとコミットが止まる。本計画のコードでは `duplicates`・`zero_amount` 等に言い換えてある。新しく名前を付けるときも、止められたら言い換える（`leak-scan:allow` で逃がさない）。ユーザーのホームディレクトリの絶対パスも書かない。
- `claude -p` やSlack通知はこの段階では使わない（対話で使うCLIのみ）。
- CLAUDE.md の運用：**コミットのたびに `git push origin master` も行う**。push が権限で止められた場合は、コミットだけ済ませてオーナーに push を依頼し、作業は続ける。
- テストの実行コマンドはリポジトリルート（`keiri-hisho/`）で `python -m pytest .claude/scripts/kessan/tests -v`。

## ファイル構成

| パス | 責務 |
|---|---|
| `.claude/scripts/kessan/common.py` | 列定義、CSVの読み書き、`to_int`、`KessanError`、年度フォルダの初期化 |
| `.claude/scripts/kessan/accounts-master.csv` | 汎用の勘定科目マスタ（マイクロ法人向け） |
| `.claude/scripts/kessan/accounts.py` | 科目マスタの読み込み、正常残高方向での増減計算 |
| `.claude/scripts/kessan/bank_import.py` | 銀行・カードCSV → `staging.csv`（重複防止、明細残高・取り込み記録） |
| `.claude/scripts/kessan/post.py` | `staging.csv` の承認済み行 → `journal.csv`（検証してから一括登録） |
| `.claude/scripts/kessan/ledger.py` | 期首残高＋仕訳から科目・補助科目別の残高を集計 |
| `.claude/scripts/kessan/trial_balance.py` | 試算表CSVの出力 |
| `.claude/scripts/kessan/check.py` | 検算（6項目）と結果レポート |
| `.claude/scripts/kessan/cli.py` | コマンド入口（init / import-bank / post / check / tb） |
| `.claude/scripts/kessan/README.md` | 使い方 |
| `.claude/scripts/kessan/tests/conftest.py`・`helpers.py`・`test_*.py` | テスト |
| `テンプレート/work/kessan/README.md` | インスタンス側の年度フォルダの説明 |
| `テンプレート/context/company/kessan-sources.yaml` | 口座と明細形式の設定の雛形 |
| `テンプレート/context/company/systems.md` | 銀行行に kessan-sources.yaml への参照を追記 |
| `.gitignore` | `*/work/kessan/*/inbox/` を追加 |
| `docs/kessan/tatsujin-import.md` | 段階0：達人の取り込み仕様メモ |

---

### Task 0: 達人シリーズの取り込み仕様を確認して記録する（段階0）

コードは書かない調査タスク。**実機での確認はオーナーにしかできない**ため、公開資料で分かることを先に整理し、実機で確かめる質問をオーナーに渡す。

**Files:**
- Create: `docs/kessan/tatsujin-import.md`

- [ ] **Step 1: 公開資料を調べる**

WebSearch で次を検索し、NTTデータ（達人シリーズ）の公式ページ・マニュアル・FAQを優先して読む。
- `決算書の達人 財務データ 読込 CSV`
- `法人税の達人 データ インポート 他社 会計ソフト`
- `勘定科目内訳明細書の達人 CSV 読込`
- `達人シリーズ 連動 汎用データ 形式`

確認したいこと：(a) 決算書の達人が他社データ（CSV等）から読み込める項目と、その形式（列の並び・文字コード・科目の対応付け方法）、(b) 法人税の達人に別表の数字を外部ファイルから入れる手段があるか、(c) 内訳明細書の達人のCSV取り込みの有無と形式、(d) 取り込みに必要な製品・オプション（有償オプションの有無）。

- [ ] **Step 2: メモを書く**

`docs/kessan/tatsujin-import.md` を次の構成で書く。調べて分かったことは出典URLを付けて書き、分からなかったことは「未確認」と明記する（推測で埋めない）。

```markdown
# 達人シリーズへの引き渡し：取り込み仕様メモ

- 調査日：YYYY-MM-DD
- ステータス：公開資料で調査済み／実機確認待ち

## 1. 決算書の達人
- 他社データの読み込み：（可否・メニュー名・形式・出典URL）
- 必要な製品・オプション：
- 科目の対応付け：

## 2. 法人税の達人（別表）
- 外部ファイルからの入力手段：
- 決算書の達人からの連動で入る項目：

## 3. 内訳明細書の達人
- CSV取り込み：（可否・形式・出典URL）

## 4. オーナーに実機で確認してほしいこと
1. 決算書の達人の「データ読込」系メニューに、CSV等の汎用形式の選択肢があるか（あれば、画面に表示される形式の説明かサンプルファイルの保存）
2. 内訳明細書の達人で取り込めるCSVのサンプルが出力できるか
3. 現在お使いの製品・オプションの一覧

## 5. 結論（実機確認後に記入）
- 引き渡しファイルを作る対象：
- 転記一覧のみにする対象：
```

- [ ] **Step 3: オーナーに確認を依頼する**

チャットで §4 の3点をオーナーに依頼する。**回答を待つ間も Task 1 以降は進めてよい**（段階1は達人の仕様に依存しない）。回答が来たら §5 を書く。

- [ ] **Step 4: Commit**

```bash
git add docs/kessan/tatsujin-import.md
git commit -m "docs: 達人シリーズの取り込み仕様メモ（公開資料の調査、実機確認待ち）"
git push origin master
```

---

### Task 1: 共通部品と勘定科目マスタ

**Files:**
- Create: `.claude/scripts/kessan/common.py`
- Create: `.claude/scripts/kessan/accounts-master.csv`
- Create: `.claude/scripts/kessan/accounts.py`
- Create: `.claude/scripts/kessan/tests/conftest.py`
- Test: `.claude/scripts/kessan/tests/test_common.py`
- Test: `.claude/scripts/kessan/tests/test_accounts.py`
- Modify: `.gitignore`（末尾の「実データ」節に1行追加）

**Interfaces:**
- Produces:
  - `common.KessanError(Exception)`
  - `common.JOURNAL_COLUMNS: list[str]`、`STAGING_COLUMNS`、`IMPORT_LOG_COLUMNS`、`STATEMENT_BALANCE_COLUMNS`、`OPENING_COLUMNS`
  - `common.read_rows(path: Path) -> list[dict[str, str]]`（ファイルが無ければ `[]`）
  - `common.write_rows(path: Path, columns: list[str], rows: list[dict]) -> None`
  - `common.append_rows(path: Path, columns: list[str], rows: list[dict]) -> None`
  - `common.project(row: dict, columns: list[str]) -> dict[str, str]`
  - `common.to_int(value) -> int`
  - `common.init_year_dir(year_dir: Path) -> None`
  - `accounts.Account(name, category, normal_side, section)`（frozen dataclass。category は 資産/負債/純資産/収益/費用、normal_side は 借/貸）
  - `accounts.load_accounts(path: Path = MASTER_PATH) -> dict[str, Account]`（マスタの行順を保持）
  - `accounts.normal_delta(account: Account, debit: int, credit: int) -> int`
  - pytest fixture：`accounts`（`load_accounts()` の結果）、`year_dir`（初期化済みの一時年度フォルダ `tmp_path/"2026-03期"`）

- [ ] **Step 1: conftest と失敗するテストを書く**

`.claude/scripts/kessan/tests/conftest.py`：

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import load_accounts  # noqa: E402
from common import init_year_dir  # noqa: E402


@pytest.fixture
def accounts():
    return load_accounts()


@pytest.fixture
def year_dir(tmp_path):
    d = tmp_path / "2026-03期"
    init_year_dir(d)
    return d
```

`.claude/scripts/kessan/tests/test_common.py`：

```python
from common import JOURNAL_COLUMNS, append_rows, init_year_dir, project, read_rows, to_int, write_rows


def test_to_int_handles_commas_yen_and_blank():
    assert to_int("1,234") == 1234
    assert to_int("¥5,000") == 5000
    assert to_int("") == 0
    assert to_int(None) == 0


def test_append_after_write_keeps_single_bom(tmp_path):
    path = tmp_path / "a.csv"
    write_rows(path, ["x", "y"], [{"x": "1", "y": "あ"}])
    append_rows(path, ["x", "y"], [{"x": "2", "y": "い"}])
    assert path.read_bytes().count(b"\xef\xbb\xbf") == 1
    assert read_rows(path) == [{"x": "1", "y": "あ"}, {"x": "2", "y": "い"}]


def test_append_to_missing_file_writes_header(tmp_path):
    path = tmp_path / "new.csv"
    append_rows(path, ["x"], [{"x": "1"}])
    assert read_rows(path) == [{"x": "1"}]


def test_read_missing_file_returns_empty(tmp_path):
    assert read_rows(tmp_path / "none.csv") == []


def test_project_fills_missing_and_drops_extra():
    assert project({"a": "1", "z": "9"}, ["a", "b"]) == {"a": "1", "b": ""}


def test_init_year_dir_creates_files_and_is_idempotent(tmp_path):
    d = tmp_path / "2026-03期"
    init_year_dir(d)
    write_rows(d / "journal.csv", JOURNAL_COLUMNS, [dict.fromkeys(JOURNAL_COLUMNS, "x")])
    init_year_dir(d)
    for name in ["journal.csv", "staging.csv", "import-log.csv",
                 "statement-balances.csv", "opening-balances.csv", "adjustments.csv"]:
        assert (d / name).exists()
    assert (d / "inbox").is_dir()
    assert (d / "output").is_dir()
    assert len(read_rows(d / "journal.csv")) == 1
```

`.claude/scripts/kessan/tests/test_accounts.py`：

```python
import pytest

from accounts import load_accounts, normal_delta
from common import KessanError


def test_master_has_basic_accounts(accounts):
    assert accounts["普通預金"].category == "資産"
    assert accounts["普通預金"].normal_side == "借"
    assert accounts["未払金"].normal_side == "貸"
    assert accounts["減価償却累計額"].normal_side == "貸"
    assert accounts["繰越利益剰余金"].category == "純資産"
    assert accounts["法人税等"].category == "費用"


def test_master_values_are_valid(accounts):
    assert {a.category for a in accounts.values()} == {"資産", "負債", "純資産", "収益", "費用"}
    assert {a.normal_side for a in accounts.values()} == {"借", "貸"}


def test_master_order_is_kept(accounts):
    names = list(accounts)
    assert names.index("普通預金") < names.index("未払金") < names.index("資本金")
    assert names.index("売上高") < names.index("支払手数料") < names.index("法人税等")


def test_normal_delta(accounts):
    assert normal_delta(accounts["普通預金"], debit=100, credit=30) == 70
    assert normal_delta(accounts["未払金"], debit=100, credit=30) == -70


def test_invalid_master_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("科目,区分,正常残高,表示区分\n現金,資産,右,流動資産\n", encoding="utf-8-sig")
    with pytest.raises(KessanError, match="科目マスタ"):
        load_accounts(path)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'accounts'`）

- [ ] **Step 3: 実装する**

`.claude/scripts/kessan/common.py`：

```python
"""kessan 共通部品：列定義とCSVの読み書き。

CSVはExcelでそのまま開けるよう、BOM付きUTF-8で保存する。
"""
import csv
from pathlib import Path


class KessanError(Exception):
    """利用者に内容を伝えて処理を止めるべきエラー。"""


JOURNAL_COLUMNS = [
    "伝票番号", "日付", "借方科目", "借方補助", "借方金額", "貸方科目", "貸方補助", "貸方金額",
    "摘要", "取引先", "証憑ファイル", "取り込み元ID", "登録区分", "登録日時",
]
STAGING_COLUMNS = JOURNAL_COLUMNS + ["読み取り信頼度", "判定", "要確認理由", "承認"]
IMPORT_LOG_COLUMNS = ["取り込み日時", "ファイル名", "口座ID", "対象期間", "件数", "入金合計", "出金合計", "登録伝票番号範囲"]
STATEMENT_BALANCE_COLUMNS = ["科目", "補助", "日付", "残高", "ファイル名"]
OPENING_COLUMNS = ["科目", "補助", "残高"]

YEAR_FILES = {
    "journal.csv": JOURNAL_COLUMNS,
    "adjustments.csv": JOURNAL_COLUMNS,
    "staging.csv": STAGING_COLUMNS,
    "import-log.csv": IMPORT_LOG_COLUMNS,
    "statement-balances.csv": STATEMENT_BALANCE_COLUMNS,
    "opening-balances.csv": OPENING_COLUMNS,
}


def read_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path, columns, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def append_rows(path, columns, rows):
    path = Path(path)
    if not path.exists():
        write_rows(path, columns, rows)
        return
    with path.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=columns).writerows(rows)


def project(row, columns):
    return {c: row.get(c, "") for c in columns}


def to_int(value):
    s = str(value or "").replace(",", "").replace("¥", "").replace("\\", "").strip()
    return int(s) if s else 0


def init_year_dir(year_dir):
    year_dir = Path(year_dir)
    for sub in ("inbox", "output"):
        (year_dir / sub).mkdir(parents=True, exist_ok=True)
    for name, columns in YEAR_FILES.items():
        if not (year_dir / name).exists():
            write_rows(year_dir / name, columns, [])
```

`.claude/scripts/kessan/accounts-master.csv`（BOM付きUTF-8で保存。Writeツールで書いた後、`python -c "import pathlib; p=pathlib.Path('.claude/scripts/kessan/accounts-master.csv'); p.write_text(p.read_text(encoding='utf-8'), encoding='utf-8-sig')"` でBOMを付ける）：

```csv
科目,区分,正常残高,表示区分
現金,資産,借,流動資産
普通預金,資産,借,流動資産
定期預金,資産,借,流動資産
売掛金,資産,借,流動資産
未収入金,資産,借,流動資産
前払費用,資産,借,流動資産
立替金,資産,借,流動資産
仮払金,資産,借,流動資産
建物,資産,借,有形固定資産
建物附属設備,資産,借,有形固定資産
車両運搬具,資産,借,有形固定資産
工具器具備品,資産,借,有形固定資産
減価償却累計額,資産,貸,有形固定資産
ソフトウエア,資産,借,無形固定資産
敷金,資産,借,投資その他の資産
保険積立金,資産,借,投資その他の資産
買掛金,負債,貸,流動負債
未払金,負債,貸,流動負債
未払費用,負債,貸,流動負債
未払法人税等,負債,貸,流動負債
預り金,負債,貸,流動負債
仮受金,負債,貸,流動負債
短期借入金,負債,貸,流動負債
役員借入金,負債,貸,固定負債
長期借入金,負債,貸,固定負債
資本金,純資産,貸,株主資本
繰越利益剰余金,純資産,貸,株主資本
売上高,収益,貸,売上高
受取利息,収益,貸,営業外収益
雑収入,収益,貸,営業外収益
役員報酬,費用,借,販売費及び一般管理費
給料手当,費用,借,販売費及び一般管理費
法定福利費,費用,借,販売費及び一般管理費
外注費,費用,借,販売費及び一般管理費
旅費交通費,費用,借,販売費及び一般管理費
通信費,費用,借,販売費及び一般管理費
消耗品費,費用,借,販売費及び一般管理費
地代家賃,費用,借,販売費及び一般管理費
水道光熱費,費用,借,販売費及び一般管理費
保険料,費用,借,販売費及び一般管理費
交際費,費用,借,販売費及び一般管理費
会議費,費用,借,販売費及び一般管理費
新聞図書費,費用,借,販売費及び一般管理費
諸会費,費用,借,販売費及び一般管理費
支払手数料,費用,借,販売費及び一般管理費
租税公課,費用,借,販売費及び一般管理費
減価償却費,費用,借,販売費及び一般管理費
雑費,費用,借,販売費及び一般管理費
支払利息,費用,借,営業外費用
雑損失,費用,借,営業外費用
法人税等,費用,借,法人税等
```

`.claude/scripts/kessan/accounts.py`：

```python
"""勘定科目マスタ。科目の区分と、残高が正常に積み上がる側（借/貸）を持つ。"""
from dataclasses import dataclass
from pathlib import Path

from common import KessanError, read_rows

MASTER_PATH = Path(__file__).with_name("accounts-master.csv")
CATEGORIES = ("資産", "負債", "純資産", "収益", "費用")


@dataclass(frozen=True)
class Account:
    name: str
    category: str
    normal_side: str
    section: str


def load_accounts(path=MASTER_PATH):
    accounts = {}
    for r in read_rows(path):
        acc = Account(r["科目"].strip(), r["区分"].strip(), r["正常残高"].strip(), r["表示区分"].strip())
        if acc.category not in CATEGORIES or acc.normal_side not in ("借", "貸"):
            raise KessanError(f"科目マスタの値が不正です: {r}")
        accounts[acc.name] = acc
    return accounts


def normal_delta(account, debit, credit):
    return debit - credit if account.normal_side == "借" else credit - debit
```

`.gitignore` の `*/work/monthly-check/raw/` の次の行に追加：

```
*/work/kessan/*/inbox/
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests -v`
Expected: PASS（11件）

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/kessan/common.py .claude/scripts/kessan/accounts.py .claude/scripts/kessan/accounts-master.csv .claude/scripts/kessan/tests .gitignore
git commit -m "kessan: 共通部品（CSV入出力・年度フォルダ初期化）と勘定科目マスタ"
git push origin master
```

---

### Task 2: 銀行・カードCSVの取り込み

**Files:**
- Create: `.claude/scripts/kessan/bank_import.py`
- Create: `.claude/scripts/kessan/tests/helpers.py`
- Test: `.claude/scripts/kessan/tests/test_bank_import.py`

**Interfaces:**
- Consumes: `common.read_rows / append_rows / project / to_int / KessanError / STAGING_COLUMNS / STATEMENT_BALANCE_COLUMNS / IMPORT_LOG_COLUMNS`
- Produces:
  - `bank_import.ImportResult(added: int, duplicates: int, zero_amount: int)`
  - `bank_import.load_sources(path: Path) -> dict`
  - `bank_import.parse_statement(file_path: Path, fmt: dict) -> list[dict]`（各要素：`日付` str, `入金` int, `出金` int, `摘要` str, `残高` int|None, `行番号` int）
  - `bank_import.import_bank(year_dir: Path, sources_path: Path, account_id: str, file_path: Path, now: datetime | None = None) -> ImportResult`
  - `helpers.SOURCES_YAML: str`、`helpers.BANK_CSV: str`、`helpers.write_sources(dir: Path) -> Path`、`helpers.write_bank_csv(dir: Path, name="2025-04.csv", text=BANK_CSV) -> Path`
- 設定ファイル（`kessan-sources.yaml`）の形：

```yaml
formats:
  <形式名>:
    encoding: cp932          # 省略時 utf-8-sig
    header_row: 2            # 列見出しがある行（1始まり）。省略時 1
    date_format: "%Y/%m/%d"
    columns:                 # 左：内部の名前（固定）／右：CSVの列見出し
      日付: 取引日
      出金: お引出し
      入金: お預入れ
      摘要: お取引内容
      残高: 残高             # 残高列が無い形式は省略可
accounts:
  - id: <口座ID>
    format: <形式名>
    科目: 普通預金
    補助: <銀行名・支店名など>
```

- [ ] **Step 1: テスト用ヘルパーと失敗するテストを書く**

`.claude/scripts/kessan/tests/helpers.py`：

```python
"""テスト用の架空データ。実在のクライアント情報は入れない。"""
from common import JOURNAL_COLUMNS, STAGING_COLUMNS

SOURCES_YAML = """\
formats:
  sample-bank:
    encoding: cp932
    header_row: 2
    date_format: "%Y/%m/%d"
    columns:
      日付: 取引日
      出金: お引出し
      入金: お預入れ
      摘要: お取引内容
      残高: 残高
accounts:
  - id: main
    format: sample-bank
    科目: 普通預金
    補助: サンプル銀行
"""

BANK_CSV = (
    "入出金明細,\n"
    "取引日,お引出し,お預入れ,お取引内容,残高\n"
    '2025/04/01,,"100,000",フリコミ カ）テストシヨウジ,"1,100,000"\n'
    '2025/04/05,3300,,テスウリヨウ,"1,096,700"\n'
    '2025/04/05,0,0,ザンダカシヨウカイ,"1,096,700"\n'
)


def write_sources(directory):
    path = directory / "kessan-sources.yaml"
    path.write_text(SOURCES_YAML, encoding="utf-8")
    return path


def write_bank_csv(directory, name="2025-04.csv", text=BANK_CSV):
    path = directory / name
    path.write_text(text, encoding="cp932")
    return path
```

`.claude/scripts/kessan/tests/test_bank_import.py`：

```python
from datetime import datetime

import pytest

from bank_import import import_bank
from common import KessanError, read_rows
from helpers import write_bank_csv, write_sources

NOW = datetime(2026, 9, 16, 10, 0, 0)


def test_import_creates_staging_rows(year_dir, tmp_path):
    result = import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    assert (result.added, result.duplicates, result.zero_amount) == (2, 0, 1)
    deposit, fee = read_rows(year_dir / "staging.csv")
    assert deposit["日付"] == "2025-04-01"
    assert (deposit["借方科目"], deposit["借方補助"], deposit["借方金額"]) == ("普通預金", "サンプル銀行", "100000")
    assert (deposit["貸方科目"], deposit["貸方金額"]) == ("", "100000")
    assert (fee["貸方科目"], fee["貸方補助"], fee["貸方金額"]) == ("普通預金", "サンプル銀行", "3300")
    assert (fee["借方科目"], fee["借方金額"]) == ("", "3300")
    assert deposit["摘要"] == "フリコミ カ）テストシヨウジ"
    assert deposit["証憑ファイル"] == "2025-04.csv"
    assert deposit["取り込み元ID"].startswith("bank:")
    assert (deposit["読み取り信頼度"], deposit["判定"], deposit["要確認理由"], deposit["承認"]) == ("高", "要確認", "相手科目未設定", "")


def test_import_records_log_and_statement_balances(year_dir, tmp_path):
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    (log,) = read_rows(year_dir / "import-log.csv")
    assert log["取り込み日時"] == "2026-09-16T10:00:00"
    assert (log["ファイル名"], log["口座ID"], log["対象期間"]) == ("2025-04.csv", "main", "2025-04-01〜2025-04-05")
    assert (log["件数"], log["入金合計"], log["出金合計"], log["登録伝票番号範囲"]) == ("2", "100000", "3300", "")
    balances = read_rows(year_dir / "statement-balances.csv")
    assert [(b["科目"], b["補助"], b["日付"], b["残高"]) for b in balances] == [
        ("普通預金", "サンプル銀行", "2025-04-01", "1100000"),
        ("普通預金", "サンプル銀行", "2025-04-05", "1096700"),
    ]


def test_reimport_same_file_adds_nothing(year_dir, tmp_path):
    sources, csv_path = write_sources(tmp_path), write_bank_csv(tmp_path)
    import_bank(year_dir, sources, "main", csv_path, now=NOW)
    result = import_bank(year_dir, sources, "main", csv_path, now=NOW)
    assert (result.added, result.duplicates) == (0, 2)
    assert len(read_rows(year_dir / "staging.csv")) == 2
    assert len(read_rows(year_dir / "import-log.csv")) == 1
    assert len(read_rows(year_dir / "statement-balances.csv")) == 2


def test_rows_already_in_journal_are_not_imported(year_dir, tmp_path):
    sources, csv_path = write_sources(tmp_path), write_bank_csv(tmp_path)
    import_bank(year_dir, sources, "main", csv_path, now=NOW)
    # staging の行が登録済みになった状態を再現する
    (year_dir / "journal.csv").write_bytes((year_dir / "staging.csv").read_bytes())
    (year_dir / "staging.csv").unlink()
    assert import_bank(year_dir, sources, "main", csv_path, now=NOW).duplicates == 2


def test_identical_rows_in_same_file_are_kept(year_dir, tmp_path):
    text = "入出金明細,\n取引日,お引出し,お預入れ,お取引内容,残高\n2025/04/10,500,,ATM,\n2025/04/10,500,,ATM,\n"
    result = import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "dup.csv", text), now=NOW)
    assert result.added == 2
    ids = [r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")]
    assert len(set(ids)) == 2
    assert read_rows(year_dir / "statement-balances.csv") == []


def test_unknown_account_id(year_dir, tmp_path):
    with pytest.raises(KessanError, match="口座ID"):
        import_bank(year_dir, write_sources(tmp_path), "nope", write_bank_csv(tmp_path))


def test_missing_column(year_dir, tmp_path):
    bad = write_bank_csv(tmp_path, "bad.csv", "x\n日付,金額\n2025/04/01,100\n")
    with pytest.raises(KessanError, match="列が見つかりません"):
        import_bank(year_dir, write_sources(tmp_path), "main", bad)


def test_bad_date(year_dir, tmp_path):
    bad = write_bank_csv(tmp_path, "bad.csv", "x\n取引日,お引出し,お預入れ,お取引内容,残高\n4月1日,100,,A,\n")
    with pytest.raises(KessanError, match="日付"):
        import_bank(year_dir, write_sources(tmp_path), "main", bad)


def test_missing_sources_file(year_dir, tmp_path):
    with pytest.raises(KessanError, match="設定ファイル"):
        import_bank(year_dir, tmp_path / "none.yaml", "main", write_bank_csv(tmp_path))
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests/test_bank_import.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'bank_import'`）

- [ ] **Step 3: 実装する**

`.claude/scripts/kessan/bank_import.py`：

```python
"""銀行・カードのCSV明細を staging.csv に取り込む。

- 預金側の科目だけを埋め、相手科目は空のまま「要確認」にする（相手科目は段階2でAIが候補を付ける）
- 取り込み元ID（明細の内容から作るハッシュ）で、登録済み・取り込み済みの行を二重に取り込まない
- 明細に残高があれば statement-balances.csv に記録する（検算で帳簿残高と照合する）
"""
import csv
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from common import (
    IMPORT_LOG_COLUMNS, STAGING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    KessanError, append_rows, read_rows, to_int,
)


@dataclass(frozen=True)
class ImportResult:
    added: int
    duplicates: int
    zero_amount: int


def load_sources(path):
    path = Path(path)
    if not path.exists():
        raise KessanError(f"口座・明細形式の設定ファイルがありません: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def parse_statement(file_path, fmt):
    file_path = Path(file_path)
    if not file_path.exists():
        raise KessanError(f"明細ファイルがありません: {file_path}")
    with file_path.open(encoding=fmt.get("encoding", "utf-8-sig"), newline="") as f:
        lines = list(csv.reader(f))
    header_index = fmt.get("header_row", 1) - 1
    if header_index >= len(lines):
        raise KessanError(f"{file_path.name}: 列見出しの行（{header_index + 1}行目）がありません")
    header = [h.strip() for h in lines[header_index]]
    idx = {}
    for key, name in fmt["columns"].items():
        if name not in header:
            raise KessanError(f"{file_path.name}: 列が見つかりません「{name}」（見出し: {header}）")
        idx[key] = header.index(name)

    rows = []
    for number, line in enumerate(lines[header_index + 1:], start=header_index + 2):
        if not any(cell.strip() for cell in line):
            continue

        def get(key):
            i = idx.get(key)
            return line[i].strip() if i is not None and i < len(line) else ""

        try:
            date = datetime.strptime(get("日付"), fmt["date_format"]).strftime("%Y-%m-%d")
        except ValueError:
            raise KessanError(f"{file_path.name} {number}行目: 日付「{get('日付')}」を読めません") from None
        balance = get("残高")
        rows.append({
            "日付": date,
            "入金": to_int(get("入金")),
            "出金": to_int(get("出金")),
            "摘要": get("摘要"),
            "残高": to_int(balance) if balance else None,
            "行番号": number,
        })
    return rows


def make_source_id(account_id, row, occurrence):
    balance = "" if row["残高"] is None else str(row["残高"])
    key = "|".join([account_id, row["日付"], str(row["入金"]), str(row["出金"]), row["摘要"], balance, str(occurrence)])
    return "bank:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _staging_row(account, file_name, row, source_id):
    staged = dict.fromkeys(STAGING_COLUMNS, "")
    subject, sub = account["科目"], account.get("補助", "")
    if row["入金"]:
        amount = str(row["入金"])
        staged.update({"借方科目": subject, "借方補助": sub, "借方金額": amount, "貸方金額": amount})
    else:
        amount = str(row["出金"])
        staged.update({"貸方科目": subject, "貸方補助": sub, "貸方金額": amount, "借方金額": amount})
    staged.update({
        "日付": row["日付"], "摘要": row["摘要"], "証憑ファイル": file_name, "取り込み元ID": source_id,
        "読み取り信頼度": "高", "判定": "要確認", "要確認理由": "相手科目未設定",
    })
    return staged


def import_bank(year_dir, sources_path, account_id, file_path, now=None):
    year_dir = Path(year_dir)
    sources = load_sources(sources_path)
    accounts_cfg = {a["id"]: a for a in sources.get("accounts", [])}
    if account_id not in accounts_cfg:
        raise KessanError(f"口座IDが設定ファイルにありません: {account_id}（登録済み: {list(accounts_cfg)}）")
    account = accounts_cfg[account_id]
    fmt = sources.get("formats", {}).get(account.get("format"))
    if fmt is None:
        raise KessanError(f"口座ID {account_id} の明細形式「{account.get('format')}」が設定ファイルにありません")

    rows = parse_statement(file_path, fmt)
    file_name = Path(file_path).name
    existing = {
        r["取り込み元ID"]
        for r in read_rows(year_dir / "journal.csv") + read_rows(year_dir / "staging.csv")
        if r.get("取り込み元ID")
    }

    seen = Counter()
    new_rows, new_balances = [], []
    duplicate = zero = 0
    for row in rows:
        key = (row["日付"], row["入金"], row["出金"], row["摘要"], row["残高"])
        occurrence = seen[key]
        seen[key] += 1
        if row["入金"] == 0 and row["出金"] == 0:
            zero += 1
            continue
        source_id = make_source_id(account_id, row, occurrence)
        if source_id in existing:
            duplicate += 1
            continue
        new_rows.append((row, _staging_row(account, file_name, row, source_id)))
        if row["残高"] is not None:
            new_balances.append({
                "科目": account["科目"], "補助": account.get("補助", ""),
                "日付": row["日付"], "残高": str(row["残高"]), "ファイル名": file_name,
            })

    if new_rows:
        append_rows(year_dir / "staging.csv", STAGING_COLUMNS, [staged for _, staged in new_rows])
        append_rows(year_dir / "statement-balances.csv", STATEMENT_BALANCE_COLUMNS, new_balances)
        dates = sorted(row["日付"] for row, _ in new_rows)
        append_rows(year_dir / "import-log.csv", IMPORT_LOG_COLUMNS, [{
            "取り込み日時": (now or datetime.now()).isoformat(timespec="seconds"),
            "ファイル名": file_name,
            "口座ID": account_id,
            "対象期間": f"{dates[0]}〜{dates[-1]}",
            "件数": str(len(new_rows)),
            "入金合計": str(sum(row["入金"] for row, _ in new_rows)),
            "出金合計": str(sum(row["出金"] for row, _ in new_rows)),
            "登録伝票番号範囲": "",
        }])
    return ImportResult(added=len(new_rows), duplicates=duplicate, zero_amount=zero)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests -v`
Expected: PASS（Task 1 と合わせて20件）

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/kessan/bank_import.py .claude/scripts/kessan/tests/helpers.py .claude/scripts/kessan/tests/test_bank_import.py
git commit -m "kessan: 銀行・カードCSVの取り込み（取り込み元IDで二重取り込み防止、明細残高と取り込み記録）"
git push origin master
```

---

### Task 3: 承認済み行の帳簿への登録

**Files:**
- Create: `.claude/scripts/kessan/post.py`
- Modify: `.claude/scripts/kessan/tests/helpers.py`（末尾に `staging_row`・`line` を追加）
- Test: `.claude/scripts/kessan/tests/test_post.py`

**Interfaces:**
- Consumes: `common.*`、`accounts.load_accounts` の戻り値（`dict[str, Account]`）
- Produces:
  - `post.PostResult(vouchers: list[str], remaining: int)`
  - `post.post_approved(year_dir: Path, accounts: dict[str, Account], now: datetime | None = None) -> PostResult`
  - `helpers.staging_row(**values) -> dict`、`helpers.line(no, date, debit=None, credit=None, **extra) -> dict`（debit/credit は `(科目, 補助, 金額)` のタプル）
- 規則：
  - `承認` 列が `済` の行だけを登録する。`伝票番号` 列が同じ行は1つの伝票（複合仕訳）、空の行は1行で1伝票。
  - 1件でも不正（金額があるのに科目が空、マスタに無い科目、伝票の貸借不一致、登録済みの取り込み元ID、同じ取り込み元IDが別伝票に重複）があれば**何も登録せず** `KessanError` を送出する。
  - 伝票番号は `journal.csv` の数字の伝票番号の最大値＋1から連番。`登録区分` は `判定` が `確立済み` なら `自動`、それ以外は `確認済`。
  - 登録した行は `staging.csv` から消す。`import-log.csv` の同じファイル名の行に `登録伝票番号範囲`（`最小-最大`）を書く。

- [ ] **Step 1: ヘルパーを追加し、失敗するテストを書く**

`.claude/scripts/kessan/tests/helpers.py` の末尾に追加：

```python


def staging_row(**values):
    row = dict.fromkeys(STAGING_COLUMNS, "")
    row.update(values)
    return row


def line(no, date, debit=None, credit=None, **extra):
    row = dict.fromkeys(JOURNAL_COLUMNS, "")
    row.update({"伝票番号": no, "日付": date})
    row.update(extra)
    if debit:
        row.update({"借方科目": debit[0], "借方補助": debit[1], "借方金額": str(debit[2])})
    if credit:
        row.update({"貸方科目": credit[0], "貸方補助": credit[1], "貸方金額": str(credit[2])})
    return row
```

`.claude/scripts/kessan/tests/test_post.py`：

```python
from datetime import datetime

import pytest

from common import IMPORT_LOG_COLUMNS, JOURNAL_COLUMNS, STAGING_COLUMNS, KessanError, read_rows, write_rows
from helpers import line, staging_row
from post import post_approved

NOW = datetime(2026, 9, 16, 10, 0, 0)


def write_staging(year_dir, rows):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, rows)


def capital(**extra):
    return staging_row(日付="2025-04-01", 借方科目="現金", 借方金額="1", 貸方科目="資本金", 貸方金額="1", 承認="済", **extra)


def test_post_moves_only_approved_rows(year_dir, accounts):
    write_staging(year_dir, [
        staging_row(日付="2025-04-01", 借方科目="普通預金", 借方補助="サンプル銀行", 借方金額="100000",
                    貸方科目="売上高", 貸方金額="100000", 摘要="入金", 取り込み元ID="bank:a",
                    判定="確立済み", 承認="済", 証憑ファイル="2025-04.csv"),
        staging_row(日付="2025-04-05", 借方金額="3300", 貸方科目="普通預金", 貸方補助="サンプル銀行",
                    貸方金額="3300", 摘要="手数料", 取り込み元ID="bank:b", 判定="要確認"),
    ])
    result = post_approved(year_dir, accounts, now=NOW)
    assert result.vouchers == ["1"]
    assert result.remaining == 1
    (posted,) = read_rows(year_dir / "journal.csv")
    assert (posted["伝票番号"], posted["登録区分"], posted["登録日時"]) == ("1", "自動", "2026-09-16T10:00:00")
    assert (posted["借方科目"], posted["貸方科目"], posted["取り込み元ID"]) == ("普通預金", "売上高", "bank:a")
    assert "承認" not in posted
    (rest,) = read_rows(year_dir / "staging.csv")
    assert rest["取り込み元ID"] == "bank:b"


def test_post_composite_voucher(year_dir, accounts):
    write_staging(year_dir, [
        staging_row(伝票番号="T1", 日付="2025-04-25", 借方科目="役員報酬", 借方金額="300000", 摘要="4月役員報酬", 承認="済"),
        staging_row(伝票番号="T1", 日付="2025-04-25", 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額="280000",
                    取り込み元ID="bank:c", 承認="済"),
        staging_row(伝票番号="T1", 日付="2025-04-25", 貸方科目="預り金", 貸方金額="20000", 承認="済"),
    ])
    assert post_approved(year_dir, accounts, now=NOW).vouchers == ["1"]
    rows = read_rows(year_dir / "journal.csv")
    assert [r["伝票番号"] for r in rows] == ["1", "1", "1"]
    assert all(r["登録区分"] == "確認済" for r in rows)


def test_numbering_continues_from_journal(year_dir, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS,
               [line("7", "2025-04-01", debit=("現金", "", 1), credit=("資本金", "", 1))])
    write_staging(year_dir, [capital(), capital()])
    assert post_approved(year_dir, accounts, now=NOW).vouchers == ["8", "9"]
    assert len(read_rows(year_dir / "journal.csv")) == 3


def test_nothing_approved_is_noop(year_dir, accounts):
    write_staging(year_dir, [staging_row(日付="2025-04-01", 借方科目="現金", 借方金額="1")])
    result = post_approved(year_dir, accounts, now=NOW)
    assert (result.vouchers, result.remaining) == ([], 1)
    assert read_rows(year_dir / "journal.csv") == []


@pytest.mark.parametrize("bad, message", [
    (staging_row(日付="2025-04-05", 借方金額="3300", 貸方科目="普通預金", 貸方金額="3300", 承認="済"),
     "借方科目が未設定"),
    (staging_row(日付="2025-04-05", 借方科目="謎の科目", 借方金額="3300", 貸方科目="普通預金", 貸方金額="3300", 承認="済"),
     "科目マスタに無い借方科目「謎の科目」"),
    (staging_row(日付="2025-04-05", 借方科目="支払手数料", 借方金額="3300", 貸方科目="普通預金", 貸方金額="3000", 承認="済"),
     "借方3300≠貸方3000"),
])
def test_invalid_rows_block_everything(year_dir, accounts, bad, message):
    write_staging(year_dir, [capital(), bad])
    with pytest.raises(KessanError, match=message):
        post_approved(year_dir, accounts, now=NOW)
    assert read_rows(year_dir / "journal.csv") == []
    assert len(read_rows(year_dir / "staging.csv")) == 2


def test_source_id_already_in_journal_is_rejected(year_dir, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS,
               [line("1", "2025-04-01", debit=("現金", "", 1), credit=("資本金", "", 1), 取り込み元ID="bank:a")])
    write_staging(year_dir, [capital(取り込み元ID="bank:a")])
    with pytest.raises(KessanError, match="登録済み"):
        post_approved(year_dir, accounts, now=NOW)


def test_same_source_id_in_two_vouchers_is_rejected(year_dir, accounts):
    write_staging(year_dir, [capital(取り込み元ID="bank:a"), capital(取り込み元ID="bank:a")])
    with pytest.raises(KessanError, match="複数の伝票"):
        post_approved(year_dir, accounts, now=NOW)


def test_post_updates_import_log_range(year_dir, accounts):
    write_rows(year_dir / "import-log.csv", IMPORT_LOG_COLUMNS,
               [dict(dict.fromkeys(IMPORT_LOG_COLUMNS, ""), ファイル名="2025-04.csv")])
    write_staging(year_dir, [capital(証憑ファイル="2025-04.csv"), capital(証憑ファイル="2025-04.csv")])
    post_approved(year_dir, accounts, now=NOW)
    write_staging(year_dir, [capital(証憑ファイル="2025-04.csv")])
    post_approved(year_dir, accounts, now=NOW)
    (log,) = read_rows(year_dir / "import-log.csv")
    assert log["登録伝票番号範囲"] == "1-3"
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests/test_post.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'post'`）

- [ ] **Step 3: 実装する**

`.claude/scripts/kessan/post.py`：

```python
"""staging.csv の承認済み行を journal.csv に登録する。

1件でも不正な行があれば何も登録しない（途中まで登録された状態を作らない）。
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common import (
    IMPORT_LOG_COLUMNS, JOURNAL_COLUMNS, STAGING_COLUMNS,
    KessanError, append_rows, project, read_rows, to_int, write_rows,
)


@dataclass(frozen=True)
class PostResult:
    vouchers: list
    remaining: int


def _group(approved):
    groups = {}
    for i, r in enumerate(approved):
        key = r["伝票番号"].strip() or f"__line{i}"
        groups.setdefault(key, []).append(r)
    return groups


def _label(key, lines):
    if key.startswith("__line"):
        return f"{lines[0]['日付']} {lines[0]['摘要']}".strip()
    return f"伝票{key}"


def _validate(groups, accounts, journal):
    errors = []
    registered = {r["取り込み元ID"] for r in journal if r.get("取り込み元ID")}
    id_groups = defaultdict(set)
    for key, lines in groups.items():
        label = _label(key, lines)
        debit = credit = 0
        for r in lines:
            for side in ("借方", "貸方"):
                amount = to_int(r[f"{side}金額"])
                name = r[f"{side}科目"].strip()
                if amount and not name:
                    errors.append(f"{label}: {side}科目が未設定")
                if name and name not in accounts:
                    errors.append(f"{label}: 科目マスタに無い{side}科目「{name}」")
                if side == "借方":
                    debit += amount
                else:
                    credit += amount
            source_id = r["取り込み元ID"].strip()
            if source_id:
                id_groups[source_id].add(key)
                if source_id in registered:
                    errors.append(f"{label}: 取り込み元ID {source_id} は登録済み")
        if debit != credit:
            errors.append(f"{label}: 借方{debit}≠貸方{credit}")
    for source_id, keys in id_groups.items():
        if len(keys) > 1:
            errors.append(f"取り込み元ID {source_id} が複数の伝票に含まれている")
    return errors


def _update_import_log(year_dir, numbers_by_file):
    path = year_dir / "import-log.csv"
    log = read_rows(path)
    changed = False
    for r in log:
        numbers = numbers_by_file.get(r["ファイル名"])
        if not numbers:
            continue
        low, high = min(numbers), max(numbers)
        if r["登録伝票番号範囲"]:
            old_low, old_high = (int(x) for x in r["登録伝票番号範囲"].split("-"))
            low, high = min(low, old_low), max(high, old_high)
        r["登録伝票番号範囲"] = f"{low}-{high}"
        changed = True
    if changed:
        write_rows(path, IMPORT_LOG_COLUMNS, [project(r, IMPORT_LOG_COLUMNS) for r in log])


def post_approved(year_dir, accounts, now=None):
    year_dir = Path(year_dir)
    staging = read_rows(year_dir / "staging.csv")
    journal = read_rows(year_dir / "journal.csv")
    approved = [r for r in staging if r["承認"].strip() == "済"]
    remaining = [r for r in staging if r["承認"].strip() != "済"]
    if not approved:
        return PostResult(vouchers=[], remaining=len(remaining))

    groups = _group(approved)
    errors = _validate(groups, accounts, journal)
    if errors:
        raise KessanError("承認済みの行を登録できません（何も登録していません）:\n" + "\n".join(errors))

    next_no = max((int(r["伝票番号"]) for r in journal if r["伝票番号"].isdigit()), default=0) + 1
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    new_rows, vouchers = [], []
    numbers_by_file = defaultdict(list)
    for lines in groups.values():
        no = str(next_no)
        next_no += 1
        vouchers.append(no)
        for r in lines:
            row = project(r, JOURNAL_COLUMNS)
            row.update({
                "伝票番号": no,
                "登録区分": "自動" if r["判定"] == "確立済み" else "確認済",
                "登録日時": stamp,
            })
            new_rows.append(row)
            if r["証憑ファイル"]:
                numbers_by_file[r["証憑ファイル"]].append(int(no))

    append_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, new_rows)
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in remaining])
    _update_import_log(year_dir, numbers_by_file)
    return PostResult(vouchers=vouchers, remaining=len(remaining))
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests -v`
Expected: PASS（30件）

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/kessan/post.py .claude/scripts/kessan/tests/helpers.py .claude/scripts/kessan/tests/test_post.py
git commit -m "kessan: 承認済み行の帳簿登録（全件検証してから一括登録、伝票番号の採番、取り込み記録の更新）"
git push origin master
```

---

### Task 4: 残高集計と試算表

**Files:**
- Create: `.claude/scripts/kessan/ledger.py`
- Create: `.claude/scripts/kessan/trial_balance.py`
- Test: `.claude/scripts/kessan/tests/test_trial_balance.py`

**Interfaces:**
- Consumes: `common.read_rows / to_int`、`accounts.normal_delta`、`helpers.line`
- Produces:
  - `ledger.entry_rows(year_dir: Path) -> list[dict]`（journal.csv ＋ adjustments.csv）
  - `ledger.load_opening(year_dir: Path) -> dict[tuple[str, str], int]`（キーは `(科目, 補助)`、値は正常残高側でプラス）
  - `ledger.compute_balances(year_dir: Path, accounts, until: str | None = None) -> dict[tuple[str, str], dict]`（値：`期首残高`・`借方発生`・`貸方発生`・`期末残高` の int。`until` を指定するとその日付までの仕訳だけを集計。マスタに無い科目は含めない＝検算で別途NGにする）
  - `trial_balance.TB_COLUMNS = ["科目", "補助", "区分", "期首残高", "借方発生", "貸方発生", "期末残高"]`
  - `trial_balance.build_trial_balance(year_dir, accounts) -> list[dict]`（科目マスタ順→補助科目名順）
  - `trial_balance.write_trial_balance(year_dir, accounts) -> Path`（`output/trial-balance.csv`）

- [ ] **Step 1: 失敗するテストを書く**

`.claude/scripts/kessan/tests/test_trial_balance.py`：

```python
from common import JOURNAL_COLUMNS, OPENING_COLUMNS, read_rows, write_rows
from helpers import line
from ledger import compute_balances
from trial_balance import write_trial_balance

BANK = ("普通預金", "サンプル銀行")


def setup_books(year_dir):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=(*BANK, 100000), credit=("売上高", "", 100000)),
        line("2", "2025-04-05", debit=("支払手数料", "", 3300), credit=(*BANK, 3300)),
    ])
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS, [
        line("A1", "2026-03-31", debit=("法人税等", "", 70000), credit=("未払法人税等", "", 70000)),
    ])


def test_compute_balances(year_dir, accounts):
    setup_books(year_dir)
    balances = compute_balances(year_dir, accounts)
    assert balances[BANK] == {"期首残高": 1000000, "借方発生": 100000, "貸方発生": 3300, "期末残高": 1096700}
    assert balances[("売上高", "")]["期末残高"] == 100000
    assert balances[("未払法人税等", "")]["期末残高"] == 70000


def test_compute_balances_until_date(year_dir, accounts):
    setup_books(year_dir)
    assert compute_balances(year_dir, accounts, until="2025-04-01")[BANK]["期末残高"] == 1100000
    assert compute_balances(year_dir, accounts, until="2025-03-31")[BANK]["期末残高"] == 1000000


def test_unknown_account_is_excluded(year_dir, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS,
               [line("1", "2025-04-01", debit=("謎の科目", "", 1), credit=("資本金", "", 1))])
    balances = compute_balances(year_dir, accounts)
    assert ("謎の科目", "") not in balances
    assert balances[("資本金", "")]["期末残高"] == 1


def test_write_trial_balance(year_dir, accounts):
    setup_books(year_dir)
    path = write_trial_balance(year_dir, accounts)
    assert path == year_dir / "output" / "trial-balance.csv"
    rows = read_rows(path)
    assert [r["科目"] for r in rows] == ["普通預金", "未払法人税等", "資本金", "売上高", "支払手数料", "法人税等"]
    assert rows[0] == {"科目": "普通預金", "補助": "サンプル銀行", "区分": "資産",
                       "期首残高": "1000000", "借方発生": "100000", "貸方発生": "3300", "期末残高": "1096700"}
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests/test_trial_balance.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'ledger'`）

- [ ] **Step 3: 実装する**

`.claude/scripts/kessan/ledger.py`：

```python
"""期首残高と仕訳（journal.csv・adjustments.csv）から、科目・補助科目別の残高を集計する。"""
from collections import defaultdict
from pathlib import Path

from accounts import normal_delta
from common import read_rows, to_int


def entry_rows(year_dir):
    year_dir = Path(year_dir)
    return read_rows(year_dir / "journal.csv") + read_rows(year_dir / "adjustments.csv")


def load_opening(year_dir):
    opening = defaultdict(int)
    for r in read_rows(Path(year_dir) / "opening-balances.csv"):
        opening[(r["科目"].strip(), r["補助"].strip())] += to_int(r["残高"])
    return dict(opening)


def compute_balances(year_dir, accounts, until=None):
    totals = defaultdict(lambda: {"期首残高": 0, "借方発生": 0, "貸方発生": 0})
    for key, amount in load_opening(year_dir).items():
        totals[key]["期首残高"] += amount
    for r in entry_rows(year_dir):
        if until and r["日付"] > until:
            continue
        for side, column in (("借方", "借方発生"), ("貸方", "貸方発生")):
            name = r[f"{side}科目"].strip()
            if name:
                totals[(name, r[f"{side}補助"].strip())][column] += to_int(r[f"{side}金額"])

    balances = {}
    for key, v in totals.items():
        account = accounts.get(key[0])
        if account is None:
            continue
        closing = v["期首残高"] + normal_delta(account, v["借方発生"], v["貸方発生"])
        balances[key] = {**v, "期末残高": closing}
    return balances
```

`.claude/scripts/kessan/trial_balance.py`：

```python
"""試算表（科目・補助科目別の期首残高・発生額・期末残高）を output/trial-balance.csv に出す。"""
from pathlib import Path

from common import write_rows
from ledger import compute_balances

TB_COLUMNS = ["科目", "補助", "区分", "期首残高", "借方発生", "貸方発生", "期末残高"]


def build_trial_balance(year_dir, accounts):
    order = {name: i for i, name in enumerate(accounts)}
    balances = compute_balances(year_dir, accounts)
    rows = []
    for (name, sub), v in sorted(balances.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        rows.append({
            "科目": name, "補助": sub, "区分": accounts[name].category,
            **{k: str(v[k]) for k in ("期首残高", "借方発生", "貸方発生", "期末残高")},
        })
    return rows


def write_trial_balance(year_dir, accounts):
    path = Path(year_dir) / "output" / "trial-balance.csv"
    write_rows(path, TB_COLUMNS, build_trial_balance(year_dir, accounts))
    return path
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests -v`
Expected: PASS（34件）

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/kessan/ledger.py .claude/scripts/kessan/trial_balance.py .claude/scripts/kessan/tests/test_trial_balance.py
git commit -m "kessan: 科目・補助科目別の残高集計と試算表の出力"
git push origin master
```

---

### Task 5: 検算

**Files:**
- Create: `.claude/scripts/kessan/check.py`
- Test: `.claude/scripts/kessan/tests/test_check.py`

**Interfaces:**
- Consumes: `ledger.entry_rows / load_opening / compute_balances`、`common.read_rows / to_int`、`helpers.line`
- Produces:
  - `check.Finding(level: str, item: str, message: str)`（level は `NG` か `WARN`）
  - `check.run_checks(year_dir: Path, accounts, prev_year_dir: Path | None = None) -> list[Finding]`
  - `check.has_ng(findings) -> bool`
  - `check.write_report(year_dir: Path, findings, now: datetime | None = None) -> Path`（`output/check-result.md`、UTF-8）
- 検算項目（設計書 §7）と item 名：

| # | item | 内容 | level |
|---|---|---|---|
| 1 | 貸借一致 | 伝票番号ごとに借方金額合計＝貸方金額合計 | NG |
| 2 | 科目マスタ | 仕訳・期首残高の科目がマスタにある | NG |
| 3 | 口座残高 | statement-balances.csv の各日付の最後の残高＝その日までの帳簿残高（口座ごとに最初の不一致日と不一致日数を報告） | NG |
| 4 | 重複 | 同じ取り込み元IDが複数の伝票にある | NG |
| 4 | 重複候補 | 同じ日付・金額・取引先（空なら摘要）が複数の伝票にある | WARN |
| 5 | マイナス残高 | 資産・負債の科目・補助科目の期末残高がマイナス（純資産・収益・費用は対象外） | NG |
| 6 | 期首残高 | 期首残高の貸借不一致／前期フォルダ指定時は前期末残高（前期の当期純利益を繰越利益剰余金に加算）と不一致 | NG |
| 6 | 期首残高 | 期首残高が未登録かつ前期フォルダの指定なし | WARN |

- [ ] **Step 1: 失敗するテストを書く**

`.claude/scripts/kessan/tests/test_check.py`：

```python
from datetime import datetime

from check import has_ng, run_checks, write_report
from common import (
    JOURNAL_COLUMNS, OPENING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    init_year_dir, read_rows, write_rows,
)
from helpers import line

BANK = ("普通預金", "サンプル銀行")


def setup_clean(year_dir, extra_lines=(), balances=(("2025-04-01", "1100000"), ("2025-04-05", "1096700"))):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=(*BANK, 100000), credit=("売上高", "", 100000), 取り込み元ID="bank:1"),
        line("2", "2025-04-05", debit=("支払手数料", "", 3300), credit=(*BANK, 3300), 取り込み元ID="bank:2"),
        *extra_lines,
    ])
    write_rows(year_dir / "statement-balances.csv", STATEMENT_BALANCE_COLUMNS, [
        {"科目": BANK[0], "補助": BANK[1], "日付": d, "残高": b, "ファイル名": "2025-04.csv"} for d, b in balances
    ])


def ng_items(findings):
    return [f.item for f in findings if f.level == "NG"]


def messages(findings, item):
    return [f.message for f in findings if f.item == item]


def test_clean_books_have_no_findings(year_dir, accounts):
    setup_clean(year_dir)
    assert run_checks(year_dir, accounts) == []


def test_unbalanced_voucher(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("支払手数料", "", 500))])
    findings = run_checks(year_dir, accounts)
    assert "貸借一致" in ng_items(findings)
    assert messages(findings, "貸借一致") == ["伝票3: 借方500≠貸方0"]


def test_unknown_account(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("謎の科目", "", 500), credit=("現金", "", 500))])
    assert "科目マスタ" in ng_items(run_checks(year_dir, accounts))


def test_statement_balance_mismatch(year_dir, accounts):
    setup_clean(year_dir, balances=(("2025-04-01", "1100000"), ("2025-04-05", "1096000")))
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == ["口座残高"]
    (message,) = messages(findings, "口座残高")
    assert "2025-04-05" in message and "帳簿1096700" in message and "明細1096000" in message


def test_last_balance_of_the_day_is_used(year_dir, accounts):
    setup_clean(year_dir, balances=(("2025-04-05", "1100000"), ("2025-04-05", "1096700")))
    assert run_checks(year_dir, accounts) == []


def test_duplicate_source_id(year_dir, accounts):
    dup = dict(debit=("雑費", "", 100), credit=(*BANK, 100), 取り込み元ID="bank:z")
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", **dup), line("4", "2025-04-10", **dup)])
    findings = run_checks(year_dir, accounts)
    assert "重複" in ng_items(findings)


def test_duplicate_candidate_is_warning(year_dir, accounts):
    same = dict(debit=("支払手数料", "", 770), credit=(*BANK, 770), 取引先="テスト商事")
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", **same), line("4", "2025-04-10", **same)])
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == []
    assert [f.item for f in findings] == ["重複候補"]


def test_negative_asset_balance(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", 5000), credit=("現金", "", 5000))])
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == ["マイナス残高"]
    assert messages(findings, "マイナス残高") == ["現金（補助なし）: 期末残高-5000"]


def test_deficit_in_retained_earnings_is_not_flagged(year_dir, accounts):
    setup_clean(year_dir)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1200000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": "-200000"},
    ])
    assert run_checks(year_dir, accounts) == []


def test_unbalanced_opening(year_dir, accounts):
    setup_clean(year_dir)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS,
               [{"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"}])
    findings = run_checks(year_dir, accounts)
    assert messages(findings, "期首残高") == ["期首残高の貸借が1000000円ずれている"]


def test_missing_opening_is_warning(year_dir, accounts):
    assert [(f.level, f.item) for f in run_checks(year_dir, accounts)] == [("WARN", "期首残高")]


def setup_prev(tmp_path):
    prev = tmp_path / "2025-03期"
    init_year_dir(prev)
    write_rows(prev / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "500000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
    ])
    write_rows(prev / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2024-06-01", debit=(*BANK, 200000), credit=("売上高", "", 200000)),
        line("2", "2024-07-01", debit=("雑費", "", 50000), credit=(*BANK, 50000)),
    ])
    return prev


def write_current_opening(year_dir, retained):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "650000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": str(retained)},
    ])


def test_opening_matches_previous_year(year_dir, accounts, tmp_path):
    prev = setup_prev(tmp_path)
    write_current_opening(year_dir, 150000)
    assert run_checks(year_dir, accounts, prev_year_dir=prev) == []


def test_opening_differs_from_previous_year(year_dir, accounts, tmp_path):
    prev = setup_prev(tmp_path)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "640000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": "140000"},
    ])
    findings = run_checks(year_dir, accounts, prev_year_dir=prev)
    assert "普通預金（サンプル銀行）: 期首640000≠前期末650000" in messages(findings, "期首残高")
    assert "繰越利益剰余金（補助なし）: 期首140000≠前期末150000" in messages(findings, "期首残高")


def test_write_report(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", 5000), credit=("現金", "", 5000))])
    findings = run_checks(year_dir, accounts)
    assert has_ng(findings)
    path = write_report(year_dir, findings, now=datetime(2026, 9, 16, 10, 0))
    text = path.read_text(encoding="utf-8")
    assert path == year_dir / "output" / "check-result.md"
    assert "# 検算結果（2026-09-16 10:00）" in text
    assert "NG: 1件 / 警告: 0件" in text
    assert "- [マイナス残高] 現金（補助なし）: 期末残高-5000" in text


def test_has_ng_false_for_warnings_only(year_dir, accounts):
    assert has_ng(run_checks(year_dir, accounts)) is False
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests/test_check.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'check'`）

- [ ] **Step 3: 実装する**

`.claude/scripts/kessan/check.py`：

```python
"""帳簿の検算。NGが1件でもあれば、決算の工程（kessan-close）に進まない。"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common import read_rows, to_int
from ledger import compute_balances, entry_rows, load_opening

BS_CATEGORIES = ("資産", "負債", "純資産")


@dataclass(frozen=True)
class Finding:
    level: str
    item: str
    message: str


def _sub_label(sub):
    return sub or "補助なし"


def check_voucher_balance(entries):
    totals = defaultdict(lambda: [0, 0])
    for r in entries:
        totals[r["伝票番号"]][0] += to_int(r["借方金額"])
        totals[r["伝票番号"]][1] += to_int(r["貸方金額"])
    return [
        Finding("NG", "貸借一致", f"伝票{no}: 借方{debit}≠貸方{credit}")
        for no, (debit, credit) in totals.items() if debit != credit
    ]


def check_accounts_exist(year_dir, entries, accounts):
    findings = []
    for r in entries:
        for side in ("借方", "貸方"):
            name = r[f"{side}科目"].strip()
            if (name or to_int(r[f"{side}金額"])) and name not in accounts:
                findings.append(Finding("NG", "科目マスタ", f"伝票{r['伝票番号']}: {side}科目「{name}」が科目マスタに無い"))
    for r in read_rows(Path(year_dir) / "opening-balances.csv"):
        if r["科目"].strip() not in accounts:
            findings.append(Finding("NG", "科目マスタ", f"期首残高: 科目「{r['科目']}」が科目マスタに無い"))
    return findings


def check_statement_balances(year_dir, accounts):
    last = {}
    for r in read_rows(Path(year_dir) / "statement-balances.csv"):
        last[(r["科目"], r["補助"], r["日付"])] = to_int(r["残高"])
    mismatches = defaultdict(list)
    for (name, sub, date), expected in sorted(last.items(), key=lambda kv: kv[0][2]):
        book = compute_balances(year_dir, accounts, until=date).get((name, sub), {}).get("期末残高", 0)
        if book != expected:
            mismatches[(name, sub)].append((date, book, expected))
    findings = []
    for (name, sub), items in mismatches.items():
        date, book, expected = items[0]
        findings.append(Finding(
            "NG", "口座残高",
            f"{name}（{_sub_label(sub)}）: {date}時点で帳簿{book}≠明細{expected}"
            f"（不一致{len(items)}日。未登録の明細行・登録誤りを確認）",
        ))
    return findings


def check_duplicates(entries):
    by_id = defaultdict(set)
    by_key = defaultdict(set)
    for r in entries:
        if r["取り込み元ID"]:
            by_id[r["取り込み元ID"]].add(r["伝票番号"])
        who = r["取引先"].strip() or r["摘要"].strip()
        amount = to_int(r["借方金額"]) or to_int(r["貸方金額"])
        if who and amount:
            by_key[(r["日付"], amount, who)].add(r["伝票番号"])
    findings = [
        Finding("NG", "重複", f"取り込み元ID {source_id} が伝票{'・'.join(sorted(nos))}に重複")
        for source_id, nos in by_id.items() if len(nos) > 1
    ]
    findings += [
        Finding("WARN", "重複候補", f"{date} {amount}円 {who}: 伝票{'・'.join(sorted(nos))}")
        for (date, amount, who), nos in by_key.items() if len(nos) > 1
    ]
    return findings


def check_negative_balances(year_dir, accounts):
    return [
        Finding("NG", "マイナス残高", f"{name}（{_sub_label(sub)}）: 期末残高{v['期末残高']}")
        for (name, sub), v in compute_balances(year_dir, accounts).items()
        if accounts[name].category in ("資産", "負債") and v["期末残高"] < 0
    ]


def check_opening(year_dir, accounts, prev_year_dir=None):
    opening = load_opening(year_dir)
    if not opening and prev_year_dir is None:
        return [Finding("WARN", "期首残高",
                        "期首残高が未登録（設立初年度でなければ opening-balances.csv に前期決算書の期末残高を入れる）")]
    findings = []
    diff = sum(
        amount if accounts[name].normal_side == "借" else -amount
        for (name, _), amount in opening.items() if name in accounts
    )
    if diff:
        findings.append(Finding("NG", "期首残高", f"期首残高の貸借が{abs(diff)}円ずれている"))

    if prev_year_dir is not None:
        prev = compute_balances(prev_year_dir, accounts)
        expected = {k: v["期末残高"] for k, v in prev.items() if accounts[k[0]].category in BS_CATEGORIES}
        profit = (sum(v["期末残高"] for k, v in prev.items() if accounts[k[0]].category == "収益")
                  - sum(v["期末残高"] for k, v in prev.items() if accounts[k[0]].category == "費用"))
        retained = ("繰越利益剰余金", "")
        expected[retained] = expected.get(retained, 0) + profit
        bs_opening_keys = {k for k in opening if k[0] in accounts and accounts[k[0]].category in BS_CATEGORIES}
        for key in sorted(set(expected) | bs_opening_keys):
            actual, want = opening.get(key, 0), expected.get(key, 0)
            if actual != want:
                findings.append(Finding("NG", "期首残高",
                                        f"{key[0]}（{_sub_label(key[1])}）: 期首{actual}≠前期末{want}"))
    return findings


def run_checks(year_dir, accounts, prev_year_dir=None):
    entries = entry_rows(year_dir)
    return (
        check_voucher_balance(entries)
        + check_accounts_exist(year_dir, entries, accounts)
        + check_statement_balances(year_dir, accounts)
        + check_duplicates(entries)
        + check_negative_balances(year_dir, accounts)
        + check_opening(year_dir, accounts, prev_year_dir)
    )


def has_ng(findings):
    return any(f.level == "NG" for f in findings)


def write_report(year_dir, findings, now=None):
    now = now or datetime.now()
    ng = [f for f in findings if f.level == "NG"]
    warn = [f for f in findings if f.level == "WARN"]
    lines = [f"# 検算結果（{now:%Y-%m-%d %H:%M}）", "", f"NG: {len(ng)}件 / 警告: {len(warn)}件", ""]
    for title, group in (("NG", ng), ("警告", warn)):
        if group:
            lines += [f"## {title}", ""] + [f"- [{f.item}] {f.message}" for f in group] + [""]
    path = Path(year_dir) / "output" / "check-result.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests -v`
Expected: PASS（49件）

- [ ] **Step 5: Commit**

```bash
git add .claude/scripts/kessan/check.py .claude/scripts/kessan/tests/test_check.py
git commit -m "kessan: 検算（貸借一致・科目マスタ・口座残高・重複・マイナス残高・期首残高）とレポート出力"
git push origin master
```

---

### Task 6: CLI・通しテスト・テンプレートと使い方

**Files:**
- Create: `.claude/scripts/kessan/cli.py`
- Create: `.claude/scripts/kessan/README.md`
- Create: `テンプレート/work/kessan/README.md`
- Create: `テンプレート/context/company/kessan-sources.yaml`
- Modify: `テンプレート/context/company/systems.md`（「銀行」「クレジットカード・決済代行」の行の備考）
- Test: `.claude/scripts/kessan/tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1〜5 のすべての Produces
- Produces:
  - `cli.main(argv: list[str] | None = None) -> int`（0＝成功、1＝エラーまたは check でNGあり）
  - サブコマンド：`init` / `import-bank`（`--account-id`・`--file` 必須、`--sources` 省略時は `<年度フォルダ>/../../../context/company/kessan-sources.yaml`）/ `post`（登録後に検算も実行、`--prev-year-dir` 任意）/ `check`（`--prev-year-dir` 任意）/ `tb`。すべて `--year-dir` 必須。

- [ ] **Step 1: 失敗するテストを書く**

`.claude/scripts/kessan/tests/test_cli.py`：

```python
from cli import main
from common import OPENING_COLUMNS, STAGING_COLUMNS, read_rows, write_rows
from helpers import write_bank_csv, write_sources


def opening(year_dir, rows):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, rows)


def test_end_to_end(tmp_path):
    year = tmp_path / "インスタンス" / "work" / "kessan" / "2026-03期"
    assert main(["init", "--year-dir", str(year)]) == 0
    opening(year, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    (tmp_path / "インスタンス" / "context" / "company").mkdir(parents=True)
    write_sources(tmp_path / "インスタンス" / "context" / "company")
    csv_path = write_bank_csv(year / "inbox")
    assert main(["import-bank", "--year-dir", str(year), "--account-id", "main", "--file", str(csv_path)]) == 0

    # 人が相手科目を入れて承認した状態を再現する
    rows = read_rows(year / "staging.csv")
    rows[0].update({"貸方科目": "売上高", "承認": "済"})
    rows[1].update({"借方科目": "支払手数料", "承認": "済"})
    write_rows(year / "staging.csv", STAGING_COLUMNS, rows)

    assert main(["post", "--year-dir", str(year)]) == 0
    assert main(["check", "--year-dir", str(year)]) == 0
    assert main(["tb", "--year-dir", str(year)]) == 0
    tb = {(r["科目"], r["補助"]): r["期末残高"] for r in read_rows(year / "output" / "trial-balance.csv")}
    assert tb == {("普通預金", "サンプル銀行"): "1096700", ("資本金", ""): "1000000",
                  ("売上高", ""): "100000", ("支払手数料", ""): "3300"}
    assert (year / "output" / "check-result.md").read_text(encoding="utf-8").count("NG: 0件") == 1


def test_check_returns_1_on_ng(tmp_path):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year)])
    opening(year, [{"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"}])
    assert main(["check", "--year-dir", str(year)]) == 1
    assert "NG: 1件" in (year / "output" / "check-result.md").read_text(encoding="utf-8")


def test_error_is_reported_with_exit_1(tmp_path, capsys):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year)])
    code = main(["import-bank", "--year-dir", str(year), "--sources", str(write_sources(tmp_path)),
                 "--account-id", "nope", "--file", str(write_bank_csv(tmp_path))])
    assert code == 1
    assert "エラー: 口座IDが設定ファイルにありません" in capsys.readouterr().err


def test_uninitialized_year_dir(tmp_path, capsys):
    assert main(["check", "--year-dir", str(tmp_path / "none")]) == 1
    assert "init" in capsys.readouterr().err
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests/test_cli.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'cli'`）

- [ ] **Step 3: CLI を実装する**

`.claude/scripts/kessan/cli.py`：

```python
"""kessan：会計システムなしで帳簿を作るためのコマンド（段階1：取り込み・登録・検算・試算表）。

インスタンスフォルダで実行する例:
  python ../.claude/scripts/kessan/cli.py init --year-dir work/kessan/2026-03期
  python ../.claude/scripts/kessan/cli.py import-bank --year-dir work/kessan/2026-03期 --account-id main --file work/kessan/2026-03期/inbox/2025-04.csv
  python ../.claude/scripts/kessan/cli.py post --year-dir work/kessan/2026-03期
  python ../.claude/scripts/kessan/cli.py check --year-dir work/kessan/2026-03期 [--prev-year-dir work/kessan/2025-03期]
  python ../.claude/scripts/kessan/cli.py tb --year-dir work/kessan/2026-03期
"""
import argparse
import sys
from pathlib import Path

from accounts import load_accounts
from bank_import import import_bank
from check import has_ng, run_checks, write_report
from common import KessanError, init_year_dir
from post import post_approved
from trial_balance import write_trial_balance


def _parser():
    parser = argparse.ArgumentParser(prog="kessan")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "import-bank", "post", "check", "tb"):
        p = sub.add_parser(name)
        p.add_argument("--year-dir", required=True, type=Path)
        if name == "import-bank":
            p.add_argument("--account-id", required=True)
            p.add_argument("--file", required=True, type=Path)
            p.add_argument("--sources", type=Path)
        if name in ("post", "check"):
            p.add_argument("--prev-year-dir", type=Path)
    return parser


def _run_check(year_dir, accounts, prev_year_dir):
    findings = run_checks(year_dir, accounts, prev_year_dir)
    path = write_report(year_dir, findings)
    ng = sum(f.level == "NG" for f in findings)
    print(f"検算: NG {ng}件 / 警告 {len(findings) - ng}件（{path}）")
    return findings


def main(argv=None):
    args = _parser().parse_args(argv)
    year_dir = args.year_dir
    try:
        if args.command == "init":
            init_year_dir(year_dir)
            print(f"年度フォルダを用意しました: {year_dir}")
            return 0
        if not (year_dir / "journal.csv").exists():
            raise KessanError(f"年度フォルダが初期化されていません（先に init を実行）: {year_dir}")
        accounts = load_accounts()

        if args.command == "import-bank":
            sources = args.sources or year_dir.resolve().parents[2] / "context" / "company" / "kessan-sources.yaml"
            r = import_bank(year_dir, sources, args.account_id, args.file)
            print(f"取り込み: 追加 {r.added}件 / 取り込み済みのためスキップ {r.duplicates}件 / 金額0のためスキップ {r.zero_amount}件")
            return 0
        if args.command == "post":
            r = post_approved(year_dir, accounts)
            print(f"登録: 伝票 {len(r.vouchers)}件（{', '.join(r.vouchers) or 'なし'}） / 未承認で残った行 {r.remaining}件")
            _run_check(year_dir, accounts, args.prev_year_dir)
            return 0
        if args.command == "check":
            return 1 if has_ng(_run_check(year_dir, accounts, args.prev_year_dir)) else 0
        if args.command == "tb":
            print(f"試算表: {write_trial_balance(year_dir, accounts)}")
            return 0
    except KessanError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest .claude/scripts/kessan/tests -v`
Expected: PASS（53件）

- [ ] **Step 5: 実際のコマンドとして動くことを確認する**

一時フォルダで手動実行し、日本語が文字化けせず表示されることを確認する（Git Bash）。

```bash
T="$(mktemp -d)"
python .claude/scripts/kessan/cli.py init --year-dir "$T/work/kessan/2026-03期"
python .claude/scripts/kessan/cli.py check --year-dir "$T/work/kessan/2026-03期"; echo "exit=$?"
rm -rf "$T"
```

Expected: `年度フォルダを用意しました: ...`、`検算: NG 0件 / 警告 1件（...check-result.md）`、`exit=0`

- [ ] **Step 6: 使い方とテンプレートを書く**

`.claude/scripts/kessan/README.md`：

````markdown
# kessan：会計システムなしで帳簿を作る

設計：`docs/superpowers/specs/2026-09-16-kessan-without-accounting-system-design.md`

数字（集計・残高・検算）はすべてこのスクリプトで確定させる。AIは資料の読み取りと科目の判断だけを行い、暗算で数字を作らない。

## 年度フォルダ（インスタンスの `work/kessan/<年度>/`）

| ファイル | 中身 |
|---|---|
| `inbox/` | 受領資料の原本（gitに入れない） |
| `staging.csv` | 取り込んだ明細・仕訳候補。`承認` 列に `済` を入れた行が `post` で登録される |
| `journal.csv` | 帳簿本体。直接編集しない（`post` 経由で登録する） |
| `adjustments.csv` | 決算整理仕訳（段階3で使う） |
| `import-log.csv` | 取り込み記録 |
| `statement-balances.csv` | 明細に載っていた残高（検算で帳簿と照合） |
| `opening-balances.csv` | 期首残高（前期決算書の期末残高。残高は正常残高側をプラスで書く） |
| `output/` | `check-result.md`（検算結果）、`trial-balance.csv`（試算表） |

## 手順

インスタンスフォルダで実行する（`<Y>` は `work/kessan/2026-03期` など）。

```bash
python ../.claude/scripts/kessan/cli.py init --year-dir <Y>
# opening-balances.csv に期首残高を入れる
# context/company/kessan-sources.yaml に口座と明細形式を登録する
python ../.claude/scripts/kessan/cli.py import-bank --year-dir <Y> --account-id <口座ID> --file <Y>/inbox/<明細>.csv
# staging.csv の相手科目を埋め、確認した行の「承認」列に「済」
python ../.claude/scripts/kessan/cli.py post --year-dir <Y>     # 登録＋検算
python ../.claude/scripts/kessan/cli.py check --year-dir <Y> --prev-year-dir <前期のY>
python ../.claude/scripts/kessan/cli.py tb --year-dir <Y>
```

- 複合仕訳は、`staging.csv` の複数行に同じ `伝票番号`（例：`T1`）を入れる。登録時に正式な連番に振り直す。
- 検算でNGが1件でもあれば、決算の工程に進まない。

## テスト

リポジトリルートで `python -m pytest .claude/scripts/kessan/tests -v`
````

`テンプレート/work/kessan/README.md`：

```markdown
# 決算（会計システムを使わない会社の帳簿）

会計システムを使っていない会社の帳簿・決算は、事業年度ごとにこのフォルダの下に `<年度>/`（例：`2026-03期/`）を作って管理する。

- 作り方・手順：`../../.claude/scripts/kessan/README.md`
- 口座と明細形式の設定：`../../context/company/kessan-sources.yaml`
- `<年度>/inbox/`（受領資料の原本）はgitに入らない。原本の保管場所は `context/company/systems.md` の「ストレージ」行に書く
```

`テンプレート/context/company/kessan-sources.yaml`：

```yaml
# 会計システムを使わない会社の帳簿づくり（kessan）で使う、口座と明細CSVの形式。
# 使い方：../../.claude/scripts/kessan/README.md
# 口座番号・ID・パスワードは書かない。

formats:
  # 例：ネットバンキングの入出金明細CSV（列見出しは実際のファイルに合わせて書き換える）
  example-bank:
    encoding: cp932          # Shift-JIS のCSVは cp932、UTF-8 は utf-8-sig
    header_row: 1            # 列見出しがある行（1始まり）
    date_format: "%Y/%m/%d"
    columns:                 # 左は固定、右を実際のCSVの列見出しにする
      日付: 取引日
      出金: お引出し
      入金: お預入れ
      摘要: お取引内容
      残高: 残高             # 残高列が無い形式はこの行を消す

accounts: []
  # - id: main               # import-bank の --account-id に指定する名前
  #   format: example-bank
  #   科目: 普通預金
  #   補助: ○○銀行 ○○支店
```

`テンプレート/context/company/systems.md` の2行を次のように置き換える（接続手段・備考の列だけ変える）：

```markdown
| 銀行 | （未設定） | | 会計システムの口座連携（連携エラーは全体通知チャンネルへ）／会計システムを使わない会社は明細CSVを受領 | 振込明細の法人略語は `../.claude/rules/company-name-abbreviations.md`。会計システムを使わない会社の口座・明細形式は `kessan-sources.yaml` |
| クレジットカード・決済代行 | （未設定） | | 会計システムの連携／会計システムを使わない会社は明細CSVを受領 | 会計システムを使わない会社のカード明細形式は `kessan-sources.yaml` |
```

- [ ] **Step 7: テンプレートの yaml が読めることを確認する**

Run: `python -c "import yaml; d=yaml.safe_load(open('テンプレート/context/company/kessan-sources.yaml', encoding='utf-8')); assert d['accounts'] == [] and 'example-bank' in d['formats']; print('ok')"`
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add .claude/scripts/kessan/cli.py .claude/scripts/kessan/README.md .claude/scripts/kessan/tests/test_cli.py テンプレート/work/kessan/README.md テンプレート/context/company/kessan-sources.yaml テンプレート/context/company/systems.md
git commit -m "kessan: CLI（init/import-bank/post/check/tb）と通しテスト、テンプレートに年度フォルダの説明と明細形式設定を追加"
git push origin master
```

---

## 段階1の完了条件（設計書 §12）

- [ ] `python -m pytest .claude/scripts/kessan/tests -v` が全件PASS
- [ ] 通しテスト（`test_end_to_end`）で、サンプルCSV → staging → 承認 → journal → 検算NG0件 → 試算表 まで通る
- [ ] 検算6項目それぞれにNGになるテストがある（Task 5）
- [ ] Task 0 のメモがコミットされ、オーナーへの実機確認の依頼が済んでいる
