"""Central character limits for browser and server-side request validation.

The limits in this module are intentionally fixed security boundaries for the
normal web interface. The article body and OpenKB AI question limits remain
configuration-aware because those features already expose controlled settings.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _


# Common identity and authentication inputs.
USERNAME_MAX_LENGTH = 150
EMAIL_MAX_LENGTH = 254
LOGIN_IDENTIFIER_MAX_LENGTH = EMAIL_MAX_LENGTH
PASSWORD_MAX_LENGTH = 256
MFA_CODE_MAX_LENGTH = 32
CHALLENGE_ID_MAX_LENGTH = 128

# Navigation, filtering, and small workflow values.
SEARCH_QUERY_MAX_LENGTH = 200
URL_MAX_LENGTH = 2048
LANGUAGE_CODE_MAX_LENGTH = 20
ACTION_MAX_LENGTH = 100
SMALL_CONTROL_MAX_LENGTH = 32

# Article/editor and administrative text fields.
ARTICLE_TITLE_MAX_LENGTH = 200
ARTICLE_KEYWORDS_MAX_LENGTH = 500
REVIEW_NOTES_MAX_LENGTH = 4_000
PROFILE_NOTES_MAX_LENGTH = 4_000
ADMIN_ALLOWED_CIDRS_MAX_LENGTH = 4_096
FILENAME_MAX_LENGTH = 255
USER_LOOKUP_MAX_LENGTH = 254
JSON_TEXT_MAX_LENGTH = 150_000

# Any unrecognised non-file form value is still bounded. This protects custom
# Django Admin inputs and future form fields even before a dedicated limit is
# added to the explicit mapping below.
GENERIC_TEXT_INPUT_MAX_LENGTH = 4_096
FIELD_NAME_MAX_LENGTH = 128


STATIC_FIELD_LIMITS = {
    # Django/security fields.
    "csrfmiddlewaretoken": 128,
    "next": URL_MAX_LENGTH,
    "challenge_id": CHALLENGE_ID_MAX_LENGTH,
    "language": LANGUAGE_CODE_MAX_LENGTH,
    "preferred_language": LANGUAGE_CODE_MAX_LENGTH,

    # Sign-in and profile fields.
    "username": LOGIN_IDENTIFIER_MAX_LENGTH,
    "email": EMAIL_MAX_LENGTH,
    "first_name": USERNAME_MAX_LENGTH,
    "last_name": USERNAME_MAX_LENGTH,
    "password": PASSWORD_MAX_LENGTH,
    "password1": PASSWORD_MAX_LENGTH,
    "password2": PASSWORD_MAX_LENGTH,
    "current_password": PASSWORD_MAX_LENGTH,
    "old_password": PASSWORD_MAX_LENGTH,
    "new_password1": PASSWORD_MAX_LENGTH,
    "new_password2": PASSWORD_MAX_LENGTH,
    "mfa_code": MFA_CODE_MAX_LENGTH,
    "code": MFA_CODE_MAX_LENGTH,

    # Search/filter fields.
    "q": SEARCH_QUERY_MAX_LENGTH,
    "target_user_lookup": USER_LOOKUP_MAX_LENGTH,

    # Article fields.
    "frm_kb_title": ARTICLE_TITLE_MAX_LENGTH,
    "title": ARTICLE_TITLE_MAX_LENGTH,
    "frm_kb_keywords": ARTICLE_KEYWORDS_MAX_LENGTH,
    "keywords": ARTICLE_KEYWORDS_MAX_LENGTH,
    "pending_update_title": ARTICLE_TITLE_MAX_LENGTH,
    "pending_update_keywords": ARTICLE_KEYWORDS_MAX_LENGTH,
    "review_notes": REVIEW_NOTES_MAX_LENGTH,
    "deletion_reason": REVIEW_NOTES_MAX_LENGTH,
    "reason": SMALL_CONTROL_MAX_LENGTH,
    "url": URL_MAX_LENGTH,
    "filename": FILENAME_MAX_LENGTH,
    "selected_files": FILENAME_MAX_LENGTH,

    # Django Admin text areas and JSON-backed fields.
    "notes": PROFILE_NOTES_MAX_LENGTH,
    "admin_allowed_cidrs": ADMIN_ALLOWED_CIDRS_MAX_LENGTH,
    "image_assets": JSON_TEXT_MAX_LENGTH,
    "pending_update_image_assets": JSON_TEXT_MAX_LENGTH,
    "review_notes_history": JSON_TEXT_MAX_LENGTH,

    # Small workflow/control values. These are user-controlled even when
    # rendered as select or hidden inputs.
    "login_mode": SMALL_CONTROL_MAX_LENGTH,
    "profile_action": SMALL_CONTROL_MAX_LENGTH,
    "editor_mode": SMALL_CONTROL_MAX_LENGTH,
    "review": SMALL_CONTROL_MAX_LENGTH,
    "submit_action": SMALL_CONTROL_MAX_LENGTH,
    "article_visibility": SMALL_CONTROL_MAX_LENGTH,
    "visibility": SMALL_CONTROL_MAX_LENGTH,
    "status": SMALL_CONTROL_MAX_LENGTH,
    "vote": SMALL_CONTROL_MAX_LENGTH,
    "tab": SMALL_CONTROL_MAX_LENGTH,
    "dialog": SMALL_CONTROL_MAX_LENGTH,
    "confirm": SMALL_CONTROL_MAX_LENGTH,
    "split": SMALL_CONTROL_MAX_LENGTH,
    "frm_kb_id": SMALL_CONTROL_MAX_LENGTH,
    "workspace_id": 36,
    "workspace_leave_action": SMALL_CONTROL_MAX_LENGTH,
    "article_id": SMALL_CONTROL_MAX_LENGTH,
    "target_user": SMALL_CONTROL_MAX_LENGTH,
    "selected_articles": SMALL_CONTROL_MAX_LENGTH,
    "_selected_action": SMALL_CONTROL_MAX_LENGTH,
    "action": ACTION_MAX_LENGTH,
}


FIELD_LABELS = {
    "username": _("username"),
    "email": _("email address"),
    "password": _("password"),
    "password1": _("password"),
    "password2": _("password confirmation"),
    "current_password": _("current password"),
    "old_password": _("current password"),
    "new_password1": _("new password"),
    "new_password2": _("new password confirmation"),
    "mfa_code": _("MFA/OTP code"),
    "code": _("MFA/OTP code"),
    "q": _("search query"),
    "question": _("OpenKB AI question"),
    "frm_kb_title": _("article title"),
    "title": _("title"),
    "frm_kb_body": _("article body"),
    "body": _("article body"),
    "pending_update_body": _("pending article body"),
    "frm_kb_keywords": _("article keywords"),
    "keywords": _("article keywords"),
    "review_notes": _("review comments"),
    "url": _("video link"),
    "target_user_lookup": _("user lookup"),
    "admin_allowed_cidrs": _("Admin allowed IP ranges"),
    "notes": _("notes"),
}


ARTICLE_BODY_FIELD_NAMES = {
    "frm_kb_body",
    "body",
    "pending_update_body",
}


def get_openkb_ai_question_limit() -> int:
    """Return the configured OpenKB AI prompt limit within its safe range."""
    try:
        value = int(getattr(settings, "OPENKB_AI_MAX_PROMPT_CHARS", 1000) or 1000)
    except (TypeError, ValueError):
        value = 1000
    return max(100, min(value, 10_000))


def get_field_character_limit(field_name: str) -> int:
    """Return the authoritative maximum character count for a request field."""
    field_name = str(field_name or "")
    if field_name in ARTICLE_BODY_FIELD_NAMES:
        # Import lazily so models can import constants from this module without
        # creating an import cycle during Django application startup.
        from .models import get_article_body_character_limit

        return get_article_body_character_limit()
    if field_name == "question":
        return get_openkb_ai_question_limit()
    return STATIC_FIELD_LIMITS.get(field_name, GENERIC_TEXT_INPUT_MAX_LENGTH)


def get_field_label(field_name: str) -> str:
    """Return a readable label without exposing unnecessary submitted data."""
    return str(FIELD_LABELS.get(field_name, _("text input")))


def template_input_limits() -> dict[str, int]:
    """Return limits used by custom templates and Django Admin JavaScript."""
    return {
        "username": LOGIN_IDENTIFIER_MAX_LENGTH,
        "email": EMAIL_MAX_LENGTH,
        "password": PASSWORD_MAX_LENGTH,
        "mfa_code": MFA_CODE_MAX_LENGTH,
        "search_query": SEARCH_QUERY_MAX_LENGTH,
        "url": URL_MAX_LENGTH,
        "review_notes": REVIEW_NOTES_MAX_LENGTH,
        "profile_notes": PROFILE_NOTES_MAX_LENGTH,
        "admin_allowed_cidrs": ADMIN_ALLOWED_CIDRS_MAX_LENGTH,
        "user_lookup": USER_LOOKUP_MAX_LENGTH,
        "generic": GENERIC_TEXT_INPUT_MAX_LENGTH,
        "ai_question": get_openkb_ai_question_limit(),
    }
