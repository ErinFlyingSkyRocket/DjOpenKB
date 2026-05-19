import os
import io
import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import F, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import ArticleVote, SuggestedArticle, UserProfile, SiteSetting


IGNORED_WIKI_NAMES = {"AGENTS.md", "log.md", "index.md", "README.md"}


def slugify_title(title):
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


def init_openkb_storage():
    settings.OPENKB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.OPENKB_RAW_DIR.mkdir(parents=True, exist_ok=True)
    settings.OPENKB_WIKI_DIR.mkdir(parents=True, exist_ok=True)
    (settings.OPENKB_WIKI_DIR / "sources").mkdir(parents=True, exist_ok=True)


def get_openkb_uploads_dir():
    """Folder used for small images pasted into suggested Markdown articles."""
    upload_dir = settings.OPENKB_WIKI_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def is_allowed_article_image(uploaded_file):
    allowed_types = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    return allowed_types.get(uploaded_file.content_type)


ARTICLE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(/wiki/uploads/([A-Za-z0-9._-]+)\)")


def extract_article_image_filenames(markdown_text):
    """Return unique uploaded image filenames referenced in Markdown body."""
    seen = []
    for filename in ARTICLE_IMAGE_RE.findall(markdown_text or ""):
        if filename not in seen and "/" not in filename and "\\" not in filename:
            seen.append(filename)
    return seen


def article_image_markdown(filename):
    return f"![image](/wiki/uploads/{filename})"


def article_image_url(filename):
    return f"/wiki/uploads/{filename}"


def delete_uploaded_image_file(filename):
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        return

    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / filename).resolve()

    if str(file_path).startswith(str(upload_dir)) and file_path.exists() and file_path.is_file():
        file_path.unlink()


def image_is_used_by_other_article(filename, current_article=None):
    queryset = SuggestedArticle.objects.all()
    if current_article and current_article.pk:
        queryset = queryset.exclude(pk=current_article.pk)

    for article in queryset.only("body", "image_assets"):
        if filename in (article.image_assets or []):
            return True
        if filename in extract_article_image_filenames(article.body):
            return True
    return False


def sync_article_image_assets(article, old_assets=None):
    """Tie currently referenced uploaded images to this article and clean stale ones."""
    old_assets = set(old_assets if old_assets is not None else (article.image_assets or []))
    new_assets = set(extract_article_image_filenames(article.body))

    stale_assets = old_assets - new_assets
    for filename in stale_assets:
        if not image_is_used_by_other_article(filename, current_article=article):
            delete_uploaded_image_file(filename)

    article.image_assets = sorted(new_assets)
    SuggestedArticle.objects.filter(pk=article.pk).update(image_assets=article.image_assets)
    return article.image_assets


def clear_committed_pending_uploads(request, image_assets):
    pending_uploads = request.session.get("pending_article_uploads", [])
    if not pending_uploads:
        return

    image_assets = set(image_assets or [])
    request.session["pending_article_uploads"] = [
        item for item in pending_uploads if item not in image_assets
    ]
    request.session.modified = True


def user_can_use_admin_tools(user):
    """Return True for users allowed to use profile-level admin maintenance tools."""
    return bool(user.is_authenticated and user.is_active and user.is_staff)


def admin_tools_required(view_func):
    """Require normal site access plus staff/admin permission."""
    @wraps(view_func)
    @main_site_login_required
    def wrapper(request, *args, **kwargs):
        if not user_can_use_admin_tools(request.user):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("You do not have permission to use admin maintenance tools.")
        return view_func(request, *args, **kwargs)

    return wrapper


def extract_uploaded_file_filenames_from_text(text):
    """Find /wiki/uploads/<filename> references from Markdown or rendered HTML text."""
    if not text:
        return set()

    filenames = set(extract_article_image_filenames(text))

    # Also catch HTML img src or plain pasted URLs such as:
    # /wiki/uploads/example.png, http://host/wiki/uploads/example.png
    for filename in re.findall(r"/wiki/uploads/([A-Za-z0-9._-]+)", text):
        if filename and "/" not in filename and "\\" not in filename:
            filenames.add(filename)

    return filenames


def get_all_referenced_uploaded_files():
    """Return uploaded filenames still referenced by articles or Markdown files."""
    referenced = set()

    # 1) Trust Django article records first. This covers draft/published articles
    # and images tracked in image_assets.
    for article in SuggestedArticle.objects.only("body", "image_assets"):
        referenced.update(article.image_assets or [])
        referenced.update(extract_uploaded_file_filenames_from_text(article.body))

    # 2) Also scan all OpenKB wiki Markdown files. This protects manually added
    # Markdown files or files edited outside Django.
    wiki_dir = settings.OPENKB_WIKI_DIR
    if wiki_dir.exists():
        for markdown_file in wiki_dir.rglob("*.md"):
            try:
                text = markdown_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            referenced.update(extract_uploaded_file_filenames_from_text(text))

    return {
        filename for filename in referenced
        if filename and "/" not in filename and "\\" not in filename
    }



def get_stray_upload_cleanup_min_age_minutes():
    """Return cleanup age threshold from Django Admin site settings."""
    try:
        value = SiteSetting.load().stray_upload_cleanup_min_age_minutes
    except Exception:
        value = 30

    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 30

    return max(value, 0)

def find_stray_uploaded_files(min_age_minutes=30):
    """Return uploaded files that are not referenced anywhere.

    The minimum age protects someone who is currently editing an article: a
    file uploaded seconds ago may not be saved into Markdown yet.
    """
    init_openkb_storage()
    upload_dir = get_openkb_uploads_dir().resolve()
    referenced = get_all_referenced_uploaded_files()
    cutoff_time = timezone.now().timestamp() - (min_age_minutes * 60)

    stray_files = []

    if not upload_dir.exists():
        return stray_files

    for file_path in upload_dir.iterdir():
        if not file_path.is_file():
            continue

        filename = file_path.name
        if filename in referenced:
            continue

        try:
            stat = file_path.stat()
        except OSError:
            continue

        if stat.st_mtime > cutoff_time:
            continue

        extension = file_path.suffix.lower().lstrip(".") or "(no extension)"
        is_previewable_image = file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}

        stray_files.append({
            "filename": filename,
            "url": article_image_url(filename),
            "extension": extension,
            "is_previewable_image": is_previewable_image,
            "size_bytes": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified_at": datetime.fromtimestamp(stat.st_mtime),
            "path": file_path,
        })
    stray_files.sort(key=lambda item: item["modified_at"], reverse=True)
    return stray_files



