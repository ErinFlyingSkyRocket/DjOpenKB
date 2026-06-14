from django.contrib.auth import get_user_model
from django.test import TestCase

from kb.permissions import (
    PERM_ADD_ARTICLES,
    PERM_MANAGE_ARTICLES,
    PERM_USE_ADMIN_TOOLS,
    ROLE_ADMIN_USERS,
    ROLE_ARTICLE_MANAGER,
    ROLE_ARTICLE_WRITER,
    ROLE_REGULAR_USER,
    set_user_direct_kb_permission,
    user_can_add_articles,
    user_can_manage_articles,
    user_can_use_admin_tools,
    user_can_view_articles,
    user_can_vote_articles,
)

from .helpers import DjOpenKBTestMixin


class RolePermissionMatrixTests(DjOpenKBTestMixin, TestCase):
    def test_new_user_defaults_to_regular_user_group(self):
        User = get_user_model()
        user = User.objects.create_user(username="new_default", password=self.password)
        self.assertTrue(user.groups.filter(name=ROLE_REGULAR_USER).exists())
        self.assertTrue(user_can_view_articles(user))
        self.assertFalse(user_can_add_articles(user))
        self.assertFalse(user_can_manage_articles(user))
        self.assertFalse(user_can_use_admin_tools(user))

    def test_regular_user_matrix(self):
        user = self.make_user("matrix_regular", role=ROLE_REGULAR_USER)
        self.assertTrue(user_can_view_articles(user))
        self.assertTrue(user_can_vote_articles(user))
        self.assertFalse(user_can_add_articles(user))
        self.assertFalse(user_can_manage_articles(user))
        self.assertFalse(user_can_use_admin_tools(user))
        self.assertFalse(user.is_staff)

    def test_article_writer_matrix(self):
        user = self.make_user("matrix_writer", role=ROLE_ARTICLE_WRITER)
        self.assertTrue(user_can_view_articles(user))
        self.assertTrue(user_can_add_articles(user))
        self.assertFalse(user_can_manage_articles(user))
        self.assertFalse(user_can_use_admin_tools(user))
        self.assertFalse(user.is_staff)

    def test_article_manager_matrix(self):
        user = self.make_user("matrix_manager", role=ROLE_ARTICLE_MANAGER)
        self.assertTrue(user_can_view_articles(user))
        self.assertFalse(user_can_add_articles(user))
        self.assertTrue(user_can_manage_articles(user))
        self.assertFalse(user_can_use_admin_tools(user))
        self.assertFalse(user.is_staff)

    def test_admin_users_matrix(self):
        user = self.make_user("matrix_admin", role=ROLE_ADMIN_USERS)
        user.refresh_from_db()
        self.assertTrue(user_can_view_articles(user))
        self.assertTrue(user_can_add_articles(user))
        self.assertTrue(user_can_manage_articles(user))
        self.assertTrue(user_can_use_admin_tools(user))
        self.assertTrue(user.is_staff)

    def test_direct_user_permissions_are_additive(self):
        regular = self.make_user("direct_regular", role=ROLE_REGULAR_USER)
        set_user_direct_kb_permission(regular, PERM_ADD_ARTICLES, True)
        regular = get_user_model().objects.get(pk=regular.pk)
        self.assertTrue(user_can_add_articles(regular))
        self.assertTrue(regular.groups.filter(name=ROLE_REGULAR_USER).exists())

        writer = self.make_user("direct_writer", role=ROLE_ARTICLE_WRITER)
        set_user_direct_kb_permission(writer, PERM_MANAGE_ARTICLES, True)
        writer = get_user_model().objects.get(pk=writer.pk)
        self.assertTrue(user_can_add_articles(writer))
        self.assertTrue(user_can_manage_articles(writer))

    def test_removing_direct_permission_does_not_remove_group_permission(self):
        writer = self.make_user("direct_remove_writer", role=ROLE_ARTICLE_WRITER)
        set_user_direct_kb_permission(writer, PERM_ADD_ARTICLES, False)
        writer = get_user_model().objects.get(pk=writer.pk)
        self.assertTrue(user_can_add_articles(writer))

    def test_direct_admin_tools_permission_grants_staff_access(self):
        user = self.make_user("direct_admin_tools", role=ROLE_REGULAR_USER)
        self.assertFalse(user.is_staff)
        set_user_direct_kb_permission(user, PERM_USE_ADMIN_TOOLS, True)
        from kb.permissions import sync_user_staff_flags_from_roles

        sync_user_staff_flags_from_roles(user)
        user.refresh_from_db()
        self.assertTrue(user_can_use_admin_tools(user))
        self.assertTrue(user.is_staff)
