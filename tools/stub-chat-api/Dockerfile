# syntax=docker/dockerfile:1

# Test tooling for the gateway epic (E2). Never deploy this image outside spike and test
# environments. The base image is named by digest (ADR-021), the same digest as
# backend/Dockerfile. Tag at pin time: python:3.12-slim. To bump, replace the digest on every
# FROM line (here and in backend/Dockerfile).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder
RUN python -m venv /opt/venv
WORKDIR /build
COPY requirements.txt ./
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app.py config.py serve.py tokens.py ./
USER app
EXPOSE 8080
# Every probe has a 2 s socket timeout and a 5 s overall timeout. The port comes from Settings,
# the same source the server reads.
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; from config import Settings; u.urlopen('http://127.0.0.1:%d/echo' % Settings.from_env().port, timeout=2)"]
# One process per container. serve.py reads host, port and keep-alive timeout from Settings.
CMD ["python", "serve.py"]
