#!/bin/sh
set -e

if [ "$DJANGO_ENV" = "production" ]; then
    echo "Collecting static files..."
    python manage.py collectstatic --noinput

    echo "Starting gunicorn..."
    exec gunicorn automator.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers 4 \
        --timeout 120 \
        --graceful-timeout 30 \
        --keep-alive 5 \
        --log-level info \
        --access-logfile - \
        --error-logfile -
else
    echo "Applying migrations..."
    python manage.py migrate --noinput

    echo "Starting development server..."
    exec python manage.py runserver 0.0.0.0:8000
fi