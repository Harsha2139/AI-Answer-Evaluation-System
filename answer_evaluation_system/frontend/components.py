"""Reusable Streamlit UI building blocks (styling, cards, tables, error handling, flash messages)."""
import html
import logging

import pandas as pd
import streamlit as st

from utils.helpers import AppError

logger = logging.getLogger(__name__)

NAVY, BLUE = "#0B1F3A", "#2563EB"

CSS = f"""
<style>
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1250px; }}
section[data-testid="stSidebar"] {{ background: {NAVY}; }}
section[data-testid="stSidebar"] * {{ color: #E8EEFC !important; }}
section[data-testid="stSidebar"] .stButton button {{ background: {BLUE}; border: none; color: white !important; }}
.page-header {{ background: linear-gradient(120deg, {NAVY} 0%, #14407a 100%); color: #fff; padding: 1.2rem 1.5rem;
    border-radius: 14px; margin-bottom: 1.2rem; }}
.page-header h2 {{ margin: 0; color: #fff; font-size: 1.55rem; }}
.page-header p {{ margin: .25rem 0 0; color: #c9d8f5; font-size: .95rem; }}
.stat-card {{ background: #fff; border: 1px solid #e3e9f4; border-left: 5px solid {BLUE}; border-radius: 12px;
    padding: .9rem 1.1rem; box-shadow: 0 1px 3px rgba(11,31,58,.07); margin-bottom: .6rem; }}
.stat-card .label {{ color: #5b6b86; font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }}
.stat-card .value {{ color: {NAVY}; font-size: 1.75rem; font-weight: 700; line-height: 1.2; }}
.stat-card .hint {{ color: #7a89a3; font-size: .75rem; }}
.badge {{ display:inline-block; padding: .1rem .55rem; border-radius: 999px; font-size: .75rem; font-weight: 600; }}
.badge-ok {{ background:#dcfce7; color:#166534; }} .badge-warn {{ background:#fef3c7; color:#92400e; }}
.badge-bad {{ background:#fee2e2; color:#991b1b; }} .badge-info {{ background:#dbeafe; color:#1e40af; }}
.empty-state {{ text-align:center; padding: 2.2rem 1rem; border: 2px dashed #cbd5e8; border-radius: 14px; color:#5b6b86; }}
.engine-note {{ font-size:.8rem; color:#5b6b86; }}
@media (max-width: 700px) {{ .stat-card .value {{ font-size: 1.3rem; }} .page-header h2 {{ font-size: 1.2rem; }} }}
</style>
"""

_BADGES = {"approved": "ok", "pending": "warn", "rejected": "bad", "outdated": "bad", "submitted": "ok",
           "in_progress": "warn", "reopened": "info", "not_started": "info", "published": "ok", "draft": "warn",
           "closed": "bad", "open": "ok", "upcoming": "info", "Pass": "ok", "Fail": "bad"}


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    st.markdown(f'<div class="page-header"><h2>{html.escape(title)}</h2><p>{html.escape(subtitle)}</p></div>',
                unsafe_allow_html=True)


def stat_cards(items):
    """items: list of (label, value, hint)."""
    cols = st.columns(len(items))
    for col, (label, value, hint) in zip(cols, items):
        col.markdown(f'<div class="stat-card"><div class="label">{html.escape(str(label))}</div>'
                     f'<div class="value">{html.escape(str(value))}</div>'
                     f'<div class="hint">{html.escape(str(hint or ""))}</div></div>', unsafe_allow_html=True)


def badge(text: str) -> str:
    kind = _BADGES.get(text, "info")
    return f'<span class="badge badge-{kind}">{html.escape(str(text).replace("_", " "))}</span>'


def empty_state(message: str, icon: str = "📭"):
    st.markdown(f'<div class="empty-state"><div style="font-size:2rem">{icon}</div>{html.escape(message)}</div>',
                unsafe_allow_html=True)


def flash(message: str, kind: str = "success"):
    """Queue a message that survives st.rerun()."""
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash():
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind, st.info)(message)


def run(fn, *args, **kwargs):
    """Call a backend function; show friendly errors. Returns (ok, result)."""
    try:
        return True, fn(*args, **kwargs)
    except AppError as exc:
        st.error(str(exc))
    except Exception:  # unexpected: log details, show a safe message (transactions already rolled back)
        logger.exception("Unhandled error in %s", getattr(fn, "__name__", fn))
        st.error("Something went wrong and the operation was rolled back. Please try again or contact the administrator.")
    return False, None


def searchable_table(df: pd.DataFrame, key: str, filter_columns=(), empty_message="No records found."):
    """Dataframe with a free-text search box and optional dropdown filters. Returns the filtered frame."""
    if df is None or df.empty:
        empty_state(empty_message)
        return df
    n_filters = 1 + len(filter_columns)
    cols = st.columns(n_filters)
    query = cols[0].text_input("Search", key=f"{key}_q", placeholder="Type to search…")
    out = df
    for col, name in zip(cols[1:], filter_columns):
        choice = col.selectbox(name, ["All"] + sorted(df[name].dropna().astype(str).unique().tolist()), key=f"{key}_f_{name}")
        if choice != "All":
            out = out[out[name].astype(str) == choice]
    if query:
        mask = out.astype(str).apply(lambda r: r.str.contains(query, case=False, regex=False)).any(axis=1)
        out = out[mask]
    st.caption(f"{len(out)} of {len(df)} records")
    st.dataframe(out, hide_index=True)
    return out


def csv_button(df: pd.DataFrame, filename: str, key: str, label: str = "Download CSV"):
    if df is not None and not df.empty:
        st.download_button(label, df.to_csv(index=False).encode("utf-8"), file_name=filename, mime="text/csv", key=key)


def show_text(label: str, text: str, key: str, height: int = 140):
    st.text_area(label, value=text or "(no answer)", height=height, disabled=True, key=key)


def fmt_pct(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.1f}%"
