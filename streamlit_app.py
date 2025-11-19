# --- Branch / Sheet / Day selector + edit row UI (drop-in) ---
st.markdown("## Choose branch, sheet and day")

# 1) Branch select
branch = st.selectbox("Branch", ["Zamalek", "Alexandria"], index=0)

# map branch -> sheet id (from secrets)
SHEET_ID_MAP = {
    "Zamalek": st.secrets.get("ZAMALEK_SHEET_ID"),
    "Alexandria": st.secrets.get("ALEXANDRIA_SHEET_ID"),
}
sheet_id = SHEET_ID_MAP.get(branch)
if not sheet_id:
    st.error("No sheet ID configured for this branch. Set it in Streamlit secrets.")
    st.stop()

# 2) List worksheets (tabs) for that Google Sheet and let user pick one
try:
    # try to call a helper that lists worksheet/tab names
    sheet_tabs = client.list_worksheets(sheet_id)  # expected: List[str]
except AttributeError:
    # fallback: implement naive listing via reading an index tab or using client internals
    # If SheetsClient lacks list_worksheets, try reading a known tab "Index" or the template list
    try:
        # try reading "Index" tab that you maintain with sheet names
        idx_df = client.read_month_sheet(sheet_id, "Index")
        sheet_tabs = list(idx_df["sheet_name"].astype(str).dropna().unique())
    except Exception:
        sheet_tabs = []  # will be handled below

if not sheet_tabs:
    st.warning("No sheet tabs detected for this branch. You can type the sheet/tab name manually.")
    sheet_name = st.text_input("Sheet / Tab name (e.g. 12/2025):", value=month_sheet_name_for_date(datetime.date.today()))
else:
    sheet_name = st.selectbox("Choose sheet (month tab)", sheet_tabs)

st.write(f"Selected sheet: **{sheet_name}**")

# 3) Read the sheet and extract Date column values (days)
df_sheet = None
try:
    df_sheet = client.read_month_sheet(sheet_id, sheet_name)
except Exception:
    st.error("Failed reading selected sheet. Maybe tab doesn't exist — the app can create it on save.")
    df_sheet = None

# if df_sheet exists, find the Date column and list days
date_col = None
if df_sheet is not None:
    for c in df_sheet.columns:
        if c.lower().strip().startswith("date"):
            date_col = c
            break

if df_sheet is None or date_col is None:
    st.info("No Date column detected in sheet. When you create/save a row the app will add the Date column.")
    # allow user to enter day manually
    chosen_day = st.date_input("Choose day", value=datetime.date.today())
else:
    # parse dates and show dropdown of available days (formatted)
    def _parse_date_cell(v):
        if pd.isna(v) or v == "":
            return None
        try:
            return pd.to_datetime(v).date()
        except Exception:
            return None
    days = []
    idx_map = {}
    for idx, v in df_sheet[date_col].items():
        d = _parse_date_cell(v)
        if d:
            label = d.isoformat()
            days.append(label)
            idx_map[label] = idx
    if not days:
        st.info("No date rows found yet in this sheet. You can create the row by entering data and saving.")
        chosen_day = st.date_input("Choose day to create", value=datetime.date.today())
        chosen_day_label = chosen_day.isoformat()
        row_idx = None
    else:
        chosen_day_label = st.selectbox("Choose day (existing rows)", days)
        row_idx = idx_map.get(chosen_day_label)
        chosen_day = datetime.date.fromisoformat(chosen_day_label)

# 4) Show the row (if exists) and allow editing — build form
st.markdown("### Day row (edit)")

# determine columns to show/edit (you can customize this list)
edit_columns = [
    "No.Invoices","No. Products","System amount Cash","System amount Card","Total System Sales",
    "entered cash amount","Card amount","Cash outs","Cleaning","Internet","Cleaning supplies","Bills","Others"
]

# build current_values dict (if row exists)
current_values = {col: "" for col in edit_columns}
if df_sheet is not None and date_col and row_idx is not None:
    for col in edit_columns:
        if col in df_sheet.columns:
            current_values[col] = df_sheet.at[row_idx, col] if pd.notna(df_sheet.at[row_idx, col]) else ""

