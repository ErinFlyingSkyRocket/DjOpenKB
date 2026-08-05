"""Helper functions split out from kb.views.services into kb.views.services_bulk.

This module is imported back by services.py so existing imports continue to work.
"""

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from ..models import ARTICLE_REVIEW_HISTORY_MAX_ENTRIES, ARTICLE_REVIEW_NOTES_MAX_LENGTH
from .services import *  # noqa: F401,F403

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


def normalize_import_keywords(value):
    """Return a safe comma-separated keyword string for import/export.

    Supports current exports, older hand-made JSON, and list-style tag fields.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        value = ", ".join(parts)
    else:
        value = str(value).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def get_import_keyword_value(item, *names):
    """Read keyword aliases from an import manifest item.

    The canonical field is `keywords`, but this also accepts common aliases so
    manually prepared imports do not lose keyword data.
    """
    for name in names:
        if name in item and item.get(name) not in (None, ""):
            return normalize_import_keywords(item.get(name))
    return ""


BULK_EXPORT_PART_SIZE_BYTES = 95 * 1024 * 1024
BULK_IMPORT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
BULK_IMPORT_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
BULK_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
BULK_IMPORT_MAX_TOTAL_MEMBERS = 5000
BULK_IMPORT_MAX_PART_ARCHIVES = 20
BULK_IMPORT_MAX_NESTING_DEPTH = 1
BULK_IMPORT_MAX_MANIFEST_BYTES = 5 * 1024 * 1024
BULK_IMPORT_MAX_ARTICLES = 2000
BULK_IMPORT_MAX_STRING_FIELD_BYTES = 2 * 1024 * 1024

_BULK_ARTICLE_ALLOWED_FIELDS = {
    "title", "body", "keywords", "keyword", "keyword_list", "tags",
    "visibility", "status", "filename", "raw_path", "wiki_path",
    "image_assets", "update_status", "pending_update_title",
    "pending_update_body", "pending_update_keywords",
    "pending_update_keyword", "pending_update_keyword_list",
    "pending_update_tags", "pending_update_image_assets", "review_notes",
    "review_notes_history", "created_at", "updated_at", "published_at",
    "author_username", "author_email",
}
_BULK_STANDARD_MANIFEST_ALLOWED_FIELDS = {
    "format", "exported_at", "article_count", "articles", "uploads",
    "part_number", "part_count",
}
_BULK_SPLIT_MANIFEST_ALLOWED_FIELDS = {
    "format", "exported_at", "part_count", "part_size_target_bytes", "parts",
}
_BULK_SPLIT_PART_ALLOWED_FIELDS = {
    "filename", "size_bytes", "article_count", "upload_count",
}


def _require_manifest_mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(_("%(label)s must be a JSON object.") % {"label": label})
    return value


def _require_manifest_string(
    value,
    field,
    *,
    required=False,
    min_length=None,
    max_length=None,
):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(_("Import field %(field)s must be text.") % {"field": field})
    if len(value.encode("utf-8")) > BULK_IMPORT_MAX_STRING_FIELD_BYTES:
        raise ValueError(_("Import field %(field)s is too large.") % {"field": field})
    value = value.strip() if field in {"title", "pending_update_title", "review_notes", "filename"} else value
    comparable_value = value.strip()
    if required and not comparable_value:
        raise ValueError(_("Import field %(field)s is required.") % {"field": field})
    if min_length is not None and comparable_value and len(comparable_value) < min_length:
        raise ValueError(
            _("Import field %(field)s must contain at least %(limit)s characters.")
            % {"field": field, "limit": min_length}
        )
    if max_length is not None and len(value) > max_length:
        raise ValueError(
            _("Import field %(field)s cannot exceed %(limit)s characters.")
            % {"field": field, "limit": max_length}
        )
    return value


def _get_strict_import_keyword_value(item, *names):
    """Accept only JSON text or a list of text for keyword aliases."""
    for name in names:
        if name not in item or item.get(name) in (None, ""):
            continue
        value = item.get(name)
        if isinstance(value, str):
            return normalize_import_keywords(value)
        if isinstance(value, list):
            if len(value) > 100 or any(
                not isinstance(entry, str) or len(entry) > 500
                for entry in value
            ):
                raise ValueError(_("Import field %(field)s contains invalid keywords.") % {"field": name})
            return normalize_import_keywords(value)
        raise ValueError(_("Import field %(field)s must be text or a list of text.") % {"field": name})
    return ""


def _require_manifest_integer(
    value,
    field,
    *,
    required=False,
    minimum=None,
    maximum=None,
):
    if value is None:
        if required:
            raise ValueError(_("Import field %(field)s is required.") % {"field": field})
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(_("Import field %(field)s must be an integer.") % {"field": field})
    if minimum is not None and value < minimum:
        raise ValueError(
            _("Import field %(field)s must be at least %(minimum)s.")
            % {"field": field, "minimum": minimum}
        )
    if maximum is not None and value > maximum:
        raise ValueError(
            _("Import field %(field)s cannot exceed %(maximum)s.")
            % {"field": field, "maximum": maximum}
        )
    return value


def _validate_import_image_list(value, field):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(_("Import field %(field)s must be a list.") % {"field": field})
    limit = get_article_image_upload_limit()
    if len(value) > max(limit, 0):
        raise ValueError(article_image_limit_error_message(len(value), limit))
    result = []
    for entry in value:
        if not isinstance(entry, str) or len(entry) > 255:
            raise ValueError(_("Import field %(field)s contains an invalid filename.") % {"field": field})
        filename = safe_uploaded_filename(entry)
        if not filename or filename != entry.strip():
            raise ValueError(_("Import field %(field)s contains an unsafe filename.") % {"field": field})
        if filename not in result:
            result.append(filename)
    return result


def _validate_manifest_upload_list(value):
    """Validate the standard manifest's declared upload inventory."""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(_("Import manifest uploads must be a list."))
    if len(value) > BULK_IMPORT_MAX_TOTAL_MEMBERS:
        raise ValueError(_("Import manifest declares too many uploaded files."))
    result = []
    for entry in value:
        if not isinstance(entry, str) or len(entry) > 255:
            raise ValueError(_("Import manifest uploads contains an invalid filename."))
        filename = safe_uploaded_filename(entry)
        if not filename or filename != entry.strip():
            raise ValueError(_("Import manifest uploads contains an unsafe filename."))
        if filename not in result:
            result.append(filename)
    return result


