from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

import structlog
import uvicorn
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from src.config import Settings
from src.observability.logging import configure_logging

log = structlog.get_logger()

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# Exit codes: argparse already uses 2 for a bad command line.
EXIT_OK = 0
EXIT_FAILED = 1

# Bind address inside a container; the platform decides what can reach the port.
API_HOST = "0.0.0.0"  # nosec B104


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.entrypoint")
    commands = parser.add_subparsers(dest="command", required=True, metavar="{api,jobs}")
    commands.add_parser("api", help="serve the HTTP API (one uvicorn process)")
    jobs = commands.add_parser("jobs", help="run a one-off job")
    job_names = jobs.add_subparsers(dest="job", required=True, metavar="{migrate}")
    job_names.add_parser("migrate", help="apply database migrations up to head")
    return parser


def redact(message: str, settings: Settings) -> str:
    """Remove the database password (raw and URL-quoted) from `message`."""
    password = settings.db_password.get_secret_value()
    for variant in {password, quote(password, safe="")}:
        message = message.replace(variant, "***")
    return message


def run_api(settings: Settings) -> int:
    uvicorn.run("src.main:app", host=API_HOST, port=settings.port, workers=1)
    return EXIT_OK


def run_migrate(settings: Settings) -> int:
    config = Config(str(ALEMBIC_INI))
    try:
        command.upgrade(config, "head")
    except Exception as exc:  # any failure must end the job non-zero without leaking the DSN
        log.error(
            "migrate_failed",
            error_type=type(exc).__name__,
            error=redact(str(exc), settings),
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        )
        return EXIT_FAILED
    log.info("migrate_done", database=settings.db_name)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(json_output=True)
    try:
        settings = Settings()
    except ValidationError as exc:
        log.error("config_invalid", error=str(exc))
        return EXIT_FAILED
    if args.command == "api":
        return run_api(settings)
    return run_migrate(settings)


if __name__ == "__main__":
    sys.exit(main())
