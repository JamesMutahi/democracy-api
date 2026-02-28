#!/bin/sh

echo "Applying database migrations..."
python manage.py migrate

echo "Adding fixtures..."
python manage.py loaddata constitution.json

echo "Starting Django application..."
exec "$@"