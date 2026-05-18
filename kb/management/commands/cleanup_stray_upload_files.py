import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from kb.models import SiteSetting
from kb.views import find_stray_uploaded_files, get_openkb_uploads_dir


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Delete stray files under openkb-data/wiki/uploads. "
        "Uses the minimum age configured in Django Admin → Site settings by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-age-minutes",
            type=int,
            default=None,
            help=(
                "Override Django Admin setting. Files newer than this many minutes are ignored. "
                "Use 0 to delete stray uploads immediately."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without deleting files.",
        )
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="Run without confirmation prompts. Intended for Docker scheduler/cron.",
        )

    def handle(self, *args, **options):
        min_age_minutes = options["min_age_minutes"]
        dry_run = options["dry_run"]
        noinput = options["noinput"]

        if min_age_minutes is None:
            min_age_minutes = SiteSetting.load().stray_upload_cleanup_min_age_minutes

        min_age_minutes = max(int(min_age_minutes), 0)
        stray_files = find_stray_uploaded_files(min_age_minutes=min_age_minutes)

        self.stdout.write(
            f"Stray upload cleanup scan complete. "
            f"Minimum age: {min_age_minutes} minute(s). "
            f"Found: {len(stray_files)} file(s)."
        )

        if not stray_files:
            return

        total_size_bytes = sum(item["size_bytes"] for item in stray_files)

        for item in stray_files:
            self.stdout.write(
                f"- {item['filename']} "
                f"({item['size_kb']} KB, modified {item['modified_at']:%Y-%m-%d %H:%M})"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only. No files were deleted."))
            return

        if not noinput:
            answer = input("Delete all listed stray upload files? Type yes to continue: ").strip().lower()
            if answer != "yes":
                self.stdout.write(self.style.WARNING("Cleanup cancelled."))
                return

        upload_dir = get_openkb_uploads_dir().resolve()
        deleted_count = 0
        deleted_size_bytes = 0
        errors = []

        for item in stray_files:
            file_path = Path(item["path"]).resolve()

            try:
                file_path.relative_to(upload_dir)
            except ValueError:
                errors.append(f"Skipped invalid path: {item['filename']}")
                continue

            try:
                if file_path.exists() and file_path.is_file():
                    size = file_path.stat().st_size
                    file_path.unlink()
                    deleted_count += 1
                    deleted_size_bytes += size
            except OSError as error:
                errors.append(f"Could not delete {item['filename']}: {error}")

        if deleted_count:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {deleted_count} stray upload file(s), "
                    f"freeing {round(deleted_size_bytes / 1024, 1)} KB."
                )
            )
        else:
            self.stdout.write(self.style.WARNING("No files were deleted."))

        for error in errors:
            self.stderr.write(self.style.ERROR(error))
