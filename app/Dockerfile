# Pinned to bookworm (Debian 12) rather than the floating python:3.12-slim tag, which
# currently resolves to trixie (Debian 13) and had an unfixed-in-repo HIGH CVE (bsdutils,
# CVE-2026-53615) at the time this Dockerfile was written — verified clean via `trivy image`.
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/main.py .

ENV DB_PATH=/tmp/notes.db
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
