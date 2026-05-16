from django.conf import settings
from django.shortcuts import render


def get_kb_articles():
    content_dir = settings.OPENKB_CONTENT_DIR

    articles = []

    if not content_dir.exists():
        return articles

    allowed_extensions = [".md", ".txt", ".pdf"]

    for file in content_dir.rglob("*"):
        if file.is_file() and file.suffix.lower() in allowed_extensions:
            articles.append({
                "title": file.stem.replace("-", " ").replace("_", " ").title(),
                "type": file.suffix.upper().replace(".", ""),
                "url": "#",
            })

    return articles


def home(request):
    return render(request, "index.html", {
        "articles": get_kb_articles(),
    })