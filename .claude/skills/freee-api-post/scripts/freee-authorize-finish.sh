#!/usr/bin/env bash
# freee-authorize-start.shで表示したURLをブラウザで開き、事業所選択後に表示される
# 認可コードをこのスクリプトに渡してトークンを取得・保存する（setup-guide.md手順2）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"

# shellcheck disable=SC1091
source "$SKILL_DIR/lib/find_python.sh"

if [ $# -ne 2 ]; then
  echo "使い方: $0 <company_key> <認可コード>" >&2
  exit 1
fi

PYTHON_BIN="$(find_python_bin)" || exit 1

"$PYTHON_BIN" "$SKILL_DIR/lib/freee_cli.py" \
  --companies-json "$SKILL_DIR/companies.json" \
  --repo-root "$REPO_ROOT" \
  authorize-finish "$1" "$2"
