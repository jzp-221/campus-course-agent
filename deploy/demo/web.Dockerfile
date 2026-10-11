# Node 22 satisfies the existing Vite engine constraint (>= 22.18).
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Requests stay same-origin; no secret or local Vite environment is included.
ENV VITE_API_BASE_URL=""
RUN npm run build

FROM caddy:2-alpine
COPY deploy/demo/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /build/frontend/dist /srv
