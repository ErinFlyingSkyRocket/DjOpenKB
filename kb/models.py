from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class UserProfile(models.Model):
    """Extra main-site account settings for Django's built-in User model.

    We keep Django's default User model so existing users/migrations stay safe.
    The account_type controls how the account should be treated in this wiki.
    """

    class AccountType(models.TextChoices):
        ADMIN = "admin", "Admin"
        USER = "user", "User"
        LDAP_USER = "ldap_user", "LDAP user"
        LDAP_ADMIN = "ldap_admin", "LDAP admin"

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
