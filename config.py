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

# Change only AI_PROVIDER to switch extraction providers.
# Supported values: gemini | anthropic | openrouter | deepseek
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").strip().lower()

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DEEPSEEK_API_KEY   = os.getenv("DEEPSEEK_API_KEY")

AI_PROVIDERS = {
    "gemini": {
        "api_key": GEMINI_API_KEY,
        "model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
    },
    "anthropic": {
        "api_key": ANTHROPIC_API_KEY,
        "model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        "url": "https://api.anthropic.com/v1/messages",
    },
    "openrouter": {
        "api_key": OPENROUTER_API_KEY,
        "model": os.getenv(
            "OPENROUTER_MODEL",
            "google/gemini-3.1-flash-lite",
        ),
        "url": "https://openrouter.ai/api/v1/chat/completions",
    },
    "deepseek": {
        "api_key": DEEPSEEK_API_KEY,
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "url": "https://api.deepseek.com/chat/completions",
    },
}


def get_ai_provider_config() -> dict:
    provider = AI_PROVIDERS.get(AI_PROVIDER)
    if provider is None:
        supported = ", ".join(AI_PROVIDERS)
        raise ValueError(
            f"Unsupported AI_PROVIDER={AI_PROVIDER!r}. Choose one of: {supported}"
        )
    if not provider["api_key"]:
        key_name = f"{AI_PROVIDER.upper()}_API_KEY"
        raise ValueError(
            f"AI_PROVIDER is {AI_PROVIDER!r}, but {key_name} is not configured."
        )
    return provider

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

# Playwright timeouts in milliseconds.
NAVIGATION_TIMEOUT = int(os.getenv("NAVIGATION_TIMEOUT", "60000"))
SELECTOR_WAIT_TIMEOUT = int(os.getenv("SELECTOR_WAIT_TIMEOUT", "20000"))
LOGIN_WAIT_TIMEOUT = int(os.getenv("LOGIN_WAIT_TIMEOUT", "120000"))

# --- Search queries ---
# LinkedIn content search: https://www.linkedin.com/search/results/content/?keywords=...
SEARCH_QUERIES = [
    # Freshers / New Grad
    "fresher software engineer hiring",
    "new grad software engineer",
    "entry level software engineer",
    "junior software engineer hiring",
    "associate software engineer",
    "graduate engineer trainee software",

    # Batch-specific
    "2026 batch hiring",
    "off campus hiring 2026",
    "off campus drive software engineer",

    # SDE Roles
    "SDE 1 hiring",
    "SDE1 opening",
    "backend engineer 0-2 years",
    "frontend engineer 0-2 years",
    "full stack engineer 0-2 years",

    # Experience Range
    "0-1 years experience software engineer",
    "0-2 years software engineer",
    "early career software engineer",

    # Internships
    "software engineer intern hiring",
    "backend intern hiring",
    "paid software internship India",

    # Backend / Stack
    "backend engineer hiring India",
    "nodejs developer hiring India",
    "python developer hiring India",
    "golang developer hiring India",
    "full stack developer hiring India",
    "mern developer hiring",

    # Startup Signals
    "we are hiring software engineer",
    "join our engineering team",
    "looking for software engineers",
    "startup hiring engineers India",
    "founding engineer hiring",

    # Referral Signals
    "referral software engineer",
    "happy to refer software engineer",
    "DM for referral software engineer",
    "can refer software engineer",

    # Application Signals
    "apply now software engineer India",
    "software engineer apply email",

    # India / Remote
    "software engineer hiring India",
    "remote software engineer India",
    "work from home software engineer India",
    "remote backend engineer India",

    # Cities
    "software engineer Pune hiring",
    "software engineer Bangalore hiring",
    "software engineer Bengaluru hiring",
    "software engineer Hyderabad hiring",

    # Hidden Gems
    "join our team engineering India",
    "software engineer opportunities India",
    "software developer opportunities India",
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
