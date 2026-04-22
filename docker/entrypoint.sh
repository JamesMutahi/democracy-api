#!/bin/sh

echo "Applying database migrations..."
python manage.py migrate

echo "Installing fixtures..."
python manage.py loaddata ./fixtures/constitution.json
python manage.py loaddata ./fixtures/counties.json
python manage.py loaddata ./fixtures/constituencies.json

echo "Starting Django application..."
exec "$@"