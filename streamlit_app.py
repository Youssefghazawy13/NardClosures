# streamlit_app.py
# Updated: Total System Sales and Total Sales computed by app; SuperPay expected computed as card * (1 - pct/100)
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

# timezone helper (Cairo)
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    CAIRO_TZ = ZoneInfo("Africa/Cairo")
except Exception:
    CAIRO_TZ = None

# project modules
from src.sheets_client import SheetsClient
from src.email_report import send_daily_submission_report

# logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# init Sheets client
st_secrets = st.secrets if hasattr(st, "secrets") else {}
try:
    client = SheetsClient(st_secrets)
except Exception:
    st.error("Failed to initialize Sheets client. Check SERVICE_ACCOUNT_JSON and sheet IDs in Streamlit secrets.")
    logger.exception("SheetsClient init failed")
    st.stop()

# helpers
def now_cairo():
    if CAIRO_TZ is not None:
        return datetime.datetime.now(CAIRO_TZ)
    else:
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

def _parse_date_cell(v):
    if pd.isna(v) or v == "":
        return None
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None

# lightweight day summary (used only optionally)
def compute_daily_metrics_from_sheet(client: SheetsClient, sheet_id: str, sheet_name: str, target_date: datetime.date) -> Dict[str, object]:
    metrics = {"num_invoices": 0, "num_products": 0, "total_system_sales": 0.0, "total_sales": 0.0,
               "entered_cash_amount": 0.0, "card_amount": 0.0, "cash_outs": 0.0}
    try:
        df = client.read_month_sheet(sheet_id, sheet_name)
    except Exception:
        return metrics
    date_col = None
    for c in df.columns:
        if c.lower().strip().startswith("date"):
            date_col = c; break
    if date_col is None:
        return metrics
    row_idx = None
    for idx, v in df[date_col].items():
        if _parse_date_cell(v) == target_date:
            row_idx = idx; break
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
        try: metrics["num_invoices"] = int(float(str(df.at[row_idx, "No.Invoices"]).strip() or 0))
        except Exception: metrics["num_invoices"] = 0
    if "No. Products" in df.columns:
        try: metrics["num_products"] = int(float(str(df.at[row_idx, "No. Products"]).replace(",", "").strip() or 0))
        except Exception: metrics["num_products"] = 0
    metrics["total_system_sales"] = get_num("Total System Sales") if "Total System Sales" in df.columns else get_num("System amount Cash") + get_num("System amount Card")
    metrics["total_sales"] = get_num("Total Sales") if "Total Sales" in df.columns else (get_num("entered cash amount") + get_num("entered Card amount") if "entered Card amount" in df.columns else get_num("entered cash amount") + get_num("Card amount"))
    metrics["entered_cash_amount"] = get_num("entered cash amount")
    if "entered Card amount" in df.columns:
        metrics["card_amount"] = get_num("entered Card amount")
    elif "Card amount" in df.columns:
        metrics["card_amount"] = get_num("Card amount")
    metrics["cash_outs"] = get_num("Cash outs")
    return metrics

# UI
st.set_page_config(layout="centered")
st.title("Register Closures — Slot-X")

# Top controls: Role & Branch only
role = st.selectbox("Role", ["Operations Manager", "Operations Team Member", "Alexandria Store Manager", "Zamalek Store Manager"], index=0)
branch = st.selectbox("Branch", ["Zamalek", "Alexandria"], index=0)
st.markdown(f"**Branch:** {branch}  •  **Role:** {role}")

# Sheet IDs map
SHEET_ID_MAP = {"Zamalek": st.secrets.get("ZAMALEK_SHEET_ID"), "Alexandria": st.secrets.get("ALEXANDRIA_SHEET_ID")}
sheet_id = SHEET_ID_MAP.get(branch)
if not sheet_id:
    st.error("Missing sheet ID for this branch. Set ZAMALEK_SHEET_ID / ALEXANDRIA_SHEET_ID in Streamlit secrets.")
    st.stop()

