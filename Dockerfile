FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chromium for linklib.screenshots (Software directory homepage captures) —
# --with-deps pulls in the OS-level libraries headless Chromium needs on
# this base image, not just the browser binary itself.
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
