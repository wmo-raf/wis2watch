#!/bin/bash
# Bash strict mode: http://redsymbol.net/articles/unofficial-bash-strict-mode/
set -euo pipefail

MIGRATE_ON_STARTUP=${MIGRATE_ON_STARTUP:-true}
COLLECT_STATICFILES_ON_STARTUP=${COLLECT_STATICFILES_ON_STARTUP:-true}

WIS2WATCH_GUNICORN_NUM_OF_WORKERS=${WIS2WATCH_GUNICORN_NUM_OF_WORKERS:-}
WIS2WATCH_CELERY_BEAT_DEBUG_LEVEL=${WIS2WATCH_CELERY_BEAT_DEBUG_LEVEL:-INFO}
WIS2WATCH_CELERY_WORKER_LOG_LEVEL=${WIS2WATCH_CELERY_WORKER_LOG_LEVEL:-INFO}

WIS2WATCH_LOG_LEVEL=${WIS2WATCH_LOG_LEVEL:-INFO}

WIS2WATCH_PORT="${WIS2WATCH_PORT:-8000}"

show_help() {
    echo """
The available WIS2Watch related commands and services are shown below:

ADMIN COMMANDS:
manage          : Manage WIS2Watch and its database
shell           : Start a Django Python shell
help            : Show this message

SERVICE COMMANDS:
gunicorn            : Start WIS2Watch django using a prod ready gunicorn server:
                         * Waits for the postgres database to be available first.
                         * Automatically migrates the database on startup.
                         * Binds to 0.0.0.0
celery-worker       : Start the celery worker queue which runs important async tasks
celery-beat         : Start the celery beat service used to schedule periodic jobs

DEV COMMANDS:
django-dev      : Start a normal WIS2Watch backend django development server, performs
                  the same checks and setup as the gunicorn command above.
"""
}

run_setup_commands_if_configured(){
  if [ "$MIGRATE_ON_STARTUP" = "true" ] ; then
    echo "python /wis2watch/app/src/wis2watch/manage.py migrate"
    /wis2watch/app/src/wis2watch/manage.py migrate
  fi

  # collect staticfiles
  if [ "$COLLECT_STATICFILES_ON_STARTUP" = "true" ] ; then
    echo "python /wis2watch/app/src/wis2watch/manage.py collectstatic --noinput"
    /wis2watch/app/src/wis2watch/manage.py collectstatic --noinput
  fi

  # Trigger monitoring immediately
  /wis2watch/app/src/wis2watch/manage.py ensure_monitoring
}

start_celery_worker() {

    EXTRA_CELERY_ARGS=()

    if [[ -n "$WIS2WATCH_GUNICORN_NUM_OF_WORKERS" ]]; then
        EXTRA_CELERY_ARGS+=(--concurrency "$WIS2WATCH_GUNICORN_NUM_OF_WORKERS")
    fi
    exec celery -A wis2watch worker "${EXTRA_CELERY_ARGS[@]}" -l "${WIS2WATCH_CELERY_WORKER_LOG_LEVEL}" "$@"
}

# Lets devs attach to this container running the passed command, press ctrl-c and only
# the command will stop. Additionally they will be able to use bash history to
# re-run the containers command after they have done what they want.
attachable_exec(){
    echo "$@"
    exec bash --init-file <(echo "history -s $*; $*")
}

run_server() {
    run_setup_commands_if_configured

    if [[ "$1" = "wsgi" ]]; then
        STARTUP_ARGS=(wis2watch.config.wsgi:application)
    elif [[ "$1" = "asgi" ]]; then
        STARTUP_ARGS=(-k uvicorn.workers.UvicornWorker wis2watch.config.asgi:application)
    else
        echo -e "\e[31mUnknown run_server argument $1 \e[0m" >&2
        exit 1
    fi


    # Gunicorn args explained in order:
    #
    # 1. See https://docs.gunicorn.org/en/stable/faq.html#blocking-os-fchmod for
    #    why we set worker-tmp-dir to /dev/shm by default.
    # 2. Log to stdout
    # 3. Log requests to stdout
    exec gunicorn --workers="$WIS2WATCH_GUNICORN_NUM_OF_WORKERS" \
        --worker-tmp-dir "${TMPDIR:-/dev/shm}" \
        --log-file=- \
        --access-logfile=- \
        --capture-output \
        -b "0.0.0.0":"${WIS2WATCH_PORT}" \
        --log-level="${WIS2WATCH_LOG_LEVEL}" \
        "${STARTUP_ARGS[@]}" \
        "${@:2}"
}

# ======================================================
# COMMANDS
# ======================================================

if [[ -z "${1:-}" ]]; then
    echo "Must provide arguments to docker-entrypoint.sh"
    show_help
    exit 1
fi

source /wis2watch/venv/bin/activate

# wait for required services to be available, using docker-compose-wait
/wait

case "$1" in
django-dev)
    run_setup_commands_if_configured
    echo "Running Development Server on 0.0.0.0:${WIS2WATCH_PORT}"
    echo "Press CTRL-p CTRL-q to close this session without stopping the container."
    attachable_exec python /wis2watch/app/src/wis2watch/manage.py runserver "0.0.0.0:${WIS2WATCH_PORT}"
    ;;
django-dev-no-attach)
    run_setup_commands_if_configured
    echo "Running Development Server on 0.0.0.0:${WIS2WATCH_PORT}"
    python /wis2watch/app/src/wis2watch/manage.py runserver "0.0.0.0:${WIS2WATCH_PORT}"
    ;;
gunicorn)
    run_server asgi "${@:2}"
    ;;
gunicorn-wsgi)
    run_server wsgi "${@:2}"
    ;;
manage)
    exec python3 /wis2watch/app/src/wis2watch/manage.py "${@:2}"
    ;;
shell)
    exec python3 /wis2watch/app/src/wis2watch/manage.py shell
    ;;
celery-worker)
    start_celery_worker -Q celery -n default-worker@%h "${@:2}"
    ;;
celery-beat)
    exec celery -A wis2watch beat -l "${WIS2WATCH_CELERY_BEAT_DEBUG_LEVEL}" -S django_celery_beat.schedulers:DatabaseScheduler "${@:2}"
    ;;
*)
    echo "Command given was $*"
    show_help
    exit 1
    ;;
esac