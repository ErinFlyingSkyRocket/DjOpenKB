from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0023_lock_article_managers_out_of_django_admin"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="suggestedarticle",
            options={
                "ordering": ["-updated_at", "-created_at"],
                "verbose_name": _("Suggested Article"),
                "verbose_name_plural": _("Suggested Articles"),
            },
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE kb_sitesetting "
                        "ADD COLUMN IF NOT EXISTS admin_allowed_cidrs text "
                        "NOT NULL DEFAULT '10.65.0.0/16,127.0.0.1/32,::1/128';"
                    ),
                    reverse_sql=(
                        "ALTER TABLE kb_sitesetting "
                        "DROP COLUMN IF EXISTS admin_allowed_cidrs;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="sitesetting",
                    name="admin_allowed_cidrs",
                    field=models.TextField(
                        default="10.65.0.0/16,127.0.0.1/32,::1/128",
                        help_text=_(
                            "Comma or newline separated CIDR/IP allowlist for Django Admin access. "
                            "Default allows 10.65.0.0/16 and local loopback. "
                            "Users outside this range receive 404 even if they know the admin URL. "
                            "Nginx may also enforce a separate outer allowlist in nginx/nginx.conf."
                        ),
                        verbose_name=_("Admin allowed IP ranges"),
                    ),
                ),
            ],
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="stray_upload_cleanup_min_age_minutes",
            field=models.PositiveIntegerField(
                default=1440,
                help_text=_(
                    "Files newer than this many minutes are ignored by the stray upload cleanup tool. "
                    "Default is 1440 minutes (24 hours) to avoid deleting images while users are drafting articles. "
                    "Set to 0 to detect/delete stray uploads immediately."
                ),
                verbose_name=_("Stray upload cleanup minimum age (minutes)"),
            ),
        ),
    ]
