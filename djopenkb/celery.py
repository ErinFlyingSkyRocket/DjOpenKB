"""Celery application used by the production OpenKB AI background worker."""

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djopenkb.settings")

app = Celery("djopenkb")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
