#!/usr/bin/with-contenv bashio
set -e

bashio::log.info "Starting JOOAN Local Control"
exec gunicorn \
  --bind 0.0.0.0:8099 \
  --workers 1 \
  --threads 6 \
  --timeout 30 \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
