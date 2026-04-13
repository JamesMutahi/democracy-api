#!/bin/sh

echo "Applying database migrations..."
python manage.py migrate

echo "Installing fixtures..."
python manage.py loaddata ./fixtures/*.json

echo "Starting Django application..."
exec "$@"