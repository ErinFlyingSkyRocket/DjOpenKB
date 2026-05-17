import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import SuggestedArticle


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
    """Delete Markdown files for a user-owned article."""
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


def get_article_metadata_by_wiki_path(wiki_path):
    try:
        return SuggestedArticle.objects.select_related("owner").get(
            wiki_path=wiki_path,
            status=SuggestedArticle.Status.PUBLISHED,
        )
    except SuggestedArticle.DoesNotExist:
        return None


def get_openkb_wiki_articles():
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
            "views": "",
            "url": f"/wiki/{relative_path}",
            "path": relative_path,
            "raw_markdown": raw_markdown,
            "author": suggested.author_display if suggested else "",
        })

    articles.sort(key=lambda item: item["date"], reverse=True)
    return articles


class OpenKBLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        username = self.request.user.get_full_name() or self.request.user.get_username()
        messages.success(self.request, f"Successful login. Welcome back, {username}.")
        return response


def home(request):
    articles = get_openkb_wiki_articles()
    return render(request, "index.html", {"articles": articles})


@login_required
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
    )
    write_article_files(article)

    messages.success(request, f"Article suggested successfully: {article.title}")
    return redirect("profile")


@login_required
def profile(request):
    articles = SuggestedArticle.objects.filter(owner=request.user)
    return render(request, "profile.html", {"articles": articles})


@login_required
def edit_suggestion(request, article_id):
    article = get_object_or_404(SuggestedArticle, pk=article_id, owner=request.user)

    if request.method == "GET":
        return render(request, "suggest_edit.html", {"article": article, "current_status": article.status})

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
        })

    article.title = title
    article.body = body
    article.keywords = keywords_raw
    article.status = status
    article.save()
    write_article_files(article)

    messages.success(request, f"Article updated: {article.title}")
    return redirect("profile")


@login_required
def delete_suggestion(request, article_id):
    article = get_object_or_404(SuggestedArticle, pk=article_id, owner=request.user)

    if request.method == "POST":
        title = article.title
        delete_article_files(article)
        article.delete()
        messages.success(request, f"Article deleted: {title}")
        return redirect("profile")

    return render(request, "suggest_delete.html", {"article": article})


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
    suggested = get_article_metadata_by_wiki_path(wiki_path)

    if suggested:
        metadata = {
            "has_details": True,
            "type": "OpenKB Source",
            "path": wiki_path,
            "published_at": suggested.created_at,
            "updated_at": suggested.updated_at,
            "author": suggested.author_display,
            "author_email": suggested.author_email,
            "keywords": suggested.keyword_list,
            "permalink": request.build_absolute_uri(suggested.public_url),
            "can_edit": request.user.is_authenticated and (
                request.user == suggested.owner or request.user.is_staff
            ),
            "edit_url": reverse("edit_suggestion", kwargs={"article_id": suggested.pk}),
        }
        title = suggested.title
    else:
        metadata = {
            "has_details": False,
            "type": "OpenKB Wiki",
            "path": wiki_path,
            "updated_at": datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        title = clean_wiki_title(file_path)

    return render(request, "articles.html", {
        "title": title,
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
