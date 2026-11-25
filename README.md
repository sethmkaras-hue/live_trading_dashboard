# Live Trading Dashboard

Private, single-user dashboard for monitoring SPY and SPYU on Alpaca IEX and keeping a journal of actual Webull fills. All trading and stop orders are handled manually in Webull.

## Ubuntu deployment

Install Docker Engine with Compose and enroll the server and iPhone in the same Tailscale network. Clone this repository on the server. Copy `.env.example` to `.env`, restrict permissions with `chmod 600 .env`, and set a unique `DASHBOARD_PASSWORD` of at least 12 characters.

1. Create an Alpaca account with Basic market data. Generate an API key and secret in your Alpaca account and put the values in `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. Paper-account keys can be used for market data. The app accesses Alpaca's market-data endpoints only. Optional extra symbols go in `EXTRA_SYMBOLS` as a comma-separated list.
2. Start the application with `docker compose up -d --build`. Check `docker compose ps` and `docker compose logs dashboard`. The app binds the host's loopback interface and stores data in the `dashboard-data` volume.
3. On the Ubuntu server, run `sudo tailscale serve --bg http://127.0.0.1:8080`. Enable tailnet HTTPS if Tailscale prompts you. Use the HTTPS URL shown by `tailscale serve status` from Safari on your iPhone. Use the configured dashboard username and password. Keep access restricted to the intended tailnet users. For a custom `DASHBOARD_PORT`, use the matching address in the Serve command.

Run one server process and one replica. Alpaca Basic limits simultaneous market-data connections. Use `docker compose up -d --build` after a source update. The named volume keeps your data across replacement and restarts.

## Workflow

- **Live Trading** opens with synchronized SPY and SPYU charts. Switch candle intervals and overlays without leaving the page. Check feed freshness, candle gaps, risk warnings, and the rule evidence before acting.
- **Record Buy** and **Record Sell** capture actual Webull fills, including partial executions and fees. Enter your execution time in the device's local timezone. Displayed market and journal times use Eastern Time.
- **Trading Journal** supports filtering, correcting fills, reviewing recorded chart and signal context, and CSV or Excel exports.
- **Performance** summarizes the recorded account, cash flows, trading results, and learning-stage progress.
- **Trading Rules** applies validated configurations, with YAML import/export and rollback as a new version.
- **Settings** shows diagnostics, backups, adjustable risk limits, and whole-share sizing with a slippage allowance.

## Development and verification

Requires Python 3.12 and Node.js 20.19 or newer. From the project root:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci --prefix frontend
npm run build --prefix frontend
.venv/bin/python -m pytest -q
DASHBOARD_DEV=1 .venv/bin/python -m uvicorn app.main:app
```

Development mode bypasses authentication. Use it only on loopback with test data. The missing-credential state lets you use the journal without producing simulated market prices. The CI suite uses isolated SQLite databases and synthetic fixtures.

For a build environment with a trusted inspection CA, the Dockerfile accepts a BuildKit secret named `build_ca` containing its CA bundle. The secret is available to the dependency installers during the build.

## Backup and restore

Download a consistent SQLite backup in Settings. It includes executions, cash flows, rule YAML, recorded market data, and trade context. Keep backups private. Save `.env` separately through your usual secret-management process.

Verify the backup is from this dashboard. Stop the application, mount the backup read-only, run the integrity-checked restore, and restart:

```sh
docker compose stop dashboard
docker compose run --rm --no-deps -v "$PWD/trading-backup.sqlite3:/restore.sqlite3:ro" dashboard python -m app.restore /restore.sqlite3
docker compose up -d dashboard
```

Restore retains the prior database as `dashboard.before-restore.sqlite3` in the volume. Archive that recovery copy before attempting another restore. Ensure the backup file is readable by the container's UID 10001.

## Documentation

- [Implementation plan](docs/PLAN.md)
- [Architecture and data model](docs/ARCHITECTURE.md)
- [Trading rule definitions](docs/RULES.md)
- [Example configuration](rules.example.yaml)
- [IEX validation protocol and known limitations](docs/VALIDATION.md)
