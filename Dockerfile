FROM python:3.11-slim

WORKDIR /app

# ffmpeg: transcodes/compresses product videos uploaded from the admin.
# gcc/python3-dev: build deps for any wheel that needs compiling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc python3-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data (SQLite DB + uploaded media) lives here. It is mounted from a
# named Docker volume in docker-compose so it survives image rebuilds/redeploys.
ENV DATA_DIR=/data
RUN mkdir -p /data

ENV FLASK_APP=run.py
ENV FLASK_DEBUG=0

EXPOSE 7014

# Take a safety DB backup before the app starts (see entrypoint.sh + scripts/
# backup_db.py). chmod so a checkout that lost the exec bit still runs.
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]

# Production WSGI server (Werkzeug's dev server is single-threaded and unhardened).
# --timeout 300: admin video uploads are transcoded synchronously with ffmpeg,
# which can take longer than gunicorn's 30s default on bigger clips.
CMD ["gunicorn", "--bind", "0.0.0.0:7014", "--workers", "2", "--threads", "4", "--timeout", "300", "run:app"]
