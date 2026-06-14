import json

from django.test import TestCase
from django.urls import reverse

from kb.models import SiteSetting, SuggestedArticle
from kb.permissions import ROLE_REGULAR_USER
from kb.views.services import get_articles_per_page
from kb.views.suggestions import get_keyword_suggestion_catalog_json
from kb.views.services_search import article_matches_title_or_keywords, search_public_articles_by_title_keywords

from .helpers import DjOpenKBTestMixin


class SearchTitleKeywordOnlyTests(DjOpenKBTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user("search_regular", role=ROLE_REGULAR_USER)
        self.client = self.login_client(self.user)
        self.title_match = self.make_article(
            "SCCM Console Cannot Open",
            status=SuggestedArticle.Status.PUBLISHED,
            body="Body does not contain the special query.",
            keywords="configuration manager, admin role",
        )
        self.keyword_match = self.make_article(
            "Generic Troubleshooting Guide",
            status=SuggestedArticle.Status.PUBLISHED,
            body="Body does not contain the keyword query either.",
            keywords="vpn, certificate",
        )
        self.body_only = self.make_article(
            "Unrelated Title",
            status=SuggestedArticle.Status.PUBLISHED,
            body="This body contains secretbodymatch but the title and keywords do not.",
            keywords="unrelated",
        )
        self.pending = self.make_article(
            "Draft Keyword Hidden",
            status=SuggestedArticle.Status.PENDING,
            body="Pending body",
            keywords="pendingkeyword",
        )

    def test_search_service_matches_title_and_keywords_only(self):
        self.assertTrue(article_matches_title_or_keywords({"title": "Moon Guide", "keywords": []}, "moon"))
        self.assertTrue(article_matches_title_or_keywords({"title": "Other", "keywords": ["moon shine"]}, "moon shine"))
        self.assertFalse(article_matches_title_or_keywords({"title": "Other", "keywords": [], "raw_markdown": "moon"}, "moon"))

    def test_database_search_does_not_match_body_only_text(self):
        results = search_public_articles_by_title_keywords("secretbodymatch")
        self.assertEqual(results, [])

    def test_database_search_matches_title_keyword_and_published_only(self):
        title_results = search_public_articles_by_title_keywords("sccm")
        keyword_results = search_public_articles_by_title_keywords("certificate")
        pending_results = search_public_articles_by_title_keywords("pendingkeyword")
        self.assertEqual([item["title"] for item in title_results], [self.title_match.title])
        self.assertEqual([item["title"] for item in keyword_results], [self.keyword_match.title])
        self.assertEqual(pending_results, [])

    def test_search_page_and_dropdown_use_same_simple_matching(self):
        page = self.client.get(reverse("search") + "?q=certificate")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.keyword_match.title)
        self.assertNotContains(page, self.body_only.title)

        dropdown = self.client.get(reverse("search_article_suggestions") + "?q=certificate")
        self.assertEqual(dropdown.status_code, 200)
        data = dropdown.json()
        self.assertEqual([item["title"] for item in data["results"]], [self.keyword_match.title])

    def test_home_tabs_paginate_using_site_setting(self):
        settings_obj = SiteSetting.load()
        settings_obj.articles_per_page = 5
        settings_obj.save()
        for index in range(10):
            self.make_article(f"Paged Article {index}", status=SuggestedArticle.Status.PUBLISHED, view_count=index)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["trending_articles"]), 5)
        self.assertEqual(response.context["trending_page_obj"].paginator.count, SuggestedArticle.objects.filter(status=SuggestedArticle.Status.PUBLISHED).count())


class KeywordSuggestionCatalogTests(DjOpenKBTestMixin, TestCase):
    def test_catalog_uses_only_existing_published_manual_keywords(self):
        self.make_article("Published Keyword One", status=SuggestedArticle.Status.PUBLISHED, keywords="moon, shine, first article")
        self.make_article("Published Keyword Two", status=SuggestedArticle.Status.PUBLISHED, keywords="moon, vpn")
        self.make_article("Pending Keyword Hidden", status=SuggestedArticle.Status.PENDING, keywords="pending-only")

        catalog = json.loads(get_keyword_suggestion_catalog_json())
        keywords = [item["keyword"] for item in catalog]
        self.assertIn("moon", keywords)
        self.assertIn("shine", keywords)
        self.assertIn("first article", keywords)
        self.assertIn("vpn", keywords)
        self.assertNotIn("pending-only", keywords)
        self.assertEqual(next(item for item in catalog if item["keyword"] == "moon")["usage_count"], 2)

    def test_catalog_keeps_manual_keywords_without_stopword_filter(self):
        self.make_article("Published Stopword Keyword", status=SuggestedArticle.Status.PUBLISHED, keywords="is, this, that")
        catalog = json.loads(get_keyword_suggestion_catalog_json())
        keywords = [item["keyword"] for item in catalog]
        self.assertIn("is", keywords)
        self.assertIn("this", keywords)
        self.assertIn("that", keywords)

    def test_articles_per_page_is_clamped_at_runtime(self):
        setting = SiteSetting.load()
        setting.articles_per_page = 1
        setting.save()
        self.assertEqual(get_articles_per_page(), 5)
        setting.articles_per_page = 150
        setting.save()
        self.assertEqual(get_articles_per_page(), 100)
        setting.articles_per_page = 25
        setting.save()
        self.assertEqual(get_articles_per_page(), 25)
