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
        # --- Replace the placeholder "Proceeding to save..." with this block ---

# map branch to sheet id from secrets
sheet_id_map = {
    "Zamalek": st.secrets.get("ZAMALEK_SHEET_ID"),
    "Alexandria": st.secrets.get("ALEXANDRIA_SHEET_ID"),
}

# helper: determine sheet tab name for a given date (adjust format to your sheet names)
def month_sheet_name_for_date(d: datetime.date) -> str:
    # example: "12_2025" or "Dec_2025" — change to match your tab names
    return f"{d.month}_{d.year}"

# Convert a human date or today's date for the save
# (in your real UI you already have a selected date — replace this)
save_date = datetime.date.today()  # replace with the actual date selected in UI

sheet_id = sheet_id_map.get(branch)
if not sheet_id:
    st.error("No sheet_id configured for branch: " + str(branch))
else:
    sheet_name = month_sheet_name_for_date(save_date)

    try:
        # Read the whole month sheet into DataFrame (preserves headers)
        df = client.read_month_sheet(sheet_id, sheet_name)

        # Find the row index for the selected date (assumes a 'Date' column)
        # If your Date column format differs, adjust parsing accordingly.
        date_col = None
        for c in df.columns:
            if c.lower().strip().startswith("date"):
                date_col = c
                break
        if date_col is None:
            st.error("Could not find a 'Date' column in the sheet.")
        else:
            # Normalize and find the date row
            # Assume df[date_col] contains dates in 'YYYY-MM-DD' or 'DD/MM/YYYY' etc.
            # We'll convert both sides to date objects for comparison.
            def parse_date_cell(v):
                if pd.isna(v):
                    return None
                try:
                    return pd.to_datetime(v).date()
                except Exception:
                    return None

            target_date = save_date
            row_idx = None
            for idx, v in df[date_col].items():
                if parse_date_cell(v) == target_date:
                    row_idx = idx
                    break

            if row_idx is None:
                # If the date row does not exist, append a new empty row and set its date
                new_row = {c: "" for c in df.columns}
                new_row[date_col] = target_date.strftime("%Y-%m-%d")
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                row_idx = len(df) - 1

            # Update the df row with edited values for manual_fields
            for fld in manual_fields:
                if fld in df.columns:
                    # Overwrite the value in the data frame
                    df.at[row_idx, fld] = edited.get(fld, df.at[row_idx, fld])
                else:
                    # Column missing in sheet: create it
                    df[fld] = ""
                    df.at[row_idx, fld] = edited.get(fld, "")

            # Write back the sheet (client should handle replacing the worksheet content)
            # Method name: adapt if your client uses a different method
            client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)

            # Append changelog (if your sheets client supports it). If not, you can write to a 'ChangeLog' sheet/tab.
            changelog_row = {
                "timestamp": datetime.datetime.now().isoformat(),
                "user": role,
                "branch": branch,
                "date": target_date.isoformat(),
                "changed_fields": ", ".join(changed_fields),
            }
            # try to append to a central changelog sheet
            try:
                client.append_changelog(sheet_id, changelog_row)   # adapt if method signature differs
            except Exception:
                # If append_changelog not implemented, write to a `ChangeLog` tab in the same sheet.
                try:
                    changelog_df = client.read_month_sheet(sheet_id, "ChangeLog")
                except Exception:
                    changelog_df = pd.DataFrame(columns=list(changelog_row.keys()))
                changelog_df = pd.concat([changelog_df, pd.DataFrame([changelog_row])], ignore_index=True)
                client.write_month_sheet(sheet_id=sheet_id, sheet_name="ChangeLog", df=changelog_df)

            st.success("Saved changes and appended changelog.")
    except Exception as e:
        st.error("Failed to save changes. Check logs for details.")
        logger.exception("Save changes failed")

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
