from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from kb.models import ActivityLog, AdminActivityLog, SiteSetting


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
        activity_queryset = ActivityLog.objects.filter(created_at__lt=cutoff)
        admin_queryset = AdminActivityLog.objects.filter(created_at__lt=cutoff)
        activity_count = activity_queryset.count()
        admin_count = admin_queryset.count()
        count = activity_count + admin_count

        if options["dry_run"]:
            self.stdout.write(
                f"Would delete {activity_count} general activity log(s) and "
                f"{admin_count} admin activity log(s) older than {days} day(s)."
            )
            return

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL djopenkb.audit_retention_cleanup = 'on'")
            activity_deleted, _ = activity_queryset.delete()
            admin_deleted, _ = admin_queryset.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {activity_deleted} general activity log row(s) and "
                f"{admin_deleted} admin activity log row(s) older than {days} day(s)."
            )
        )
