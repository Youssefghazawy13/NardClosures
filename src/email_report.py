# src/email_report.py
# Simple SMTP email sender used by the app.
import os
import smtplib
import ssl
import logging
from typing import List, Dict, Optional
from email.message import EmailMessage

logger = logging.getLogger(__name__)

def _build_plain_text_report(report: Dict[str, str]) -> str:
    lines = []
    lines.append(f"Branch: {report.get('branch','')}")
    lines.append(f"Date: {report.get('date','')}")
    lines.append("")
    keys = [
        "No.Invoices","No. Products","System amount Cash","System amount Card","Total System Sales",
        "entered cash amount","entered Card amount","Total Sales",
        "Cash outs","Petty cash","Cash Deficit","Card Deficit","SuperPay expected","Net cash",
        "Accumulative cash","Accumulative card","Total Money","closure_time","closed_by"
    ]
    for k in keys:
        if k in report:
            lines.append(f"{k}: {report.get(k)}")
    if "extra" in report:
        lines.append("")
        lines.append(str(report["extra"]))
    return "\n".join(lines)

def send_daily_submission_report(report: Dict[str, str], recipients: List[str], subject_prefix: str = "Daily closure", attachments: Optional[List[str]] = None) -> bool:
    """
    report: dict of fields -> values (strings)
    recipients: list of email addresses
    attachments: optional list of file paths to attach
    Returns True on success.
    """
    try:
        smtp_server = os.environ.get("SMTP_SERVER") or (st_secrets_get("SMTP_SERVER"))
        smtp_port = int(os.environ.get("SMTP_PORT") or (st_secrets_get("SMTP_PORT") or 587))
        smtp_user = os.environ.get("SMTP_USER") or (st_secrets_get("SMTP_USER"))
        smtp_password = os.environ.get("SMTP_PASSWORD") or (st_secrets_get("SMTP_PASSWORD"))
    except Exception:
        logger.exception("SMTP config not found")
        return False

    if not smtp_user or not smtp_password:
        logger.error("SMTP user/password missing")
        return False

    subject = f"{subject_prefix} — {report.get('branch','')} — {report.get('date','')}"
    body = _build_plain_text_report(report)

    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    # attach files if provided
    if attachments:
        import mimetypes
        for path in attachments:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                ctype, encoding = mimetypes.guess_type(path)
                if ctype is None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(path))
            except Exception:
                logger.exception("Failed to attach file %s", path)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info("Email sent to %s", recipients)
        return True
    except Exception:
        logger.exception("Failed to send email")
        return False

# helper for reading secrets in environments where st.secrets isn't available
def st_secrets_get(key: str) -> Optional[str]:
    try:
        import streamlit as _st
        return _st.secrets.get(key)
    except Exception:
        return None
