"""Continuously monitor a SQLite database and email new anomaly sets."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time

from monitor import (
    detect_anomalies,
    email_settings_from_env,
    ensure_sqlite_schema,
    generate_summary,
    load_sqlite_data,
    send_email_alert,
)


def alert_signature(anomalies) -> str:
    values = [(item.metric, item.date, round(item.value, 6)) for item in anomalies]
    return json.dumps(values, sort_keys=True)


def read_state(database: str) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT value FROM monitor_state WHERE name = 'alert_signature'"
        ).fetchone()
    return row[0] if row else ""


def write_state(database: str, value: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO monitor_state (name, value) VALUES ('alert_signature', ?)
            ON CONFLICT(name) DO UPDATE SET value = excluded.value
            """,
            (value,),
        )
        connection.commit()


def check_once(database: str, window: int, threshold: float) -> bool:
    data = load_sqlite_data(database)
    if data.empty:
        return False
    anomalies = detect_anomalies(data, window=window, threshold=threshold)
    if not anomalies:
        write_state(database, "")
        print("No anomalies detected.")
        return False
    signature = alert_signature(anomalies)
    if signature == read_state(database):
        print("No new anomaly set.")
        return False
    summaries = {item.metric: generate_summary(item) for item in anomalies}
    send_email_alert(anomalies, summaries, email_settings_from_env())
    write_state(database, signature)
    print(f"Email alert sent for {len(anomalies)} anomaly(s).")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuously monitor SQLite metrics and email alerts.")
    parser.add_argument("--database", default="signalforge.db")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between checks (default: 30)")
    parser.add_argument("--threshold", type=float, default=2.0)
    parser.add_argument("--window", type=int, default=7)
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    args = parser.parse_args()
    ensure_sqlite_schema(args.database)
    while True:
        try:
            check_once(args.database, args.window, args.threshold)
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"Monitor check failed: {error}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())