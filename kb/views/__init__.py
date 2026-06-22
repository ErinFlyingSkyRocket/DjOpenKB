"""View package for the kb app.

This replaces the old single kb/views.py monster file. Public names are
re-exported here so existing imports such as `from . import views` and
`from kb.views import OpenKBLoginView` continue to work.
"""

from .services import (
    delete_article_files,
    find_stray_uploaded_files,
    get_openkb_uploads_dir,
    log_activity,
    mark_article_image_deleted,
    slugify_title,
    sync_openkb_ai_index,
    write_article_files,
)
from .auth import OpenKBLoginView, OpenKBLogoutView, root_entry, set_site_language, profile, update_profile, change_password
from .main import home, internal_articles, article_detail, wiki_detail, vote_article, search_articles, internal_search_articles, search_article_suggestions, internal_search_article_suggestions
from .suggestions import (
    suggest,
    suggest_internal,
    edit_my_suggestions,
    edit_my_internal_suggestions,
    edit_suggestion,
    delete_suggestion,
    upload_article_image,
    delete_article_image,
    serve_article_image,
)
from .admin import (
    clean_stray_upload_files,
    admin_bulk_articles,
    export_articles_zip,
    import_articles_zip,
    manage_pending_articles,
    manage_internal_pending_articles,
    manage_orphan_articles,
    manage_article_deletion_queue,
)
from .ai import ask_openkb_ai, openkb_ai_job_status, cancel_openkb_ai_job
from .mfa import mfa_setup, mfa_verify, reset_mfa, cancel_mfa_login

__all__ = [
    "OpenKBLoginView",
    "OpenKBLogoutView",
    "root_entry",
    "set_site_language",
    "profile",
    "update_profile",
    "change_password",
    "home",
    "internal_articles",
    "article_detail",
    "wiki_detail",
    "vote_article",
    "search_articles",
    "internal_search_articles",
    "search_article_suggestions",
    "internal_search_article_suggestions",
    "suggest",
    "suggest_internal",
    "edit_my_suggestions",
    "edit_my_internal_suggestions",
    "edit_suggestion",
    "delete_suggestion",
    "upload_article_image",
    "delete_article_image",
    "serve_article_image",
    "clean_stray_upload_files",
    "admin_bulk_articles",
    "export_articles_zip",
    "import_articles_zip",
    "manage_pending_articles",
    "manage_internal_pending_articles",
    "manage_orphan_articles",
    "manage_article_deletion_queue",
    "ask_openkb_ai",
    "openkb_ai_job_status",
    "cancel_openkb_ai_job",
    "mfa_setup",
    "mfa_verify",
    "reset_mfa",
    "cancel_mfa_login",
    "delete_article_files",
    "find_stray_uploaded_files",
    "get_openkb_uploads_dir",
    "log_activity",
    "mark_article_image_deleted",
    "slugify_title",
    "sync_openkb_ai_index",
    "write_article_files",
]
