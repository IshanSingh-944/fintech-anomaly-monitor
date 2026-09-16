"""Run the monitor from cron: python run_monitor.py daily_metrics.csv"""

from __future__ import annotations

import argparse

from monitor import detect_anomalies, email_settings_from_env, generate_summary, load_data, send_email_alert


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect SignalForge fintech metric anomalies and email the team.")
    parser.add_argument("data", help="Path to a CSV or Excel metrics file")
    parser.add_argument("--threshold", type=float, default=2.0, help="Z-score threshold (default: 2.0)")
    parser.add_argument("--window", type=int, default=7, help="Trailing baseline days (default: 7)")
    parser.add_argument("--no-email", action="store_true", help="Print anomalies without sending email")
    args = parser.parse_args()

    data = load_data(args.data)
    anomalies = detect_anomalies(data, window=args.window, threshold=args.threshold)
    if not anomalies:
        print("No anomalies detected.")
        return 0
    summaries = {}
    for anomaly in anomalies:
        summaries[anomaly.metric] = generate_summary(anomaly)
        print(f"[{anomaly.severity.upper()}] {anomaly.label}: {anomaly.direction} {abs(anomaly.change_pct):.1f}%")
        print(summaries[anomaly.metric])
    if not args.no_email:
        send_email_alert(anomalies, summaries, email_settings_from_env())
        print("Email alert sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())