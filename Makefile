# Test entry points. `make test-integration` starts postgres, kafka and the migrate job with docker
# compose, runs the integration tests with REQUIRE_SERVICES=1 (a missing service fails, never
# skips) and always tears the stack down afterwards.
#
# Knobs (all overridable on the command line or in the environment):
#   ENV_FILE                 compose env file with the DB_* keys (default .env, see .env.example)
#   COMPOSE_PROJECT_NAME     compose project; test-integration defaults to its own so teardown
#                            never touches the volumes of a dev stack
#   POSTGRES_HOST_PORT, KAFKA_HOST_PORT, DB_PORT   host ports (see .env.example)
#   PYTEST                   test runner command (default `poetry run pytest`)

ENV_FILE ?= .env
PYTEST ?= poetry run pytest
COMPOSE = docker compose --env-file $(ENV_FILE)
DEPS_UP = $(COMPOSE) up -d --wait postgres kafka migrate
DEPS_DOWN = $(COMPOSE) down -v
INTEGRATION_PROJECT ?= fa-integration

.PHONY: deps-up deps-down test-unit test-integration

deps-up:
	$(DEPS_UP)

deps-down:
	$(DEPS_DOWN)

test-unit:
	cd backend && $(PYTEST) -m "not integration" -q

# The DB_* keys come from ENV_FILE; KAFKA_BOOTSTRAP is the compose host listener. The trap runs
# the same teardown as deps-down on success, failure and interrupt.
test-integration: export COMPOSE_PROJECT_NAME := $(or $(COMPOSE_PROJECT_NAME),$(INTEGRATION_PROJECT))
test-integration:
	@test -f $(ENV_FILE) || { echo "$(ENV_FILE) not found: copy .env.example and fill in the secrets" >&2; exit 1; }
	trap '$(DEPS_DOWN)' EXIT INT TERM; \
	$(DEPS_UP) && \
	{ set -a; . $(abspath $(ENV_FILE)); set +a; \
	  cd backend && REQUIRE_SERVICES=1 KAFKA_BOOTSTRAP=localhost:$${KAFKA_HOST_PORT:-9092} \
	  $(PYTEST) -m integration -q; }
