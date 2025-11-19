# src/email_report.py
"""
Email sending helper for the Register Closures app.

Provides:
    send_daily_submission_report(report: dict, recipients: Union[str, List[str]]) -> bool

Reads SMTP configuration from one of:
 - streamlit secrets (preferred): st.secrets["SMTP_SERVER"], ["SMTP_PORT"], ["SMTP_USER"], ["SMTP_PASSWORD"]
 - environment variables fallback: SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

Example `report` dict:
{
    "date": "2025-12-01",
    "branch": "Zamalek",
    "changed_fields": "Internet, Cleaning supplies, Bills",
    "total_sales": "...",
    ...
}

`recipients` can be:
 - a list of email addresses, or
 - a comma-separated string of emails

Return value:
 - True on success, False on failure (exceptions are logged).
"""

from __future__ import annotations

import os
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Iterable, List, Union, Optional

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def _get_smtp_config() -> dict:
    """
    Try to load SMTP configuration from Streamlit secrets (if available),
    otherwise fall back to environment variables.

    Returns a dict with keys: server, port (int), user, password.
    Raises ValueError if required keys are missing.
    """
    server = None
    port = None
    user = None
    password = None

    # Try Streamlit secrets if streamlit is installed and running
    try:
        import streamlit as st  # type: ignore

        secrets = getattr(st, "secrets", None)
        if secrets:
            server = secrets.get("SMTP_SERVER") or server
            port = secrets.get("SMTP_PORT") or port
            user = secrets.get("SMTP_USER") or user
            password = secrets.get("SMTP_PASSWORD") or password
    except Exception:
        # streamlit may not be available in some contexts — ignore
        pass

    # Environment fallback
    server = server or os.environ.get("SMTP_SERVER")
    port = port or os.environ.get("SMTP_PORT")
    user = user or os.environ.get("SMTP_USER")
    password = password or os.environ.get("SMTP_PASSWORD")

    if port is None:
        # default to 587 (STARTTLS)
        port = 587

    try:
        port = int(port)
    except Exception:
        raise ValueError(f"Invalid SMTP_PORT value: {port!r}")

    if not server or not user or not password:
        raise ValueError(
            "SMTP configuration incomplete. Set SMTP_SERVER, SMTP_PORT, SMTP_USER and SMTP_PASSWORD "
            "in Streamlit secrets or environment variables."
        )

    return {"server": server, "port": port, "user": user, "password": password}


def _normalize_recipients(recipients: Union[str, Iterable[str], None]) -> List[str]:
    """
    Normalize recipients argument to a list of strings.
    Accepts:
      - comma-separated string
      - iterable of strings
    Returns list of non-empty trimmed email strings.
    """
    if recipients is None:
        return []
    if isinstance(recipients, str):
        parts = [p.strip() for p in recipients.split(",")]
        return [p for p in parts if p]
    try:
        return [str(p).strip() for p in recipients if str(p).strip()]
    except Exception:
        return []


def _build_message(subject: str, sender: str, recipients: List[str], report: dict) -> EmailMessage:
    """
    Construct an EmailMessage with both plain text and HTML parts.
    The HTML is simple and safe (table of key/value pairs).
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    # Plain-text body
    plain_lines = [
        f"{subject}",
        "",
        "Summary:",
    ]
    for k, v in report.items():
        plain_lines.append(f"{k}: {v}")
    plain_body = "\n".join(plain_lines)

    # HTML body - basic table
    html_rows = []
    for k, v in report.items():
        # escape minimal HTML; this is internal data so keep it simple
        k_ = str(k)
        v_ = str(v)
        html_rows.append(f"<tr><td style='padding:6px;border:1px solid #ddd'><strong>{k_}</strong></td>"
                         f"<td style='padding:6px;border:1px solid #ddd'>{v_}</td></tr>")

    html_body = f"""
    <html>
      <body>
        <h2 style="font-family:Arial, sans-serif">Daily submission report</h2>
        <p style="font-family:Arial, sans-serif">Summary for <strong>{report.get('branch','')}</strong> — date: <strong>{report.get('date','')}</strong></p>
        <table style="border-collapse:collapse;font-family:Arial, sans-serif">
          {''.join(html_rows)}
        </table>
        <p style="font-family:Arial, sans-serif;color:#666;font-size:12px">This is an automated message from the Register Closures app.</p>
      </body>
    </html>
    """

    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_daily_submission_report(report: dict, recipients: Union[str, Iterable[str]]) -> bool:
    """
    Send a daily submission report.

    Args:
        report: dict with report data (date, branch, changed_fields, etc.)
        recipients: list of emails or comma-separated string of emails

    Returns:
        True if message was accepted by the SMTP server and no exception was raised; False otherwise.
    """
    recipients_list = _normalize_recipients(recipients)
    if not recipients_list:
        logger.error("No recipients provided to send_daily_submission_report")
        raise ValueError("No recipients provided")

    try:
        cfg = _get_smtp_config()
    except Exception as e:
        logger.exception("SMTP configuration error: %s", e)
        raise

    subject = f"Daily Closure Report — {report.get('branch','')} — {report.get('date','')}"
    sender = cfg["user"]

    try:
        msg = _build_message(subject, sender, recipients_list, report)
    except Exception as e:
        logger.exception("Failed to build email message: %s", e)
        raise

    try:
        context = ssl.create_default_context()
        # Use SMTP with STARTTLS (port usually 587)
        with smtplib.SMTP(cfg["server"], cfg["port"], timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        logger.info("Daily submission report sent to %s", recipients_list)
        return True
    except Exception as e:
        logger.exception("Failed to send daily submission report: %s", e)
        return False


# expose a default handler when used as a script for quick test (optional)
if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    sample = {"date": "2025-12-01", "branch": "Zamalek", "changed_fields": "Internet, Bills", "total_sales": "1200"}
    try:
        # Example usage: send to SMTP_USER from env (or configure secrets)
        recips = os.environ.get("REPORT_RECIPIENTS", os.environ.get("SMTP_USER", ""))
        send_daily_submission_report(sample, recips)
    except Exception as e:
        logger.exception("Test send failed: %s", e)
