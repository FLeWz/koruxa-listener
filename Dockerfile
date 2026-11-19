# Python worker: polls Koruxa, posts to Discord, runs market refreshes.
# It attaches to a Chrome that runs elsewhere (CHROME_CDP_URL); no browser is installed here.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Same uid as the web image, so both can write to the shared /data volume.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /data && chown app /data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py main.py ./
COPY core core
COPY services services

USER app

CMD ["python", "main.py"]
