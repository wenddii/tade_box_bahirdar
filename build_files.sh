#!/bin/bash
echo "Building project assets..."
python -m pip install -r requirements.txt
python manage.py collectstatic --noinput --clear
python manage.py migrate --noinput
echo "Build finished successfully."
