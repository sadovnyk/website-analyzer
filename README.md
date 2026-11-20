# Website Analyzer

A web application that monitors websites and keeps a history of their health: response time, SSL certificate expiry, server geolocation and broken links. Sites are scanned on demand when added and periodically by a scheduler.

## Features

- **Dashboard** — list of tracked sites with the latest HTTP status and page title; sites can be pinned, paused/resumed or deleted.
- **Site details** — full scan history with a chart of response times.
- **Website info** — page title, meta description and response time (ping).
- **IP & geolocation** — resolves the domain to an IP and looks up country, city and hosting organization.
- **SSL certificate check** — validity and number of days until expiry.
- **Link analysis** — extracts all unique links from the page and checks them concurrently (`asyncio` + `aiohttp`) to find broken ones.
- **Safety checks** — URLs are normalized and validated; private/internal hosts are blocked (SSRF protection); cross-site POST requests are rejected.
- **Limits** — up to 50 tracked sites, up to 5 active at the same time.
- **Health endpoint** — `GET /healthz` reports MySQL and MongoDB availability.

Note: some websites (e.g., Wikipedia) block automated requests and return `403`; the tool detects and reports this.

## Technologies

- Python 3, `asyncio`, `aiohttp`, `BeautifulSoup4`
- FastAPI, Uvicorn, Jinja2 (web UI)
- MySQL (`PyMySQL`) — sites and scan results
- MongoDB (`pymongo`) — broken links per scan
- `tenacity` — retries for DB connections
- Docker Compose — databases
- `pytest`, `pytest-asyncio` — tests

## Project structure

```
core/          scanning logic, database access, security, logging
web/           FastAPI app (main.py), templates, static files
db/            schema.sql (MySQL), mongo-init.js (MongoDB)
main.py        analyze(url) — runs all checks for one URL
scheduler.py   scans all active sites once (run it from cron)
tests/         test suite
```

## Installation

```bash
git clone https://github.com/sadovnyk/website-analyzer-cli.git
cd website-analyzer-cli
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

| Variable                            | Description                                                                             |
|-------------------------------------|-----------------------------------------------------------------------------------------|
| `DB_HOST`, `DB_PORT`                | MySQL host and port (`127.0.0.1:3307` with the provided compose file)                   |
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | MySQL credentials and database name                                                     |
| `MONGO_USER`, `MONGO_PASSWORD`      | MongoDB root credentials used by the container                                          |
| `MONGO_URI`                         | MongoDB connection string, e.g. `mongodb://user:pass@127.0.0.1:27018/?authSource=admin` |
| `MONGO_DB`, `MONGO_CL`              | MongoDB database and collection for broken links                                        |

## Databases

Start both databases:

```bash
docker compose up -d
```

On the **first start** (empty `mysql_data` / `mongodb_data`) the containers initialize themselves:

- `db/schema.sql` creates the MySQL tables;
- `db/mongo-init.js` creates the MongoDB collection and index.

If the data directories already exist, init scripts are skipped. Apply them manually:

```bash
docker exec -i analyzer-mysql mysql -u<DB_USER> -p<DB_PASSWORD> <DB_NAME> < db/schema.sql
```

### MySQL schema

**`sites`** — tracked websites

| Column      | Type                    | Notes                        |
|-------------|-------------------------|------------------------------|
| `id`        | INT, PK, auto-increment |                              |
| `url`       | VARCHAR(255), unique    | normalized URL               |
| `is_active` | TINYINT(1), default 1   | included in scheduled scans  |
| `is_pinned` | TINYINT(1), default 0   | shown first on the dashboard |
| `added_at`  | DATETIME                |                              |

**`scans`** — one row per scan

| Column                                                               | Type                    | Notes               |
|----------------------------------------------------------------------|-------------------------|---------------------|
| `id`                                                                 | INT, PK, auto-increment |                     |
| `site_id`                                                            | INT, FK → `sites.id`    | `ON DELETE CASCADE` |
| `url`                                                                | VARCHAR(255)            |                     |
| `scanned_at`                                                         | DATETIME                |                     |
| `status_code`, `title`, `description`, `duration_ms`, `status_error` |                         | HTTP check          |
| `ip`, `country`, `city`, `org`, `geo_error`                          |                         | geolocation         |
| `ssl_days_left`, `valid`, `cert_error`                               |                         | SSL certificate     |
| `total_links`, `links_error`                                         |                         | link check summary  |

### MongoDB collection

Collection `MONGO_CL` (default `scan_links`) holds one document per scan:

```json
{
  "_id": "ObjectId(...)",
  "my_sql_scan_id": 2,
  "broken_links": []
}
```

`my_sql_scan_id` references `scans.id` in MySQL and is indexed.

## Usage

Start the web interface:

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

`scheduler.py` scans every active site once and exits. Run it periodically, for example with cron (every hour):

```cron
0 * * * * cd /home/to/website-analyzer-cli && .venv/bin/python scheduler.py
```
Replace `/path/to/website-analyzer-cli` with the absolute path to your cloned repository
(you can get it by running `pwd` inside the project folder).

Logs are written to `logs/cron.log`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Example Output

## Soon