# streamlit_app.py (final cleaned version)

import os
import re
import logging
import datetime
import json
from typing import List, Optional

import streamlit as st
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
except Exception:
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
    Adjust this function to match your sheet tab naming convention.
    Current format used here: "M/YYYY" (e.g. "12/2025")
    """
    return f"{d.month}/{d.year}"


# --- Helper: compute daily metrics from month sheet ------------------------
def compute_daily_metrics_from_sheet(client: SheetsClient, sheet_id: str, sheet_name: str, target_date: datetime.date):
    metrics = {
        "num_invoices": 0,
        "num_products": 0,
        "total_system_sales": 0.0,
        "total_sales": 0.0,
        "entered_cash_amount": 0.0,
        "card_amount": 0.0,
        "cash_outs": 0.0,
    }
    try:
        df = client.read_month_sheet(sheet_id, sheet_name)
    except Exception:
        return metrics

    # find date column
    date_col = None
    for c in df.columns:
        if c.lower().strip().startswith("date"):
            date_col = c
            break
    if date_col is None:
        return metrics

    def parse_date_cell(v):
        if pd.isna(v) or v == "":
            return None
        try:
            return pd.to_datetime(v).date()
        except Exception:
            return None

    row_idx = None
    for idx, v in df[date_col].items():
        if parse_date_cell(v) == target_date:
            row_idx = idx
            break
    if row_idx is None:
        return metrics

    def get_num(col):
        if col in df.columns:
            val = df.at[row_idx, col]
            try:
                return float(str(val).replace(",", "").strip() or 0)
            except Exception:
                return 0.0
        return 0.0

    # num_invoices
    if "No.Invoices" in df.columns:
        try:
            metrics["num_invoices"] = int(float(str(df.at[row_idx, "No.Invoices"]).strip() or 0))
        except Exception:
            metrics["num_invoices"] = 0

    # num_products
    if "No. Products" in df.columns:
        try:
            metrics["num_products"] = int(float(str(df.at[row_idx, "No. Products"]).replace(",", "").strip() or 0))
        except Exception:
            metrics["num_products"] = 0

    # total_system_sales
    if "Total System Sales" in df.columns:
        metrics["total_system_sales"] = get_num("Total System Sales")
    else:
        metrics["total_system_sales"] = get_num("System amount Cash") + get_num("System amount Card")

    # total_sales
    if "Total Sales" in df.columns:
        metrics["total_sales"] = get_num("Total Sales")
    else:
        metrics["total_sales"] = metrics["total_system_sales"]

    metrics["entered_cash_amount"] = get_num("entered cash amount")
    metrics["card_amount"] = get_num("Card amount")
    metrics["cash_outs"] = get_num("Cash outs")

    return metrics


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

# Manual fields (these should match your sheet columns)
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

if sheet_id:
    try:
        df_month = client.read_month_sheet(sheet_id, sheet_name)
        # try find date column
        date_col = None
        for c in df_month.columns:
            if c.lower().strip().startswith("date"):
                date_col = c
                break
        row_idx = None
        if date_col is not None:
            def _parse_date_cell(v):
                if pd.isna(v):
                    return None
                try:
                    return pd.to_datetime(v).date()
                except Exception:
                    return None
            for idx, v in df_month[date_col].items():
                if _parse_date_cell(v) == selected_date:
                    row_idx = idx
                    break
        if row_idx is not None:
            for fld in manual_fields:
                old_values[fld] = df_month.at[row_idx, fld] if fld in df_month.columns else ""
        else:
            for fld in manual_fields:
                old_values[fld] = ""
    except Exception:
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
    st.text_input(f"Old - {c}", value=str(old_values.get(c, "")), key=f"old_display_{c}", disabled=True)

st.subheader("Edited values (input to save)")
for c in manual_fields:
    edited[c] = st.text_input(f"Edited - {c}", value=str(old_values.get(c, "")), key=f"edited_{c}")

# Button to compare & save
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

        # ------------------ SAVE BLOCK ----------------------------------
        sheet_id = SHEET_ID_MAP.get(branch)
        if not sheet_id:
            st.error("No sheet_id configured for branch: " + str(branch))
        else:
            sheet_name = month_sheet_name_for_date(selected_date)
            try:
                try:
                    df = client.read_month_sheet(sheet_id, sheet_name)
                except Exception:
                    cols = ["Date"] + manual_fields
                    df = pd.DataFrame(columns=cols)

                # identify date column
                date_col = None
                for c in df.columns:
                    if c.lower().strip().startswith("date"):
                        date_col = c
                        break
                if date_col is None:
                    date_col = "Date"
                    if date_col not in df.columns:
                        df.insert(0, date_col, "")

                # find or append row for selected_date
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

                if row_idx is None:
                    new_row = {c: "" for c in df.columns}
                    new_row[date_col] = selected_date.strftime("%Y-%m-%d")
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    row_idx = len(df) - 1

                # ensure manual fields exist and cast to object to avoid dtype issues
                for fld in manual_fields:
                    if fld not in df.columns:
                        df[fld] = ""
                    else:
                        try:
                            df[fld] = df[fld].astype(object)
                        except Exception:
                            df[fld] = df[fld].apply(lambda x: x if (x is None or isinstance(x, str)) else x)

                # update changed fields in dataframe
                for fld in changed_fields:
                    df.at[row_idx, fld] = edited.get(fld, df.at[row_idx, fld])

                # Set closed_by to the selected role (user) and closure_time if not present
                def _find_column(df_local, candidates):
                    for cand in candidates:
                        for c in df_local.columns:
                            if c.lower().strip() == cand.lower().strip():
                                return c
                    return None

                closed_by_col = _find_column(df, ["Closed By", "closed_by", "closedby", "ClosedBy"])
                closure_time_col = _find_column(df, ["Closed At", "Closure Time", "closed_at", "closed_time", "closedat"])

                # write closed_by (role)
                closed_by_value = str(role)
                if not closed_by_col:
                    closed_by_col = "Closed By"
                    if closed_by_col not in df.columns:
                        df[closed_by_col] = ""
                df.at[row_idx, closed_by_col] = closed_by_value

                # write closure_time: if sheet has a value use it, else set now
                if closure_time_col and df.at[row_idx, closure_time_col]:
                    closure_time_value = str(df.at[row_idx, closure_time_col])
                else:
                    closure_time_value = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')
                    if not closure_time_col:
                        closure_time_col = "Closure Time"
                        if closure_time_col not in df.columns:
                            df[closure_time_col] = ""
                    df.at[row_idx, closure_time_col] = closure_time_value

                # write the updated month sheet back
                client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)

                # Build changelog row
                changelog_row = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "user": role,
                    "branch": branch,
                    "date": selected_date.isoformat(),
                    "changed_fields": ", ".join(changed_fields),
                }

                # Append to central changelog if supported (fallback to ChangeLog tab)
                try:
                    client.append_changelog(sheet_id, changelog_row)
                except Exception:
                    try:
                        changelog_df = client.read_month_sheet(sheet_id, "ChangeLog")
                    except Exception:
                        changelog_df = pd.DataFrame(columns=list(changelog_row.keys()))
                    changelog_df = pd.concat([changelog_df, pd.DataFrame([changelog_row])], ignore_index=True)
                    client.write_month_sheet(sheet_id=sheet_id, sheet_name="ChangeLog", df=changelog_df)

                st.success("Saved changes and appended changelog.")
            except Exception:
                st.error("Failed to save changes. Check logs for details.")
                logger.exception("Save changes failed")
        # ------------------ END SAVE BLOCK -----------------------------------

# --- Build and send detailed report (no debug) -----------------------------
st.write("---")
if st.button("Send today's summary email (test)"):
    sheet_id = SHEET_ID_MAP.get(branch)
    sheet_name = month_sheet_name_for_date(selected_date)

    # compute daily metrics
    metrics = compute_daily_metrics_from_sheet(client, sheet_id, sheet_name, selected_date)

    # build report (requested fields); removed changed_fields per your request
    report = {
        "branch": branch,
        "date": selected_date.isoformat(),
        "No.Invoices": metrics.get("num_invoices", 0),
        "No. Products": metrics.get("num_products", 0),
        "System amount Cash": f"{metrics.get('entered_cash_amount', 0):.2f}",
        "System amount Card": f"{metrics.get('card_amount', 0):.2f}",
        "Total System Sales": f"{metrics.get('total_system_sales', 0):.2f}",
        "closure_time": "",
        "closed_by": str(role),
    }

    # read sheet and attach the single-row snapshot if present
    snapshot_path: Optional[str] = None
    try:
        df_month = client.read_month_sheet(sheet_id, sheet_name)
        # locate date row
        date_col = None
        for c in df_month.columns:
            if c.lower().strip().startswith("date"):
                date_col = c
                break

        row_idx = None
        if date_col is not None:
            def _parse_date_cell(v):
                if pd.isna(v) or v == "":
                    return None
                try:
                    return pd.to_datetime(v).date()
                except Exception:
                    return None
            for idx, v in df_month[date_col].items():
                if _parse_date_cell(v) == selected_date:
                    row_idx = idx
                    break

        if row_idx is not None:
            def get_cell(col):
                return df_month.at[row_idx, col] if col in df_month.columns else ""

            # fill report values from sheet if possible (overwrites computed ones)
            for key_col in ["No.Invoices", "No. Products", "System amount Cash", "System amount Card", "Total System Sales"]:
                if key_col in df_month.columns:
                    report[key_col] = str(get_cell(key_col))

            # closure_time: use sheet value if present otherwise use now
            closure_candidates = ["Closed At", "Closure Time", "closed_at", "closed_time", "ClosedAt", "closed at"]
            closure_col = None
            for cand in closure_candidates:
                for c in df_month.columns:
                    if c.lower().strip() == cand.lower().strip():
                        closure_col = c
                        break
                if closure_col:
                    break
            if closure_col:
                report["closure_time"] = str(get_cell(closure_col))
            else:
                report["closure_time"] = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')

            # create CSV snapshot for attachment
            try:
                row_df = df_month.loc[[row_idx]]
                snapshot_path = f"/tmp/closure_{branch}_{selected_date.isoformat()}.csv"
                row_df.to_csv(snapshot_path, index=False)
            except Exception:
                snapshot_path = None
    except Exception:
        # if reading sheet failed, continue with computed metrics
        logger.exception("Failed to read month sheet for report")

    # recipients
    raw_recipients = st.secrets.get("REPORT_RECIPIENTS", "")
    recipients: List[str] = [r.strip() for r in raw_recipients.split(",") if r.strip()] if raw_recipients else []
    if not recipients:
        st.error("No recipients configured for email report.")
    else:
        try:
            if snapshot_path:
                ok = send_daily_submission_report(report, recipients, attachments=[snapshot_path])
            else:
                ok = send_daily_submission_report(report, recipients)
            if ok:
                st.success(f"Daily report email sent to: {', '.join(recipients)}")
            else:
                st.error("Failed to send email — check logs.")
        except Exception:
            st.error("Failed to send email. See logs.")
            logger.exception("Email send failed (detailed)")

# --- End of file -----------------------------------------------------------
