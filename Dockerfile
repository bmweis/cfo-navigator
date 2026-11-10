FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chromium for linklib.screenshots (Software directory homepage captures) —
# --with-deps pulls in the OS-level libraries headless Chromium needs on
# this base image, not just the browser binary itself.
#
# libatomic1 first (2026-11): recent Playwright releases ship a node driver that
# links libatomic.so.1, which python:3.11-slim does not have, so the install
# step died with exit 127 before it could fetch anything. requirements.txt
# leaves playwright unpinned, so the failure arrived with a new release, not
# with a code change.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
