from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("suggest/", views.suggest, name="suggest"),
    path("article/<str:article_slug>/", views.article_detail, name="article_detail"),
]