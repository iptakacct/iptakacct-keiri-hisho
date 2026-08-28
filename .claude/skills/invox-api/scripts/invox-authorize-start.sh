#!/usr/bin/env bash
# invoxの認可URLを表示するだけのスクリプト（setup-guide.md手順3）。
# 実際のコード交換は invox-authorize-finish.sh で行う。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"

# shellcheck disable=SC1091
source "$SKILL_DIR/lib/find_python.sh"

if [ $# -ne 1 ]; then
  echo "使い方: $0 <account_key>" >&2
  echo "  account_key は accounts.json の \"name\"" >&2
  exit 1
fi

PYTHON_BIN="$(find_python_bin)" || exit 1

"$PYTHON_BIN" "$SKILL_DIR/lib/invox_cli.py" \
  --accounts-json "$SKILL_DIR/accounts.json" \
  --repo-root "$REPO_ROOT" \
  authorize-start "$1"
