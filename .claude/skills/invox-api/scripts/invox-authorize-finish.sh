#!/usr/bin/env bash
# invox-authorize-start.shで表示したURLをブラウザで開き、許可後にリダイレクトされる
# https://web.invox.jp/?code=... から手動でコピーした認可コードを渡してトークンを取得・保存する
# （setup-guide.md手順4）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"

# shellcheck disable=SC1091
source "$SKILL_DIR/lib/find_python.sh"

if [ $# -ne 2 ]; then
  echo "使い方: $0 <account_key> <認可コード>" >&2
  exit 1
fi

PYTHON_BIN="$(find_python_bin)" || exit 1

"$PYTHON_BIN" "$SKILL_DIR/lib/invox_cli.py" \
  --accounts-json "$SKILL_DIR/accounts.json" \
  --repo-root "$REPO_ROOT" \
  authorize-finish "$1" "$2"
