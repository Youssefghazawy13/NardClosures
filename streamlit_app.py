# streamlit_app.py (full updated file)
import os
import re
import logging
import datetime
import json

import streamlit as st
# TEMP SMTP CHECK - paste and run, then remove
import smtplib, ssl
import streamlit as st

st.sidebar.markdown("### SMTP debug (temporary)")
try:
    server = st.secrets.get("SMTP_SERVER")
    port = int(st.secrets.get("SMTP_PORT") or 587)
    user = st.secrets.get("SMTP_USER")
    pwd = st.secrets.get("SMTP_PASSWORD")
    st.sidebar.write("SMTP server preview:", server, port)
    st.sidebar.write("SMTP user (masked):", (user[:3] + "..." + user[-5:]) if user else None)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(server, port, timeout=20) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(user, pwd)
    st.sidebar.success("SMTP login successful ✅")
except Exception as e:
    st.sidebar.error("SMTP test failed. See error below.")
    st.sidebar.write(repr(e))
    import logging
    logging.exception("SMTP test failed")

from dotenv import load_dotenv
load_dotenv()

import pandas as pd

# --- Project modules (ensure these exist in src/) ---
from src.sheets_client import SheetsClient
from src.calc import recalc_forward
from src.email_report import send_daily_submission_report

# --- Logging ---------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Initialize Sheets client using Streamlit secrets ----------------------
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


# --- Utility: map branch -> sheet id from secrets ---------------------------
SHEET_ID_MAP = {
    "Zamalek": st.secrets.get("ZAMALEK_SHEET_ID"),
    "Alexandria": st.secrets.get("ALEXANDRIA_SHEET_ID"),
}

# --- Helper: get month-sheet name (change to match your actual tabs) -------
def month_sheet_name_for_date(d: datetime.date) -> str:
    """
    Return the worksheet tab name for a date.
    Adjust this function if your tabs are named like "1/2025" or "Dec_2025" or "December 2025".
    Current format: "M/YYYY" (e.g. "12/2025")
    """
    return f"{d.month}/{d.year}"


# --- UI --------------------------------------------------------------------
st.title("Register Closures — Streamlit")

# Sidebar user selection
st.sidebar.header("User & Branch")
role = st.sidebar.selectbox(
    "Role",
    [
        "Operations Manager",
        "Operations Team Member",
        "Alexandria Store Manager",
        "Zamalek Store Manager",
    ],
)
branch = st.sidebar.selectbox("Branch", ["Zamalek", "Alexandria"])

# Date selector
st.sidebar.header("Date")
selected_date = st.sidebar.date_input("Select date", value=datetime.date.today())

# For demo purposes: define manual fields used in the sheet.
# Replace this list with the exact columns used in your Google Sheets header.
manual_fields = [
    "No.Invoices",
    "No. Products",
    "System amount Cash",
    "System amount Card",
    "entered cash amount",
    "Card amount",
    "Cash outs",
    "Employee advances",
    "Transportaion Goods",
    "Transportaion Allowance",
    "Cleaning",
    "Internet",
    "Cleaning supplies",
    "Bills",
    "Others",
]

st.header("Daily entry (manual fields)")
st.write("Fill values below and click Compare & Save")

# Load old values from sheet (if exists) to prefill inputs
sheet_id = SHEET_ID_MAP.get(branch)
sheet_name = month_sheet_name_for_date(selected_date)

old_values = {}
edited = {}

# Attempt to load the month sheet and prefill old_values. If missing, continue with blanks.
if sheet_id:
    try:
        df_month = client.read_month_sheet(sheet_id, sheet_name)
        # attempt to find Date column
        date_col = None
        for c in df_month.columns:
            if c.lower().strip().startswith("date"):
                date_col = c
                break
        row_idx = None
        if date_col is not None:
            # find matching date row
            def parse_date_cell(v):
                if pd.isna(v):
                    return None
                try:
                    return pd.to_datetime(v).date()
                except Exception:
                    return None
            for idx, v in df_month[date_col].items():
                if parse_date_cell(v) == selected_date:
                    row_idx = idx
                    break
        # If a row exists, load old values from that row
        if row_idx is not None:
            for fld in manual_fields:
                if fld in df_month.columns:
                    old_values[fld] = df_month.at[row_idx, fld]
                else:
                    old_values[fld] = ""
        else:
            # no row yet for date: old values empty
            for fld in manual_fields:
                old_values[fld] = ""
    except Exception:
        # Could not read sheet (sheet missing or permissions) — keep old_values empty
        logger.exception("Failed to read month sheet for prefill")
        for fld in manual_fields:
            old_values[fld] = ""
else:
    st.warning("Sheet ID not configured for branch. Prefill disabled.")
    for fld in manual_fields:
        old_values[fld] = ""

# Render inputs
st.subheader("Old values (read from sheet)")
for c in manual_fields:
    # show old values in disabled text inputs for reference
    st.text_input(f"Old - {c}", value=str(old_values.get(c, "")), key=f"old_display_{c}", disabled=True)

