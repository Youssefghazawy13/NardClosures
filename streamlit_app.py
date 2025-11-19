# streamlit_app.py (complete, mobile-first, production-ready)
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

# project modules (must exist under src/)
from src.sheets_client import SheetsClient
from src.calc import recalc_forward
from src.email_report import send_daily_submission_report

# ----------------------- logging ------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # keep production quieter; exceptions are still logged

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
    """Robust conversion to float used for numeric comparisons."""
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

def month_sheet_name_for_date(d: datetime.date) -> str:
    """Return tab name for a given date. Adjust if your tabs use a different format."""
    return f"{d.month}/{d.year}"

def _find_column(df_local: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Return first matching column name from candidates (case-insensitive exact match)."""
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
    """Compute summary metrics for the target_date row in the given sheet tab."""
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
st.title("Register Closures — THE G")

# Top controls (immediately under title, mobile-friendly)
st.markdown("### Controls")
role = st.selectbox("Role", ["Operations Manager", "Operations Team Member", "Alexandria Store Manager", "Zamalek Store Manager"], index=0)
branch = st.selectbox("Branch", ["Zamalek", "Alexandria"], index=0)
selected_date = st.date_input("Date", value=datetime.date.today())

st.markdown(f"**Branch:** {branch} • **Role:** {role} • **Date:** {selected_date.isoformat()}")

# Map branch -> sheet id
SHEET_ID_MAP = {
    "Zamalek": st.secrets.get("ZAMALEK_SHEET_ID"),
    "Alexandria": st.secrets.get("ALEXANDRIA_SHEET_ID"),
}
sheet_id = SHEET_ID_MAP.get(branch)
if not sheet_id:
    st.error("Missing sheet ID for this branch. Set ZAMALEK_SHEET_ID / ALEXANDRIA_SHEET_ID in Streamlit secrets.")
    st.stop()

# ----------------------- Sheet tabs listing & preview ----------------------
st.markdown("### Sheet (month) selection & preview")

# Try to get worksheet names (tabs)
sheet_tabs = []
try:
    if hasattr(client, "list_worksheets"):
        sheet_tabs = client.list_worksheets(sheet_id)
    else:
        # Fallback attempt: if client exposes a method to read an 'Index' tab listing tabs
        try:
            idx_df = client.read_month_sheet(sheet_id, "Index")
            if "sheet_name" in idx_df.columns:
                sheet_tabs = [str(x) for x in idx_df["sheet_name"].dropna().unique()]
        except Exception:
            sheet_tabs = []
except Exception:
    sheet_tabs = []

# if no tabs discovered, allow manual input
if not sheet_tabs:
    st.info("No tabs discovered automatically. You may type the sheet (tab) name, e.g. '12/2025'. The app will create the tab if missing.")
    sheet_name = st.text_input("Sheet / Tab name (e.g. 12/2025)", value=month_sheet_name_for_date(selected_date))
else:
    sheet_name = st.selectbox("Choose sheet (month tab)", sheet_tabs, index=0 if month_sheet_name_for_date(selected_date) not in sheet_tabs else sheet_tabs.index(month_sheet_name_for_date(selected_date)))

st.write(f"Selected sheet: **{sheet_name}**")

# Preview sheet (and create template tab if it doesn't exist)
df_month = None
created_new_tab = False
try:
    try:
        df_month = client.read_month_sheet(sheet_id, sheet_name)
    except Exception:
        # create a template tab with columns we expect
        template_cols = ["Date",
                         "No.Invoices","No. Products",
                         "System amount Cash","System amount Card","Total System Sales","Total Sales",
                         "entered cash amount","Card amount","Cash outs",
                         "Employee advances","Transportaion Goods","Transportaion Allowance",
                         "Cleaning","Internet","Cleaning supplies","Bills","Others",
                         "Closed By","Closure Time"]
        empty_df = pd.DataFrame(columns=template_cols)
        client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=empty_df)
        df_month = empty_df
        created_new_tab = True
except Exception:
    logger.exception("Could not read or create month tab")
    st.error("Unable to read or create the selected tab. Check Service Account permissions & sheet ID.")

if created_new_tab:
    st.info("Month tab did not exist — created a new tab using the standard template.")

if df_month is not None:
    try:
        st.dataframe(df_month.head(10), use_container_width=True)
    except Exception:
        st.write("Preview not available")

# ----------------------- Choose day (from Date column) ---------------------
st.markdown("### Choose day")
date_col = None
if df_month is not None:
    for c in df_month.columns:
        if c.lower().strip().startswith("date"):
            date_col = c
            break

row_idx = None
chosen_day = selected_date
if df_month is None or date_col is None:
    st.info("No Date column detected or sheet unavailable. You can create a new row by saving the form below.")
    # chosen_day stays as selected_date
else:
    # collect available days from the Date column
    days_map = {}
    for idx, v in df_month[date_col].items():
        d = _parse_date_cell(v)
        if d:
            days_map[d.isoformat()] = idx
    if days_map:
        # show dropdown labeled with ISO dates (keeps interface simple)
        day_labels = sorted(days_map.keys())
        selected_label = st.selectbox("Existing days in sheet", ["(choose)"] + day_labels)
        if selected_label != "(choose)":
            chosen_day = datetime.date.fromisoformat(selected_label)
            row_idx = days_map[selected_label]
    else:
        st.info("No date rows found in this tab yet. Create the row by filling the form and saving.")

# ----------------------- Edit form for selected day ------------------------
st.markdown("### Day row (edit or create)")
edit_columns = [
    "No.Invoices","No. Products","System amount Cash","System amount Card","Total System Sales",
    "entered cash amount","Card amount","Cash outs","Cleaning","Internet","Cleaning supplies","Bills","Others"
]

# prefill inputs with existing row values if available
current_values = {col: "" for col in edit_columns}
if df_month is not None and date_col and row_idx is not None:
    for col in edit_columns:
        if col in df_month.columns:
            current_values[col] = df_month.at[row_idx, col] if pd.notna(df_month.at[row_idx, col]) else ""

# build form
with st.form("day_edit_form", clear_on_submit=False):
    inputs = {}
    for col in edit_columns:
        inputs[col] = st.text_input(col, value=str(current_values.get(col, "")), key=f"frm_{sheet_id}_{sheet_name}_{col}")
    save_clicked = st.form_submit_button("Save row to sheet")

    if save_clicked:
        # read latest sheet
        try:
            df = client.read_month_sheet(sheet_id, sheet_name)
        except Exception:
            # create template if missing
            template_cols = ["Date"] + edit_columns + ["Closed By", "Closure Time"]
            df = pd.DataFrame(columns=template_cols)

        # find or create date column
        date_col_local = None
        for c in df.columns:
            if c.lower().strip().startswith("date"):
                date_col_local = c
                break
        if date_col_local is None:
            date_col_local = "Date"
            if date_col_local not in df.columns:
                df.insert(0, date_col_local, "")

        # locate existing row for chosen_day
        row_idx_local = None
        for idx, v in df[date_col_local].items():
            if _parse_date_cell(v) == chosen_day:
                row_idx_local = idx
                break

        if row_idx_local is None:
            # append new row
            new_row = {c: "" for c in df.columns}
            new_row[date_col_local] = chosen_day.isoformat()
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            row_idx_local = len(df) - 1

        # ensure edit columns exist and cast to object
        for col in edit_columns:
            if col not in df.columns:
                df[col] = ""
            else:
                try:
                    df[col] = df[col].astype(object)
                except Exception:
                    df[col] = df[col].apply(lambda x: x if (x is None or isinstance(x, str)) else x)

        # compute changes (numeric safe where possible)
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

        if not changed:
            st.info("No changes detected. Nothing to save.")
        else:
            # write changed values
            for col, val in changed.items():
                df.at[row_idx_local, col] = val

            # write Closed By (use role) and Closure Time if missing
            closed_by_col = _find_column(df, ["Closed By", "closed_by", "ClosedBy"]) or "Closed By"
            if closed_by_col not in df.columns:
                df[closed_by_col] = ""
            df.at[row_idx_local, closed_by_col] = str(role)

            closure_col = _find_column(df, ["Closed At", "Closure Time", "closed_at", "closed_time"]) or "Closure Time"
            if closure_col not in df.columns:
                df[closure_col] = ""
            # if already populated keep it; else set now
            if not df.at[row_idx_local, closure_col]:
                df.at[row_idx_local, closure_col] = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')

            # write back to sheet
            try:
                client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)
            except Exception as e:
                st.error("Failed to write to Google Sheets. Check permissions & sheet ID.")
                logger.exception("Failed to write month sheet")
            else:
                # append changelog inside the same Google Sheet (ChangeLog tab)
                changelog_row = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "user": role,
                    "branch": branch,
                    "sheet": sheet_name,
                    "date": chosen_day.isoformat(),
                    "changed_fields": ", ".join(changed.keys()),
                    "prev_values": json.dumps(prev_vals, default=str),
                    "new_values": json.dumps(changed, default=str)
                }
                try:
                    # prefer client.append_changelog if available
                    if hasattr(client, "append_changelog"):
                        client.append_changelog(sheet_id, changelog_row)
                    else:
                        # fallback to writing ChangeLog tab
                        try:
                            ch_df = client.read_month_sheet(sheet_id, "ChangeLog")
                        except Exception:
                            ch_df = pd.DataFrame(columns=list(changelog_row.keys()))
                        ch_df = pd.concat([ch_df, pd.DataFrame([changelog_row])], ignore_index=True)
                        client.write_month_sheet(sheet_id=sheet_id, sheet_name="ChangeLog", df=ch_df)
                except Exception:
                    logger.exception("Failed to append changelog")

                st.success(f"Saved row for {chosen_day.isoformat()} — changed: {', '.join(changed.keys())}")

                # offer CSV download snapshot of updated row
                try:
                    # re-read updated sheet and produce snapshot
                    updated_df = client.read_month_sheet(sheet_id, sheet_name)
                    for idx, v in updated_df[date_col_local].items():
                        if _parse_date_cell(v) == chosen_day:
                            snapshot_df = updated_df.loc[[idx]]
                            csv_bytes = snapshot_df.to_csv(index=False).encode("utf-8")
                            st.download_button(label="Download updated row (CSV)", data=csv_bytes, file_name=f"closure_{branch}_{chosen_day.isoformat()}.csv", mime="text/csv")
                            break
                except Exception:
                    logger.exception("Failed to prepare snapshot download")

# ----------------------- Send today's summary email (detailed) -------------
st.markdown("### Reporting")
if st.button("Send today's summary email (test)"):
    # compute metrics
    metrics = compute_daily_metrics_from_sheet(client, sheet_id, sheet_name, selected_date)

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

    # try to attach the day's row CSV if exists
    snapshot_path: Optional[str] = None
    try:
        df_month2 = client.read_month_sheet(sheet_id, sheet_name)
        # find date column and row
        date_col2 = None
        for c in df_month2.columns:
            if c.lower().strip().startswith("date"):
                date_col2 = c
                break
        row_idx2 = None
        if date_col2:
            for idx, v in df_month2[date_col2].items():
                if _parse_date_cell(v) == selected_date:
                    row_idx2 = idx
                    break
        if row_idx2 is not None:
            def _get_cell(col):
                return df_month2.at[row_idx2, col] if col in df_month2.columns else ""
            for key_col in ["No.Invoices", "No. Products", "System amount Cash", "System amount Card", "Total System Sales"]:
                if key_col in df_month2.columns:
                    report[key_col] = str(_get_cell(key_col))

            # closure_time: prefer sheet value else now
            closure_col2 = _find_column(df_month2, ["Closed At", "Closure Time", "closed_at", "closed_time"])
            if closure_col2:
                report["closure_time"] = str(_get_cell(closure_col2))
            else:
                report["closure_time"] = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')

            # snapshot CSV
            try:
                snapshot_df = df_month2.loc[[row_idx2]]
                snapshot_path = f"/tmp/closure_{branch}_{selected_date.isoformat()}.csv"
                snapshot_df.to_csv(snapshot_path, index=False)
            except Exception:
                snapshot_path = None
    except Exception:
        logger.exception("Failed reading month sheet for email report")

    # recipients from secrets
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

# ----------------------- Footer / service-account path ---------------------
st.markdown("---")
st.markdown("Service-account JSON (local): `sandbox:/mnt/data/b19a61d2-13c7-49f2-a19f-f20665f57d6e.json`")
