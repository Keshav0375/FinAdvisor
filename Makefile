# Test entry points. `make test-integration` starts postgres, kafka and the migrate job with docker
# compose, runs the integration tests with REQUIRE_SERVICES=1 (a missing service fails, never
# skips) and always tears the stack down afterwards. It needs no real secrets: it defaults to the
# committed demo values in .env.integration.
#
# Knobs (all overridable on the command line or in the environment):
#   ENV_FILE                 compose env file with the DB_* keys; default .env.integration for
#                            test-integration, .env (see .env.example) for the other targets
#   INTEGRATION_PROJECT      compose project of test-integration (default fa-integration), so its
#                            teardown never touches the volumes of a dev stack
#   COMPOSE_WAIT_TIMEOUT_S   seconds `up --wait` waits for healthy services (default 300)
#   PYTEST                   test runner command (default `poetry run pytest`)
# Host ports (POSTGRES_HOST_PORT, KAFKA_HOST_PORT, DB_PORT) come from ENV_FILE: copy it to change
# them, for example to run two worktrees side by side.

ifneq ($(filter test-integration,$(MAKECMDGOALS)),)
ENV_FILE ?= .env.integration
endif
ENV_FILE ?= .env
override ENV_FILE := $(abspath $(ENV_FILE))
PYTEST ?= poetry run pytest
COMPOSE_WAIT_TIMEOUT_S ?= 300
INTEGRATION_PROJECT ?= fa-integration

COMPOSE = docker compose --env-file $(ENV_FILE)
DEPS_UP = $(COMPOSE) up -d --wait --wait-timeout $(COMPOSE_WAIT_TIMEOUT_S) postgres kafka migrate
DEPS_DOWN = $(COMPOSE) down -v

# Value of KEY: its last KEY=value line in ENV_FILE, surrounding quotes removed. The file is read
# as text and never evaluated by a shell or by make, so a value with spaces or $ stays as written.
dotenv = $(shell sed -n -e 's/^$(1)=//p' $(ENV_FILE) | tail -n 1 | sed -e 's/^"\(.*\)"$$/\1/' -e "s/^'\(.*\)'$$/\1/")

.PHONY: deps-up deps-down test-unit test-integration

deps-up:
	$(DEPS_UP)

deps-down:
	$(DEPS_DOWN)

test-unit:
	cd backend && $(PYTEST) -m "not integration" -q

# The tests read the same DB_* values as the compose services; KAFKA_BOOTSTRAP is the compose host
# listener. The trap runs from the repository root (ENV_FILE is absolute) and tears the stack down
# on success, failure and interrupt.
test-integration: export COMPOSE_PROJECT_NAME = $(INTEGRATION_PROJECT)
test-integration: export DB_HOST = $(call dotenv,DB_HOST)
test-integration: export DB_PORT = $(call dotenv,DB_PORT)
test-integration: export DB_NAME = $(call dotenv,DB_NAME)
test-integration: export DB_USER = $(call dotenv,DB_USER)
test-integration: export DB_PASSWORD = $(call dotenv,DB_PASSWORD)
test-integration: export KAFKA_BOOTSTRAP = localhost:$(or $(call dotenv,KAFKA_HOST_PORT),9092)
test-integration:
	@test -f $(ENV_FILE) || { echo "$(ENV_FILE) not found" >&2; exit 1; }
	trap '$(DEPS_DOWN)' EXIT INT TERM; \
	$(DEPS_UP) && (cd backend && REQUIRE_SERVICES=1 $(PYTEST) -m integration -q)
