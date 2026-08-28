#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse ガード: keiri-hisho のBashチェック（git許可リスト等）。
秘密ファイル読み取り・DLして即実行のチェックはグローバル側
（~/.claude/hooks/guard-generic.py）が担当するためここでは行わない。"""
import json, re, sys, subprocess

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# 信頼する git の場所は .claude/hooks/guard-allowlist.txt（1行1つ、#はコメント）から読む。
# 空＝すべてask運用（pushは通すがcloneは許可制にならない）。導入時に自社のGitHubアカウント等を書く。
import os
_ALLOW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard-allowlist.txt")
try:
    with open(_ALLOW_FILE, encoding="utf-8") as _f:
        ALLOWED = [l.strip() for l in _f if l.strip() and not l.strip().startswith("#")]
except OSError:
    ALLOWED = []

def deny(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)  # exit 2 = ブロック

def url_allowed(url):
    u = url.lower()
    u = re.sub(r'^(https?://|git@|ssh://git@)', '', u).replace(':', '/', 1)
    return any(u.startswith(a.lower().rstrip('/') + '/') or u.startswith(a.lower().rstrip('/'))
               for a in ALLOWED)

data = json.load(sys.stdin)
if data.get("tool_name") != "Bash":
    sys.exit(0)
cmd = (data.get("tool_input") or {}).get("command", "")

# git clone / リポジトリ取得を許可リスト制に
m = re.search(r'\b(?:git\s+clone|gh\s+repo\s+clone)\s+(\S+)', cmd)
if m and not url_allowed(m.group(1)):
    deny("ブロック: 許可リスト外のリポジトリ取得です → " + m.group(1)
         + "\n信頼できる場合は .claude/hooks/guard.py の ALLOWED に追加してください。")

# npm / pip の git 直インストールをブロック
m = re.search(r'\b(?:npm\s+(?:install|i)|pip3?\s+install|uv\s+pip\s+install)\s+.*?'
              r'((?:git\+|github:|https?://github\.com/|git@)\S+)', cmd)
if m and not url_allowed(m.group(1).replace('git+', '')):
    deny("ブロック: 許可リスト外の git 直インストールです → " + m.group(1))

# git push 先を許可リスト制に
m = re.search(r'\bgit\s+push(?:\s+(-\S+\s+)*)?\s*(\S+)?', cmd)
if ALLOWED and re.search(r'\bgit\s+push\b', cmd):
    remote = (m.group(2) if m and m.group(2) and not m.group(2).startswith('-') else 'origin')
    try:
        url = subprocess.run(['git', 'remote', 'get-url', remote],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        url = ''
    if url and not url_allowed(url):
        deny("ブロック: 許可リスト外のリモートへの push です → " + url
             + "\n持ち出し防止のため、社外リモートへの push は禁止しています。")

sys.exit(0)
