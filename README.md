<h1 align="center">Website Analyzer (SitePulse)</h1>

A web application that monitors websites and keeps a history of their health: response time, SSL certificate expiry,
server geolocation and broken links. Sites are scanned on demand when added and periodically by a scheduler that runs in
Docker.

## Features

- **Dashboard** — list of tracked sites with the latest HTTP status and page title; search by URL, filter by
  Active/Paused; sites can be pinned, paused/resumed or deleted.
- **Site details** — full scan history with charts of uptime and response time (expandable), plus the list of broken
  links per scan.
- **Website info** — page title, meta description and response time (ping).
- **IP & geolocation** — resolves the domain to an IP and looks up country, city and hosting organization.
- **SSL certificate check** — validity and number of days until expiry.
- **Link analysis** — extracts unique links from the page (up to 200) and checks them concurrently (`asyncio` +
  `aiohttp`) to find broken ones.
- **Safety checks** — URLs are normalized and validated; private/internal hosts are blocked on every redirect hop (SSRF
  protection); response bodies are size-limited; cross-site POST requests are rejected.
- **Limits** — up to 50 tracked sites, up to 5 active at the same time.
- **Health endpoint** — `GET /healthz` reports MySQL and MongoDB availability.

Note: some websites (e.g., Wikipedia) block automated requests and return `403`; the tool detects and reports this.

## Technologies

- Python 3.12, `asyncio`, `aiohttp`, `BeautifulSoup4`
- FastAPI, Uvicorn, Jinja2 (web UI), Chart.js and Turbo (frontend)
- MySQL (`PyMySQL`) — sites and scan results
- MongoDB (`pymongo`) — broken links per scan
- `tenacity` — retries for DB connections
- Docker Compose — MySQL, MongoDB and the scheduler
- `pytest`, `pytest-asyncio` — tests

## Project structure

```
core/                scanning logic, database access, security, logging
web/                 FastAPI app (main.py), templates, static files
db/                  schema.sql (MySQL), mongo.js (MongoDB init script)
scripts/             run_scheduler.sh — loop that runs the scheduler inside Docker
main.py              analyze(url) — runs all checks for one URL
scheduler.py         scans all active sites once
tests/               test suite
Dockerfile           image for the scheduler container
docker-compose.yaml  mysql, mongodb and scheduler services
```

## Installation

```bash
git clone https://github.com/sadovnyk/website-analyzer.git
cd website-analyzer
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Copy the example file and fill in your values:

```bash
cp .env.example .env             # Windows (cmd): copy .env.example .env
```

| Variable                            | Description                                                                                      |
|-------------------------------------|--------------------------------------------------------------------------------------------------|
| `DB_HOST`, `DB_PORT`                | MySQL host and port for running the web app on the host (`127.0.0.1:3307` with the compose file) |
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | MySQL credentials and database name (`DB_USER` must not be `root`)                               |
| `MONGO_USER`, `MONGO_PASSWORD`      | MongoDB root credentials used by the container                                                   |
| `MONGO_URI`                         | MongoDB connection string, e.g. `mongodb://user:pass@127.0.0.1:27018/?authSource=admin`          |
| `MONGO_DB`, `MONGO_CL`              | MongoDB database and collection for broken links                                                 |
| `SCHEDULER_INTERVAL_SECONDS`        | Pause between scheduled scans (default `900`)                                                    |
| `APP_UID`, `APP_GID`                | User and group id the scheduler container runs as (default `1000`)                               |

**Linux/macOS only.** Set your own ids so files created by the container (e.g. `logs/cron.log`) belong to you, not to
root:

```bash
echo "APP_UID=$(id -u)" >> .env
echo "APP_GID=$(id -g)" >> .env
```

On Windows (Docker Desktop) skip this step: files in mounted folders are not created as root, and the default value
`1000` is harmless.

Inside the scheduler container, `DB_HOST`, `DB_PORT` and `MONGO_URI` are overridden by `docker-compose.yaml` to point to
the `mysql` and `mongodb` services, so the values in `.env` only matter for the web app started on the host.

