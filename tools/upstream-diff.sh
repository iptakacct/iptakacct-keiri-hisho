#!/usr/bin/env bash
# upstream-diff: 開発元（上流）リポジトリで、manifest.json に載せた対象パスに
# last_synced_commit 以降どんな変更があったかを表示する（読み取り専用）。
#
# 使い方:
#   bash tools/upstream-diff.sh            # コミット一覧＋変更ファイル一覧
#   bash tools/upstream-diff.sh --patch    # 差分本文も表示
#   bash tools/upstream-diff.sh --count    # 未同期コミット数だけを出力（upstream-check用）
#
# 前提: upstream/local.json に {"source_root": "<上流リポジトリのローカルパス>"} があること
set -eu
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY=""; for c in python3 python py; do if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi; done
[ -n "$PY" ] || { echo "python が見つかりません" >&2; exit 1; }
MODE="${1:-}"

SRC_ROOT="$("$PY" "$ROOT/tools/upstream_cfg.py" root)"
LAST="$("$PY" "$ROOT/tools/upstream_cfg.py" last)"
if [ -z "$SRC_ROOT" ] || [ ! -d "$SRC_ROOT/.git" ]; then
  echo "upstream-diff: upstream/local.json の source_root が未設定、またはgitリポジトリではありません" >&2
  exit 2
fi
mapfile -t PATHS < <("$PY" "$ROOT/tools/upstream_cfg.py" paths)

RANGE="${LAST}..HEAD"
if [ "$MODE" = "--count" ]; then
  git -C "$SRC_ROOT" rev-list --count "$RANGE" -- "${PATHS[@]}"
  exit 0
fi

echo "== 上流: $SRC_ROOT"
echo "== 範囲: $RANGE（対象パス ${#PATHS[@]}件）"
echo
echo "== コミット一覧"
git -C "$SRC_ROOT" log --date=short --format="%h %ad %s" "$RANGE" -- "${PATHS[@]}" || true
echo
echo "== 変更ファイル"
git -C "$SRC_ROOT" diff --stat "$RANGE" -- "${PATHS[@]}" || true
if [ "$MODE" = "--patch" ]; then
  echo
  echo "== 差分"
  git -C "$SRC_ROOT" diff "$RANGE" -- "${PATHS[@]}" || true
fi
