# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE_DIGEST=sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
FROM python:3.12-slim-bookworm@${PYTHON_IMAGE_DIGEST}

ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Command Centre" \
      org.opencontainers.image.description="A small personal command centre for task and life administration" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/Ruairi-Grant/Command-center"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/lib

RUN groupadd --system --gid 10001 commandcentre \
    && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent \
       --shell /usr/sbin/nologin commandcentre \
    && mkdir -p /app /var/lib/command-centre/data /var/lib/command-centre/backups \
    && chown -R 10001:10001 /app /var/lib/command-centre

WORKDIR /app
COPY --chown=10001:10001 lib ./lib

USER 10001:10001
EXPOSE 8787
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health/live', timeout=2).read()"

ENTRYPOINT ["python", "/app/lib/cc_dashboard.py"]
