import json
import re
from datetime import datetime
from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render


def slugify_title(title):
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


def get_article_folders():
    articles_dir = settings.OPENKB_ARTICLES_DIR
    articles = []

    if not articles_dir.exists():
        return articles

    for folder in articles_dir.iterdir():
        if not folder.is_dir():
            continue

        metadata_path = folder / "metadata.json"
        article_path = folder / "article.md"

        if not metadata_path.exists() or not article_path.exists():
            continue

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if metadata.get("status") != "published":
            continue

        articles.append({
            "title": metadata.get("title", folder.name),
            "type": metadata.get("type", "MD").upper(),
            "date": metadata.get("created_at", ""),
            "views": metadata.get("view_count", 0),
            "url": f"/article/{folder.name}/",
            "slug": folder.name,
        })

    articles.sort(key=lambda item: item.get("date", ""), reverse=True)
    return articles


def home(request):
    articles = get_article_folders()

    return render(request, "index.html", {
        "articles": articles,
    })


def suggest(request):
    if request.method == "GET":
        return render(request, "suggest.html")

    title = request.POST.get("frm_kb_title", "").strip()
    body = request.POST.get("frm_kb_body", "").strip()
    keywords_raw = request.POST.get("frm_kb_keywords", "").strip()

    if len(title) < 5 or len(body) < 5:
        return render(request, "suggest.html", {
            "error": "Article title and body must be at least 5 characters."
        })

    settings.OPENKB_ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    timestamp_slug = datetime.now().strftime("%Y%m%d-%H%M%S")
    title_slug = slugify_title(title)
    article_slug = f"{timestamp_slug}-{title_slug}"

    article_folder = settings.OPENKB_ARTICLES_DIR / article_slug
    attachments_folder = article_folder / "attachments"

    article_folder.mkdir(parents=True, exist_ok=True)
    attachments_folder.mkdir(parents=True, exist_ok=True)

    article_md_path = article_folder / "article.md"
    metadata_path = article_folder / "metadata.json"

    article_md_path.write_text(body, encoding="utf-8")

    keywords = [
        keyword.strip()
        for keyword in keywords_raw.split(",")
        if keyword.strip()
    ]

    metadata = {
        "title": title,
        "slug": article_slug,
        "type": "md",
        "main_file": "article.md",
        "keywords": keywords,
        "created_from": "suggest",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "published",
        "view_count": 0,
    }

    metadata_path.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8"
    )

    return render(request, "suggest.html", {
        "message": f"Article uploaded successfully: {title}"
    })


def article_detail(request, article_slug):
    article_folder = settings.OPENKB_ARTICLES_DIR / article_slug
    metadata_path = article_folder / "metadata.json"
    article_path = article_folder / "article.md"

    if not article_folder.exists() or not article_folder.is_dir():
        raise Http404("Article not found")

    if not metadata_path.exists() or not article_path.exists():
        raise Http404("Article content not found")

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        raise Http404("Invalid article metadata")

    raw_markdown = article_path.read_text(encoding="utf-8", errors="ignore")

    html_content = markdown.markdown(
        raw_markdown,
        extensions=["fenced_code", "tables"]
    )

    metadata["view_count"] = int(metadata.get("view_count", 0)) + 1
    metadata_path.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8"
    )

    return render(request, "article.html", {
        "title": metadata.get("title", article_slug),
        "content": html_content,
        "metadata": metadata,
    })