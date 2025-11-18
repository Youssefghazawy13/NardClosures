
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pandas as pd
from src.sheets_client import SheetsClient

def _row_to_metrics(row):
    def val(k):
        try:
            return float(row.get(k, 0) or 0)
        except Exception:
            return 0.0
    metrics = {
        "Date": str(row.get("Date", "")),
        "No.Invoices": int(val("No.Invoices")),
        "No.Products": int(val("No. Products")),
        "Total System Sales": val("Total System Sales"),
        "Total Sales": val("Total Sales"),
        "entered cash amount": val("entered cash amount"),
        "Card amount": val("Card amount"),
        "Cash": val("Cash"),
        "Cash Deficit": val("Cash Deficit"),
        "Card Deficit": val("Card Deficit"),
        "net cash": val("net cash"),
        "Accumulative cash": val("Accumulative cash"),
        "Accumulative card": val("Accumulative card"),
        "Total Money": val("Total Money"),
        "SuperPay expected": val("SuperPay expected"),
        "SuperPay sent": val("SuperPay sent"),
        "SuperPay diff": val("SuperPay diff"),
        "Cashouts": val("Cashouts"),
        "Petty cash": val("Petty cash"),
    }
    return metrics

def _metrics_to_html_table(metrics: dict, title: str):
    rows = "".join(f"<tr><td style='padding:4px 8px;border:1px solid #ddd;font-family:Arial'>{k}</td>"
                   f"<td style='padding:4px 8px;border:1px solid #ddd;font-family:Arial;text-align:right'>{metrics[k]}</td></tr>"
                   for k in metrics)
    html = f"""
    <h3 style="font-family:Arial">{title}</h3>
    <table style="border-collapse:collapse;border:1px solid #ddd;margin-bottom:12px">
      <thead><tr><th style='padding:6px;background:#f6f6f6'>Metric</th><th style='padding:6px;background:#f6f6f6'>Value</th></tr></thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    """
    return html

def _build_email_html(branch_reports: dict, totals_report: dict, report_date: str):
    header = f"<h2 style='font-family:Arial'>Daily Sales Report — {report_date}</h2>"
    parts = [header]
    for branch, metrics in branch_reports.items():
        parts.append(_metrics_to_html_table(metrics, f"{branch} — Details"))
    parts.append("<h3 style='font-family:Arial'>Combined Totals</h3>")
    parts.append(_metrics_to_html_table(totals_report, "Totals"))
    footer = "<p style='font-family:Arial;font-size:12px;color:#666'>This is an automated message from Register Closures system.</p>"
    return "<div>" + "".join(parts) + footer + "</div>"

def send_daily_submission_report(date_str: str, recipients: list, st_secrets=None,
                                 zamalek_sheet_id=None, alex_sheet_id=None, smtp_config=None):
    client = SheetsClient(st_secrets)

    def find_row_for_date(sheet_id, date_str):
        sh = client.gc.open_by_key(sheet_id)
        for ws in sh.worksheets():
            if ws.title in ("Settings","ChangeLog"):
                continue
            df = client.read_month_sheet(sheet_id, ws.title)
            try:
                df["__date_key"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
            except Exception:
                df["__date_key"] = df["Date"].astype(str)
            matched = df[df["__date_key"] == date_str]
            if not matched.empty:
                return matched.iloc[0].to_dict()
        return None

    zam_row = find_row_for_date(zamalek_sheet_id, date_str)
    alex_row = find_row_for_date(alex_sheet_id, date_str)

    branch_reports = {}
    if zam_row is not None:
        branch_reports["Zamalek"] = _row_to_metrics(zam_row)
    else:
        branch_reports["Zamalek"] = {"Date": date_str, "No.Invoices": 0, "No.Products":0, "Total Sales":0}

    if alex_row is not None:
        branch_reports["Alexandria"] = _row_to_metrics(alex_row)
    else:
        branch_reports["Alexandria"] = {"Date": date_str, "No.Invoices": 0, "No.Products":0, "Total Sales":0}

    totals = {}
    numeric_keys = [k for k in branch_reports["Zamalek"].keys() if k not in ("Date",)]
    for k in numeric_keys:
        try:
            totals[k] = round(sum(float(branch_reports[b].get(k, 0) or 0) for b in branch_reports), 2)
        except Exception:
            totals[k] = ""
    totals["Date"] = date_str

    html_body = _build_email_html(branch_reports, totals, report_date=date_str)

    if smtp_config is None and st_secrets is not None:
        smtp_config = {
            "smtp_server": st_secrets.get("SMTP_SERVER", "smtp.gmail.com"),
            "smtp_port": int(st_secrets.get("SMTP_PORT", 587)),
            "smtp_user": st_secrets.get("SMTP_USER"),
            "smtp_password": st_secrets.get("SMTP_PASSWORD"),
            "use_tls": True
        }

    if not smtp_config or not smtp_config.get("smtp_user") or not smtp_config.get("smtp_password"):
        raise RuntimeError("Missing SMTP configuration. Provide smtp_user & smtp_password in smtp_config or Streamlit secrets.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Daily Report] Sales summary for {date_str}"
    msg["From"] = smtp_config["smtp_user"]
    msg["To"] = ", ".join(recipients)
    part_html = MIMEText(html_body, "html")
    msg.attach(part_html)

    server = smtplib.SMTP(smtp_config["smtp_server"], smtp_config["smtp_port"])
    try:
        if smtp_config.get("use_tls", True):
            server.starttls()
        server.login(smtp_config["smtp_user"], smtp_config["smtp_password"])
        server.sendmail(msg["From"], recipients, msg.as_string())
    finally:
        server.quit()

    return {"status": "sent", "recipients": recipients, "date": date_str}
