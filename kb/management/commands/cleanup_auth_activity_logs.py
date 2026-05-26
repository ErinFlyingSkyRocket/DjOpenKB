from django.core.management.base import BaseCommand
from django.utils import timezone

from kb.models import AuthActivityLog, SiteSetting


class Command(BaseCommand):
    help = (
        "Delete old authentication/MFA activity logs based on the retention "
        "period configured in Django Admin → Site settings."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=None,
            help=(
                "Override Django Admin setting. Logs older than this many days are deleted. "
                "Use 0 to keep logs indefinitely."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many logs would be deleted without deleting them.",
        )
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="Run without confirmation prompts. Intended for Docker scheduler/cron.",
        )

    def handle(self, *args, **options):
        retention_days = options["retention_days"]
        dry_run = options["dry_run"]
        noinput = options["noinput"]

        if retention_days is None:
            retention_days = SiteSetting.load().auth_activity_log_retention_days

        retention_days = max(int(retention_days), 0)

        if retention_days == 0:
            self.stdout.write(
                self.style.WARNING(
                    "Authentication activity log cleanup skipped. Retention is set to 0 days, so logs are kept indefinitely."
                )
            )
            return

        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        queryset = AuthActivityLog.objects.filter(created_at__lt=cutoff)
        delete_count = queryset.count()

        self.stdout.write(
            f"Authentication activity log cleanup scan complete. "
            f"Retention: {retention_days} day(s). "
            f"Cutoff: {cutoff:%Y-%m-%d %H:%M:%S %Z}. "
            f"Found: {delete_count} old log(s)."
        )

        if not delete_count:
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only. No logs were deleted."))
            return

        if not noinput:
            answer = input("Delete all old authentication activity logs? Type yes to continue: ").strip().lower()
            if answer != "yes":
                self.stdout.write(self.style.WARNING("Cleanup cancelled."))
                return

        deleted, _detail = queryset.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} old authentication activity log row(s)."))
