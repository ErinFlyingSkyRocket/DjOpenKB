from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0011_articlevote"),
    ]

    operations = [
        migrations.AlterField(
            model_name="suggestedarticle",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending", "Pending"),
                    ("published", "Published"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
    ]