def make_unique_article_filename(title, original_filename=""):
    """Create a unique Markdown filename for an imported article."""
    timestamp_slug = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    original_name = Path(original_filename or "").name
    original_stem = Path(original_name).stem if original_name else ""
    base_slug = slugify_title(title or original_stem or "imported-article")
    candidate = f"{timestamp_slug}-{base_slug}.md"

    while SuggestedArticle.objects.filter(filename=candidate).exists():
        candidate = f"{timestamp_slug}-{base_slug}-{uuid.uuid4().hex[:6]}.md"

    return candidate


def safe_zip_member_name(name):
    """Return a normalized zip member name, or empty string for unsafe paths."""
    normalized = str(name or "").replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]

    if not parts or any(part == ".." for part in parts):
        return ""

    return "/".join(parts)


def safe_uploaded_filename(name):
    """Keep only the filename portion and strip path traversal characters."""
    filename = Path(str(name or "").replace("\\", "/")).name.strip()
    if not filename or filename in {".", ".."}:
        return ""
    return filename


def make_unique_upload_filename(original_filename):
    """Create a non-conflicting filename under openkb-data/wiki/uploads."""
    upload_dir = get_openkb_uploads_dir()
    original = safe_uploaded_filename(original_filename)
    suffix = Path(original).suffix.lower()
    stem = slugify_title(Path(original).stem or "uploaded-file")
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")

    candidate = f"{timestamp}-{uuid.uuid4().hex[:8]}-{stem}{suffix}"
    while (upload_dir / candidate).exists():
        candidate = f"{timestamp}-{uuid.uuid4().hex[:12]}-{stem}{suffix}"

    return candidate


def rewrite_uploaded_file_references(text, filename_map):
    """Rewrite /wiki/uploads/<old> references after imported files are renamed."""
    updated = text or ""
    for old_name, new_name in filename_map.items():
        if not old_name or not new_name or old_name == new_name:
            continue
        updated = updated.replace(f"/wiki/uploads/{old_name}", f"/wiki/uploads/{new_name}")
        updated = updated.replace(f"uploads/{old_name}", f"uploads/{new_name}")
    return updated


def markdown_title_and_body(markdown_text, fallback_title="Imported article"):
    """Parse a title/body from Markdown when importing a zip without manifest.json."""
    text = (markdown_text or "").lstrip("\ufeff")
    lines = text.splitlines()
    title = fallback_title

    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip() or fallback_title
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]

    body = "\n".join(lines).strip()

    keyword_match = re.search(r"\n?\*\*Keywords:\*\*\s*(.+?)\s*$", body, flags=re.IGNORECASE | re.DOTALL)
    keywords = ""
    if keyword_match:
        keywords = keyword_match.group(1).strip()
        body = body[:keyword_match.start()].rstrip()

    return title, body, keywords


def build_bulk_export_payload():
    """Build the JSON manifest and file list for article bulk export."""
    articles = []
    referenced_uploads = set()

    for article in SuggestedArticle.objects.select_related("owner").order_by("created_at", "id"):
        image_assets = sorted(set((article.image_assets or []) + extract_article_image_filenames(article.body)))
        referenced_uploads.update(image_assets)

        articles.append({
            "title": article.title,
            "body": article.body,
            "keywords": article.keywords,
            "status": article.status,
            "filename": article.filename,
            "raw_path": article.raw_path,
            "wiki_path": article.wiki_path,
            "image_assets": image_assets,
            "created_at": article.created_at.isoformat() if article.created_at else "",
            "updated_at": article.updated_at.isoformat() if article.updated_at else "",
            "author_username": article.author_username,
            "author_email": article.author_email,
        })

    return {
        "format": "djopenkb-bulk-export-v1",
        "exported_at": timezone.now().isoformat(),
        "article_count": len(articles),
        "articles": articles,
        "uploads": sorted(referenced_uploads),
    }


def copy_imported_uploads_from_zip(zip_file, upload_member_names):
    """Copy uploaded files from an import zip into openkb-data/wiki/uploads.

    Returns a mapping of original filename -> new filename so article bodies can
    be rewritten safely when a filename already exists.
    """
    upload_dir = get_openkb_uploads_dir()
    filename_map = {}

    for member_name in upload_member_names:
        safe_member = safe_zip_member_name(member_name)
        if not safe_member:
            continue

        original_filename = safe_uploaded_filename(safe_member)
        if not original_filename:
            continue

        new_filename = make_unique_upload_filename(original_filename)
        target_path = (upload_dir / new_filename).resolve()

        try:
            target_path.relative_to(upload_dir.resolve())
        except ValueError:
            continue

        with zip_file.open(member_name, "r") as source, target_path.open("wb") as target:
            shutil.copyfileobj(source, target)

        filename_map[original_filename] = new_filename

    return filename_map


