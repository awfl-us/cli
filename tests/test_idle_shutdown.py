import unittest

from awfl.consumer.idle_shutdown import IdleShutdown


class IdleShutdownTests(unittest.TestCase):
    def test_one_hour_and_activity(self):
        now = [0.0]
        timeout = IdleShutdown(3600, clock=lambda: now[0], is_busy=lambda: False)
        now[0] = 3599
        self.assertFalse(timeout.is_due())
        timeout.touch()
        now[0] = 3600
        self.assertFalse(timeout.is_due())
        now[0] = 7199
        self.assertTrue(timeout.is_due())

    def test_active_tasks_and_subprocesses_wait_until_completion(self):
        now = [0.0]
        busy = [False]
        timeout = IdleShutdown(3600, clock=lambda: now[0], is_busy=lambda: busy[0])
        now[0] = 4000
        busy[0] = True
        self.assertFalse(timeout.is_due())
        now[0] = 8000
        self.assertFalse(timeout.is_due())
        busy[0] = False
        self.assertFalse(timeout.is_due())
        now[0] = 11599
        self.assertFalse(timeout.is_due())
        now[0] = 11600
        self.assertTrue(timeout.is_due())

    def test_disabled_for_local_cli(self):
        timeout = IdleShutdown(0, clock=lambda: 10**9, is_busy=lambda: False)
        self.assertFalse(timeout.is_due())
