from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from kb.models import ActivityLog, SiteSetting


class Command(BaseCommand):
    help = "Delete old general activity logs based on Site settings retention."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override Site settings retention days for this run. Use 0 to keep all logs.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many logs would be deleted without deleting them.",
        )
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="Accepted for scheduler compatibility. This command does not prompt.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = SiteSetting.load().activity_log_retention_days

        if days <= 0:
            self.stdout.write(self.style.SUCCESS("General activity log retention is disabled; no logs deleted."))
            return

        cutoff = timezone.now() - timedelta(days=days)
        queryset = ActivityLog.objects.filter(created_at__lt=cutoff)
        count = queryset.count()

        if options["dry_run"]:
            self.stdout.write(f"Would delete {count} general activity log(s) older than {days} day(s).")
            return

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL djopenkb.audit_retention_cleanup = 'on'")
            deleted, _ = queryset.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} general activity log row(s) older than {days} day(s)."))
