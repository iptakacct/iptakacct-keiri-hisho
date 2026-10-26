#!/usr/bin/env bash
# freee 未処理明細・未添付証憑の無人監視（フェーズ3）。登録は一切しない（読み取り専用）。
# companies.json の全社について、未処理明細の滞留・初めて見る摘要・ファイルボックスの未添付証憑
# （添付候補付き）・口座連携の異常を検知し、報告すべきものがある会社だけ Slack に投稿する。
#
# 投稿先：会社エントリの notify_webhook_env（.env のキー名）があればそのWebhook、無ければ
#   GENERAL_SLACK_WEBHOOK_URL（全体通知チャンネル）。メンションは SLACK_MENTION_USER_ID（無ければなし）。
# 失敗時：全体通知チャンネルにメンション付きで通知（automation.md の型）。
# 使い方：freee-monitor.sh [--dry-run]（dry-run は投稿・状態更新なし、本文を標準出力へ）
set -uo pipefail
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
ENV_FILE="$REPO_ROOT/.claude/scripts/.env"
LOG_DIR="$SKILL_DIR/work"
LOG_FILE="$LOG_DIR/monitor-wrapper.log"
SLACK_USER_ID="${SLACK_MENTION_USER_ID:-}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

mkdir -p "$LOG_DIR"
timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(timestamp)] $1" >> "$LOG_FILE"; }

# 二重起動防止（スリープ復帰時の同時起動などで同じ処理が並走すると二重起票になりうる）。
# 30分を過ぎたロックは異常終了の残骸とみなして引き継ぐ
LOCK_DIR="$LOG_DIR/monitor.lock"
if [ -d "$LOCK_DIR" ] && [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
  rmdir "$LOCK_DIR" 2>/dev/null || true
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "先行の実行が続いているため終了"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

# メンション文字列（SLACK_MENTION_USER_ID が無ければ空）
mention() { [ -n "$SLACK_USER_ID" ] && printf '<@%s> ' "$SLACK_USER_ID" || true; }

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\r'/}"; s="${s//$'\t'/ }"; s="${s//$'\n'/\\n}"
  printf '%s' "$s"
}

# 引数: Webhook URL, 本文。成功なら0
# 日本語を含むJSONはコマンドライン引数で渡すと文字化けしうるため、一時ファイル経由で渡す
post_webhook() {
  local url="$1" message="$2" payload_file http_status
  payload_file="$(mktemp)"
  printf '{"text":"%s"}' "$(json_escape "$message")" > "$payload_file"
  http_status=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
    -H 'Content-type: application/json; charset=utf-8' --data-binary "@${payload_file}" \
    "$url" || echo "curl-failed")
  rm -f "$payload_file"
  [ "$http_status" = "200" ] && return 0
  log "ERROR: Webhook投稿失敗（$http_status）"
  return 1
}

if [ ! -f "$ENV_FILE" ]; then
  log "ERROR: .envが無いため投稿できません: $ENV_FILE"
  [ "$DRY_RUN" = 1 ] || exit 1
else
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  SLACK_USER_ID="${SLACK_MENTION_USER_ID:-$SLACK_USER_ID}"
fi

# 失敗通知など、全体通知チャンネルへの投稿
notify_general() {
  if [ -z "${GENERAL_SLACK_WEBHOOK_URL:-}" ]; then
    log "ERROR: GENERAL_SLACK_WEBHOOK_URLが.envに設定されていません"
    return 1
  fi
  post_webhook "$GENERAL_SLACK_WEBHOOK_URL" "$1"
}

log "開始：freee監視（dry_run=$DRY_RUN）"

# shellcheck disable=SC1091
source "$SKILL_DIR/lib/find_python.sh"
if ! PYTHON_BIN="$(find_python_bin)"; then
  log "ERROR: pythonが見つかりません"
  [ "$DRY_RUN" = 1 ] || notify_general "$(mention)freee監視が失敗しました（pythonが見つかりません）。" || true
  exit 1
fi

OUT_FILE="$(mktemp)"; ERR_FILE="$(mktemp)"
EXTRA=(); [ "$DRY_RUN" = 1 ] && EXTRA=(--dry-run)
"$PYTHON_BIN" "$SKILL_DIR/lib/freee_cli.py" \
  --companies-json "$SKILL_DIR/companies.json" --repo-root "$REPO_ROOT" \
  monitor ${EXTRA[@]+"${EXTRA[@]}"} > "$OUT_FILE" 2> "$ERR_FILE"
RC=$?
cat "$ERR_FILE" >> "$LOG_FILE"

if [ "$DRY_RUN" = 1 ]; then
  echo "--- stderr"; cat "$ERR_FILE"; echo "--- reports (JSON lines)"; cat "$OUT_FILE"
  rm -f "$OUT_FILE" "$ERR_FILE"
  exit $RC
fi

POST_FAIL=0
# 1行1社のJSON（company / notify_webhook_env / text）を投稿する
while IFS= read -r line; do
  [ -n "$line" ] || continue
  ENV_KEY="$("$PYTHON_BIN" -c 'import json,sys; print(json.loads(sys.argv[1]).get("notify_webhook_env") or "")' "$line")"
  TEXT="$("$PYTHON_BIN" -c 'import json,sys; print(json.loads(sys.argv[1])["text"])' "$line")"
  URL=""
  if [ -n "$ENV_KEY" ]; then URL="${!ENV_KEY:-}"; fi
  if [ -z "$URL" ]; then URL="${GENERAL_SLACK_WEBHOOK_URL:-}"; fi
  if [ -z "$URL" ]; then
    log "ERROR: 投稿先のWebhookが未設定です（notify_webhook_env か GENERAL_SLACK_WEBHOOK_URL）"
    POST_FAIL=1
    continue
  fi
  post_webhook "$URL" "$(mention)【freee監視】要確認があります
${TEXT}" || POST_FAIL=1
done < "$OUT_FILE"
rm -f "$OUT_FILE"

if [ $RC -ne 0 ]; then
  DETAIL="$(grep '^FAILURES:' "$ERR_FILE" | head -1)"
  notify_general "$(mention)【freee監視失敗】${DETAIL:-（詳細は .claude/skills/freee-api-post/work/monitor-wrapper.log）}" || true
fi
rm -f "$ERR_FILE"
if [ $RC -ne 0 ] || [ $POST_FAIL -ne 0 ]; then log "終了：異常（rc=$RC post_fail=$POST_FAIL）"; exit 1; fi
log "終了：正常"
exit 0
