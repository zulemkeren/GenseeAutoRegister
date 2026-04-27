"""Login and check final account/referral/tier status."""
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
from register import solve_recaptcha, inject_recaptcha_token

LOGIN_URL = "https://auth.gensee.ai/html/login?next=https%3A%2F%2Fwebapp.gensee.ai%2Fui%2F"


def log(m):
    sys.stderr.write(m + "\n"); sys.stderr.flush()


async def login(page, captcha_token):
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(1500)
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
    await page.wait_for_function(
        "() => { const s = document.getElementById('verification-section'); return s && !s.classList.contains('hidden'); }",
        timeout=20000,
    )
    otp = tempmail.wait_for_otp(creds["mail_tm_token"], since_ts=send_ts, timeout=180)
    log(f"OTP: {otp}")
    await page.fill("#verification-code-input", otp)
    await page.click("#verify-code-btn")
    for _ in range(60):
        await page.wait_for_timeout(500)
        if "webapp.gensee.ai" in page.url and "/login" not in page.url:
            break
    await page.wait_for_timeout(4000)


async def call(page, method, path, body=None):
    body_js = (
        f"body: JSON.stringify({json.dumps(body)}), headers: {{'Content-Type':'application/json'}},"
        if body is not None
        else ""
    )
    return await page.evaluate(
        f"""async () => {{
            const r = await fetch('{path}', {{method:'{method}', credentials:'include', {body_js}}});
            const txt = await r.text();
            return {{status: r.status, body: txt}};
        }}"""
    )


async def main():
    proxy = get_proxy()
    if proxy:
        log(f"[proxy] sticky session = {proxy.username.split('session-')[-1]}")
        log_ip(proxy)
    captcha_token = solve_recaptcha()
    launch_kwargs = {"headless": True}
    if proxy is not None:
        launch_kwargs["proxy"] = proxy.playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await login(page, captcha_token)
        log(f"At {page.url}\n")

        endpoints = [
            "/user/info",
            "/user/profile",
            "/user/account",
            "/user/promo-task/status",
            "/user/referral-code",
            "/user/can-redeem-referral",
            "/subscription/info",
            "/subscription/onboarding",
            "/coupon/available",
            "/billing/history",
            "/notifications/unread-count",
        ]
        for path in endpoints:
            r = await call(page, "GET", path)
            short_body = r["body"][:600].encode("ascii", "replace").decode()
            log(f"{path:42s} -> {r['status']}: {short_body}")

        # Try POST /subscription/start-trial with the referral code
        log("\nTrying POST /subscription/start-trial with referral W3EWKVAJ...")
        r = await call(page, "POST", "/subscription/start-trial", {"referral_code": "W3EWKVAJ"})
        log(f"  -> {r['status']}: {r['body'][:400].encode('ascii','replace').decode()}")

        # Re-check
        r = await call(page, "GET", "/user/info")
        log(f"\n/user/info AFTER: {r['body'][:600]}")
        r = await call(page, "GET", "/subscription/info")
        log(f"/subscription/info AFTER: {r['body'][:600]}")

        await browser.close()


asyncio.run(main())