def import_articles_from_zip(uploaded_zip, owner):
    """Import articles and uploaded files from a DjOpenKB bulk export zip.

    All imported articles are assigned to the admin user performing the import.
    """
    imported_count = 0
    errors = []

    with zipfile.ZipFile(uploaded_zip) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        safe_names = {safe_zip_member_name(item.filename): item.filename for item in members if safe_zip_member_name(item.filename)}

        # Hard safety limits for admin imports.
        total_uncompressed = sum(item.file_size for item in members)
        if total_uncompressed > 200 * 1024 * 1024:
            raise ValueError("Import zip is too large after extraction. Maximum allowed uncompressed size is 200 MB.")

        upload_members = [
            original_name for safe_name, original_name in safe_names.items()
            if safe_name.startswith("uploads/")
        ]
        filename_map = copy_imported_uploads_from_zip(archive, upload_members)

        manifest_name = safe_names.get("manifest.json")
        manifest = None
        if manifest_name:
            with archive.open(manifest_name, "r") as manifest_file:
                manifest = json.loads(manifest_file.read().decode("utf-8"))

        article_payloads = []

        if manifest and manifest.get("format") == "djopenkb-bulk-export-v1":
            for item in manifest.get("articles", []):
                article_payloads.append({
                    "title": item.get("title") or "Imported article",
                    "body": item.get("body") or "",
                    "keywords": item.get("keywords") or "",
                    "status": item.get("status") or SuggestedArticle.Status.PUBLISHED,
                    "filename": item.get("filename") or "",
                })
        else:
            markdown_names = [
                original_name for safe_name, original_name in safe_names.items()
                if safe_name.lower().endswith(".md") and not safe_name.startswith("uploads/")
            ]

            for markdown_name in markdown_names:
                safe_name = safe_zip_member_name(markdown_name)
                with archive.open(markdown_name, "r") as markdown_file:
                    markdown_text = markdown_file.read().decode("utf-8", errors="ignore")

                title, body, keywords = markdown_title_and_body(
                    markdown_text,
                    fallback_title=Path(safe_name).stem.replace("-", " ").replace("_", " ").title(),
                )
                article_payloads.append({
                    "title": title,
                    "body": body,
                    "keywords": keywords,
                    "status": SuggestedArticle.Status.PUBLISHED,
                    "filename": Path(safe_name).name,
                })

        if not article_payloads:
            raise ValueError("No articles found in the zip. Include manifest.json or Markdown files.")

        for item in article_payloads:
            title = (item.get("title") or "Imported article").strip()[:200]
            body = rewrite_uploaded_file_references(item.get("body") or "", filename_map)
            keywords = (item.get("keywords") or "").strip()[:500]
            status = item.get("status") or SuggestedArticle.Status.PUBLISHED

            if status not in dict(SuggestedArticle.Status.choices):
                status = SuggestedArticle.Status.PUBLISHED

            filename = make_unique_article_filename(title, item.get("filename") or "")

            try:
                article = SuggestedArticle.objects.create(
                    owner=owner,
                    title=title,
                    body=body,
                    keywords=keywords,
                    filename=filename,
                    wiki_path=f"sources/{filename}",
                    raw_path=f"raw/{filename}",
                    status=status,
                    image_assets=extract_article_image_filenames(body),
                )
                write_article_files(article)
                sync_article_image_assets(article, old_assets=[])
                imported_count += 1
            except Exception as error:
                errors.append(f"{title}: {error}")

    return imported_count, errors


def get_article_image_cards(article):
    return [
        {
            "filename": filename,
            "url": article_image_url(filename),
            "markdown": article_image_markdown(filename),
            "existing": True,
        }
        for filename in (article.image_assets or extract_article_image_filenames(article.body))
    ]


def clean_wiki_title(file_path):
    return file_path.stem.replace("-", " ").replace("_", " ").title()


def format_user_for_markdown(user):
    full_name = user.get_full_name().strip()
    if full_name and user.email:
        return f"{full_name} ({user.email})"
    if user.email:
        return user.email
    return user.get_username()


def build_article_markdown(article):
    keyword_line = ""
    if article.keywords:
        keyword_line = f"\n\n**Keywords:** {article.keywords}"

    return (
        f"# {article.title}\n\n"
        f"{article.body}\n"
        f"{keyword_line}\n"
    )


