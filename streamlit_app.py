# streamlit_app.py
# Final updated: Role -> Branch -> month tab -> day (must select existing day) -> Submit
# No optional date picker, no auto-create rows/columns/tabs. Closure info goes to ChangeLog only.

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

# timezone helper
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    CAIRO_TZ = ZoneInfo("Africa/Cairo")
except Exception:
    CAIRO_TZ = None

# project modules (make sure these exist)
from src.sheets_client import SheetsClient
from src.email_report import send_daily_submission_report

# ---------- logging ----------
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# ---------- init Sheets client ----------
st_secrets = st.secrets if hasattr(st, "secrets") else {}
try:
    client = SheetsClient(st_secrets)
except Exception:
    st.error("Failed to initialize Sheets client. Check SERVICE_ACCOUNT_JSON and sheet IDs in Streamlit secrets.")
    logger.exception("SheetsClient init failed")
    st.stop()

# ---------- helpers ----------
def now_cairo():
    if CAIRO_TZ is not None:
        return datetime.datetime.now(CAIRO_TZ)
    else:
        # fallback: try env TZ
        try:
            tz_name = os.environ.get("TZ", "")
            if tz_name:
                from zoneinfo import ZoneInfo as _ZI
                return datetime.datetime.now(_ZI(tz_name))
        except Exception:
            pass
        return datetime.datetime.now()

