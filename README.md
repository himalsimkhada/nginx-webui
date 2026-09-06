# nginx-webui

API-only management backend for nginx. JSON API only — no UI, no templates.
It is designed to be driven by the **webui** admin facade (same author), which
talks to this service over plain HTTP on port `8400`.

- **Status** – version, running/master PID, worker count, config file.
- **Config editor** – browse/edit any file under `/etc/nginx` (path-traversal safe).
- **Server-block wizard** – generate a reverse-proxy site in one call (`nginx -t`-friendly).
- **Sites** – list, create, read, edit, delete, enable/disable (`sites-available` ↔ `sites-enabled`).
- **Validation & control** – `nginx -t`, reload (`nginx -s reload`), restart (`systemctl restart nginx`).
- **Logs** – tail error/access logs with optional filtering.
- **Backup/Restore** – one-click config tarball; restore validates with `nginx -t` and rolls back on failure.
- **Metrics** – `/metrics`, `/healthz`, `/readyz` (Prometheus text, dependency-free): per-process
  memory, CPU, I/O, uptime, open fds, threads, HTTP counters, service health/readiness.
- **Shared auth** – same `WEBUI_PASSWORD` as every webui-family service; the facade
  auto-logs-in from server to server.

## Quick start

```bash
git clone git@github.com:himalsimkhada/nginx-webui.git
cd nginx-webui
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
WEBUI_PASSWORD='your-password' SECRET_KEY='a-long-random-string' ./venv/bin/python app.py
```

The API listens on **8400** (`NGINX_WEBUI_PORT`). Point the webui facade at it:

```
PORTAL_MODULES=nginx=http://127.0.0.1:8400,bind=http://127.0.0.1:5000
```

### Permissions

File edits and site symlinks under `/etc/nginx` need root. Either run as root or
add passwordless rules for the service account:

```bash
echo 'himal ALL=(ALL) NOPASSWD: /usr/sbin/nginx, /usr/sbin/systemctl restart nginx' > /etc/sudoers.d/nginx-webui
```

### Docker

```bash
cp .env.example .env
docker compose up -d --build
```

The container mounts `/etc/nginx` and `/var/log/nginx` from the host, so config
editing, `nginx -t`, and site management work from the container. The container
**cannot** signal or restart the host nginx master itself, so to make status,
`nginx -t`, reload and restart act on the *host* nginx, run the **host control
agent** on the nginx host and point the container at it:

```bash
# on the nginx host, as root
sudo cp host_agent.py /usr/local/bin/nginx-webui-host-agent.py
sudo cp nginx-webui-host-agent.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now nginx-webui-host-agent
```

```bash
# in .env (docker-compose)
NGINX_STATUS_URL=http://host.docker.internal:8080/nginx_status   # optional, host stub_status
NGINX_CTL_URL=http://host.docker.internal:9401                   # mandatory for control
```

On bare metal (systemd/venv on the host) leave both empty — the service talks
to the host nginx directly (needs the `sudo` rules below).

### The `.env` in the UI

The nginx-webui environment file (where `NGINX_STATUS_URL` / `NGINX_CTL_URL`
live) shows up as **`.env`** in the dashboard's Config Files editor so you can
see and change it without SSH. Bare metal: `/etc/nginx-webui.env` (the systemd
`EnvironmentFile`). Docker: `./.env` next to `docker-compose.yml` is mounted at
that path (`NGINX_WEBUI_ENV_FILE=/etc/nginx-webui.env` by default; override with
`NGINX_WEBUI_ENV_FILE`). Edits apply after restarting the service or
`docker compose up -d`.

## API

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/login` | Login (shared password) |
| GET | `/api/session` | Auth state |
| GET | `/api/status` | Version, pid, workers, config file |
| GET · POST | `/api/check` | `nginx -t` validation |
| POST | `/api/control/reload` | `nginx -s reload` |
| POST | `/api/control/restart` | `systemctl restart nginx` |
| GET | `/api/config/files` | Editable files |
| GET · PUT | `/api/config/file/<path>` | Read / write a config file |
| GET | `/api/sites` | Site list (enabled + available) |
| GET · PUT · DELETE | `/api/site/<name>` | Site CRUD |
| POST | `/api/site/<name>/toggle` | Enable / disable site |
| POST | `/api/site` | Create reverse-proxy site |
| GET | `/api/logs?lines=&query=` | Error/Access log tail |
| GET | `/api/backup` | Config tarball (base64) |
| POST | `/api/restore` | Apply + `nginx -t` + rollback |
| GET | `/healthz` `/readyz` | Probes (open) |
| GET | `/metrics` | Prometheus metrics (auth-gated) |
| GET | `/api/metrics/portal` | Gauges as JSON |

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest        # 32 tests, no nginx required (binary faked)
```

CI: GitHub Actions (pytest + flake8) on every push/PR.

## License

MIT