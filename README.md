<div align="center">

```
                     /$$                                                    /$$                 /$$
                    |__/                                                   | $$                |__/
 /$$$$$$$   /$$$$$$  /$$ /$$$$$$$  /$$   /$$        /$$  /$$  /$$  /$$$$$$ | $$$$$$$  /$$   /$$ /$$
| $$__  $$ /$$__  $$| $$| $$__  $$|  $$ /$$//$$$$$$| $$ | $$ | $$ /$$__  $$| $$__  $$| $$  | $$| $$
| $$  \ $$| $$  \ $$| $$| $$  \ $$ \  $$$$/|______/| $$ | $$ | $$| $$$$$$$$| $$  \ $$| $$  | $$| $$
| $$  | $$| $$  | $$| $$| $$  | $$  >$$  $$        | $$ | $$ | $$| $$_____/| $$  | $$| $$  | $$| $$
| $$  | $$|  $$$$$$$| $$| $$  | $$ /$$/\  $$       |  $$$$$/$$$$/|  $$$$$$$| $$$$$$$/|  $$$$$$/| $$
|__/  |__/ \____  $$|__/|__/  |__/|__/  \__/        \_____/\___/  \_______/|_______/  \______/ |__/
           /$$  \ $$                                                                               
          |  $$$$$$/                                                                               
           \______/                                                                                
```

### Manage your nginx reverse proxies from a modern web UI

**A lightweight, dependency-free web interface** for nginx reverse-proxy management — sites, TLS, redirects, config editing, `nginx -t`, reload/restart, logs and backup, exactly like you do from the shell. Zero rebuilding, no database, ~40 MB RAM.

