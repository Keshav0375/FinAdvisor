from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"

# Keys Settings requires at boot (see src/config.py); checked early for a friendlier message.
REQUIRED_ENV_KEYS = (
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_POOL_SIZE",
    "DB_STATEMENT_TIMEOUT_MS",
    "ANTHROPIC_API_KEY",
    "VOYAGE_API_KEY",
    "JWT_SECRET",
)

# Seconds `docker compose up --wait` waits for healthchecks; override with COMPOSE_WAIT_TIMEOUT_S.
DEFAULT_COMPOSE_WAIT_TIMEOUT_S = 300

BANNER = """
====================================================
  FinAdvisor — Compliance-Aware Wealth Advisor
====================================================
"""


def _print(msg: str) -> None:
    print(f"  [finadvisor] {msg}")


def _error(msg: str) -> None:
    print(f"  [finadvisor] ERROR: {msg}", file=sys.stderr)


def _read_env_file() -> dict[str, str]:
    env_file = BACKEND_DIR / ".env"
    env_vars: dict[str, str] = {}
    if not env_file.exists():
        return env_vars
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env_vars[key.strip()] = val.strip().strip('"').strip("'")
    return env_vars


def _compose_wait_timeout() -> str:
    raw = os.environ.get("COMPOSE_WAIT_TIMEOUT_S", str(DEFAULT_COMPOSE_WAIT_TIMEOUT_S))
    if not raw.isdigit() or int(raw) <= 0:
        _error(f"COMPOSE_WAIT_TIMEOUT_S must be a positive number of seconds, got '{raw}'.")
        sys.exit(1)
    return raw


def _docker_compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    # Compose reads the same backend/.env as the launcher, so host ports and credentials agree.
    cmd = ["docker", "compose", "--env-file", str(BACKEND_DIR / ".env"), *args]
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=capture,
        text=True,
    )


def _kill_port(port: int) -> None:
    if platform.system() != "Windows":
        return
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            pid = parts[-1]
            if pid.isdigit() and int(pid) > 0:
                _print(f"Killing stale process on port {port} (PID {pid})...")
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True,
                )


def check_docker() -> None:
    _print("Checking Docker engine...")
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            _error(
                "Docker engine is not running.\n"
                "           Start Docker Desktop first, then re-run this command."
            )
            sys.exit(1)
    except FileNotFoundError:
        _error(
            "Docker CLI not found.\n"
            "           Install Docker Desktop: https://docs.docker.com/desktop/"
        )
        sys.exit(1)
    _print("Docker is running.")


def check_env() -> dict[str, str]:
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        _error(
            f".env not found at {env_file}\n"
            "           Copy .env.example to backend/.env and replace every REPLACE_ME."
        )
        sys.exit(1)

    env_vars = _read_env_file()
    missing = [key for key in REQUIRED_ENV_KEYS if not env_vars.get(key)]

    if missing:
        _error(
            f"Missing keys in backend/.env: {', '.join(missing)}\n"
            "           Add them and try again."
        )
        sys.exit(1)

    _print("Environment keys verified.")
    return env_vars


def free_ports() -> None:
    _print("Checking ports 8000 and 3000...")
    _kill_port(8000)
    _kill_port(3000)


def start_postgres() -> None:
    # --wait returns once the pg_isready healthcheck passes (or fails after its retries).
    _print("Starting PostgreSQL (pgvector)...")
    result = _docker_compose(
        "up", "-d", "--wait", "--wait-timeout", _compose_wait_timeout(), "postgres"
    )
    if result.returncode != 0:
        _error("PostgreSQL did not become healthy. Check 'docker compose logs postgres'.")
        sys.exit(1)
    _print("PostgreSQL is ready.")


def _psql_query(query: str, env_vars: dict[str, str]) -> tuple[int, str]:
    result = _docker_compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        env_vars["DB_USER"],
        "-d",
        env_vars["DB_NAME"],
        "-tAc",
        query,
        capture=True,
    )
    return result.returncode, result.stdout.strip()


