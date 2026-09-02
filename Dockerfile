# syntax=docker/dockerfile:1.7
FROM python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN addgroup --system --gid 10001 ekaa \
    && adduser --system --uid 10001 --gid 10001 ekaa

COPY pyproject.toml README.md ./
COPY constraints.txt ./
COPY backend ./backend
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker/entrypoint.sh /usr/local/bin/ekaa-entrypoint

RUN python -m pip install --upgrade pip \
    && python -m pip install -c constraints.txt . \
    && chmod 0555 /usr/local/bin/ekaa-entrypoint \
    && chown -R ekaa:ekaa /app

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

ENTRYPOINT ["/usr/local/bin/ekaa-entrypoint"]
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
