# src/email_report.py
"""
Robust email helper with optional attachments for Register Closures app.

Function:
    send_daily_submission_report(report: dict, recipients: Union[str, Iterable[str]], attachments: Optional[List[str]] = None) -> bool

Reads SMTP config from Streamlit secrets if available, otherwise from environment variables.
"""

from __future__ import annotations

import os
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Iterable, List, Optional, Union

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def _get_smtp_config() -> dict:
    """
    Load SMTP configuration from Streamlit secrets or environment variables.
    Returns dict: {'server': str, 'port': int, 'user': str, 'password': str}
    Raises ValueError if required values are missing.
    """
    server = None
    port = None
    user = None
    password = None

    try:
        import streamlit as st  # type: ignore
        secrets = getattr(st, "secrets", None)
        if secrets:
            server = secrets.get("SMTP_SERVER") or server
            port = secrets.get("SMTP_PORT") or port
            user = secrets.get("SMTP_USER") or user
            password = secrets.get("SMTP_PASSWORD") or password
    except Exception:
        pass

    server = server or os.environ.get("SMTP_SERVER")
    port = port or os.environ.get("SMTP_PORT")
    user = user or os.environ.get("SMTP_USER")
    password = password or os.environ.get("SMTP_PASSWORD")

    if port is None:
        port = 587
    try:
        port = int(port)
    except Exception:
        raise ValueError(f"Invalid SMTP_PORT: {port!r}")

    if not server or not user or not password:
        raise ValueError("SMTP configuration incomplete. Set SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD in secrets or env.")

    return {"server": server, "port": port, "user": user, "password": password}


def _normalize_recipients(recipients: Union[str, Iterable[str], None]) -> List[str]:
    if recipients is None:
        return []
    if isinstance(recipients, str):
        parts = [p.strip() for p in recipients.split(",")]
        return [p for p in parts if p]
    try:
        return [str(p).strip() for p in recipients if str(p).strip()]
    except Exception:
        return []


def _build_message(subject: str, sender: str, recipients: List[str], report: dict, attachments: Optional[List[str]] = None) -> EmailMessage:
    """
    Build an EmailMessage with plain-text and HTML parts and attach files if provided.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    # Plain text body
    plain_lines = [f"{subject}", "", "Report details:"]
    for k, v in report.items():
        plain_lines.append(f"{k}: {v}")
    plain_body = "\n".join(plain_lines)
    msg.set_content(plain_body)

    # HTML body: build a simple table
    html_rows = []
    for k, v in report.items():
        k_ = str(k)
        v_ = str(v)
        html_rows.append(f"<tr><td style='padding:6px;border:1px solid #ddd'><strong>{k_}</strong></td><td style='padding:6px;border:1px solid #ddd'>{v_}</td></tr>")
    html_body = f"""
    <html>
      <body>
        <h2>Daily Closure Report</h2>
        <p>Branch: <strong>{report.get('branch','')}</strong> — Date: <strong>{report.get('date','')}</strong></p>
        <table style="border-collapse:collapse">{''.join(html_rows)}</table>
        <p style="color:#666;font-size:12px">This is an automated message from the Register Closures app.</p>
      </body>
    </html>
    """
    msg.add_alternative(html_body, subtype="html")

    # Attach files if any
    if attachments:
        for path in attachments:
            try:
                if not path or not os.path.isfile(path):
                    logger.warning("Attachment path missing or not a file: %s", path)
                    continue
                with open(path, "rb") as f:
                    data = f.read()
                maintype = "application"
                subtype = "octet-stream"
                filename = os.path.basename(path)
                msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
            except Exception:
                logger.exception("Failed to attach file: %s", path)

    return msg


def send_daily_submission_report(report: dict, recipients: Union[str, Iterable[str]], attachments: Optional[List[str]] = None) -> bool:
    """
    Send the daily submission report to recipients.

    Args:
        report: dict with report fields
        recipients: list or comma-separated string of emails
        attachments: optional list of file paths to attach

    Returns:
        True on success, False otherwise (exceptions logged).
    """
    recipients_list = _normalize_recipients(recipients)
    if not recipients_list:
        logger.error("No recipients provided to send_daily_submission_report")
        raise ValueError("No recipients provided")

    try:
        cfg = _get_smtp_config()
    except Exception:
        logger.exception("SMTP configuration error")
        raise

    subject = f"Daily Closure Report — {report.get('branch','')} — {report.get('date','')}"
    sender = cfg["user"]

    try:
        msg = _build_message(subject, sender, recipients_list, report, attachments=attachments)
    except Exception:
        logger.exception("Failed to build email message")
        raise

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["server"], cfg["port"], timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        logger.info("Daily submission report sent to %s", recipients_list)
        return True
    except Exception:
        logger.exception("Failed to send daily submission report")
        return False


# Optional direct test when executed as script
if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    sample = {"date": "2025-12-01", "branch": "Zamalek", "No.Invoices": 1, "No. Products": 2, "Total System Sales": "1200"}
    recips = os.environ.get("REPORT_RECIPIENTS", os.environ.get("SMTP_USER", ""))
    send_daily_submission_report(sample, recips)
