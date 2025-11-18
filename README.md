# Register Closures App

Streamlit app for managing daily register closures across two branches (Zamalek & Alexandria).
Features:
- Read & write monthly sheets in Google Sheets
- Forward recalculation of accumulative fields
- ChangeLog appending
- Email daily report (per-branch + totals) after save

Instructions:
- Deploy to Streamlit Cloud and set secrets (SERVICE_ACCOUNT_JSON, sheet IDs, SMTP creds).
- Share Google Sheets with service account email.
