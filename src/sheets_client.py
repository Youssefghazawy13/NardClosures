import json
import re
import logging
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

logger = logging.getLogger(__name__)

def _mask_private_key_in_text(s: str) -> str:
    """
    Mask the private_key value inside a JSON-like string so logs don't expose it.
    Replaces the private_key value with [PRIVATE_KEY_REDACTED].
    """
    # replace the private_key value between quotes
    masked = re.sub(r'("private_key"\s*:\s*")(.+?)(")', r'\1[PRIVATE_KEY_REDACTED]\3', s, flags=re.DOTALL)
    return masked

def _extract_client_email(s: str) -> str | None:
    """Try to find client_email value in the raw secret string (without parsing JSON)."""
    m = re.search(r'"client_email"\s*:\s*"([^"]+)"', s)
    if m:
        return m.group(1)
    return None

def _try_json_loads(s: str):
    """Try to load JSON and return a dict or raise the exception."""
    return json.loads(s)

def _get_gspread_client_from_secrets(st_secrets=None):
    """
    Robust loader for SERVICE_ACCOUNT_JSON stored in Streamlit secrets.
    Tries several tolerant strategies and logs a masked preview when it fails.
    """
    # 1) Try reading from Streamlit secrets first
    raw = None
    if st_secrets and "SERVICE_ACCOUNT_JSON" in st_secrets:
        raw = st_secrets["SERVICE_ACCOUNT_JSON"]
    else:
        # fallback to file path
        sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        if sa_path and os.path.exists(sa_path):
            # load file content and return client
            creds = ServiceAccountCredentials.from_json_keyfile_name(sa_path, SCOPES)
            return gspread.authorize(creds)
        raise RuntimeError("No Google service account credentials found. Provide Streamlit secret SERVICE_ACCOUNT_JSON or set GOOGLE_SERVICE_ACCOUNT_FILE.")

    # attempt direct json.loads
    try:
        sa_info = _try_json_loads(raw)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_info, SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        # prepare a helpful, SAFE masked preview
        masked = _mask_private_key_in_text(raw)
        client_email = _extract_client_email(raw)
        preview = masked[:1000]  # limit length for logs
        logger.error("Failed to json.loads SERVICE_ACCOUNT_JSON. Masked preview (private_key redacted):\n%s", preview)
        if client_email:
            logger.error("Detected client_email in secret: %s", client_email)
        # Give a clear suggestion
        logger.error("SERVICE_ACCOUNT_JSON appears malformed. Common fixes:\n"
                     " - Ensure you pasted the full JSON exactly (starts with '{' and ends with '}')\n"
                     " - In Streamlit secrets, wrap the JSON in triple quotes: SERVICE_ACCOUNT_JSON = \"\"\"<paste JSON here>\"\"\"\n"
                     " - Do NOT add extra surrounding quotes or remove braces\n"
                     " - Ensure the private_key section is intact (it may contain '\\n' sequences)\n"
                     "If you prefer, re-download the JSON from Google Cloud and paste again.")
        # raise the original error so the app fails clearly (but logs now contain masked preview)
        raise RuntimeError("SERVICE_ACCOUNT_JSON is invalid JSON. See logs for a masked preview.") from e
