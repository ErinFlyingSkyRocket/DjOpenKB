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
