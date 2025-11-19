# streamlit_app.py
import os
import re
import logging
import datetime
import json

import streamlit as st
from dotenv import load_dotenv
load_dotenv()

import pandas as pd

# your project modules (assumes these exist in src/)
from src.sheets_client import SheetsClient
from src.calc import recalc_forward
from src.email_report import send_daily_submission_report

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Initialize client using Streamlit secrets ---
st_secrets = st.secrets if hasattr(st, "secrets") else {}
try:
    client = SheetsClient(st_secrets)
except Exception as e:
    st.error("Failed to initialize Sheets client. Check service account secrets and logs.")
    logger.exception("SheetsClient init failed")
    st.stop()

# --- Helper: robust numeric parsing -----------------------------------------
def safe_float(val):
    """
    Robust conversion to float:
    - None or empty or '-' or '—' -> 0.0
    - strips commas, currency symbols, whitespace
    - supports parentheses for negative numbers: (123.45) -> -123.45
    - removes any non-digit except dot and minus before float conversion
    - raises ValueError if still impossible
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s == "" or s in ("-", "—"):
        return 0.0

    # parentheses negative ( (123) => -123 )
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]

    # remove commas used as thousand separators
    s = s.replace(",", "")

    # remove common currency symbols and whitespace
    # keep digits, dot, minus only
    s = re.sub(r"[^\d\.\-]", "", s)

    if s == "" or s == "-" or s == ".":
        raise ValueError(f"Unparseable numeric value: {val!r}")

    return float(s)


# --- Example UI / Main app flow ---------------------------------------------
st.title("Register Closures — Streamlit (safe parser patch)")

# Sidebar: choose role & branch (minimal for the example)
st.sidebar.header("User & Branch")
role = st.sidebar.selectbox("Role", ["Operations Manager", "Operations Team Member", "Alexandria Store Manager", "Zamalek Store Manager"])
branch = st.sidebar.selectbox("Branch", ["Zamalek", "Alexandria"])

# Simple inputs for testing changed_fields logic
st.header("Daily entry (test)")

# Manual fields sample (should match the real manual_fields in your app)
manual_fields = [
    "No.Invoices", "No. Products", "System amount Cash", "System amount Card",
    "entered cash amount", "Card amount", "Cash outs", "Employee advances",
    "Transportaion Goods", "Transportaion Allowance", "Cleaning", "Internet",
    "Cleaning supplies", "Bills", "Others"
]

# Simulate "old_values" (read from sheet in the real app) - here we provide inputs for both
st.subheader("Old values (simulate loaded from sheet)")
old_values = {}
for c in manual_fields:
    old_values[c] = st.text_input(f"Old - {c}", value="0", key=f"old_{c}")

st.subheader("Edited values (what user will save)")
edited = {}
for c in manual_fields:
    edited[c] = st.text_input(f"Edited - {c}", value="0", key=f"new_{c}")

# Button to simulate save/compare
if st.button("Compare & Save"):
    changed_fields = []
    parse_errors = []
    for c in manual_fields:
        old_raw = old_values.get(c)
        new_raw = edited.get(c)

        try:
            old_num = safe_float(old_raw)
        except Exception as e:
            parse_errors.append((c, "old", old_raw, str(e)))
            logger.exception("Failed parsing old value for %s: %r", c, old_raw)
            # fallback to 0.0 to allow process to continue
            old_num = 0.0

        try:
            new_num = safe_float(new_raw)
        except Exception as e:
            parse_errors.append((c, "new", new_raw, str(e)))
            logger.exception("Failed parsing new value for %s: %r", c, new_raw)
            new_num = 0.0

        if old_num != new_num:
            changed_fields.append(c)

    # Show results
    if parse_errors:
        st.warning("Some fields had parsing issues. Check logs or correct inputs.")
        for (field, which, raw, err_msg) in parse_errors:
            st.write(f"Parse issue — field: {field}, which: {which}, raw: {raw!r}, error: {err_msg}")

    if changed_fields:
        st.success(f"Detected changed fields: {changed_fields}")
    else:
        st.info("No changed numeric fields detected.")

    # --- Place where your app would proceed to update sheet / append changelog ---
    # Example (pseudo):
    try:
        # Example function call - replace with your real save logic
        # client.write_month_sheet(sheet_id, sheet_name, df)
        st.write("Proceeding to save... (this is a placeholder)")
    except Exception as e:
        st.error("Failed to save changes.")
        logger.exception("Save failed")

# ---------------------------------------------------------------------------
# End of file
