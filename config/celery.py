"""Celery entrypoint.

Both the worker and beat load this module:

    celery -A config worker -l info
    celery -A config beat -l info

Configuration lives in Django settings under the `CELERY_` namespace, so
there is exactly one place (settings.py / .env) to change broker URLs and
the beat schedule.
"""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')

# `namespace='CELERY'` means every Celery option is read from a setting
# prefixed with CELERY_ (CELERY_BROKER_URL -> broker_url).
app.config_from_object('django.conf:settings', namespace='CELERY')

# Picks up `tasks.py` in every installed app (e.g. ghl/tasks.py).
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Smoke test: `debug_task.delay()` should print in the worker log."""
    print(f'Request: {self.request!r}')
