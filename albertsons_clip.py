#!/usr/bin/env python3
"""
Albertsons Coupon Clipper — CDP Edition
========================================
Connects to your real Chrome via CDP (Chrome DevTools Protocol) using your
existing browser session. No separate profile, no expiring auth.

Self-healing: if the session expires, auto-reauthenticates using credentials
from .env (ALBERTSONS_PHONE, ALBERTSONS_PASSWORD). Fails gracefully if device
verification is needed (requires SMS code + --login).

Usage:
    python albertsons_clip.py          # auto (connect or launch)
    python albertsons_clip.py --login  # headed launch + manual sign-in
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

# ── Configuration ────────────────────────────────────────────

CHROME_EXE = os.environ.get(
    "CHROME_EXE",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if sys.platform == "win32"
    else "/usr/bin/google-chrome",
)

CHROME_USER_DATA = os.environ.get(
    "CHROME_USER_DATA",
    os.path.expandvars(
        r"%LOCALAPPDATA%\Google\Chrome\User Data"
        if sys.platform == "win32"
        else "~/.config/google-chrome"
    ),
)

CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CDP_URL = f"http://localhost:{CDP_PORT}"
COUPONS_URL = "https://www.albertsons.com/loyalty/coupons-deals.html"
SIGNIN_URL = (
    "https://www.albertsons.com/content/www/albertsons/en/account/"
    "sign-in.html?goto=/foru/coupons-deals.html"
)


# ── Credential loading ───────────────────────────────────────

def load_env_creds():
    """Load ALBERTSONS_PHONE and ALBERTSONS_PASSWORD from environment."""
    phone = os.environ.get("ALBERTSONS_PHONE")
    password = os.environ.get("ALBERTSONS_PASSWORD")

    # Fallback: try loading .env from cwd
    if not phone or not password:
        env_file = Path(".env")
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ALBERTSONS_PHONE=") and not phone:
                        phone = line.split("=", 1)[1].strip()
                    elif line.startswith("ALBERTSONS_PASSWORD=") and not password:
                        password = line.split("=", 1)[1].strip()

    return phone, password


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Chrome management ────────────────────────────────────────

def chrome_process_running() -> bool:
    """Check if any Chrome process is running."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return "chrome.exe" in result.stdout
        else:
            result = subprocess.run(
                ["pgrep", "-x", "chrome"], capture_output=True, timeout=5
            )
            return result.returncode == 0
    except Exception:
        return False


async def cdp_available() -> bool:
    """Check if Chrome CDP endpoint is reachable."""
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{CDP_URL}/json/version", timeout=aiohttp.ClientTimeout(total=2)
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


def launch_chrome_with_cdp(headless: bool = False) -> bool:
    """Launch Chrome with remote debugging port enabled, using the real profile."""
    if not chrome_process_running():
        args = [
            CHROME_EXE,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CHROME_USER_DATA}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
        ]
        if headless:
            args.append("--headless=new")

        log(f"Launching Chrome (headless={headless})...")
        try:
            subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        except Exception as e:
            log(f"Failed to launch Chrome: {e}")
            return False

    if not headless:
        log("Chrome is running but CDP port isn't open.")
        log(f"  Start Chrome with: --remote-debugging-port={CDP_PORT}")
        return False

    # Headless mode, Chrome already running — try temp profile
    log("Chrome is running but CDP not available. Using temp profile...")
    return _launch_with_temp_profile()


def _launch_with_temp_profile() -> bool:
    """Launch headless Chrome with a temp profile copying session data."""
    import shutil
    import tempfile

    temp_dir = Path(tempfile.mkdtemp(prefix="albertsons-chrome-"))
    real_default = Path(CHROME_USER_DATA) / "Default"

    for fname in ["Cookies", "Login Data", "Preferences", "Network Persistent State"]:
        src = real_default / fname
        dst = temp_dir / "Default" / fname
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(dst))
            except Exception:
                pass

    local_state = Path(CHROME_USER_DATA) / "Local State"
    if local_state.exists():
        try:
            shutil.copy2(str(local_state), str(temp_dir) / "Local State")
        except Exception:
            pass

    args = [
        CHROME_EXE,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={temp_dir}",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
    ]

    log(f"Launching headless Chrome with temp profile at {temp_dir}...")
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        log(f"Failed to launch Chrome: {e}")
        return False