def format_dt_cairo(dt: datetime.datetime):
    if dt is None:
        return ""
    if CAIRO_TZ is not None:
        return dt.astimezone(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S")
    else:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

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

def compute_financials_from_inputs(inputs: dict, sheet_row: Optional[dict]=None, superpay_percent: float = None):
    def val(k):
        if k in inputs and inputs.get(k) not in (None, ""):
            return float(str(inputs.get(k)).replace(",", "").strip() or 0)
        if sheet_row and k in sheet_row:
            try:
                return float(str(sheet_row.get(k) or 0).replace(",", "").strip())
            except Exception:
                return 0.0
        return 0.0

    system_cash = val("System amount Cash")
    system_card = val("System amount Card")
    entered_cash = val("entered cash amount")
    card_amount = val("Card amount")
    cash_outs = val("Cash outs")
    petty_cash = val("Petty cash")  # may be 0 if absent

    cash_deficit = system_cash - entered_cash
    card_deficit = system_card - card_amount

    sp_percent = float(superpay_percent) if superpay_percent is not None else 0.0
    superpay_expected = card_amount - (card_amount * sp_percent / 100.0)

    net_cash = entered_cash - cash_outs - petty_cash

    return {
        "System amount Cash": round(system_cash,2),
        "System amount Card": round(system_card,2),
        "entered cash amount": round(entered_cash,2),
        "Card amount": round(card_amount,2),
        "Cash outs": round(cash_outs,2),
        "Petty cash": round(petty_cash,2),
        "cash_deficit": round(cash_deficit, 2),
        "card_deficit": round(card_deficit, 2),
        "superpay_expected": round(superpay_expected, 2),
        "net_cash": round(net_cash, 2),
    }

def compute_daily_metrics_from_sheet(client: SheetsClient, sheet_id: str, sheet_name: str, target_date: datetime.date) -> Dict[str, object]:
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

# ---------- UI ----------
st.set_page_config(layout="centered")
st.title("Register Closures — THE G")

# Top: Role and Branch only
role = st.selectbox("Role", ["Operations Manager", "Operations Team Member", "Alexandria Store Manager", "Zamalek Store Manager"], index=0)
branch = st.selectbox("Branch", ["Zamalek", "Alexandria"], index=0)
st.markdown(f"**Branch:** {branch}  •  **Role:** {role}")

# sheet ids map
SHEET_ID_MAP = {
    "Zamalek": st.secrets.get("ZAMALEK_SHEET_ID"),
    "Alexandria": st.secrets.get("ALEXANDRIA_SHEET_ID"),
}
sheet_id = SHEET_ID_MAP.get(branch)
if not sheet_id:
    st.error("Missing sheet ID for this branch. Set ZAMALEK_SHEET_ID / ALEXANDRIA_SHEET_ID in Streamlit secrets.")
    st.stop()

# Allowed months Dec_2025 .. Dec_2026
start = datetime.date(2025, 12, 1)
end   = datetime.date(2026, 12, 1)
months = []
cur = start
while cur <= end:
    months.append(cur.strftime("%B_%Y"))
    year = cur.year + (cur.month // 12)
    month = cur.month % 12 + 1
    cur = datetime.date(year, month, 1)

# fetch real tabs for the spreadsheet
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

st.markdown("Select month tab (Dec_2025 → Dec_2026) and day")

if not available_month_tabs:
    st.warning("No monthly tabs available in the Dec_2025–Dec_2026 range for this branch. Create required tabs manually in the spreadsheet.")
    st.stop()

sheet_name = st.selectbox("Select month tab", available_month_tabs)
if not sheet_name:
    st.info("Select a month tab to continue.")
    st.stop()

st.write(f"Selected tab: **{sheet_name}**")

# read selected tab (do NOT create)
try:
    df_month = client.read_month_sheet(sheet_id, sheet_name)
except Exception as e:
    st.error(f"Tab '{sheet_name}' not found or cannot be read. Make sure the tab exists and the service account has Editor access.")
    logger.exception("read_month_sheet failed for %s: %s", sheet_name, e)
    st.stop()

# find Date column
date_col = None
for c in df_month.columns:
    if c.lower().strip().startswith("date"):
        date_col = c
        break

if date_col is None:
    st.info("No 'Date' column found in the selected tab. Add a 'Date' column (format yyyy-mm-dd) and reload the app.")
    st.stop()

# build day dropdown from existing Date values — REQUIRE selection
days_map = {}
for idx, v in df_month[date_col].items():
    d = _parse_date_cell(v)
    if d:
        days_map[d.isoformat()] = idx

if not days_map:
    st.info("No date rows in this tab. You manage rows manually in the sheet; the app will not create them.")
    st.stop()

sorted_days = sorted(days_map.keys())
selected_label = st.selectbox("Select day", ["(choose)"] + sorted_days)
if selected_label == "(choose)":
    st.info("Select a day to load its row for editing.")
    st.stop()

# user chose a valid existing day
chosen_day = datetime.date.fromisoformat(selected_label)
row_idx = days_map[selected_label]
st.success(f"Loaded row for {chosen_day.isoformat()}")

# Edit fields
st.markdown("Enter values for this day")

edit_columns = [
    "No.Invoices","No. Products","System amount Cash","System amount Card","Total System Sales",
    "entered cash amount","Card amount","Cash outs","Employee advances","Transportaion Goods","Transportaion Allowance",
    "Cleaning","Internet","Cleaning supplies","Bills","Others",
]

# prefill values
current_values = {col: "" for col in edit_columns}
for col in edit_columns:
    if col in df_month.columns:
        current_values[col] = df_month.at[row_idx, col] if pd.notna(df_month.at[row_idx, col]) else ""

with st.form("single_submit_form", clear_on_submit=False):
    inputs = {}
    for col in edit_columns:
        inputs[col] = st.text_input(col, value=str(current_values.get(col, "")), key=f"input_{col}")

    submit = st.form_submit_button("Submit")

    if submit:
        # read latest sheet
        try:
            df = client.read_month_sheet(sheet_id, sheet_name)
        except Exception:
            st.error("Failed to read sheet before save.")
            logger.exception("read before write failed")
            st.stop()

        # locate date column again
        date_col_local = None
        for c in df.columns:
            if c.lower().strip().startswith("date"):
                date_col_local = c
                break
        if date_col_local is None:
            st.error("Date column missing on write — aborting.")
            st.stop()

        # find the existing row index for chosen_day (must exist)
        row_idx_local = None
        for idx, v in df[date_col_local].items():
            if _parse_date_cell(v) == chosen_day:
                row_idx_local = idx
                break
        if row_idx_local is None:
            st.error("Selected day is not present in the sheet (row missing). The app will not create rows. Add the row manually and try again.")
            st.stop()

        # ensure columns the user edits exist in sheet — but DO NOT create audit columns
        missing_edit_cols = [c for c in edit_columns if c not in df.columns]
        if missing_edit_cols:
            # we DO NOT create these automatically: user must add them to the template tab
            st.error(f"Missing expected columns in the sheet: {', '.join(missing_edit_cols)}. Add them to the sheet template and reload.")
            st.stop()

        # compute changed values (for changelog)
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

        # write inputs into the existing row (only existing columns)
        for col in edit_columns:
            df.at[row_idx_local, col] = inputs.get(col, df.at[row_idx_local, col])

        # DO NOT write Closed By / Closure Time into month sheet
        closure_dt = now_cairo()
        closure_ts = format_dt_cairo(closure_dt)
        closed_by = str(role)

        # write back to sheet (overwrite values only)
        try:
            client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)
        except Exception as e:
            st.error("Failed to write to Google Sheets. Ensure the tab exists and service account has Editor access.")
            logger.exception("write_month_sheet failed: %s", e)
            st.stop()

        # prepare financials computed (not written to month sheet)
        sp_pct = float(st.secrets.get("SUPERPAY_PERCENT", 0))
        # build sheet_row dict for baseline values if needed
        sheet_row_dict = {c: df.at[row_idx_local, c] for c in df.columns}
        financials = compute_financials_from_inputs(inputs, sheet_row=sheet_row_dict, superpay_percent=sp_pct)

        # append changelog (ChangeLog tab is allowed to be created)
        changelog_row = {
            "timestamp": closure_ts,
            "user": closed_by,
            "branch": branch,
            "sheet": sheet_name,
            "date": chosen_day.isoformat(),
            "changed_fields": ", ".join(changed.keys()) if changed else "(filled)",
            "prev_values": json.dumps(prev_vals, default=str),
            "new_values": json.dumps(changed if changed else inputs, default=str),
            "closed_by": closed_by,
            "closure_time": closure_ts,
            "financials": json.dumps(financials, default=str)
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

        st.success(f"Saved values for {chosen_day.isoformat()} — changelog appended. Closure time (Cairo): {closure_ts}")

        # build and send daily summary email (report includes computed financials)
        try:
            report = {
                "branch": branch,
                "date": chosen_day.isoformat(),
                "No.Invoices": inputs.get("No.Invoices", sheet_row_dict.get("No.Invoices", "")),
                "No. Products": inputs.get("No. Products", sheet_row_dict.get("No. Products", "")),
                "System amount Cash": f"{financials['System amount Cash']:.2f}",
                "System amount Card": f"{financials['System amount Card']:.2f}",
                "entered cash amount": f"{financials['entered cash amount']:.2f}",
                "Card amount": f"{financials['Card amount']:.2f}",
                "cash_deficit": f"{financials['cash_deficit']:.2f}",
                "card_deficit": f"{financials['card_deficit']:.2f}",
                "superpay_expected": f"{financials['superpay_expected']:.2f}",
                "net_cash": f"{financials['net_cash']:.2f}",
                "closure_time": closure_ts,
                "closed_by": closed_by,
            }

            # snapshot optional
            snapshot_path = None
            try:
                updated_df = client.read_month_sheet(sheet_id, sheet_name)
                for idx, v in updated_df[date_col].items():
                    if _parse_date_cell(v) == chosen_day:
                        snapshot_df = updated_df.loc[[idx]]
                        snapshot_path = f"/tmp/closure_{branch}_{chosen_day.isoformat()}.csv"
                        snapshot_df.to_csv(snapshot_path, index=False)
                        break
            except Exception:
                snapshot_path = None

            raw_recipients = st.secrets.get("REPORT_RECIPIENTS", "")
            recipients: List[str] = [r.strip() for r in raw_recipients.split(",") if r.strip()] if raw_recipients else []
            if not recipients:
                st.warning("No recipients configured for email report.")
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
            logger.exception("Daily summary send failed")
            st.error("Failed to send daily summary email. See logs.")

# footer (local service-account JSON path)
st.markdown("---")
st.markdown("Service-account JSON (local): `/mnt/data/b19a61d2-13c7-49f2-a19f-f20665f57d6e.json`")
