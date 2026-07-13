#!/usr/bin/env python3
"""Synchronise DjOpenKB's project-owned Django locale catalogs.

This utility deliberately scans only this project's Django code and templates;
it does not rewrite third-party OpenKB or Django package translations. It keeps
existing translations whose source message has not changed, adds new entries
with a blank translation, removes obsolete project entries, and compiles fresh
``django.mo`` files without requiring gettext on the host.

Usage:
    python scripts/sync_locales.py
    python scripts/sync_locales.py --check
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parents[1]
LOCALE_DIR = BASE_DIR / "locale"
PYTHON_SOURCE_DIRS = (BASE_DIR / "kb", BASE_DIR / "djopenkb")
TEMPLATE_SOURCE_DIRS = (BASE_DIR / "website" / "templates", BASE_DIR / "kb" / "templates")
SKIP_PATH_PARTS = {"migrations", "__pycache__", ".git"}
TRANSLATION_CALLS = {"_", "gettext", "gettext_lazy", "gettext_noop"}
PLURAL_TRANSLATION_CALLS = {"ngettext", "ngettext_lazy"}
CONTEXT_TRANSLATION_CALLS = {"pgettext", "pgettext_lazy"}
CONTEXT_PLURAL_TRANSLATION_CALLS = {"npgettext", "npgettext_lazy"}
PLACEHOLDER_RE = re.compile(r"%\((?P<name>[^)]+)\)[#0 +\-]*\d*(?:\.\d+)?[diouxXeEfFgGcrs]")

# GNU gettext plural rules for the locale directories configured in settings.py.
PLURAL_FORMS = {
    "ar": "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 && n%100<=99 ? 4 : 5;",
    "da": "nplurals=2; plural=(n != 1);",
    "de": "nplurals=2; plural=(n != 1);",
    "en": "nplurals=2; plural=(n != 1);",
    "es": "nplurals=2; plural=(n != 1);",
    "fa": "nplurals=2; plural=(n > 1);",
    "fi": "nplurals=2; plural=(n != 1);",
    "fr": "nplurals=2; plural=(n > 1);",
    "id": "nplurals=1; plural=0;",
    "it": "nplurals=2; plural=(n != 1);",
    "ja": "nplurals=1; plural=0;",
    "ko": "nplurals=1; plural=0;",
    "ms": "nplurals=1; plural=0;",
    "nl": "nplurals=2; plural=(n != 1);",
    "pl": "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "pt_BR": "nplurals=2; plural=(n != 1);",
    "ru": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "sv": "nplurals=2; plural=(n != 1);",
    "ta": "nplurals=2; plural=(n != 1);",
    "th": "nplurals=1; plural=0;",
    "tr": "nplurals=2; plural=(n > 1);",
    "vi": "nplurals=1; plural=0;",
    "zh_Hans": "nplurals=1; plural=0;",
}


@dataclass(frozen=True, order=True)
class MessageKey:
    context: str
    singular: str
    plural: str = ""


@dataclass
class PoEntry:
    context: str
    singular: str
    plural: str
    translations: list[str]


class SourceCollector:
    def __init__(self) -> None:
        self.references: dict[MessageKey, set[str]] = defaultdict(set)

    def add(self, singular: str, reference: str, plural: str = "", context: str = "") -> None:
        singular = (singular or "").strip()
        plural = (plural or "").strip()
        context = (context or "").strip()
        if not singular:
            return
        self.references[MessageKey(context, singular, plural)].add(reference)


def function_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def literal_string(node: ast.AST | None) -> str | None:
    """Return a static source string or ``None`` for a dynamic expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = literal_string(node.left)
        right = literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def literal_dict_values(node: ast.AST) -> Iterable[tuple[str, int]]:
    """Yield static string values from nested dictionaries.

    DjOpenKB's Django Admin labels are stored in literal ``labels`` and
    ``help_texts`` dictionaries, then passed through ``gettext_lazy`` at
    runtime. A normal AST scan cannot see the final translation call, so the
    locale synchroniser explicitly collects those dictionary values.
    """
    if not isinstance(node, ast.Dict):
        return
    for value_node in node.values:
        value = literal_string(value_node)
        if value is not None:
            yield value, getattr(value_node, "lineno", getattr(node, "lineno", 1))
        elif isinstance(value_node, ast.Dict):
            yield from literal_dict_values(value_node)


