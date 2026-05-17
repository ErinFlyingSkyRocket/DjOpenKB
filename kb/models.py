from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class SuggestedArticle(models.Model):
    """User-submitted OpenKB article metadata.

    The actual Markdown is mirrored into openkb-data/raw and
    openkb-data/wiki/sources so the public wiki can still read it normally.
    """

    class Status(models.TextChoices):
        PUBLISHED = "published", "Published"
        DRAFT = "draft", "Draft"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="suggested_articles",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    keywords = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PUBLISHED,
    )
    filename = models.CharField(max_length=255, unique=True, blank=True)
    raw_path = models.CharField(max_length=500, blank=True)
    wiki_path = models.CharField(max_length=500, blank=True)
    image_assets = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = "Suggested Article"
        verbose_name_plural = "Suggested Articles"

    def __str__(self):
        return self.title

    @property
    def public_url(self):
        if not self.wiki_path:
            return "#"
        return reverse("wiki_detail", kwargs={"wiki_path": self.wiki_path})

    @property
    def keyword_list(self):
        return [item.strip() for item in self.keywords.split(",") if item.strip()]

    @property
    def author_display(self):
        full_name = self.owner.get_full_name().strip()
        if full_name:
            return full_name
        return self.owner.get_username()

    @property
    def author_email(self):
        return self.owner.email or ""
