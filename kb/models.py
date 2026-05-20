from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class UserProfile(models.Model):
    """Extra main-site account settings for Django's built-in User model.

    We keep Django's default User model so existing users/migrations stay safe.
    The account_type controls how the account should be treated in this wiki.
    """

    class AccountType(models.TextChoices):
        ADMIN = "admin", _("Admin")
        USER = "user", _("User")
        LDAP_USER = "ldap_user", _("LDAP user")
        LDAP_ADMIN = "ldap_admin", _("LDAP admin")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="kb_profile",
    )
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.USER,
        help_text="Admin/LDAP admin accounts can access Django admin when staff status is enabled.",
    )
    can_access_main_site = models.BooleanField(
        default=True,
        help_text="Untick this to block the user from accessing the main wiki site.",
    )
    preferred_language = models.CharField(
        max_length=20,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        help_text="Preferred language for the main wiki user interface.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Main Site User Profile"
        verbose_name_plural = "Main Site User Profiles"

    def __str__(self):
        return f"{self.user.username} ({self.get_account_type_display()})"

    @property
    def is_admin_type(self):
        return self.account_type in {
            self.AccountType.ADMIN,
            self.AccountType.LDAP_ADMIN,
        }

    @property
    def is_ldap_type(self):
        return self.account_type in {
            self.AccountType.LDAP_USER,
            self.AccountType.LDAP_ADMIN,
        }

    def save(self, *args, **kwargs):
        """Keep Django admin permission flags aligned with the selected type.

        - Admin / LDAP admin: staff access is enabled.
        - User / LDAP user: staff and superuser access are removed.
        - Existing createsuperuser accounts stay as superuser admin accounts.
        """
        super().save(*args, **kwargs)

        update_fields = []

        if self.account_type == self.AccountType.ADMIN:
            if not self.user.is_staff:
                self.user.is_staff = True
                update_fields.append("is_staff")
        elif self.account_type == self.AccountType.LDAP_ADMIN:
            if not self.user.is_staff:
                self.user.is_staff = True
                update_fields.append("is_staff")
        else:
            if self.user.is_staff:
                self.user.is_staff = False
                update_fields.append("is_staff")
            if self.user.is_superuser:
                self.user.is_superuser = False
                update_fields.append("is_superuser")

        if update_fields:
            self.user.save(update_fields=update_fields)


class SuggestedArticle(models.Model):
    """User-submitted OpenKB article metadata.

    The actual Markdown is mirrored into openkb-data/raw and
    openkb-data/wiki/sources so the public wiki can still read it normally.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PENDING = "pending", _("Pending")
        FAILED = "failed", _("Pending failed")
        PUBLISHED = "published", _("Published")

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suggested_articles",
    )
    author_username_snapshot = models.CharField(max_length=150, blank=True)
    author_name_snapshot = models.CharField(max_length=255, blank=True)
    author_email_snapshot = models.EmailField(blank=True)
    author_account_type_snapshot = models.CharField(max_length=50, blank=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    keywords = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_articles",
        verbose_name=_("Approved by"),
        help_text="Admin user who approved this article for public display.",
    )
    approved_at = models.DateTimeField(
        verbose_name=_("Approved at"),
        null=True,
        blank=True,
        help_text="Date and time when this article was approved for public display.",
    )
    review_notes = models.TextField(
        blank=True,
        verbose_name=_("Pending failed comments"),
        help_text="Admin feedback shown to the article owner when the article is in Draft or Pending failed status.",
    )
    view_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of unique session views for this article.",
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

    def refresh_author_snapshot(self):
        """Store a copy of the current owner details on the article.

        This keeps author information visible even if the User account is
        deleted later, because owner will become NULL but these snapshot fields
        will remain.
        """
        if not self.owner:
            return

        self.author_username_snapshot = self.owner.get_username()
        self.author_name_snapshot = self.owner.get_full_name().strip()
        self.author_email_snapshot = self.owner.email or ""

        profile = getattr(self.owner, "kb_profile", None)
        if profile:
            self.author_account_type_snapshot = profile.get_account_type_display()
        elif self.owner.is_superuser or self.owner.is_staff:
            self.author_account_type_snapshot = "Admin"
        else:
            self.author_account_type_snapshot = ""

    def save(self, *args, **kwargs):
        if self.owner:
            self.refresh_author_snapshot()

        super().save(*args, **kwargs)

    @property
    def author_display(self):
        if self.owner:
            full_name = self.owner.get_full_name().strip()
            if full_name:
                return full_name
            return self.owner.get_username()

        if self.author_name_snapshot:
            return self.author_name_snapshot

        if self.author_username_snapshot:
            return self.author_username_snapshot

        return "Deleted user"

    @property
    def author_username(self):
        if self.owner:
            return self.owner.get_username()

        return self.author_username_snapshot or "deleted-user"

    @property
    def author_email(self):
        if self.owner:
            return self.owner.email or ""

        return self.author_email_snapshot or ""

    @property
    def author_account_type(self):
        if self.owner:
            profile = getattr(self.owner, "kb_profile", None)
            if profile:
                return profile.get_account_type_display()

            if self.owner.is_superuser or self.owner.is_staff:
                return "Admin"

        return self.author_account_type_snapshot or ""

    @property
    def helpful_vote_count(self):
        return self.votes.filter(value=ArticleVote.VoteValue.UP).count()

    @property
    def unhelpful_vote_count(self):
        return self.votes.filter(value=ArticleVote.VoteValue.DOWN).count()

    @property
    def total_vote_count(self):
        return self.votes.count()


class ArticleVote(models.Model):
    """One helpful/unhelpful vote per user per article."""

    class VoteValue(models.IntegerChoices):
        UP = 1, _("Helpful")
        DOWN = -1, _("Not helpful")

    article = models.ForeignKey(
        SuggestedArticle,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="article_votes",
    )
    value = models.SmallIntegerField(choices=VoteValue.choices)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("article", "user")
        ordering = ["-updated_at"]
        verbose_name = "Article vote"
        verbose_name_plural = "Article votes"

    def __str__(self):
        label = "Helpful" if self.value == self.VoteValue.UP else "Not helpful"
        return f"{self.article.title} - {self.user} - {label}"


class SiteSetting(models.Model):
    """Singleton-style site settings editable from Django Admin."""

    stray_upload_cleanup_min_age_minutes = models.PositiveIntegerField(
        default=30,
        verbose_name="Stray upload cleanup minimum age (minutes)",
        help_text=(
            "Files newer than this many minutes are ignored by the stray upload cleanup tool. "
            "Set to 0 to detect/delete stray uploads immediately."
        ),
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site setting"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        # Keep this model singleton-like: always use primary key 1.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj
