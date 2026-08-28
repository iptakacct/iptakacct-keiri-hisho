#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""leak-scan: 汎用版リポジトリにクライアント固有情報・秘密が混入していないか検査する。

使い方:
  python tools/leak-scan.py            # リポジトリ全体（git管理下のファイル）を検査
  python tools/leak-scan.py --staged   # git stagedファイルのみ（pre-commit用）
  python tools/leak-scan.py --terms path/to/terms.txt   # 固有語リストを明示指定
  python tools/leak-scan.py --self-test                 # 検出器自体の動作確認

検出対象:
  1. 組み込みパターン（Slack ID/トークン/Webhook、メールアドレス、APIシークレット、
     freee company_idの実値、個人ユーザーフォルダの絶対パス）
  2. 固有語リスト（任意）。upstream/local.json の terms_file、または --terms、
     または環境変数 LEAK_SCAN_TERMS で指定。無ければ組み込みパターンのみ。
     ※ 固有語リストはこのリポジトリに同梱しない（リスト自体が機密のため）

終了コード: 0=検出なし / 1=検出あり / 2=実行エラー
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

BUILTIN_PATTERNS = [
    ("slack-id", re.compile(r"\b[CUDG]0[A-Z0-9]{8,10}\b")),
    ("slack-token", re.compile(r"\bxox[abpr]-[A-Za-z0-9-]+")),
    ("slack-webhook", re.compile(r"hooks\.slack\.com/services/\S+")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("api-secret", re.compile(r"(client_secret|access_token|refresh_token)\"?\s*[:=]\s*\"?[A-Za-z0-9_\-]{16,}")),
    ("freee-company-id", re.compile(r"\"company_id\"\s*:\s*\d{7,}")),
    ("user-home-path", re.compile(r"(?i)c:[/\\]+users[/\\]+(?!<)[^/\\\s\"']+[/\\]")),
    ("user-home-path-posix", re.compile(r"/(?:Users|home)/(?!<)[A-Za-z0-9_.-]+/")),
]

# 検査対象外（バイナリ・生成物・このツール自身のテストデータ）
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache"}
SKIP_FILES = {"tools/leak-scan.py"}  # 自身のself-testサンプルを除外
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".pyc", ".ico", ".woff", ".woff2"}
# 例示用に許可する文字列（プレースホルダ）
ALLOW_LINE_MARK = "leak-scan:allow"
ALLOWED_EMAILS = {"noreply@anthropic.com"}


def load_terms(explicit_path):
    """固有語リストを読む。優先順: --terms > 環境変数 > upstream/local.json"""
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    if os.environ.get("LEAK_SCAN_TERMS"):
        candidates.append(os.environ["LEAK_SCAN_TERMS"])
    local_json = os.path.join(REPO_ROOT, "upstream", "local.json")
    if os.path.exists(local_json):
        try:
            with open(local_json, encoding="utf-8") as f:
                cfg = json.load(f)
            root = cfg.get("source_root", "")
            tf = cfg.get("terms_file", "")
            if tf:
                candidates.append(tf if os.path.isabs(tf) else os.path.join(root, tf))
        except Exception as e:  # 設定不備は警告のみ
            print(f"leak-scan: upstream/local.json を読めません: {e}", file=sys.stderr)
    for p in candidates:
        if os.path.exists(p):
            terms = []
            with open(p, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#"):
                        terms.append(s)
            return p, terms
    return None, []


def target_files(staged):
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                             capture_output=True, text=True, cwd=REPO_ROOT, encoding="utf-8").stdout
    else:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, cwd=REPO_ROOT, encoding="utf-8").stdout
    files = []
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        parts = rel.split("/")
        if any(p in SKIP_DIRS for p in parts):
            continue
        if os.path.splitext(rel)[1].lower() in SKIP_EXT or rel in SKIP_FILES:
            continue
        files.append(rel)
    return files


def read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data[:4096]:
        return None  # バイナリ
    return data.decode("utf-8", errors="replace")


def scan_text(text, terms, rel):
    hits = []
    lowered_terms = [(t, t.lower()) for t in terms]
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_LINE_MARK in line:
            continue
        for name, pat in BUILTIN_PATTERNS:
            for m in pat.finditer(line):
                if name == "email" and m.group(0) in ALLOWED_EMAILS:
                    continue
                hits.append((rel, lineno, name, m.group(0)))
        low = line.lower()
        for t, tl in lowered_terms:
            if tl in low:
                hits.append((rel, lineno, "term", t))
    return hits


def run(staged, terms_path, files=None):
    src, terms = load_terms(terms_path)
    if files is None:
        files = target_files(staged)
    hits = []
    for rel in files:
        abspath = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(abspath):
            continue
        if src and os.path.abspath(abspath) == os.path.abspath(src):
            continue
        text = read_text(abspath)
        if text is None:
            continue
        hits.extend(scan_text(text, terms, rel))
    return src, terms, files, hits


def self_test():
    sample = "\n".join([
        "連絡先: someone@example.com",
        "channel C0AB12CD34E",
        "token xoxb-1234-abcd",
        "https://hooks.slack.com/services/T000/B000/xxxx",
        "\"company_id\": 12345678",
        "path C:/Users/someone/x",
        "\"client_secret\": \"abcdefghijklmnopqrstu\"",
        "固有語テスト ほげほげ社",
        "許可行 someone@example.com  # leak-scan:allow",
    ])
    hits = scan_text(sample, ["ほげほげ社"], "<self-test>")
    kinds = sorted({h[2] for h in hits})
    expected = ["api-secret", "email", "freee-company-id", "slack-id", "slack-token", "slack-webhook", "term", "user-home-path", "user-home-path-posix"]
    ok = kinds == expected and not any(h[1] == 9 for h in hits)
    print("self-test:", "OK" if ok else f"NG (got {kinds})")
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--terms")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("paths", nargs="*", help="検査するファイル（省略時はgit管理下すべて）")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    src, terms, files, hits = run(a.staged, a.terms, a.paths or None)
    print(f"leak-scan: {len(files)}ファイル / 固有語{len(terms)}語（{src or '未設定：組み込みパターンのみ'}）")
    if not hits:
        print("leak-scan: 検出なし")
        return 0
    for rel, lineno, name, frag in hits:
        print(f"  {rel}:{lineno}: [{name}] {frag}")
    print(f"leak-scan: {len(hits)}件検出。固有情報を除去するか、例示なら行末に `{ALLOW_LINE_MARK}` を付けてください。")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"leak-scan: 実行エラー {e}", file=sys.stderr)
        sys.exit(2)