def _validate_review_history(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(_("Review comment history must be a list."))
    if len(value) > ARTICLE_REVIEW_HISTORY_MAX_ENTRIES:
        raise ValueError(
            _("Review comment history cannot contain more than %(limit)s entries.")
            % {"limit": ARTICLE_REVIEW_HISTORY_MAX_ENTRIES}
        )
    allowed_keys = {"note", "action", "status", "reviewer", "reviewer_id", "created_at"}
    cleaned = []
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, dict) or set(entry) - allowed_keys:
            raise ValueError(
                _("Review comment history entry %(index)s has an invalid structure.")
                % {"index": index}
            )
        note = _require_manifest_string(entry.get("note"), "review_notes_history.note", required=True, max_length=ARTICLE_REVIEW_NOTES_MAX_LENGTH)
        clean_entry = {"note": note}
        for key, limit in (("action", 40), ("status", 20), ("reviewer", 255), ("created_at", 64)):
            if key in entry and entry.get(key) is not None:
                clean_entry[key] = _require_manifest_string(entry.get(key), f"review_notes_history.{key}", max_length=limit)
        reviewer_id = entry.get("reviewer_id")
        if reviewer_id is not None:
            if not isinstance(reviewer_id, int) or isinstance(reviewer_id, bool) or reviewer_id < 1:
                raise ValueError(
                    _("Review comment history entry %(index)s has an invalid reviewer ID.")
                    % {"index": index}
                )
            clean_entry["reviewer_id"] = reviewer_id
        cleaned.append(clean_entry)
    return cleaned


