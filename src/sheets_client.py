# src/sheets_client.py
import os, json, re, logging
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import pandas as pd

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

def _mask_private_key_in_text(s: str) -> str:
    return re.sub(r'("private_key"\s*:\s*")(.+?)(")', r'\1[PRIVATE_KEY_REDACTED]\3', s, flags=re.DOTALL)

def _get_gspread_client_from_secrets(st_secrets=None):
    # 1) Try Streamlit secrets SERVICE_ACCOUNT_JSON
    if st_secrets and "SERVICE_ACCOUNT_JSON" in st_secrets:
        raw = st_secrets["SERVICE_ACCOUNT_JSON"]
        if not raw or not raw.strip():
            logger.error("SERVICE_ACCOUNT_JSON exists but is empty.")
            raise RuntimeError("SERVICE_ACCOUNT_JSON is empty in Streamlit secrets.")
        try:
            sa_info = json.loads(raw)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_info, SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            masked = _mask_private_key_in_text(raw)
            logger.error("SERVICE_ACCOUNT_JSON parse failed. Masked preview:\n%s", masked[:2000])
            # try simple fixes hints
            raise RuntimeError("SERVICE_ACCOUNT_JSON is invalid JSON. Re-paste the JSON into Streamlit secrets (use single-line json or ensure private_key uses \\n).") from e

    # 2) Fallback: env var path GOOGLE_SERVICE_ACCOUNT_FILE
    sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_path:
        if not os.path.exists(sa_path):
            logger.error("GOOGLE_SERVICE_ACCOUNT_FILE is set but file not found: %s", sa_path)
            raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_FILE path not found: {sa_path}")
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(sa_path, SCOPES)
            return gspread.authorize(creds)
        except Exception:
            logger.exception("Failed to create gspread client from file: %s", sa_path)
            raise

    # 3) Nothing found
    raise RuntimeError("No Google service account credentials found. Provide SERVICE_ACCOUNT_JSON in st.secrets or set GOOGLE_SERVICE_ACCOUNT_FILE env var.")

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
            if c not in ("Date", "Others Comment", "Notes", "Others"):
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
            ws = sh.add_worksheet(title=sheet_name, rows=str(len(df) + 10), cols=str(len(df.columns) + 5))
        values = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
        ws.clear()
        ws.update(values)

    def append_changelog(self, sheet_id: str, changelog_sheet: str = "ChangeLog", row: list | None = None):
        if row is None:
            return
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(changelog_sheet)
        except Exception:
            ws = sh.add_worksheet(title=changelog_sheet, rows="100", cols="20")
        ws.append_row(row, value_input_option="USER_ENTERED")