def iter_python_files() -> Iterable[Path]:
    for root in PYTHON_SOURCE_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in SKIP_PATH_PARTS for part in path.parts):
                continue
            # Test-only messages are not part of the main or admin UI.
            if path.name in {"tests.py"}:
                continue
            yield path


def collect_python_messages(collector: SourceCollector) -> None:
    for path in iter_python_files():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise RuntimeError(f"Could not parse {path}: {exc}") from exc
        relative = path.relative_to(BASE_DIR).as_posix()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = function_name(node.func)
            ref = f"{relative}:{node.lineno}"
            if name in TRANSLATION_CALLS and node.args:
                singular = literal_string(node.args[0])
                if singular is not None:
                    collector.add(singular, ref)
            elif name in PLURAL_TRANSLATION_CALLS and len(node.args) >= 2:
                singular = literal_string(node.args[0])
                plural = literal_string(node.args[1])
                if singular is not None and plural is not None:
                    collector.add(singular, ref, plural=plural)
            elif name in CONTEXT_TRANSLATION_CALLS and len(node.args) >= 2:
                context = literal_string(node.args[0])
                singular = literal_string(node.args[1])
                if context is not None and singular is not None:
                    collector.add(singular, ref, context=context)
            elif name in CONTEXT_PLURAL_TRANSLATION_CALLS and len(node.args) >= 3:
                context = literal_string(node.args[0])
                singular = literal_string(node.args[1])
                plural = literal_string(node.args[2])
                if context is not None and singular is not None and plural is not None:
                    collector.add(singular, ref, plural=plural, context=context)
            elif name == "_set_admin_model_label" and len(node.args) >= 3:
                # _set_admin_model_label translates both literal labels inside
                # the helper, so make them visible to the static extractor.
                singular = literal_string(node.args[1])
                plural = literal_string(node.args[2])
                if singular is not None:
                    collector.add(singular, ref)
                if plural is not None:
                    collector.add(plural, ref)

        if relative == "kb/admin.py":
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                target_names = {
                    target.id for target in node.targets
                    if isinstance(target, ast.Name)
                }
                if not target_names.intersection({"labels", "help_texts"}):
                    continue
                for message, line_number in literal_dict_values(node.value):
                    collector.add(message, f"{relative}:{line_number}")


DIRECT_TEMPLATE_RE = re.compile(
    r"{%\s*(?:trans|translate)\s+(?P<quote>['\"])(?P<message>.*?)(?P=quote)(?P<tail>.*?)%}",
    re.DOTALL,
)
BLOCK_TEMPLATE_RE = re.compile(
    r"{%\s*(?:blocktrans|blocktranslate)(?P<args>.*?)%}(?P<body>.*?){%\s*endblock(?:trans|translate)\s*%}",
    re.DOTALL,
)
PLURAL_TAG_RE = re.compile(r"{%\s*plural\s*%}", re.IGNORECASE)
CONTEXT_RE = re.compile(r"\bcontext\s+(?P<quote>['\"])(?P<context>.*?)(?P=quote)", re.DOTALL)
COMMENT_RE = re.compile(r"{#.*?#}", re.DOTALL)
VARIABLE_RE = re.compile(r"{{\s*(?P<value>[^}]+?)\s*}}")


