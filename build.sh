#!/usr/bin/env bash
# Salir inmediatamente si ocurre un error
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
