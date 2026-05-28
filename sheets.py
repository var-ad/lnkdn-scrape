"""
sheets.py
Write structured job records to Google Sheets using a service account.
Deduplicates by LinkedIn Post URL to avoid repeat rows.
"""

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _get_worksheet():
    creds = Credentials.from_service_account_file(config.GOOGLE_SA_JSON, scopes=SCOPES)
    gc = gspread.authorize(creds)

    try:
        sh = gc.open_by_key(config.SPREADSHEET_ID)
    except PermissionError as e:
        email = getattr(creds, "service_account_email", "(unknown service account)")
        raise PermissionError(
            "Google Sheets permission denied. Share the target spreadsheet with "
            f"this service account email as Editor: {email}"
        ) from e
    except APIError as e:
        email = getattr(creds, "service_account_email", "(unknown service account)")
        raise RuntimeError(
            f"Could not open spreadsheet {config.SPREADSHEET_ID}. "
            f"Service account: {email}. Original error: {e}"
        ) from e

    # Create tab if it doesn't exist
    try:
        ws = sh.worksheet(config.SHEET_TAB_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=config.SHEET_TAB_NAME, rows=2000, cols=len(config.SHEET_COLUMNS))
        print(f"[sheets] Created worksheet tab: {config.SHEET_TAB_NAME}")

    return ws


def ensure_header(ws):
    """Write header row if sheet is empty."""
    existing = ws.row_values(1)
    if not existing or existing[0] != config.SHEET_COLUMNS[0]:
        ws.insert_row(config.SHEET_COLUMNS, index=1)
        # Bold + freeze header row
        ws.format("1:1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.13, "green": 0.13, "blue": 0.18},
        })
        ws.freeze(rows=1)
        print("[sheets] Header row written.")


def get_existing_urls(ws) -> set[str]:
    """Return set of LinkedIn Post URLs already in the sheet (col index of that column)."""
    try:
        col_idx = config.SHEET_COLUMNS.index("LinkedIn Post URL") + 1  # 1-based
        values = ws.col_values(col_idx)
        return set(v.strip() for v in values[1:] if v.strip())  # skip header
    except Exception:
        return set()


def append_jobs(jobs: list[dict]) -> int:
    """
    Append job rows to the sheet, skipping duplicates.
    Returns number of rows actually written.
    """
    if not jobs:
        print("[sheets] No jobs to write.")
        return 0

    ws = _get_worksheet()
    ensure_header(ws)
    existing_urls = get_existing_urls(ws)

    rows_to_add = []
    for job in jobs:
        url = job.get("LinkedIn Post URL", "").strip()
        if url and url in existing_urls:
            continue  # already logged
        row = [job.get(col, "") for col in config.SHEET_COLUMNS]
        rows_to_add.append(row)
        if url:
            existing_urls.add(url)

    if rows_to_add:
        ws.append_rows(rows_to_add, value_input_option="USER_ENTERED")
        print(f"[sheets] ✅ Written {len(rows_to_add)} new rows.")
    else:
        print("[sheets] All posts already in sheet — nothing new to add.")

    return len(rows_to_add)
