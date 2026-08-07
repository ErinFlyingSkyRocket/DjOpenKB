from django.db import migrations, models


def populate_article_approval_snapshots(apps, schema_editor):
    ArticleEditWorkspace = apps.get_model("kb", "ArticleEditWorkspace")
    for workspace in ArticleEditWorkspace.objects.select_related("article").iterator():
        approved_at = getattr(workspace.article, "approved_at", None)
        ArticleEditWorkspace.objects.filter(pk=workspace.pk).update(
            article_approved_at_snapshot=approved_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("kb", "0015_article_edit_workspace"),
    ]

    operations = [
        migrations.AddField(
            model_name="articleeditworkspace",
            name="article_approved_at_snapshot",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text=(
                    "Approval timestamp of the article version used to initialise this checkpoint. "
                    "A newer approval takes precedence over an older open editor."
                ),
                null=True,
            ),
        ),
        migrations.RunPython(
            populate_article_approval_snapshots,
            migrations.RunPython.noop,
        ),
    ]
