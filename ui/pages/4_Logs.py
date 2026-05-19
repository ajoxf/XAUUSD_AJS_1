"""Event log viewer — tails events.jsonl."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from ui.components import get_settings, sidebar_controls

st.set_page_config(page_title="Logs — XAUUSD Bot", page_icon="📜", layout="wide")
sidebar_controls()

st.title("📜 Logs")
st.caption("Every decision the bot makes is recorded here. Useful for "
            "post-mortem and debugging.")

settings = get_settings()
events_path = settings.log_dir / "events.jsonl"
text_path = settings.log_dir / "bot.log"

if not events_path.exists():
    st.info("No events logged yet. Start the bot from the sidebar.")
    st.stop()

# Read all events
with events_path.open() as f:
    events = [json.loads(line) for line in f if line.strip()]

if not events:
    st.info("Log file is empty.")
    st.stop()

st.caption(f"`{events_path}` — {len(events)} events")

# Filters
c1, c2, c3 = st.columns([2, 2, 1])
with c1:
    kinds = sorted({e.get("kind", "unknown") for e in events})
    selected_kinds = st.multiselect("Filter by event type", options=kinds,
                                      default=kinds)
with c2:
    search = st.text_input("Search (substring match)", value="")
with c3:
    tail = st.number_input("Show last N", min_value=10, max_value=5000,
                            value=200, step=50)

filtered = [e for e in events
             if e.get("kind") in selected_kinds
             and (not search or search.lower() in json.dumps(e).lower())]
filtered = filtered[-int(tail):]

# Render as a DataFrame
rows = []
for e in filtered:
    rows.append({
        "Time (UTC)": e.get("ts", "")[:19].replace("T", " "),
        "Event": e.get("kind", ""),
        "Detail": json.dumps({k: v for k, v in e.items()
                                if k not in ("ts", "kind")},
                              default=str)[:300],
    })
df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True, hide_index=True, height=600)

# Raw text log
with st.expander("Show raw text log"):
    if text_path.exists():
        text = text_path.read_text()
        st.code(text[-10000:], language="log")
    else:
        st.caption("No text log yet.")

# Download buttons
st.markdown("---")
dcol = st.columns(2)
with dcol[0]:
    st.download_button("📥 Download events (JSONL)",
                        events_path.read_bytes(),
                        file_name="events.jsonl",
                        mime="application/jsonl")
with dcol[1]:
    if text_path.exists():
        st.download_button("📥 Download text log",
                            text_path.read_bytes(),
                            file_name="bot.log",
                            mime="text/plain")