## Running

Start the databases and the scheduler:

```bash
docker compose up -d
```

On the **first start** (empty `mysql_data` / `mongodb_data`) the containers initialize themselves:

- `db/schema.sql` creates the MySQL tables;
- `db/mongo.js` creates the MongoDB collection and index.

MySQL has a healthcheck, and the scheduler starts only after MySQL is ready, so the first scan does not fail while the
database is still initializing. Check with `docker compose ps` (`analyzer-mysql` should become `healthy`).

If the data directories already exist, init scripts are skipped. Apply the schema manually:

```bash
docker exec -i analyzer-mysql mysql -u<DB_USER> -p<DB_PASSWORD> <DB_NAME> < db/schema.sql
```

> Note: `MYSQL_USER` / `MYSQL_PASSWORD` are applied only when `mysql_data/` is empty. If you change them later, either
> change the credentials inside MySQL or remove `mysql_data/` (this deletes all data).

### Web interface

Start the web app on the host:

```bash
uvicorn web.main:app --reload
```

Open http://127.0.0.1:8000, add a website — it is scanned immediately in the background.

| Route                    | Description                    |
|--------------------------|--------------------------------|
| `GET /`                  | dashboard                      |
| `GET/POST /add-site`     | add a site                     |
| `GET /site/{id}`         | scan history and details       |
| `POST /site/{id}/toggle` | pause / resume scheduled scans |
| `POST /site/{id}/pin`    | pin / unpin                    |
| `POST /site/{id}/delete` | delete a site and its history  |
| `GET /healthz`           | database health check          |

### Scheduled scans

The `scheduler` container runs `scripts/run_scheduler.sh`: it scans every active site, sleeps for
`SCHEDULER_INTERVAL_SECONDS`, and repeats. Sites are scanned one after another; a failure on one site does not stop the
others.

To change the interval, edit `SCHEDULER_INTERVAL_SECONDS` in `.env` and recreate only the scheduler container (a plain
`docker compose restart` does **not** reload `.env`):

```bash
docker compose up -d scheduler
```

A single scan of all active sites can also be run manually on the host:

```bash
python scheduler.py
```

Logs are written to `logs/cron.log`:

```bash
tail -f logs/cron.log            # Windows (PowerShell): Get-Content logs/cron.log -Wait
```

## Troubleshooting

| Problem                                                    | Fix                                                                                                                                                                                            |
|------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `Permission denied: logs/cron.log` when starting `uvicorn` | (Linux/macOS) The file was created by root. Set `APP_UID` / `APP_GID` in `.env`, then run `sudo chown -R $USER:$USER logs/` and `docker compose up -d --build`.                                |
| `Database error: db_select_failed` in the log              | Check the exact reason in the line `get_sites failed: ...` right above it in `logs/cron.log`. Typical causes: MySQL not ready yet, wrong credentials (see the note about `mysql_data/` above). |
| Changed `.env` but the scheduler behaves the same          | Use `docker compose up -d scheduler`, not `restart`.                                                                                                                                           |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests mock the network and the databases, so no running containers are needed.

<h2 align="center">Screenshots</h2>

<p align="center">
  <kbd><img src="screenshots/main_window.png" width="800" alt="Dashboard"></kbd>
  <br>
  <sub><b>Dashboard</b> — tracked sites, search, filter, overview and recent activity</sub>
</p>

<p align="center">
  <kbd><img src="screenshots/new_site.png" width="800" alt="Add a site"></kbd>
  <br>
  <sub><b>Add a site</b> — the address is validated and scanned right away</sub>
</p>

<p align="center">
  <kbd><img src="screenshots/trends.png" width="800" alt="Trends"></kbd>
  <br>
  <sub><b>Trends</b> — uptime and response time across scans</sub>
</p>

<p align="center">
  <kbd><img src="screenshots/broken_links.png" width="800" alt="Broken links"></kbd>
  <br>
  <sub><b>Broken links</b> — problem links found during a scan</sub>
</p>
