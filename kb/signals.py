from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import SuggestedArticle, UserProfile


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    """Create/sync the main-site profile whenever a User is created.

    The article author snapshot is also refreshed while the user still exists,
    so username/email changes are reflected in the article metadata. If the
    user is later deleted, the last stored snapshot remains on the article.
    """
    if created:
        if instance.is_superuser or instance.is_staff:
            account_type = UserProfile.AccountType.ADMIN
        else:
            account_type = UserProfile.AccountType.USER

        UserProfile.objects.get_or_create(
            user=instance,
            defaults={
                "account_type": account_type,
                "can_access_main_site": True,
                "preferred_language": "en",
            },
        )

    # Keep author snapshot details updated when a user edits their name/email,
    # and avoid crashing during login if the model code and signal code are ever
    # temporarily out of sync. Login updates User.last_login, which also fires
    # this signal.
    for article in SuggestedArticle.objects.filter(owner=instance):
        if not hasattr(article, "refresh_author_snapshot"):
            continue

        article.refresh_author_snapshot()
        SuggestedArticle.objects.filter(pk=article.pk).update(
            author_username_snapshot=article.author_username_snapshot,
            author_name_snapshot=article.author_name_snapshot,
            author_email_snapshot=article.author_email_snapshot,
            author_account_type_snapshot=article.author_account_type_snapshot,
        )