st.subheader("Edited values (input to save)")
for c in manual_fields:
    edited[c] = st.text_input(f"Edited - {c}", value=str(old_values.get(c, "")), key=f"edited_{c}")

# Button to compare & save
if st.button("Compare & Save"):
    # Detect changed numeric fields with robust parsing
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
            old_num = 0.0

        try:
            new_num = safe_float(new_raw)
        except Exception as e:
            parse_errors.append((c, "new", new_raw, str(e)))
            logger.exception("Failed parsing new value for %s: %r", c, new_raw)
            new_num = 0.0

        if old_num != new_num:
            changed_fields.append(c)

    if parse_errors:
        st.warning("Some fields had parsing issues. Check logs or correct inputs.")
        for (field, which, raw, err_msg) in parse_errors:
            st.write(f"Parse issue — field: {field}, which: {which}, raw: {raw!r}, error: {err_msg}")

    if not changed_fields:
        st.info("No changed numeric fields detected. Nothing to save.")
    else:
        st.success(f"Detected changed fields: {changed_fields}")

        # ------------------ SAVE BLOCK (replace placeholder) ------------------
        # Map branch to sheet id
        sheet_id = SHEET_ID_MAP.get(branch)
        if not sheet_id:
            st.error("No sheet_id configured for branch: " + str(branch))
        else:
            sheet_name = month_sheet_name_for_date(selected_date)
            try:
                # Read the month sheet (or create an empty df with expected columns)
                try:
                    df = client.read_month_sheet(sheet_id, sheet_name)
                except Exception:
                    # If sheet or tab missing, create an empty df with Date + manual_fields
                    cols = ["Date"] + manual_fields
                    df = pd.DataFrame(columns=cols)

                # Identify Date column name or create one if missing
                date_col = None
                for c in df.columns:
                    if c.lower().strip().startswith("date"):
                        date_col = c
                        break
                if date_col is None:
                    date_col = "Date"
                    if date_col not in df.columns:
                        df.insert(0, date_col, "")

                # Find row for selected_date
                def parse_date_cell(v):
                    if pd.isna(v) or v == "":
                        return None
                    try:
                        return pd.to_datetime(v).date()
                    except Exception:
                        return None

                row_idx = None
                for idx, v in df[date_col].items():
                    if parse_date_cell(v) == selected_date:
                        row_idx = idx
                        break

                # Append new row if date row not found
                if row_idx is None:
                    new_row = {c: "" for c in df.columns}
                    new_row[date_col] = selected_date.strftime("%Y-%m-%d")
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    row_idx = len(df) - 1

                # Ensure all manual fields exist as columns
                for fld in manual_fields:
                    if fld not in df.columns:
                        df[fld] = ""

                # Update changed fields into dataframe row
                for fld in changed_fields:
                    df.at[row_idx, fld] = edited.get(fld, df.at[row_idx, fld])

                # Write the updated month sheet back
                # If your SheetsClient supplies a safer patch/update method, prefer that to avoid rewriting entire sheet.
                client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)

                # Build changelog row
                changelog_row = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "user": role,
                    "branch": branch,
                    "date": selected_date.isoformat(),
                    "changed_fields": ", ".join(changed_fields),
                }

                # Append to central changelog if supported
                try:
                    client.append_changelog(sheet_id, changelog_row)
                except Exception:
                    # fallback: create/read "ChangeLog" tab and append
                    try:
                        changelog_df = client.read_month_sheet(sheet_id, "ChangeLog")
                    except Exception:
                        changelog_df = pd.DataFrame(columns=list(changelog_row.keys()))
                        # --- Optional: send email report button (small test) -----------------------
st.write("---")
if st.button("Send today's summary email (test)"):
    try:
        # Build a minimal summary to send — adapt fields to your desired report
        report = {
            "date": selected_date.isoformat(),
            "branch": branch,
            "changed_fields": ", ".join(changed_fields) if 'changed_fields' in locals() else "",
        }

        # ---- Email sending block (correctly indented inside try) ----
        raw_recipients = st.secrets.get("REPORT_RECIPIENTS", "")
        if raw_recipients and isinstance(raw_recipients, str):
            recipients = [r.strip() for r in raw_recipients.split(",") if r.strip()]
        else:
            smtp_user = st.secrets.get("SMTP_USER")
            recipients = [smtp_user] if smtp_user else []

        if not recipients:
            st.error("No email recipients configured. Set REPORT_RECIPIENTS or SMTP_USER in Streamlit secrets.")
        else:
            ok = send_daily_submission_report(report, recipients)
            if ok:
                st.success(f"Daily email report sent to: {', '.join(recipients)}")
            else:
                st.error("Failed to send email — check logs.")

    except Exception:
        st.error("Failed to send email. Check logs.")
        logger.exception("Email send failed")

# --- End of file -----------------------------------------------------------