<!-- badges (static, no network lookups) -->
![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-1.17+-009639?logo=nginx&logoColor=white)
![Stack](https://img.shields.io/badge/Flask-Docker-Vanilla%20JS-green)
![Docker](https://img.shields.io/badge/Docker-24273D?logo=docker&logoColor=white)
![Style](https://img.shields.io/badge/dark%20%2F%20light-mode-blueviolet)
![License](https://img.shields.io/badge/license-MIT-orange)
![maintained](https://img.shields.io/badge/maintained-yes-2ea44f)
![PRs](https://img.shields.io/badge/PRs-welcome-2ea44f)

One command install, or run it from a container. Manages **bare-metal nginx** or
nginx running on the same host from Docker — through a tiny host-side control
agent. Web UI frontends with automatically-guessed Let's Encrypt-friendly TLS paths.

</div>

---

## Install — one line

Copy-paste this. No cloning, no setup — the installer bootstraps itself, then asks
which deployment you want:

```bash
curl -fsSL https://raw.githubusercontent.com/himalsimkhada/nginx-webui/main/install.sh | bash
```

> Not ready yet? `./install.sh --check` does a safe dry run and reports what the
> installer would detect on your machine without changing anything.

---

## Table of Contents

- [Why nginx-webui?](#why-nginx-webui)
- [Features](#features)
- [Install & quick start](#quick-start)
- [Requirements](#requirements)
- [Deployment options](#deployment-options)
  - [1. Docker + host control agent](#option-1--docker--host-control-agent)
  - [2. Manual (bare-metal)](#option-2--manual-bare-metal)
- [Configuration](#configuration)
- [The `.env` in the UI](#the-env-in-the-ui)
- [Security notes](#security-notes)
- [API reference](#api-reference)
- [Development](#development)
- [Project structure](#project-structure)
- [How it works](#how-it-works)
- [License](#license)

---

## Why nginx-webui?

Managing nginx normally means SSH-ing in, hand-writing `server` blocks that are
easy to get wrong, running `nginx -t`, remembering `nginx -s reload`, and
tailing logs by hand. This project gives you a polished, single-page dashboard
for daily reverse-proxy chores — while **touching nothing** about how nginx
runs underneath.

It deals only with the same config files and the same commands real admins use
(`nginx -t`, `nginx -s reload`, `systemctl restart nginx`). Your nginx stays
yours; the UI just makes it pleasant.

---

## Features

| | |
|---|---|
| **Dashboard** | Live server state with stat boxes — version, running/master PID, workers, config file — and reload / check / restart buttons. The status card shows which transport answered (**host agent / http probe / local `/proc`**). |
| **Reverse-proxy wizard** | Create a site in one flat form: application name, domain (`server_name`), upstream (`proxy_pass`), HTTP **and** HTTPS ports, websocket upgrade, max upload size, upstream timeout. |
| **TLS made simple** | Enable TLS and the HTTPS port appears right in the TLS section; pick a saved certificate or type paths manually. Auto-defaults to 443, changeable. Optional `Redirect HTTP → HTTPS`. |
| **Saved SSL store** | Named certificate entries reused across sites — stored as **file paths** or as **pasted PEM content** (written where both host nginx and the container can read them), with CRUD in a dedicated card. |
| **Sites** | List, create, read, edit (form **or** raw config), delete, enable/disable (`sites-available` ↔ `sites-enabled`). |
| **Config editor** | Browse/edit any file under `/etc/nginx` (path-traversal safe). `nginx.conf` opens **read-only** until you click Edit; the nginx-webui `.env` is editable too. |
| **Validation & control** | `nginx -t`, reload (`nginx -s reload`), restart (`systemctl restart nginx`) — on the **host** nginx even when the UI runs in a container. |
| **Backup & restore** | One-click config tarball; restore validates with `nginx -t` and rolls back on failure. |
| **Log viewer** | Tail nginx error/access logs with line-count control and text filtering. |
| **Access protection** | Shared password (`WEBUI_PASSWORD`), 30-minute *remember me* session, log-out button, brute-force lockout (5 failures → 15 min block). |
| **Dark & light mode** | Theme toggle, persisted in the browser. |
| **Feather-light** | Flask + vanilla HTML/CSS/JS. No build step, no Node.js, no database — ~40 MB RAM. |

---

## Quick start

Install in one command — the installer **bootstraps itself** when streamed, cloning the repo before it runs:

```bash
curl -fsSL https://raw.githubusercontent.com/himalsimkhada/nginx-webui/main/install.sh | bash
```

Or clone and run directly:

```bash
git clone https://github.com/himalsimkhada/nginx-webui.git
cd nginx-webui
./install.sh
```

You'll be asked which deployment you want:

```
  1) Docker + host agent   - web UI container managing nginx on THIS machine (recommended)
  2) Manual                - nginx and the web UI both installed directly on THIS machine
```

> **Dry run first:** `./install.sh --check` reports what the installer detects
> on your machine (distro, nginx, host agent, Docker, python3) without changing
> a thing.

---

## Requirements

- Linux with nginx installed (`apt install nginx`), `nginx -t`-clean config
- Python 3.10+ (manual mode only)
- `sudo` access (for config files and `nginx` control)
- Docker is **optional** — needed only for the containerized deployment

Supported distros: Debian/Ubuntu (apt), RHEL/Fedora (dnf), Arch (pacman).

---

## Deployment options

| Option | What runs where | When to pick it |
|---|---|---|
| **1. Docker + host agent** | Web UI in a container, nginx on the host | You keep your existing nginx, UI stays containerized |
| **2. Manual** | Everything on this machine, systemd service | Minimal footprint, single server |

### Option 1 — Docker + host control agent

The web-UI container mounts `/etc/nginx` and `/var/log/nginx` from the host —
config editing, site management and `nginx -t` work straight from it. But a
container lives in another PID namespace and has no `systemctl`, so it **cannot
signal or restart the host nginx master**. For that, a tiny host-side
control agent (`host_agent.py`, standard library only) runs as a systemd
service on the host:

```bash
# ON THE NGINX HOST — one time
sudo cp host_agent.py /usr/local/bin/nginx-webui-host-agent.py
sudo chmod 755 /usr/local/bin/nginx-webui-host-agent.py
sudo cp nginx-webui-host-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nginx-webui-host-agent
```

It binds `0.0.0.0:9401` (so containers reach it via `host.docker.internal`);
restrict with a firewall if the host is on a LAN. API (JSON): `GET /status`,
`POST /check`, `POST /reload`, `POST /restart`. The installer (mode 1) does all
of this for you, writes `.env`, then:

```bash
cp .env.example .env
docker compose up -d --build
```

`.env` sets `NGINX_CTL_URL=http://host.docker.internal:9401`, so status, check,
reload and restart all act on the **host** nginx. The compose file adds
`extra_hosts: host.docker.internal → host-gateway`; if your Docker lacks
`host-gateway`, put the host's LAN IP in `NGINX_CTL_URL` instead.

### Option 2 — Manual (bare-metal)

Everything on this one machine, managed as a systemd service. Web UI at
`http://localhost:8400`.

```bash
./install.sh     # choose 2) Manual
```

By hand:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
WEBUI_PASSWORD='your-password' SECRET_KEY='a-long-random-string' ./venv/bin/python app.py
```

As a boot-starting service (running as root, no sudo rules needed):

```bash
sudo cp nginx-webui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nginx-webui
```

If you prefer to run the service as an unprivileged user, add passwordless rules:

```bash
echo 'himal ALL=(ALL) NOPASSWD: /usr/sbin/nginx, /usr/bin/systemctl restart nginx' > /etc/sudoers.d/nginx-webui
```

---

## Configuration

All container settings live in `.env` (see `.env.example`); bare-metal uses
environment variables or the systemd `EnvironmentFile`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `NGINX_CONF_DIR` | `/etc/nginx` | nginx config directory (files, sites, SSL store) |
| `NGINX_WEBUI_PORT` | `8400` | API listen port |
| `NGINX_STATUS_URL` | *(empty)* | HTTP probe of a reachable nginx `stub_status` for status-only checks |
| `NGINX_CTL_URL` | *(empty)* | Host control agent URL for status/check/reload/restart of the **host** nginx (containers) |
| `NGINX_WEBUI_ENV_FILE` | `/etc/nginx-webui.env` | Where the `.env` shown in the UI lives |
| `SSL_STORE_FILE` | `$NGINX_CONF_DIR/webui-ssl-store.json` | Saved certificate store |
| `SSL_CERTS_DIR` | `$NGINX_CONF_DIR/ssl` | Where pasted PEM content is written |
| `LOG_FILE` | *(auto)* | Log file to tail when journalctl isn't available (containers) |
| `NGINX_TIMEOUT` | `10` | Command timeout (s) for nginx operations |
| `WEBUI_PASSWORD` | *(empty = auth off)* | Single shared password required to use the UI |
| `SECRET_KEY` | *(dev default)* | Secret used to sign the session cookie; set a random value |
| `MAX_LOGIN_ATTEMPTS` / `LOCK_TIME` | `5` / `900` | Brute-force lockout config |

**Status source priority:** `NGINX_CTL_URL` (host agent) → `NGINX_STATUS_URL`
(HTTP probe) → local `/proc` + direct commands (bare-metal).

> **Access protection:** set `WEBUI_PASSWORD` to require a password at login.
> The *Remember me* checkbox persists the session for **30 minutes** then
> auto-logs-out (otherwise the session ends when the browser closes). A **Log
> out** button lives in the nav bar. If the variable is empty, authentication
> is disabled entirely.

---

## The `.env` in the UI

The file holding `NGINX_STATUS_URL` / `NGINX_CTL_URL` / etc. shows up as
**`.env`** in the dashboard's Config Files editor, so you can see and change it
without SSH. Bare metal: `/etc/nginx-webui.env` (the systemd `EnvironmentFile`).
Docker: `./.env` next to `docker-compose.yml` is mounted there. Edits apply
after restarting the service or `docker compose up -d`.

---

## Security notes

- Authentication is a single shared password compared against the configured `WEBUI_PASSWORD` — nothing stored on disk, no user database.
- Sessions use a signed cookie (set `SECRET_KEY`!), with **brute-force lockout** (5 failed logins → 15 min block) on the login form.
- Config file access is path-traversal safe and confined to `NGINX_CONF_DIR` (the `.env` being the single deliberate exception).
- The host control agent is fire-and-forget: bind `0.0.0.0` only for Docker reachability, restrict with a firewall, and it exposes an intentionally tiny API.
- `nginx.conf` opens read-only and requires an explicit Edit click to change.
- Restore writes go through the same validation gate nginx itself uses (`nginx -t`), with rollback on failure.
- No build step, no runtime downloads, no telemetry, no analytics.

---

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/login` | Login (shared password) |
| GET | `/api/session` | Auth state |
| GET | `/api/system` | Service identity |
| GET | `/api/status` | Version, pid, workers, config file |
| GET · POST | `/api/check` | `nginx -t` validation |
| GET · POST | `/api/control/check` | Alias of `/api/check` (same pattern as reload/restart) |
| POST | `/api/control/reload` | `nginx -s reload` (host agent or local) |
| POST | `/api/control/restart` | `systemctl restart nginx` (host agent or local) |
| GET | `/api/config/files` | Editable files |
| GET · PUT | `/api/config/file/<path>` | Read / write a config file |
| GET | `/api/sites` | Site list (enabled + available) |
| GET · PUT · DELETE | `/api/site/<name>` | Site CRUD |
| POST | `/api/site` | Create reverse-proxy site |
| POST | `/api/site/<name>/toggle` | Enable / disable site |
| GET | `/api/ssl` | List saved certificates |
| POST | `/api/ssl` | Add a certificate (paths or PEM) |
| PUT · DELETE | `/api/ssl/<name>` | Update / delete a certificate |
| GET | `/api/logs?lines=&query=` | Error/Access log tail |
| GET | `/api/backup` | Config tarball (base64) |
| POST | `/api/restore` | Apply + `nginx -t` + rollback |
| GET | `/healthz` `/readyz` | Probes (open) |
| GET | `/metrics` | Prometheus metrics (auth-gated) |
| GET | `/api/metrics/portal` | Gauges as JSON |

---

## Development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

pytest                      # full suite, no nginx required (binary faked)
```

The suite covers status + status-source priority, config editing (`nginx.conf`,
sites, `.env`), reverse-proxy site create/edit with separate HTTP/HTTPS ports,
SSL store with both file-path and pasted-PEM modes, backup/restore, the control
agent path, auth + lockout, and the API. CI runs on GitHub Actions for every
push and PR.

---

## Project structure

```
nginx-webui/
├── app.py                  # Flask backend — JSON API only
├── nginx_manager.py        # status, control, sites, SSL store, config, backup
├── host_agent.py           # host-side nginx control agent (stdlib only)
├── metrics.py              # /metrics, /healthz, /readyz (prometheus text)
├── templates/              # — (none: this service is API-only)
├── tests/                  # pytest suite
├── requirements.txt        # flask
├── Dockerfile              # Container image
├── docker-compose.yml      # Web UI container + host mounts + host.docker.internal
├── install.sh              # bootstrapping one-shot installer (--check safe)
├── nginx-webui.service     # Systemd unit (bare-metal)
├── nginx-webui-host-agent.service  # Systemd unit for the host control agent
└── .env.example            # Container configuration template
```

> The web-UI frontend lives in the companion repo
> [himalsimkhada/webui](https://github.com/himalsimkhada/webui) — the admin
> facade that talks to nginx-webui (and BIND) over HTTP.

---

## How it works

The backend drives nginx through the exact same tools an admin does:

- **Control** — `nginx -t`, `nginx -s reload`, `systemctl restart nginx`;
  delegated to the **host control agent** when running in a container
  (PID namespaces block direct signals).
- **Status** — host agent `/status`, an HTTP `stub_status` probe, or local
  `/proc` + `nginx -v` / `nginx -T` on bare metal; the dashboard shows which.
- **Config files** — reads/writes `nginx.conf`, `conf.d/`, `snippets/`,
  `sites-available/` under `/etc/nginx` (or your `NGINX_CONF_DIR`).
- **Sites** — generated server blocks include TLS (`server_name`, `listen 443 ssl`,
  cert/key), optional HTTP→HTTPS redirect, websocket `Upgrade` headers, and
  `client_max_body_size` / `proxy_read_timeout`.
- **Validation** — every saved server block passes `nginx -t` before the
  reload is offered.
- **Logs** — mounted log files (containers) or journalctl (bare metal).

Transport is chosen from the environment: as a systemd service it acts directly
on the host nginx; inside a container it talks to the host through
`NGINX_CTL_URL`. No database. No magic.

---

<div align="center">

**Poke around, file an issue, open a PR — feedback welcome.**

</div>

## License

MIT