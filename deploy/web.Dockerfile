# syntax=docker/dockerfile:1
FROM node:22-slim AS build
RUN corepack enable
WORKDIR /app
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web ./
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.27-alpine
# rendered with envsubst at start: needs API_TOKEN and AUTH_BASIC (see the template)
ENV AUTH_BASIC="fin-app" API_TOKEN=""
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=755 deploy/web-entrypoint.d/10-validate-env.sh /docker-entrypoint.d/10-validate-env.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
