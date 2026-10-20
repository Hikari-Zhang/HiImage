FROM node:22-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28-alpine

USER root
RUN apk add --no-cache libcap \
    && setcap cap_net_bind_service=+ep /usr/sbin/nginx

COPY frontend/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html

USER 101

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
