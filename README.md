# linkedin-monitor

Monitor [LinkedIn](https://linkedin.com) profiles for new posts and get notified via Slack and/or Discord.

## How It Works

1. Uses [Playwright](https://playwright.dev/) to open a headless Chromium browser with saved session cookies
2. Visits each profile's "Recent Activity" page and parses post data from the DOM
3. Compares against previously seen post IDs stored in `state.json`
4. Sends new posts to Slack (and optionally Discord) via webhook; operational errors go to a separate Discord error webhook

> **Note:** LinkedIn does not provide a public API for reading profile activity. This tool uses browser automation with your own login session cookies. Be aware this may conflict with LinkedIn's Terms of Service. Use at your own discretion.

## Prerequisites

- Python 3.10+
- Playwright (`pip install playwright && playwright install chromium`)

## Quick Start

```bash
git clone https://github.com/seoyeon6174/linkedin-monitor.git
cd linkedin-monitor
pip install -r requirements.txt
playwright install chromium
```

### 1. Set Up Session

Run the session setup script to log in and save your cookies:

```bash
python3 setup_session.py
```

A browser window will open. Log in to LinkedIn (including 2FA if enabled). The script automatically detects the `li_at` authentication cookie and saves the session — no manual confirmation needed (5-minute timeout).

### 2. Configure Profiles

Edit the `MONITOR_PROFILES` list in `monitor_linkedin.py`:

```python
MONITOR_PROFILES = [
    {"name": "Sam Altman", "url": "https://www.linkedin.com/in/samaltman/recent-activity/all/"},
    {"name": "Satya Nadella", "url": "https://www.linkedin.com/in/satyanadella/recent-activity/all/"},
]
```

### 3. Run

`monitor_linkedin.py` reads webhook settings from environment variables — the `.env` file is sourced by `run_monitor.sh`, not by the Python script itself. For a direct run, export the variables first:

```bash
cp .env.example .env   # then fill in your webhook URLs
set -a; source .env; set +a
python3 monitor_linkedin.py
```

## Environment Variables

| Variable                  | Required | Description                                                       |
| ------------------------- | -------- | ----------------------------------------------------------------- |
| `LINKEDIN_SLACK_WEBHOOK`  | No       | Slack incoming webhook URL for new-post notifications             |
| `DISCORD_WEBHOOK_THREADS` | No       | Discord webhook for new-post notifications (dual-send)            |
| `DISCORD_WEBHOOK_ERRORS`  | No       | Discord webhook for error alerts (session expiry, parse failures) |
| `LINKEDIN_DRY_RUN`        | No       | `1`/`true`/`yes`/`on` — run without sending any notifications     |

If `LINKEDIN_SLACK_WEBHOOK` is not set, new posts are printed to the console instead.

## Scheduling

`run_monitor.sh` wraps the monitor with a random 0–10 minute delay to avoid predictable access patterns. It sources `.env` from the repo directory if present.

### Option 1: cron

```bash
# Run every hour from 8 AM to 9 PM
0 8-21 * * * /path/to/linkedin-monitor/run_monitor.sh >> /path/to/linkedin-monitor/cron.log 2>&1
```

### Option 2: launchd (macOS)

Create a LaunchAgent plist with `StartCalendarInterval` entries pointing at `run_monitor.sh`, then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.linkedin-monitor.plist
```

launchd is more reliable than cron on macOS for jobs that need home-directory access.

## Notification Policy

To minimize false alarms, a post is only sent to the user-facing channel when **all** of these hold:

- Post ID not in the profile's `seen_ids` history
- Post text is at least 30 characters (filters reactions/shares without commentary)
- Post timestamp parses successfully and is within the last 24 hours

Additional safety behaviors:

- **First-run warmup**: the first run for each profile records a baseline without sending notifications
- **Safe mode**: if the previously seen anchor post disappears from the page, user notifications are skipped and a warning goes to the error webhook
- **Session expiry detection**: login/authwall/signup redirects, guest locale subdomains (e.g. `kr.linkedin.com`), and guest-page HTML markers all trigger an error alert telling you to re-run `setup_session.py`
- **Navigation retry**: `page.goto` retries up to 3 times (45s timeout, 5s/10s backoff)

## Tuning Parameters

Constants at the top of `monitor_linkedin.py`:

| Constant             | Default | Description                            |
| -------------------- | ------- | -------------------------------------- |
| `MAX_POSTS_TO_CRAWL` | 8       | Posts collected per profile            |
| `SEEN_IDS_LIMIT`     | 8       | Deduplication history size per profile |
| `RECENT_HOURS`       | 24      | Only notify posts newer than this      |
| `MIN_TEXT_LENGTH`    | 30      | Minimum post text length to notify     |

## Running Tests

```bash
pip install pytest
python3 -m pytest test_monitor_linkedin.py -v
```

## Session Management

Session cookies are stored in `session/linkedin_state.json`. Sessions typically last several weeks to months but can expire. When the session expires:

1. The monitor detects it and sends an error notification (Discord error webhook, if configured)
2. Run `python3 setup_session.py` to log in again and refresh the session

**Important:** Never commit or share your session file — it contains your LinkedIn login cookies.

## Troubleshooting

- **Posts not detected / parse timeouts**: LinkedIn changes its DOM classes periodically. Check the selectors in `parse_posts()` and `_parse_single_post()` against the live page structure.
- **Repeated "session expired" alerts right after login**: make sure you completed login in the setup browser window until the `li_at` cookie was detected.

## License

[MIT](LICENSE)
