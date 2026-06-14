from __future__ import annotations

import base64
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from kb.mfa import MFA_SESSION_KEY, MFA_USER_SESSION_KEY
from kb.models import ArticleVote, SiteSetting, SuggestedArticle, UserMFADevice, UserProfile
from kb.permissions import (
    ROLE_ADMIN_USERS,
    ROLE_ARTICLE_MANAGER,
    ROLE_ARTICLE_WRITER,
    ROLE_REGULAR_USER,
    assign_single_role_group,
    seed_djopenkb_role_groups,
    sync_user_staff_flags_from_roles,
)


VALID_TINY_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class DjOpenKBTestMixin:
    """Reusable helpers for DjOpenKB role/security tests."""

    password = "DjOpenKB-Test-Passw0rd!"

    @classmethod
    def setUpTestData(cls):
        seed_djopenkb_role_groups()

    def setUp(self):
        super().setUp()
        seed_djopenkb_role_groups()
        SiteSetting.load()

    def make_user(
        self,
        username: str,
        *,
        role: str = ROLE_REGULAR_USER,
        is_staff: bool = False,
        is_superuser: bool = False,
        can_access_main_site: bool = True,
        auth_source: str = UserProfile.AuthSource.LOCAL,
        account_type: str | None = None,
    ):
        User = get_user_model()
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=self.password,
            is_staff=is_staff,
            is_superuser=is_superuser,
        )

        profile = user.kb_profile
        profile.can_access_main_site = can_access_main_site
        profile.auth_source = auth_source
        if account_type:
            profile.account_type = account_type
        elif is_superuser or is_staff or role == ROLE_ADMIN_USERS:
            profile.account_type = UserProfile.AccountType.ADMIN
        else:
            profile.account_type = UserProfile.AccountType.USER
        profile.save()

        if role:
            assign_single_role_group(user, role)

        self.confirm_mfa(user)
        user.refresh_from_db()
        return user

    def confirm_mfa(self, user):
        device, _created = UserMFADevice.objects.get_or_create(
            user=user,
            defaults={"secret": "JBSWY3DPEHPK3PXP", "confirmed": True, "confirmed_at": timezone.now()},
        )
        if not device.confirmed:
            device.confirmed = True
            device.confirmed_at = timezone.now()
            device.save(update_fields=["confirmed", "confirmed_at"])
        return device

    def login_client(self, user):
        client = Client()
        client.force_login(user, backend="kb.backends.EmailOrUsernameModelBackend")
        session = client.session
        session[MFA_SESSION_KEY] = True
        session[MFA_USER_SESSION_KEY] = str(user.pk)
        session.save()
        return client

    def force_mfa_verified(self, client, user):
        session = client.session
        session[MFA_SESSION_KEY] = True
        session[MFA_USER_SESSION_KEY] = str(user.pk)
        session.save()

    def make_article(
        self,
        title: str,
        *,
        owner=None,
        status: str = SuggestedArticle.Status.PUBLISHED,
        body: str = "This is a test article body with enough content.",
        keywords: str = "",
        view_count: int = 0,
        approved_by=None,
        approved_at=None,
        filename: str | None = None,
        **extra_fields,
    ):
        owner = owner or self.make_user(f"owner_{SuggestedArticle.objects.count() + 1}", role=ROLE_ARTICLE_WRITER)
        approved_by = approved_by if approved_by is not None else (owner if status == SuggestedArticle.Status.PUBLISHED else None)
        approved_at = approved_at if approved_at is not None else (timezone.now() if status == SuggestedArticle.Status.PUBLISHED else None)
        filename = filename or f"test-{timezone.now().strftime('%Y%m%d%H%M%S')}-{SuggestedArticle.objects.count() + 1}.md"
        return SuggestedArticle.objects.create(
            owner=owner,
            title=title,
            body=body,
            keywords=keywords,
            status=status,
            filename=filename,
            wiki_path=f"sources/{filename}",
            raw_path=f"raw/{filename}",
            approved_by=approved_by,
            approved_at=approved_at,
            view_count=view_count,
            **extra_fields,
        )

    def grant_group(self, user, role):
        assign_single_role_group(user, role)
        user.refresh_from_db()
        return user

    def remove_all_role_groups(self, user):
        for group in Group.objects.filter(name__in=[ROLE_REGULAR_USER, ROLE_ARTICLE_WRITER, ROLE_ARTICLE_MANAGER, ROLE_ADMIN_USERS]):
            user.groups.remove(group)
        sync_user_staff_flags_from_roles(user)
        user.refresh_from_db()
        return user

    def assert_status_in(self, response, allowed, message=""):
        self.assertIn(response.status_code, set(allowed), message or f"Unexpected status {response.status_code}")

    def common_article_post_data(
        self,
        *,
        title="A Valid Test Article",
        body="This article has enough body text to pass validation.",
        keywords="test, regression",
        submit_action="submit",
        status=None,
        review_notes="",
    ):
        data = {
            "frm_kb_title": title,
            "frm_kb_body": body,
            "frm_kb_keywords": keywords,
            "submit_action": submit_action,
        }
        if status is not None:
            data["status"] = status
        if review_notes:
            data["review_notes"] = review_notes
        return data

    def patch_article_file_writes(self):
        """Patch expensive file sync helpers for article workflow view tests."""
        stack = ExitStack()
        stack.enter_context(patch("kb.views.suggestions.init_openkb_storage", return_value=None))
        stack.enter_context(patch("kb.views.suggestions.write_article_files", return_value=None))
        stack.enter_context(patch("kb.views.suggestions.sync_article_image_assets", return_value=None))
        stack.enter_context(patch("kb.views.suggestions.clear_committed_pending_uploads", return_value=None))
        stack.enter_context(patch("kb.views.services.mark_openkb_ai_stale", return_value=None))
        return stack

    def temporary_openkb_paths(self):
        """Temporary OpenKB paths for upload/image-serving tests."""
        tempdir = TemporaryDirectory()
        base = Path(tempdir.name)
        stack = ExitStack()
        stack.callback(tempdir.cleanup)
        stack.enter_context(patch("django.conf.settings.OPENKB_DATA_DIR", base / "openkb-data"))
        stack.enter_context(patch("django.conf.settings.OPENKB_RAW_DIR", base / "openkb-data" / "raw"))
        stack.enter_context(patch("django.conf.settings.OPENKB_WIKI_DIR", base / "openkb-data" / "wiki"))
        return stack

    def add_upvote(self, article, user=None):
        user = user or self.make_user(f"voter_{ArticleVote.objects.count() + 1}")
        return ArticleVote.objects.create(article=article, user=user, value=ArticleVote.VoteValue.UP)