# Allowed months Dec_2025 .. Dec_2026
start = datetime.date(2025, 12, 1); end = datetime.date(2026, 12, 1)
months = []
cur = start
while cur <= end:
    months.append(cur.strftime("%B_%Y"))
    year = cur.year + (cur.month // 12)
    month = cur.month % 12 + 1
    cur = datetime.date(year, month, 1)

# list actual tabs
try:
    if hasattr(client, "list_worksheets"): real_tabs = client.list_worksheets(sheet_id) or []
    else:
        gc = getattr(client, "gc", None) or getattr(client, "_gc", None)
        if gc:
            sh = gc.open_by_key(sheet_id); real_tabs = [ws.title for ws in sh.worksheets()]
        else:
            real_tabs = []
except Exception:
    real_tabs = []

available_month_tabs = [m for m in months if m in real_tabs]

st.markdown("Select month tab and day")

if not available_month_tabs:
    st.warning("No monthly tabs available in the Dec_2025–Dec_2026 range for this branch. Create required tabs manually in the spreadsheet.")
    st.stop()

sheet_name = st.selectbox("Select month tab", available_month_tabs)
if not sheet_name:
    st.info("Select a month tab to continue."); st.stop()
st.write(f"Selected tab: **{sheet_name}**")

# read the selected tab (do NOT create)
try:
    df_month = client.read_month_sheet(sheet_id, sheet_name)
except Exception as e:
    st.error(f"Tab '{sheet_name}' not found or cannot be read. Make sure the tab exists and service account has Editor access.")
    logger.exception("read_month_sheet failed for %s: %s", sheet_name, e)
    st.stop()

# find Date column
date_col = None
for c in df_month.columns:
    if c.lower().strip().startswith("date"):
        date_col = c; break
if date_col is None:
    st.info("No 'Date' column found in the selected tab. Add a 'Date' column (format yyyy-mm-dd) and reload the app."); st.stop()

# build day dropdown from existing date rows (require selection)
days_map = {}
for idx, v in df_month[date_col].items():
    d = _parse_date_cell(v)
    if d: days_map[d.isoformat()] = idx
if not days_map:
    st.info("No date rows in this tab. Manage rows manually in the sheet; the app will not create them."); st.stop()
sorted_days = sorted(days_map.keys())
selected_label = st.selectbox("Select day", ["(choose)"] + sorted_days)
if selected_label == "(choose)":
    st.info("Select a day to load its row for editing."); st.stop()
chosen_day = datetime.date.fromisoformat(selected_label)
row_idx = days_map[selected_label]
st.success(f"Loaded row for {chosen_day.isoformat()}")

# Manual fields (Total System Sales removed — app calculates it)
edit_columns = [
    "No.Invoices","No. Products","System amount Cash","System amount Card",
    "entered cash amount","entered Card amount","Cash outs","system cashouts",
    "Employee advances","Transportation Goods","Transportation Allowance",
    "Cleaning","Internet","Cleaning supplies","Bills","Others","Others Comment","Petty cash"
]

# prefill
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
        # reload sheet
        try:
            df = client.read_month_sheet(sheet_id, sheet_name)
        except Exception:
            st.error("Failed to read sheet before save."); logger.exception("read before write failed"); st.stop()

        # find date col locally
        date_col_local = None
        for c in df.columns:
            if c.lower().strip().startswith("date"):
                date_col_local = c; break
        if date_col_local is None:
            st.error("Date column missing on write — aborting."); st.stop()

        # find row index (must exist)
        row_idx_local = None
        for idx, v in df[date_col_local].items():
            if _parse_date_cell(v) == chosen_day:
                row_idx_local = idx; break
        if row_idx_local is None:
            st.error("Selected day is not present in the sheet (row missing). The app will not create rows. Add the row manually and try again."); st.stop()

        # check that all manual edit columns exist
        missing_edit_cols = [c for c in edit_columns if c not in df.columns]
        if missing_edit_cols:
            st.error(f"Missing expected columns in the sheet: {', '.join(missing_edit_cols)}. Add them to the sheet template and reload."); st.stop()

        # compute changes for changelog
        changed = {}; prev_vals = {}
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
                changed[col] = new; prev_vals[col] = prev

        # write back only manual fields
        for col in edit_columns:
            df.at[row_idx_local, col] = inputs.get(col, df.at[row_idx_local, col])

        # DO NOT write Closed By / Closure Time to month sheet
        closure_dt = now_cairo(); closure_ts = format_dt_cairo(closure_dt); closed_by = str(role)

        # write back df to sheet (overwrite values)
        try:
            client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)
        except Exception as e:
            st.error("Failed to write to Google Sheets. Ensure the tab exists and service account has Editor access."); logger.exception("write_month_sheet failed: %s", e); st.stop()

        # prepare sheet_row_dict baseline
        sheet_row_dict = {c: df.at[row_idx_local, c] for c in df.columns}

        # per-day totals & accumulative calculations
        def _safe_from_sources(key, inputs_map, sheet_map):
            v = inputs_map.get(key, None)
            if v is None or str(v).strip() == "":
                v = sheet_map.get(key, 0)
            try:
                return float(str(v).replace(",", "").strip() or 0)
            except Exception:
                return 0.0

        system_cash_day = _safe_from_sources("System amount Cash", inputs, sheet_row_dict)
        system_card_day = _safe_from_sources("System amount Card", inputs, sheet_row_dict)
        entered_cash_day = _safe_from_sources("entered cash amount", inputs, sheet_row_dict)
        entered_card_day = _safe_from_sources("entered Card amount", inputs, sheet_row_dict)
        cash_outs_day = _safe_from_sources("Cash outs", inputs, sheet_row_dict)
        petty_cash_day = _safe_from_sources("Petty cash", inputs, sheet_row_dict)

        # Total System Sales = system_cash + system_card (app-calculated)
        total_system_sales_day = round(system_cash_day + system_card_day, 2)
        # Total Sales = entered cash amount + entered Card amount (app-calculated)
        total_sales_day = round(entered_cash_day + entered_card_day, 2)

        # accumulative sums up to chosen_day (inclusive) within this tab
        rows_by_date = []
        for idx2, v in df[date_col_local].items():
            d = _parse_date_cell(v)
            if d is not None:
                rows_by_date.append((d, idx2))
        rows_by_date.sort(key=lambda x: x[0])

        acc_cash = 0.0; acc_card = 0.0
        for d, ridx in rows_by_date:
            if d > chosen_day:
                break
            try:
                val_cash = float(str(df.at[ridx, "entered cash amount"]).replace(",", "").strip() or 0)
            except Exception:
                val_cash = 0.0
            try:
                val_card = float(str(df.at[ridx, "entered Card amount"]).replace(",", "").strip() or 0)
            except Exception:
                try:
                    val_card = float(str(df.at[ridx, "Card amount"]).replace(",", "").strip() or 0)
                except Exception:
                    val_card = 0.0
            acc_cash += val_cash; acc_card += val_card
        acc_cash = round(acc_cash, 2); acc_card = round(acc_card, 2); total_money_accumulative = round(acc_cash + acc_card, 2)

        # deficits and superpay expected and net cash
        cash_deficit_day = round(system_cash_day - entered_cash_day, 2)
        card_deficit_day = round(system_card_day - entered_card_day, 2)
        sp_pct = float(st.secrets.get("SUPERPAY_PERCENT", 0))
        # CORRECT SuperPay expected: card * (1 - pct/100)
        superpay_expected_day = round(entered_card_day * (1.0 - sp_pct / 100.0), 2)
        net_cash_day = round(entered_cash_day - cash_outs_day - petty_cash_day, 2)

        financials = {
            "system_cash_day": system_cash_day, "system_card_day": system_card_day,
            "entered_cash_day": entered_cash_day, "entered_card_day": entered_card_day,
            "cash_outs_day": cash_outs_day, "petty_cash_day": petty_cash_day,
            "total_system_sales_day": total_system_sales_day, "total_sales_day": total_sales_day,
            "cash_deficit_day": cash_deficit_day, "card_deficit_day": card_deficit_day,
            "superpay_expected_day": superpay_expected_day, "net_cash_day": net_cash_day,
            "accumulative_cash": acc_cash, "accumulative_card": acc_card, "total_money_accumulative": total_money_accumulative
        }

        # --------- WRITE computed columns into the month sheet row (only if columns exist) ----------
        computed_to_write = {
            "Total System Sales": total_system_sales_day,
            "Total Sales": total_sales_day,
            "Net cash": financials.get("net_cash_day"),
            "net cash": financials.get("net_cash_day"),
            "Accumulative cash": financials.get("accumulative_cash"),
            "Accumulative card": financials.get("accumulative_card"),
            "Total Money": financials.get("total_money_accumulative"),
            "SuperPay expected": financials.get("superpay_expected_day"),
            "Cash Deficit": financials.get("cash_deficit_day"),
            "Card Deficit": financials.get("card_deficit_day"),
        }

        # SuperPay diff compute
        superpay_sent_val = None
        if "SuperPay sent" in inputs and inputs.get("SuperPay sent", "") != "":
            try:
                superpay_sent_val = float(str(inputs.get("SuperPay sent")).replace(",", "").strip())
            except Exception:
                superpay_sent_val = None
        if superpay_sent_val is None:
            if "SuperPay sent" in sheet_row_dict:
                try:
                    superpay_sent_val = float(str(sheet_row_dict.get("SuperPay sent") or 0).replace(",", "").strip())
                except Exception:
                    superpay_sent_val = None

        if superpay_sent_val is not None:
            sp_expected = computed_to_write.get("SuperPay expected") or 0.0
            sp_diff = round((float(sp_expected) - float(superpay_sent_val)), 2)
            computed_to_write["SuperPay diff"] = sp_diff
        else:
            if "SuperPay diff" in df.columns:
                sp_expected = computed_to_write.get("SuperPay expected") or 0.0
                computed_to_write["SuperPay diff"] = round(float(sp_expected) - 0.0, 2)

        wrote_any = False
        for col_name, val in computed_to_write.items():
            if col_name in df.columns:
                try:
                    if val is None:
                        df.at[row_idx_local, col_name] = ""
                    else:
                        if isinstance(val, (int, float)):
                            df.at[row_idx_local, col_name] = round(float(val), 2)
                        else:
                            try:
                                df.at[row_idx_local, col_name] = round(float(str(val).replace(",", "").strip()), 2)
                            except Exception:
                                df.at[row_idx_local, col_name] = str(val)
                    wrote_any = True
                except Exception:
                    logger.exception("Failed writing computed column %s", col_name)

        if wrote_any:
            try:
                client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)
            except Exception:
                logger.exception("Failed to write computed columns back to sheet")

        # append changelog (ChangeLog may be created)
        changelog_row = {
            "timestamp": closure_ts, "user": closed_by, "branch": branch, "sheet": sheet_name,
            "date": chosen_day.isoformat(), "changed_fields": ", ".join(changed.keys()) if changed else "(filled)",
            "prev_values": json.dumps(prev_vals, default=str), "new_values": json.dumps(changed if changed else inputs, default=str),
            "closed_by": closed_by, "closure_time": closure_ts,
            "financials": json.dumps(financials, default=str),
            "total_system_sales_day": total_system_sales_day, "total_sales_day": total_sales_day,
            "accumulative_cash": acc_cash, "accumulative_card": acc_card, "total_money": total_money_accumulative
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

        # Build and send daily summary email
        try:
            report = {
                "branch": branch, "date": chosen_day.isoformat(),
                "No.Invoices": inputs.get("No.Invoices", sheet_row_dict.get("No.Invoices", "")),
                "No. Products": inputs.get("No. Products", sheet_row_dict.get("No. Products", "")),
                "System amount Cash": f"{system_cash_day:.2f}", "System amount Card": f"{system_card_day:.2f}",
                "Total System Sales": f"{total_system_sales_day:.2f}", "entered cash amount": f"{entered_cash_day:.2f}",
                "entered Card amount": f"{entered_card_day:.2f}", "Total Sales": f"{total_sales_day:.2f}",
                "Cash outs": f"{cash_outs_day:.2f}", "Petty cash": f"{petty_cash_day:.2f}",
                "Cash Deficit": f"{cash_deficit_day:.2f}", "Card Deficit": f"{card_deficit_day:.2f}",
                "SuperPay expected": f"{superpay_expected_day:.2f}", "SuperPay sent": sheet_row_dict.get("SuperPay sent", ""),
                "SuperPay diff": f"{(computed_to_write.get('SuperPay diff') or 0):.2f}",
                "Net cash": f"{net_cash_day:.2f}",
                "Accumulative cash": f"{acc_cash:.2f}", "Accumulative card": f"{acc_card:.2f}",
                "Total Money": f"{total_money_accumulative:.2f}", "closure_time": closure_ts, "closed_by": closed_by
            }
            # snapshot attachment
            snapshot_path = None
            try:
                updated_df = client.read_month_sheet(sheet_id, sheet_name)
                for idx2, v in updated_df[date_col_local].items():
                    if _parse_date_cell(v) == chosen_day:
                        snapshot_df = updated_df.loc[[idx2]]
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
            logger.exception("Daily summary send failed"); st.error("Failed to send daily summary email. See logs.")

# footer - local service-account JSON path
st.markdown("---")
st.markdown("Service-account JSON (local): `/mnt/data/b19a61d2-13c7-49f2-a19f-f20665f57d6e.json`")
