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

The container mounts `/etc/nginx` and `/var/log/nginx` from the host. Config
editing, `nginx -t`, and site management work from the container; `reload`/
`restart` are **host-side only** (they signal the host master process).

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