def normalise_block_message(value: str) -> str:
    value = COMMENT_RE.sub("", value)

    def replace_variable(match: re.Match[str]) -> str:
        raw = match.group("value").strip()
        # Django's extraction uses the variable name, not a filter expression.
        name = re.split(r"[| .:]", raw, maxsplit=1)[0].strip()
        return f"%({name})s" if name else ""

    value = VARIABLE_RE.sub(replace_variable, value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def iter_template_files() -> Iterable[Path]:
    for root in TEMPLATE_SOURCE_DIRS:
        if not root.exists():
            continue
        yield from root.rglob("*.html")


def line_number_for_offset(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def collect_template_messages(collector: SourceCollector) -> None:
    for path in iter_template_files():
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(BASE_DIR).as_posix()

        for match in DIRECT_TEMPLATE_RE.finditer(source):
            message = match.group("message")
            context_match = CONTEXT_RE.search(match.group("tail") or "")
            context = context_match.group("context") if context_match else ""
            collector.add(message, f"{relative}:{line_number_for_offset(source, match.start())}", context=context)

        for match in BLOCK_TEMPLATE_RE.finditer(source):
            body = match.group("body")
            context_match = CONTEXT_RE.search(match.group("args") or "")
            context = context_match.group("context") if context_match else ""
            ref = f"{relative}:{line_number_for_offset(source, match.start())}"
            plural_match = PLURAL_TAG_RE.search(body)
            if plural_match:
                singular = normalise_block_message(body[: plural_match.start()])
                plural = normalise_block_message(body[plural_match.end() :])
                collector.add(singular, ref, plural=plural, context=context)
            else:
                collector.add(normalise_block_message(body), ref, context=context)


def po_unquote(fragment: str) -> str:
    quote_index = fragment.find('"')
    if quote_index == -1:
        return ""
    try:
        return json.loads(fragment[quote_index:])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid PO string fragment: {fragment!r}") from exc


def parse_po(path: Path) -> dict[MessageKey, PoEntry]:
    """Parse the subset of the PO format written by Django/msgmerge."""
    if not path.exists():
        return {}

    entries: dict[MessageKey, PoEntry] = {}
    context: str | None = None
    singular: str | None = None
    plural = ""
    translations: dict[int, str] = {}
    state: tuple[str, int | None] | None = None

    def finish() -> None:
        nonlocal context, singular, plural, translations, state
        if singular is not None:
            max_index = max(translations, default=0)
            values = [translations.get(index, "") for index in range(max_index + 1)]
            key = MessageKey(context or "", singular, plural)
            entries[key] = PoEntry(context or "", singular, plural, values)
        context = None
        singular = None
        plural = ""
        translations = {}
        state = None

    for raw_line in path.read_text(encoding="utf-8").splitlines() + [""]:
        line = raw_line.rstrip("\n")
        if not line.strip():
            finish()
            continue
        if line.startswith("#"):
            continue
        if line.startswith("msgctxt "):
            context = po_unquote(line)
            state = ("context", None)
            continue
        if line.startswith("msgid_plural "):
            plural = po_unquote(line)
            state = ("plural", None)
            continue
        if line.startswith("msgid "):
            singular = po_unquote(line)
            state = ("singular", None)
            continue
        match = re.match(r"msgstr\[(\d+)\]\s+", line)
        if match:
            index = int(match.group(1))
            translations[index] = po_unquote(line)
            state = ("translation", index)
            continue
        if line.startswith("msgstr "):
            translations[0] = po_unquote(line)
            state = ("translation", 0)
            continue
        if line.lstrip().startswith('"') and state:
            value = po_unquote(line.strip())
            kind, index = state
            if kind == "context":
                context = (context or "") + value
            elif kind == "singular":
                singular = (singular or "") + value
            elif kind == "plural":
                plural += value
            elif kind == "translation" and index is not None:
                translations[index] = translations.get(index, "") + value

    return entries


def plural_count(language: str) -> int:
    rule = PLURAL_FORMS.get(language, "nplurals=2; plural=(n != 1);")
    match = re.search(r"nplurals\s*=\s*(\d+)", rule)
    return int(match.group(1)) if match else 2


def header_text(language: str) -> str:
    return (
        "Project-Id-Version: DjOpenKB\n"
        "Report-Msgid-Bugs-To: \n"
        "POT-Creation-Date: \n"
        "PO-Revision-Date: \n"
        "Last-Translator: \n"
        "Language-Team: \n"
        f"Language: {language}\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        f"Plural-Forms: {PLURAL_FORMS.get(language, 'nplurals=2; plural=(n != 1);')}\n"
    )


def po_field_lines(name: str, value: str) -> list[str]:
    if "\n" not in value:
        return [f"{name} {json.dumps(value, ensure_ascii=False)}"]
    lines = [f"{name} \"\""]
    for chunk in value.splitlines(keepends=True):
        lines.append(json.dumps(chunk, ensure_ascii=False))
    if value and not value.endswith("\n") and not value.splitlines(keepends=True):
        lines.append(json.dumps(value, ensure_ascii=False))
    return lines


def translations_for(language: str, key: MessageKey, existing: dict[MessageKey, PoEntry]) -> list[str]:
    count = plural_count(language) if key.plural else 1
    if language == "en":
        if key.plural:
            base = [key.singular, key.plural]
            return [base[min(index, len(base) - 1)] for index in range(count)]
        return [key.singular]

    previous = existing.get(key)
    if not previous:
        return [""] * count
    return [(previous.translations[index] if index < len(previous.translations) else "") for index in range(count)]


def placeholder_names(value: str) -> set[str]:
    """Return named Django/Python interpolation placeholders in a catalog string."""
    return set(PLACEHOLDER_RE.findall(value))


def translation_values_are_safe(key: MessageKey, values: list[str]) -> bool:
    """Ensure non-empty translations keep the named placeholders from the source."""
    source_values = [key.singular]
    if key.plural:
        source_values.append(key.plural)
    expected = set().union(*(placeholder_names(value) for value in source_values))
    return all(not value or placeholder_names(value) == expected for value in values)


def write_po(path: Path, language: str, references: dict[MessageKey, set[str]], existing: dict[MessageKey, PoEntry]) -> tuple[int, int]:
    lines: list[str] = [
        "# DjOpenKB project locale catalog.",
        "# Generated by scripts/sync_locales.py. Keep msgid values unchanged; edit only msgstr values.",
        "msgid \"\"",
        "msgstr \"\"",
    ]
    for header_line in header_text(language).splitlines(keepends=True):
        lines.append(json.dumps(header_line, ensure_ascii=False))
    lines.append("")

    completed = 0
    for key in sorted(references):
        refs = sorted(references[key])
        lines.append("#: " + " ".join(refs))
        if key.context:
            lines.extend(po_field_lines("msgctxt", key.context))
        lines.extend(po_field_lines("msgid", key.singular))
        if key.plural:
            lines.extend(po_field_lines("msgid_plural", key.plural))
        values = translations_for(language, key, existing)
        is_safe = translation_values_are_safe(key, values)
        if key.plural:
            if all(values) and is_safe:
                completed += 1
            for index, value in enumerate(values):
                lines.extend(po_field_lines(f"msgstr[{index}]", value))
        else:
            if values and values[0] and is_safe:
                completed += 1
            lines.extend(po_field_lines("msgstr", values[0] if values else ""))
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return len(references), completed


def mo_message_id(key: MessageKey) -> str:
    source = key.singular
    if key.plural:
        source += "\x00" + key.plural
    if key.context:
        source = key.context + "\x04" + source
    return source


def write_mo(path: Path, language: str, references: dict[MessageKey, set[str]], existing: dict[MessageKey, PoEntry]) -> None:
    catalog: dict[str, str] = {"": header_text(language)}
    for key in references:
        values = translations_for(language, key, existing)
        if not translation_values_are_safe(key, values):
            # Keep the PO entry for correction, but never compile a translation
            # that would fail at runtime because an interpolation token changed.
            continue
        if key.plural:
            # Do not compile partial plural translations: Django should fall back
            # to the source text until every plural form has been translated.
            if not values or not all(values):
                continue
            translated = "\x00".join(values)
        else:
            if not values or not values[0]:
                continue
            translated = values[0]
        catalog[mo_message_id(key)] = translated

    ordered = sorted(catalog.items(), key=lambda item: item[0].encode("utf-8"))
    originals = [source.encode("utf-8") for source, _translation in ordered]
    translated = [translation.encode("utf-8") for _source, translation in ordered]
    count = len(ordered)
    header_size = 7 * 4
    table_size = count * 8
    originals_offset = header_size + (2 * table_size)
    translated_offset = originals_offset + sum(len(value) + 1 for value in originals)

    original_table: list[tuple[int, int]] = []
    cursor = originals_offset
    for value in originals:
        original_table.append((len(value), cursor))
        cursor += len(value) + 1

    translated_table: list[tuple[int, int]] = []
    cursor = translated_offset
    for value in translated:
        translated_table.append((len(value), cursor))
        cursor += len(value) + 1

    # hash_size=0 means there is no hash table, so hash_offset must be zero.
    output = [struct.pack("<7I", 0x950412DE, 0, count, header_size, header_size + table_size, 0, 0)]
    output.append(b"".join(struct.pack("<2I", length, offset) for length, offset in original_table))
    output.append(b"".join(struct.pack("<2I", length, offset) for length, offset in translated_table))
    output.append(b"\x00".join(originals) + b"\x00")
    output.append(b"\x00".join(translated) + b"\x00")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(output))


def locale_directories() -> list[Path]:
    return sorted(path for path in LOCALE_DIR.iterdir() if path.is_dir() and (path / "LC_MESSAGES").exists())


def write_status(references: dict[MessageKey, set[str]], counts: dict[str, tuple[int, int]]) -> None:
    total = len(references)
    plural = sum(1 for key in references if key.plural)
    lines = [
        "# Locale Translation Status",
        "",
        "This file is generated by `scripts/sync_locales.py`.",
        "",
        f"- Project-owned source messages: **{total}**",
        f"- Plural-aware messages: **{plural}**",
        "- Article titles/bodies, usernames, filenames, stored audit history, and database identifiers are intentionally not catalog entries.",
        "- Standard Django Admin framework strings are supplied by Django's own installed translations; this catalog covers DjOpenKB's custom Admin labels, helper text, actions, and messages.",
        "",
        "| Locale | Complete entries | Total entries |",
        "|---|---:|---:|",
    ]
    for language, (total_entries, completed_entries) in sorted(counts.items()):
        lines.append(f"| `{language}` | {completed_entries} | {total_entries} |")
    lines.extend([
        "",
        "## Translation workflow",
        "",
        "1. Translate only `msgstr` / `msgstr[n]`; do not edit `msgid`, `msgid_plural`, reference lines, or placeholder names such as `%(count)s`.",
        "2. Run `python scripts/sync_locales.py` after adding or changing UI text. Existing exact translations are retained and fresh `.mo` files are compiled; use `--check` to detect catalog drift or altered named placeholders.",
        "3. Rebuild/restart the web container so Django reads the updated compiled locale files.",
    ])
    (LOCALE_DIR / "TRANSLATION_STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_messages() -> dict[MessageKey, set[str]]:
    collector = SourceCollector()
    collect_python_messages(collector)
    collect_template_messages(collector)
    return dict(collector.references)


def check_catalogs(references: dict[MessageKey, set[str]]) -> int:
    failed = False
    expected = set(references)
    for locale_path in locale_directories():
        language = locale_path.name
        po_path = locale_path / "LC_MESSAGES" / "django.po"
        entries = parse_po(po_path)
        actual = {key for key in entries if key.singular}
        missing = expected - actual
        stale = actual - expected
        unsafe = [
            key for key, entry in entries.items()
            if key.singular and key in expected and not translation_values_are_safe(key, entry.translations)
        ]
        if missing or stale or unsafe:
            failed = True
            print(f"{language}: {len(missing)} missing, {len(stale)} obsolete, {len(unsafe)} unsafe placeholder translation(s)")
            for key in sorted(missing)[:5]:
                print(f"  missing: {key.singular!r}")
            for key in sorted(stale)[:5]:
                print(f"  obsolete: {key.singular!r}")
            for key in sorted(unsafe)[:5]:
                print(f"  unsafe placeholders: {key.singular!r}")
    if not failed:
        print(f"Locale catalogs are in sync: {len(expected)} project messages across {len(locale_directories())} locales.")
    return 1 if failed else 0


def sync_catalogs(references: dict[MessageKey, set[str]]) -> int:
    counts: dict[str, tuple[int, int]] = {}
    for locale_path in locale_directories():
        language = locale_path.name
        po_path = locale_path / "LC_MESSAGES" / "django.po"
        mo_path = locale_path / "LC_MESSAGES" / "django.mo"
        existing = parse_po(po_path)
        total, completed = write_po(po_path, language, references, existing)
        # Reload the just-written source catalog so the MO reflects exactly the
        # content a translator sees in the PO file.
        compiled_entries = parse_po(po_path)
        write_mo(mo_path, language, references, compiled_entries)
        counts[language] = (total, completed)
        print(f"{language}: {completed}/{total} complete; compiled {mo_path.relative_to(BASE_DIR)}")
    write_status(references, counts)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report catalog drift without changing files.")
    args = parser.parse_args()
    references = collect_messages()
    if args.check:
        return check_catalogs(references)
    return sync_catalogs(references)


if __name__ == "__main__":
    raise SystemExit(main())
