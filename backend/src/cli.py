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


def _docker_compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", *args]
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
            "           Create backend/.env with at minimum:\n"
            "             ANTHROPIC_API_KEY=sk-ant-...\n"
            "             VOYAGE_API_KEY=pa-..."
        )
        sys.exit(1)

    env_vars = _read_env_file()
    missing = []
    if not env_vars.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not env_vars.get("VOYAGE_API_KEY"):
        missing.append("VOYAGE_API_KEY")

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
    _print("Starting PostgreSQL (pgvector)...")
    _docker_compose("up", "-d", "postgres")


def wait_for_postgres(timeout: int = 30) -> None:
    _print("Waiting for PostgreSQL...")
    start = time.time()
    while time.time() - start < timeout:
        result = _docker_compose(
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            "-U",
            "finadvisor",
            capture=True,
        )
        if result.returncode == 0:
            _print("PostgreSQL is ready.")
            return
        time.sleep(1)
    _error(f"PostgreSQL not ready after {timeout}s. Check 'docker compose logs postgres'.")
    sys.exit(1)


def _psql_query(query: str) -> tuple[int, str]:
    result = _docker_compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "finadvisor",
        "-d",
        "finadvisor",
        "-tAc",
        query,
        capture=True,
    )
    return result.returncode, result.stdout.strip()


def ensure_schema() -> None:
    rc, out = _psql_query(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='documents'"
    )
    if rc == 0 and out == "1":
        _print("Database schema already applied.")
        return

    _print("Applying database schema...")
    result = _docker_compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "finadvisor",
        "-d",
        "finadvisor",
        "-f",
        "/docker-entrypoint-initdb.d/01-schema.sql",
        capture=True,
    )
    if result.returncode != 0:
        _error(f"Schema apply failed:\n{result.stderr}")
        sys.exit(1)
    _print("Schema applied.")


def ensure_data_ingested() -> None:
    rc, out = _psql_query("SELECT count(*) FROM chunks")
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
    env["LLM_BASE_URL"] = "https://api.anthropic.com"
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
    wait_for_postgres()
    ensure_schema()
    ensure_data_ingested()

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
