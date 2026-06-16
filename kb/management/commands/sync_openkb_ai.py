from django.core.management.base import BaseCommand, CommandError

from kb.models import SuggestedArticle
from kb.views.services import get_openkb_internal_data_dir, sync_internal_openkb_ai_index, sync_openkb_ai_index
from django.conf import settings


class Command(BaseCommand):
    help = "Rebuild OpenKB AI summaries and indexes from published article Markdown."

    def add_arguments(self, parser):
        parser.add_argument(
            "--scope",
            choices=("public", "internal", "all"),
            default="all",
            help=(
                "Which OpenKB AI index to rebuild. "
                "public = public articles only; internal = public + internal articles; all = both."
            ),
        )

    def handle(self, *args, **options):
        scope = options["scope"]
        public_count = SuggestedArticle.objects.filter(
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.PUBLIC,
        ).count()
        internal_count = SuggestedArticle.objects.filter(
            status=SuggestedArticle.Status.PUBLISHED,
            visibility=SuggestedArticle.Visibility.INTERNAL,
        ).count()

        if scope in {"public", "all"}:
            sync_openkb_ai_index()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Public OpenKB AI index synced at {settings.OPENKB_DATA_DIR} "
                    f"({public_count} published public article(s))."
                )
            )

        if scope in {"internal", "all"}:
            internal_data_dir = sync_internal_openkb_ai_index()
            if internal_data_dir != get_openkb_internal_data_dir():
                raise CommandError("Internal OpenKB data directory resolved unexpectedly.")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Internal OpenKB AI index synced at {internal_data_dir} "
                    f"({public_count} public + {internal_count} internal published article(s))."
                )
            )
