"""Find and remove uploaded image files that are no longer owned anywhere.

Run a safe preview from the Ubuntu server host:
    cd /opt/DjOpenKB
    sudo docker compose exec web \
      python manage.py cleanup_stray_upload_files --dry-run

Run interactively using the minimum age configured in Django Admin:
    sudo docker compose exec web \
      python manage.py cleanup_stray_upload_files

Run non-interactively for the Docker scheduler:
    sudo docker compose exec web \
      python manage.py cleanup_stray_upload_files --noinput

Purpose and warning:
    Delete files under the OpenKB uploads directory only when they are not
    referenced by an article, Markdown file, persistent New Article checkpoint,
    or persistent existing-article edit/review checkpoint. Valid checkpoints
    never expire because of age. Use
    --dry-run first because deleted orphan files cannot be restored by this
    command.
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from kb.models import ArticleImageUploadLog, SiteSetting
from kb.views import find_stray_uploaded_files, get_openkb_uploads_dir, mark_article_image_deleted


class Command(BaseCommand):
    help = (
        "Delete orphaned files under openkb-data/wiki/uploads. Files owned by "
        "articles or persistent article creation/edit checkpoints are always protected, "
        "regardless of age."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-age-minutes",
            type=int,
            default=None,
            help=(
                "Override the Django Admin setting. Orphan files newer than this "
                "many minutes are ignored. Use 0 to delete detected orphan uploads immediately."
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
            f"Orphan-file minimum age: {min_age_minutes} minute(s). "
            f"Persistent checkpoints are protected: yes. "
            f"Stray files: {len(stray_files)}."
        )

        for item in stray_files:
            self.stdout.write(
                f"- {item['filename']} "
                f"({item['size_kb']} KB, modified {item['modified_at']:%Y-%m-%d %H:%M})"
            )

        if not stray_files:
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only. No files were deleted."))
            return

        if not noinput:
            answer = input(
                "Delete all listed orphan upload files? Type yes to continue: "
            ).strip().lower()
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
                    mark_article_image_deleted(
                        item["filename"],
                        reason=ArticleImageUploadLog.DeleteReason.AUTO_CLEANUP,
                    )
                    deleted_count += 1
                    deleted_size_bytes += size
            except OSError as error:
                errors.append(f"Could not delete {item['filename']}: {error}")

        if deleted_count:
            self.stdout.write(self.style.SUCCESS(
                f"Deleted {deleted_count} orphan upload file(s), "
                f"freeing {round(deleted_size_bytes / 1024, 1)} KB."
            ))
        else:
            self.stdout.write(self.style.WARNING("No files were deleted."))

        for error in errors:
            self.stderr.write(self.style.ERROR(error))
