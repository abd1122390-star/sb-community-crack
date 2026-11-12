# Public Website Asset Collector Telegram Bot

This bot accepts a public HTTP(S) website URL and creates a ZIP of browser-delivered files it can reach:
HTML pages, linked CSS/JavaScript, images, fonts and other referenced static assets.

## Important limits
- It only downloads files that the website publicly serves.
- It does not recover backend/server-side source code, databases, secrets, private endpoints or protected files.
- It does not bypass logins, paywalls, access controls or hosting protections.
- It visits up to 20 same-host HTML pages, collects up to 250 files, and caps total download size at 35 MB.
- Use it only on sites you own or have permission to archive.

## Run locally
1. Install Python 3.10 or newer.
2. Install dependencies: `pip install -r requirements.txt`
3. Create a Telegram bot with BotFather and copy its token.
4. Set the environment variable `BOT_TOKEN`:
   - Linux/macOS: `export BOT_TOKEN="YOUR_BOT_TOKEN"`
   - Windows PowerShell: `$env:BOT_TOKEN="YOUR_BOT_TOKEN"`
5. Run: `python bot.py`
6. Open your Telegram bot, send `/start`, then send a public website URL.

## Deploy
Deploy on a Python worker service that supports long-running polling processes. Add `BOT_TOKEN` as a secret/environment variable and use:
`python bot.py`

This is a downloader for publicly exposed browser assets, not a source-code recovery tool. Some websites load content dynamically or require API calls, so the ZIP may not reproduce the original site exactly.
