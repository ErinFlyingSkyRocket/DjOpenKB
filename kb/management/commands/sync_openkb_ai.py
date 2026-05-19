from django.core.management.base import BaseCommand

from kb.views import sync_openkb_ai_index


class Command(BaseCommand):
    help = "Rebuild OpenKB AI summaries and index.md from published wiki source Markdown files."

    def handle(self, *args, **options):
        sync_openkb_ai_index()
        self.stdout.write(self.style.SUCCESS("OpenKB AI index synced from wiki/sources."))
