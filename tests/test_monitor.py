import io
import sqlite3

import pandas as pd

from monitor import calculate_metrics, detect_anomalies, ensure_sqlite_schema, load_data, load_sqlite_data


def test_load_data_derives_rates_and_sorts_dates():
    data = load_data(io.BytesIO(b"date,total_payments,successful_payments\n2026-01-02,100,90\n2026-01-01,100,95\n"))
    assert list(data["date"].dt.day) == [1, 2]
    assert data["payment_success_rate"].tolist() == [95.0, 90.0]


def test_detect_anomalies_flags_latest_outlier():
    data = calculate_metrics(pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=8),
        "payment_success_rate": [99, 99, 99, 99, 99, 99, 99, 80],
    }))
    anomalies = detect_anomalies(data, window=7, threshold=2)
    assert anomalies[0].metric == "payment_success_rate"
    assert anomalies[0].direction == "down"
    assert anomalies[0].severity == "critical"


def test_short_series_does_not_create_false_alerts():
    data = calculate_metrics(pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=2),
        "transaction_volume": [100, 105],
    }))
    assert detect_anomalies(data) == []


def test_load_sqlite_data_reads_default_metrics_table(tmp_path):
    database = tmp_path / "monitor.db"
    ensure_sqlite_schema(str(database))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO daily_metrics (date, total_payments, successful_payments) VALUES (?, ?, ?)",
            ("2026-01-01", 100, 95),
        )
        connection.commit()
    data = load_sqlite_data(str(database))
    assert data.loc[0, "payment_success_rate"] == 95