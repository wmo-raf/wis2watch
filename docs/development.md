# Developing WIS2Watch

The stack in `docker-compose.yml` is the one a deployment runs: gunicorn,
production settings, code baked into the image. Editing anything means
rebuilding. This document is about the other mode, where a change takes effect
when you save it.

## Turning it on

```bash
cp .env.sample .env      # if you have not already
```

Then uncomment one line in `.env`:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml
```

and bring the stack up, building once:

```bash
make build
make up
```

The build is needed the first time only, and only if you already had an image:
the development commands live in `docker-entrypoint.sh`, which is copied into
the image rather than mounted. After that, `make up` alone.

`make` on its own lists every target. They are all thin wrappers over
`docker compose`, and every one of them refuses to run unless the `COMPOSE_FILE`
line above is set -- which is the point: a `make up` that quietly started the
production stack would look identical until you saved a file and nothing
happened. Nothing here is required; `docker compose up` works just as well.

There is deliberately no production target. A deployment runs `docker compose`
directly, and giving that a friendlier name would only make the two easier to
confuse.

`COMPOSE_FILE` is what makes `docker compose` read the development overlay on
top of the production file, for every command, without your having to pass
`-f` twice each time. It lives in `.env` because that file is already yours
alone and already untracked. A deployment checks out the same repository, does
not set the line, and is unaffected by anything below.

The cost of that convenience is that it is invisible: if a container is running
something you did not expect, `.env` is the first place to look.

## What you get

`docker compose up` now runs the same seven containers, four of them changed:

| Service | Runs | Reloads on |
|---|---|---|
| `wis2watch` | `runserver` | any Python file, via Django's own reloader |
| `wis2watch_celery_worker` | the worker under `watchfiles` | any `.py` under `wis2watch/src` |
| `wis2watch_celery_beat` | beat under `watchfiles` | the same |
| `wis2watch_ingest` | the supervisor under `watchfiles` | the same |

`wis2watch_db`, `wis2watch_redis` and `wis2watch_web_proxy` are untouched.

`wis2watch/src` is bind-mounted into all four. Only `src` -- `requirements.txt`
and `pyproject.toml` are installed during the build, so changing either needs
`docker compose build` whether they are mounted or not.

Templates, being under `src`, reload with everything else.

## Browse it at `http://localhost:8000/`

Not `http://localhost/`. The nginx proxy on port 80 serves `/static/` from the
`collectstatic` output in `docker/static`, which goes stale the moment you edit
anything. Port 8000 is the Django process itself, and with `DEBUG` on,
`config/urls.py` serves static from the staticfiles finders -- your source tree,
live -- and media from `MEDIA_ROOT`.

The proxy keeps running, so when you specifically want to check something
nginx-shaped -- a proxy header, the `/ws/` route, gzip -- collect the files once
and use port 80:

```bash
make collectstatic
```

(That runs `docker compose exec wis2watch python manage.py collectstatic`.
`docker compose exec` does not run the image's entrypoint, so the entrypoint's
own `manage` command is not available to it -- but the container's working
directory and `PATH` are already the right ones, so `manage.py` is called
directly.)

Websockets work on port 8000 without it: `daphne` is first in `INSTALLED_APPS`,
so `runserver` is an ASGI server.

## Settings

The development containers run `wis2watch.config.settings.dev`, not
`production`. This is not a convenience -- `production.py` assigns
`DEBUG = False` outright, after the environment has been read, so no variable
can turn debug on while that module is selected.

`dev.py` gives you `DEBUG = True`, `ALLOWED_HOSTS = ["*"]`, an insecure
built-in `SECRET_KEY`, the console email backend -- so the daily digest and the
alert mails print into the worker's logs instead of needing an SMTP host -- and
plain static files storage rather than the hashed manifest.

