#!/usr/bin/env python3
"""長時間タスクのSlack通知hook（2026-08-29）

- UserPromptSubmit: セッションごとの開始時刻を記録
- Stop / Notification: 開始からの経過時間がしきい値以上なら Slack へWebhook投稿（メンション付き）

しきい値は環境変数 LONG_TASK_NOTIFY_MINUTES（既定5分。投稿が多すぎるため1分から変更）。
Webhook URLは .claude/scripts/.env の LONG_TASK_SLACK_WEBHOOK_URL（専用チャンネル用、任意）→
GENERAL_SLACK_WEBHOOK_URL（全体通知チャンネル）の順で使う（hookプロセス自身が読むのでセキュリティhookの対象外）。
デスクトップ通知は既定オフ（LONG_TASK_DESKTOP_NOTIFY=1 で有効）。Slackアプリの通知と2重になるため。
失敗しても本体の動作を止めない（常にexit 0）。
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

MENTION = ""  # .env の SLACK_MENTION_USER_ID から設定
STATE_DIR = os.path.join(tempfile.gettempdir(), "claude-long-task")
ENV_FILE = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", "."), ".claude", "scripts", ".env")
THRESHOLD_SEC = float(os.environ.get("LONG_TASK_NOTIFY_MINUTES", "5")) * 60  # .env側の同名キーは main() で上書き
# デスクトップ通知は既定でオフ：Slackデスクトップアプリがメンション通知を出すため、
# トーストと重なってポップアップが2重になる。必要なら LONG_TASK_DESKTOP_NOTIFY=1 で再有効化
DESKTOP_NOTIFY = os.environ.get("LONG_TASK_DESKTOP_NOTIFY", "0") == "1"


def state_path(session_id):
    return os.path.join(STATE_DIR, f"{session_id}.json")


def webhook_url():
    """LONG_TASK_SLACK_WEBHOOK_URL（専用チャンネル）があれば優先、無ければ全体通知チャンネルを使う"""
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None
    for key in ("LONG_TASK_SLACK_WEBHOOK_URL", "GENERAL_SLACK_WEBHOOK_URL"):
        m = re.search(key + r'="?([^"\n]+)"?', content)
        if m:
            return m.group(1).strip()
    return None


def env_value(key):
    """.claude/scripts/.env から値を1つ読む（無ければNone）"""
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None
    m = re.search(key + r'="?([^"\n]+)"?', content)
    return m.group(1).strip() if m else None


def mention():
    uid = env_value("SLACK_MENTION_USER_ID")
    return f"<@{uid}>" if uid else ""


def post(text):
    url = webhook_url()
    if not url:
        return
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    urllib.request.urlopen(req, timeout=10).read()


def desktop_notify(title, body):
    """OSのデスクトップ通知（Windows: トースト＋音 / macOS: 通知センター）。Slackとは独立に出す"""
    import platform
    import subprocess

    try:
        if platform.system() == "Windows":
            ps = r'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast duration="long"><visual><binding template="ToastGeneric"><text>{TITLE}</text><text>{BODY}</text></binding></visual><audio src="ms-winsoundevent:Notification.Default"/></toast>')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
# 未登録のアプリIDだとWindowsが通知を捨てるため、登録済みのPowerShellのAUMIDを使う
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
'''
            esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "''")
            ps = ps.replace("{TITLE}", esc(title)).replace("{BODY}", esc(body))
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                timeout=15, capture_output=True,
            )
        elif platform.system() == "Darwin":
            script = f'display notification "{body}" with title "{title}" sound name "Glass"'
            subprocess.run(["osascript", "-e", script], timeout=15, capture_output=True)
    except Exception as e:
        print(f"long-task-notify(desktop): {e}", file=sys.stderr)


def main():
    try:
        # Windowsではsys.stdinがcp932になりうるため、バイト列をUTF-8で明示デコード（automation.md参照）
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except Exception:
        return
    global THRESHOLD_SEC
    env_min = env_value("LONG_TASK_NOTIFY_MINUTES")
    if env_min:
        try:
            THRESHOLD_SEC = float(env_min) * 60
        except ValueError:
            pass
    event = data.get("hook_event_name", "")
    sid = data.get("session_id", "unknown")
    os.makedirs(STATE_DIR, exist_ok=True)
    project = os.path.basename(data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR", ""))

    if event == "UserPromptSubmit":
        prompt = (data.get("prompt") or "").strip().replace("\n", " ")
        with open(state_path(sid), "w", encoding="utf-8") as f:
            json.dump({"start": time.time(), "prompt": prompt[:60]}, f, ensure_ascii=False)
        # UserPromptSubmitの標準出力はClaudeへの追加コンテキストになる。
        # 毎回、返答の冒頭で所要時間の目安を出すよう機械的にリマインドする（communication.md「長くなりそうな作業の予告」）
        sys.stdout.buffer.write(
            "【所要時間の予告】返答の1行目に必ず「所要目安：約N分」（1分未満なら「所要目安：すぐ」）と書いてから本題に入る。"
            "ツール呼び出しより先に出すこと。5分以上の見込みなら、終わったらSlackで通知される旨も添える。\n".encode("utf-8")
        )
        return

    if event in ("Stop", "Notification"):
        if event == "Stop" and data.get("stop_hook_active"):
            return
        try:
            with open(state_path(sid), encoding="utf-8") as f:
                st = json.load(f)
        except OSError:
            return
        elapsed = time.time() - st.get("start", time.time())
        if elapsed < THRESHOLD_SEC:
            return
        mins = int(elapsed // 60)
        if event == "Stop":
            label = "作業が終わりました"
            # 二重通知防止：完了後は開始時刻を消す
            try:
                os.remove(state_path(sid))
            except OSError:
                pass
        else:
            ntype = data.get("notification_type") or data.get("message") or ""
            label = f"入力待ちで止まっています（{ntype}）"
            # 同じ待ちで連投しないよう、開始時刻を今にリセット
            st["start"] = time.time()
            with open(state_path(sid), "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
        text = f"{mention()} Claude Code [{project}] {label}（{mins}分経過）\n> {st.get('prompt', '')}"
        if DESKTOP_NOTIFY:
            desktop_notify(f"Claude Code [{project}] {label}", f"{mins}分経過 / {st.get('prompt', '')}")
        post(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 本体を止めない
        print(f"long-task-notify: {e}", file=sys.stderr)
    sys.exit(0)
