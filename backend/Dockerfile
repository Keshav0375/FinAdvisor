# syntax=docker/dockerfile:1

# Every FROM names the base image by digest (ADR-021), so the digest is repeated
# on each line instead of deriving stages from one another. Tag at pin time:
# python:3.12-slim. To bump, replace the digest on all FROM lines.

# Poetry, kept in its own venv so it never ends up in an application venv.
# Poetry 2.1.2 is the version that generated poetry.lock; it bundles
# poetry-core 2.1.2, which pyproject.toml pins in [build-system].requires.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS poetry
ARG POETRY_VERSION=2.1.2
RUN python -m venv /opt/poetry \
    && /opt/poetry/bin/pip install --no-cache-dir "poetry==${POETRY_VERSION}"

# Main dependencies, installed strictly from poetry.lock into /opt/venv.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder
COPY --from=poetry /opt/poetry /opt/poetry
RUN python -m venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/opt/poetry/bin:$PATH \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1
WORKDIR /app
COPY pyproject.toml poetry.lock ./
# Fails the build when pyproject.toml and poetry.lock are out of sync.
RUN poetry check --lock \
    && poetry install --no-root --only main --no-interaction

# Main + dev dependencies, for the dev image.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder-dev
COPY --from=poetry /opt/poetry /opt/poetry
RUN python -m venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/opt/poetry/bin:$PATH \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1
WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry check --lock \
    && poetry install --no-root --with dev --no-interaction

# Dev image: main + dev dependencies, tests, reload on change.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder-dev /opt/venv /opt/venv
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app src ./src
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app tests ./tests
USER app
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]

# Runtime image (last stage, so a plain build targets it): main dependencies only.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app src ./src
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app db ./db
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
USER app
EXPOSE 8000
# Entrypoints: `api` (default) and `jobs migrate`; anything else exits 2 with usage.
ENTRYPOINT ["python", "-m", "src.entrypoint"]
CMD ["api"]