async def wait_for_cdp(timeout: int = 30) -> bool:
    """Wait for Chrome CDP to become available."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await cdp_available():
            return True
        await asyncio.sleep(1)
    return False


# ── Auto-reauth ──────────────────────────────────────────────

async def auto_reauth(page) -> bool:
    """
    Attempt to re-authenticate using stored phone+password.
    Returns True if successful, False if device verification or other blocker.
    """
    phone, password = load_env_creds()
    if not phone or not password:
        log("No Albertsons credentials found — set ALBERTSONS_PHONE and ALBERTSONS_PASSWORD in .env")
        return False

    log("Session expired. Attempting auto-reauth...")

    # Navigate to sign-in
    await page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(4)

    # Dismiss cookie banner
    try:
        await page.locator("#onetrust-accept-btn-handler").click(timeout=2000)
        await asyncio.sleep(1)
    except Exception:
        pass

    # Fill phone number
    await page.evaluate("() => document.querySelector('#enterUsername').focus()")
    await asyncio.sleep(0.3)
    await page.keyboard.type(phone, delay=80)
    await asyncio.sleep(1)

    # Click "Sign in without a password" (Albertsons UI quirk — this is the
    # gateway to the password form, despite the misleading label)
    await page.locator('button:has-text("Sign in without a password")').click(timeout=5000)
    await asyncio.sleep(4)

    # Switch to password form
    try:
        await page.locator(
            'button:has-text("Use password"), a:has-text("Use password")'
        ).first.click(timeout=5000)
        log("  Switched to password form.")
    except Exception:
        log("  'Use password' not found — may already be on password form.")
    await asyncio.sleep(3)

    # Fill password
    pwd_count = await page.locator('input[type="password"]').count()
    if pwd_count == 0:
        log("  No password field found. Cannot continue.")
        return False

    await page.evaluate(
        """() => {
            const p = document.querySelectorAll('input[type="password"]');
            if (p.length) p[0].focus();
        }"""
    )
    await asyncio.sleep(0.3)
    await page.keyboard.type(password, delay=50)
    await asyncio.sleep(0.8)

    # Submit password
    await page.locator(
        'button[type="submit"]:not([disabled]):visible'
    ).first.click(timeout=5000)
    log("  Password submitted.")
    await asyncio.sleep(5)

    # Check for device verification (blocks automation)
    body = await page.evaluate("() => document.body.innerText?.substring(0, 500)")
    if "verify your device" in body.lower() or "new device" in body.lower():
        log("  Albertsons requires device verification (6-digit SMS code).")
        log("  Cannot auto-reauth — run with --login for manual intervention.")
        return False

    # Verify success
    current_url = page.url
    if "sign-in" in current_url.lower():
        log(f"  Still on sign-in page. Reauth may have failed.")
        return False

    log("  Reauth successful! Session restored.")
    return True


# ── Coupon loading and clipping ──────────────────────────────

async def load_all_coupons(page):
    """Click 'Load more' until no more coupons appear."""
    log("Loading all coupons...")
    prev = 0
    stable = 0
    max_attempts = 40

    for _ in range(max_attempts):
        count = await page.evaluate(
            "() => document.querySelectorAll('.coupon-card').length"
        )
        if count == prev:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
            prev = count
            log(f"  {count} coupons loaded...")

        clicked = await page.evaluate("""
            () => {
                const btn = [...document.querySelectorAll('button,a')]
                    .find(el => el.innerText.trim() === 'Load more');
                if (btn) { btn.scrollIntoView({behavior:'instant',block:'center'}); btn.click(); return true; }
                return false;
            }
        """)
        if not clicked:
            stable += 1
        await asyncio.sleep(1.2)

    final = await page.evaluate(
        "() => document.querySelectorAll('.coupon-card').length"
    )
    log(f"Total coupons loaded: {final}")
    return final


async def count_coupons(page):
    """Count coupons: needsClipping, alreadyClipped, activateOffers."""
    return await page.evaluate("""
        () => {
            const cards = [...document.querySelectorAll('.coupon-card')];
            let clip = 0, clipped = 0, activate = 0;
            cards.forEach(c => {
                if (c.innerText.includes('Activate')) activate++;
                else if (c.innerText.includes('Clip Coupon')) clip++;
                else if (c.innerText.includes('Clipped')) clipped++;
            });
            return {needsClipping: clip, alreadyClipped: clipped, activateOffers: activate, total: cards.length};
        }
    """)


async def clip_all_coupons(page):
    """Scroll and clip until no unclipped coupons remain. Two-pass."""
    initial = await count_coupons(page)
    log(
        f"Before: {initial['needsClipping']} to clip, "
        f"{initial['alreadyClipped']} already clipped, "
        f"{initial['activateOffers']} activate-only (skipped)"
    )

    if initial["needsClipping"] == 0:
        log("Nothing to clip!")
        return initial, 0

    max_rounds = 200
    viewport = page.viewport_size
    scroll_step = (viewport["height"] // 2) if viewport else 400

    # ── Pass 1: scroll-and-clip ──
    for _ in range(max_rounds):
        status = await count_coupons(page)
        if status["needsClipping"] == 0:
            break

        visible_count = await page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button')].filter(b => {
                    if (b.innerText.trim() !== 'Clip Coupon') return false;
                    // Skip activate-only offers (they spend loyalty points)
                    const card = b.closest('.coupon-card, [class*="cpn-flex"]');
                    if (card && card.innerText.includes('Activate')) return false;
                    const r = b.getBoundingClientRect();
                    return r.bottom > 0 && r.top < window.innerHeight;
                });
                return btns.length;
            }
        """)

        if visible_count > 0:
            log(f"  Clicking {visible_count} visible Clip Coupon buttons...")
            for _ in range(visible_count):
                try:
                    btn = page.locator(
                        "button:has-text('Clip Coupon'):visible"
                    ).first
                    await btn.scroll_into_view_if_needed(timeout=1000)
                    await btn.click(timeout=2000)
                    await asyncio.sleep(0.4)
                except Exception:
                    break
            await asyncio.sleep(2)
        else:
            await page.evaluate(f"window.scrollBy(0, {scroll_step})")
            await asyncio.sleep(1)

    final = await count_coupons(page)
    new_clips = final["alreadyClipped"] - initial["alreadyClipped"]

    # ── Pass 2: top-to-bottom sweep for stragglers ──
    if final["needsClipping"] > 0:
        log(f"  Second pass for remaining {final['needsClipping']}...")
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1)
        for _ in range(50):
            status = await count_coupons(page)
            if status["needsClipping"] == 0:
                break
            visible = await page.evaluate("""
                () => [...document.querySelectorAll('button')].filter(b => {
                    if (b.innerText.trim() !== 'Clip Coupon') return false;
                    const r = b.getBoundingClientRect();
                    return r.bottom > 0 && r.top < window.innerHeight;
                }).length
            """)
            if visible > 0:
                for _ in range(visible):
                    try:
                        btn = page.locator(
                            "button:has-text('Clip Coupon'):visible"
                        ).first
                        await btn.scroll_into_view_if_needed(timeout=1000)
                        await btn.click(timeout=2000)
                        await asyncio.sleep(0.3)
                    except Exception:
                        break
                await asyncio.sleep(1)
            else:
                await page.evaluate(f"window.scrollBy(0, {scroll_step})")
                await asyncio.sleep(0.8)

        final = await count_coupons(page)
        new_clips = final["alreadyClipped"] - initial["alreadyClipped"]

    log(
        f"After: {final['needsClipping']} remaining, "
        f"{final['alreadyClipped']} clipped, "
        f"{new_clips} newly clipped this run"
    )
    return final, new_clips


