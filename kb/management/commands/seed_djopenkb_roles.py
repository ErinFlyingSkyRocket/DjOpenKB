from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from kb.permissions import (
    ROLE_ADMIN_USERS,
    ROLE_GROUP_NAMES,
    ROLE_REGULAR_USER,
    assign_single_role_group,
    seed_djopenkb_role_groups,
)


class Command(BaseCommand):
    help = "Create/update Knowledge Repository role groups and optionally assign missing users to a default role."

    def add_arguments(self, parser):
        parser.add_argument(
            "--assign-missing-users",
            action="store_true",
            help="Assign users without a Knowledge Repository role group to Regular User or Admin Users.",
        )

    def handle(self, *args, **options):
        seed_djopenkb_role_groups()
        self.stdout.write(self.style.SUCCESS("Knowledge Repository role groups and permissions were seeded."))

        if not options["assign_missing_users"]:
            return

        UserModel = get_user_model()
        assigned = 0
        for user in UserModel.objects.all():
            if user.groups.filter(name__in=ROLE_GROUP_NAMES).exists():
                continue
            role_name = ROLE_ADMIN_USERS if user.is_staff or user.is_superuser else ROLE_REGULAR_USER
            assign_single_role_group(user, role_name)
            assigned += 1

        self.stdout.write(self.style.SUCCESS(f"Assigned default role groups to {assigned} user(s)."))
