#!/bin/sh

echo "Applying database migrations..."
python manage.py migrate

echo "Starting Django application..."
exec "$@"