def validate_bulk_manifest(manifest):
    """Validate and normalize a standard export manifest before file copying."""
    manifest = _require_manifest_mapping(manifest, _("Import manifest"))
    unknown_manifest_fields = set(manifest) - _BULK_STANDARD_MANIFEST_ALLOWED_FIELDS
    if unknown_manifest_fields:
        raise ValueError(
            _("Import manifest contains unsupported fields: %(fields)s")
            % {"fields": ", ".join(sorted(unknown_manifest_fields))}
        )
    if manifest.get("format") != "djopenkb-bulk-export-v1":
        raise ValueError(_("Unsupported import manifest format."))
    _require_manifest_string(manifest.get("exported_at"), "exported_at", max_length=64)
    _validate_manifest_upload_list(manifest.get("uploads"))
    articles = manifest.get("articles")
    if not isinstance(articles, list):
        raise ValueError(_("Import manifest articles must be a list."))
    if not articles or len(articles) > BULK_IMPORT_MAX_ARTICLES:
        raise ValueError(
            _("Import manifest must contain between 1 and %(limit)s articles.")
            % {"limit": BULK_IMPORT_MAX_ARTICLES}
        )
    declared_count = _require_manifest_integer(
        manifest.get("article_count"),
        "article_count",
        minimum=1,
        maximum=BULK_IMPORT_MAX_ARTICLES,
    )
    if declared_count is not None and declared_count != len(articles):
        raise ValueError(_("Import manifest article_count does not match the articles list."))

    part_number = _require_manifest_integer(
        manifest.get("part_number"),
        "part_number",
        minimum=1,
        maximum=BULK_IMPORT_MAX_PART_ARCHIVES,
    )
    part_count = _require_manifest_integer(
        manifest.get("part_count"),
        "part_count",
        minimum=1,
        maximum=BULK_IMPORT_MAX_PART_ARCHIVES,
    )
    if (part_number is None) != (part_count is None):
        raise ValueError(_("Import manifest part_number and part_count must be provided together."))
    if part_number is not None and part_number > part_count:
        raise ValueError(_("Import manifest part_number cannot exceed part_count."))

    cleaned_articles = []
    status_values = {value for value, _label in SuggestedArticle.Status.choices}
    update_values = {value for value, _label in SuggestedArticle.UpdateStatus.choices}
    visibility_values = {value for value, _label in SuggestedArticle.Visibility.choices}

    for index, raw_item in enumerate(articles, start=1):
        item = _require_manifest_mapping(raw_item, _("Article %(index)s") % {"index": index})
        unknown = set(item) - _BULK_ARTICLE_ALLOWED_FIELDS
        if unknown:
            raise ValueError(
                _("Article %(index)s contains unsupported fields: %(fields)s")
                % {"index": index, "fields": ", ".join(sorted(unknown))}
            )

        title = _require_manifest_string(
            item.get("title"), "title", required=True, min_length=5, max_length=200
        )
        body = _require_manifest_string(item.get("body"), "body", required=True, min_length=5)
        status = _require_manifest_string(item.get("status") or SuggestedArticle.Status.PUBLISHED, "status", required=True, max_length=20)
        visibility = _require_manifest_string(item.get("visibility") or SuggestedArticle.Visibility.PUBLIC, "visibility", required=True, max_length=20)
        update_status = _require_manifest_string(item.get("update_status") or SuggestedArticle.UpdateStatus.NONE, "update_status", required=True, max_length=20)
        if status not in status_values or status == SuggestedArticle.Status.DELETE_QUEUED:
            raise ValueError(_("Article %(index)s has an unsupported workflow status.") % {"index": index})
        if visibility not in visibility_values:
            raise ValueError(_("Article %(index)s has an invalid visibility.") % {"index": index})
        if update_status not in update_values:
            raise ValueError(_("Article %(index)s has an invalid pending-update status.") % {"index": index})

        pending_title = _require_manifest_string(
            item.get("pending_update_title"),
            "pending_update_title",
            min_length=5,
            max_length=200,
        )
        pending_body = _require_manifest_string(item.get("pending_update_body"), "pending_update_body")
        pending_keywords = _get_strict_import_keyword_value(
            item,
            "pending_update_keywords",
            "pending_update_keyword",
            "pending_update_keyword_list",
            "pending_update_tags",
        )
        pending_assets = _validate_import_image_list(item.get("pending_update_image_assets"), "pending_update_image_assets")
        review_notes = _require_manifest_string(
            item.get("review_notes"),
            "review_notes",
            max_length=ARTICLE_REVIEW_NOTES_MAX_LENGTH,
        )
        has_pending_update_content = any((pending_title, pending_body.strip(), pending_keywords, pending_assets))
        if status != SuggestedArticle.Status.PUBLISHED:
            if update_status != SuggestedArticle.UpdateStatus.NONE or has_pending_update_content:
                raise ValueError(_("Only published articles may contain an unpublished or pending update."))
        if (update_status != SuggestedArticle.UpdateStatus.NONE or has_pending_update_content) and (not pending_title or not pending_body.strip()):
            raise ValueError(_("An unpublished or pending article update requires both a title and article body."))
        if status == SuggestedArticle.Status.FAILED and not review_notes:
            raise ValueError(_("A Pending failed article requires review comments."))
        if update_status == SuggestedArticle.UpdateStatus.FAILED and not review_notes:
            raise ValueError(_("A failed pending article update requires review comments."))

        filename = _require_manifest_string(item.get("filename"), "filename", max_length=255)
        if filename and (
            safe_uploaded_filename(filename) != filename
            or Path(filename).suffix.lower() != ".md"
        ):
            raise ValueError(_("Import field filename must be a safe Markdown filename."))

        # Validate metadata fields even though ownership and timestamps are
        # intentionally rebuilt locally rather than trusted from the archive.
        for field_name, maximum in (
            ("raw_path", 500),
            ("wiki_path", 500),
            ("created_at", 64),
            ("updated_at", 64),
            ("published_at", 64),
            ("author_username", 150),
            ("author_email", 254),
        ):
            _require_manifest_string(item.get(field_name), field_name, max_length=maximum)

        cleaned_articles.append({
            "title": title,
            "body": body,
            "keywords": _get_strict_import_keyword_value(item, "keywords", "keyword", "keyword_list", "tags"),
            "status": status,
            "visibility": visibility,
            "filename": filename,
            "image_assets": _validate_import_image_list(item.get("image_assets"), "image_assets"),
            "update_status": update_status,
            "pending_update_title": pending_title,
            "pending_update_body": pending_body,
            "pending_update_keywords": pending_keywords,
            "pending_update_image_assets": pending_assets,
            "review_notes": review_notes,
            "review_notes_history": _validate_review_history(item.get("review_notes_history")),
        })
    return cleaned_articles


