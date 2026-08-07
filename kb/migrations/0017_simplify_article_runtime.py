from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("kb", "0016_article_edit_approval_snapshot"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="articlecreationworkspace",
            name="last_editor_sequence",
        ),
        migrations.RemoveField(
            model_name="articlecreationworkspace",
            name="last_editor_token",
        ),
        migrations.RemoveField(
            model_name="articlecreationworkspace",
            name="revision",
        ),
        migrations.RemoveField(
            model_name="sitesetting",
            name="admin_request_limit_per_minute",
        ),
        migrations.RemoveField(
            model_name="sitesetting",
            name="login_request_limit_per_minute",
        ),
        migrations.RemoveField(
            model_name="sitesetting",
            name="mfa_request_limit_per_minute",
        ),
    ]