def prepare_article_display_markdown(raw_markdown, title, suggested=None):
    """Remove Django-generated wrapper Markdown from public article display.

    SuggestedArticle files are saved with a leading H1 title and a trailing
    **Keywords:** metadata line so the Markdown file is self-contained.
    The public article page already displays the title and keywords in the
    OpenKB layout, so remove those generated lines to avoid duplicate UI text.
    """
    cleaned = raw_markdown.lstrip("\ufeff")

    # Remove the first Markdown H1 only when it matches the article title.
    lines = cleaned.splitlines()
    if lines:
        first_line = lines[0].strip()
        if first_line.startswith("#"):
            heading_text = first_line.lstrip("#").strip()
            if heading_text.lower() == title.strip().lower():
                lines = lines[1:]
                while lines and not lines[0].strip():
                    lines = lines[1:]
                cleaned = "\n".join(lines)

    # Remove the generated keywords line from the body. Keywords are displayed
    # below the article details panel instead.
    if suggested and suggested.keywords:
        cleaned = re.sub(
            r"\n*\*\*Keywords:\*\*\s*.*\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).rstrip()

    return cleaned


def write_article_files(article):
    """Mirror a SuggestedArticle into OpenKB raw and public wiki/source Markdown files."""
    init_openkb_storage()

    if not article.filename:
        timestamp_slug = timezone.localtime(article.created_at or timezone.now()).strftime("%Y%m%d-%H%M%S")
        article.filename = f"{timestamp_slug}-{slugify_title(article.title)}.md"

    raw_file_path = settings.OPENKB_RAW_DIR / article.filename
    sources_dir = settings.OPENKB_WIKI_DIR / "sources"
    wiki_file_path = sources_dir / article.filename

    markdown_content = build_article_markdown(article)

    raw_file_path.write_text(markdown_content, encoding="utf-8")

    if article.status == SuggestedArticle.Status.PUBLISHED:
        wiki_file_path.write_text(markdown_content, encoding="utf-8")
    elif wiki_file_path.exists():
        wiki_file_path.unlink()

    article.raw_path = raw_file_path.relative_to(settings.OPENKB_DATA_DIR).as_posix()
    article.wiki_path = f"sources/{article.filename}"

    SuggestedArticle.objects.filter(pk=article.pk).update(
        filename=article.filename,
        raw_path=article.raw_path,
        wiki_path=article.wiki_path,
    )


def delete_article_files(article):
    """Delete Markdown files and image assets for a user-owned article."""
    candidates = []

    if article.raw_path:
        candidates.append((settings.OPENKB_DATA_DIR / article.raw_path, settings.OPENKB_DATA_DIR))

    if article.wiki_path:
        candidates.append((settings.OPENKB_WIKI_DIR / article.wiki_path, settings.OPENKB_WIKI_DIR))

    for file_path, root_dir in candidates:
        file_path = file_path.resolve()
        root_dir = root_dir.resolve()

        if str(file_path).startswith(str(root_dir)) and file_path.exists() and file_path.is_file():
            file_path.unlink()

    for filename in (article.image_assets or extract_article_image_filenames(article.body)):
        if not image_is_used_by_other_article(filename, current_article=article):
            delete_uploaded_image_file(filename)


def get_article_metadata_by_wiki_path(wiki_path):
    try:
        return SuggestedArticle.objects.select_related("owner").get(
            wiki_path=wiki_path,
            status=SuggestedArticle.Status.PUBLISHED,
        )
    except SuggestedArticle.DoesNotExist:
        return None



def record_article_session_view(request, article):
    """Increment an article view count once per browser session.

    This prevents simple page refreshes from repeatedly increasing the counter.
    A different browser/session can still count as a new view.
    """
    if not article or not article.pk:
        return

    session_key = "viewed_suggested_article_ids"
    viewed_ids = request.session.get(session_key, [])
    article_key = str(article.pk)

    if article_key in viewed_ids:
        return

    SuggestedArticle.objects.filter(pk=article.pk).update(view_count=F("view_count") + 1)
    article.view_count = (article.view_count or 0) + 1

    viewed_ids.append(article_key)
    request.session[session_key] = viewed_ids[-1000:]
    request.session.modified = True

def get_openkb_wiki_articles(sort_by_views=False):
    """Read OpenKB Markdown files from openkb-data/wiki, including raw content."""
    init_openkb_storage()

    wiki_dir = settings.OPENKB_WIKI_DIR
    articles = []

    if not wiki_dir.exists():
        return articles

    suggested_by_path = {
        item.wiki_path: item
        for item in SuggestedArticle.objects.select_related("owner").filter(
            status=SuggestedArticle.Status.PUBLISHED,
        )
    }

    for file_path in wiki_dir.rglob("*.md"):
        if file_path.name in IGNORED_WIKI_NAMES:
            continue

        relative_path = file_path.relative_to(wiki_dir).as_posix()
        suggested = suggested_by_path.get(relative_path)
        modified_at = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

        if relative_path.startswith("summaries/"):
            article_type = "OpenKB AI Summary"
        elif relative_path.startswith("sources/"):
            article_type = "OpenKB Source"
        else:
            article_type = "OpenKB Wiki"

        raw_markdown = file_path.read_text(encoding="utf-8", errors="ignore")

        articles.append({
            "title": suggested.title if suggested else clean_wiki_title(file_path),
            "type": article_type,
            "date": modified_at,
            "views": suggested.view_count if suggested else 0,
            "url": f"/wiki/{relative_path}",
            "path": relative_path,
            "raw_markdown": raw_markdown,
            "author": suggested.author_display if suggested else "",
        })

    if sort_by_views:
        articles.sort(
            key=lambda item: (item.get("views") or 0, item.get("date") or ""),
            reverse=True,
        )
    else:
        articles.sort(key=lambda item: item["date"], reverse=True)
    return articles




def is_ldap_managed_user(user):
    """Return True when the account should be treated as LDAP-managed.

    LDAP-created users commonly have an unusable local Django password. The
    email/username domain check keeps the UI sensible for company LDAP accounts
    even if the backend stores a local password differently.
    """
    username = (user.get_username() or "").lower()
    email = (user.email or "").lower()
    return (
        not user.has_usable_password()
        or username.endswith("@nextlabs.com")
        or email.endswith("@nextlabs.com")
    )


def get_user_profile(user):
    if not user.is_authenticated:
        return None

    profile, created = UserProfile.objects.get_or_create(user=user)

    # Keep existing superuser/staff accounts visible as Admin unless already LDAP admin.
    if (user.is_superuser or user.is_staff) and profile.account_type not in {
        UserProfile.AccountType.ADMIN,
        UserProfile.AccountType.LDAP_ADMIN,
    }:
        profile.account_type = UserProfile.AccountType.ADMIN
        profile.save(update_fields=["account_type", "updated_at"])

    return profile


def user_can_access_main_site(user):
    if not user.is_authenticated or not user.is_active:
        return False

    profile = get_user_profile(user)
    return bool(profile and profile.can_access_main_site)


def main_site_login_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not user_can_access_main_site(request.user):
            logout(request)
            return redirect("home")
        return view_func(request, *args, **kwargs)

    return wrapper


def get_account_type_display(user):
    profile = get_user_profile(user)
    if not profile:
        return "Guest"
    return profile.get_account_type_display()


def format_profile_display_name(user):
    full_name = user.get_full_name().strip()
    return full_name or user.get_username()

class OpenKBLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if user_can_access_main_site(request.user):
                return redirect(self.get_success_url())
            logout(request)
            return redirect("home")

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if not user_can_access_main_site(user):
            logout(self.request)
            return self.form_invalid(form)
        return super().form_valid(form)


@require_POST
def set_site_language(request):
    """Set the active UI language from the navbar dropdown.

    Anonymous users store the choice in the django_language cookie.
    Logged-in users also sync the same choice to their UserProfile.
    """
    language_code = (request.POST.get("language") or "").strip().lower()
    allowed_codes = {code for code, _name in settings.LANGUAGES}

    if language_code not in allowed_codes:
        language_code = settings.LANGUAGE_CODE

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("home")
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("home")

    if request.user.is_authenticated:
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.preferred_language = language_code
        profile.save(update_fields=["preferred_language", "updated_at"])

    translation.activate(language_code)
    request.LANGUAGE_CODE = language_code

    response = redirect(next_url)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language_code,
        max_age=60 * 60 * 24 * 365,
        samesite="Lax",
    )
    return response


def paginate_articles(request, articles, per_page=20):
    """Paginate article lists safely for the index/search page."""
    paginator = Paginator(articles, per_page)
    page_number = request.GET.get("page", 1)

    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return page_obj


def home(request):
    all_articles = get_openkb_wiki_articles(sort_by_views=True)
    page_obj = paginate_articles(request, all_articles, per_page=20)

    return render(request, "index.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": page_obj.paginator,
        "total_article_count": len(all_articles),
    })


@main_site_login_required
def suggest(request):
    init_openkb_storage()

    if request.method == "GET":
        return render(request, "suggest.html")

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()

    if len(title) < 5 or len(body) < 5:
        return render(request, "suggest.html", {
            "error": "Article title and body must be at least 5 characters.",
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
        })

    timestamp_slug = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp_slug}-{slugify_title(title)}.md"

    article = SuggestedArticle.objects.create(
        owner=request.user,
        title=title,
        body=body,
        keywords=keywords_raw,
        filename=filename,
        wiki_path=f"sources/{filename}",
        raw_path=f"raw/{filename}",
        status=SuggestedArticle.Status.PUBLISHED,
        image_assets=extract_article_image_filenames(body),
    )
    write_article_files(article)
    sync_article_image_assets(article, old_assets=[])
    clear_committed_pending_uploads(request, article.image_assets)

    messages.success(request, f"Article suggested successfully: {article.title}")
    return redirect("edit_my_suggestions")


