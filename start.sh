#!/usr/bin/env bash
set -o errexit

python manage.py migrate --noinput

if [ "${RUN_EMBEDDED_CELERY:-false}" = "true" ]; then
  celery -A incident_management worker -l info --pool=solo --concurrency="${CELERY_WORKER_CONCURRENCY:-1}" --without-gossip --without-mingle --without-heartbeat &
fi

exec gunicorn incident_management.wsgi:application -c gunicorn.conf.py
