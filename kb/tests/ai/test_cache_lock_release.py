from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from kb.cache_locks import acquire_distributed_lock, release_distributed_lock


class DistributedCacheLockTests(SimpleTestCase):
    @patch("kb.cache_locks._redis_client")
    def test_redis_acquire_uses_nx_and_expiry(self, client_factory):
        client = Mock()
        client.set.return_value = True
        client_factory.return_value = client

        self.assertTrue(acquire_distributed_lock("job:1", "owner", 30))
        client.set.assert_called_once_with(
            "djopenkb:distributed-lock:job:1",
            "owner",
            nx=True,
            ex=30,
        )

    @patch("kb.cache_locks._redis_client")
    def test_release_is_one_atomic_compare_and_delete_script(self, client_factory):
        client = Mock()
        client.eval.return_value = 1
        client_factory.return_value = client

        self.assertTrue(release_distributed_lock("job:1", "owner"))
        args = client.eval.call_args.args
        self.assertIn("redis.call('get'", args[0])
        self.assertIn("redis.call('del'", args[0])
        self.assertEqual(args[1:], (1, "djopenkb:distributed-lock:job:1", "owner"))

    @override_settings(REDIS_URL="redis://redis:6379/0")
    @patch("kb.cache_locks.cache.add")
    @patch("kb.cache_locks._redis_client")
    def test_configured_redis_failure_does_not_create_split_brain_fallback(
        self,
        client_factory,
        cache_add,
    ):
        client = Mock()
        client.set.side_effect = OSError("redis unavailable")
        client_factory.return_value = client

        self.assertFalse(acquire_distributed_lock("job:1", "owner", 30))
        cache_add.assert_not_called()
