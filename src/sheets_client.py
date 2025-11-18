# src/sheets_client.py
import os
import json
import re
import logging
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import pandas as pd

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _mask_private_key_in_text(s: str) -> str:
    """Mask the private_key value inside a JSON-like string so logs don't expose it."""
    return re.sub(r'("private_key"\s*:\s*")(.+?)(")', r'\1[PRIVATE_KEY_REDACTED]\3', s, flags=re.DOTALL)

def _get_gspread_client_from_secrets(st_secrets=None):
    """
    Create and return a gspread client.
    Supports:
      - st.secrets["SERVICE_ACCOUNT_JSON"] (Streamlit Cloud)
      - local file path via env var GOOGLE_SERVICE_ACCOUNT_FILE
    """
    # 1) Try Streamlit secrets
    if st_secrets and "SERVICE_ACCOUNT_JSON" in st_secrets:
        raw = st_secrets["SERVICE_ACCOUNT_JSON"]
        try:
            sa_info = json.loads(raw)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_info, SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            masked = _mask_private_key_in_text(raw)
            logger.error("SERVICE_ACCOUNT_JSON parse failed. Masked preview:\n%s", masked[:1000])
            raise RuntimeError("SERVICE_ACCOUNT_JSON is invalid JSON. Re-paste the JSON into Streamlit secrets.") from e

    # 2) Try local file path from env var
    sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_path and os.path.exists(sa_path):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(sa_path, SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logger.exception("Failed to load service account from file: %s", sa_path)
            raise

    # 3) Nothing found
    raise RuntimeError("No Google service account credentials found. Provide SERVICE_ACCOUNT_JSON in st.secrets or set GOOGLE_SERVICE_ACCOUNT_FILE env var.")

class SheetsClient:
    """Simple wrapper around gspread for reading/writing month sheets and appending changelog."""

    def __init__(self, st_secrets=None):
        self.gc = _get_gspread_client_from_secrets(st_secrets)

    def read_month_sheet(self, sheet_id: str, sheet_name: str) -> pd.DataFrame:
        """Read entire sheet (month) to DataFrame, preserving header row and coercing numeric columns."""
        sh = self.gc.open_by_key(sheet_id)
        ws = sh.worksheet(sheet_name)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        df = pd.DataFrame(values[1:], columns=values[0])
        # coerce numeric cols except text fields
        for c in df.columns:
            if c not in ("Date", "Others Comment", "Notes", "Others"):
                # replace blank strings with 0 before conversion
                df[c] = pd.to_numeric(df[c].replace('', '0'), errors="coerce").fillna(0)
        # try convert Date column to date objects
        try:
            df["Date"] = pd.to_datetime(df["Date"]).dt.date
        except Exception:
            pass
        return df

    def write_month_sheet(self, sheet_id: str, sheet_name: str, df: pd.DataFrame):
        """Overwrite the sheet with the given dataframe (header + rows)."""
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(sheet_name)
        except Exception:
            ws = sh.add_worksheet(title=sheet_name, rows=str(len(df) + 10), cols=str(len(df.columns) + 5))
        values = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
        ws.clear()
        ws.update(values)

    def append_changelog(self, sheet_id: str, changelog_sheet: str = "ChangeLog", row: list | None = None):
        """Append a row (list) into the ChangeLog sheet."""
        if row is None:
            return
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(changelog_sheet)
        except Exception:
            ws = sh.add_worksheet(title=changelog_sheet, rows="100", cols="20")
        ws.append_row(row, value_input_option="USER_ENTERED")
