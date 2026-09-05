FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY metrics.py nginx_manager.py app.py ./

RUN mkdir -p /etc/nginx

EXPOSE 8400
ENV NGINX_WEBUI_PORT=8400 NGINX_CONF_DIR=/etc/nginx

HEALTHCHECK --interval=30s --timeout=3s CMD curl -fsS http://127.0.0.1:8400/readyz || exit 1

CMD ["python", "app.py"]