def get_profile_account_context(user):
    user_is_ldap_managed = is_ldap_managed_user(user)
    profile, created = UserProfile.objects.get_or_create(user=user)

    return {
        "total_user_article_count": SuggestedArticle.objects.filter(owner=user).count(),
        "profile_display_name": format_profile_display_name(user),
        "user_is_ldap_managed": user_is_ldap_managed,
        "account_type_display": get_account_type_display(user),
        "can_change_local_password": user.has_usable_password() and not user_is_ldap_managed,
        "can_confirm_profile_changes": user.has_usable_password(),
        "can_use_admin_tools": user_can_use_admin_tools(user),
        "profile_preferred_language": profile.preferred_language,
        "supported_languages": settings.LANGUAGES,
    }


@main_site_login_required
def profile(request):
    return render(request, "profile.html", get_profile_account_context(request.user))


@admin_tools_required
def clean_stray_upload_files(request):
    min_age_minutes = get_stray_upload_cleanup_min_age_minutes()
    stray_files = find_stray_uploaded_files(min_age_minutes=min_age_minutes)
    total_size_bytes = sum(item["size_bytes"] for item in stray_files)

    if request.method == "POST":
        deleted_count = 0
        deleted_size_bytes = 0
        errors = []

        # Re-scan on POST so the cleanup uses the latest file/article state.
        for item in stray_files:
            file_path = item["path"]
            upload_dir = get_openkb_uploads_dir().resolve()
            file_path = file_path.resolve()

            try:
                file_path.relative_to(upload_dir)
            except ValueError:
                errors.append(f"Skipped invalid path: {item['filename']}")
                continue

            try:
                if file_path.exists() and file_path.is_file():
                    deleted_size_bytes += file_path.stat().st_size
                    file_path.unlink()
                    deleted_count += 1
            except OSError as error:
                errors.append(f"Could not delete {item['filename']}: {error}")

        if deleted_count:
            messages.success(
                request,
                f"Cleaned up {deleted_count} stray upload file(s), freeing {round(deleted_size_bytes / 1024, 1)} KB."
            )
        else:
            messages.info(request, "No stray upload files were deleted.")

        for error in errors[:5]:
            messages.error(request, error)

        return redirect("clean_stray_upload_files")

    return render(request, "admin_clean_stray_upload_files.html", {
        "stray_files": stray_files,
        "stray_count": len(stray_files),
        "total_size_kb": round(total_size_bytes / 1024, 1),
        "min_age_minutes": min_age_minutes,
    })



@admin_tools_required
def admin_bulk_articles(request):
    """Admin page for importing/exporting article bundles."""
    return render(request, "admin_bulk_articles.html")


@admin_tools_required
def export_articles_zip(request):
    """Export all Django-managed articles plus referenced uploaded files as a zip."""
    manifest = build_bulk_export_payload()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr(
            "README.txt",
            (
                "DjOpenKB bulk article export.\n"
                "Import this zip from My Profile -> Admin tools -> Bulk import/export articles.\n"
                "Articles are stored in manifest.json and articles/*.md.\n"
                "Referenced uploaded files are stored in uploads/.\n"
            ),
        )

        for article in manifest["articles"]:
            article_filename = safe_uploaded_filename(article.get("filename")) or f"{slugify_title(article.get('title') or 'article')}.md"
            archive.writestr(f"articles/{article_filename}", build_article_markdown(type("ArticleExport", (), article)))

        upload_dir = get_openkb_uploads_dir().resolve()
        exported_uploads = set()

        for filename in manifest.get("uploads", []):
            filename = safe_uploaded_filename(filename)
            if not filename or filename in exported_uploads:
                continue

            file_path = (upload_dir / filename).resolve()
            try:
                file_path.relative_to(upload_dir)
            except ValueError:
                continue

            if file_path.exists() and file_path.is_file():
                archive.write(file_path, f"uploads/{filename}")
                exported_uploads.add(filename)

    buffer.seek(0)
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="djopenkb-export-{timestamp}.zip"'
    return response


@admin_tools_required
def import_articles_zip(request):
    """Import articles from a zip and assign ownership to the current admin user."""
    if request.method != "POST":
        return redirect("admin_bulk_articles")

    uploaded_zip = request.FILES.get("import_zip")
    if not uploaded_zip:
        messages.error(request, "Please choose a .zip file to import.")
        return redirect("admin_bulk_articles")

    if not uploaded_zip.name.lower().endswith(".zip"):
        messages.error(request, "Only .zip import files are allowed.")
        return redirect("admin_bulk_articles")

    max_upload_size = 100 * 1024 * 1024
    if uploaded_zip.size > max_upload_size:
        messages.error(request, "Import zip is too large. Maximum allowed size is 100 MB.")
        return redirect("admin_bulk_articles")

    try:
        imported_count, errors = import_articles_from_zip(uploaded_zip, owner=request.user)
    except zipfile.BadZipFile:
        messages.error(request, "Invalid zip file.")
        return redirect("admin_bulk_articles")
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("admin_bulk_articles")
    except Exception as error:
        messages.error(request, f"Import failed: {error}")
        return redirect("admin_bulk_articles")

    if imported_count:
        messages.success(request, f"Imported {imported_count} article(s). Owner set to {request.user.get_username()}.")
    else:
        messages.warning(request, "No articles were imported.")

    for error in errors[:10]:
        messages.error(request, error)

    if len(errors) > 10:
        messages.error(request, f"{len(errors) - 10} more import error(s) were hidden.")

    return redirect("admin_bulk_articles")


@main_site_login_required
def edit_my_suggestions(request):
    search_query = request.GET.get("q", "").strip()

    article_queryset = SuggestedArticle.objects.filter(owner=request.user)
    total_user_article_count = article_queryset.count()

    if search_query:
        article_queryset = article_queryset.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(keywords__icontains=search_query)
            | Q(status__icontains=search_query)
            | Q(filename__icontains=search_query)
            | Q(wiki_path__icontains=search_query)
        )

    article_queryset = article_queryset.order_by("-updated_at", "-created_at")
    page_obj = paginate_articles(request, article_queryset, per_page=20)

    return render(request, "edit_my_suggestions.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "profile_search_query": search_query,
        "profile_result_count": article_queryset.count(),
        "total_user_article_count": total_user_article_count,
        "is_profile_search": bool(search_query),
        "profile_display_name": format_profile_display_name(request.user),
    })