def ensure_schema() -> None:
    # Alembic owns the schema. The migrate service (backend image, `jobs migrate`) refuses, and
    # explains the one-off stamp, when the database was built from schema.sql before migrations.
    _print("Applying database migrations (compose service 'migrate')...")
    result = _docker_compose("up", "--no-deps", "--exit-code-from", "migrate", "migrate")
    if result.returncode != 0:
        _error("Database migration failed. Check output above.")
        sys.exit(1)
    _print("Database schema is at the latest revision.")


def ensure_data_ingested(env_vars: dict[str, str]) -> None:
    rc, out = _psql_query("SELECT count(*) FROM chunks", env_vars)
    if rc == 0 and out.isdigit() and int(out) > 0:
        _print(f"Data already ingested ({out} chunks) — skipping.")
        return

    corpus_count = len(list(CORPUS_DIR.rglob("*.json")))
    if corpus_count == 0:
        _error(
            "No corpus files found. Run:\n"
            "           cd backend && python scripts/generate_corpus.py"
        )
        sys.exit(1)

    _print(f"Ingesting {corpus_count} documents (calls Voyage AI for embeddings)...")
    result = subprocess.run(
        [sys.executable, "scripts/ingest.py"],
        cwd=str(BACKEND_DIR),
    )
    if result.returncode != 0:
        _error("Ingest failed. Check output above.")
        sys.exit(1)
    _print("Ingest complete.")


def start_backend(env_vars: dict[str, str]) -> subprocess.Popen[bytes]:
    _print("Starting backend on http://localhost:8000 ...")
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = env_vars["ANTHROPIC_API_KEY"]
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--reload",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
    )


def wait_for_backend(timeout: int = 60) -> None:
    _print("Waiting for backend (up to 60s on cold start)...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = urlopen("http://127.0.0.1:8000/api/health", timeout=2)
            if resp.status == 200:
                _print("Backend is healthy.")
                return
        except (URLError, OSError):
            pass
        time.sleep(2)
    _error(
        "Backend did not start within 60s.\n           Check the uvicorn output above for errors."
    )
    sys.exit(1)


def start_frontend() -> subprocess.Popen[bytes]:
    npm = shutil.which("npm")
    if not npm:
        _error("npm not found. Install Node.js 20 LTS:\n           https://nodejs.org/")
        sys.exit(1)

    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.exists():
        _print("Installing frontend dependencies (npm install)...")
        subprocess.run([npm, "install"], cwd=str(FRONTEND_DIR), check=True)

    _print("Starting frontend on http://localhost:3000 ...")
    return subprocess.Popen(
        [npm, "run", "dev"],
        cwd=str(FRONTEND_DIR),
    )


def main() -> None:
    print(BANNER)

    check_docker()
    env_vars = check_env()
    free_ports()

    start_postgres()
    ensure_schema()
    ensure_data_ingested(env_vars)

    backend_proc = start_backend(env_vars)
    wait_for_backend()
    frontend_proc = start_frontend()

    print()
    print("=" * 52)
    print("  FinAdvisor is ready!")
    print()
    print("  Frontend:  http://localhost:3000")
    print("  Backend:   http://localhost:8000")
    print("  API docs:  http://localhost:8000/docs")
    print()
    print("  LLM mode:  Anthropic API (direct, no gateway)")
    print("  Database:  PostgreSQL + pgvector (Docker)")
    print()
    print("  Select a user from the dropdown, then ask:")
    print('    "Is the Meridian Core Bond Fund suitable')
    print('     for a conservative retiree?"')
    print()
    print("  Press Ctrl+C to stop all services")
    print("=" * 52)
    print()

    try:
        backend_proc.wait()
    except KeyboardInterrupt:
        print()
        _print("Shutting down...")
        frontend_proc.terminate()
        backend_proc.terminate()
        try:
            frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            frontend_proc.kill()
        try:
            backend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_proc.kill()
        _print("Services stopped.")
        _print("PostgreSQL container still running.")
        _print("Run 'docker compose down' to stop it.")


if __name__ == "__main__":
    main()