The consequence to keep in mind: local development does not exercise
`production.py`. A bug that lives only there -- in the manifest storage, in
`CSRF_TRUSTED_ORIGINS`, in the CORS list -- will not show up until you run the
production stack. When you touch any of those, run without the `COMPOSE_FILE`
line once before you trust it.

## The Vue islands

The frontend is not in Docker. It is a Vite project at
`wis2watch/src/wis2watch/monitoring/wis2watch-monitoring`, and it runs on your
machine:

```bash
make frontend
```

That installs the dependencies if they are missing, then runs the Vite dev
server. Long form, if you would rather:

```bash
cd wis2watch/src/wis2watch/monitoring/wis2watch-monitoring
npm install
npm run dev
```

That serves the islands from `http://localhost:5173` with hot module
replacement -- a component edit appears without a page reload. The Django side
already knows to point at it: with `DEBUG` on, `{% vue_bundle_url %}` emits the
dev server's URL instead of a `{% static %}` path.

**If you are not working on the frontend**, you do not need node at all. Set

```
VUE_FRONTEND_USE_DEV_SERVER=False
```

in `.env`, and the templates load the bundles committed under
`monitoring/static/vue/` instead. Without either the dev server running or this
variable set, the two pages carrying islands -- the ingest monitor map and the
node statistics panel -- come up blank, because their script tags point at a
port with nothing behind it.

The bundles are committed, so a frontend change is not finished until you have
run `make frontend-build` and committed the result. See
`docs/adr/0001-vue-islands-in-wagtail-admin-pages.md`.

## Tests

Django's own runner, inside the web container, against a throwaway copy of the
real database:

```bash
make test                                        # all 1242 of them, about 90s
make test T=wis2watch.core.tests.test_silence    # one module
make test T=wis2watch.ingest                     # one app
```

The stack has to be up -- these run through `docker compose exec`.

Most of a short run is spent building the test database, which means applying
every migration. `make test-keepdb` keeps it between runs and takes the same
narrowed `T=`:

```bash
make test-keepdb T=wis2watch.core.tests.test_silence   # ~3s instead of ~14s
```

The catch is the usual one: a schema change lands in a database that already
exists. If a test starts failing in a way that makes no sense, run `make test`
once to build it afresh.

Tests that exercise failure paths log at ERROR while they pass -- unreachable
brokers, refused OSCAR connections. Noise in the output is not a failing run;
the last line is.

## Migrations, and a note for Linux

```bash
make makemigrations
```

This writes files into the bind-mounted source tree, which means it writes as
the container's user. The image runs as `9999:9999` unless `UID` and `GID` are
set in `.env` before the build.

On **macOS** that does not matter: Docker Desktop translates ownership across
the mount and the write succeeds regardless.

On **Linux** it fails. Your source tree belongs to you, uid 9999 cannot write
to it, and `makemigrations` dies on a permission error. Set `UID` and `GID` in
`.env` to your own `id -u` and `id -g`, then `docker compose build`.

## Database backups

```bash
make db-dump      # writes to docker/backup/
make db-restore   # DROPS the database, then restores the newest dump
```

Those two targets are development-only -- like everything in the `Makefile`,
they refuse to run unless the development overlay is switched on. The backup
itself is not: `make db-dump` is a wrapper around a management command, and on
a production host you run that command directly.

```bash
docker compose exec wis2watch python manage.py dbbackup
```

A restore needs the writers stopped first. The connector empties the database
before it replays the dump, and the services reconnect to the empty one and
carry on writing -- so a row the stack writes during the restore collides with
the same primary key arriving from the dump, and `pg_restore` fails at the end
building the index. `wis2watch` itself stays up because it is the container
being exec'd into; stopping the proxy is what leaves it idle.

```bash
docker compose stop wis2watch_celery_worker wis2watch_celery_beat \
                    wis2watch_ingest wis2watch_web_proxy
docker compose exec wis2watch python manage.py dbrestore --i-know-this-drops-the-database
docker compose start
```

