#!/usr/bin/env bash
# upstream-check: 上流に未同期の変更があるかを週次で確認し、あればSlackに通知する。
# スケジューラーから実行する想定（自動化の型: エラーを握りつぶさない・ログを残す・失敗はSlackへ）。
#
# 通知先: LONG_TASK_SLACK_WEBHOOK_URL → GENERAL_SLACK_WEBHOOK_URL の順（環境設定ファイル .claude/scripts/.env）
set -u
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.claude/scripts/.env"
LOG="$ROOT/upstream/upstream-check.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

notify() {
  local text="$1"
  [ -f "$ENV_FILE" ] || { log "ERROR: 環境設定ファイルが無く通知できません"; return 1; }
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  local url="${LONG_TASK_SLACK_WEBHOOK_URL:-${GENERAL_SLACK_WEBHOOK_URL:-}}"
  [ -n "$url" ] || { log "ERROR: Webhook URLが未設定で通知できません"; return 1; }
  [ -n "${SLACK_MENTION_USER_ID:-}" ] && text="<@${SLACK_MENTION_USER_ID}> ${text}"
  local payload; payload="$(mktemp)"
  "${PY:-python}" -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}, ensure_ascii=False))' "$text" > "$payload"
  curl -sS -o /dev/null -X POST -H 'Content-type: application/json; charset=utf-8' --data-binary "@$payload" "$url"
  local rc=$?; rm -f "$payload"; return $rc
}

PY=""; for c in python3 python py; do if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi; done
[ -n "$PY" ] || { echo "python が見つかりません" >&2; exit 1; }
export PY

COUNT="$(bash "$ROOT/tools/upstream-diff.sh" --count 2>>"$LOG")"
rc=$?
if [ $rc -ne 0 ] || [ -z "$COUNT" ]; then
  log "ERROR: upstream-diff --count が失敗しました（rc=$rc）"
  notify "keiri-hisho upstream-check が失敗しました（upstream-diff エラー、rc=$rc）。upstream/upstream-check.log を確認してください。" || true
  exit 1
fi

if [ "$COUNT" -gt 0 ]; then
  FILES="$(bash "$ROOT/tools/upstream-diff.sh" 2>/dev/null | sed -n '/== 変更ファイル/,$p' | tail -n +2 | head -n 15)"
  log "未同期コミット ${COUNT}件"
  notify "keiri-hisho: 上流（開発元）に未同期の変更が ${COUNT}コミットあります。keiri-hisho で /upstream-sync を実行してください。
${FILES}" || true
else
  log "未同期なし"
fi
exit 0
