# Development shortcuts. Every target here assumes the development overlay, so
# they all go through the same switch: COMPOSE_FILE in your .env. There is no
# production target -- a deployment runs `docker compose` directly, and giving
# it a friendlier name here would only make the two easier to confuse.
#
# `make` on its own lists what is available. See docs/development.md.

FRONTEND_DIR := wis2watch/src/wis2watch/monitoring/wis2watch-monitoring
COMPOSE := docker compose

.DEFAULT_GOAL := help

# ======================================================
# GUARD
# ======================================================

# Refuses to run if the development overlay is not switched on. Without this a
# missing .env line means `make up` quietly starts the production stack, which
# looks identical until you save a file and nothing happens.
.PHONY: check-dev
check-dev:
	@grep -qE '^[[:space:]]*COMPOSE_FILE=.*docker-compose\.dev\.yml' .env 2>/dev/null || { \
		echo "The development overlay is not enabled."; \
		echo ""; \
		echo "Add this line to .env, uncommented:"; \
		echo ""; \
		echo "  COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml"; \
		echo ""; \
		echo "See docs/development.md."; \
		exit 1; \
	}

# ======================================================
# THE STACK
# ======================================================

.PHONY: up
up: check-dev ## Start the stack in the foreground (ctrl-c stops it)
	$(COMPOSE) up

.PHONY: start
start: check-dev ## Start the stack in the background
	$(COMPOSE) up -d

.PHONY: stop
stop: check-dev ## Stop the containers, keeping them
	$(COMPOSE) stop

.PHONY: down
down: check-dev ## Stop and remove the containers (the database volume survives)
	$(COMPOSE) down

.PHONY: restart
restart: check-dev ## Restart every service
	$(COMPOSE) restart

.PHONY: build
build: check-dev ## Rebuild the image -- needed after a requirements.txt or entrypoint change
	$(COMPOSE) build

.PHONY: ps
ps: check-dev ## What is running
	$(COMPOSE) ps

.PHONY: logs
logs: check-dev ## Follow every service's logs
	$(COMPOSE) logs -f

.PHONY: logs-web logs-worker logs-beat logs-ingest
logs-web: check-dev ## Follow the web server's logs
	$(COMPOSE) logs -f wis2watch

logs-worker: check-dev ## Follow the celery worker's logs
	$(COMPOSE) logs -f wis2watch_celery_worker

logs-beat: check-dev ## Follow the celery beat logs
	$(COMPOSE) logs -f wis2watch_celery_beat

logs-ingest: check-dev ## Follow the ingestion supervisor's logs
	$(COMPOSE) logs -f wis2watch_ingest

# ======================================================
# FRONTEND
# ======================================================

.PHONY: frontend
frontend: $(FRONTEND_DIR)/node_modules ## Run the Vite dev server on :5173, with hot reload
	cd $(FRONTEND_DIR) && npm run dev

.PHONY: frontend-build
frontend-build: $(FRONTEND_DIR)/node_modules ## Build the Vue bundles -- commit the result
	cd $(FRONTEND_DIR) && npm run build

.PHONY: frontend-test
frontend-test: $(FRONTEND_DIR)/node_modules ## Run the island unit tests (vitest)
	cd $(FRONTEND_DIR) && npm test

.PHONY: frontend-install
frontend-install: ## Reinstall the frontend dependencies from the lockfile
	cd $(FRONTEND_DIR) && npm ci

# Installs on first use rather than making you remember to, and reinstalls when
# the lockfile moves. Not .PHONY: whether the directory is up to date is the
# whole test.
$(FRONTEND_DIR)/node_modules: $(FRONTEND_DIR)/package-lock.json
	cd $(FRONTEND_DIR) && npm install
	@touch $(FRONTEND_DIR)/node_modules

# ======================================================
# DJANGO
# ======================================================

# `docker compose exec` does not run the image's entrypoint, so the entrypoint's
# `manage` command is not on PATH for it. The container's workdir and PATH are
# already right, so manage.py is called directly.
MANAGE := $(COMPOSE) exec wis2watch python manage.py

.PHONY: migrate
migrate: check-dev ## Apply migrations
	$(MANAGE) migrate

.PHONY: makemigrations
makemigrations: check-dev ## Write new migrations (Linux: needs UID/GID set, see docs/development.md)
	$(MANAGE) makemigrations

.PHONY: superuser
superuser: check-dev ## Create a login
	$(MANAGE) createsuperuser

.PHONY: collectstatic
collectstatic: check-dev ## Collect static files, so the nginx proxy on :80 serves current ones
	$(MANAGE) collectstatic --noinput

# What to run when no T= is given: everything.
TEST_LABEL = $(if $(T),$(T),wis2watch)

.PHONY: test
test: check-dev ## Run the test suite (T=dotted.path to narrow it)
	$(MANAGE) test $(TEST_LABEL) --noinput

# Building the test database means running every migration, which is most of
# the wait on a short run. Keeping it is worth minutes over an afternoon spent
# on one module -- at the price that a schema change lands in a database that
# already exists, so if a test starts failing in a way that makes no sense,
# run `make test` once to build it afresh.
.PHONY: test-keepdb
test-keepdb: check-dev ## The same, reusing the test database (fast to rerun)
	$(MANAGE) test $(TEST_LABEL) --keepdb

.PHONY: shell
shell: check-dev ## A Django shell
	$(MANAGE) shell

.PHONY: bash
bash: check-dev ## A bash prompt inside the web container
	$(COMPOSE) exec wis2watch bash

.PHONY: attach
attach: check-dev ## Attach to runserver -- ctrl-c stops it, ctrl-p ctrl-q detaches
	$(COMPOSE) attach wis2watch

# ======================================================
# DATABASE BACKUPS
# ======================================================

.PHONY: db-dump
db-dump: check-dev ## Dump the database to docker/backup/
	$(MANAGE) dbbackup

# Not $(MANAGE): that is `compose exec`, which needs the web container running,
# and `dbrestore` refuses to start while anything else holds a connection to
# the database it is about to empty. So the stack comes down, the restore runs
# in a container of its own, and the stack goes back up -- back up even when
# the restore failed, which is why this is one shell line keeping the status.
#
# No --noinput: the prompt is the second of the two things standing between a
# mistyped environment and a destroyed database. The flag is the first, and it
# is spelled out rather than abbreviated on purpose. See docs/development.md.
WRITERS = wis2watch wis2watch_celery_worker wis2watch_celery_beat wis2watch_ingest wis2watch_web_proxy

.PHONY: db-restore
db-restore: check-dev ## Restore the newest dump -- DROPS the current database first
	@$(COMPOSE) stop $(WRITERS); \
	$(COMPOSE) run --rm --no-deps wis2watch manage dbrestore --i-know-this-drops-the-database; \
	status=$$?; \
	$(COMPOSE) start; \
	exit $$status

# ======================================================
# HELP
# ======================================================

.PHONY: help
help: ## List these targets
	@echo "WIS2Watch development. Browse the stack at http://localhost:8000/"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
