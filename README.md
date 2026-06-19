# Albertsons Coupon Clipper

Automated coupon clipping for Albertsons/Safeway loyalty programs using Chrome DevTools Protocol (CDP).

Connects to your existing Chrome browser — uses your real session, no separate profile, no expiring auth. Supports self-healing re-authentication when sessions expire.

## Features

- **Authenticated clipping** — Uses your real Chrome profile, not headless hacks
- **Self-healing** — Auto re-authenticates when the session expires (uses stored credentials)
- **Activate-aware** — Skips "Activate" offers that spend loyalty points
- **Cron-friendly** — Runs headless, reports counts, exits clean
- **Manual override** — `--login` flag for device verification flows

## Setup

### Prerequisites

- Python 3.10+
- Google Chrome
- An Albertsons for U™ account

### Installation

```bash
git clone https://github.com/kriisko-glitch/albertsons-coupon-clipper.git
cd albertsons-coupon-clipper
pip install -r requirements.txt
```

### Configuration

Copy the example env file and fill in your Albertsons credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```ini
ALBERTSONS_PHONE=2085550199
ALBERTSONS_PASSWORD=your_password_here

# Optional — override Chrome paths if non-standard
# CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe
# CHROME_USER_DATA=%LOCALAPPDATA%\Google\Chrome\User Data
```

### Chrome Setup (one-time)

Chrome must be running with the remote debugging port enabled:

```
chrome.exe --remote-debugging-port=9222
```

Or create a desktop shortcut with that flag. The script can also launch Chrome for you if it's not running.

## Usage

### Clip all coupons

```bash
python albertsons_clip.py
```

### Manual re-authentication (device verification)

If Albertsons requires a device verification code (6-digit SMS), run:

```bash
python albertsons_clip.py --login
```

This opens a visible Chrome window. Sign in manually, then the session is saved for future headless runs.

### Cron / Scheduled

Add to your crontab or task scheduler:

```bash
0 8 * * * cd /path/to/albertsons-coupon-clipper && python albertsons_clip.py
```

## How It Works

1. **Connects to Chrome** via CDP (port 9222)
2. **Navigates** to the Albertsons coupons page
3. **Loads all coupons** by clicking "Load more"
4. **Clips visible coupons** in a scroll-while-clip loop
5. **Reports** final counts

If the session is expired:
6. **Auto-reauthenticates** using phone + password from `.env`
7. **Retries** clipping after successful re-auth

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success — coupons clipped (or none needed) |
| 1 | Error — Chrome/CDP issue |
| 2 | Auth failure — device verification needed, run `--login` |

## Security

- Credentials are stored **only** in your local `.env` file (gitignored)
- The script connects to YOUR Chrome — no remote browser services
- No telemetry, no network calls except to Albertsons domains

## License

MIT
