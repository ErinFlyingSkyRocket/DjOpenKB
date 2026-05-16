import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt


def slugify_title(title):
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


def init_openkb_storage():
    settings.OPENKB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.OPENKB_RAW_DIR.mkdir(parents=True, exist_ok=True)
    settings.OPENKB_WIKI_DIR.mkdir(parents=True, exist_ok=True)


def clean_wiki_title(file_path):
    return file_path.stem.replace("-", " ").replace("_", " ").title()


def get_openkb_wiki_articles():
    """Read OpenKB Markdown files from openkb-data/wiki, including raw content."""
    init_openkb_storage()

    wiki_dir = settings.OPENKB_WIKI_DIR
    articles = []
    ignored_names = {"AGENTS.md", "log.md", "index.md", "README.md"}

    if not wiki_dir.exists():
        return articles

    for file_path in wiki_dir.rglob("*.md"):
        if file_path.name in ignored_names:
            continue

        relative_path = file_path.relative_to(wiki_dir).as_posix()
        modified_at = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

        if relative_path.startswith("summaries/"):
            article_type = "OpenKB AI Summary"
        elif relative_path.startswith("sources/"):
            article_type = "OpenKB Source"
        else:
            article_type = "OpenKB Wiki"

        raw_markdown = file_path.read_text(encoding="utf-8", errors="ignore")

        articles.append({
            "title": clean_wiki_title(file_path),
            "type": article_type,
            "date": modified_at,
            "views": "",
            "url": f"/wiki/{relative_path}",
            "path": relative_path,
            "raw_markdown": raw_markdown,
        })

    articles.sort(key=lambda item: item["date"], reverse=True)
    return articles


def home(request):
    articles = get_openkb_wiki_articles()
    return render(request, "index.html", {"articles": articles})


def save_exact_suggested_article(title, body, keywords_raw):
    """
    Save suggested article as exact user-written Markdown.
    It saves to:
    - openkb-data/raw/
    - openkb-data/wiki/sources/

    It does not generate a new AI summary.
    Existing summaries inside openkb-data/wiki/summaries/ will still show on homepage.
    """
    timestamp_slug = datetime.now().strftime("%Y%m%d-%H%M%S")
    title_slug = slugify_title(title)
    filename = f"{timestamp_slug}-{title_slug}.md"

    raw_file_path = settings.OPENKB_RAW_DIR / filename

    sources_dir = settings.OPENKB_WIKI_DIR / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    wiki_file_path = sources_dir / filename

    markdown_content = f"# {title}\n\n{body}\n"

    raw_file_path.write_text(markdown_content, encoding="utf-8")
    wiki_file_path.write_text(markdown_content, encoding="utf-8")

    return filename


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

    filename = save_exact_suggested_article(title, body, keywords_raw)

    return render(request, "suggest.html", {
        "message": f"Article saved exactly as submitted: {filename}",
    })


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
    html_content = markdown.markdown(raw_markdown, extensions=["fenced_code", "tables", "toc"])

    metadata = {
        "type": "OpenKB Wiki",
        "path": file_path.relative_to(wiki_dir).as_posix(),
        "updated_at": datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
    }

    return render(request, "articles.html", {
        "title": clean_wiki_title(file_path),
        "content": html_content,
        "raw_markdown": raw_markdown,
        "metadata": metadata,
    })


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

    return render(request, "index.html", {
        "articles": results if query_original else get_openkb_wiki_articles(),
        "search_query": query_original,
        "is_search": bool(query_original),
        "result_count": len(results),
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
    """
    Find related OpenKB articles so the Ask OpenKB AI answer can show clickable sources.

    It searches:
    - openkb-data/wiki/sources/
    - openkb-data/wiki/summaries/
    - other openkb-data/wiki/*.md files
    """
    init_openkb_storage()

    query = question.lower().strip()
    query_words = [
        word for word in re.findall(r"[a-zA-Z0-9]+", query)
        if len(word) >= 2
    ]

    if not query_words:
        return []

    wiki_dir = settings.OPENKB_WIKI_DIR
    ignored_names = {"AGENTS.md", "log.md", "index.md", "README.md"}

    if not wiki_dir.exists():
        return []

    related = []

    for file_path in wiki_dir.rglob("*.md"):
        if file_path.name in ignored_names:
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

        # Prefer original solution/source articles over AI summaries
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
            related.append({
                "score": score,
                "title": clean_wiki_title(file_path),
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
    """
    Use the real local OpenKB AI query flow.

    Returns:
    - answer: OpenKB AI answer
    - related_articles: clickable related local OpenKB articles
    """
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