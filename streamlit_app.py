# streamlit_app.py (final — mobile-first, months filtered Dec_2025..Dec_2026, branch->tab->day flow)
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

def month_sheet_name_for_date_monthname(d: datetime.date) -> str:
    """Format for tabs: 'December_2025'"""
    return d.strftime("%B_%Y")

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

# Top controls
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

# ----------------------- Allowed months list (Dec_2025 .. Dec_2026) ------------
st.markdown("### Select month tab (Dec_2025 → Dec_2026) and day")

# build allowed month names range
start = datetime.date(2025, 12, 1)
end   = datetime.date(2026, 12, 1)
months = []
cur = start
while cur <= end:
    months.append(cur.strftime("%B_%Y"))
    # advance one month
    year = cur.year + (cur.month // 12)
    month = cur.month % 12 + 1
    cur = datetime.date(year, month, 1)

# fetch actual tabs in spreadsheet for the branch
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

# intersect allowed months with real tabs (preserve months order)
available_month_tabs = [m for m in months if m in real_tabs]

if not available_month_tabs:
    st.warning("لا يوجد شيت شهرى متاح ضمن النطاق (Dec_2025–Dec_2026) فى هذا الفرع. تأكد من وجود التابس المطلوبة في جوجل شيت وصلاحيات الـ service account.")
    sheet_name = st.text_input("أدخل اسم الشيت يدويًا (مثال: December_2025):", value=months[0])
else:
    sheet_name = st.selectbox("اختر الشيت الشهري", available_month_tabs)

if not sheet_name:
    st.info("اختر شيتًا للشهر لاستكمال.")
    st.stop()

st.write(f"Selected tab: **{sheet_name}**")

# ----------------------- read chosen tab and list dates --------------------
try:
    df_month = client.read_month_sheet(sheet_id, sheet_name)
except Exception as e:
    st.error(f"فشل قراءة التاب '{sheet_name}'. تأكد أن التاب موجود وService Account لديه صلاحية Editor.")
    logger.exception("Failed to read tab %s: %s", sheet_name, e)
    st.stop()

date_col = None
for c in df_month.columns:
    if c.lower().strip().startswith("date"):
        date_col = c
        break

if date_col is None:
    st.info("التاب لا يحتوي على عمود 'Date'. قم بإضافة عمود Date بصيغة yyyy-mm-dd داخل الشيت.")
    chosen_date = st.date_input("اختر تاريخًا لإنشائه/تعديله", value=datetime.date.today())
    row_idx = None
else:
    days_map = {}
    for idx, v in df_month[date_col].items():
        d = _parse_date_cell(v)
        if d:
            days_map[d.isoformat()] = idx

    if not days_map:
        st.info("لا توجد صفوف تواريخ داخل هذا التاب بعد. يمكنك إنشاء الصف عبر نموذج الحفظ أدناه.")
        chosen_date = st.date_input("اختر تاريخًا لإنشائه", value=datetime.date.today())
        row_idx = None
    else:
        sorted_days = sorted(days_map.keys())
        selected_label = st.selectbox("اختر يومًا من الشيت", ["(choose)"] + sorted_days)
        if selected_label == "(choose)":
            st.info("اختر يومًا لتحميل صفه من الشيت.")
            row_idx = None
            chosen_date = datetime.date.today()
        else:
            chosen_date = datetime.date.fromisoformat(selected_label)
            row_idx = days_map[selected_label]
            st.success(f"تم تحميل صف تاريخ: {chosen_date.isoformat()} (row index: {row_idx})")

# ----------------------- Day edit form ------------------------------------
st.markdown("### Day row (edit or create)")
edit_columns = [
    "No.Invoices","No. Products","System amount Cash","System amount Card","Total System Sales",
    "entered cash amount","Card amount","Cash outs","Cleaning","Internet","Cleaning supplies","Bills","Others",
]

current_values = {col: "" for col in edit_columns}
if df_month is not None and date_col and row_idx is not None:
    for col in edit_columns:
        if col in df_month.columns:
            current_values[col] = df_month.at[row_idx, col] if pd.notna(df_month.at[row_idx, col]) else ""

with st.form("day_edit_form", clear_on_submit=False):
    inputs = {}
    for col in edit_columns:
        inputs[col] = st.text_input(col, value=str(current_values.get(col, "")), key=f"frm_{sheet_id}_{sheet_name}_{col}")
    save_clicked = st.form_submit_button("Save row to sheet")

    if save_clicked:
        try:
            df = client.read_month_sheet(sheet_id, sheet_name)
        except Exception:
            template_cols = ["Date"] + edit_columns + ["Closed By", "Closure Time"]
            df = pd.DataFrame(columns=template_cols)

        # find/date column
        date_col_local = None
        for c in df.columns:
            if c.lower().strip().startswith("date"):
                date_col_local = c
                break
        if date_col_local is None:
            date_col_local = "Date"
            if date_col_local not in df.columns:
                df.insert(0, date_col_local, "")

        # find row or append
        row_idx_local = None
        for idx, v in df[date_col_local].items():
            if _parse_date_cell(v) == chosen_date:
                row_idx_local = idx
                break
        if row_idx_local is None:
            new_row = {c: "" for c in df.columns}
            new_row[date_col_local] = chosen_date.isoformat()
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            row_idx_local = len(df) - 1

        # ensure columns
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

        if not changed:
            st.info("No changes detected. Nothing to save.")
        else:
            for col, val in changed.items():
                df.at[row_idx_local, col] = val

            closed_by_col = _find_column(df, ["Closed By", "closed_by", "ClosedBy"]) or "Closed By"
            if closed_by_col not in df.columns:
                df[closed_by_col] = ""
            df.at[row_idx_local, closed_by_col] = str(role)

            closure_col = _find_column(df, ["Closed At", "Closure Time", "closed_at", "closed_time"]) or "Closure Time"
            if closure_col not in df.columns:
                df[closure_col] = ""
            if not df.at[row_idx_local, closure_col]:
                df.at[row_idx_local, closure_col] = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')

            try:
                client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)
            except Exception:
                st.error("Failed to write to Google Sheets. Check permissions and sheet ID.")
                logger.exception("Failed to write month sheet")
            else:
                changelog_row = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "user": role,
                    "branch": branch,
                    "sheet": sheet_name,
                    "date": chosen_date.isoformat(),
                    "changed_fields": ", ".join(changed.keys()),
                    "prev_values": json.dumps(prev_vals, default=str),
                    "new_values": json.dumps(changed, default=str)
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

                st.success(f"Saved row for {chosen_date.isoformat()} — changed: {', '.join(changed.keys())}")

                try:
                    updated_df = client.read_month_sheet(sheet_id, sheet_name)
                    for idx, v in updated_df[date_col_local].items():
                        if _parse_date_cell(v) == chosen_date:
                            snapshot_df = updated_df.loc[[idx]]
                            csv_bytes = snapshot_df.to_csv(index=False).encode("utf-8")
                            st.download_button(label="Download updated row (CSV)", data=csv_bytes, file_name=f"closure_{branch}_{chosen_date.isoformat()}.csv", mime="text/csv")
                            break
                except Exception:
                    logger.exception("Failed to prepare snapshot download")

# ----------------------- Send today's summary email (detailed) -------------
st.markdown("### Reporting")
if st.button("Send today's summary email (test)"):
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

    snapshot_path: Optional[str] = None
    try:
        df_month2 = client.read_month_sheet(sheet_id, sheet_name)
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
            closure_col2 = _find_column(df_month2, ["Closed At", "Closure Time", "closed_at", "closed_time"])
            if closure_col2:
                report["closure_time"] = str(_get_cell(closure_col2))
            else:
                report["closure_time"] = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')
            try:
                snapshot_df = df_month2.loc[[row_idx2]]
                snapshot_path = f"/tmp/closure_{branch}_{selected_date.isoformat()}.csv"
                snapshot_df.to_csv(snapshot_path, index=False)
            except Exception:
                snapshot_path = None
    except Exception:
        logger.exception("Failed reading month sheet for email report")

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

# Footer: local service-account JSON path (for your reference)
st.markdown("---")
st.markdown("Service-account JSON (local): `sandbox:/mnt/data/b19a61d2-13c7-49f2-a19f-f20665f57d6e.json`")
