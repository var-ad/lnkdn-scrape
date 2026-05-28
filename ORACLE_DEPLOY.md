# Oracle Cloud Deployment

This repo is prepared to run once per day at 6:45 PM IST and write each run to a daily Google Sheets tab named like `29-5-2026`.

## 1. VM Setup

Use an Oracle Cloud Ubuntu VM.

```bash
sudo apt update
sudo apt install -y git python3-venv
sudo mkdir -p /opt/linkedin-scraper
sudo chown "$USER:$USER" /opt/linkedin-scraper
```

Copy or clone this repo into `/opt/linkedin-scraper`.

```bash
cd /opt/linkedin-scraper
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
mkdir -p logs
```

## 2. Environment

Create `/opt/linkedin-scraper/.env` from `.env.example`.

Important cloud values:

```env
DATE_FILTER=past-24h
TIMEZONE=Asia/Kolkata
DAILY_SHEET_TABS=true
HEADLESS_BROWSER=true
```

Copy your Google service account JSON into the repo and set:

```env
GOOGLE_SA_JSON=your-service-account.json
SPREADSHEET_ID=your_sheet_id
```

Share the Google Sheet with the service account email as Editor.

## 3. LinkedIn Session

Cloud runs should use an existing `.linkedin_session.json`, because first-time LinkedIn login may require CAPTCHA or 2FA.

Run locally once with `HEADLESS_BROWSER=false`, complete login, then copy `.linkedin_session.json` to:

```text
/opt/linkedin-scraper/.linkedin_session.json
```

## 4. Schedule

Install the systemd unit and timer:

```bash
sudo cp deploy/linkedin-scraper.service /etc/systemd/system/
sudo cp deploy/linkedin-scraper.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now linkedin-scraper.timer
```

Check schedule and logs:

```bash
systemctl list-timers linkedin-scraper.timer
tail -f /opt/linkedin-scraper/logs/scraper.log
```

Manual test run:

```bash
cd /opt/linkedin-scraper
. .venv/bin/activate
python main.py --date past-24h --dry-run
```
