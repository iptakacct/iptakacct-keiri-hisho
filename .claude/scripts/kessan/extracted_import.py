"""読み取り結果ファイル（extracted/*.json）を検証してから staging.csv に取り込む（import-extracted）。

- 形式に誤りがあるファイルは丸ごと取り込まず、ファイル名と理由を返す（他のファイルの取り込みは続ける）
- 通帳・出納帳は銀行CSVと同じ共通部分（bank_import.stage_statement_rows）で取り込む。
  通帳の取り込み元IDは銀行CSVと同じ作り方なので、同じ取引をCSVと通帳の両方で受け取っても二重に入らない
- 取り込んだ資料は import-log.csv に「資料」のパス（inbox/…）で記録し、同じ資料の再取り込みは件数0にする
"""
from dataclasses import dataclass, field
from pathlib import Path

from bank_import import load_sources, normalize_description, stage_statement_rows
from common import load_period, read_rows
from extracted import check_passbook_pages, extracted_files, load_document, validate_document

PAGE_NOT_CHAINED = "ページの残高が連続しない"


@dataclass
class ExtractedImportResult:
    imported: list = field(default_factory=list)          # 取り込んだ読み取り結果ファイル名
    already_imported: list = field(default_factory=list)  # 資料が取り込み済みのため件数0だったファイル名
    rejected: dict = field(default_factory=dict)          # 形式エラーで取り込まなかったファイル名 → 理由の一覧
    unreadable: list = field(default_factory=list)        # 種類=読めない の資料（inbox/…）
    added: int = 0
    duplicates: int = 0
    zero_amount: int = 0
    low_confidence_pages: list = field(default_factory=list)  # 「inbox/… ページN」
    overlapping_imports: list = field(default_factory=list)

    def add(self, r):
        self.added += r.added
        self.duplicates += r.duplicates
        self.zero_amount += r.zero_amount
        self.overlapping_imports += r.overlapping_imports


def _statement_row(r):
    return {"日付": r["日付"], "入金": r["入金"], "出金": r["出金"],
            "摘要": normalize_description(r["摘要"]), "残高": r.get("残高")}


def _import_passbook(year_dir, data, source_accounts, result, now):
    broken = set(check_passbook_pages(data["ページ"]))
    rows = []
    for page in data["ページ"]:
        for r in page["行"]:
            row = _statement_row(r)
            if page["ページ番号"] in broken:
                row.update({"読み取り信頼度": "低", "要確認理由": PAGE_NOT_CHAINED})
            rows.append(row)
    result.low_confidence_pages += [f"{data['資料']} ページ{n}" for n in sorted(broken)]
    account = source_accounts[data["口座ID"]]
    result.add(stage_statement_rows(
        year_dir, data["口座ID"], account["科目"], account.get("補助", ""), data["資料"], rows,
        log_id=data["口座ID"], now=now, always_log=True,
    ))


def _import_cash_book(year_dir, data, result, now):
    subject, sub = data["科目"], data.get("補助", "")
    log_id = f"出納帳:{subject}" + (f"（{sub}）" if sub else "")
    result.add(stage_statement_rows(
        year_dir, f"{subject}|{sub}", subject, sub, data["資料"], [_statement_row(r) for r in data["行"]],
        log_id=log_id, now=now, always_log=True,
    ))


def import_extracted(year_dir, sources_path, accounts, files, now=None):
    year_dir = Path(year_dir)
    sources = load_sources(sources_path)
    source_accounts = {a["id"]: a for a in sources.get("accounts") or []}
    period = load_period(year_dir)
    result = ExtractedImportResult()
    for path in files:
        path = Path(path)
        data, errors = load_document(path)
        if not errors:
            errors = validate_document(data, accounts, set(source_accounts), period, year_dir)
        if errors:
            result.rejected[path.name] = errors
            continue
        if data["種類"] == "読めない":
            result.unreadable.append(data["資料"])
            continue
        if data["資料"] in {r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")}:
            result.already_imported.append(path.name)
            continue
        if data["種類"] == "通帳":
            _import_passbook(year_dir, data, source_accounts, result, now)
        elif data["種類"] == "出納帳":
            _import_cash_book(year_dir, data, result, now)
        else:
            result.rejected[path.name] = [f"種類「{data['種類']}」の取り込みには未対応です"]
            continue
        result.imported.append(path.name)
    return result


def inbox_status(year_dir):
    """inbox/ の資料ごとに (inbox/…, 状態) を返す。状態：取り込み済み／読めない／読み取り済み・未取り込み／未処理。"""
    year_dir = Path(year_dir)
    logged = {r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")}
    kinds = {}
    for path in extracted_files(year_dir):
        data, errors = load_document(path)
        if not errors and isinstance(data, dict) and isinstance(data.get("資料"), str):
            kinds[data["資料"]] = data.get("種類")
    inbox = year_dir / "inbox"
    statuses = []
    for path in sorted(p for p in inbox.rglob("*") if p.is_file() and not p.name.startswith(".")):
        rel = "inbox/" + path.relative_to(inbox).as_posix()
        if rel in logged or path.name in logged:  # 銀行CSV（import-bank）はファイル名だけで記録されている
            status = "取り込み済み"
        elif kinds.get(rel) == "読めない":
            status = "読めない"
        elif rel in kinds:
            status = "読み取り済み・未取り込み"
        else:
            status = "未処理"
        statuses.append((rel, status))
    return statuses
