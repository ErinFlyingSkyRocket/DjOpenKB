from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from kb.auth_monitoring import _record_lockout_failure


class AtomicLockoutCounterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @staticmethod
    def _stage(*, failure_limit):
        return {
            "failure_limit": failure_limit,
            "block_seconds": 300,
            "repeat_count": 0,
            "stage_number": 1,
            "source": "test",
        }

    def test_concurrent_failures_are_not_lost_below_threshold(self):
        stage = self._stage(failure_limit=100)

        with patch("kb.auth_monitoring.get_auth_lockout_policy_stages", return_value=[stage]), patch(
            "kb.auth_monitoring._get_strike_ttl_seconds", return_value=3600
        ):
            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(
                    pool.map(
                        lambda _index: _record_lockout_failure("password:test:atomic-count"),
                        range(40),
                    )
                )

        self.assertFalse(any(row["locked"] for row in results))
        self.assertEqual(max(row["failure_count"] for row in results), 40)
        self.assertEqual(len({row["failure_count"] for row in results}), 40)

    def test_concurrent_threshold_creates_only_one_lockout_stage(self):
        stage = self._stage(failure_limit=10)

        with patch("kb.auth_monitoring.get_auth_lockout_policy_stages", return_value=[stage]), patch(
            "kb.auth_monitoring._get_strike_ttl_seconds", return_value=3600
        ):
            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(
                    pool.map(
                        lambda _index: _record_lockout_failure("password:test:atomic-threshold"),
                        range(20),
                    )
                )

        self.assertEqual(sum(bool(row["lockout_created"]) for row in results), 1)
        self.assertTrue(any(row["locked"] for row in results))
