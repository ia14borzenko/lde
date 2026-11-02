# Сервер LDE. Сборка из корня репозитория LDE:
#   docker compose -f lde-server/compose.behind-proxy.yaml up -d --build
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN useradd --system --uid 10001 --home-dir /app lde \
 && mkdir -p /data && chown lde /data \
 && printf '#!/bin/sh\ncd /app && exec python -m app.cli "$@"\n' > /usr/local/bin/lde-server \
 && chmod 755 /usr/local/bin/lde-server
WORKDIR /app
COPY lde-server/app /app/app
COPY lde-server/web /app/web
COPY lde/studio/static/vendor/mathlive /app/vendor/mathlive
COPY lde/studio/static/index.html lde/studio/static/app.js lde/studio/static/app.css /app/studio/
ENV LDE_DATA=/data LDE_PORT=8790 LDE_HOST=0.0.0.0 LDE_VENDOR=/app/vendor LDE_STUDIO=/app/studio \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER lde
EXPOSE 8790
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=3)"
ENTRYPOINT ["lde-server"]
CMD ["serve"]
