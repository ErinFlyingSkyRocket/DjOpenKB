from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import connection, models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import re
import uuid

from .crypto import decrypt_value, encrypt_value, is_encrypted_value


def normalize_article_title(title):
    """Normalize article titles so duplicates are caught case-insensitively.

    This intentionally ignores leading/trailing spaces, repeated internal
    whitespace, and letter case, so these are treated as the same title:
    "My Article", " my   article ", and "MY ARTICLE".
    """
    return re.sub(r"\s+", " ", (title or "").strip()).casefold()


ARTICLE_KEYWORD_DEFAULT_LIMIT = 20
ARTICLE_KEYWORD_MIN_LIMIT = 1
ARTICLE_KEYWORD_MAX_LIMIT = 100
ARTICLE_KEYWORD_MAX_TOTAL_LENGTH = 500

ARTICLE_BODY_DEFAULT_CHARACTER_LIMIT = 100_000
ARTICLE_BODY_MIN_CHARACTER_LIMIT = 1_000
ARTICLE_BODY_MAX_CHARACTER_LIMIT = 2_000_000
ARTICLE_NORMALIZED_TITLE_MAX_LENGTH = 600
ARTICLE_REVIEW_NOTES_MAX_LENGTH = 4_000
ARTICLE_REVIEW_HISTORY_MAX_ENTRIES = 50
PENDING_IMAGE_UPLOAD_DEFAULT_COUNT_LIMIT = 100
PENDING_IMAGE_UPLOAD_DEFAULT_BYTE_LIMIT_MB = 100


def split_article_keywords(value):
    """Return trimmed keyword values from comma, semicolon, or newline input."""
    return [
        item.strip()
        for item in re.split(r"[,;\n]+", str(value or ""))
        if item.strip()
    ]


