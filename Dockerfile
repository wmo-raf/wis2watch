# syntax = docker/dockerfile:1.5

# use osgeo gdal ubuntu small 3.11.4 image.
# pre-installed with GDAL 3.11.4
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.11.4 as base

ARG UID
ENV UID=${UID:-9999}
ARG GID
ENV GID=${GID:-9999}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Create or rename group to wis2_docker_group with desired GID
RUN if getent group $GID > /dev/null; then \
        existing_group=$(getent group $GID | cut -d: -f1); \
        if [ "$existing_group" != "wis2_docker_group" ]; then \
            groupmod -n wis2_docker_group "$existing_group"; \
        fi; \
    else \
        groupadd -g $GID wis2_docker_group; \
    fi
RUN useradd --shell /bin/bash -u $UID -g $GID -o -c "" -m wis2_docker_user -l || exit 0
ENV DOCKER_USER=wis2_docker_user

ENV POSTGRES_VERSION=17

# install dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    lsb-release \
    ca-certificates \
    curl \
    libgeos-dev \
    libpq-dev \
    python3-pip --fix-missing \
    gosu \
    git \
    && echo "deb http://apt.postgresql.org/pub/repos/apt/ $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && curl --silent https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - \
    && apt-get update \
    && apt-get install -y postgresql-client-$POSTGRES_VERSION \
    python3-dev \
    python3-venv \
    && apt-get autoclean \
    && apt-get clean \
    && apt-get autoremove \
    && rm -rf /var/lib/apt/lists/*

# install docker-compose wait
ARG DOCKER_COMPOSE_WAIT_VERSION
ENV DOCKER_COMPOSE_WAIT_VERSION=${DOCKER_COMPOSE_WAIT_VERSION:-2.12.1}
ARG DOCKER_COMPOSE_WAIT_PLATFORM_SUFFIX
ENV DOCKER_COMPOSE_WAIT_PLATFORM_SUFFIX=${DOCKER_COMPOSE_WAIT_PLATFORM_SUFFIX:-}

ADD https://github.com/ufoscout/docker-compose-wait/releases/download/$DOCKER_COMPOSE_WAIT_VERSION/wait${DOCKER_COMPOSE_WAIT_PLATFORM_SUFFIX} /wait
RUN chmod +x /wait

USER $UID:$GID

# Install  dependencies into a virtual env.
COPY --chown=$UID:$GID ./wis2watch/requirements.txt /wis2watch/requirements.txt
RUN python3 -m venv /wis2watch/venv

ENV PIP_CACHE_DIR=/tmp/wis2watch_pip_cache
RUN --mount=type=cache,mode=777,target=$PIP_CACHE_DIR,uid=$UID,gid=$GID . /wis2watch/venv/bin/activate && pip3 install  -r /wis2watch/requirements.txt

# Copy over code
COPY --chown=$UID:$GID ./wis2watch /wis2watch/app

WORKDIR /wis2watch/app/src/wis2watch

# Ensure that Python outputs everything that's printed inside
# the application rather than buffering it.
ENV PYTHONUNBUFFERED 1

RUN /wis2watch/venv/bin/pip install --no-cache-dir -e /wis2watch/app/

COPY --chown=$UID:$GID ./docker-entrypoint.sh /wis2watch/docker-entrypoint.sh

ENV DJANGO_SETTINGS_MODULE='wis2watch.config.settings.production'

# Add the venv to the path
ENV PATH="/wis2watch/venv/bin:$PATH"

ENTRYPOINT ["/wis2watch/docker-entrypoint.sh"]

CMD ["gunicorn"]