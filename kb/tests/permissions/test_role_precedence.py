from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from kb.admin_security import AdminMFASessionMiddleware, is_admin_step_up_path
from kb.middleware import ForceLoginAndAdminGuardMiddleware


class ManagerRolePrecedenceTests(TestCase):
    """Manager is the highest standard role within its own article scope."""

    def setUp(self):
        from kb.permissions import seed_djopenkb_role_groups

        seed_djopenkb_role_groups()
        self.user = get_user_model().objects.create_user(
            username="manager-role-precedence-test",
            email="manager-role-precedence-test@example.invalid",
            password="safe-test-password",
        )

    def _groups(self):
        return set(self.user.groups.values_list("name", flat=True))

    def test_public_manager_removes_public_writer_and_approver(self):
        from django.contrib.auth.models import Group

        from kb.permissions import (
            ROLE_ARTICLE_APPROVER,
            ROLE_ARTICLE_MANAGER,
            ROLE_ARTICLE_WRITER,
        )

        writer = Group.objects.get(name=ROLE_ARTICLE_WRITER)
        approver = Group.objects.get(name=ROLE_ARTICLE_APPROVER)
        manager = Group.objects.get(name=ROLE_ARTICLE_MANAGER)

        # Exercise the normal m2m signal path used by the Django Admin group
        # selector, not only the explicit bulk-role action.
        with self.captureOnCommitCallbacks(execute=True):
            self.user.groups.add(writer, approver, manager)

        role_names = self._groups()
        self.assertIn(ROLE_ARTICLE_MANAGER, role_names)
        self.assertNotIn(ROLE_ARTICLE_WRITER, role_names)
        self.assertNotIn(ROLE_ARTICLE_APPROVER, role_names)

    def test_internal_manager_removes_only_lower_internal_roles(self):
        from django.contrib.auth.models import Group

        from kb.permissions import (
            ROLE_ARTICLE_APPROVER,
            ROLE_INTERNAL_ARTICLE_APPROVER,
            ROLE_INTERNAL_ARTICLE_MANAGER,
            ROLE_INTERNAL_ARTICLE_WRITER,
            ROLE_INTERNAL_USER,
        )

        public_approver = Group.objects.get(name=ROLE_ARTICLE_APPROVER)
        internal_user = Group.objects.get(name=ROLE_INTERNAL_USER)
        internal_writer = Group.objects.get(name=ROLE_INTERNAL_ARTICLE_WRITER)
        internal_approver = Group.objects.get(name=ROLE_INTERNAL_ARTICLE_APPROVER)
        internal_manager = Group.objects.get(name=ROLE_INTERNAL_ARTICLE_MANAGER)

        with self.captureOnCommitCallbacks(execute=True):
            self.user.groups.add(
                public_approver,
                internal_user,
                internal_writer,
                internal_approver,
                internal_manager,
            )

        role_names = self._groups()
        self.assertIn(ROLE_ARTICLE_APPROVER, role_names)
        self.assertIn(ROLE_INTERNAL_ARTICLE_MANAGER, role_names)
        self.assertNotIn(ROLE_INTERNAL_USER, role_names)
        self.assertNotIn(ROLE_INTERNAL_ARTICLE_WRITER, role_names)
        self.assertNotIn(ROLE_INTERNAL_ARTICLE_APPROVER, role_names)

    def test_assigning_public_manager_immediately_normalises_existing_roles(self):
        from django.contrib.auth.models import Group

        from kb.permissions import (
            ROLE_ARTICLE_APPROVER,
            ROLE_ARTICLE_MANAGER,
            ROLE_ARTICLE_WRITER,
            assign_single_role_group,
        )

        self.user.groups.add(
            Group.objects.get(name=ROLE_ARTICLE_WRITER),
            Group.objects.get(name=ROLE_ARTICLE_APPROVER),
        )
        assign_single_role_group(self.user, ROLE_ARTICLE_MANAGER)

        role_names = self._groups()
        self.assertIn(ROLE_ARTICLE_MANAGER, role_names)
        self.assertNotIn(ROLE_ARTICLE_WRITER, role_names)
        self.assertNotIn(ROLE_ARTICLE_APPROVER, role_names)