def validate_split_manifest(manifest, safe_names):
    manifest = _require_manifest_mapping(manifest, _("Split import manifest"))
    unknown_manifest_fields = set(manifest) - _BULK_SPLIT_MANIFEST_ALLOWED_FIELDS
    if unknown_manifest_fields:
        raise ValueError(
            _("Split import manifest contains unsupported fields: %(fields)s")
            % {"fields": ", ".join(sorted(unknown_manifest_fields))}
        )
    if manifest.get("format") != "djopenkb-bulk-export-split-v1":
        raise ValueError(_("Unsupported split import manifest format."))
    _require_manifest_string(manifest.get("exported_at"), "exported_at", max_length=64)
    _require_manifest_integer(
        manifest.get("part_size_target_bytes"),
        "part_size_target_bytes",
        minimum=1,
        maximum=BULK_IMPORT_MAX_UPLOAD_BYTES,
    )
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError(_("Split import manifest parts must be a non-empty list."))
    if len(parts) > BULK_IMPORT_MAX_PART_ARCHIVES:
        raise ValueError(_("Split import package contains too many part archives. Maximum allowed is 20 parts."))
    declared_count = _require_manifest_integer(
        manifest.get("part_count"),
        "part_count",
        minimum=1,
        maximum=BULK_IMPORT_MAX_PART_ARCHIVES,
    )
    if declared_count is not None and declared_count != len(parts):
        raise ValueError(_("Split import manifest part_count does not match the parts list."))
    part_names = []
    for index, part in enumerate(parts, start=1):
        part = _require_manifest_mapping(part, _("Split part %(index)s") % {"index": index})
        unknown_part_fields = set(part) - _BULK_SPLIT_PART_ALLOWED_FIELDS
        if unknown_part_fields:
            raise ValueError(
                _("Split part %(index)s contains unsupported fields: %(fields)s")
                % {"index": index, "fields": ", ".join(sorted(unknown_part_fields))}
            )
        filename = _require_manifest_string(part.get("filename"), "parts.filename", required=True, max_length=500)
        _require_manifest_integer(
            part.get("size_bytes"),
            "parts.size_bytes",
            minimum=0,
            maximum=BULK_IMPORT_MAX_UPLOAD_BYTES,
        )
        _require_manifest_integer(
            part.get("article_count"),
            "parts.article_count",
            minimum=0,
            maximum=BULK_IMPORT_MAX_ARTICLES,
        )
        _require_manifest_integer(
            part.get("upload_count"),
            "parts.upload_count",
            minimum=0,
            maximum=BULK_IMPORT_MAX_TOTAL_MEMBERS,
        )
        safe_name = safe_zip_member_name(filename)
        if not safe_name or safe_name != filename or not safe_name.lower().endswith(".zip"):
            raise ValueError(_("Split import manifest contains an unsafe part filename."))
        if safe_name not in safe_names:
            raise ValueError(_("Split import part is missing from the package: %(filename)s") % {"filename": safe_name})
        if safe_name in part_names:
            raise ValueError(_("Split import manifest contains a duplicate part filename."))
        part_names.append(safe_name)
    return sorted(part_names)


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_("Import manifest contains a duplicate JSON field: %(field)s") % {"field": key})
        result[key] = value
    return result


def _read_bulk_manifest(archive, manifest_name):
    if not manifest_name:
        return None
    with archive.open(manifest_name, "r") as manifest_file:
        data = manifest_file.read(BULK_IMPORT_MAX_MANIFEST_BYTES + 1)
    if len(data) > BULK_IMPORT_MAX_MANIFEST_BYTES:
        raise ValueError(_("Import manifest is too large. Maximum allowed size is 5 MB."))
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except UnicodeDecodeError as error:
        raise ValueError(_("Import manifest must use valid UTF-8 text.")) from error
    except json.JSONDecodeError as error:
        raise ValueError(_("Import manifest contains invalid JSON.")) from error


def _preflight_bulk_import_archive(uploaded_zip, *, depth=0, budget=None):
    """Validate the complete nested ZIP tree before importing any article.

    The budget is shared across an outer split package and all part archives so
    individually valid parts cannot collectively consume unbounded resources.
    """
    if depth > BULK_IMPORT_MAX_NESTING_DEPTH:
        raise ValueError(_("Nested split import packages are not allowed."))

    if budget is None:
        budget = {
            "total_uncompressed": 0,
            "total_members": 0,
            "archive_count": 0,
        }

    with zipfile.ZipFile(uploaded_zip) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        archive_uncompressed = sum(max(0, int(item.file_size or 0)) for item in members)
        if archive_uncompressed > BULK_IMPORT_MAX_UNCOMPRESSED_BYTES:
            raise ValueError(_("Import zip is too large after extraction. Maximum allowed uncompressed size is 200 MB."))

        budget["archive_count"] += 1
        budget["total_members"] += len(members)
        budget["total_uncompressed"] += archive_uncompressed

        if budget["archive_count"] > BULK_IMPORT_MAX_PART_ARCHIVES + 1:
            raise ValueError(_("Split import package contains too many part archives. Maximum allowed is 20 parts."))
        if budget["total_members"] > BULK_IMPORT_MAX_TOTAL_MEMBERS:
            raise ValueError(_("Import package contains too many files. Maximum allowed across all parts is 5000 files."))
        if budget["total_uncompressed"] > BULK_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError(_("Import package is too large across all parts. Maximum cumulative uncompressed size is 500 MB."))

        safe_names = {
            safe_zip_member_name(item.filename): item.filename
            for item in members
            if safe_zip_member_name(item.filename)
        }
        manifest_name = safe_names.get("manifest.json")
        manifest = _read_bulk_manifest(archive, manifest_name)

        if manifest and manifest.get("format") == "djopenkb-bulk-export-split-v1":
            if depth >= BULK_IMPORT_MAX_NESTING_DEPTH:
                raise ValueError(_("Nested split import packages are not allowed."))

            part_names = validate_split_manifest(manifest, safe_names)

            for part_name in part_names:
                with archive.open(safe_names[part_name], "r") as part_file:
                    part_bytes = part_file.read(BULK_IMPORT_MAX_UPLOAD_BYTES + 1)
                if len(part_bytes) > BULK_IMPORT_MAX_UPLOAD_BYTES:
                    raise ValueError(
                        _("Split import part %(part_name)s is larger than 100 MB.")
                        % {"part_name": part_name}
                    )
                _preflight_bulk_import_archive(
                    io.BytesIO(part_bytes),
                    depth=depth + 1,
                    budget=budget,
                )

    return budget


