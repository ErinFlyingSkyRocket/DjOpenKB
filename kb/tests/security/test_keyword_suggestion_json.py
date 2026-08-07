from django.template.loader import render_to_string
from django.test import SimpleTestCase


class KeywordSuggestionJsonSafetyTests(SimpleTestCase):
    def test_keyword_catalog_is_embedded_with_django_json_script_escaping(self):
        html = render_to_string(
            "_keyword_suggestions.html",
            {
                "keyword_suggestion_catalog": [
                    {
                        "keyword": "</script><img src=x onerror=alert(1)>",
                        "usage_count": 1,
                    }
                ],
            },
        )

        self.assertIn('id="openkbKeywordSuggestionCatalog"', html)
        self.assertIn('type="application/json"', html)
        self.assertIn(r"\u003C/script\u003E", html)
        self.assertNotIn("</script><img src=x onerror=alert(1)>", html)
