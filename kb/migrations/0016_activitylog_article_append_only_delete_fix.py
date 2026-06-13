from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0015_append_only_audit_logs"),
    ]

    operations = [
        migrations.AlterField(
            model_name="activitylog",
            name="article",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text=(
                    "Snapshot fields keep the audit trail after an article is deleted. "
                    "This relation intentionally does not enforce a database constraint because audit logs are append-only."
                ),
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="activity_logs",
                to="kb.suggestedarticle",
            ),
        ),
    ]