def normalize_article_keywords(value):
    """Return a stable comma-separated keyword string without duplicates."""
    keywords = []
    seen = set()
    for keyword in split_article_keywords(value):
        comparison_value = keyword.casefold()
        if comparison_value in seen:
            continue
        seen.add(comparison_value)
        keywords.append(keyword)
    return ", ".join(keywords)


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
        help_text=_("Admin/LDAP admin accounts can access Django admin when staff status is enabled."),
    )
    auth_source = models.CharField(
        max_length=20,
        choices=AuthSource.choices,
        default=AuthSource.LOCAL,
        help_text=_("Controls whether the password is managed locally in Knowledge Repository or externally by Active Directory."),
    )
    can_access_main_site = models.BooleanField(
        default=True,
        help_text=_("Untick this to block the user from accessing the main wiki site."),
    )
    preferred_language = models.CharField(
        max_length=20,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        help_text=_("Preferred language for the main wiki user interface."),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Main Site User Profile")
        verbose_name_plural = _("Main Site User Profiles")

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

    @classmethod
    def expected_auth_source_for_account_type(cls, account_type):
        """Return the authentication source that matches a profile account type."""
        if account_type in {cls.AccountType.LDAP_USER, cls.AccountType.LDAP_ADMIN}:
            return cls.AuthSource.AD
        return cls.AuthSource.LOCAL

    def clean(self):
        """Prevent confusing local/LDAP profile combinations in Django Admin.

        Local account types must use the local authentication source, while
        LDAP account types must use the Active Directory source. This keeps
        display labels, password handling, and Admin Users role sync aligned.
        """
        super().clean()
        expected_source = self.expected_auth_source_for_account_type(self.account_type)
        if self.auth_source != expected_source:
            if expected_source == self.AuthSource.AD:
                raise ValidationError({
                    "auth_source": _(
                        "LDAP account types must use Active Directory as the authentication source."
                    )
                })
            raise ValidationError({
                "auth_source": _(
                    "Local account types must use Local user as the authentication source."
                )
            })

    def save(self, *args, **kwargs):
        """Keep the profile label and admin role in sync.

        The Admin Users group is the source of truth for Django Admin access.
        Editing this profile to a local/LDAP admin type adds the Admin Users
        group. Editing it back to a local/LDAP user removes the Admin Users
        group and the user's staff/superuser flags are cleared automatically.
        """
        syncing_from_roles = getattr(self, "_djopenkb_syncing_from_roles", False)
        super().save(*args, **kwargs)

        if syncing_from_roles or not getattr(self.user, "pk", None):
            return

        try:
            from .permissions import (
                ROLE_ADMIN_USERS,
                ROLE_REGULAR_USER,
                assign_single_role_group,
                sync_user_staff_flags_from_roles,
                user_has_disabled_role,
            )

            if user_has_disabled_role(self.user):
                sync_user_staff_flags_from_roles(self.user)
                return

            if self.is_admin_type:
                assign_single_role_group(self.user, ROLE_ADMIN_USERS, clear_direct_permissions=True)
            elif self.user.groups.filter(name=ROLE_ADMIN_USERS).exists():
                assign_single_role_group(self.user, ROLE_REGULAR_USER)
            else:
                sync_user_staff_flags_from_roles(self.user)
        except Exception:
            # Do not break migrations or user saves if auth/group tables are not
            # ready yet during initial deployment.
            return


class UserMFADevice(models.Model):
    """TOTP authenticator device for Knowledge Repository users.

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
        verbose_name = _("User MFA device")
        verbose_name_plural = _("User MFA devices")

    def __str__(self):
        status = _("confirmed") if self.confirmed else _("setup pending")
        return _("%(username)s MFA (%(status)s)") % {"username": self.user.username, "status": status}

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


class AppendOnlyAuditLogMixin:
    """Application-level guard for audit log rows.

    Log records may be inserted, but existing records must not be edited or
    manually deleted. Retention cleanup uses database-level protection so that
    scheduled deletion of expired logs can still run according to Site settings.
    """

    immutable_update_message = _("Audit log records are append-only and cannot be edited.")
    immutable_delete_message = _("Audit log records cannot be manually deleted. They are removed only by retention cleanup.")

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError(self.immutable_update_message)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(self.immutable_delete_message)


class AuthActivityLog(AppendOnlyAuditLogMixin, models.Model):
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
        ADMIN_MFA_VERIFY_SUCCESS = "admin_mfa_verify_success", _("Django Admin MFA verification success")
        ADMIN_MFA_VERIFY_FAILURE = "admin_mfa_verify_failure", _("Django Admin MFA verification failure")
        MFA_RESET_SELF = "mfa_reset_self", _("MFA reset by user")
        MFA_RESET_ADMIN = "mfa_reset_admin", _("MFA reset by admin")
        AUTH_LOCKOUT_TRIGGERED = "auth_lockout_triggered", _("Authentication lockout triggered")
        ADMIN_MFA_LOCKOUT_TRIGGERED = "admin_mfa_lockout_triggered", _("Django Admin MFA lockout triggered")
        AUTH_LOCKOUT_RESET_ADMIN = "auth_lockout_reset_admin", _("Authentication lockout reset by admin")
        LOGOUT = "logout", _("Logout")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)
    success = models.BooleanField(default=False, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        db_constraint=False,
        help_text=_(
            "Historical user ID snapshot only. This relation intentionally does not enforce "
            "a database constraint because audit logs must remain immutable when users are deleted."
        ),
        on_delete=models.DO_NOTHING,
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
        verbose_name = _("Authentication activity log")
        verbose_name_plural = _("Authentication activity logs")
        indexes = [
            models.Index(fields=["-created_at", "event_type"], name="kb_authacti_created_9c7968_idx"),
            models.Index(fields=["ip_address", "-created_at"], name="kb_authacti_ip_addr_f1d0bb_idx"),
            models.Index(fields=["username", "-created_at"], name="kb_authacti_usernam_0b8fd5_idx"),
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
        DELETE_QUEUED = "delete_queued", _("Deletion queued")

    class UpdateStatus(models.TextChoices):
        NONE = "none", _("No pending update")
        PENDING = "pending", _("Pending update")
        FAILED = "failed", _("Update pending failed")

    class Visibility(models.TextChoices):
        PUBLIC = "public", _("Public article")
        INTERNAL = "internal", _("Internal article")

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
    normalized_title = models.CharField(
        max_length=ARTICLE_NORMALIZED_TITLE_MAX_LENGTH,
        unique=True,
        editable=False,
        verbose_name=_("Normalized article title"),
        help_text=_("Internal case-insensitive title key used to prevent duplicate article titles."),
    )
    body = models.TextField()
    keywords = models.CharField(max_length=500, blank=True)
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        db_index=True,
        verbose_name=_("Article visibility"),
        help_text=_("Public articles are visible to normal wiki users. Internal articles are visible only to users with internal article access."),
    )
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
        help_text=_("Admin user who approved this article for public display."),
    )
    approved_at = models.DateTimeField(
        verbose_name=_("Approved at"),
        null=True,
        blank=True,
        help_text=_("Date and time when this article was approved for public display."),
    )
    review_notes = models.TextField(
        blank=True,
        verbose_name=_("Current pending failed comments"),
        help_text=_("Current admin feedback shown to the article owner while the article is in Draft or Pending failed status."),
    )
    review_notes_history = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Pending failed comments history"),
        help_text=_("Historical review feedback entries from previous rejection/resubmission rounds."),
    )
    pending_update_title = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Pending update title"),
        help_text=_("Edited title waiting for admin approval. The published title remains unchanged until approval."),
    )
    pending_update_body = models.TextField(
        blank=True,
        verbose_name=_("Pending update body"),
        help_text=_("Edited Markdown body waiting for admin approval. The published body remains unchanged until approval."),
    )
    pending_update_keywords = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Pending update keywords"),
        help_text=_("Edited keywords waiting for admin approval."),
    )
    pending_update_image_assets = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Pending update image assets"),
        help_text=_("Images referenced by the pending update draft."),
    )
    update_status = models.CharField(
        max_length=20,
        choices=UpdateStatus.choices,
        default=UpdateStatus.NONE,
        db_index=True,
        verbose_name=_("Update approval status"),
    )
    update_submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Update submitted at"),
    )
    update_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Update reviewed at"),
    )
    view_count = models.PositiveIntegerField(default=0)
    filename = models.CharField(max_length=255, unique=True, blank=True)
    raw_path = models.CharField(max_length=500, blank=True)
    wiki_path = models.CharField(max_length=500, blank=True)
    image_assets = models.JSONField(default=list, blank=True)
    deletion_previous_status = models.CharField(
        max_length=20,
        choices=Status.choices,
        blank=True,
        db_index=True,
        verbose_name=_("Previous status before deletion queue"),
        help_text=_("Original workflow status saved so the article can be restored from the deletion queue."),
    )
    deletion_queued_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Deletion queued at"),
    )
    deletion_queued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="queued_article_deletions",
        verbose_name=_("Deletion queued by"),
    )
    deletion_purge_after = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Permanent deletion after"),
        help_text=_("After this time, the queued article can be permanently deleted by the scheduled cleanup."),
    )
    deletion_restored_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Deletion restored at"),
    )
    deletion_restored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="restored_article_deletions",
        verbose_name=_("Deletion restored by"),
    )
    deletion_reason = models.TextField(
        blank=True,
        verbose_name=_("Deletion reason"),
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = _("Suggested Article")
        verbose_name_plural = _("Suggested Articles")

    def __str__(self):
        return self.title

    def full_clean(self, exclude=None, validate_unique=True, validate_constraints=True):
        """Populate the derived title key before Django validates model fields."""
        self.normalized_title = normalize_article_title(self.title)
        return super().full_clean(
            exclude=exclude,
            validate_unique=validate_unique,
            validate_constraints=validate_constraints,
        )

    def clean(self):
        """Validate article bodies, keywords, and duplicate article titles."""
        super().clean()

        if len((self.title or "").strip()) < 5:
            raise ValidationError({"title": _("Article title must contain at least 5 characters.")})
        if len((self.body or "").strip()) < 5:
            raise ValidationError({"body": _("Article body must contain at least 5 characters.")})

        try:
            self.body = validate_article_body(self.body)
        except ValidationError as error:
            raise ValidationError({"body": error.messages}) from error

        try:
            self.pending_update_body = validate_article_body(
                self.pending_update_body
            )
        except ValidationError as error:
            raise ValidationError(
                {"pending_update_body": error.messages}
            ) from error

        try:
            self.keywords = validate_article_keywords(self.keywords)
        except ValidationError as error:
            raise ValidationError({"keywords": error.messages}) from error

        try:
            self.pending_update_keywords = validate_article_keywords(
                self.pending_update_keywords
            )
        except ValidationError as error:
            raise ValidationError(
                {"pending_update_keywords": error.messages}
            ) from error

        self.normalized_title = normalize_article_title(self.title)
        if not self.normalized_title:
            raise ValidationError({"title": _("Enter an article title.")})

        queryset = SuggestedArticle.objects.filter(normalized_title=self.normalized_title)
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)
        if queryset.exists():
            raise ValidationError({
                "title": _("An article with this title already exists. Please use a different title.")
            })

        if len(self.review_notes or "") > ARTICLE_REVIEW_NOTES_MAX_LENGTH:
            raise ValidationError({
                "review_notes": _("Review comments cannot exceed %(limit)s characters.")
                % {"limit": ARTICLE_REVIEW_NOTES_MAX_LENGTH}
            })

        history = self.review_notes_history or []
        if not isinstance(history, list):
            raise ValidationError({"review_notes_history": _("Review comment history must be a list.")})
        if len(history) > ARTICLE_REVIEW_HISTORY_MAX_ENTRIES:
            raise ValidationError({
                "review_notes_history": _("Review comment history cannot contain more than %(limit)s entries.")
                % {"limit": ARTICLE_REVIEW_HISTORY_MAX_ENTRIES}
            })
        allowed_history_keys = {"note", "action", "status", "reviewer", "reviewer_id", "created_at"}
        for index, entry in enumerate(history, start=1):
            if not isinstance(entry, dict):
                raise ValidationError({
                    "review_notes_history": _("Review comment history entry %(index)s must be an object.")
                    % {"index": index}
                })
            if set(entry) - allowed_history_keys:
                raise ValidationError({
                    "review_notes_history": _("Review comment history entry %(index)s contains unsupported fields.")
                    % {"index": index}
                })
            note = entry.get("note", "")
            if not isinstance(note, str) or not note.strip() or len(note) > ARTICLE_REVIEW_NOTES_MAX_LENGTH:
                raise ValidationError({
                    "review_notes_history": _("Review comment history entry %(index)s has an invalid note.")
                    % {"index": index}
                })
            for key, limit in (("action", 40), ("status", 20), ("reviewer", 255), ("created_at", 64)):
                value = entry.get(key, "")
                if value is not None and (not isinstance(value, str) or len(value) > limit):
                    raise ValidationError({
                        "review_notes_history": _("Review comment history entry %(index)s has an invalid %(field)s value.")
                        % {"index": index, "field": key}
                    })
            reviewer_id = entry.get("reviewer_id")
            if reviewer_id is not None and (not isinstance(reviewer_id, int) or isinstance(reviewer_id, bool) or reviewer_id < 1):
                raise ValidationError({
                    "review_notes_history": _("Review comment history entry %(index)s has an invalid reviewer ID.")
                    % {"index": index}
                })

        pending_update_has_content = any((
            (self.pending_update_title or "").strip(),
            (self.pending_update_body or "").strip(),
            (self.pending_update_keywords or "").strip(),
            self.pending_update_image_assets or [],
        ))
        supports_pending_update = self.status == self.Status.PUBLISHED or (
            self.status == self.Status.DELETE_QUEUED
            and self.deletion_previous_status == self.Status.PUBLISHED
        )
        if not supports_pending_update:
            if self.update_status != self.UpdateStatus.NONE:
                raise ValidationError({
                    "update_status": _("Only published articles can have a pending article update.")
                })
            if pending_update_has_content:
                raise ValidationError({
                    "pending_update_title": _("Only published articles can store an unpublished update draft.")
                })
        if self.update_status != self.UpdateStatus.NONE or pending_update_has_content:
            if len((self.pending_update_title or "").strip()) < 5:
                raise ValidationError({
                    "pending_update_title": _("A pending article update title must contain at least 5 characters.")
                })
            if len((self.pending_update_body or "").strip()) < 5:
                raise ValidationError({
                    "pending_update_body": _("A pending article update body must contain at least 5 characters.")
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
    def is_internal(self):
        return self.visibility == self.Visibility.INTERNAL

    @property
    def is_public(self):
        return self.visibility == self.Visibility.PUBLIC

    @property
    def visibility_label(self):
        return self.get_visibility_display()

    @property
    def is_deletion_queued(self):
        return self.status == self.Status.DELETE_QUEUED

    @property
    def deletion_original_status(self):
        status_value = self.deletion_previous_status or self.Status.DRAFT
        valid_values = {value for value, _label in self.Status.choices}
        if status_value == self.Status.DELETE_QUEUED or status_value not in valid_values:
            return self.Status.DRAFT
        return status_value

    @property
    def keyword_list(self):
        return [item.strip() for item in self.keywords.split(",") if item.strip()]

    @property
    def has_staged_update(self):
        """Return True when a published article holds an editable update copy.

        A staged update can be a private saved draft, a submitted pending update,
        or a previously rejected update. Managers must resolve this copy through
        the update workflow so a successful publish clears the temporary fields.
        """
        return self.status == self.Status.PUBLISHED and bool((self.pending_update_body or "").strip())

    @property
    def has_pending_update(self):
        return self.has_staged_update and self.update_status == self.UpdateStatus.PENDING

    @property
    def has_failed_update(self):
        return self.has_staged_update and self.update_status == self.UpdateStatus.FAILED

    @property
    def has_update_draft(self):
        return self.status == self.Status.PUBLISHED and self.update_status in {
            self.UpdateStatus.PENDING,
            self.UpdateStatus.FAILED,
        }

    def clear_pending_update(self):
        self.pending_update_title = ""
        self.pending_update_body = ""
        self.pending_update_keywords = ""
        self.pending_update_image_assets = []
        self.update_status = self.UpdateStatus.NONE
        self.update_submitted_at = None
        self.update_reviewed_at = timezone.now()

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
            self.author_account_type_snapshot = str(_("Admin"))
        else:
            self.author_account_type_snapshot = ""

    def save(self, *args, **kwargs):
        if self.owner:
            self.refresh_author_snapshot()
        self.normalized_title = normalize_article_title(self.title)

        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "title" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"normalized_title"}

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

        return str(_("Deleted user"))

    @property
    def author_username(self):
        if self.owner:
            return self.owner.get_username()

        return self.author_username_snapshot or str(_("deleted-user"))

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
                return str(_("Admin"))

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



class ArticleDeletionRequest(models.Model):
    """Historical records from the retired deletion-request approval workflow.

    The active application no longer creates, reviews, or queries these records.
    The model and table are retained so earlier audit/history data is not destroyed
    by this simplification migration.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending deletion approval")
        APPROVED = "approved", _("Deletion approved")
        REJECTED = "rejected", _("Deletion rejected")

    article = models.ForeignKey(
        SuggestedArticle,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deletion_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="article_deletion_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_article_deletion_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    reason = models.TextField(blank=True)
    review_comment = models.TextField(blank=True)
    article_title_snapshot = models.CharField(max_length=255, blank=True, db_index=True)
    article_visibility_snapshot = models.CharField(max_length=20, blank=True, db_index=True)
    article_status_snapshot = models.CharField(max_length=20, blank=True, db_index=True)
    article_owner_username_snapshot = models.CharField(max_length=255, blank=True, db_index=True)
    article_owner_email_snapshot = models.EmailField(blank=True)
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("Article deletion request")
        verbose_name_plural = _("Article deletion requests")
        indexes = [
            models.Index(fields=["status", "article_visibility_snapshot"], name="kb_delreq_status_vis_idx"),
            models.Index(fields=["-requested_at", "status"], name="kb_delreq_req_status_idx"),
        ]

    def __str__(self):
        title = self.article_title_snapshot or (self.article.title if self.article_id and self.article else "")
        return f"{self.get_status_display()} - {title}"

    def refresh_article_snapshot(self):
        article = self.article
        if not article:
            return
        self.article_title_snapshot = article.title
        self.article_visibility_snapshot = article.visibility
        self.article_status_snapshot = article.status
        self.article_owner_username_snapshot = article.author_username
        self.article_owner_email_snapshot = article.author_email

    def save(self, *args, **kwargs):
        if self.article_id:
            self.refresh_article_snapshot()
        super().save(*args, **kwargs)


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
        verbose_name = _("Article vote")
        verbose_name_plural = _("Article votes")

    def __str__(self):
        label = _("Helpful") if self.value == self.VoteValue.UP else _("Not helpful")
        return f"{self.article.title} - {self.user} - {label}"


class ArticleCreationWorkspace(models.Model):
    """Persistent server-side checkpoint for one user's unsaved new article.

    The checkpoint owns uncommitted image uploads until the user saves,
    submits, publishes, resets, or explicitly discards the new article. It is
    intentionally separate from SuggestedArticle so checkpoint content does
    not create a visible Draft entry in My Articles and never expires merely
    because it has not been edited recently.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="article_creation_workspace",
    )
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    keywords = models.CharField(max_length=500, blank=True)
    visibility = models.CharField(
        max_length=20,
        choices=SuggestedArticle.Visibility.choices,
        default=SuggestedArticle.Visibility.PUBLIC,
    )
    image_assets = models.JSONField(default=list, blank=True)
    is_dirty = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = _("Article creation workspace")
        verbose_name_plural = _("Article creation workspaces")

    def __str__(self):
        owner_name = self.owner.get_username() if self.owner_id else str(_("unknown user"))
        return _("New article workspace for %(owner)s") % {"owner": owner_name}

    @property
    def has_content(self):
        return bool(
            self.is_dirty
            or self.title.strip()
            or self.body.strip()
            or self.keywords.strip()
            or self.image_assets
        )


class ArticleEditWorkspace(models.Model):
    """Persistent per-user checkpoint for editing one existing article.

    Each authorised user receives an independent checkpoint for an article and
    editor mode. Autosave uses last-write-wins semantics by design: the most
    recent save from that user's tabs becomes the restored checkpoint. The
    workspace is separate from ``SuggestedArticle`` until the user performs a
    normal Save, submit, review, or publish action.
    """

    class EditorMode(models.TextChoices):
        EDIT = "edit", _("Edit")
        REVIEW = "review", _("Review")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="article_edit_workspaces",
    )
    article = models.ForeignKey(
        SuggestedArticle,
        on_delete=models.CASCADE,
        related_name="edit_workspaces",
    )
    editor_mode = models.CharField(
        max_length=10,
        choices=EditorMode.choices,
        default=EditorMode.EDIT,
    )
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    keywords = models.CharField(max_length=500, blank=True)
    visibility = models.CharField(
        max_length=20,
        choices=SuggestedArticle.Visibility.choices,
        default=SuggestedArticle.Visibility.PUBLIC,
    )
    status = models.CharField(max_length=20, blank=True)
    review_notes = models.TextField(blank=True)
    image_assets = models.JSONField(default=list, blank=True)
    owned_image_assets = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Uncommitted images uploaded specifically into this edit checkpoint."
        ),
    )
    is_dirty = models.BooleanField(default=False, db_index=True)
    article_approved_at_snapshot = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text=_(
            "Approval timestamp of the article version used to initialise this checkpoint. "
            "A newer approval takes precedence over an older open editor."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = _("Article edit workspace")
        verbose_name_plural = _("Article edit workspaces")
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "article", "editor_mode"],
                name="kb_unique_article_edit_workspace",
            )
        ]

    def __str__(self):
        owner_name = self.owner.get_username() if self.owner_id else str(_("unknown user"))
        article_title = self.article.title if self.article_id else str(_("unknown article"))
        return _("%(mode)s checkpoint for %(article)s by %(owner)s") % {
            "mode": self.get_editor_mode_display(),
            "article": article_title,
            "owner": owner_name,
        }

    @property
    def has_content(self):
        return bool(
            self.is_dirty
            or self.title.strip()
            or self.body.strip()
            or self.keywords.strip()
            or self.review_notes.strip()
            or self.image_assets
            or self.owned_image_assets
        )


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
    creation_workspace_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        editable=False,
        help_text=_(
            "Temporary new-article workspace that owned this upload when it was created. "
            "The UUID is retained as an audit snapshot after the workspace is removed."
        ),
    )
    edit_workspace_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        editable=False,
        help_text=_(
            "Existing-article checkpoint that owned this upload when it was created. "
            "The UUID is retained as an audit snapshot after the workspace is removed."
        ),
    )
    editing_article_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        editable=False,
        help_text=_(
            "Existing article being edited when this upload was created. "
            "This is an audit snapshot and does not enforce a database relation."
        ),
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        db_constraint=False,
        help_text=_(
            "Historical uploader ID snapshot only. The username/email snapshot fields keep the log readable "
            "after the user account is changed or deleted."
        ),
        on_delete=models.DO_NOTHING,
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
        db_constraint=False,
        help_text=_(
            "Historical deleter ID snapshot only. Image deletion activity is appended into ActivityLog "
            "rather than editing this upload log row."
        ),
        on_delete=models.DO_NOTHING,
        related_name="deleted_article_images",
    )
    delete_reason = models.CharField(max_length=30, choices=DeleteReason.choices, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = _("Article image upload log")
        verbose_name_plural = _("Article image upload logs")
        indexes = [
            models.Index(fields=["filename"], name="kb_articlei_filenam_c7f6d4_idx"),
            models.Index(fields=["uploader_username_snapshot", "-uploaded_at"], name="kb_articlei_uploade_6e1e42_idx"),
            models.Index(fields=["-uploaded_at"], name="kb_articlei_uploade_5d2a0f_idx"),
        ]

    def __str__(self):
        uploader = self.uploader_username_snapshot or str(_("unknown user"))
        return _("%(filename)s uploaded by %(uploader)s") % {"filename": self.filename, "uploader": uploader}

    @property
    def uploader_display(self):
        # ``uploaded_by`` intentionally has no database constraint so immutable
        # audit rows can survive deletion of an account that uploaded an image
        # later committed to an article. Fall back to the stored snapshot when
        # the referenced Django user row no longer exists.
        try:
            uploader = self.uploaded_by
        except ObjectDoesNotExist:
            uploader = None

        if uploader:
            full_name = uploader.get_full_name().strip()
            if full_name:
                return f"{full_name} ({uploader.get_username()})"
            return uploader.get_username()
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
        if self.pk and not self._state.adding:
            raise ValidationError(_("Article image upload log records are append-only and cannot be edited."))
        if self.uploaded_by:
            self.snapshot_uploader()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Article image upload log records cannot be manually deleted."))

    def mark_deleted(self, actor=None, reason=""):
        # Kept as a no-op compatibility method. This model is now append-only;
        # image deletion events are recorded as separate ActivityLog rows instead
        # of editing the original upload log row.
        return None


class ActivityLog(AppendOnlyAuditLogMixin, models.Model):
    """General admin audit/activity log for non-authentication actions.

    Authentication and MFA events stay in AuthActivityLog. This table tracks
    article, vote, image, AI, and admin-tool activity so admins can review who
    did what, from where, and when.
    """

    class EventType(models.TextChoices):
        ARTICLE_CREATED = "article_created", _("Article created")
        ARTICLE_UPDATED = "article_updated", _("Article updated")
        ARTICLE_DELETED = "article_deleted", _("Article deleted")
        ARTICLE_DELETE_QUEUED = "article_delete_queued", _("Article queued for deletion")
        ARTICLE_DELETE_RESTORED = "article_delete_restored", _("Article restored from deletion queue")
        ARTICLE_DELETE_PURGED = "article_delete_purged", _("Article permanently deleted")
        ARTICLE_DELETE_AUTO_PURGED = "article_delete_auto_purged", _("Article auto-deleted from queue")
        ARTICLE_DELETION_REQUESTED = "article_deletion_requested", _("Article deletion requested")
        ARTICLE_DELETION_REJECTED = "article_deletion_rejected", _("Article deletion rejected")
        ARTICLE_STATUS_CHANGED = "article_status_changed", _("Article status changed")
        ARTICLE_SUBMITTED = "article_submitted", _("Article submitted for approval")
        ARTICLE_APPROVED = "article_approved", _("Article approved/published")
        ARTICLE_REJECTED = "article_rejected", _("Article marked pending failed")
        ARTICLE_REVIEW_NOTIFICATION_QUEUED = (
            "article_review_notification_queued",
            _("Article review notification queued"),
        )
        ARTICLE_REVIEW_NOTIFICATION_SENT = (
            "article_review_notification_sent",
            _("Article review notification sent"),
        )
        ARTICLE_REVIEW_NOTIFICATION_FAILED = (
            "article_review_notification_failed",
            _("Article review notification failed"),
        )
        ARTICLE_REVIEW_NOTIFICATION_SKIPPED = (
            "article_review_notification_skipped",
            _("Article review notification skipped"),
        )
        ARTICLE_OWNER_NOTIFICATION_QUEUED = (
            "article_owner_notification_queued",
            _("Article owner notification queued"),
        )
        ARTICLE_OWNER_NOTIFICATION_SENT = (
            "article_owner_notification_sent",
            _("Article owner notification sent"),
        )
        ARTICLE_OWNER_NOTIFICATION_FAILED = (
            "article_owner_notification_failed",
            _("Article owner notification failed"),
        )
        ARTICLE_OWNER_NOTIFICATION_SKIPPED = (
            "article_owner_notification_skipped",
            _("Article owner notification skipped"),
        )
        ARTICLE_ORPHAN_ASSIGNED = "article_orphan_assigned", _("Orphan article assigned")
        ARTICLE_ORPHAN_DELETED = "article_orphan_deleted", _("Orphan article deleted")
        ARTICLE_VIEWED = "article_viewed", _("Article viewed")
        VOTE_UP = "vote_up", _("Article vote up")
        VOTE_DOWN = "vote_down", _("Article vote down")
        VOTE_UPDATED = "vote_updated", _("Article vote changed")
        VOTE_REMOVED = "vote_removed", _("Article vote removed")
        IMAGE_UPLOADED = "image_uploaded", _("Article image uploaded")
        IMAGE_DELETED = "image_deleted", _("Article image deleted")
        AI_QUESTION = "ai_question", _("OpenKB AI question")
        AI_RATE_LIMITED = "ai_rate_limited", _("OpenKB AI rate limited")
        BULK_IMPORT = "bulk_import", _("Bulk article import")
        PROFILE_EMAIL_UPDATED = "profile_email_updated", _("Profile email updated")
        PROFILE_PASSWORD_CHANGED = "profile_password_changed", _("Profile password changed")
        ADMIN_TOOL_ACTION = "admin_tool_action", _("Admin tool action")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=60, choices=EventType.choices, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        db_constraint=False,
        help_text=_(
            "Historical user ID snapshot only. This relation intentionally does not enforce "
            "a database constraint because audit logs must remain immutable when users are deleted."
        ),
        on_delete=models.DO_NOTHING,
        related_name="activity_logs",
    )
    username = models.CharField(max_length=255, blank=True, db_index=True)

    article = models.ForeignKey(
        SuggestedArticle,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="activity_logs",
        help_text=_(
            "Snapshot fields keep the audit trail after an article is deleted. "
            "This relation intentionally does not enforce a database constraint because audit logs are append-only."
        ),
    )
    article_title = models.CharField(max_length=255, blank=True, db_index=True)
    article_status = models.CharField(max_length=40, blank=True, db_index=True)
    article_owner_user_id_snapshot = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Article owner user ID snapshot"),
        help_text=_("Historical user ID of the account that owned the article when this log was created."),
    )
    article_owner_username_snapshot = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name=_("Article owner username snapshot"),
    )
    article_owner_name_snapshot = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Article owner name snapshot"),
    )
    article_owner_email_snapshot = models.EmailField(
        blank=True,
        verbose_name=_("Article owner email snapshot"),
    )
    article_owner_account_type_snapshot = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        verbose_name=_("Article owner account type snapshot"),
    )

    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.TextField(blank=True)
    path = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Activity log")
        verbose_name_plural = _("Activity logs")
        indexes = [
            models.Index(fields=["-created_at", "event_type"], name="kb_activity_created_34f83d_idx"),
            models.Index(fields=["username", "-created_at"], name="kb_activity_usernam_e4c3d4_idx"),
            models.Index(fields=["article_title", "-created_at"], name="kb_activity_article_0387e8_idx"),
            models.Index(fields=["article_owner_username_snapshot", "-created_at"], name="kb_act_owner_cr_idx"),
            models.Index(fields=["ip_address", "-created_at"], name="kb_activity_ip_addr_709e8b_idx"),
        ]

    def __str__(self):
        actor = self.username or (self.user.get_username() if self.user_id else str(_("anonymous")))
        target = f" - {self.article_title}" if self.article_title else ""
        return f"{self.get_event_type_display()} - {actor}{target} - {self.created_at:%Y-%m-%d %H:%M:%S}"


class AdminActivityLog(AppendOnlyAuditLogMixin, models.Model):
    """Append-only audit log for actions performed inside Django Admin.

    This complements Django's built-in admin LogEntry table by keeping a
    project-owned retention-managed audit trail for admin create/change/delete
    actions and admin POST requests such as MFA resets or bulk actions.
    """

    class EventType(models.TextChoices):
        ADMIN_ADD = "admin_add", _("Admin object created")
        ADMIN_CHANGE = "admin_change", _("Admin object changed")
        ADMIN_DELETE = "admin_delete", _("Admin object deleted")
        ADMIN_ACTION = "admin_action", _("Admin action/request")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        db_constraint=False,
        help_text=_(
            "Historical admin user snapshot only. This relation intentionally does not enforce "
            "a database constraint because admin audit logs must remain immutable when users are deleted."
        ),
        on_delete=models.DO_NOTHING,
        related_name="admin_activity_logs",
    )
    admin_username = models.CharField(max_length=255, blank=True, db_index=True)
    target_app_label = models.CharField(max_length=100, blank=True, db_index=True)
    target_model = models.CharField(max_length=100, blank=True, db_index=True)
    target_object_id = models.CharField(max_length=255, blank=True, db_index=True)
    target_repr = models.CharField(max_length=500, blank=True)
    action_flag = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.TextField(blank=True)
    path = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    change_message = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Admin activity log")
        verbose_name_plural = _("Admin activity logs")
        indexes = [
            models.Index(fields=["-created_at", "event_type"], name="kb_adminlog_created_idx"),
            models.Index(fields=["admin_username", "-created_at"], name="kb_adminlog_user_idx"),
            models.Index(fields=["target_app_label", "target_model", "-created_at"], name="kb_adminlog_target_idx"),
            models.Index(fields=["ip_address", "-created_at"], name="kb_adminlog_ip_idx"),
        ]

    def __str__(self):
        actor = self.admin_username or (self.admin_user.get_username() if self.admin_user_id else str(_("unknown admin")))
        target = self.target_repr or self.target_object_id or self.path or "-"
        return f"{self.get_event_type_display()} - {actor} - {target} - {self.created_at:%Y-%m-%d %H:%M:%S}"


SITE_SETTING_CACHE_KEY = "site_setting:singleton:v1"
SITE_SETTING_CACHE_SECONDS = 60


class SiteSetting(models.Model):
    """Singleton-style site settings editable from Django Admin."""

    stray_upload_cleanup_min_age_minutes = models.PositiveIntegerField(
        default=1440,
        verbose_name=_("Stray upload cleanup minimum age (minutes)"),
        help_text=_(
            "Uploaded files newer than this many minutes are ignored when they are not "
            "referenced by an article or a user-owned New Article checkpoint. Existing "
            "checkpoints never expire because of age. Set to 0 only for immediate orphan-file "
            "detection/deletion."
        ),
    )
    article_deletion_queue_retention_days = models.PositiveIntegerField(
        default=7,
        verbose_name=_("Article deletion queue retention (days)"),
        help_text=_(
            "How long deleted published articles remain recoverable in My Profile → Admin tools → Deletion queue before permanent deletion. "
            "Default is 7 days. Set to 0 to permanently delete published articles immediately after MFA confirmation."
        ),
    )
    article_image_upload_limit = models.PositiveIntegerField(
        default=50,
        verbose_name=_("Article image upload limit"),
        help_text=_(
            "Maximum number of pasted/uploaded images allowed per article, including draft, "
            "pending, published, and pending-update versions. Default is 50. Set to 0 to disable article image uploads."
        ),
    )
    pending_image_upload_limit_per_user = models.PositiveIntegerField(
        default=PENDING_IMAGE_UPLOAD_DEFAULT_COUNT_LIMIT,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
        verbose_name=_("Pending image uploads per user"),
        help_text=_(
            "Maximum number of uncommitted article images one user may keep across all browsers and sessions. "
            "Default is 100. Allowed range: 1 to 1000."
        ),
    )
    pending_image_upload_byte_limit_mb_per_user = models.PositiveIntegerField(
        default=PENDING_IMAGE_UPLOAD_DEFAULT_BYTE_LIMIT_MB,
        validators=[MinValueValidator(1), MaxValueValidator(2048)],
        verbose_name=_("Pending image upload storage per user (MB)"),
        help_text=_(
            "Maximum combined storage for one user's uncommitted article images across all browsers and sessions. "
            "Default is 100 MB. Allowed range: 1 to 2048 MB."
        ),
    )
    article_video_max_width_px = models.PositiveIntegerField(
        default=720,
        validators=[MinValueValidator(160), MaxValueValidator(1920)],
        verbose_name=_("Article video maximum width (px)"),
        help_text=_(
            "Maximum display width for article video players in pixels. "
            "Videos remain responsive and keep a 16:9 ratio on smaller screens. "
            "Default is 720 px. Allowed range: 160 to 1920 px."
        ),
    )
    article_keyword_limit = models.PositiveIntegerField(
        default=ARTICLE_KEYWORD_DEFAULT_LIMIT,
        validators=[
            MinValueValidator(ARTICLE_KEYWORD_MIN_LIMIT),
            MaxValueValidator(ARTICLE_KEYWORD_MAX_LIMIT),
        ],
        verbose_name=_("Article keyword limit"),
        help_text=_(
            "Maximum number of keywords allowed for each article and pending article update. "
            "Default is 20. Allowed range: 1 to 100."
        ),
    )
    article_body_character_limit = models.PositiveIntegerField(
        default=ARTICLE_BODY_DEFAULT_CHARACTER_LIMIT,
        validators=[
            MinValueValidator(ARTICLE_BODY_MIN_CHARACTER_LIMIT),
            MaxValueValidator(ARTICLE_BODY_MAX_CHARACTER_LIMIT),
        ],
        verbose_name=_("Article body character limit"),
        help_text=_(
            "Maximum number of characters allowed in an article body or pending article update. "
            "Default is 100000. Allowed range: 1000 to 2000000."
        ),
    )
    articles_per_page = models.PositiveIntegerField(
        default=10,
        verbose_name=_("Articles per page"),
        help_text=_(
            "Number of published articles shown per page in search/results and in each homepage article column "
            "such as Trending Topics, Most Liked, and Most Recent Articles. Recommended range: 5 to 100. Default is 10."
        ),
    )
    auth_activity_log_retention_days = models.PositiveIntegerField(
        default=30,
        verbose_name=_("Authentication activity log retention (days)"),
        help_text=_(
            "Authentication/MFA monitoring logs older than this many days can be deleted by the cleanup command. "
            "Use 0 to keep authentication activity logs indefinitely."
        ),
    )
    session_timeout_hours = models.PositiveIntegerField(
        default=8,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
        verbose_name=_("User session timeout (hours)"),
        help_text=_(
            "Authenticated sessions expire after this many hours from sign-in. Pending-MFA sessions cannot exceed this lifetime, "
            "but they normally expire sooner according to the separate MFA login completion timeout. "
            "Default is 8 hours. Allowed range: 1 to 168 hours (7 days)."
        ),
    )
    mfa_login_timeout_seconds = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(30), MaxValueValidator(900)],
        verbose_name=_("MFA login completion timeout (seconds)"),
        help_text=_(
            "Maximum time allowed to complete MFA after the username/password step succeeds. "
            "When the countdown reaches zero, the pending login is cleared and the user must enter their username and password again. "
            "Default is 60 seconds. Allowed range: 30 to 900 seconds (15 minutes)."
        ),
    )
    admin_mfa_verification_timeout_seconds = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(30), MaxValueValidator(900)],
        verbose_name=_("Admin MFA verification timeout (seconds)"),
        help_text=_(
            "Maximum time allowed to complete the separate MFA check before entering Django Admin. "
            "When the countdown reaches zero, the administrator stays signed in and must start a new verification window before trying again. "
            "Default is 60 seconds. Allowed range: 30 to 900 seconds (15 minutes)."
        ),
    )
    activity_log_retention_days = models.PositiveIntegerField(
        default=30,
        verbose_name=_("General activity log retention (days)"),
        help_text=_(
            "Article/vote/image/admin-tool/admin-site activity logs older than this many days can be deleted by the cleanup command. "
            "Use 0 to keep general and admin activity logs indefinitely."
        ),
    )
    admin_log_rows_per_page = models.PositiveIntegerField(
        default=200,
        verbose_name=_("Admin log rows per page"),
        help_text=_(
            "Number of rows to show per page in Django Admin log tables. "
            "Recommended range: 50 to 500. Default is 200."
        ),
    )
    admin_ip_allowlist_enabled = models.BooleanField(
        default=False,
        verbose_name=_("Enable Admin IP allowlist"),
        help_text=_(
            "Disabled by default. When disabled, Django Admin can be reached from any IPv4 or IPv6 address, "
            "subject to normal authentication and Admin MFA. When enabled, only the configured IP/CIDR ranges are allowed."
        ),
    )
    admin_allowed_cidrs = models.TextField(
        default="",
        blank=True,
        verbose_name=_("Admin allowed IP ranges"),
        help_text=_(
            "Optional comma, space, or newline separated IPv4/IPv6 addresses or CIDR ranges. "
            "Examples: 192.0.2.50, 10.0.0.0/24, 2001:db8::/32. "
            "Leave blank while the allowlist is disabled."
        ),
    )
    auth_lockout_strike_ttl_seconds = models.PositiveIntegerField(
        default=604800,
        verbose_name=_("Authentication lockout escalation memory (seconds)"),
        help_text=_(
            "How long failed-login/MFA lockout history is remembered if the user never signs in successfully. "
            "Successful password/MFA verification resets the relevant lockout history immediately. Default is 604800 seconds (7 days)."
        ),
    )

    admin_mfa_idle_timeout_seconds = models.PositiveIntegerField(
        default=600,
        verbose_name=_("Admin MFA idle timeout (seconds)"),
        help_text=_(
            "How long an administrator may stay inactive inside Django Admin after completing the extra admin MFA check. "
            "Default is 600 seconds (10 minutes). Minimum enforced by code is 60 seconds; maximum enforced by code is 86400 seconds."
        ),
    )

    openkb_ai_prompt_limit_per_24_hours = models.PositiveIntegerField(
        default=20,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
        verbose_name=_("OpenKB AI prompts per 24 hours"),
        help_text=_(
            "Maximum accepted Ask OpenKB AI questions per user in a fixed 24-hour window. "
            "The first accepted question starts the window and later questions do not extend it. Default: 20."
        ),
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Site setting")
        verbose_name_plural = _("Site settings")

    def __str__(self):
        return str(_("Site settings"))

    def save(self, *args, **kwargs):
        # Keep this model singleton-like: always use primary key 1.
        self.pk = 1
        super().save(*args, **kwargs)
        # Settings are cached briefly because many requests read several limits.
        # Clear both the singleton cache and the dedicated AI quota cache after
        # every Admin save so changes take effect immediately.
        try:
            cache.delete_many([
                SITE_SETTING_CACHE_KEY,
                "openkb_ai:quota24h:configured-limit",
            ])
        except Exception:
            # A settings save must not fail after the database write merely
            # because the shared cache is temporarily unavailable.
            pass

    @classmethod
    def load(cls):
        # Django TestCase and data migrations run inside database transactions.
        # Avoid sharing a cached model instance across rolled-back transactional
        # state; normal request paths still use the one-minute shared cache.
        if connection.in_atomic_block:
            obj, _created = cls.objects.get_or_create(pk=1)
            return obj

        try:
            cached = cache.get(SITE_SETTING_CACHE_KEY)
        except Exception:
            cached = None
        if isinstance(cached, cls) and cached.pk == 1:
            return cached

        obj, _created = cls.objects.get_or_create(pk=1)
        try:
            cache.set(SITE_SETTING_CACHE_KEY, obj, SITE_SETTING_CACHE_SECONDS)
        except Exception:
            pass
        return obj


def get_article_body_character_limit():
    """Return the configured article body character limit within a safe range."""
    try:
        value = int(
            getattr(
                SiteSetting.load(),
                "article_body_character_limit",
                ARTICLE_BODY_DEFAULT_CHARACTER_LIMIT,
            )
            or ARTICLE_BODY_DEFAULT_CHARACTER_LIMIT
        )
    except Exception:
        return ARTICLE_BODY_DEFAULT_CHARACTER_LIMIT

    return min(
        max(value, ARTICLE_BODY_MIN_CHARACTER_LIMIT),
        ARTICLE_BODY_MAX_CHARACTER_LIMIT,
    )


def count_article_body_characters(value):
    """Count Markdown characters while treating every line break as one character."""
    body = str(value or "")
    return len(body.replace("\r\n", "\n").replace("\r", "\n"))


def article_body_character_limit_error_message(character_count, limit=None):
    """Return the user-facing article body character-limit message."""
    limit = get_article_body_character_limit() if limit is None else int(limit)
    return _(
        "This article contains %(count)s characters, but the maximum allowed is %(limit)s. "
        "Please shorten the article before saving."
    ) % {"count": character_count, "limit": limit}


def validate_article_body(value, limit=None):
    """Validate an article or pending-update Markdown body without truncating it."""
    body = str(value or "")
    character_limit = (
        get_article_body_character_limit() if limit is None else int(limit)
    )
    character_count = count_article_body_characters(body)
    if character_count > character_limit:
        raise ValidationError(
            article_body_character_limit_error_message(
                character_count,
                character_limit,
            )
        )
    return body


def get_article_keyword_limit():
    """Return the configured article keyword limit within a safe range."""
    try:
        value = int(
            getattr(
                SiteSetting.load(),
                "article_keyword_limit",
                ARTICLE_KEYWORD_DEFAULT_LIMIT,
            )
            or ARTICLE_KEYWORD_DEFAULT_LIMIT
        )
    except Exception:
        return ARTICLE_KEYWORD_DEFAULT_LIMIT

    return min(
        max(value, ARTICLE_KEYWORD_MIN_LIMIT),
        ARTICLE_KEYWORD_MAX_LIMIT,
    )


def article_keyword_limit_error_message(keyword_count, limit=None):
    """Return the user-facing article keyword count validation message."""
    limit = get_article_keyword_limit() if limit is None else int(limit)
    return _(
        "This article has %(count)s keywords, but the maximum allowed is %(limit)s. "
        "Please remove some keywords before saving."
    ) % {"count": keyword_count, "limit": limit}


def validate_article_keywords(value, limit=None):
    """Normalise and validate article keywords against count and field limits."""
    normalized_value = normalize_article_keywords(value)
    keywords = split_article_keywords(normalized_value)
    keyword_limit = get_article_keyword_limit() if limit is None else int(limit)

    if len(keywords) > keyword_limit:
        raise ValidationError(
            article_keyword_limit_error_message(len(keywords), keyword_limit)
        )
    if len(normalized_value) > ARTICLE_KEYWORD_MAX_TOTAL_LENGTH:
        raise ValidationError(
            _("Article keywords must not exceed %(limit)s characters in total.")
            % {"limit": ARTICLE_KEYWORD_MAX_TOTAL_LENGTH}
        )
    return normalized_value


class AuthLockoutPolicyStage(models.Model):
    """Progressive password/MFA lockout stages managed from Site settings.

    The same policy is used for password-login failures and MFA-code failures,
    but their counters remain separate per user. repeat_count=0 means this
    stage repeats forever, which is ideal for the last stage.
    """

    site_setting = models.ForeignKey(
        SiteSetting,
        on_delete=models.CASCADE,
        related_name="auth_lockout_stages",
    )
    sort_order = models.PositiveIntegerField(
        default=10,
        verbose_name=_("Stage order"),
        help_text=_("Lower numbers run first. Use 10, 20, 30, etc. so you can insert stages later."),
    )
    failure_limit = models.PositiveIntegerField(
        default=10,
        verbose_name=_("Failed attempts before block"),
        help_text=_("Number of wrong password/MFA attempts required before this stage blocks the user."),
    )
    block_seconds = models.PositiveIntegerField(
        default=300,
        verbose_name=_("Block duration (seconds)"),
        help_text=_("How long the login/MFA check is blocked after this stage triggers."),
    )
    repeat_count = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Repeat count"),
        help_text=_("How many lockouts should use this stage before moving to the next stage. Use 0 on the final stage to repeat forever."),
    )
    enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enabled"),
    )

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = _("Authentication lockout policy stage")
        verbose_name_plural = _("Authentication lockout policy stages")

    def __str__(self):
        repeat_label = _("forever") if self.repeat_count == 0 else self.repeat_count
        return _(
            "Stage %(order)s: %(failures)s failures -> %(block)s seconds, repeat %(repeat)s"
        ) % {
            "order": self.sort_order,
            "failures": self.failure_limit,
            "block": self.block_seconds,
            "repeat": repeat_label,
        }

    def clean(self):
        errors = {}
        if self.failure_limit < 1:
            errors["failure_limit"] = _("Failed attempts must be at least 1.")
        if self.block_seconds < 60:
            errors["block_seconds"] = _("Block duration must be at least 60 seconds.")
        if errors:
            raise ValidationError(errors)
