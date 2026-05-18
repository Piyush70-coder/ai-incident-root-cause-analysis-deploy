#!/usr/bin/env bash
set -o errexit

REQ_FILE="${REQUIREMENTS_FILE:-requirements.txt}"

python -m pip install --upgrade pip
pip install -r "${REQ_FILE}"
python manage.py collectstatic --noinput
