import os
import tempfile
import time
import unittest

from mf_lock import company_lock, LockTimeout, _reclaim_stale


class TestCompanyLock(unittest.TestCase):
    def test_取れたら中で作業でき抜けたら消える(self):
        with tempfile.TemporaryDirectory() as base:
            with company_lock("mf-token-x", base=base):
                self.assertTrue(os.path.isdir(os.path.join(base, "mf-token-x.lock")))
            self.assertFalse(os.path.exists(os.path.join(base, "mf-token-x.lock")))

    def test_例外で抜けてもロックは消える(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(RuntimeError):
                with company_lock("mf-token-x", base=base):
                    raise RuntimeError("boom")
            self.assertFalse(os.path.exists(os.path.join(base, "mf-token-x.lock")))

    def test_先客がいて待ち時間が尽きたらLockTimeout(self):
        with tempfile.TemporaryDirectory() as base:
            os.mkdir(os.path.join(base, "mf-token-x.lock"))
            with self.assertRaises(LockTimeout):
                with company_lock("mf-token-x", base=base, wait_seconds=0, sleep=lambda s: None):
                    pass

    def test_先客が抜けるまで待ってから取る(self):
        with tempfile.TemporaryDirectory() as base:
            lock_dir = os.path.join(base, "mf-token-x.lock")
            os.mkdir(lock_dir)
            waited = []

            def fake_sleep(s):
                waited.append(s)
                os.rmdir(lock_dir)  # 1回待ったところで先客が抜ける

            with company_lock("mf-token-x", base=base, wait_seconds=30, sleep=fake_sleep):
                self.assertTrue(os.path.isdir(lock_dir))
            self.assertEqual(waited, [1])

    def test_古いロックは引き継ぐ(self):
        with tempfile.TemporaryDirectory() as base:
            lock_dir = os.path.join(base, "mf-token-x.lock")
            os.mkdir(lock_dir)
            old = time.time() - 300
            os.utime(lock_dir, (old, old))
            with company_lock("mf-token-x", base=base, wait_seconds=0, stale_seconds=120,
                              sleep=lambda s: None):
                self.assertTrue(os.path.isdir(lock_dir))

    def test_古いと判断した後に他者が取り直したロックは消さずに戻す(self):
        # 2つのプロセスが同時に「古い」と判断したとき、後発が先発の新しいロックを消さない
        with tempfile.TemporaryDirectory() as base:
            lock_dir = os.path.join(base, "mf-token-x.lock")
            os.mkdir(lock_dir)  # 今取られたばかり（新しい）
            _reclaim_stale(lock_dir, stale_seconds=120)
            self.assertTrue(os.path.isdir(lock_dir))
            self.assertEqual(os.listdir(base), ["mf-token-x.lock"])

    def test_古いロックの退避は1回だけ成功する(self):
        with tempfile.TemporaryDirectory() as base:
            lock_dir = os.path.join(base, "mf-token-x.lock")
            os.mkdir(lock_dir)
            old = time.time() - 300
            os.utime(lock_dir, (old, old))
            _reclaim_stale(lock_dir, stale_seconds=120)
            _reclaim_stale(lock_dir, stale_seconds=120)  # 2人目は何もしない（例外も出さない）
            self.assertEqual(os.listdir(base), [])

    def test_名前の記号は置き換える(self):
        with tempfile.TemporaryDirectory() as base:
            with company_lock("a/b c", base=base):
                self.assertEqual(os.listdir(base), ["a_b_c.lock"])


if __name__ == "__main__":
    unittest.main()
