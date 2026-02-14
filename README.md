# linkedin-monitor

Monitor [LinkedIn](https://linkedin.com) profiles for new posts and get notified via Slack.

## How It Works

1. Uses [Playwright](https://playwright.dev/) to open a headless Chromium browser with saved session cookies
2. Visits each profile's "Recent Activity" page and parses post data from the DOM
3. Compares against previously seen post IDs stored in `state.json`
4. Sends new posts to a Slack channel via webhook

> **Note:** LinkedIn does not provide a public API for reading profile activity. This tool uses browser automation with your own login session cookies. Be aware this may conflict with LinkedIn's Terms of Service. Use at your own discretion.

## Prerequisites

- Python 3.10+
- Playwright (`pip install playwright && playwright install chromium`)

## Quick Start

```bash
git clone https://github.com/skylahkim/linkedin-monitor.git
cd linkedin-monitor
pip install -r requirements.txt
playwright install chromium
```

### 1. Set Up Session

Run the session setup script to log in and save your cookies:

```bash
python setup_session.py
```

A browser window will open. Log in to LinkedIn (including 2FA if enabled), then press Enter in the terminal once you see the feed.

### 2. Configure Profiles

Edit the `MONITOR_PROFILES` list in `monitor_linkedin.py`:

```python
MONITOR_PROFILES = [
    {"name": "Sam Altman", "url": "https://www.linkedin.com/in/samaltman/recent-activity/all/"},
    {"name": "Satya Nadella", "url": "https://www.linkedin.com/in/satyanadella/recent-activity/all/"},
]
```

### 3. Run

```bash
export LINKEDIN_SLACK_WEBHOOK="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
python monitor_linkedin.py
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LINKEDIN_SLACK_WEBHOOK` | Yes | Slack incoming webhook URL |

If `LINKEDIN_SLACK_WEBHOOK` is not set, the monitor runs in dry-run mode and prints new posts to the console.

## Cron Setup

Use `run_monitor.sh` to run the monitor on a schedule. The wrapper adds a random delay (0-30 minutes) before execution to avoid predictable access patterns.

```bash
# Example: run every hour from 8:20 AM to 9:20 PM
20 8-21 * * * /path/to/linkedin-monitor/run_monitor.sh >> /path/to/linkedin-monitor/cron.log 2>&1
```

Make sure `LINKEDIN_SLACK_WEBHOOK` is set in your environment or in a `.env` file sourced by the script.

## Content Filtering

Posts with fewer than 30 characters of text are excluded from notifications. This filters out low-content posts like reactions or shares without commentary.

## Session Management

Session cookies are stored in `session/linkedin_state.json`. Sessions typically last several weeks to months but can expire. When the session expires:

1. The monitor detects the login redirect and sends a Slack error notification
2. Run `python setup_session.py` to log in again and refresh the session

**Important:** Never commit or share your session file — it contains your LinkedIn login cookies.

## License

[MIT](LICENSE)
