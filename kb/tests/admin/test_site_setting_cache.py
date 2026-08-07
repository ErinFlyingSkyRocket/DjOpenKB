from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from kb.models import SITE_SETTING_CACHE_KEY, SiteSetting


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "site-setting-cache-tests",
        }
    }
)
class SiteSettingCacheTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        SiteSetting.objects.update_or_create(
            pk=1,
            defaults={"article_keyword_limit": 20},
        )

    def tearDown(self):
        cache.delete(SITE_SETTING_CACHE_KEY)

    def test_second_load_uses_shared_cache(self):
        with CaptureQueriesContext(connection) as first_queries:
            first = SiteSetting.load()
        with CaptureQueriesContext(connection) as second_queries:
            second = SiteSetting.load()

        self.assertEqual(first.pk, 1)
        self.assertEqual(second.pk, 1)
        self.assertGreaterEqual(len(first_queries), 1)
        self.assertEqual(len(second_queries), 0)

    def test_save_invalidates_cached_settings(self):
        cached = SiteSetting.load()
        self.assertEqual(cached.article_keyword_limit, 20)

        current = SiteSetting.objects.get(pk=1)
        current.article_keyword_limit = 25
        current.save()

        with CaptureQueriesContext(connection) as queries:
            refreshed = SiteSetting.load()

        self.assertGreaterEqual(len(queries), 1)
        self.assertEqual(refreshed.article_keyword_limit, 25)
