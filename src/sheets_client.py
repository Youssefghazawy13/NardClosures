# src/sheets_client.py
"""
SheetsClient: simple wrapper around gspread.
- Expects Streamlit secrets to contain SERVICE_ACCOUNT_JSON (or set GOOGLE_SERVICE_ACCOUNT_FILE env to path)
- list_worksheets(sheet_id)
- read_month_sheet(sheet_id, sheet_name) -> pd.DataFrame
- write_month_sheet(sheet_id, sheet_name, df) -> overwrite worksheet (will raise if worksheet missing)
- append_changelog(sheet_id, row_dict) -> appends to ChangeLog, creates ChangeLog if missing
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
    raw = None
    if st_secrets and "SERVICE_ACCOUNT_JSON" in st_secrets:
        raw = st_secrets["SERVICE_ACCOUNT_JSON"]
    raw_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not raw and raw_env:
        with open(raw_env, "r") as f:
            sa_info = json.load(f)
    else:
        if not raw:
            raise RuntimeError("No SERVICE_ACCOUNT_JSON found in secrets or GOOGLE_SERVICE_ACCOUNT_FILE env var")
        # try parse; accept escaped \n or multiline
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                sa_info = json.loads(raw)
            except Exception:
                try:
                    sa_info = json.loads(raw.replace("\\n", "\n"))
                except Exception as e:
                    masked = raw[:80] + "..." if len(raw) > 80 else raw
                    logger.exception("SERVICE_ACCOUNT_JSON parse failed, masked preview: %s", masked)
                    raise RuntimeError("SERVICE_ACCOUNT_JSON is invalid JSON. Re-paste into secrets (escape \\n in private_key) or use file path.") from e
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
        try:
            sh = self.gc.open_by_key(sheet_id)
            return [ws.title for ws in sh.worksheets()]
        except Exception:
            logger.exception("list_worksheets failed for %s", sheet_id)
            return []

    def read_month_sheet(self, sheet_id: str, sheet_name: str) -> pd.DataFrame:
        sh = self.gc.open_by_key(sheet_id)
        ws = sh.worksheet(sheet_name)  # raises if missing
        records = ws.get_all_records()
        if not records:
            headers = ws.row_values(1) or []
            return pd.DataFrame(columns=headers)
        return pd.DataFrame(records)

    def write_month_sheet(self, sheet_id: str, sheet_name: str, df: pd.DataFrame):
        # Overwrite worksheet contents. Will raise if worksheet missing.
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(sheet_name)
            ws.clear()
        except gspread.WorksheetNotFound:
            # Do NOT create month tabs automatically
            raise
        header = list(df.columns)
        values = [header]
        for _, row in df.iterrows():
            values.append([("" if pd.isna(x) else str(x)) for x in row.tolist()])
        ws.update(values)

    def append_changelog(self, sheet_id: str, row_dict: Dict[str, Any]):
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet("ChangeLog")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="ChangeLog", rows="2000", cols=str(max(10, len(row_dict))))
            headers = list(row_dict.keys())
            ws.append_row(headers)
        existing_headers = ws.row_values(1)
        needed = [k for k in row_dict.keys() if k not in existing_headers]
        if needed:
            new_headers = existing_headers + needed
            # rewrite header row safely
            ws.delete_rows(1)
            ws.insert_row(new_headers, index=1)
        final_headers = ws.row_values(1)
        row = [row_dict.get(h, "") for h in final_headers]
        ws.append_row(row)
