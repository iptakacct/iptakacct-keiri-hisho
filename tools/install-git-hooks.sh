#!/usr/bin/env bash
# git hooks（pre-commitのleak-scan）を有効化する。clone直後に1回実行する。
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
git -C "$ROOT" config core.hooksPath .githooks
chmod +x "$ROOT/.githooks/"* 2>/dev/null || true
echo "core.hooksPath=.githooks を設定しました。"
