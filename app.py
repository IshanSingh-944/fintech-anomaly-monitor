from __future__ import annotations

import html
import os

import pandas as pd
import streamlit as st

from streamlit_autorefresh import st_autorefresh

from monitor import METRICS, detect_anomalies, email_settings_from_env, ensure_sqlite_schema, generate_summary, load_data, load_sqlite_data, send_email_alert, send_test_email


st.set_page_config(page_title="SignalForge Monitor", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap');
:root { --ink:#edf5f1; --muted:#91a59e; --paper:#0d1516; --panel:#14201f; --line:#29413c; --green:#62d6a1; --green-soft:#16392e; --red:#ff9178; --red-soft:#3b211d; }
html, body, [class*="css"] { font-family:'Space Grotesk', sans-serif; color:var(--ink); }
.stApp { background:radial-gradient(circle at 100% 0%, #19352d 0, transparent 34%), #0d1516; }
.block-container { max-width:1380px; padding:2.25rem 3rem 4rem; }
.topbar { display:flex; justify-content:space-between; align-items:flex-end; border-bottom:1px solid var(--line); padding-bottom:1.35rem; margin-bottom:1.8rem; }
.kicker { color:var(--green); font:500 .7rem 'DM Mono', monospace; letter-spacing:.12em; text-transform:uppercase; }
.title { font-size:3rem; font-weight:600; letter-spacing:-.055em; line-height:1; margin-top:.4rem; }
.description { color:var(--muted); font-size:.95rem; margin-top:.65rem; max-width:580px; }
.date-label { color:var(--muted); font:500 .72rem 'DM Mono', monospace; text-align:right; text-transform:uppercase; letter-spacing:.08em; }
.date-value { font-size:1.05rem; font-weight:600; margin-top:.35rem; text-align:right; }
.metric-card { background:rgba(20,32,31,.92); border:1px solid var(--line); border-radius:10px; padding:1.15rem 1.2rem; min-height:130px; box-shadow:0 5px 18px rgba(0,0,0,.2); }
.metric-label { color:var(--muted); font-size:.82rem; }
.metric-value { font:500 1.95rem 'DM Mono', monospace; letter-spacing:-.04em; margin:.7rem 0 .55rem; }
.metric-status { color:var(--green); font-size:.76rem; }
.metric-status.alert { color:var(--red); }
.section-title { font-size:1.1rem; font-weight:600; margin:2rem 0 .75rem; }
.signal { background:var(--panel); border:1px solid var(--line); border-left:4px solid var(--red); border-radius:8px; padding:1rem 1.15rem; margin:.7rem 0; box-shadow:0 5px 18px rgba(0,0,0,.2); }
.signal-title { font-size:1rem; font-weight:600; }
.signal-copy { color:#c2d0cb; font-size:.88rem; line-height:1.5; margin-top:.45rem; }
.signal-meta { color:var(--muted); font:500 .7rem 'DM Mono', monospace; margin-top:.7rem; text-transform:uppercase; }
.healthy { background:var(--green-soft); border:1px solid #28634e; border-radius:8px; color:var(--green); padding:1rem 1.1rem; }
section[data-testid="stSidebar"] { background:#101c1b; border-right:1px solid var(--line); }
div[data-testid="stFileUploader"] { border:1px dashed #4d7868; border-radius:8px; padding:.35rem; background:#14201f; }
div[data-baseweb="input"], div[data-baseweb="select"] { background:#182725; }
input, textarea { color:var(--ink) !important; }
button[kind="secondary"] { background:#182725; border-color:#3a5b50; color:var(--ink); }
button[kind="primary"] { background:var(--green); border-color:var(--green); }
@media (max-width: 800px) { .block-container { padding:1.5rem 1rem 3rem; } .topbar { display:block; } .date-label, .date-value { text-align:left; } .date-label { margin-top:1.2rem; } .title { font-size:2.4rem; } }
</style>
""", unsafe_allow_html=True)


def settings_from_form() -> dict[str, str]:
    return {
        "smtp_host": st.session_state.smtp_host,
        "smtp_port": str(st.session_state.smtp_port),
        "smtp_security": st.session_state.smtp_security,
        "smtp_user": st.session_state.smtp_user,
        "smtp_password": st.session_state.smtp_password,
        "recipient": st.session_state.recipient,
    }


def initialize_settings() -> None:
    defaults = email_settings_from_env()
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    try:
        st.session_state.smtp_port = int(st.session_state.get("smtp_port") or 587)
    except (TypeError, ValueError):
        st.session_state.smtp_port = 587


initialize_settings()
st.markdown('<div class="topbar"><div><div class="kicker">SignalForge / operations monitor</div><div class="title">Daily control room</div><div class="description">Detect movement in payments, volume, refunds, and churn before it becomes a business problem.</div></div><div><div class="date-label">Workspace status</div><div class="date-value">Monitoring active</div></div></div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Workspace")
    source = st.radio("Data source", ["Sample / uploaded file", "Live SQLite database"], index=0)
    uploaded = st.file_uploader("Daily data file", type=["csv", "xlsx", "xls"]) if source == "Sample / uploaded file" else None
    database = st.text_input("SQLite database path", os.getenv("SQLITE_DB_PATH", "signalforge.db")) if source == "Live SQLite database" else ""
    if source == "Sample / uploaded file":
        st.caption("Sample data is used by default. Upload your own CSV or Excel file for this session.")
    else:
        st.caption("The database is reloaded every 30 seconds.")
    st.markdown("### Detection settings")
    threshold = st.slider("Sensitivity", 1.0, 4.0, 2.0, .1, help="Lower values flag smaller movements.")
    window = st.slider("Baseline days", 3, 30, 7)
    st.markdown("### Summary settings")
    model = st.text_input("Ollama model", "llama3.2")
    st.caption("Ollama is optional. The monitor uses a local fallback when it is unavailable.")
    auto_notify = st.checkbox("Automatically email new signals", help="Sends once when a new latest-reading anomaly set appears.")
    with st.expander("Email delivery setup"):
        st.caption("Enter your own sender and recipient. These credentials stay in this browser session and are never written to the repository.")
        st.text_input("Your SMTP host", key="smtp_host", placeholder="smtp.gmail.com")
        st.number_input("SMTP port", min_value=1, max_value=65535, step=1, key="smtp_port")
        st.selectbox("Connection security", ["STARTTLS", "SSL"], key="smtp_security")
        st.text_input("Your sender email / SMTP username", key="smtp_user", placeholder="you@example.com")
        st.text_input("Your SMTP app password", type="password", key="smtp_password")
        st.text_input("Recipient email", key="recipient", placeholder="alerts-recipient@example.com")
        if st.button("Send test email", use_container_width=True):
            try:
                send_test_email(settings_from_form())
                st.success("Test email sent.")
            except (ValueError, OSError) as error:
                st.error(str(error))

if source == "Live SQLite database":
    ensure_sqlite_schema(database)
    st_autorefresh(interval=30_000, key="sqlite_refresh")
    data = load_sqlite_data(database)
else:
    data = load_data(uploaded) if uploaded else load_data("sample_data.csv")
if data.empty:
    st.info("No rows are available yet. Add records to the daily_metrics table.")
    st.stop()
anomalies = detect_anomalies(data, window=window, threshold=threshold)
latest = data.iloc[-1]
alert_signature = tuple((item.metric, item.date, round(item.value, 6)) for item in anomalies)
if auto_notify and anomalies and alert_signature != st.session_state.get("last_alert_signature"):
    try:
        summaries = {item.metric: generate_summary(item, model=model) for item in anomalies}
        send_email_alert(anomalies, summaries, settings_from_form())
        st.session_state.last_alert_signature = alert_signature
        st.toast("New alert report sent.")
    except (ValueError, OSError) as error:
        st.warning(f"Automatic delivery is not configured: {error}")
st.markdown(f'<div class="kicker">Latest reading / {latest["date"].strftime("%d %b %Y")}</div>', unsafe_allow_html=True)

cards = st.columns(4)
for card, (metric, label) in zip(cards, METRICS.items()):
    with card:
        value = latest.get(metric)
        if pd.isna(value):
            value_text = "Not available"
        elif metric in {"payment_success_rate", "refund_rate", "user_churn"}:
            value_text = f"{value:.1f}%"
        else:
            value_text = f"{value:,.0f}"
        flagged = any(item.metric == metric for item in anomalies)
        status = "Review signal" if flagged else "Within baseline"
        state_class = " alert" if flagged else ""
        st.markdown(f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{value_text}</div><div class="metric-status{state_class}">{status}</div></div>', unsafe_allow_html=True)

left, right = st.columns([1.08, 1], gap="large")
with left:
    st.markdown('<div class="section-title">Signals requiring attention</div>', unsafe_allow_html=True)
    if not anomalies:
        st.markdown('<div class="healthy">No unusual movement detected in the latest reading.</div>', unsafe_allow_html=True)
    else:
        for anomaly in anomalies:
            summary = html.escape(generate_summary(anomaly, model=model)).replace("\n", "<br>")
            st.markdown(f'<div class="signal"><div class="signal-title">{html.escape(anomaly.label)} {anomaly.direction} {abs(anomaly.change_pct):.1f}%</div><div class="signal-copy">{summary}</div><div class="signal-meta">{anomaly.severity} / z-score {anomaly.z_score:.1f} / baseline {anomaly.baseline:.1f}</div></div>', unsafe_allow_html=True)
    if anomalies:
        st.markdown('<div class="section-title">Alert delivery</div>', unsafe_allow_html=True)
        st.caption("Review the signals, then send the current report to the configured recipient.")
        if st.button("Send current alert report", type="primary", use_container_width=True):
            summaries = {item.metric: generate_summary(item, model=model) for item in anomalies}
            try:
                send_email_alert(anomalies, summaries, settings_from_form())
                st.success("Alert report sent.")
            except (ValueError, OSError) as error:
                st.error(str(error))
with right:
    st.markdown('<div class="section-title">Trend context</div>', unsafe_allow_html=True)
    chart_metrics = [metric for metric in METRICS if metric in data]
    st.line_chart(data.set_index("date")[chart_metrics].rename(columns=METRICS), height=330)
    with st.expander("View source data"):
        st.dataframe(data, use_container_width=True, hide_index=True)
