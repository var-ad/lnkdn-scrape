import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

# --- Credentials ---
LINKEDIN_EMAIL    = os.getenv("LINKEDIN_EMAIL")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DEEPSEEK_API_KEY   = os.getenv("DEEPSEEK_API_KEY")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")

# Path to Google service account JSON
GOOGLE_SA_JSON    = os.getenv("GOOGLE_SA_JSON", "service_account.json")
SPREADSHEET_ID    = os.getenv("SPREADSHEET_ID")   # from the Google Sheet URL

# --- Sheet tab naming ---
# If enabled, every run writes to a tab named like "29-5-2026" in Asia/Kolkata.
TIMEZONE          = os.getenv("TIMEZONE", "Asia/Kolkata")
DAILY_SHEET_TABS  = _env_bool("DAILY_SHEET_TABS", True)


def today_sheet_tab_name() -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    return f"{now.day}-{now.month}-{now.year}"


SHEET_TAB_NAME = os.getenv("SHEET_TAB_NAME") or (
    today_sheet_tab_name() if DAILY_SHEET_TABS else "Jobs"
)

# --- Scrape window ---
# "past-24h" | "past-week"
DATE_FILTER = os.getenv("DATE_FILTER", "past-24h")

# Max posts to scrape per query (scroll iterations × ~5 posts each)
MAX_SCROLL_ROUNDS = int(os.getenv("MAX_SCROLL_ROUNDS", "6"))

# Seconds to wait between scroll steps (be polite to LinkedIn)
SCROLL_DELAY = float(os.getenv("SCROLL_DELAY", "2.5"))

# Browser mode. Use HEADLESS_BROWSER=true for cloud deployments.
HEADLESS_BROWSER = _env_bool("HEADLESS_BROWSER", False)

# --- Search queries ---
# LinkedIn content search: https://www.linkedin.com/search/results/content/?keywords=...
SEARCH_QUERIES = [
    "is hiring fresher",
    "hiring SDE 1",
    "hiring software engineer fresher",
    "hiring intern 2025 email",
    "hiring intern 2026 apply form",
    "fresher hiring Pune",
    "fresher hiring Bangalore",
    "fresher hiring Hyderabad",
    "fresher hiring India remote",
    "SDE1 opening fresher apply",
    "new grad software engineer hiring",
    "entry level software engineer hiring India",
    "hiring junior developer India",
]

# --- Google Sheets columns (order matters) ---
SHEET_COLUMNS = [
    "Post Date",
    "Company",
    "Job Title",
    "Job Type",          # FTE | Intern | Contract
    "Location",
    "Salary / Stipend",
    "Apply Link / Email",
    "Skills Required",
    "Poster Name",
    "LinkedIn Post URL",
    "Raw Snippet",       # first 300 chars of post for quick review
]
