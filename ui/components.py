"""Shared Streamlit widgets and session-state helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from config.settings import Settings
from ui.supervisor import EngineSupervisor


def get_settings() -> Settings:
    if "settings" not in st.session_state:
        st.session_state.settings = Settings.from_env()
    return st.session_state.settings


def get_supervisor() -> EngineSupervisor:
    settings = get_settings()
    if "supervisor" not in st.session_state:
        st.session_state.supervisor = EngineSupervisor(settings)
    return st.session_state.supervisor


def replace_supervisor(new_settings: Settings) -> EngineSupervisor:
    """Tear down any running engine and rebuild with new settings."""
    sup = st.session_state.get("supervisor")
    if sup is not None:
        sup.stop()
    st.session_state.settings = new_settings
    st.session_state.supervisor = EngineSupervisor(new_settings)
    return st.session_state.supervisor


def status_badge(status: str) -> str:
    colours = {
        "stopped": "⚪",
        "starting": "🟡",
        "running": "🟢",
        "completed": "🔵",
        "error": "🔴",
    }
    return f"{colours.get(status, '⚪')} **{status.upper()}**"


def sidebar_controls() -> None:
    """Render Start/Stop and status in the sidebar. Called from every page."""
    sup = get_supervisor()
    settings = get_settings()

    with st.sidebar:
        st.markdown("### Bot Control")
        st.markdown(status_badge(sup.status))

        snap = sup.snapshot(recent_events_limit=0)
        if snap.last_heartbeat:
            st.caption(f"Last tick: {snap.last_heartbeat.strftime('%H:%M:%S UTC')}")

        cols = st.columns(2)
        with cols[0]:
            if st.button("▶ Start", use_container_width=True,
                          disabled=sup.is_running):
                sup.start()
                st.rerun()
        with cols[1]:
            if st.button("■ Stop", use_container_width=True,
                          disabled=not sup.is_running):
                sup.stop()
                st.rerun()

        st.markdown("---")
        st.markdown(f"**Mode:** `{settings.mode}`")
        st.markdown(f"**Symbol:** `{settings.symbol}`")
        st.markdown(f"**Risk:** `{settings.risk_pct * 100:.2f}%`")
        st.markdown(f"**Equity:** `${snap.equity:,.2f}`")

        if snap.last_error:
            st.error(f"Engine error:\n```\n{snap.last_error[:500]}\n```")


def save_env_file(env_path: Path, updates: dict[str, Any]) -> None:
    """Update .env file with given key/value pairs. Creates if missing."""
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k.strip()] = v.strip()
    existing.update({str(k): str(v) for k, v in updates.items()})
    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text("\n".join(lines) + "\n")
