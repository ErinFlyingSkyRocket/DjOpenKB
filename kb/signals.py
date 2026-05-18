from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    """Create/sync the main-site profile whenever a User is created."""
    if not created:
        return

    if instance.is_superuser or instance.is_staff:
        account_type = UserProfile.AccountType.ADMIN
    else:
        account_type = UserProfile.AccountType.USER

    UserProfile.objects.get_or_create(
        user=instance,
        defaults={
            "account_type": account_type,
            "can_access_main_site": True,
        },
    )
