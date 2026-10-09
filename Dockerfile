# Standalone Markdown -> PPTX service.
# Node base image supplies the converter runtime; Chromium is installed at build
# time OUTSIDE the source tree so that a code bind-mount cannot hide it.
# BASE_IMAGE lets an unreachable Docker Hub be bypassed with a registry mirror.
ARG BASE_IMAGE=node:20-bookworm-slim
FROM ${BASE_IMAGE}

# Official CDN is unreachable from some networks; override for a local mirror.
ARG DEBIAN_MIRROR=
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright

ENV PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST} \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    NODE_PATH=/opt/pptsvc/node_modules \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PPTSVC_OUTPUT_DIR=/srv/pptsvc/output \
    PPTSVC_MAX_CONCURRENCY=2

# python3 for the API, Noto CJK so Chinese decks render with correct metrics.
# DEBIAN_MIRROR rewrites the apt sources; apt retries also cover the apt call
# that "playwright install --with-deps" makes for Chromium's system libraries.
RUN set -eux; \
    if [ -n "${DEBIAN_MIRROR}" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        if [ -f "$f" ]; then \
          sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g; s|security.debian.org|${DEBIAN_MIRROR}|g" "$f"; \
        fi; \
      done; \
    fi; \
    echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates \
      fonts-noto-cjk fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /srv/pptsvc
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Converter dependencies are installed once, outside the mounted source tree.
COPY vendor/html2pptx/package.json vendor/html2pptx/package-lock.json /opt/pptsvc/
RUN cd /opt/pptsvc && npm ci --omit=dev \
 && /opt/pptsvc/node_modules/.bin/playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY vendor ./vendor
COPY assets ./assets
RUN mkdir -p /srv/pptsvc/output

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=8).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
