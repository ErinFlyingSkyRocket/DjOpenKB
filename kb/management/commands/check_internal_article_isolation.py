from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from kb.models import SuggestedArticle
from kb.views.services import (
    DJANGO_ARTICLE_SOURCE_MARKER,
    get_openkb_internal_data_dir,
    sync_internal_openkb_ai_index,
    sync_openkb_ai_index,
)


class Command(BaseCommand):
    help = "Verify internal articles are not present in the public OpenKB data/index tree."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync-first",
            action="store_true",
            help="Rebuild public and internal OpenKB AI indexes before checking isolation.",
        )

    def handle(self, *args, **options):
        if options["sync_first"]:
            sync_openkb_ai_index()
            sync_internal_openkb_ai_index()

        internal_articles = list(
            SuggestedArticle.objects.filter(
                visibility=SuggestedArticle.Visibility.INTERNAL,
            ).only("id", "title", "filename", "raw_path", "wiki_path", "status")
        )

        public_root = settings.OPENKB_DATA_DIR.resolve()
        internal_root = get_openkb_internal_data_dir().resolve()

        violations = []
        expected_internal_files = []

        for article in internal_articles:
            filename = article.filename
            if not filename:
                continue

            public_candidates = [
                public_root / "wiki" / "sources" / filename,
                public_root / "raw" / filename,
                public_root / "raw" / "internal" / filename,
            ]
            for candidate in public_candidates:
                if candidate.exists():
                    violations.append(f"Internal article #{article.pk} found in public tree: {candidate}")

            expected_internal_files.append(internal_root / "raw" / "internal" / filename)
            if article.status == SuggestedArticle.Status.PUBLISHED:
                expected_internal_files.append(internal_root / "wiki" / "sources" / filename)

        # Also scan generated public source files for explicit internal metadata.
        public_sources_dir = public_root / "wiki" / "sources"
        if public_sources_dir.exists():
            for path in public_sources_dir.glob("*.md"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if DJANGO_ARTICLE_SOURCE_MARKER in text and "visibility: internal" in text:
                    violations.append(f"Internal visibility marker found in public source file: {path}")

        missing_internal = [path for path in expected_internal_files if not path.exists()]

        if violations:
            for violation in violations:
                self.stderr.write(self.style.ERROR(violation))
            raise CommandError("Internal article isolation check failed.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Internal article isolation passed. Checked {len(internal_articles)} internal article(s)."
            )
        )

        if missing_internal:
            self.stdout.write(
                self.style.WARNING(
                    f"Note: {len(missing_internal)} expected internal runtime file(s) are missing. "
                    "Run: python manage.py sync_openkb_ai --scope internal"
                )
            )
