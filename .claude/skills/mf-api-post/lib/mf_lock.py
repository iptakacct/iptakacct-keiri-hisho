"""会社ごとのロック。マネーフォワードの使い捨てリフレッシュトークンを、
同時に動いた別の処理と取り合わないためのもの。

置き場は OS の一時ディレクトリ配下（環境変数 RUN_LOCK_DIR_BASE で変更可）。方式は
「ディレクトリを mkdir できた者が勝ち」で、定期実行と、対話中に Claude が叩く CLI との間でも排他される。

先客がいても終わらずに待つ（トークンが要るので諦めて終われないため）。
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager


class LockTimeout(Exception):
    """待ち時間内にロックが空かなかった。"""


def default_lock_base() -> str:
    env = os.environ.get("RUN_LOCK_DIR_BASE")
    if env:
        return env
    return os.path.join(tempfile.gettempdir(), "keiri-hisho-locks")


def _reclaim_stale(lock_dir: str, stale_seconds: float) -> None:
    """古いロックを片づける。

    消してから作り直す方式だと、2つのプロセスが同時に「古い」と判断したとき、
    後から消した側が先に取り直した側の新しいロックまで消し、両方が実行に進む
    名前を変えて退避するのは1プロセスしか成功しないので、
    退避に成功した側だけが中身を消す。退避したものが実は新しかった（判断の後に
    他のプロセスが取り直していた）場合は元に戻す。
    """
    aside = f"{lock_dir}.stale.{os.getpid()}.{time.time_ns()}"
    try:
        os.rename(lock_dir, aside)
    except OSError:
        return  # 他のプロセスが先に片づけた
    try:
        fresh = time.time() - os.path.getmtime(aside) < stale_seconds
    except OSError:
        fresh = False
    if fresh:
        try:
            os.rename(aside, lock_dir)
            return
        except OSError:
            pass
    shutil.rmtree(aside, ignore_errors=True)


@contextmanager
def company_lock(name: str, base: str | None = None, wait_seconds: float = 30,
                 stale_seconds: float = 120, sleep=time.sleep):
    base = base or default_lock_base()
    os.makedirs(base, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    lock_dir = os.path.join(base, safe + ".lock")
    deadline = time.time() + wait_seconds
    while True:
        try:
            os.mkdir(lock_dir)
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_dir)
            except OSError:
                continue  # 先客がちょうど抜けた。すぐ取り直す
            if age >= stale_seconds:
                # 異常終了で残ったもの。更新は数秒で終わるので、これだけ古ければ誰も使っていない
                _reclaim_stale(lock_dir, stale_seconds)
                continue
            if time.time() >= deadline:
                raise LockTimeout(f"{name} のロックが{wait_seconds:g}秒待っても空きませんでした")
            sleep(1)
    try:
        with open(os.path.join(lock_dir, "owner"), "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} python\n")
        yield
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)
