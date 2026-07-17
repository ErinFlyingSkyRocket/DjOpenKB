from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        "Repair known kb schema drift after development migration consolidation. "
        "This keeps the existing local PostgreSQL data and only adds missing safe columns."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="Run without confirmation prompts. Intended for Docker startup.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(
                    f"repair_kb_schema is intended for PostgreSQL. Current database vendor: {connection.vendor}. Skipping."
                )
            )
            return

        self._repair_suggested_article()
        self._repair_site_setting()
        self.stdout.write(self.style.SUCCESS("KB schema repair check completed."))

    def _column_exists(self, table_name, column_name):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s
                  AND column_name = %s
                LIMIT 1
                """,
                [table_name, column_name],
            )
            return cursor.fetchone() is not None

    def _table_exists(self, table_name):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = %s
                LIMIT 1
                """,
                [table_name],
            )
            return cursor.fetchone() is not None

    def _repair_suggested_article(self):
        table_name = "kb_suggestedarticle"

        if not self._table_exists(table_name):
            self.stdout.write(
                self.style.WARNING(f"Table {table_name} does not exist yet. Run migrate first. Skipping repair.")
            )
            return

        missing_actions = []

        if not self._column_exists(table_name, "review_notes_history"):
            missing_actions.append(
                (
                    "review_notes_history",
                    """
                    ALTER TABLE kb_suggestedarticle
                    ADD COLUMN review_notes_history jsonb NOT NULL DEFAULT '[]'::jsonb
                    """,
                )
            )

        if not missing_actions:
            self.stdout.write("No kb_suggestedarticle schema drift found.")
            return

        with connection.cursor() as cursor:
            for column_name, sql in missing_actions:
                self.stdout.write(f"Adding missing column: {table_name}.{column_name}")
                cursor.execute(sql)

        self.stdout.write(
            self.style.SUCCESS(
                f"Repaired {len(missing_actions)} missing column(s) on {table_name}. Existing article data was preserved."
            )
        )

    def _repair_site_setting(self):
        table_name = "kb_sitesetting"

        if not self._table_exists(table_name):
            self.stdout.write(
                self.style.WARNING(f"Table {table_name} does not exist yet. Run migrate first. Skipping repair.")
            )
            return

        if self._column_exists(table_name, "article_video_max_width_px"):
            self.stdout.write("No kb_sitesetting schema drift found.")
            return

        with connection.cursor() as cursor:
            self.stdout.write(f"Adding missing column: {table_name}.article_video_max_width_px")
            cursor.execute(
                """
                ALTER TABLE kb_sitesetting
                ADD COLUMN article_video_max_width_px integer NOT NULL DEFAULT 360
                CHECK (article_video_max_width_px >= 0)
                """
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Repaired missing kb_sitesetting.article_video_max_width_px column. Existing site settings were preserved."
            )
        )

