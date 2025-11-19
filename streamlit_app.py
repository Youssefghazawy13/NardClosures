# streamlit_app.py
# Single-submit flow: Role -> Branch -> Month tab -> Day -> Edit fields -> Submit writes row & sends report

import os
import re
import json
import logging
import datetime
from typing import List, Optional, Dict

import streamlit as st
from dotenv import load_dotenv
load_dotenv()

import pandas as pd

# project modules (ensure these exist)
from src.sheets_client import SheetsClient
from src.email_report import send_daily_submission_report

# ----------------------- logging ------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# ----------------------- init Sheets client --------------------------------
st_secrets = st.secrets if hasattr(st, "secrets") else {}
try:
    client = SheetsClient(st_secrets)
except Exception:
    st.error("Failed to initialize Sheets client. Check SERVICE_ACCOUNT_JSON and sheet IDs in Streamlit secrets.")
    logger.exception("SheetsClient init failed")
    st.stop()

# ----------------------- helpers ------------------------------------------
def safe_float(val):
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s == "" or s in ("-", "—"):
        return 0.0
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.replace(",", "")
    s = re.sub(r"[^\d\.\-]", "", s)
    if s == "" or s == "-" or s == ".":
        raise ValueError(f"Unparseable numeric value: {val!r}")
    return float(s)