@main_site_login_required
def update_profile(request):
    if request.method != "POST":
        return redirect("profile")

    User = get_user_model()
    user = request.user
    user_is_ldap_managed = is_ldap_managed_user(user)
    profile_action = request.POST.get("profile_action", "").strip()

    if profile_action == "language":
        language_code = request.POST.get("preferred_language", "").strip()
        allowed_codes = {code for code, _name in settings.LANGUAGES}

        if language_code not in allowed_codes:
            messages.error(request, _("Invalid language selected."))
            return redirect("profile")

        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.preferred_language = language_code
        profile.save(update_fields=["preferred_language", "updated_at"])

        translation.activate(language_code)
        request.LANGUAGE_CODE = language_code

        messages.success(request, _("Language preference updated successfully."))
        response = redirect("profile")
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            language_code,
            max_age=60 * 60 * 24 * 365,
            samesite="Lax",
        )
        return response

    # For Django local accounts, require the current password before changing
    # username/email. LDAP users normally do not have a local usable password,
    # so LDAP-managed fields are protected by backend rules instead.
    if user.has_usable_password():
        current_password = request.POST.get("current_password", "")
        if not user.check_password(current_password):
            messages.error(request, _("Confirm password is incorrect."))
            return redirect("profile")

    if profile_action == "username":
        username = request.POST.get("username", "").strip()
        if not username:
            messages.error(request, _("Username cannot be empty."))
            return redirect("profile")

        username_exists = User.objects.exclude(pk=user.pk).filter(username__iexact=username).exists()
        if username_exists:
            messages.error(request, _("That username is already used by another account."))
            return redirect("profile")

        user.username = username
        user.save(update_fields=["username"])
        messages.success(request, _("Username updated successfully."))
        return redirect("profile")

    if profile_action == "email":
        if user_is_ldap_managed:
            messages.error(request, _("LDAP email is managed by LDAP/AD and cannot be changed here."))
            return redirect("profile")

        email = request.POST.get("email", "").strip()
        user.email = email
        user.save(update_fields=["email"])
        messages.success(request, _("Email updated successfully."))
        return redirect("profile")

    messages.error(request, _("Invalid profile update request."))
    return redirect("profile")



def validate_profile_password_policy(password, user):
    """Return password policy issues for the profile change-password form.

    This enforces the local project policy used for Django-managed accounts:
    minimum length plus uppercase, lowercase, number, and special character.
    LDAP-managed users should change passwords through LDAP/AD instead.
    """
    issues = []

    if len(password) < 12:
        issues.append("Password must be at least 12 characters long.")
    if not re.search(r"[A-Z]", password):
        issues.append("Password must include at least 1 uppercase letter.")
    if not re.search(r"[a-z]", password):
        issues.append("Password must include at least 1 lowercase letter.")
    if not re.search(r"[0-9]", password):
        issues.append("Password must include at least 1 number.")
    if not re.search(r"[^A-Za-z0-9]", password):
        issues.append("Password must include at least 1 special character.")

    lower_password = password.lower()
    username = (user.get_username() or "").lower()
    email_name = (user.email or "").split("@")[0].lower()

    if username and len(username) >= 3 and username in lower_password:
        issues.append("Password must not contain your username.")
    if email_name and len(email_name) >= 3 and email_name in lower_password:
        issues.append("Password must not contain the name part of your email address.")

    return issues

@main_site_login_required
def change_password(request):
    if request.method != "POST":
        return redirect("profile")

    user = request.user

    if is_ldap_managed_user(user) or not user.has_usable_password():
        messages.error(request, "This account is managed by LDAP. Please change your password through the company password system.")
        return redirect("profile")

    old_password = request.POST.get("old_password", "")
    new_password1 = request.POST.get("new_password1", "")
    new_password2 = request.POST.get("new_password2", "")

    if not user.check_password(old_password):
        messages.error(request, "Old password is incorrect.")
        return redirect("profile")

    if new_password1 != new_password2:
        messages.error(request, "New password and confirm password do not match.")
        return redirect("profile")

    policy_issues = validate_profile_password_policy(new_password1, user)
    if policy_issues:
        messages.error(request, " ".join(policy_issues))
        return redirect("profile")

    try:
        validate_password(new_password1, user=user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("profile")

    user.set_password(new_password1)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    messages.success(request, "Password changed successfully.")
    return redirect("edit_my_suggestions")


@main_site_login_required
def edit_suggestion(request, article_id):
    article = get_object_or_404(SuggestedArticle, pk=article_id)

    if article.owner != request.user and not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to edit this article.")

    if request.method == "GET":
        return render(request, "suggest_edit.html", {
            "article": article,
            "current_status": article.status,
            "existing_images_json": json.dumps(get_article_image_cards(article)),
        })

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()
    status = request.POST.get("status", SuggestedArticle.Status.PUBLISHED)

    if status not in SuggestedArticle.Status.values:
        status = SuggestedArticle.Status.PUBLISHED

    if len(title) < 5 or len(body) < 5:
        return render(request, "suggest_edit.html", {
            "article": article,
            "error": "Article title and body must be at least 5 characters.",
            "title_value": title,
            "body_value": body,
            "keywords_value": keywords_raw,
            "status_value": status,
            "current_status": status,
            "existing_images_json": json.dumps(get_article_image_cards(article)),
        })

    old_image_assets = list(article.image_assets or extract_article_image_filenames(article.body))
    article.title = title
    article.body = body
    article.keywords = keywords_raw
    article.status = status
    article.image_assets = extract_article_image_filenames(body)
    article.save()
    write_article_files(article)
    sync_article_image_assets(article, old_assets=old_image_assets)
    clear_committed_pending_uploads(request, article.image_assets)

    messages.success(request, f"Article updated: {article.title}")
    return redirect("edit_my_suggestions")


@main_site_login_required
def delete_suggestion(request, article_id):
    article = get_object_or_404(SuggestedArticle, pk=article_id)

    if article.owner != request.user and not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to delete this article.")

    if request.method == "POST":
        title = article.title
        delete_article_files(article)
        article.delete()
        messages.success(request, f"Article deleted: {title}")
        return redirect("edit_my_suggestions")

    return render(request, "suggest_delete.html", {"article": article})


@main_site_login_required
@require_POST
def upload_article_image(request):
    """Upload a small pasted image for use inside Markdown articles.

    The endpoint is intentionally login-protected because only logged-in users
    can create/edit suggestions. The returned Markdown can be inserted directly
    into the editor, for example: ![image](/wiki/uploads/abc.png)
    """
    uploaded_file = request.FILES.get("image")

    if not uploaded_file:
        return JsonResponse({"error": "No image file received."}, status=400)

    extension = is_allowed_article_image(uploaded_file)
    if not extension:
        return JsonResponse({"error": "Only PNG, JPG, GIF, or WEBP images are allowed."}, status=400)

    max_size = 2 * 1024 * 1024
    if uploaded_file.size > max_size:
        return JsonResponse({"error": "Image is too large. Maximum allowed size is 2 MB."}, status=400)

    upload_dir = get_openkb_uploads_dir()
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{uuid.uuid4().hex[:12]}{extension}"
    file_path = upload_dir / filename

    with file_path.open("wb") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)

    pending_uploads = request.session.get("pending_article_uploads", [])
    if filename not in pending_uploads:
        pending_uploads.append(filename)
    request.session["pending_article_uploads"] = pending_uploads[-100:]
    request.session.modified = True

    image_url = f"/wiki/uploads/{filename}"
    return JsonResponse({
        "url": image_url,
        "filename": filename,
        "markdown": f"![image]({image_url})",
    })


