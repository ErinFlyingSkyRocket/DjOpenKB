# Generated manually for DjOpenKB pending update workflow

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0012_encrypt_mfa_secret"),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestedarticle",
            name="pending_update_title",
            field=models.CharField(blank=True, help_text="Edited title waiting for admin approval. The published title remains unchanged until approval.", max_length=200, verbose_name="Pending update title"),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="pending_update_body",
            field=models.TextField(blank=True, help_text="Edited Markdown body waiting for admin approval. The published body remains unchanged until approval.", verbose_name="Pending update body"),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="pending_update_keywords",
            field=models.CharField(blank=True, help_text="Edited keywords waiting for admin approval.", max_length=500, verbose_name="Pending update keywords"),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="pending_update_image_assets",
            field=models.JSONField(blank=True, default=list, help_text="Images referenced by the pending update draft.", verbose_name="Pending update image assets"),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="update_status",
            field=models.CharField(choices=[("none", "No pending update"), ("pending", "Pending update"), ("failed", "Update pending failed")], db_index=True, default="none", max_length=20, verbose_name="Update approval status"),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="update_submitted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Update submitted at"),
        ),
        migrations.AddField(
            model_name="suggestedarticle",
            name="update_reviewed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Update reviewed at"),
        ),
    ]
