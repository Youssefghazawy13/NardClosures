import streamlit as st
# ---- DEBUG SNIPPET: safe checks (temporary) ----
secret_keys = ["SERVICE_ACCOUNT_JSON","ZAMALEK_SHEET_ID","ALEXANDRIA_SHEET_ID","SMTP_USER","SMTP_PASSWORD","SUPERPAY_PERCENT"]
present = {k: (k in st.secrets and bool(st.secrets[k])) for k in secret_keys}
st.sidebar.markdown("**Debug: secrets present?**")
for k,v in present.items():
    st.sidebar.write(f"{k}: {v}")
# try safe extract client_email without printing private key
try:
    raw = st.secrets.get("SERVICE_ACCOUNT_JSON","")
    import re
    m = re.search(r'"client_email"\s*:\s*"([^"]+)"', raw)
    st.sidebar.write("client_email OK:" , bool(m))
    if m: st.sidebar.write("client_email (masked):", m.group(1))
except Exception:
    st.sidebar.write("client_email: error")
# -------------------------------------------------
from dotenv import load_dotenv
load_dotenv()

import os, datetime, pandas as pd
from src.sheets_client import SheetsClient
from src.calc import recalc_forward
from src.email_report import send_daily_submission_report

st.set_page_config(page_title="Register Closures", layout="wide")

# --- Secrets handling ---
# Prefer st.secrets (Streamlit Cloud). Fall back to environment variables for local dev.
st_secrets = None
try:
    st_secrets = st.secrets
except Exception:
    st_secrets = None

def get_secret(key, default=None):
    if st_secrets and key in st_secrets:
        return st_secrets.get(key)
    return os.getenv(key, default)

ZAM_ID = get_secret("ZAMALEK_SHEET_ID")
ALX_ID = get_secret("ALEXANDRIA_SHEET_ID")
SP_PCT = float(get_secret("SUPERPAY_PERCENT", 0.014))

if not ZAM_ID or not ALX_ID:
    st.error("Missing sheet IDs. Set ZAMALEK_SHEET_ID and ALEXANDRIA_SHEET_ID in Streamlit secrets or environment.")
    st.stop()

client = SheetsClient(st_secrets)

# --- Authentication (simple role selector for MVP) ---
st.sidebar.header("User & Branch")
user = st.sidebar.selectbox("User role", [
    "Operations Manager",
    "Operations Team Member",
    "Alexandria Store Manager",
    "Zamalek Store Manager"
])
cashier = st.sidebar.text_input("Cashier / Data entered by (optional)", value="")

branch = st.sidebar.selectbox("Branch", ["Zamalek", "Alexandria"])
sheet_id = ZAM_ID if branch == "Zamalek" else ALX_ID

st.title("Register Closures — Prototype")
st.markdown(f"Branch: **{branch}**  |  Role: **{user}**  |  SuperPay%: **{SP_PCT*100:.2f}%**")

# load available month sheets (skip Settings & ChangeLog)
try:
    sh = client.gc.open_by_key(sheet_id)
    worksheet_list = [ws.title for ws in sh.worksheets() if ws.title not in ("Settings","ChangeLog")]
except Exception as e:
    st.error(f"Failed to open sheet: {e}")
    st.stop()

month = st.selectbox("Month sheet", worksheet_list)

# read whole month sheet as dataframe
df = client.read_month_sheet(sheet_id, month)
if df.empty:
    st.info("Month sheet is empty. Check template.")
    st.stop()

# show compact overview
st.subheader("Month overview")
overview_cols = ["Date","Total Sales","Cash","Card amount","Accumulative cash","Accumulative card","Total Money"]
for c in overview_cols:
    if c not in df.columns:
        df[c] = 0
st.dataframe(df[overview_cols].fillna(0).astype(object).head(31), height=300)

# pick date
date_list = df["Date"].astype(str).tolist()
selected_date = st.selectbox("Select date to edit", date_list)
row_idx = int(df.index[df["Date"].astype(str) == selected_date][0])
row = df.loc[row_idx].copy()

st.subheader(f"Editing {selected_date}")

# manual fields (as defined)
manual_fields = [
    "No.Invoices","No. Products","System amount Cash","System amount Card",
    "entered cash amount","Card amount","Cash outs","Employee advances","Transportation Goods",
    "Transportation Allowance","Cleaning","Internet","Cleaning supplies","Bills",
    "Others","Others Comment","Notes","system cashouts","Cashouts","Petty cash","SuperPay sent"
]

with st.form("edit_form", clear_on_submit=False):
    edited = {}
    cols = st.columns(2)
    for i, field in enumerate(manual_fields):
        col = cols[i%2]
        val = row.get(field, "")
        if field in ["Others Comment","Notes"]:
            edited[field] = col.text_area(field, value=str(val), key=field)
        else:
            try:
                numval = float(val or 0)
            except Exception:
                numval = 0.0
            edited[field] = col.number_input(field, value=numval, step=1.0, key=field)
    save = st.form_submit_button("Save changes")

if save:
    # prepare old values for changelog
    old_values = {c: row.get(c) for c in manual_fields}
    # write manual fields into df
    for c, v in edited.items():
        df.at[row_idx, c] = v
    # recalc forward
    df_updated = recalc_forward(df, start_idx=row_idx, superpay_pct=SP_PCT)
    # write back
    client.write_month_sheet(sheet_id, month, df_updated)
    # append changelog
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed_fields = [c for c in manual_fields if float(old_values.get(c) or 0) != float(edited.get(c) or 0)]
    row_for_log = [
        timestamp, f"{user}" + (f" | {cashier}" if cashier else ""), branch, month, selected_date,
        ",".join(changed_fields),
        str(old_values),
        str({c:edited[c] for c in changed_fields}),
        ""
    ]
    client.append_changelog(sheet_id, "ChangeLog", row_for_log)
    st.success("Saved and recalculated forward. Sheet updated.")

    # send email report for this date across both branches
    try:
        recipients = [x.strip() for x in (get_secret("REPORT_RECIPIENTS","").split(",")) if x.strip()]
        selected_date_str = pd.to_datetime(selected_date).strftime("%Y-%m-%d")
        send_daily_submission_report(
            date_str=selected_date_str,
            recipients=recipients,
            st_secrets=st_secrets,
            zamalek_sheet_id=ZAM_ID,
            alex_sheet_id=ALX_ID,
            smtp_config=None
        )
        st.info("Email report sent.")
    except Exception as e:
        st.warning(f"Email failed: {e}")