@main_site_login_required
@require_POST
def delete_article_image(request):
    """Delete a pasted image that was uploaded during the current editing session.

    This endpoint is used by the editor's image preview tray. It only deletes
    filenames stored in the current session's pending upload list, so a user
    cannot delete arbitrary article images by guessing a filename.
    """
    filename = (request.POST.get("filename") or "").strip()
    if not filename:
        return JsonResponse({"error": "No image filename received."}, status=400)

    # Basic filename-only guard. Uploaded names are generated by the server and
    # should not contain path separators.
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        return JsonResponse({"error": "Invalid image filename."}, status=400)

    pending_uploads = request.session.get("pending_article_uploads", [])
    if filename not in pending_uploads and not request.user.is_staff:
        return JsonResponse({"error": "This image is not removable from this editing session."}, status=403)

    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / filename).resolve()
    if not str(file_path).startswith(str(upload_dir)):
        return JsonResponse({"error": "Invalid image path."}, status=400)

    if file_path.exists() and file_path.is_file():
        file_path.unlink()

    request.session["pending_article_uploads"] = [item for item in pending_uploads if item != filename]
    request.session.modified = True
    return JsonResponse({"deleted": True})


def serve_article_image(request, filename):
    """Serve images pasted into Markdown articles from openkb-data/wiki/uploads."""
    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / filename).resolve()

    if not str(file_path).startswith(str(upload_dir)):
        raise Http404("Invalid image path")

    if not file_path.exists() or not file_path.is_file():
        raise Http404("Image not found")

    content_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    content_type = content_types.get(file_path.suffix.lower(), "application/octet-stream")
    return FileResponse(file_path.open("rb"), content_type=content_type)


def wiki_detail(request, wiki_path):
    """Display Markdown files from openkb-data/wiki/."""
    init_openkb_storage()

    wiki_dir = settings.OPENKB_WIKI_DIR.resolve()
    file_path = (wiki_dir / wiki_path).resolve()

    if not str(file_path).startswith(str(wiki_dir)):
        raise Http404("Invalid wiki path")

    if not file_path.exists() or not file_path.is_file() or file_path.suffix.lower() != ".md":
        raise Http404("Wiki page not found")

    raw_markdown = file_path.read_text(encoding="utf-8", errors="ignore")
    suggested = get_article_metadata_by_wiki_path(wiki_path)

    if suggested:
        record_article_session_view(request, suggested)
        title = suggested.title
        display_markdown = prepare_article_display_markdown(raw_markdown, title, suggested)
        metadata = {
            "has_details": True,
            "type": "OpenKB Source",
            "path": wiki_path,
            "published_at": suggested.created_at,
            "updated_at": suggested.updated_at,
            "author": suggested.author_display,
            "author_username": suggested.author_username,
            "author_email": suggested.author_email,
            "author_account_type": suggested.author_account_type,
            "keywords": suggested.keyword_list,
            "permalink": request.build_absolute_uri(suggested.public_url),
            "view_count": suggested.view_count,
            "helpful_vote_count": suggested.votes.filter(value=ArticleVote.VoteValue.UP).count(),
            "unhelpful_vote_count": suggested.votes.filter(value=ArticleVote.VoteValue.DOWN).count(),
            "total_vote_count": suggested.votes.count(),
            "user_vote": (
                suggested.votes.filter(user=request.user).values_list("value", flat=True).first()
                if request.user.is_authenticated else None
            ),
            "vote_url": reverse("vote_article", kwargs={"article_id": suggested.pk}),
            "can_vote": request.user.is_authenticated,
            "login_url": f'{reverse("login")}?next={request.get_full_path()}',
            "can_edit": request.user.is_authenticated and (
                request.user == suggested.owner or request.user.is_staff
            ),
            "edit_url": reverse("edit_suggestion", kwargs={"article_id": suggested.pk}),
            "delete_url": reverse("delete_suggestion", kwargs={"article_id": suggested.pk}),
        }
    else:
        title = clean_wiki_title(file_path)
        display_markdown = raw_markdown
        metadata = {
            "has_details": True,
            "type": "OpenKB Wiki",
            "path": wiki_path,
            "published_at": None,
            "updated_at": datetime.fromtimestamp(file_path.stat().st_mtime),
            "author": "OpenKB",
            "author_username": "",
            "author_email": "",
            "author_account_type": "",
            "keywords": [],
            "permalink": request.build_absolute_uri(),
            "view_count": 0,
            "helpful_vote_count": 0,
            "unhelpful_vote_count": 0,
            "total_vote_count": 0,
            "user_vote": None,
            "vote_url": "",
            "can_vote": False,
            "login_url": f'{reverse("login")}?next={request.get_full_path()}',
            "can_edit": False,
            "edit_url": "",
            "delete_url": "",
        }

    featured_articles = [
        article for article in get_openkb_wiki_articles()
        if article.get("path") != wiki_path
    ][:5]

    html_content = markdown.markdown(display_markdown, extensions=["fenced_code", "tables", "toc"])

    return render(request, "articles.html", {
        "title": title,
        "content": html_content,
        "raw_markdown": raw_markdown,
        "metadata": metadata,
        "featured_articles": featured_articles,
    })



