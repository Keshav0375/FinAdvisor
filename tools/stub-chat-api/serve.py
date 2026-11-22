"""Entrypoint: runs the stub with uvicorn, taking every listener setting from Settings."""

from __future__ import annotations

import uvicorn

from app import create_app
from config import Settings


def main(settings: Settings | None = None) -> None:
    cfg = settings or Settings.from_env()
    uvicorn.run(
        create_app(cfg),
        host=cfg.host,
        port=cfg.port,
        timeout_keep_alive=cfg.keepalive_timeout_s,
    )


if __name__ == "__main__":
    main()
