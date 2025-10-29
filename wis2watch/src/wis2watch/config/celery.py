from celery import Celery

app = Celery("wis2watch")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