def build_bulk_export_payload(articles=None):
    """Build the JSON manifest and file list for article bulk export.

    The payload includes live article fields and any pending-update fields so an
    export/import keeps Django DB state and OpenKB Markdown files in sync.
    """
    article_rows = []
    referenced_uploads = set()

    if articles is None:
        articles = SuggestedArticle.objects.select_related("owner").order_by("created_at", "id")

    for article in articles:
        live_assets = sorted(set((article.image_assets or []) + extract_article_image_filenames(article.body)))
        pending_assets = sorted(set((article.pending_update_image_assets or []) + extract_article_image_filenames(article.pending_update_body)))
        referenced_uploads.update(live_assets)
        referenced_uploads.update(pending_assets)

        article_rows.append({
            "title": article.title,
            "body": article.body,
            "keywords": article.keywords,
            "visibility": article.visibility,
            "keyword_list": article.keyword_list,
            "tags": article.keyword_list,
            "status": article.status,
            "filename": article.filename,
            "raw_path": article.raw_path,
            "wiki_path": article.wiki_path,
            "image_assets": live_assets,
            "update_status": getattr(article, "update_status", SuggestedArticle.UpdateStatus.NONE),
            "pending_update_title": getattr(article, "pending_update_title", "") or "",
            "pending_update_body": getattr(article, "pending_update_body", "") or "",
            "pending_update_keywords": getattr(article, "pending_update_keywords", "") or "",
            "pending_update_keyword_list": [item.strip() for item in (getattr(article, "pending_update_keywords", "") or "").split(",") if item.strip()],
            "pending_update_image_assets": pending_assets,
            "review_notes": getattr(article, "review_notes", "") or "",
            "review_notes_history": getattr(article, "review_notes_history", []) or [],
            "created_at": article.created_at.isoformat() if article.created_at else "",
            "updated_at": article.updated_at.isoformat() if article.updated_at else "",
            "published_at": article.published_at.isoformat() if getattr(article, "published_at", None) else "",
            "author_username": article.author_username,
            "author_email": article.author_email,
        })

    return {
        "format": "djopenkb-bulk-export-v1",
        "exported_at": timezone.now().isoformat(),
        "article_count": len(article_rows),
        "articles": article_rows,
        "uploads": sorted(referenced_uploads),
    }


def _upload_file_size(filename):
    filename = safe_uploaded_filename(filename)
    if not filename:
        return 0
    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / filename).resolve()
    try:
        file_path.relative_to(upload_dir)
    except ValueError:
        return 0
    try:
        return file_path.stat().st_size if file_path.exists() and file_path.is_file() else 0
    except OSError:
        return 0


def _article_export_size_estimate(article):
    live_assets = set((article.image_assets or []) + extract_article_image_filenames(article.body))
    pending_assets = set((article.pending_update_image_assets or []) + extract_article_image_filenames(article.pending_update_body))
    upload_size = sum(_upload_file_size(filename) for filename in live_assets | pending_assets)
    markdown_size = len(build_article_markdown(article).encode("utf-8"))
    pending_text_size = len((getattr(article, "pending_update_body", "") or "").encode("utf-8"))
    # Add a little overhead for manifest JSON and zip metadata.
    return upload_size + markdown_size + pending_text_size + 4096


def split_articles_for_bulk_export(max_part_size_bytes=BULK_EXPORT_PART_SIZE_BYTES):
    """Return article batches that should produce importable zip parts below the target size."""
    batches = []
    current_batch = []
    current_size = 0

    for article in SuggestedArticle.objects.select_related("owner").order_by("created_at", "id"):
        article_size = _article_export_size_estimate(article)
        if current_batch and current_size + article_size > max_part_size_bytes:
            batches.append(current_batch)
            current_batch = []
            current_size = 0

        current_batch.append(article)
        current_size += article_size

    if current_batch:
        batches.append(current_batch)

    return batches


