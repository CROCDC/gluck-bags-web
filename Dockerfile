FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=run.py
ENV FLASK_DEBUG=0

EXPOSE 7014

# Production WSGI server (Werkzeug's dev server is single-threaded and unhardened).
CMD ["gunicorn", "--bind", "0.0.0.0:7014", "--workers", "2", "--threads", "4", "run:app"]