def _find_column(df_local: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for cand in candidates:
        for c in df_local.columns:
            if c.lower().strip() == cand.lower().strip():
                return c
    return None

def _parse_date_cell(v):
    if pd.isna(v) or v == "":
        return None
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None

def compute_daily_metrics_from_sheet(client: SheetsClient, sheet_id: str, sheet_name: str, target_date: datetime.date) -> Dict[str, object]:
    """Compute a compact summary for the target_date row in sheet_name."""
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

    date_col = None
    for c in df.columns:
        if c.lower().strip().startswith("date"):
            date_col = c
            break
    if date_col is None:
        return metrics

    row_idx = None
    for idx, v in df[date_col].items():
        if _parse_date_cell(v) == target_date:
            row_idx = idx
            break
    if row_idx is None:
        return metrics

    def get_num(col):
        if col in df.columns:
            try:
                return float(str(df.at[row_idx, col]).replace(",", "").strip() or 0)
            except Exception:
                return 0.0
        return 0.0

    if "No.Invoices" in df.columns:
        try:
            metrics["num_invoices"] = int(float(str(df.at[row_idx, "No.Invoices"]).strip() or 0))
        except Exception:
            metrics["num_invoices"] = 0

    if "No. Products" in df.columns:
        try:
            metrics["num_products"] = int(float(str(df.at[row_idx, "No. Products"]).replace(",", "").strip() or 0))
        except Exception:
            metrics["num_products"] = 0

    if "Total System Sales" in df.columns:
        metrics["total_system_sales"] = get_num("Total System Sales")
    else:
        metrics["total_system_sales"] = get_num("System amount Cash") + get_num("System amount Card")

    if "Total Sales" in df.columns:
        metrics["total_sales"] = get_num("Total Sales")
    else:
        metrics["total_sales"] = metrics["total_system_sales"]

    metrics["entered_cash_amount"] = get_num("entered cash amount")
    metrics["card_amount"] = get_num("Card amount")
    metrics["cash_outs"] = get_num("Cash outs")

    return metrics

# ----------------------- UI (mobile-first) --------------------------------
st.set_page_config(layout="centered")
st.title("Register Closures — Slot-X")

# Top controls: ROLE, BRANCH only
role = st.selectbox("Role", ["Operations Manager", "Operations Team Member", "Alexandria Store Manager", "Zamalek Store Manager"], index=0)
branch = st.selectbox("Branch", ["Zamalek", "Alexandria"], index=0)
st.markdown(f"**Branch:** {branch} • **Role:** {role}")

# Map branch -> sheet id
SHEET_ID_MAP = {
    "Zamalek": st.secrets.get("ZAMALEK_SHEET_ID"),
    "Alexandria": st.secrets.get("ALEXANDRIA_SHEET_ID"),
}
sheet_id = SHEET_ID_MAP.get(branch)
if not sheet_id:
    st.error("Missing sheet ID for this branch. Set ZAMALEK_SHEET_ID / ALEXANDRIA_SHEET_ID in Streamlit secrets.")
    st.stop()

# ---------- Month tabs restricted Dec_2025 .. Dec_2026 ------------
st.markdown("Select month tab and day")

start = datetime.date(2025, 12, 1)
end   = datetime.date(2026, 12, 1)
months = []
cur = start
while cur <= end:
    months.append(cur.strftime("%B_%Y"))  # e.g. December_2025
    year = cur.year + (cur.month // 12)
    month = cur.month % 12 + 1
    cur = datetime.date(year, month, 1)

# fetch real tabs
try:
    if hasattr(client, "list_worksheets"):
        real_tabs = client.list_worksheets(sheet_id) or []
    else:
        gc = getattr(client, "gc", None) or getattr(client, "_gc", None)
        if gc:
            sh = gc.open_by_key(sheet_id)
            real_tabs = [ws.title for ws in sh.worksheets()]
        else:
            real_tabs = []
except Exception:
    real_tabs = []

available_month_tabs = [m for m in months if m in real_tabs]

if not available_month_tabs:
    st.warning("No monthly tabs available in the Dec_2025–Dec_2026 range for this branch. Create tabs manually if needed.")
    sheet_name = st.text_input("Enter sheet (tab) name manually (e.g. December_2025):", value=months[0])
else:
    sheet_name = st.selectbox("Select month tab", available_month_tabs)

if not sheet_name:
    st.info("Choose a month tab to continue.")
    st.stop()

st.write(f"Selected tab: {sheet_name}")

# read chosen tab and gather days
try:
    df_month = client.read_month_sheet(sheet_id, sheet_name)
except Exception as e:
    st.error(f"Failed to read tab '{sheet_name}'. Ensure the tab exists and the service account has Editor access.")
    logger.exception("Failed to read tab %s: %s", sheet_name, e)
    st.stop()

date_col = None
for c in df_month.columns:
    if c.lower().strip().startswith("date"):
        date_col = c
        break

if date_col is None:
    st.info("No Date column found in this tab. Add a 'Date' column (yyyy-mm-dd) in the sheet.")
    choosen_day = None
    row_idx = None
else:
    days_map = {}
    for idx, v in df_month[date_col].items():
        d = _parse_date_cell(v)
        if d:
            days_map[d.isoformat()] = idx

    if not days_map:
        st.info("No date rows yet. Use the form below to create a row.")
        choosen_day = None
        row_idx = None
    else:
        sorted_days = sorted(days_map.keys())
        selected_label = st.selectbox("Select day", ["(choose)"] + sorted_days)
        if selected_label == "(choose)":
            choosen_day = None
            row_idx = None
            st.info("Pick a day to load its row for editing.")
        else:
            choosen_day = datetime.date.fromisoformat(selected_label)
            row_idx = days_map[selected_label]
            st.success(f"Loaded row for {choosen_day.isoformat()}")

# ----------------------- Edit fields (form) --------------------------------
st.markdown("Enter values for the selected day")

edit_columns = [
    "No.Invoices","No. Products","System amount Cash","System amount Card","Total System Sales",
    "entered cash amount","Card amount","Cash outs","Employee advances","Transportaion Goods","Transportaion Allowance",
    "Cleaning","Internet","Cleaning supplies","Bills","Others",
]

# prefill if existing row selected
current_values = {col: "" for col in edit_columns}
if date_col and row_idx is not None and df_month is not None:
    for col in edit_columns:
        if col in df_month.columns:
            current_values[col] = df_month.at[row_idx, col] if pd.notna(df_month.at[row_idx, col]) else ""

# Single submit form (one button)
with st.form("single_submit_form", clear_on_submit=False):
    inputs = {}
    for col in edit_columns:
        inputs[col] = st.text_input(col, value=str(current_values.get(col, "")), key=f"input_{col}")

    submit = st.form_submit_button("Submit")

    if submit:
        # validate that a day is selected (must pick an existing day or allow create)
        if choosen_day is None:
            st.error("No day selected. Select a day from the dropdown or create one by entering values and submitting.")
        else:
            # load latest sheet
            try:
                df = client.read_month_sheet(sheet_id, sheet_name)
            except Exception:
                st.error("Failed to read sheet before save.")
                logger.exception("read before write failed")
                st.stop()

            # ensure date column exists
            date_col_local = None
            for c in df.columns:
                if c.lower().strip().startswith("date"):
                    date_col_local = c
                    break
            if date_col_local is None:
                date_col_local = "Date"
                if date_col_local not in df.columns:
                    df.insert(0, date_col_local, "")

            # find row index for chosen day (or create new row)
            row_idx_local = None
            for idx, v in df[date_col_local].items():
                if _parse_date_cell(v) == choosen_day:
                    row_idx_local = idx
                    break
            if row_idx_local is None:
                # append new row
                new_row = {c: "" for c in df.columns}
                new_row[date_col_local] = choosen_day.isoformat()
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                row_idx_local = len(df) - 1

            # ensure columns exist
            for col in edit_columns:
                if col not in df.columns:
                    df[col] = ""
                else:
                    try:
                        df[col] = df[col].astype(object)
                    except Exception:
                        df[col] = df[col].apply(lambda x: x if (x is None or isinstance(x, str)) else x)

            # compute changes
            changed = {}
            prev_vals = {}
            for col in edit_columns:
                prev = df.at[row_idx_local, col] if col in df.columns else ""
                new = inputs.get(col, "")
                changed_flag = False
                try:
                    if safe_float(prev) != safe_float(new):
                        changed_flag = True
                except Exception:
                    if str(prev).strip() != str(new).strip():
                        changed_flag = True
                if changed_flag:
                    changed[col] = new
                    prev_vals[col] = prev

            # even if no numeric changes, we will write inputs to ensure the row is filled
            for col in edit_columns:
                df.at[row_idx_local, col] = inputs.get(col, df.at[row_idx_local, col])

            # write Closed By and Closure Time
            closed_by_col = _find_column(df, ["Closed By", "closed_by", "ClosedBy"]) or "Closed By"
            if closed_by_col not in df.columns:
                df[closed_by_col] = ""
            df.at[row_idx_local, closed_by_col] = str(role)

            closure_col = _find_column(df, ["Closed At", "Closure Time", "closed_at", "closed_time"]) or "Closure Time"
            if closure_col not in df.columns:
                df[closure_col] = ""
            if not df.at[row_idx_local, closure_col]:
                df.at[row_idx_local, closure_col] = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')

            # attempt to write sheet (will raise if worksheet missing)
            try:
                client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)
            except Exception as e:
                st.error("Failed to write to Google Sheets. Make sure the tab exists and service account has Editor access.")
                logger.exception("write_month_sheet failed: %s", e)
                st.stop()

            # append changelog in same spreadsheet
            changelog_row = {
                "timestamp": datetime.datetime.now().isoformat(),
                "user": role,
                "branch": branch,
                "sheet": sheet_name,
                "date": choosen_day.isoformat(),
                "changed_fields": ", ".join(changed.keys()) if changed else "(filled)",
                "prev_values": json.dumps(prev_vals, default=str),
                "new_values": json.dumps(changed if changed else inputs, default=str)
            }
            try:
                if hasattr(client, "append_changelog"):
                    client.append_changelog(sheet_id, changelog_row)
                else:
                    try:
                        ch_df = client.read_month_sheet(sheet_id, "ChangeLog")
                    except Exception:
                        ch_df = pd.DataFrame(columns=list(changelog_row.keys()))
                    ch_df = pd.concat([ch_df, pd.DataFrame([changelog_row])], ignore_index=True)
                    client.write_month_sheet(sheet_id=sheet_id, sheet_name="ChangeLog", df=ch_df)
            except Exception:
                logger.exception("Failed to append changelog")

            st.success(f"Row saved for {choosen_day.isoformat()} and changelog appended.")

            # Build daily summary report and send email
            try:
                metrics = compute_daily_metrics_from_sheet(client, sheet_id, sheet_name, choosen_day)
                report = {
                    "branch": branch,
                    "date": choosen_day.isoformat(),
                    "No.Invoices": metrics.get("num_invoices", 0),
                    "No. Products": metrics.get("num_products", 0),
                    "System amount Cash": f"{metrics.get('entered_cash_amount', 0):.2f}",
                    "System amount Card": f"{metrics.get('card_amount', 0):.2f}",
                    "Total System Sales": f"{metrics.get('total_system_sales', 0):.2f}",
                    "closure_time": df.at[row_idx_local, closure_col] if closure_col in df.columns else "",
                    "closed_by": str(role),
                }

                # try to attach the day's row CSV
                try:
                    updated_df = client.read_month_sheet(sheet_id, sheet_name)
                    for idx, v in updated_df[date_col].items():
                        if _parse_date_cell(v) == choosen_day:
                            snapshot_df = updated_df.loc[[idx]]
                            snapshot_path = f"/tmp/closure_{branch}_{choosen_day.isoformat()}.csv"
                            snapshot_df.to_csv(snapshot_path, index=False)
                            break
                except Exception:
                    snapshot_path = None

                raw_recipients = st.secrets.get("REPORT_RECIPIENTS", "")
                recipients: List[str] = [r.strip() for r in raw_recipients.split(",") if r.strip()] if raw_recipients else []
                if not recipients:
                    st.warning("No recipients configured for email report. Set REPORT_RECIPIENTS in secrets.")
                else:
                    if snapshot_path:
                        ok = send_daily_submission_report(report, recipients, attachments=[snapshot_path])
                    else:
                        ok = send_daily_submission_report(report, recipients)
                    if ok:
                        st.success(f"Daily summary email sent to: {', '.join(recipients)}")
                    else:
                        st.error("Failed to send daily summary email. Check SMTP secrets and logs.")
            except Exception:
                st.error("Failed to build/send daily summary. Check logs.")
                logger.exception("Daily summary send failed")

# Footer: local service-account JSON path (for your reference)
st.markdown("---")
st.markdown("Service-account JSON (local): `/mnt/data/b19a61d2-13c7-49f2-a19f-f20665f57d6e.json`")
