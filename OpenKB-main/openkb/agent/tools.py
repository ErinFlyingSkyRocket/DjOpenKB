"""Constrained wiki tool functions for the OpenKB agents.

All reads are confined to the generated wiki structure, file types are
allowlisted, and file sizes are bounded before content reaches an LLM tool.
"""
from __future__ import annotations

import json as _json
import re
from pathlib import Path

MAX_WIKI_TEXT_BYTES = 1 * 1024 * 1024
MAX_PAGE_INDEX_BYTES = 5 * 1024 * 1024
MAX_WIKI_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PAGE_REQUEST_COUNT = 25
ALLOWED_MARKDOWN_DIRECTORIES = {"sources", "summaries", "concepts"}
ALLOWED_ROOT_MARKDOWN_FILES = {"index.md"}


def _resolved_relative_path(path: str, wiki_root: str) -> tuple[Path, Path] | None:
    root = Path(wiki_root).resolve()
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None
    candidate = Path(path.strip().replace("\\", "/"))
    if candidate.is_absolute():
        return None
    full_path = (root / candidate).resolve()
    if not full_path.is_relative_to(root):
        return None
    return root, full_path


def _is_allowed_markdown_path(full_path: Path, root: Path) -> bool:
    relative = full_path.relative_to(root)
    if relative.as_posix() in ALLOWED_ROOT_MARKDOWN_FILES:
        return True
    return (
        len(relative.parts) >= 2
        and relative.parts[0] in ALLOWED_MARKDOWN_DIRECTORIES
        and full_path.suffix.lower() == ".md"
    )


def list_wiki_files(directory: str, wiki_root: str) -> str:
    """List Markdown files only inside approved wiki content directories."""
    resolved = _resolved_relative_path(directory, wiki_root)
    if resolved is None:
        return "Access denied: invalid wiki directory."
    root, target = resolved
    relative = target.relative_to(root)
    if not relative.parts or relative.parts[0] not in ALLOWED_MARKDOWN_DIRECTORIES:
        return "Access denied: directory is outside approved wiki content."
    if not target.exists() or not target.is_dir():
        return "No files found."

    md_files = sorted(
        p.name
        for p in target.iterdir()
        if p.is_file() and p.suffix.lower() == ".md" and p.resolve().is_relative_to(root)
    )
    return "\n".join(md_files) if md_files else "No files found."


def read_wiki_file(path: str, wiki_root: str) -> str:
    """Read one bounded Markdown file from an approved wiki location."""
    resolved = _resolved_relative_path(path, wiki_root)
    if resolved is None:
        return "Access denied: invalid wiki path."
    root, full_path = resolved
    if not _is_allowed_markdown_path(full_path, root):
        return "Access denied: only approved Markdown wiki files may be read."
    if not full_path.exists() or not full_path.is_file():
        return f"File not found: {path}"
    try:
        size = full_path.stat().st_size
    except OSError:
        return f"File not found: {path}"
    if size > MAX_WIKI_TEXT_BYTES:
        return "Access denied: wiki file exceeds the safe read limit."
    try:
        return full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "Unable to read the requested wiki file safely."


def parse_pages(pages: str) -> list[int]:
    """Parse a page specification into a bounded sorted page list."""
    result: set[int] = set()
    for part in str(pages or "").split(","):
        part = part.strip()
        range_match = re.fullmatch(r"(\d+)-(\d+)", part)
        if range_match:
            start, end = (int(value) for value in range_match.groups())
            if start > end:
                continue
            for page in range(start, end + 1):
                if page > 0:
                    result.add(page)
                if len(result) >= MAX_PAGE_REQUEST_COUNT:
                    return sorted(result)
            continue
        if part.isdigit():
            page = int(part)
            if page > 0:
                result.add(page)
        if len(result) >= MAX_PAGE_REQUEST_COUNT:
            break
    return sorted(result)


def get_wiki_page_content(doc_name: str, pages: str, wiki_root: str) -> str:
    """Return bounded content from an approved PageIndex JSON source."""
    if not isinstance(doc_name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", doc_name):
        return "Access denied: invalid document name."
    root = Path(wiki_root).resolve()
    target = (root / "sources" / f"{doc_name}.json").resolve()
    if not target.is_relative_to(root / "sources"):
        return "Access denied: invalid document path."
    if not target.exists() or not target.is_file():
        return f"File not found: sources/{doc_name}.json"
    try:
        if target.stat().st_size > MAX_PAGE_INDEX_BYTES:
            return "Access denied: page data exceeds the safe read limit."
        data = _json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, _json.JSONDecodeError):
        return "Unable to read the requested page data safely."
    if not isinstance(data, list):
        return "Unable to read the requested page data safely."

    requested = set(parse_pages(pages))
    if not requested:
        return "No valid page numbers were requested."
    matches = [entry for entry in data if isinstance(entry, dict) and entry.get("page") in requested]
    if not matches:
        return f"No content found for pages {pages} in {doc_name}."

    parts: list[str] = []
    for entry in matches[:MAX_PAGE_REQUEST_COUNT]:
        page_num = entry.get("page")
        content = entry.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        block = f"[Page {page_num}]\n{content}"
        images = entry.get("images")
        if isinstance(images, list):
            paths = ", ".join(
                image.get("path")
                for image in images
                if isinstance(image, dict) and isinstance(image.get("path"), str)
            )
            if paths:
                block += f"\n[Images: {paths}]"
        parts.append(block)
    output = "\n\n".join(parts) + "\n\n"
    if len(output.encode("utf-8")) > MAX_WIKI_TEXT_BYTES:
        return "Access denied: requested page content exceeds the safe read limit."
    return output


_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def read_wiki_image(path: str, wiki_root: str) -> dict:
    """Read a bounded allowlisted image under ``sources/images`` only."""
    import base64

    resolved = _resolved_relative_path(path, wiki_root)
    if resolved is None:
        return {"type": "text", "text": "Access denied: invalid image path."}
    root, full_path = resolved
    image_root = (root / "sources" / "images").resolve()
    if not full_path.is_relative_to(image_root):
        return {"type": "text", "text": "Access denied: image is outside the approved image directory."}
    mime = _MIME_TYPES.get(full_path.suffix.lower())
    if mime is None:
        return {"type": "text", "text": "Access denied: unsupported image type."}
    if not full_path.exists() or not full_path.is_file():
        return {"type": "text", "text": f"Image not found: {path}"}
    try:
        if full_path.stat().st_size > MAX_WIKI_IMAGE_BYTES:
            return {"type": "text", "text": "Access denied: image exceeds the safe read limit."}
        raw = full_path.read_bytes()
    except OSError:
        return {"type": "text", "text": "Unable to read the requested image safely."}
    return {"type": "image", "image_url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}


def write_wiki_file(path: str, content: str, wiki_root: str) -> str:
    """Write a bounded Markdown file inside the wiki root."""
    resolved = _resolved_relative_path(path, wiki_root)
    if resolved is None:
        return "Access denied: invalid wiki path."
    root, full_path = resolved
    if not _is_allowed_markdown_path(full_path, root):
        return "Access denied: only approved Markdown wiki files may be written."
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_WIKI_TEXT_BYTES:
        return "Access denied: wiki content exceeds the safe write limit."
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return f"Written: {path}"
