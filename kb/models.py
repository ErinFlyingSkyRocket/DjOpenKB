from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import re

from .crypto import decrypt_value, encrypt_value, is_encrypted_value


def normalize_article_title(title):
    """Normalize article titles so duplicates are caught case-insensitively.

    This intentionally ignores leading/trailing spaces, repeated internal
    whitespace, and letter case, so these are treated as the same title:
    "My Article", " my   article ", and "MY ARTICLE".
    """
    return re.sub(r"\s+", " ", (title or "").strip()).casefold()


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

    class AuthSource(models.TextChoices):
        LOCAL = "local", _("Local user")
        AD = "ad", _("Active Directory user")

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
    auth_source = models.CharField(
        max_length=20,
        choices=AuthSource.choices,
        default=AuthSource.LOCAL,
        help_text="Controls whether the password is managed locally in DjOpenKB or externally by Active Directory.",
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

    @property
    def is_ad_managed(self):
        return self.auth_source == self.AuthSource.AD

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


class UserMFADevice(models.Model):
    """TOTP authenticator device for DjOpenKB users.

    MFA is enforced as a login criterion for both local Django accounts and
    LDAP/AD accounts. Users must have one confirmed device before using the
    protected site.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="kb_mfa_device",
    )
    # Stored encrypted at rest. Use get_secret()/set_secret() instead of
    # reading/writing this field directly.
    secret = models.CharField(max_length=512)
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    reset_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "User MFA device"
        verbose_name_plural = "User MFA devices"

    def __str__(self):
        status = "confirmed" if self.confirmed else "setup pending"
        return f"{self.user.username} MFA ({status})"

    def get_secret(self):
        return decrypt_value(self.secret)

    def set_secret(self, raw_secret):
        self.secret = encrypt_value(raw_secret)

    @property
    def secret_is_encrypted(self):
        return is_encrypted_value(self.secret)

    def save(self, *args, **kwargs):
        if self.secret and not is_encrypted_value(self.secret):
            self.secret = encrypt_value(self.secret)
        super().save(*args, **kwargs)

    def mark_confirmed(self):
        now = timezone.now()
        self.confirmed = True
        self.confirmed_at = now
        self.last_verified_at = now
        self.save(update_fields=["confirmed", "confirmed_at", "last_verified_at"])

    def mark_verified(self):
        self.last_verified_at = timezone.now()
        self.save(update_fields=["last_verified_at"])


class AuthActivityLog(models.Model):
    """Security/audit events for login and MFA monitoring.

    This is used by admins to spot repeated failed password attempts, repeated
    MFA/OTP failures, and MFA reset activity from the Django admin site.
    """

    class EventType(models.TextChoices):
        PASSWORD_SUCCESS = "password_success", _("Password login success")
        PASSWORD_FAILURE = "password_failure", _("Password login failure")
        PENDING_MFA = "pending_mfa", _("Pending MFA created")
        MFA_SETUP_SUCCESS = "mfa_setup_success", _("MFA setup success")
        MFA_SETUP_FAILURE = "mfa_setup_failure", _("MFA setup failure")
        MFA_VERIFY_SUCCESS = "mfa_verify_success", _("MFA verify success")
        MFA_VERIFY_FAILURE = "mfa_verify_failure", _("MFA verify failure")
        MFA_RESET_SELF = "mfa_reset_self", _("MFA reset by user")
        MFA_RESET_ADMIN = "mfa_reset_admin", _("MFA reset by admin")
        LOGOUT = "logout", _("Logout")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)
    success = models.BooleanField(default=False, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auth_activity_logs",
    )
    username = models.CharField(max_length=255, blank=True, db_index=True)
    login_mode = models.CharField(max_length=30, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.TextField(blank=True)
    path = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Authentication activity log"
        verbose_name_plural = "Authentication activity logs"
        indexes = [
            models.Index(fields=["-created_at", "event_type"]),
            models.Index(fields=["ip_address", "-created_at"]),
            models.Index(fields=["username", "-created_at"]),
        ]

    def __str__(self):
        user_label = self.username or (self.user.get_username() if self.user_id else "unknown")
        return f"{self.get_event_type_display()} - {user_label} - {self.created_at:%Y-%m-%d %H:%M:%S}"


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
        verbose_name=_("Current pending failed comments"),
        help_text="Current admin feedback shown to the article owner while the article is in Draft or Pending failed status.",
    )
    review_notes_history = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Pending failed comments history"),
        help_text="Historical review feedback entries from previous rejection/resubmission rounds.",
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

    def clean(self):
        """Prevent duplicate article titles across drafts, pending, failed, and published articles."""
        super().clean()

        normalized_title = normalize_article_title(self.title)
        if not normalized_title:
            return

        queryset = SuggestedArticle.objects.all()
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)

        for article in queryset.only("id", "title"):
            if normalize_article_title(article.title) == normalized_title:
                raise ValidationError({
                    "title": _("An article with this title already exists. Please use a different title.")
                })


    def add_review_note_history(self, note, reviewer=None, action="pending_failed"):
        """Append a review note to history while avoiding exact duplicate consecutive entries."""
        note = (note or "").strip()
        if not note:
            return False

        history = list(self.review_notes_history or [])
        reviewer_label = "System"
        reviewer_id = None

        if reviewer is not None and getattr(reviewer, "is_authenticated", False):
            reviewer_id = reviewer.pk
            reviewer_label = reviewer.get_username() or getattr(reviewer, "email", "") or f"User {reviewer.pk}"

        entry = {
            "note": note,
            "action": action,
            "status": self.status,
            "reviewer": reviewer_label,
            "reviewer_id": reviewer_id,
            "created_at": timezone.now().isoformat(),
        }

        if history:
            last_entry = history[-1]
            if (
                last_entry.get("note") == entry["note"]
                and last_entry.get("action") == entry["action"]
                and last_entry.get("status") == entry["status"]
            ):
                return False

        history.append(entry)
        self.review_notes_history = history[-50:]
        return True

    def archive_current_review_note(self, actor=None, action="cleared"):
        """Move the current review note into history before clearing it."""
        return self.add_review_note_history(self.review_notes, reviewer=actor, action=action)

    @property
    def public_url(self):
        if not self.pk:
            return "#"
        return reverse("article_detail", kwargs={"article_id": self.pk})

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


class ArticleImageUploadLog(models.Model):
    """Audit record for images uploaded through the article editor.

    The uploaded file may be saved into an article later, deleted by the user,
    or removed by stray-file cleanup. Keeping a separate audit record lets
    admins see who originally uploaded a stray file even if it is not currently
    linked to any article.
    """

    class DeleteReason(models.TextChoices):
        USER_REMOVED = "user_removed", _("Removed by uploader from editor")
        ADMIN_CLEANUP = "admin_cleanup", _("Deleted by admin stray-file cleanup")
        AUTO_CLEANUP = "auto_cleanup", _("Deleted by automatic stray-file cleanup")

    filename = models.CharField(max_length=255, unique=True, db_index=True)
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_article_images",
    )
    uploader_username_snapshot = models.CharField(max_length=150, blank=True, db_index=True)
    uploader_email_snapshot = models.EmailField(blank=True)
    uploader_account_type_snapshot = models.CharField(max_length=50, blank=True)
    upload_ip_address = models.GenericIPAddressField(null=True, blank=True)
    upload_user_agent = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now, db_index=True)

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_article_images",
    )
    delete_reason = models.CharField(max_length=30, choices=DeleteReason.choices, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Article image upload log"
        verbose_name_plural = "Article image upload logs"
        indexes = [
            models.Index(fields=["filename"]),
            models.Index(fields=["uploader_username_snapshot", "-uploaded_at"]),
            models.Index(fields=["-uploaded_at"]),
        ]

    def __str__(self):
        uploader = self.uploader_username_snapshot or "unknown user"
        return f"{self.filename} uploaded by {uploader}"

    @property
    def uploader_display(self):
        if self.uploaded_by:
            full_name = self.uploaded_by.get_full_name().strip()
            if full_name:
                return f"{full_name} ({self.uploaded_by.get_username()})"
            return self.uploaded_by.get_username()
        return self.uploader_username_snapshot or ""

    def snapshot_uploader(self):
        if not self.uploaded_by:
            return

        self.uploader_username_snapshot = self.uploaded_by.get_username()
        self.uploader_email_snapshot = self.uploaded_by.email or ""

        profile = getattr(self.uploaded_by, "kb_profile", None)
        if profile:
            self.uploader_account_type_snapshot = profile.get_account_type_display()
        elif self.uploaded_by.is_superuser or self.uploaded_by.is_staff:
            self.uploader_account_type_snapshot = "Admin"
        else:
            self.uploader_account_type_snapshot = ""

    def save(self, *args, **kwargs):
        if self.uploaded_by:
            self.snapshot_uploader()
        super().save(*args, **kwargs)

    def mark_deleted(self, actor=None, reason=""):
        self.deleted_at = timezone.now()
        self.deleted_by = actor if getattr(actor, "pk", None) else None
        self.delete_reason = reason or ""
        self.save(update_fields=["deleted_at", "deleted_by", "delete_reason"])


class ActivityLog(models.Model):
    """General admin audit/activity log for non-authentication actions.

    Authentication and MFA events stay in AuthActivityLog. This table tracks
    article, vote, image, AI, and admin-tool activity so admins can review who
    did what, from where, and when.
    """

    class EventType(models.TextChoices):
        ARTICLE_CREATED = "article_created", "Article created"
        ARTICLE_UPDATED = "article_updated", "Article updated"
        ARTICLE_DELETED = "article_deleted", "Article deleted"
        ARTICLE_STATUS_CHANGED = "article_status_changed", "Article status changed"
        ARTICLE_SUBMITTED = "article_submitted", "Article submitted for approval"
        ARTICLE_APPROVED = "article_approved", "Article approved/published"
        ARTICLE_REJECTED = "article_rejected", "Article marked pending failed"
        ARTICLE_ORPHAN_ASSIGNED = "article_orphan_assigned", "Orphan article assigned"
        ARTICLE_ORPHAN_DELETED = "article_orphan_deleted", "Orphan article deleted"
        ARTICLE_VIEWED = "article_viewed", "Article viewed"
        VOTE_UP = "vote_up", "Article vote up"
        VOTE_DOWN = "vote_down", "Article vote down"
        VOTE_UPDATED = "vote_updated", "Article vote changed"
        VOTE_REMOVED = "vote_removed", "Article vote removed"
        IMAGE_UPLOADED = "image_uploaded", "Article image uploaded"
        IMAGE_DELETED = "image_deleted", "Article image deleted"
        AI_QUESTION = "ai_question", "OpenKB AI question"
        AI_RATE_LIMITED = "ai_rate_limited", "OpenKB AI rate limited"
        BULK_IMPORT = "bulk_import", "Bulk article import"
        ADMIN_TOOL_ACTION = "admin_tool_action", "Admin tool action"

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=60, choices=EventType.choices, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity_logs",
    )
    username = models.CharField(max_length=255, blank=True, db_index=True)

    article = models.ForeignKey(
        SuggestedArticle,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity_logs",
    )
    article_title = models.CharField(max_length=255, blank=True, db_index=True)
    article_status = models.CharField(max_length=40, blank=True, db_index=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.TextField(blank=True)
    path = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Activity log"
        verbose_name_plural = "Activity logs"
        indexes = [
            models.Index(fields=["-created_at", "event_type"]),
            models.Index(fields=["username", "-created_at"]),
            models.Index(fields=["article_title", "-created_at"]),
            models.Index(fields=["ip_address", "-created_at"]),
        ]

    def __str__(self):
        actor = self.username or (self.user.get_username() if self.user_id else "anonymous")
        target = f" - {self.article_title}" if self.article_title else ""
        return f"{self.get_event_type_display()} - {actor}{target} - {self.created_at:%Y-%m-%d %H:%M:%S}"


class SiteSetting(models.Model):
    """Singleton-style site settings editable from Django Admin."""

    stray_upload_cleanup_min_age_minutes = models.PositiveIntegerField(
        default=1440,
        verbose_name="Stray upload cleanup minimum age (minutes)",
        help_text=(
            "Files newer than this many minutes are ignored by the stray upload cleanup tool. "
            "Default is 1440 minutes (24 hours) to avoid deleting images while users are drafting articles. "
            "Set to 0 to detect/delete stray uploads immediately."
        ),
    )
    auth_activity_log_retention_days = models.PositiveIntegerField(
        default=30,
        verbose_name="Authentication activity log retention (days)",
        help_text=(
            "Authentication/MFA monitoring logs older than this many days can be deleted by the cleanup command. "
            "Use 0 to keep authentication activity logs indefinitely."
        ),
    )
    session_timeout_days = models.PositiveIntegerField(
        default=30,
        verbose_name="User session timeout (days)",
        help_text=(
            "Authenticated user sessions expire after this many days from sign-in. "
            "After expiry, users are signed out and must log in again. Set to 0 to expire the session when the browser closes."
        ),
    )
    activity_log_retention_days = models.PositiveIntegerField(
        default=30,
        verbose_name="General activity log retention (days)",
        help_text=(
            "Article/vote/image/admin-tool activity logs older than this many days can be deleted by the cleanup command. "
            "Use 0 to keep general activity logs indefinitely."
        ),
    )
    admin_log_rows_per_page = models.PositiveIntegerField(
        default=200,
        verbose_name="Admin log rows per page",
        help_text=(
            "Number of rows to show per page in Django Admin log tables. "
            "Recommended range: 50 to 500. Default is 200."
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
