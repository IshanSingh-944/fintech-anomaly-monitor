"""Core data, anomaly, summary, and email helpers for SignalForge."""

from __future__ import annotations

import io
import os
import sqlite3
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import BinaryIO

import numpy as np
import pandas as pd
import requests

METRICS = {
    "payment_success_rate": "Payment success rate",
    "transaction_volume": "Transaction volume",
    "refund_rate": "Refund rate",
    "user_churn": "User churn",
}


@dataclass(frozen=True)
class Anomaly:
    metric: str
    label: str
    date: str
    value: float
    baseline: float
    change_pct: float
    z_score: float
    severity: str
    direction: str


def load_data(source: str | bytes | BinaryIO) -> pd.DataFrame:
    """Load CSV or Excel data and normalize the date column."""
    if hasattr(source, "read"):
        raw = source.read()
        name = getattr(source, "name", "upload.csv")
    else:
        raw = source
        name = str(source)
    if str(name).lower().endswith((".xlsx", ".xls")):
        frame = pd.read_excel(io.BytesIO(raw) if isinstance(raw, bytes) else raw)
    else:
        frame = pd.read_csv(io.BytesIO(raw) if isinstance(raw, bytes) else raw)
    frame.columns = [str(column).strip().lower().replace(" ", "_") for column in frame.columns]
    if "date" not in frame.columns:
        raise ValueError("Data must include a 'date' column.")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return calculate_metrics(frame)


def load_sqlite_data(database: str = "signalforge.db") -> pd.DataFrame:
    """Load the daily_metrics table from a SQLite database."""
    with sqlite3.connect(database) as connection:
        frame = pd.read_sql_query(
            "SELECT * FROM daily_metrics ORDER BY date",
            connection,
        )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return calculate_metrics(frame)


