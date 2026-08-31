#!/usr/bin/env bash
# weekly-template-review: 各インスタンスで前週に増えた知見（context/・CLAUDE.md・rules・固有スキル）を
# git logから集め、claude -p に「テンプレート／共通へ還流すべき候補」を判定させて #general に報告する。
# テンプレート・共通ファイルは自動では書き換えない（適用は /template-sync、オーナー承認後）。
#
# 使い方:
#   weekly-template-review.sh            通常実行（Slack投稿あり、状態更新あり）
#   weekly-template-review.sh --dry-run  claude -p まで実行し結果を標準出力へ。Slack投稿・状態更新なし
#   weekly-template-review.sh --diff     対象差分を標準出力に出すだけ（/template-sync が使う）
#
# 自動化の型（automation.md）: エラーを握りつぶさない・ログを残す・失敗は #general へ Webhook で通知。
set -uo pipefail
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
WORK_DIR="$SCRIPT_DIR/work"
LOG_FILE="$WORK_DIR/weekly-template-review.log"
STATE_FILE="$WORK_DIR/template-review-state.json"
# メンション先は .env の SLACK_MENTION_USER_ID（未設定ならメンションなし）
SLACK_USER_ID=""
MODEL="claude-sonnet-5"
MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry" ;;
  --diff) MODE="diff" ;;
esac

if [ -z "${CLAUDE_BIN:-}" ]; then
  if command -v claude >/dev/null 2>&1; then CLAUDE_BIN="claude"
  elif [ -x "$HOME/.local/bin/claude" ]; then CLAUDE_BIN="$HOME/.local/bin/claude"
  else CLAUDE_BIN="claude"; fi
fi

mkdir -p "$WORK_DIR"
timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(timestamp)] $1" >> "$LOG_FILE"; }

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\r'/}"; s="${s//$'\t'/ }"; s="${s//$'\n'/\\n}"
  printf '%s' "$s"
}

notify_general() {
  local message="$1" payload_file http_status
  if [ ! -f "$ENV_FILE" ]; then log "ERROR: .envが無く#general通知不可"; return 1; fi
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  SLACK_USER_ID="${SLACK_MENTION_USER_ID:-}"
  if [ -z "${GENERAL_SLACK_WEBHOOK_URL:-}" ]; then log "ERROR: GENERAL_SLACK_WEBHOOK_URL未設定"; return 1; fi
  payload_file="$(mktemp)"
  printf '{"text":"%s"}' "$(json_escape "$message")" > "$payload_file"
  http_status=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
    -H 'Content-type: application/json; charset=utf-8' --data-binary "@${payload_file}" \
    "$GENERAL_SLACK_WEBHOOK_URL" || echo "curl-failed")
  rm -f "$payload_file"
  if [ "$http_status" = "200" ]; then return 0; fi
  log "ERROR: #general通知失敗（$http_status）"
  return 1
}

cd "$REPO_ROOT" || exit 1

# --- 対象範囲：インスタンス配下の知見ファイル。テンプレート・ルート.claude・docs・work/ は除く
# インスタンス＝ルート直下のフォルダのうち、テンプレート/ 以外で CLAUDE.md を持つもの
INSTANCES=()
for d in */; do
  d="${d%/}"
  [ "$d" = "テンプレート" ] && continue
  [ -f "$d/CLAUDE.md" ] && INSTANCES+=("$d")
