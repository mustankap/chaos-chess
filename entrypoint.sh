#!/bin/sh
set -e

if [ "$1" = "daphne" ]; then
    python manage.py check --deploy --fail-level WARNING
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi
exec "$@"
