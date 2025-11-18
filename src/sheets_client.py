
import os, json
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

def _get_gspread_client_from_secrets(st_secrets=None):
    if st_secrets and "SERVICE_ACCOUNT_JSON" in st_secrets:
        sa_info = json.loads(st_secrets["SERVICE_ACCOUNT_JSON"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_info, SCOPES)
        return gspread.authorize(creds)
    sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_path and os.path.exists(sa_path):
        creds = ServiceAccountCredentials.from_json_keyfile_name(sa_path, SCOPES)
        return gspread.authorize(creds)
    raise RuntimeError("No Google service account credentials found. Provide Streamlit secret SERVICE_ACCOUNT_JSON or set GOOGLE_SERVICE_ACCOUNT_FILE env var.")

class SheetsClient:
    def __init__(self, st_secrets=None):
        self.gc = _get_gspread_client_from_secrets(st_secrets)

    def read_month_sheet(self, sheet_id: str, sheet_name: str) -> pd.DataFrame:
        sh = self.gc.open_by_key(sheet_id)
        ws = sh.worksheet(sheet_name)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        df = pd.DataFrame(values[1:], columns=values[0])
        for c in df.columns:
            if c != "Date" and c not in ["Others Comment", "Notes", "Others"]:
                df[c] = pd.to_numeric(df[c].replace('', '0'), errors="coerce").fillna(0)
        try:
            df["Date"] = pd.to_datetime(df["Date"]).dt.date
        except Exception:
            pass
        return df

    def write_month_sheet(self, sheet_id: str, sheet_name: str, df: pd.DataFrame):
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(sheet_name)
        except Exception:
            ws = sh.add_worksheet(title=sheet_name, rows=str(len(df)+10), cols=str(len(df.columns)+5))
        values = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
        ws.clear()
        ws.update(values)

    def append_changelog(self, sheet_id: str, changelog_sheet="ChangeLog", row=None):
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(changelog_sheet)
        except Exception:
            ws = sh.add_worksheet(title=changelog_sheet, rows="100", cols="20")
        if row is None:
            return
        ws.append_row(row, value_input_option="USER_ENTERED")