done
if [ ${#INSTANCES[@]} -eq 0 ]; then
  log "ERROR: インスタンスが見つかりません"
  [ "$MODE" = "run" ] && notify_general "テンプレ還流レビューが失敗しました（インスタンスが見つかりません）。" || true
  exit 1
fi
PATHSPECS=()
for i in "${INSTANCES[@]}"; do
  PATHSPECS+=("$i/CLAUDE.md" "$i/context" "$i/.claude/rules" "$i/.claude/skills")
done

# --- 対象期間：前回実行コミット以降。無ければ直近7日
LAST_COMMIT=""
if [ -f "$STATE_FILE" ]; then
  LAST_COMMIT="$(grep -o '"last_commit": *"[0-9a-f]*"' "$STATE_FILE" | grep -oE '[0-9a-f]{7,40}' || true)"
fi
if [ -n "$LAST_COMMIT" ] && git cat-file -e "$LAST_COMMIT" 2>/dev/null; then
  RANGE_ARGS=("$LAST_COMMIT..HEAD")
  RANGE_DESC="前回実行（$LAST_COMMIT）以降"
else
  RANGE_ARGS=("--since=7 days ago")
  RANGE_DESC="直近7日"
fi
HEAD_COMMIT="$(git rev-parse --short HEAD)"

DIFF_FILE="$(mktemp)"
{
  echo "== 対象期間: $RANGE_DESC（HEAD $HEAD_COMMIT）"
  echo "== コミット一覧"
  git log "${RANGE_ARGS[@]}" --format='%h %ad %s' --date=short -- "${PATHSPECS[@]}"
  echo
  echo "== 差分"
  git log "${RANGE_ARGS[@]}" -p --format='--- commit %h %s' -- "${PATHSPECS[@]}"
} > "$DIFF_FILE" 2>>"$LOG_FILE"

COMMIT_COUNT="$(git log "${RANGE_ARGS[@]}" --format='%h' -- "${PATHSPECS[@]}" | wc -l | tr -d ' ')"

if [ "$MODE" = "diff" ]; then
  cat "$DIFF_FILE"; rm -f "$DIFF_FILE"; exit 0
fi

log "開始：テンプレ還流レビュー（mode=$MODE、$RANGE_DESC、対象コミット${COMMIT_COUNT}件）"

if [ "$COMMIT_COUNT" = "0" ]; then
  log "対象の変更なし。Slack投稿なし"
  rm -f "$DIFF_FILE"
  if [ "$MODE" = "run" ]; then
    printf '{"last_commit": "%s", "last_run": "%s"}\n' "$HEAD_COMMIT" "$(timestamp)" > "$STATE_FILE"
  else
    echo "対象の変更なし（$RANGE_DESC）"
  fi
  exit 0
fi

# 差分が巨大な場合の上限（claude -p に読ませる分。超えたら先頭だけ）
MAX_BYTES=400000
DIFF_SIZE="$(wc -c < "$DIFF_FILE" | tr -d ' ')"
if [ "$DIFF_SIZE" -gt "$MAX_BYTES" ]; then
  head -c "$MAX_BYTES" "$DIFF_FILE" > "$DIFF_FILE.tmp" && mv "$DIFF_FILE.tmp" "$DIFF_FILE"
  echo "(差分が大きいため先頭${MAX_BYTES}バイトのみ)" >> "$DIFF_FILE"
fi

DIFF_PATH_FOR_PROMPT="$(cygpath -w "$DIFF_FILE" 2>/dev/null || echo "$DIFF_FILE")"

PROMPT="これはkeiri-hishoの週次「テンプレ還流レビュー」です。Slackへの投稿は行わず、投稿すべきメッセージ本文を作るところまでがこのタスクの役割です。ファイルの書き換えも一切行いません。

背景：複数社の経理BPOを各インスタンス（クライアントごとのフォルダ）で進める中で蓄積した知見を、次のクライアントで最初から使える形で「テンプレート/」（新規インスタンスの骨組み）または共通ルール・スキル「.claude/rules/」「.claude/skills/」（既存インスタンスにも効く振る舞い）へ還流させたい。会計システムに限らず、周辺アプリ・メール・Slack・銀行・給与など全てが対象。判定基準は .claude/skills/template-sync/SKILL.md の「蓄積先」「汎用化の規則」。

やること：
1. ファイル「${DIFF_PATH_FOR_PROMPT}」を読む。前回レビュー以降に各インスタンスの CLAUDE.md・context/・.claude/rules/・.claude/skills/ に入った変更（コミット一覧と差分）が入っている。
2. 変更ごとに「他社でも使えるか」を判定する。使えるものは、(a) 共通rules/skillsへ入れる振る舞い・手順・ツールの癖、(b) テンプレートへ入れる骨組み・項目・型、のどちらかに分類し、候補として挙げる。その会社だけの事実（確認済み仕訳パターン・取引先・金額・ID）は候補にしない。
3. 既に共通・テンプレートに同等の記述があるか、テンプレート/ と .claude/rules/ を確認し、既にあるものは候補から外す。
4. 出力は以下の2行構成にすること（他の文言・前置き・後書きは一切含めない）：
   1行目：MENTION: yes （候補が1件以上ある）または MENTION: no （候補なし）
   2行目以降：Slackに投稿するメッセージ本文（mrkdwn）。先頭に「■ テンプレ還流レビュー（週次）」、対象期間とコミット件数、候補一覧（1件につき1行：元インスタンス／内容の要約／蓄積先(a)or(b)と入れるファイル案）、末尾に「反映するときは /template-sync を実行してください」。候補なしなら「今週は還流候補なし」の一言と対象コミット件数だけ。全体で20行以内。"

OUTPUT_FILE="$(mktemp)"
if ! "$CLAUDE_BIN" -p "$PROMPT" --model "$MODEL" --permission-mode bypassPermissions \
     --output-format text --no-session-persistence > "$OUTPUT_FILE" 2>>"$LOG_FILE"; then
  log "ERROR: claude -p が失敗しました"
  rm -f "$DIFF_FILE" "$OUTPUT_FILE"
  [ "$MODE" = "run" ] && notify_general "テンプレ還流レビューが失敗しました（claude -p エラー）。.claude/scripts/work/weekly-template-review.log を確認してください。" || true
  exit 1
fi
rm -f "$DIFF_FILE"

# claude -pが前置き（「わかりました、確認します」等）を付けてMENTION行が1行目に来ないことがある
# （2026-08-31判明：実運用で1回発生し、その前置きが誤ってSlack本文に混入した）。
# 1行目決め打ちではなく、"MENTION: yes"/"MENTION: no"の行を全体から探し、その行より前は
# 前置きとして捨て、その行より後をBODYとする。
CLEAN_OUTPUT="$(tr -d '\r' < "$OUTPUT_FILE")"
rm -f "$OUTPUT_FILE"
MENTION_LINENO="$(printf '%s\n' "$CLEAN_OUTPUT" | grep -n -m1 -E '^MENTION: (yes|no)$' | cut -d: -f1)"
if [ -z "$MENTION_LINENO" ]; then
  log "ERROR: claude -p の出力にMENTION行が見つかりません（前置き混入の疑い）: $(printf '%s' "$CLEAN_OUTPUT" | head -c 200)"
  [ "$MODE" = "run" ] && notify_general "<@${SLACK_USER_ID}> テンプレ還流レビューが失敗しました（出力形式が不正）。.claude/scripts/work/weekly-template-review.log を確認してください。" || true
  exit 1
fi
MENTION_LINE="$(printf '%s\n' "$CLEAN_OUTPUT" | sed -n "${MENTION_LINENO}p")"
BODY="$(printf '%s\n' "$CLEAN_OUTPUT" | tail -n "+$((MENTION_LINENO + 1))")"

if [ -z "$BODY" ]; then
  log "ERROR: claude -p の出力が空です（1行目: $MENTION_LINE）"
  [ "$MODE" = "run" ] && notify_general "テンプレ還流レビューが失敗しました（出力が空）。" || true
  exit 1
fi

if [ "$MENTION_LINE" = "MENTION: yes" ]; then
  if [ -n "$SLACK_USER_ID" ]; then TEXT="<@${SLACK_USER_ID}> ${BODY}"; else TEXT="$BODY"; fi
else
  TEXT="$BODY"
fi

if [ "$MODE" = "dry" ]; then
  echo "$MENTION_LINE"; echo "$BODY"
  log "dry-run 完了（$MENTION_LINE）"
  exit 0
fi

if notify_general "$TEXT"; then
  printf '{"last_commit": "%s", "last_run": "%s"}\n' "$HEAD_COMMIT" "$(timestamp)" > "$STATE_FILE"
  log "完了：#general投稿成功（$MENTION_LINE、状態更新 last_commit=$HEAD_COMMIT）"
  exit 0
fi
log "ERROR: #general投稿に失敗。状態は更新しない（次回に持ち越し）"
exit 1
