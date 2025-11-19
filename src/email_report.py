# src/email_report.py
import os
import smtplib
import ssl
import logging
from typing import List, Dict, Optional
from email.message import EmailMessage
from email.utils import formataddr

logger = logging.getLogger(__name__)

def _build_plain_text_report(report: Dict[str, str]) -> str:
    lines = []
    lines.append(f"Branch: {report.get('branch','')}")
    lines.append(f"Date: {report.get('date','')}")
    lines.append("")
    for k, v in report.items():
        if k in ("branch","date"):
            continue
        lines.append(f"{k}: {v}")
    return "\n".join(lines)

def _build_html_table(report: Dict[str, str]) -> str:
    # produce a simple bordered table
    rows = []
    # keep a stable order for the important fields first
    order = [
        "No.Invoices","No. Products","System amount Cash","System amount Card","Total System Sales",
        "entered cash amount","entered Card amount","Total Sales",
        "Cash outs","Petty cash","Cash Deficit","Card Deficit","SuperPay expected","SuperPay sent","SuperPay diff","Net cash",
        "Accumulative cash","Accumulative card","Total Money","closure_time","closed_by"
    ]
    # fallback: include any other keys at the end
    used = set()
    html = ['<html><body>']
    html.append(f"<p><strong>Branch:</strong> {report.get('branch','')} &nbsp;&nbsp; <strong>Date:</strong> {report.get('date','')}</p>")
    html.append('<table style="border-collapse:collapse;border:1px solid #888;">')
    # header
    html.append('<thead><tr>')
    html.append('<th style="border:1px solid #888;padding:6px;background:#eee">Field</th>')
    html.append('<th style="border:1px solid #888;padding:6px;background:#eee">Value</th>')
    html.append('</tr></thead><tbody>')
    for key in order:
        if key in report:
            val = report.get(key, "")
            html.append(f'<tr><td style="border:1px solid #888;padding:6px">{key}</td><td style="border:1px solid #888;padding:6px">{val}</td></tr>')
            used.add(key)
    # additional keys
    for k, v in report.items():
        if k in ("branch","date") or k in used:
            continue
        html.append(f'<tr><td style="border:1px solid #888;padding:6px">{k}</td><td style="border:1px solid #888;padding:6px">{v}</td></tr>')
    html.append('</tbody></table>')
    if "extra" in report:
        html.append('<p><strong>Notes:</strong></p>')
        html.append(f'<pre>{report["extra"]}</pre>')
    html.append('</body></html>')
    return "".join(html)

def st_secrets_get(key: str) -> Optional[str]:
    try:
        import streamlit as _st
        return _st.secrets.get(key)
    except Exception:
        return None

def send_daily_submission_report(report: Dict[str, str], recipients: List[str], subject_prefix: str = "Daily closure", attachments: Optional[List[str]] = None) -> bool:
    """
    Send an email with an HTML table and plain-text fallback.
    report: mapping of fields -> values (strings)
    recipients: list of email addresses
    attachments: optional list of file paths to attach
    """
    try:
        smtp_server = os.environ.get("SMTP_SERVER") or st_secrets_get("SMTP_SERVER")
        smtp_port = int(os.environ.get("SMTP_PORT") or (st_secrets_get("SMTP_PORT") or 587))
        smtp_user = os.environ.get("SMTP_USER") or st_secrets_get("SMTP_USER")
        smtp_password = os.environ.get("SMTP_PASSWORD") or st_secrets_get("SMTP_PASSWORD")
        from_name = os.environ.get("EMAIL_FROM_NAME") or st_secrets_get("EMAIL_FROM_NAME") or "Register Closures"
    except Exception:
        logger.exception("SMTP config not found")
        return False

    if not smtp_user or not smtp_password:
        logger.error("SMTP user/password missing")
        return False

    subject = f"{subject_prefix} — {report.get('branch','')} — {report.get('date','')}"
    plain_body = _build_plain_text_report(report)
    html_body = _build_html_table(report)

    msg = EmailMessage()
    msg["From"] = formataddr((from_name, smtp_user))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

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
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info("Email sent to %s", recipients)
        return True
    except Exception:
        logger.exception("Failed to send email")
        return False
