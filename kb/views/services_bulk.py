"""Helper functions split out from kb.views.services into kb.views.services_bulk.

This module is imported back by services.py so existing imports continue to work.
"""

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
    return value[:500]


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
    """Build one importable DjOpenKB export zip and return its bytes plus manifest."""
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
                "DjOpenKB split bulk export package.\n\n"
                "Extract this package first, then import each zip inside the parts/ folder.\n"
                "Each part zip is a normal DjOpenKB import file. Import part001, then part002, and continue in order.\n"
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


def import_articles_from_zip(uploaded_zip, owner):
    """Import articles and uploaded files from a DjOpenKB bulk export zip.

    All imported articles are assigned to the admin user performing the import.
    Normal single-part export zips and extracted split-export part zips are both
    supported. If an outer split package is uploaded, the importer will try to
    import the nested part zips in order.
    """
    imported_count = 0
    errors = []

    with zipfile.ZipFile(uploaded_zip) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        safe_names = {safe_zip_member_name(item.filename): item.filename for item in members if safe_zip_member_name(item.filename)}

        # Hard safety limits for admin imports.
        total_uncompressed = sum(item.file_size for item in members)
        if total_uncompressed > BULK_IMPORT_MAX_UNCOMPRESSED_BYTES:
            raise ValueError(_("Import zip is too large after extraction. Maximum allowed uncompressed size is 200 MB."))

        manifest_name = safe_names.get("manifest.json")
        manifest = None
        if manifest_name:
            with archive.open(manifest_name, "r") as manifest_file:
                manifest = json.loads(manifest_file.read().decode("utf-8"))

        if manifest and manifest.get("format") == "djopenkb-bulk-export-split-v1":
            part_names = [
                part.get("filename") for part in sorted(manifest.get("parts", []), key=lambda item: item.get("filename") or "")
                if part.get("filename") in safe_names
            ]
            if not part_names:
                raise ValueError("Split export package did not contain any importable part zip files.")

            for part_name in part_names:
                with archive.open(safe_names[part_name], "r") as part_file:
                    part_bytes = part_file.read(BULK_IMPORT_MAX_UPLOAD_BYTES + 1)
                if len(part_bytes) > BULK_IMPORT_MAX_UPLOAD_BYTES:
                    errors.append(_("Skipped %(part_name)s: part is larger than 100 MB. Extract and split it again before import.") % {"part_name": part_name})
                    continue
                part_imported, part_errors = import_articles_from_zip(io.BytesIO(part_bytes), owner=owner)
                imported_count += part_imported
                errors.extend([f"{part_name}: {error}" for error in part_errors])

            return imported_count, errors

        upload_members = [
            original_name for safe_name, original_name in safe_names.items()
            if safe_name.startswith("uploads/")
        ]
        filename_map = copy_imported_uploads_from_zip(archive, upload_members)

        article_payloads = []

        if manifest and manifest.get("format") == "djopenkb-bulk-export-v1":
            for item in manifest.get("articles", []):
                article_payloads.append({
                    "title": item.get("title") or "Imported article",
                    "body": item.get("body") or "",
                    "keywords": get_import_keyword_value(item, "keywords", "keyword", "keyword_list", "tags"),
                    "status": item.get("status") or SuggestedArticle.Status.PUBLISHED,
                    "filename": item.get("filename") or "",
                    "update_status": item.get("update_status") or SuggestedArticle.UpdateStatus.NONE,
                    "pending_update_title": item.get("pending_update_title") or "",
                    "pending_update_body": item.get("pending_update_body") or "",
                    "pending_update_keywords": get_import_keyword_value(item, "pending_update_keywords", "pending_update_keyword", "pending_update_keyword_list", "pending_update_tags"),
                    "pending_update_image_assets": item.get("pending_update_image_assets") or [],
                    "review_notes": item.get("review_notes") or "",
                    "review_notes_history": item.get("review_notes_history") or [],
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

        seen_import_titles = set()

        for item in article_payloads:
            title = (item.get("title") or "Imported article").strip()[:200]
            body = rewrite_uploaded_file_references(item.get("body") or "", filename_map)
            keywords = normalize_import_keywords(item.get("keywords"))
            status = item.get("status") or SuggestedArticle.Status.PUBLISHED
            update_status = item.get("update_status") or SuggestedArticle.UpdateStatus.NONE
            pending_update_title = (item.get("pending_update_title") or "").strip()[:200]
            pending_update_body = rewrite_uploaded_file_references(item.get("pending_update_body") or "", filename_map)
            pending_update_keywords = normalize_import_keywords(item.get("pending_update_keywords"))
            review_notes = (item.get("review_notes") or "").strip()
            review_notes_history = item.get("review_notes_history") or []
            imported_pending_assets = [
                filename_map.get(safe_uploaded_filename(filename), safe_uploaded_filename(filename))
                for filename in (item.get("pending_update_image_assets") or [])
                if safe_uploaded_filename(filename)
            ]

            if status not in dict(SuggestedArticle.Status.choices):
                status = SuggestedArticle.Status.PUBLISHED
            if update_status not in dict(SuggestedArticle.UpdateStatus.choices):
                update_status = SuggestedArticle.UpdateStatus.NONE

            normalized_title = normalize_article_title(title)
            if normalized_title in seen_import_titles:
                errors.append(_("Skipped duplicate title inside import zip: %(title)s") % {"title": title})
                continue
            seen_import_titles.add(normalized_title)

            duplicate_article = find_duplicate_article_by_title(title)
            if duplicate_article:
                errors.append(_("Skipped duplicate title already in OpenKB: %(title)s") % {"title": title})
                continue

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
                    update_status=update_status,
                    pending_update_title=pending_update_title,
                    pending_update_body=pending_update_body,
                    pending_update_keywords=pending_update_keywords,
                    pending_update_image_assets=sorted(set(imported_pending_assets + extract_article_image_filenames(pending_update_body))),
                    review_notes=review_notes,
                    review_notes_history=review_notes_history if isinstance(review_notes_history, list) else [],
                )
                write_article_files(article)
                sync_article_image_assets(article, old_assets=[])
                imported_count += 1
            except Exception as error:
                errors.append(f"{title}: {error}")

    return imported_count, errors


def get_article_image_cards(article, image_assets=None):
    assets = image_assets
    if assets is None:
        assets = article.image_assets or extract_article_image_filenames(article.body)

    return [
        {
            "filename": filename,
            "url": article_image_url(filename),
            "markdown": article_image_markdown(filename),
            "existing": True,
        }
        for filename in (assets or [])
    ]