There is deliberately no `make` target for this. A production deployment drives
`docker compose` itself, and a friendlier name for the restore would put the
command that drops the production database one tab-completion away from the one
that drops the local one.

Both go through `django-dbbackup`, but not through its stock PostgreSQL
connector: that one cannot restore a TimescaleDB database, and the way it fails
is worth knowing about before you need it to work.

`pg_dump` writes a hypertable out as two separate things -- the chunks, as
ordinary tables in a `_timescaledb_internal` schema, and the catalogue rows
saying which tables those are, inside the extension. Replaying either against a
live extension is what TimescaleDB refuses: the catalogue is guarded, and a
chunk restored without its catalogue row is a table that merely resembles part
of a hypertable. The documented way through is to suspend the guards for the
duration, with `timescaledb_pre_restore()` before and
`timescaledb_post_restore()` after -- and dbbackup has nowhere to put those
calls, because `RESTORE_PREFIX` and `RESTORE_SUFFIX` wrap the command line
rather than the SQL.

So `wis2watch/utils/dbbackup.py` subclasses the connector to do it, and
`wis2watch/core/management/commands/dbrestore.py` guards what that costs.

**The restore drops the database.** Not `--clean`: a hypertable cannot be
replayed over its own remains, so the target is dropped and recreated outright.
That makes `dbrestore` a one-command way to destroy whichever database the
environment points at, and on a production host the environment points at
production. Hence `--i-know-this-drops-the-database`, which has no short form
and is not implied by `--noinput`. `make db-restore` passes the flag and leaves
the confirmation prompt in place.

**The database image is pinned.** A TimescaleDB dump can only be restored into
the extension version it was taken from, and a custom-format dump does not
record which that was -- so a mismatch surfaces as catalogue-level strangeness
rather than a legible error. `docker-compose.yml` therefore names
`timescale/timescaledb-ha:pg17.10-ts2.28.1` rather than the floating `pg17` tag,
which would otherwise drift between a server deployed once and a laptop that
pulled last week. Moving the pin is a deliberate act: dumps taken before it
cannot be restored after it.

**Dumps are not in `media/`.** dbbackup 5 reads its storage from
`STORAGES["dbbackup"]`, and silently ignores the older `DBBACKUP_STORAGE`
settings -- so configuring the old ones sends dumps to `MEDIA_ROOT` instead,
which `nginx.conf` serves at `/media/` with nothing in front of it. A dump holds
every broker password in the registry. `settings/base.py` sets the `STORAGES`
alias; leave it there.

## Things worth knowing

**The watchers poll.** Filesystem events from the host do not cross a bind
mount into the container reliably, and `watchfiles` only auto-detects WSL, not
Docker -- so `WATCHFILES_FORCE_POLLING` is set for the three watched services.
A missed restart is silent, and debugging code that is no longer running costs
more than the stat sweep does. `node_modules` is excluded from the sweep
explicitly; it is 8,000 files that are never watched for.

**`ingest` grows.** With `DEBUG` on, Django keeps every SQL query it runs in
`connection.queries`. In the web process that list is cleared each request. The
ingestion supervisor has no requests and never exits, so the list grows for as
long as the container is up. Over a working day it is not worth noticing; if
you leave the stack running for days, `docker compose restart wis2watch_ingest`
puts it back.

**Ingestion runs by default.** The supervisor subscribes to the real WIS2
Global Broker as soon as the stack is up, and writes what it sees to your local
database. That is deliberate -- almost every page in this tool is a view over
ingested data, and an empty database looks like a broken install. If you want
it quiet, `docker compose stop wis2watch_ingest`.

**Attaching to the web container.** `make attach` puts you on the `runserver`
process. Ctrl-C stops the server and leaves you at a shell
inside the container with the command in history, which is also what makes
`breakpoint()` in a view usable. Ctrl-P Ctrl-Q detaches without stopping
anything.
