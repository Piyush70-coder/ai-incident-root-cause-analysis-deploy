#!/usr/bin/env bash
set -o errexit

REQ_FILE="${REQUIREMENTS_FILE:-requirements.txt}"

python --version
python -c "import sys; assert sys.version_info[:2] == (3, 11), f'Expected Python 3.11.x, got {sys.version}'"
python -m pip install --upgrade "pip<25.2" setuptools wheel
pip install --no-cache-dir --prefer-binary -r "${REQ_FILE}"
python manage.py collectstatic --noinput
