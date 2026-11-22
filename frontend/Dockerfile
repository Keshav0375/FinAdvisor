# syntax=docker/dockerfile:1

# The FROM line names the base image by digest (ADR-021). Tag at pin time:
# node:20-slim. To bump, replace the digest on every FROM line.

# Dev image: all dependencies from package-lock.json, Vite dev server with
# hot reload on port 3000. It is the only stage, so a plain `docker build` targets it too.
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS dev
WORKDIR /app
# The base image ships a non-root `node` user (uid 1000).
RUN chown node:node /app
USER node
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci
COPY --chown=node:node . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