def ensure_sqlite_schema(database: str = "signalforge.db") -> None:
    """Create the default metrics and alert-state tables when absent."""
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_metrics (
                date TEXT PRIMARY KEY,
                total_payments REAL,
                successful_payments REAL,
                refunds REAL,
                transaction_volume REAL,
                active_users REAL,
                churned_users REAL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_state (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.commit()


def calculate_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Add rate metrics when their component columns are available."""
    frame = frame.copy()
    calculations = {
        "payment_success_rate": ("successful_payments", "total_payments"),
        "refund_rate": ("refunds", "total_payments"),
        "user_churn": ("churned_users", "active_users"),
    }
    for metric, (numerator, denominator) in calculations.items():
        if metric not in frame and numerator in frame and denominator in frame:
            frame[metric] = np.where(frame[denominator] > 0, frame[numerator] / frame[denominator] * 100, 0)
    return frame


def detect_anomalies(frame: pd.DataFrame, window: int = 7, threshold: float = 2.0) -> list[Anomaly]:
    """Compare the latest observation with a trailing baseline for each metric."""
    anomalies: list[Anomaly] = []
    if len(frame) < 2:
        return anomalies
    for metric, label in METRICS.items():
        if metric not in frame:
            continue
        values = pd.to_numeric(frame[metric], errors="coerce").dropna()
        if len(values) < 2:
            continue
        latest = float(values.iloc[-1])
        history = values.iloc[max(0, len(values) - window - 1):-1]
        if len(history) < 2:
            history = values.iloc[:-1]
        if len(history) < 2:
            continue
        baseline = float(history.mean())
        deviation = float(history.std(ddof=0))
        z_score = 0.0 if latest == baseline else (float("inf") if deviation == 0 else (latest - baseline) / deviation)
        if abs(z_score) < threshold:
            continue
        change_pct = ((latest - baseline) / abs(baseline) * 100) if baseline else 0.0
        direction = "up" if latest > baseline else "down"
        severity = "critical" if abs(z_score) >= 3 else "warning"
        anomalies.append(Anomaly(metric, label, frame["date"].iloc[-1].strftime("%d %b %Y"), latest, baseline, change_pct, z_score, severity, direction))
    return sorted(anomalies, key=lambda item: abs(item.z_score), reverse=True)


def _format_value(anomaly: Anomaly) -> str:
    if "rate" in anomaly.metric or anomaly.metric == "user_churn":
        return f"{anomaly.value:.1f}%"
    return f"{anomaly.value:,.0f}"


def fallback_summary(anomaly: Anomaly) -> str:
    """Business-language summary used when Ollama is unavailable."""
    direction = "dropped" if anomaly.direction == "down" else "increased"
    causes = {
        "payment_success_rate": "gateway issues, bank downtime, or a fraud spike",
        "transaction_volume": "a campaign ending, an acquisition issue, or an outage",
        "refund_rate": "product issues, settlement errors, or customer dissatisfaction",
        "user_churn": "service friction, pricing changes, or a retention issue",
    }
    return f"{anomaly.label} {direction} {abs(anomaly.change_pct):.1f}% to {_format_value(anomaly)}.\nLikely causes: {causes[anomaly.metric]}.\nReview the latest operational events and compare against the {anomaly.baseline:.1f} baseline."


def generate_summary(anomaly: Anomaly, model: str = "llama3.2", host: str = "http://localhost:11434") -> str:
    """Ask a local Ollama model for a concise summary, falling back offline."""
    prompt = ("You are a fintech operations analyst. In three short lines, explain this anomaly to a business manager. "
              f"Metric: {anomaly.label}; current: {_format_value(anomaly)}; baseline: {anomaly.baseline:.1f}; "
              f"change: {anomaly.change_pct:.1f}%; direction: {anomaly.direction}. Include likely causes and one action.")
    try:
        response = requests.post(f"{host.rstrip('/')}/api/generate", json={"model": model, "prompt": prompt, "stream": False}, timeout=12)
        response.raise_for_status()
        text = response.json().get("response", "").strip()
        return text or fallback_summary(anomaly)
    except (requests.RequestException, ValueError, KeyError):
        return fallback_summary(anomaly)


def send_email_alert(anomalies: list[Anomaly], summaries: dict[str, str], settings: dict[str, str]) -> None:
    """Send one plain-text alert through an SMTP server."""
    required = ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "recipient"]
    missing = [key for key in required if not settings.get(key)]
    if missing:
        raise ValueError(f"Missing email settings: {', '.join(missing)}")
    message = EmailMessage()
    message["Subject"] = f"SignalForge Monitor: {len(anomalies)} anomaly alert(s)"
    message["From"] = settings["smtp_user"]
    message["To"] = settings["recipient"]
    sections = [f"{item.label}: {item.direction} {abs(item.change_pct):.1f}%\n{summaries[item.metric]}" for item in anomalies]
    message.set_content("SignalForge anomaly monitor alert\n\n" + "\n\n".join(sections))
    security = settings.get("smtp_security", "STARTTLS").upper()
    smtp_class = smtplib.SMTP_SSL if security == "SSL" else smtplib.SMTP
    with smtp_class(settings["smtp_host"], int(settings["smtp_port"])) as server:
        if security == "STARTTLS":
            server.starttls()
        server.login(settings["smtp_user"], settings["smtp_password"])
        server.send_message(message)


def send_test_email(settings: dict[str, str]) -> None:
    """Send a short configuration test to the configured recipient."""
    test = EmailMessage()
    test["Subject"] = "SignalForge Monitor email test"
    test["From"] = settings.get("smtp_user", "")
    test["To"] = settings.get("recipient", "")
    test.set_content("Your SignalForge Monitor email settings are working.")
    required = ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "recipient"]
    missing = [key for key in required if not settings.get(key)]
    if missing:
        raise ValueError(f"Missing email settings: {', '.join(missing)}")
    security = settings.get("smtp_security", "STARTTLS").upper()
    smtp_class = smtplib.SMTP_SSL if security == "SSL" else smtplib.SMTP
    with smtp_class(settings["smtp_host"], int(settings["smtp_port"])) as server:
        if security == "STARTTLS":
            server.starttls()
        server.login(settings["smtp_user"], settings["smtp_password"])
        server.send_message(test)


def email_settings_from_env() -> dict[str, str]:
    settings = {key: os.getenv(key.upper(), "") for key in ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "recipient")}
    settings["smtp_security"] = os.getenv("SMTP_SECURITY", "STARTTLS")
    return settings