@require_POST
@login_required
@main_site_login_required
def vote_article(request, article_id):
    """Save one helpful/unhelpful vote per logged-in user per article."""
    article = get_object_or_404(
        SuggestedArticle,
        pk=article_id,
        status=SuggestedArticle.Status.PUBLISHED,
    )

    vote_value = request.POST.get("vote")
    if vote_value == "up":
        value = ArticleVote.VoteValue.UP
    elif vote_value == "down":
        value = ArticleVote.VoteValue.DOWN
    else:
        messages.error(request, _("Invalid vote."))
        return redirect(article.public_url)

    existing_vote = ArticleVote.objects.filter(
        article=article,
        user=request.user,
    ).first()

    if existing_vote and existing_vote.value == value:
        existing_vote.delete()
        messages.success(request, _("Your vote has been removed."))
    elif existing_vote:
        existing_vote.value = value
        existing_vote.save(update_fields=["value", "updated_at"])
        messages.success(request, _("Your vote has been updated."))
    else:
        ArticleVote.objects.create(
            article=article,
            user=request.user,
            value=value,
        )
        messages.success(request, _("Thank you. Your vote has been saved."))

    next_url = request.POST.get("next") or article.public_url
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = article.public_url

    return redirect(next_url)


def search_articles(request):
    """Search OpenKB source and summary Markdown files."""
    init_openkb_storage()

    query_original = request.GET.get("q", "").strip()
    query = query_original.lower()
    results = []

    if query:
        for article in get_openkb_wiki_articles():
            file_path = settings.OPENKB_WIKI_DIR / article["path"]
            if not file_path.exists():
                continue

            body = file_path.read_text(encoding="utf-8", errors="ignore")
            searchable_text = " ".join([article["title"], body]).lower()

            if query in searchable_text:
                results.append(article)

    all_articles = results if query_original else get_openkb_wiki_articles()
    page_obj = paginate_articles(request, all_articles, per_page=20)

    return render(request, "index.html", {
        "articles": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": page_obj.paginator,
        "search_query": query_original,
        "is_search": bool(query_original),
        "result_count": len(results),
        "total_article_count": len(all_articles),
    })


def run_openkb_query(question):
    """Call the original OpenKB CLI from inside openkb-data."""
    init_openkb_storage()

    env = os.environ.copy()

    if getattr(settings, "LLM_API_KEY", ""):
        env["LLM_API_KEY"] = settings.LLM_API_KEY

    if getattr(settings, "GEMINI_API_KEY", ""):
        env["GEMINI_API_KEY"] = settings.GEMINI_API_KEY

    env["OPENKB_AI_PROVIDER"] = "openkb-cli"
    env["OPENKB_GEMINI_MODEL"] = getattr(settings, "OPENKB_GEMINI_MODEL", "gemini/gemini-2.5-flash")
    env["LITELLM_DROP_PARAMS"] = "true"
    env["DROP_PARAMS"] = "true"

    command = (
        "import litellm; "
        "litellm.drop_params = True; "
        "from openkb.cli import cli; "
        "cli.main("
        "args=['query', __import__('sys').argv[1]], "
        "prog_name='openkb', "
        "standalone_mode=False"
        ")"
    )

    result = subprocess.run(
        [sys.executable, "-c", command, question],
        cwd=str(settings.OPENKB_DATA_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "openkb query failed")

    return result.stdout.strip()


def find_related_openkb_articles(question, limit=5):
    """Find related OpenKB articles so Ask OpenKB AI can show clickable sources."""
    init_openkb_storage()

    query = question.lower().strip()
    query_words = [
        word for word in re.findall(r"[a-zA-Z0-9]+", query)
        if len(word) >= 2
    ]

    if not query_words:
        return []

    wiki_dir = settings.OPENKB_WIKI_DIR

    if not wiki_dir.exists():
        return []

    related = []

    for file_path in wiki_dir.rglob("*.md"):
        if file_path.name in IGNORED_WIKI_NAMES:
            continue

        relative_path = file_path.relative_to(wiki_dir).as_posix()
        raw_markdown = file_path.read_text(encoding="utf-8", errors="ignore")

        searchable_text = " ".join([
            file_path.stem,
            relative_path,
            raw_markdown,
        ]).lower()

        score = 0

        for word in query_words:
            if word in searchable_text:
                score += 1

            if word in file_path.stem.lower():
                score += 3

            if word in relative_path.lower():
                score += 2

        if query and query in searchable_text:
            score += 10

        if relative_path.startswith("sources/"):
            score += 5
            article_type = "OpenKB Source"
        elif relative_path.startswith("summaries/"):
            score += 1
            article_type = "OpenKB AI Summary"
        else:
            score += 3
            article_type = "OpenKB Wiki"

        if score > 0:
            suggested = get_article_metadata_by_wiki_path(relative_path)
            related.append({
                "score": score,
                "title": suggested.title if suggested else clean_wiki_title(file_path),
                "type": article_type,
                "path": relative_path,
                "url": f"/wiki/{relative_path}",
            })

    related.sort(key=lambda item: item["score"], reverse=True)

    results = []
    seen_paths = set()

    for item in related:
        if item["path"] in seen_paths:
            continue

        seen_paths.add(item["path"])
        results.append({
            "title": item["title"],
            "type": item["type"],
            "path": item["path"],
            "url": item["url"],
        })

        if len(results) >= limit:
            break

    return results


@csrf_exempt
def ask_openkb_ai(request):
    """Use the real local OpenKB AI query flow."""
    if request.method != "POST":
        return JsonResponse({"error": "POST request required"}, status=405)

    question = request.POST.get("question", "").strip()

    if not question:
        return JsonResponse({"error": "Please type a question first."}, status=400)

    if not settings.OPENKB_DATA_DIR.exists():
        return JsonResponse({
            "error": "OpenKB data folder not found. Check OPENKB_DATA_DIR in settings.py."
        }, status=500)

    try:
        answer = run_openkb_query(question)

        if not answer:
            answer = "OpenKB AI returned an empty response."

        related_articles = find_related_openkb_articles(question)

        return JsonResponse({
            "answer": answer,
            "related_articles": related_articles,
        })

    except FileNotFoundError:
        return JsonResponse({
            "error": "OpenKB CLI not found. Run: python -m pip install -e OpenKB-main"
        }, status=500)

    except subprocess.TimeoutExpired:
        return JsonResponse({
            "error": "OpenKB AI query timed out. Try a shorter question."
        }, status=500)

    except Exception as error:
        return JsonResponse({
            "error": f"OpenKB AI query failed: {str(error)}",
            "related_articles": [],
        }, status=500)
