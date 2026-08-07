from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from kb.models import ArticleVote, SuggestedArticle
from kb.views.services import (
    get_contextual_related_articles,
    get_home_article_card_queryset,
    paginate_home_article_cards,
)


class HomepageArticleCardQueryTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="homepage-query-owner",
            email="homepage-query-owner@example.invalid",
            password="safe-test-password",
        )
        self.viewer = user_model.objects.create_user(
            username="homepage-query-viewer",
            email="homepage-query-viewer@example.invalid",
            password="safe-test-password",
        )
        self.popular = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Popular lightweight article",
            body="This deliberately large body must not be selected for a homepage card.",
            keywords="homepage, popular",
            filename="popular-lightweight-article.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
            view_count=25,
        )
        self.liked = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Liked lightweight article",
            body="Another body that is unnecessary on the homepage.",
            keywords="homepage, liked",
            filename="liked-lightweight-article.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
            view_count=3,
        )
        SuggestedArticle.objects.create(
            owner=self.owner,
            title="Hidden draft article",
            body="Draft content.",
            filename="hidden-draft-article.md",
            status=SuggestedArticle.Status.DRAFT,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        ArticleVote.objects.create(
            article=self.liked,
            user=self.viewer,
            value=ArticleVote.VoteValue.UP,
        )
        self.factory = RequestFactory()

    def test_home_queryset_selects_only_card_fields(self):
        queryset = get_home_article_card_queryset(
            visibility=SuggestedArticle.Visibility.PUBLIC,
            user=self.viewer,
            sort_mode="trending",
        )

        row = queryset.first()
        self.assertEqual(row["id"], self.popular.pk)
        self.assertEqual(
            set(row),
            {
                "id",
                "title",
                "updated_at",
                "view_count",
                "visibility",
                "db_helpful_vote_count",
            },
        )
        sql = str(queryset.query).lower()
        self.assertNotIn('"body"', sql)
        self.assertNotIn('"pending_update_body"', sql)

    def test_home_pagination_is_database_backed_and_preserves_sort_modes(self):
        request = self.factory.get("/home/", {"page": "1"})
        request.user = self.viewer

        trending = paginate_home_article_cards(
            request,
            visibility=SuggestedArticle.Visibility.PUBLIC,
            user=self.viewer,
            sort_mode="trending",
            per_page=1,
        )
        liked = paginate_home_article_cards(
            request,
            visibility=SuggestedArticle.Visibility.PUBLIC,
            user=self.viewer,
            sort_mode="liked",
            per_page=1,
            known_total_count=trending.paginator.count,
        )

        self.assertEqual(trending.paginator.count, 2)
        self.assertEqual(trending.object_list[0]["suggested_id"], self.popular.pk)
        self.assertEqual(liked.object_list[0]["suggested_id"], self.liked.pk)
        self.assertNotIn("raw_markdown", trending.object_list[0])
        self.assertNotIn("body", trending.object_list[0])


class RelatedArticleQueryTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="related-query-owner",
            email="related-query-owner@example.invalid",
            password="safe-test-password",
        )
        self.current = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Network access guide",
            body="Current article body.",
            keywords="vpn, access",
            filename="network-access-guide.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.keyword_match = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Remote working guide",
            body="Body text is not used for related scoring.",
            keywords="vpn, remote",
            filename="remote-working-guide.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.title_match = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Network troubleshooting",
            body="Body text is not used for related scoring.",
            keywords="diagnostics",
            filename="network-troubleshooting.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )
        self.body_only_match = SuggestedArticle.objects.create(
            owner=self.owner,
            title="Printer maintenance",
            body="This body mentions network access and vpn repeatedly.",
            keywords="printer, toner",
            filename="printer-maintenance.md",
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        )

    def test_related_articles_use_only_title_and_keywords(self):
        results = get_contextual_related_articles(
            {
                "title": self.current.title,
                "keywords": self.current.keyword_list,
                "suggested_id": self.current.pk,
            },
            limit=5,
            user=self.owner,
        )
        result_ids = {item["suggested_id"] for item in results}

        self.assertIn(self.keyword_match.pk, result_ids)
        self.assertIn(self.title_match.pk, result_ids)
        self.assertNotIn(self.current.pk, result_ids)
        self.assertNotIn(self.body_only_match.pk, result_ids)
        self.assertTrue(all("raw_markdown" not in item for item in results))