# render inputs in a form for nicer UX
with st.form("edit_day_form", clear_on_submit=False):
    inputs = {}
    col_pairs = list(edit_columns)
    for col in col_pairs:
        inputs[col] = st.text_input(col, value=str(current_values.get(col,"")), key=f"frm_{sheet_id}_{sheet_name}_{col}")
    submitted = st.form_submit_button("Save to sheet")

    if submitted:
        # read latest sheet again to avoid clobber race
        try:
            df = client.read_month_sheet(sheet_id, sheet_name)
        except Exception:
            # create template if missing
            template_cols = ["Date"] + edit_columns + ["Closed By","Closure Time"]
            df = pd.DataFrame(columns=template_cols)

        # locate date col & row idx again (or append row)
        date_col_local = None
        for c in df.columns:
            if c.lower().strip().startswith("date"):
                date_col_local = c
                break
        if date_col_local is None:
            date_col_local = "Date"
            if date_col_local not in df.columns:
                df.insert(0, date_col_local, "")

        # find row
        row_idx_local = None
        for idx, v in df[date_col_local].items():
            parsed = _parse_date_cell(v)
            if parsed == chosen_day:
                row_idx_local = idx
                break
        if row_idx_local is None:
            # append new row
            new_row = {c: "" for c in df.columns}
            new_row[date_col_local] = chosen_day.isoformat()
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            row_idx_local = len(df) - 1

        # ensure edit columns exist
        for col in edit_columns:
            if col not in df.columns:
                df[col] = ""

        # compute changed fields and previous values
        changed = {}
        prev_vals = {}
        for col in edit_columns:
            prev = df.at[row_idx_local, col] if col in df.columns else ""
            new = inputs.get(col, "")
            # numeric-safe comparison when possible
            try:
                if float(str(prev or 0)) != float(str(new or 0)):
                    changed[col] = new
                    prev_vals[col] = prev
            except Exception:
                if str(prev).strip() != str(new).strip():
                    changed[col] = new
                    prev_vals[col] = prev

        # write changed values
        for col, val in changed.items():
            df.at[row_idx_local, col] = val

        # set closed_by and closure_time using role / now (as requested)
        df = df.copy()  # safe
        closed_by_col = _find_column(df, ["Closed By", "closed_by", "ClosedBy"]) or "Closed By"
        if closed_by_col not in df.columns:
            df[closed_by_col] = ""
        df.at[row_idx_local, closed_by_col] = str(role)

        closure_col = _find_column(df, ["Closed At", "Closure Time", "closed_at", "closed_time"]) or "Closure Time"
        if closure_col not in df.columns:
            df[closure_col] = ""
        df.at[row_idx_local, closure_col] = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')

        # write back to sheet
        try:
            client.write_month_sheet(sheet_id=sheet_id, sheet_name=sheet_name, df=df)
        except Exception as e:
            st.error("Failed to write to sheet. Check permissions and sheet id.")
            st.write(repr(e))
        else:
            # append changelog to the same sheet (ChangeLog tab)
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
                client.append_changelog(sheet_id, changelog_row)
            except Exception:
                # fallback: write to ChangeLog tab
                try:
                    ch_df = client.read_month_sheet(sheet_id, "ChangeLog")
                except Exception:
                    ch_df = pd.DataFrame(columns=list(changelog_row.keys()))
                ch_df = pd.concat([ch_df, pd.DataFrame([changelog_row])], ignore_index=True)
                client.write_month_sheet(sheet_id=sheet_id, sheet_name="ChangeLog", df=ch_df)

            st.success(f"Saved row for {chosen_day.isoformat()}. Changed: {', '.join(changed.keys())}")

            # optional: offer to download snapshot of updated row
            try:
                updated_df = client.read_month_sheet(sheet_id, sheet_name)
                # find the updated row again and provide CSV download
                for idx, v in updated_df[date_col].items():
                    if _parse_date_cell(v) == chosen_day:
                        snapshot = updated_df.loc[[idx]]
                        csv_bytes = snapshot.to_csv(index=False).encode("utf-8")
                        st.download_button(label="Download row CSV", data=csv_bytes, file_name=f"closure_{branch}_{chosen_day.isoformat()}.csv", mime="text/csv")
                        break
            except Exception:
                pass
