from django.core.management.base import BaseCommand
from django.utils import timezone

from kb.models import SuggestedArticle, SiteSetting
from kb.views.services import purge_article_from_deletion_queue


class Command(BaseCommand):
    help = "Permanently delete articles whose deletion-queue recovery period has expired."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many queued articles would be permanently deleted without deleting them.",
        )
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="Run without confirmation prompts. Intended for Docker scheduler/cron.",
        )
        parser.add_argument(
            "--retention-days",
            type=int,
            default=None,
            help=(
                "Override Django Admin setting for this run. Queued articles older than this many days are purged. "
                "Use 0 to purge all queued articles immediately."
            ),
        )

    def handle(self, *args, **options):
        now = timezone.now()
        retention_days = options.get("retention_days")

        if retention_days is None:
            retention_days = SiteSetting.load().article_deletion_queue_retention_days

        retention_days = max(int(retention_days), 0)
        cutoff = now - timezone.timedelta(days=retention_days)

        # If retention is 0, all queued articles are due immediately. This also
        # lets admins purge older queued rows as soon as they change the setting
        # from a recovery period to immediate permanent deletion.
        if retention_days <= 0:
            queryset = SuggestedArticle.objects.select_related("owner", "deletion_queued_by").filter(
                status=SuggestedArticle.Status.DELETE_QUEUED,
            )
            fallback_queryset = SuggestedArticle.objects.none()
        else:
            # Prefer the saved purge_after timestamp. The queued_at fallback handles
            # any legacy row that somehow has a queued_at value but no purge_after.
            queryset = SuggestedArticle.objects.select_related("owner", "deletion_queued_by").filter(
                status=SuggestedArticle.Status.DELETE_QUEUED,
            ).filter(
                deletion_purge_after__lte=now,
            )
            fallback_queryset = SuggestedArticle.objects.select_related("owner", "deletion_queued_by").filter(
                status=SuggestedArticle.Status.DELETE_QUEUED,
                deletion_purge_after__isnull=True,
                deletion_queued_at__lte=cutoff,
            )

        article_ids = list(queryset.values_list("id", flat=True))
        article_ids.extend(list(fallback_queryset.values_list("id", flat=True)))
        article_ids = sorted(set(article_ids))
        purge_count = len(article_ids)

        self.stdout.write(
            f"Deletion queue cleanup scan complete. Retention: {retention_days} day(s). "
            f"Found: {purge_count} queued article(s) ready for permanent deletion."
        )

        if not purge_count:
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run only. No articles were permanently deleted."))
            return

        if not options["noinput"]:
            answer = input("Permanently delete all due queued articles? Type yes to continue: ").strip().lower()
            if answer != "yes":
                self.stdout.write(self.style.WARNING("Deletion queue cleanup cancelled."))
                return

        purged = 0
        failed = []
        for article in SuggestedArticle.objects.select_related("owner", "deletion_queued_by").filter(id__in=article_ids):
            title = article.title
            try:
                if purge_article_from_deletion_queue(
                    None,
                    article,
                    automatic=True,
                    source="cleanup_article_deletion_queue",
                ):
                    purged += 1
            except Exception as exc:
                failed.append(title)
                self.stderr.write(self.style.ERROR(f"Failed to permanently delete '{title}': {exc}"))

        if purged:
            self.stdout.write(self.style.SUCCESS(f"Permanently deleted {purged} queued article(s)."))
        if failed:
            self.stdout.write(self.style.WARNING(f"{len(failed)} queued article(s) could not be permanently deleted."))
