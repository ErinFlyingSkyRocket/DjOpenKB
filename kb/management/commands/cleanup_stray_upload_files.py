"""Find and remove uploaded image files that are no longer referenced by articles.

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

Show all supported options:
    sudo docker compose exec web \
      python manage.py cleanup_stray_upload_files --help

Purpose and warning:
    Discards abandoned temporary new-article workspaces and deletes files under
    the OpenKB uploads directory when they are no longer owned by an active
    workspace or linked to an article. Use --dry-run first because discarded
    temporary work and deleted files cannot be restored by this command.
"""

import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from kb.models import ArticleCreationWorkspace, ArticleImageUploadLog, SiteSetting
from kb.views import find_stray_uploaded_files, get_openkb_uploads_dir, mark_article_image_deleted
from kb.views.services import discard_article_creation_workspace, stale_article_creation_workspaces


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Discard abandoned new-article workspaces and delete stray files under "
        "openkb-data/wiki/uploads. Uses the minimum age configured in Django Admin "
        "→ Site settings by default; workspace retention is never shorter than 24 hours."
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
        # Temporary creation workspaces are never auto-discarded earlier than
        # 24 hours, even when ordinary stray-file scanning is set to 0.
        workspace_min_age_minutes = max(min_age_minutes, 1440)
        stale_workspaces = list(stale_article_creation_workspaces(workspace_min_age_minutes))
        stale_workspace_ids = [str(workspace.pk) for workspace in stale_workspaces]
        stray_files = find_stray_uploaded_files(
            min_age_minutes=min_age_minutes,
            exclude_workspace_ids=stale_workspace_ids,
        )

        self.stdout.write(
            f"Stray upload cleanup scan complete. "
            f"File minimum age: {min_age_minutes} minute(s). "
            f"Abandoned workspace minimum age: {workspace_min_age_minutes} minute(s). "
            f"Abandoned workspaces: {len(stale_workspaces)}. "
            f"Stray files: {len(stray_files)}."
        )

        for workspace in stale_workspaces:
            self.stdout.write(
                f"- workspace {workspace.pk} for user ID {workspace.owner_id} "
                f"(last updated {workspace.updated_at:%Y-%m-%d %H:%M})"
            )
        for item in stray_files:
            self.stdout.write(
                f"- {item['filename']} "
                f"({item['size_kb']} KB, modified {item['modified_at']:%Y-%m-%d %H:%M})"
            )

        if not stale_workspaces and not stray_files:
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "Dry run only. No workspaces or files were deleted."
            ))
            return

        if not noinput:
            answer = input(
                "Discard all listed abandoned workspaces and delete all listed stray files? "
                "Type yes to continue: "
            ).strip().lower()
            if answer != "yes":
                self.stdout.write(self.style.WARNING("Cleanup cancelled."))
                return

        discarded_workspace_count = 0
        workspace_errors = []
        for workspace in stale_workspaces:
            try:
                with transaction.atomic():
                    locked_workspace = (
                        ArticleCreationWorkspace.objects
                        .select_for_update()
                        .filter(pk=workspace.pk)
                        .first()
                    )
                    if locked_workspace is not None:
                        discard_article_creation_workspace(
                            None,
                            locked_workspace,
                            reason=ArticleImageUploadLog.DeleteReason.AUTO_CLEANUP,
                        )
                        discarded_workspace_count += 1
            except Exception as error:
                logger.exception("Could not discard stale article creation workspace %s", workspace.pk)
                workspace_errors.append(
                    f"Could not discard stale article workspace {workspace.pk}: {error}"
                )

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

        if discarded_workspace_count:
            self.stdout.write(self.style.SUCCESS(
                f"Discarded {discarded_workspace_count} abandoned article workspace(s)."
            ))
        if deleted_count:
            self.stdout.write(self.style.SUCCESS(
                f"Deleted {deleted_count} stray upload file(s), "
                f"freeing {round(deleted_size_bytes / 1024, 1)} KB."
            ))
        if not discarded_workspace_count and not deleted_count:
            self.stdout.write(self.style.WARNING("No workspaces or files were deleted."))

        for error in workspace_errors + errors:
            self.stderr.write(self.style.ERROR(error))
