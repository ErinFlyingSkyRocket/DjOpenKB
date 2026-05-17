from django.urls import path, re_path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("suggest/", views.suggest, name="suggest"),
    path("profile/", views.profile, name="profile"),
    path("profile/update/", views.update_profile, name="update_profile"),
    path("profile/change-password/", views.change_password, name="change_password"),
    path("profile/articles/<int:article_id>/edit/", views.edit_suggestion, name="edit_suggestion"),
    path("profile/articles/<int:article_id>/delete/", views.delete_suggestion, name="delete_suggestion"),
    path("search/", views.search_articles, name="search"),
    path("ask-openkb-ai/", views.ask_openkb_ai, name="ask_openkb_ai"),
    path("article-image-upload/", views.upload_article_image, name="upload_article_image"),
    path("article-image-delete/", views.delete_article_image, name="delete_article_image"),
    path("wiki/uploads/<path:filename>", views.serve_article_image, name="serve_article_image"),
    re_path(r"^wiki/(?P<wiki_path>.+)$", views.wiki_detail, name="wiki_detail"),
]
