FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=run.py

EXPOSE 7014

CMD ["flask", "run", "--host=0.0.0.0", "--port=7014"]
