# src/sheets_client.py
"""
Simple SheetsClient wrapper around gspread using a service account.
Provides:
- list_worksheets(sheet_id) -> List[str]
- read_month_sheet(sheet_id, sheet_name) -> pandas.DataFrame
- write_month_sheet(sheet_id, sheet_name, df) -> None
- append_changelog(sheet_id, row_dict) -> None

This implementation expects a Streamlit secrets dict passed on init containing
the key "SERVICE_ACCOUNT_JSON" (single-line JSON or multi-line).
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional

import pandas as pd

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def _get_gspread_client_from_secrets(st_secrets: dict):
    """
    Accepts:
      st_secrets["SERVICE_ACCOUNT_JSON"] -> full JSON text OR
      env var GOOGLE_SERVICE_ACCOUNT_FILE pointing to local path of json
    Returns gspread client.
    """
    raw = None
    if st_secrets and "SERVICE_ACCOUNT_JSON" in st_secrets:
        raw = st_secrets["SERVICE_ACCOUNT_JSON"]
    raw_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not raw and raw_env:
        # treat as path
        with open(raw_env, "r") as f:
            sa_info = json.load(f)
    else:
        if not raw:
            raise RuntimeError("No SERVICE_ACCOUNT_JSON found in secrets or GOOGLE_SERVICE_ACCOUNT_FILE env var")
        # if the secret is already masked or single-line, attempt to load
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                sa_info = json.loads(raw)
            except Exception as e:
                # try to replace literal \n to newlines if someone inserted escaped newlines
                try:
                    sa_info = json.loads(raw.replace("\\n", "\n"))
                except Exception:
                    raise RuntimeError("SERVICE_ACCOUNT_JSON is invalid JSON. Re-paste into secrets (use proper JSON or escaped \\n for private_key).") from e
        else:
            raise RuntimeError("SERVICE_ACCOUNT_JSON must be JSON string in secrets")

    creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc

class SheetsClient:
    def __init__(self, st_secrets: Optional[dict] = None):
        self.st_secrets = st_secrets or {}
        self.gc = _get_gspread_client_from_secrets(self.st_secrets)

    def list_worksheets(self, sheet_id: str) -> List[str]:
        """Return list of worksheet/tab names for the spreadsheet."""
        try:
            sh = self.gc.open_by_key(sheet_id)
            return [ws.title for ws in sh.worksheets()]
        except Exception:
            logger.exception("list_worksheets failed for %s", sheet_id)
            return []

    def read_month_sheet(self, sheet_id: str, sheet_name: str) -> pd.DataFrame:
        """Read an entire worksheet/tab into a DataFrame. Raises on failure."""
        sh = self.gc.open_by_key(sheet_id)
        ws = sh.worksheet(sheet_name)  # will raise if not exists
        records = ws.get_all_records()
        if not records:
            # if returns empty, return empty df but keep headers if any
            headers = ws.row_values(1) or []
            return pd.DataFrame(columns=headers)
        return pd.DataFrame(records)

    def write_month_sheet(self, sheet_id: str, sheet_name: str, df: pd.DataFrame):
        """
        Overwrite the worksheet tab with df content.
        If worksheet not exists, raises.
        """
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(sheet_name)
            # clear and update
            ws.clear()
        except gspread.WorksheetNotFound:
            # explicitly do NOT create by default — caller should decide
            raise

        # prepare payload: header row + rows
        header = list(df.columns)
        values = [header]
        # convert all values to string for safe upload
        for _, row in df.iterrows():
            values.append([("" if pd.isna(x) else str(x)) for x in row.tolist()])

        # batch update
        ws.update(values)

    def append_changelog(self, sheet_id: str, row_dict: Dict[str, Any]):
        """
        Append a changelog row to 'ChangeLog' worksheet; create the tab if missing.
        row_dict: mapping column_name -> value
        """
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet("ChangeLog")
        except gspread.WorksheetNotFound:
            # create ChangeLog with a reasonable size
            ws = sh.add_worksheet(title="ChangeLog", rows="1000", cols=str(max(10, len(row_dict))))
            # write header
            headers = list(row_dict.keys())
            ws.append_row(headers)

        # Ensure header matches (append missing headers if necessary)
        existing_headers = ws.row_values(1)
        needed = [k for k in row_dict.keys() if k not in existing_headers]
        if needed:
            # expand header row by rewriting entire header row (keep original order)
            new_headers = existing_headers + needed
            ws.delete_rows(1)
            ws.insert_row(new_headers, index=1)

        # append values in header order
        final_headers = ws.row_values(1)
        row = [row_dict.get(h, "") for h in final_headers]
        ws.append_row(row)
