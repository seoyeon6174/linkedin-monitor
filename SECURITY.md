# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly:

1. **GitHub Issues** - Open an issue at [github.com/seoyeon6174/linkedin-monitor/issues](https://github.com/seoyeon6174/linkedin-monitor/issues)
2. **Email** - Contact the maintainer directly

## Important

- **Never post secrets** (API keys, webhook URLs, session cookies, tokens) in issues or pull requests
- If you accidentally expose a secret, rotate it immediately and notify the maintainer
- **Never share your `session/linkedin_state.json`** file — it contains your LinkedIn login cookies

## Scope

This project runs as a monitoring script and interacts with:

- LinkedIn (linkedin.com) via Playwright browser automation with session cookies
- Slack via webhook URLs

Security concerns related to these integrations are in scope.
