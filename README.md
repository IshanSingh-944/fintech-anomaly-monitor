# SignalForge Monitor

An AI-assisted operations monitor for fintech teams. SignalForge turns CSV or Excel exports into four signals: payment success rate, transaction volume, refund rate, and user churn.

## What it does

- Loads CSV and Excel data and derives rates from raw counts.
- Compares the latest value with a configurable trailing baseline using a z-score.
- Labels large movements as warning or critical anomalies.
- Uses a local Ollama model for business-friendly explanations, with an offline fallback.
- Sends a single plain-text SMTP alert for all detected anomalies.
- Includes a Streamlit dashboard for uploads, trend context, email setup, test delivery, and notification.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The dashboard opens with `sample_data.csv`, which contains an intentional refund and churn spike on the final day. Upload a file with a `date` column and any of these raw columns:

`total_payments`, `successful_payments`, `refunds`, `transaction_volume`, `active_users`, `churned_users`

You can also provide pre-calculated columns named `payment_success_rate`, `transaction_volume`, `refund_rate`, or `user_churn`.

## Ollama summaries

Install Ollama, then pull a model before opening the dashboard:

```bash
ollama pull llama3.2
```

If Ollama is not running, the monitor stays usable and writes a deterministic business summary instead.

## Email setup

Open `Email delivery setup` in the sidebar. Enter the SMTP host, port, security mode, account, password or app password, and recipient. Click `Send test email` before sending a real report. These values live only in the current browser session; users can independently upload data and configure their own delivery destination.

For a shared deployment, do not put customer SMTP passwords in Streamlit secrets. Let each signed-in user enter their own session settings, or put only an organization-owned sender in server environment variables. Copy `.env.example` for the latter:

```bash
export SMTP_HOST=smtp.gmail.com SMTP_PORT=587
export SMTP_USER=alerts@example.com SMTP_PASSWORD=your-app-password RECIPIENT=ops@example.com
export SMTP_SECURITY=STARTTLS
```

## Deploy

The simplest hosted option is Streamlit Community Cloud: deploy this repository, set `app.py` as the entry point, and users open the URL in their browser. Each session has isolated uploaded data and email form values. For a private deployment, build the included container:

```bash
docker build -t signalforge-monitor .
docker run --rm -p 8501:8501 signalforge-monitor
```

The CLI runner is also suitable for a server cron job using an organization-owned mailbox:

```bash
python run_monitor.py sample_data.csv
```

Use `--no-email` to check a file without sending. Never commit SMTP credentials.

## Development

```bash
python -m pytest -q
python -m py_compile monitor.py app.py run_monitor.py
```

The design keeps detection independent from Streamlit and external services, so the statistical core can be tested or scheduled on its own.

## Live SQLite monitoring

The dashboard can poll a SQLite database every 30 seconds. Select `Live SQLite database` in the sidebar and use the database path configured by `SQLITE_DB_PATH` or the default `signalforge.db`. The app creates this table automatically:

```sql
CREATE TABLE daily_metrics (
	date TEXT PRIMARY KEY,
	total_payments REAL,
	successful_payments REAL,
	refunds REAL,
	transaction_volume REAL,
	active_users REAL,
	churned_users REAL
);
```

For email monitoring that continues after everyone logs out, run the independent worker alongside the dashboard:

```bash
source .venv/bin/activate
python sqlite_monitor.py --database signalforge.db --interval 30
```

It stores the last emailed anomaly set in `monitor_state`, so the same reading is not emailed repeatedly. Keep the worker running with a process manager such as systemd or Docker. Configure the `SMTP_*` environment variables before starting it.