def build_single_bulk_export_zip(articles=None, part_number=None, part_count=None):
    """Build one importable Knowledge Repository export zip and return its bytes plus manifest."""
    manifest = build_bulk_export_payload(articles=articles)
    if part_number and part_count:
        manifest["part_number"] = part_number
        manifest["part_count"] = part_count

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr(
            "README.txt",
            (
                "Knowledge Repository bulk article export.\n"
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
    return buffer.getvalue(), manifest


def build_bulk_export_download(force_split=False, max_part_size_bytes=BULK_EXPORT_PART_SIZE_BYTES):
    """Build either one importable zip or a package containing importable part zips.

    If the export grows beyond the part size target, the returned outer zip contains
    parts/djopenkb-export-partXXX-of-YYY.zip files. Each part can be imported
    separately and stays below the import upload limit where possible.
    """
    batches = split_articles_for_bulk_export(max_part_size_bytes=max_part_size_bytes)
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M%S")

    if not force_split and len(batches) <= 1:
        data, manifest = build_single_bulk_export_zip()
        return data, f"djopenkb-export-{timestamp}.zip", "application/zip", manifest, False

    outer_manifest = {
        "format": "djopenkb-bulk-export-split-v1",
        "exported_at": timezone.now().isoformat(),
        "part_count": len(batches),
        "part_size_target_bytes": max_part_size_bytes,
        "parts": [],
    }

    outer_buffer = io.BytesIO()
    with zipfile.ZipFile(outer_buffer, "w", compression=zipfile.ZIP_DEFLATED) as outer:
        part_count = len(batches)
        for index, batch in enumerate(batches, start=1):
            part_bytes, part_manifest = build_single_bulk_export_zip(batch, part_number=index, part_count=part_count)
            part_filename = f"parts/djopenkb-export-{timestamp}-part{index:03d}-of-{part_count:03d}.zip"
            outer.writestr(part_filename, part_bytes)
            outer_manifest["parts"].append({
                "filename": part_filename,
                "size_bytes": len(part_bytes),
                "article_count": part_manifest.get("article_count", 0),
                "upload_count": len(part_manifest.get("uploads", [])),
            })

        outer.writestr("manifest.json", json.dumps(outer_manifest, indent=2, ensure_ascii=False))
        outer.writestr(
            "README.txt",
            (
                "Knowledge Repository split bulk export package.\n\n"
                "Extract this package first, then import each zip inside the parts/ folder.\n"
                "Each part zip is a normal Knowledge Repository import file. Import part001, then part002, and continue in order.\n"
            ),
        )

    outer_buffer.seek(0)
    return outer_buffer.getvalue(), f"djopenkb-export-{timestamp}-split-package.zip", "application/zip", outer_manifest, True


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

        if Path(original_filename).suffix.lower() not in ALLOWED_ARTICLE_IMAGE_EXTENSIONS:
            continue

        with zip_file.open(member_name, "r") as source:
            data = source.read(MAX_ARTICLE_IMAGE_SIZE_BYTES + 1)

        if len(data) > MAX_ARTICLE_IMAGE_SIZE_BYTES:
            continue

        from django.core.files.uploadedfile import SimpleUploadedFile
        temp_upload = SimpleUploadedFile(original_filename, data)

        try:
            image_info = validate_article_image_upload(temp_upload)
        except ValidationError:
            continue

        # Rename using the verified image type rather than trusting the zip filename.
        new_filename = make_unique_upload_filename(Path(original_filename).with_suffix(image_info["extension"]).name)
        target_path = (upload_dir / new_filename).resolve()
        try:
            target_path.relative_to(upload_dir.resolve())
        except ValueError:
            continue

        target_path.write_bytes(data)
        filename_map[original_filename] = new_filename

    return filename_map


def imported_payload_image_filenames(item):
    """Return safe source upload names referenced by one import payload."""
    referenced = []
    for text in (
        item.get("body") or "",
        item.get("pending_update_body") or "",
    ):
        for filename in extract_article_image_filenames(text):
            safe_name = safe_uploaded_filename(filename)
            if safe_name and safe_name not in referenced:
                referenced.append(safe_name)

    for field_name in ("image_assets", "pending_update_image_assets"):
        for filename in item.get(field_name) or []:
            safe_name = safe_uploaded_filename(filename)
            if safe_name and safe_name not in referenced:
                referenced.append(safe_name)

    return referenced


def imported_upload_exists(filename):
    """Return True when an already-managed upload can satisfy an import link."""
    safe_name = safe_uploaded_filename(filename)
    if not safe_name:
        return False
    upload_dir = get_openkb_uploads_dir().resolve()
    file_path = (upload_dir / safe_name).resolve()
    try:
        file_path.relative_to(upload_dir)
    except ValueError:
        return False
    return file_path.exists() and file_path.is_file()


def cleanup_unreferenced_import_uploads(filename_map, retained_filenames, errors=None):
    """Remove newly copied import images that no successful article references."""
    retained = {safe_uploaded_filename(name) for name in retained_filenames or []}
    retained.discard("")

    for new_filename in sorted(set(filename_map.values()) - retained):
        if image_is_used_by_other_article(new_filename):
            continue
        try:
            delete_uploaded_image_file(new_filename)
        except OSError as error:
            if errors is not None:
                errors.append(
                    _("Could not remove an unreferenced imported image: %(filename)s (%(error)s)")
                    % {"filename": new_filename, "error": error}
                )


def import_articles_from_zip(uploaded_zip, owner, *, _depth=0, _preflight_complete=False):
    """Import articles and only the uploaded images retained by those articles.

    All imported articles are assigned to the admin user performing the import.
    Normal single-part export zips and extracted split-export part zips are both
    supported. If an outer split package is uploaded, the importer will try to
    import the nested part zips in order.
    """
    if not _preflight_complete:
        _preflight_bulk_import_archive(uploaded_zip)
        try:
            uploaded_zip.seek(0)
        except Exception:
            pass

    if _depth > BULK_IMPORT_MAX_NESTING_DEPTH:
        raise ValueError(_("Nested split import packages are not allowed."))

    imported_count = 0
    errors = []

    with zipfile.ZipFile(uploaded_zip) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        safe_names = {
            safe_zip_member_name(item.filename): item.filename
            for item in members
            if safe_zip_member_name(item.filename)
        }

        # Hard safety limits for admin imports.
        total_uncompressed = sum(item.file_size for item in members)
        if total_uncompressed > BULK_IMPORT_MAX_UNCOMPRESSED_BYTES:
            raise ValueError(_("Import zip is too large after extraction. Maximum allowed uncompressed size is 200 MB."))

        manifest_name = safe_names.get("manifest.json")
        manifest = _read_bulk_manifest(archive, manifest_name)

        if manifest and manifest.get("format") == "djopenkb-bulk-export-split-v1":
            if _depth >= BULK_IMPORT_MAX_NESTING_DEPTH:
                raise ValueError(_("Nested split import packages are not allowed."))

            part_names = validate_split_manifest(manifest, safe_names)

            for part_name in part_names:
                with archive.open(safe_names[part_name], "r") as part_file:
                    part_bytes = part_file.read(BULK_IMPORT_MAX_UPLOAD_BYTES + 1)
                if len(part_bytes) > BULK_IMPORT_MAX_UPLOAD_BYTES:
                    errors.append(
                        _("Skipped %(part_name)s: part is larger than 100 MB. Extract and split it again before import.")
                        % {"part_name": part_name}
                    )
                    continue
                part_imported, part_errors = import_articles_from_zip(
                    io.BytesIO(part_bytes),
                    owner=owner,
                    _depth=_depth + 1,
                    _preflight_complete=True,
                )
                imported_count += part_imported
                errors.extend([f"{part_name}: {error}" for error in part_errors])

            return imported_count, errors

        article_payloads = []

        if manifest:
            article_payloads = validate_bulk_manifest(manifest)
        else:
            markdown_names = [
                original_name
                for safe_name, original_name in safe_names.items()
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
                    "visibility": SuggestedArticle.Visibility.PUBLIC,
                    "filename": Path(safe_name).name,
                    "image_assets": extract_article_image_filenames(body),
                })

        if not article_payloads:
            raise ValueError(_("No articles found in the zip. Include manifest.json or Markdown files."))

        # Copy only upload members that are linked by at least one article
        # payload. Files packaged in the ZIP but not referenced are ignored.
        referenced_source_uploads = {
            filename
            for item in article_payloads
            for filename in imported_payload_image_filenames(item)
        }
        available_upload_members = {
            safe_uploaded_filename(safe_name): original_name
            for safe_name, original_name in safe_names.items()
            if safe_name.startswith("uploads/") and safe_uploaded_filename(safe_name)
        }
        upload_members = [
            member_name
            for original_name, member_name in available_upload_members.items()
            if original_name in referenced_source_uploads
        ]
        filename_map = copy_imported_uploads_from_zip(archive, upload_members)
        retained_import_uploads = set()
        seen_import_titles = set()

        try:
            for item in article_payloads:
                title = (item.get("title") or "").strip()
                source_image_refs = imported_payload_image_filenames(item)
                missing_image_refs = [
                    filename
                    for filename in source_image_refs
                    if filename not in filename_map
                    and (
                        filename in available_upload_members
                        or not imported_upload_exists(filename)
                    )
                ]
                if missing_image_refs:
                    errors.append(
                        _("Skipped %(title)s because linked image files were missing or invalid: %(filenames)s")
                        % {
                            "title": title,
                            "filenames": ", ".join(sorted(missing_image_refs)),
                        }
                    )
                    continue

                body = rewrite_uploaded_file_references(item.get("body") or "", filename_map)
                keywords = normalize_import_keywords(item.get("keywords"))
                status = item.get("status") or SuggestedArticle.Status.PUBLISHED
                visibility = item.get("visibility") or SuggestedArticle.Visibility.PUBLIC
                update_status = item.get("update_status") or SuggestedArticle.UpdateStatus.NONE
                pending_update_title = (item.get("pending_update_title") or "").strip()
                pending_update_body = rewrite_uploaded_file_references(item.get("pending_update_body") or "", filename_map)
                pending_update_keywords = normalize_import_keywords(item.get("pending_update_keywords"))
                review_notes = (item.get("review_notes") or "").strip()
                review_notes_history = item.get("review_notes_history") or []
                imported_pending_assets = [
                    filename_map.get(safe_uploaded_filename(filename), safe_uploaded_filename(filename))
                    for filename in (item.get("pending_update_image_assets") or [])
                    if safe_uploaded_filename(filename)
                ]

                if len(title) < 5 or len(body.strip()) < 5:
                    errors.append(
                        _("Skipped %(title)s: article title and body must each contain at least 5 characters.")
                        % {"title": title or _("Untitled article")}
                    )
                    continue

                normalized_title = normalize_article_title(title)
                if normalized_title in seen_import_titles:
                    errors.append(_("Skipped duplicate title inside import zip: %(title)s") % {"title": title})
                    continue
                seen_import_titles.add(normalized_title)

                duplicate_article = find_duplicate_article_by_title(title)
                if duplicate_article:
                    errors.append(_("Skipped duplicate title already in OpenKB: %(title)s") % {"title": title})
                    continue

                try:
                    body = validate_article_body(body)
                    pending_update_body = validate_article_body(pending_update_body)
                    keywords = validate_article_keywords(keywords)
                    pending_update_keywords = validate_article_keywords(
                        pending_update_keywords
                    )
                    validate_article_video_links_for_anonymous_access(body)
                    validate_article_video_links_for_anonymous_access(pending_update_body)
                    validate_article_image_count(extract_article_image_filenames(body))
                    validate_article_image_count(
                        sorted(set(imported_pending_assets + extract_article_image_filenames(pending_update_body)))
                    )
                except ValidationError as error:
                    message = error.messages[0] if getattr(error, "messages", None) else str(error)
                    errors.append(f"{title}: {message}")
                    continue

                filename = make_unique_article_filename(title, item.get("filename") or "")
                article = None

                try:
                    with transaction.atomic():
                        now = timezone.now()
                        article = SuggestedArticle(
                            owner=owner,
                            title=title,
                            body=body,
                            keywords=keywords,
                            visibility=visibility,
                            filename=filename,
                            wiki_path=f"internal/sources/{filename}" if visibility == SuggestedArticle.Visibility.INTERNAL else f"sources/{filename}",
                            raw_path=f"raw/internal/{filename}" if visibility == SuggestedArticle.Visibility.INTERNAL else f"raw/{filename}",
                            status=status,
                            approved_by=owner if status == SuggestedArticle.Status.PUBLISHED else None,
                            approved_at=now if status == SuggestedArticle.Status.PUBLISHED else None,
                            image_assets=extract_article_image_filenames(body),
                            update_status=update_status,
                            update_submitted_at=now if update_status == SuggestedArticle.UpdateStatus.PENDING else None,
                            update_reviewed_at=now if update_status == SuggestedArticle.UpdateStatus.FAILED else None,
                            pending_update_title=pending_update_title,
                            pending_update_body=pending_update_body,
                            pending_update_keywords=pending_update_keywords,
                            pending_update_image_assets=sorted(set(imported_pending_assets + extract_article_image_filenames(pending_update_body))),
                            review_notes=review_notes,
                            review_notes_history=review_notes_history,
                        )
                        article.full_clean()
                        article.save()
                        write_article_files(article)
                        sync_article_image_assets(article, old_assets=[])

                    retained_import_uploads.update(article.image_assets or [])
                    retained_import_uploads.update(article.pending_update_image_assets or [])
                    imported_count += 1
                except IntegrityError:
                    if article is not None:
                        try:
                            delete_article_files(article)
                        except Exception:
                            pass
                    errors.append(_("Skipped duplicate title already in OpenKB: %(title)s") % {"title": title})
                except Exception as error:
                    if article is not None:
                        try:
                            delete_article_files(article)
                        except Exception:
                            pass
                    errors.append(f"{title}: {error}")
        finally:
            cleanup_unreferenced_import_uploads(
                filename_map,
                retained_import_uploads,
                errors=errors,
            )

    return imported_count, errors


def get_article_image_cards_from_filenames(image_assets, existing=True):
    return [
        {
            "filename": filename,
            "url": article_image_url(filename),
            "markdown": article_image_markdown(filename),
            "existing": bool(existing),
        }
        for filename in (image_assets or [])
        if safe_uploaded_filename(filename)
    ]


def get_article_image_cards(article, image_assets=None):
    assets = image_assets
    if assets is None:
        assets = article.image_assets or extract_article_image_filenames(article.body)

    return get_article_image_cards_from_filenames(assets, existing=True)