def is_logged_in(url: str) -> bool:
    """Check if we're on the authenticated coupons page."""
    url_lower = url.lower()
    if "foru-guest" in url_lower or "signin" in url_lower:
        return False
    if "loyalty/coupons-deals" in url_lower:
        return True
    return False


# ── Main ─────────────────────────────────────────────────────

async def main():
    force_login = "--login" in sys.argv

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # ── Step 1: Get a Chrome connection ──
        if await cdp_available():
            log("Connected to existing Chrome via CDP.")
        else:
            headless = False if force_login else True
            if not launch_chrome_with_cdp(headless=headless):
                log("Could not launch Chrome.")
                log(f"  Start Chrome with: --remote-debugging-port={CDP_PORT}")
                return 1
            log("Waiting for CDP endpoint...")
            if not await wait_for_cdp():
                log("Chrome launched but CDP never became available.")
                return 1

        # ── Step 2: Connect via CDP ──
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        log("CDP connected.")

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        # ── Step 3: Navigate to coupons ──
        await page.goto(COUPONS_URL, wait_until="domcontentloaded", timeout=30000)

        # Let Albertsons JS redirects settle
        stable_url = page.url
        for _ in range(6):
            await asyncio.sleep(1.5)
            new_url = page.url
            if new_url == stable_url:
                break
            stable_url = new_url

        # ── Session check / reauth ──
        if not is_logged_in(stable_url):
            log(f"Not signed in — currently at {stable_url[:80]}")

            if force_login:
                # Headed mode — wait for manual sign-in (10 min max)
                log("Waiting for manual sign-in (max 10 min)...")
                for i in range(200):
                    url = page.url
                    if is_logged_in(url):
                        log("Signed in!")
                        break
                    if i % 10 == 0:
                        log(f"  Waiting... (currently at {url[:80]})")
                    await asyncio.sleep(3)
                else:
                    log("Timed out waiting for sign-in.")
                    await browser.close()
                    return 1
            else:
                # Auto-reauth
                if not await auto_reauth(page):
                    log("Auto-reauth failed. Run with --login for manual intervention.")
                    await page.close()
                    await browser.close()
                    return 2

                # Navigate back to coupons and verify
                await page.goto(COUPONS_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                if not is_logged_in(page.url):
                    log(f"Reauth appeared to succeed but still not logged in.")
                    await page.close()
                    await browser.close()
                    return 2
                log("Session restored, proceeding to clip.")

        # ── Step 4: Clip coupons ──
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1)

        total = await load_all_coupons(page)
        if total == 0:
            log("No coupon cards found on page.")
            await page.close()
            await browser.close()
            return 1

        # Scroll back to top (load_all_coupons scrolled down)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1)

        final, new_clips = await clip_all_coupons(page)
        log(f"Done! {new_clips} coupons clipped this run.")

        await page.close()
        await browser.close()
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code or 0)
