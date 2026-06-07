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
    slugify_title,
    sync_openkb_ai_index,
    write_article_files,
)
from .auth import OpenKBLoginView, OpenKBLogoutView, set_site_language, profile, update_profile, change_password
from .main import home, article_detail, wiki_detail, vote_article, search_articles
from .suggestions import (
    suggest,
    edit_my_suggestions,
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
    manage_orphan_articles,
)
from .ai import ask_openkb_ai
from .mfa import mfa_setup, mfa_verify, reset_mfa

__all__ = [
    "OpenKBLoginView",
    "OpenKBLogoutView",
    "set_site_language",
    "profile",
    "update_profile",
    "change_password",
    "home",
    "article_detail",
    "wiki_detail",
    "vote_article",
    "search_articles",
    "suggest",
    "edit_my_suggestions",
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
    "manage_orphan_articles",
    "ask_openkb_ai",
    "mfa_setup",
    "mfa_verify",
    "reset_mfa",
    "delete_article_files",
    "find_stray_uploaded_files",
    "get_openkb_uploads_dir",
    "log_activity",
    "slugify_title",
    "sync_openkb_ai_index",
    "write_article_files",
]
