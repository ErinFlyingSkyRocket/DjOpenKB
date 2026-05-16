from django.urls import path, re_path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("suggest/", views.suggest, name="suggest"),
    path("search/", views.search_articles, name="search"),
    path("ask-openkb-ai/", views.ask_openkb_ai, name="ask_openkb_ai"),
    re_path(r"^wiki/(?P<wiki_path>.+)$", views.wiki_detail, name="wiki_detail"),
]
