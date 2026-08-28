#!/usr/bin/env bash
# freeeの認可URLを表示するだけのスクリプト（setup-guide.md手順1、新規会社追加・再認可の両方で使う）。
# 実際のコード交換は freee-authorize-finish.sh で行う。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"

# shellcheck disable=SC1091
source "$SKILL_DIR/lib/find_python.sh"

if [ $# -ne 1 ]; then
  echo "使い方: $0 <company_key>" >&2
  echo "  company_key は companies.json の \"name\" または company_id" >&2
  exit 1
fi

PYTHON_BIN="$(find_python_bin)" || exit 1

"$PYTHON_BIN" "$SKILL_DIR/lib/freee_cli.py" \
  --companies-json "$SKILL_DIR/companies.json" \
  --repo-root "$REPO_ROOT" \
  authorize-start "$1"
