#!/usr/bin/env bash
# invoxのrefresh_token（有効期限の記載なし＝実質無期限）を切らさないための定期ヘルスチェック。
# accounts.json記載の全アカウントについてトークン更新＋軽い読み取りを行うだけで、
# 業務データの承認・書き込みは一切行わない。週1回、Windowsタスクスケジューラーから起動する想定。
#
# 失敗時は全体通知チャンネル（channel_id: CH-GENERAL）にBot（Incoming Webhook）経由で通知する
# （feedback_sync-error-to-general の原則：接続断は該当クライアントのチャンネルではなく全体通知チャンネルへ）。
# claude -p + Slack MCPでの本人アカウント投稿は使わない（自己送信メンション抑制、automation.md既知の落とし穴）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
ENV_FILE="$REPO_ROOT/.claude/scripts/.env"
LOG_DIR="$SKILL_DIR/work"
LOG_FILE="$LOG_DIR/healthcheck-wrapper.log"
SLACK_USER_ID=""  # メンション先は環境設定ファイルの SLACK_MENTION_USER_ID（notify_general内で付与）

mkdir -p "$LOG_DIR"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(timestamp)] $1" >> "$LOG_FILE"; }

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\r'/}"
  s="${s//$'\t'/ }"
  s="${s//$'\n'/\\n}"
  printf '%s' "$s"
}

notify_general() {
  local message="$1"
  local payload_file response_file http_status response_body

  if ! command -v curl >/dev/null 2>&1; then
    log "ERROR: curlが見つからないため全体通知チャンネルへの通知ができません"
    echo "エラー: curlが見つからないため全体通知チャンネルへの通知ができません" >&2
    return 1
  fi

  if [ ! -f "$ENV_FILE" ]; then
    log "ERROR: .envファイルが見つからないため全体通知チャンネルへの通知ができません: $ENV_FILE"
    echo "エラー: .envファイルが見つかりません: $ENV_FILE" >&2
    return 1
  fi
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  if [ -n "${SLACK_MENTION_USER_ID:-}" ]; then
    message="<@${SLACK_MENTION_USER_ID}> ${message}"
  fi
  if [ -z "${GENERAL_SLACK_WEBHOOK_URL:-}" ]; then
    log "ERROR: GENERAL_SLACK_WEBHOOK_URLが.envに設定されていません"
    echo "エラー: GENERAL_SLACK_WEBHOOK_URLが.envに設定されていません" >&2
    return 1
  fi

  payload_file="$(mktemp)"
  response_file="$(mktemp)"
  printf '{"text":"%s"}' "$(json_escape "$message")" > "$payload_file"

  if ! http_status=$(curl -sS -o "$response_file" -w "%{http_code}" \
    -X POST -H 'Content-type: application/json; charset=utf-8' \
    --data-binary "@${payload_file}" \
    "$GENERAL_SLACK_WEBHOOK_URL"); then
    log "ERROR: 全体通知チャンネルへのWebhook投稿でcurlの実行自体に失敗した"
    echo "エラー: 全体通知チャンネルへのWebhook投稿でcurlの実行自体に失敗しました" >&2
    rm -f "$payload_file" "$response_file"
    return 1
  fi
  response_body="$(cat "$response_file" 2>/dev/null || echo "(取得失敗)")"
  rm -f "$payload_file" "$response_file"

  if [ "$http_status" = "200" ] && [ "$response_body" = "ok" ]; then
    log "全体通知チャンネルへのWebhook投稿：成功（HTTP $http_status）"
    return 0
  fi
  log "ERROR: 全体通知チャンネルへのWebhook投稿が失敗した可能性（HTTP $http_status, レスポンス: $response_body）"
  echo "エラー: 全体通知チャンネルへのWebhook投稿が失敗した可能性（HTTP $http_status, レスポンス: $response_body）" >&2
  return 1
}

log "開始：invoxヘルスチェック"

# shellcheck disable=SC1091
source "$SKILL_DIR/lib/find_python.sh"
if ! PYTHON_BIN="$(find_python_bin)"; then
  log "ERROR: python3もpythonも実行できないため、ヘルスチェックを開始できませんでした"
  notify_general "invoxヘルスチェックが失敗しました（pythonが見つかりません）。手動での確認が必要です。" || true
  exit 1
fi

OUTPUT_FILE="$(mktemp)"

if "$PYTHON_BIN" "$SKILL_DIR/lib/invox_cli.py" \
  --accounts-json "$SKILL_DIR/accounts.json" \
  --repo-root "$REPO_ROOT" \
  healthcheck > "$OUTPUT_FILE" 2>&1; then
  rm -f "$OUTPUT_FILE"
  log "成功"
  exit 0
fi

FAILURE_DETAIL="$(cat "$OUTPUT_FILE")"
rm -f "$OUTPUT_FILE"

log "ERROR: ヘルスチェック失敗: $FAILURE_DETAIL"

notify_general "【invox APIヘルスチェック失敗】${FAILURE_DETAIL} — トークンが失効している可能性があります。.claude/skills/invox-api/setup-guide.md の手順に沿って invox-authorize-start.sh から再認可してください。" || true

exit 1
