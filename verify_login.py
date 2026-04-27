"""Login to verify the registered account works."""
import asyncio, json, os, sys, time

import account_store

_account = account_store.latest_account()
if _account is None:
    print("ERROR: no accounts found. Run register.py first.", file=sys.stderr)
    sys.exit(1)
creds = account_store.compat_dict(_account)
if creds.get("proxy_session"):
    os.environ["PROXY_SESSION"] = creds["proxy_session"]

from playwright.async_api import async_playwright
import tempmail
from proxy_helper import get_proxy, log_ip
from register import RECAPTCHA_SITEKEY, solve_recaptcha, inject_recaptcha_token

LOGIN_URL = "https://auth.gensee.ai/html/login?next=https%3A%2F%2Fwebapp.gensee.ai%2Fui%2F"


async def main():
    print(f"Login as: {creds['email']}")
    proxy = get_proxy()
    if proxy:
        print(f"[proxy] sticky session = {proxy.username.split('session-')[-1]}")
        log_ip(proxy)
    captcha_token = solve_recaptcha()
    launch_kwargs = {"headless": True}
    if proxy is not None:
        launch_kwargs["proxy"] = proxy.playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(1500)
        # Sign-in tab is default. Click email button.
        await page.click("#email-btn")
        await page.wait_for_selector("#email-input", state="visible")
        await page.fill("#email-input", creds["email"])
        await page.wait_for_function(
            "() => typeof grecaptcha !== 'undefined' && document.getElementById('g-recaptcha-response')"
        )
        await inject_recaptcha_token(page, captcha_token, widget_index=0)
        await page.wait_for_timeout(800)
        send_ts = time.time()
        await page.click("#send-code-btn")
        # Wait for the verification section to become visible
        await page.wait_for_function(
            """() => {
                const s = document.getElementById('verification-section');
                return s && !s.classList.contains('hidden');
            }""",
            timeout=20000,
        )
        otp = tempmail.wait_for_otp(creds["mail_tm_token"], since_ts=send_ts, timeout=180)
        print(f"  OTP: {otp}")
        await page.fill("#verification-code-input", otp)
        await page.click("#verify-code-btn")

        # Wait for redirect to webapp.gensee.ai
        for _ in range(60):
            await page.wait_for_timeout(500)
            if "webapp.gensee.ai" in page.url and "/signup" not in page.url and "/login" not in page.url:
                break
        await page.wait_for_timeout(3000)

        print(f"  Final URL: {page.url}")
        try:
            await page.screenshot(path="login_result.png", full_page=True, timeout=10000)
        except Exception as e:
            print(f"  (screenshot skipped: {e})")

        # Check user info
        try:
            user_info = await page.evaluate(
                """async () => {
                    const r = await fetch('/user/info', {credentials: 'include'});
                    return {status: r.status, body: await r.text()};
                }"""
            )
            print("  /user/info:", user_info)
        except Exception as e:
            print("  could not fetch user info:", e)

        await browser.close()


asyncio.run(main())
