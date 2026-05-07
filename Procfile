web: gunicorn wsgi:app --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT:-8000} --access-logfile - --error-logfile -
worker: python worker.py
