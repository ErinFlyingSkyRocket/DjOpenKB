from django.test import SimpleTestCase

from kb.views.services_ai import clean_openkb_ai_error_message


class OpenKBAIUserMessageTests(SimpleTestCase):
    def test_no_matching_information_uses_no_result_message(self):
        message = clean_openkb_ai_error_message(
            "No matching information was found in the wiki for this topic."
        )

        self.assertEqual(
            str(message),
            "The knowledge base does not contain matching information about that topic.",
        )
        self.assertNotIn("could not complete the request", str(message).lower())

    def test_unknown_runtime_failure_uses_temporary_unavailable_message(self):
        message = clean_openkb_ai_error_message("unexpected provider runtime failure")

        self.assertIn("temporarily unavailable", str(message).lower())
        self.assertNotIn("could not complete the request", str(message).lower())


from unittest.mock import Mock, patch

from kb.views.ai_jobs import _complete_with_article_fallback


class OpenKBAINoMatchPriorityTests(SimpleTestCase):
    @patch("kb.views.ai_jobs._set_terminal_result")
    @patch("kb.views.ai_jobs.find_related_openkb_articles", return_value=[])
    def test_provider_fallback_without_matching_article_returns_normal_no_match(
        self,
        _find_related,
        set_terminal_result,
    ):
        _complete_with_article_fallback(
            "00000000-0000-0000-0000-000000000001",
            "What is the time now?",
            Mock(),
        )

        job_id, status, payload = set_terminal_result.call_args.args
        self.assertEqual(job_id, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(status, "completed")
        self.assertEqual(
            str(payload["answer"]),
            "The knowledge base does not contain matching information about that topic.",
        )
        self.assertNotIn("error", payload)
        self.assertFalse(payload["show_related_articles"])

    @patch("kb.views.ai_jobs._set_terminal_result")
    @patch("kb.views.ai_jobs.find_related_openkb_articles")
    @patch(
        "kb.views.ai_jobs.build_openkb_article_recommendation_answer",
        return_value="Related article fallback",
    )
    def test_provider_fallback_keeps_matching_article_recommendations(
        self,
        _build_answer,
        find_related,
        set_terminal_result,
    ):
        articles = [{"title": "Relevant article", "url": "/article/1/"}]
        find_related.return_value = articles

        _complete_with_article_fallback(
            "00000000-0000-0000-0000-000000000002",
            "How do I use the relevant feature?",
            Mock(),
        )

        _job_id, status, payload = set_terminal_result.call_args.args
        self.assertEqual(status, "completed")
        self.assertEqual(payload["answer"], "Related article fallback")
        self.assertEqual(payload["related_articles"], articles)
        self.assertTrue(payload["show_related_articles"])
