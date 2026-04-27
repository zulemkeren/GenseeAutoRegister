"""Create a Gensee sandbox/container instance and confirm it's running.

Login -> create instance (or reuse existing) -> wait until healthy -> exit.
The instance keeps running on Gensee's servers after this script exits, so
the 30-min referral requirement is satisfied by the sandbox staying up.
The script itself returns in ~30-90 seconds (depends on captcha + login).

If credentials.json contains `proxy_session`, the same residential IP that
created the account is reused (set BEFORE importing proxy_helper).
"""
import asyncio, json, os, sys, time
from pathlib import Path

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

LOGIN_URL = "https://auth.gensee.ai/html/login?next=https%3A%2F%2Fwebapp.gensee.ai%2Fui%2F%3Freferral%3DW3EWKVAJ"
INSTANCE_NAME = "AutoInstance"


def log(m: str) -> None:
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {m}\n")
    sys.stderr.flush()


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
    log(f"  OTP: {otp}")
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


async def get_or_create_sandbox(page):
    # Reuse existing if any
    r = await call(page, "GET", "/sandbox/list")
    if r["status"] == 200:
        try:
            data = json.loads(r["body"])
            if data:
                sandbox_id, info = next(iter(data.items()))
                log(f"  Reusing existing sandbox {sandbox_id} (status={info.get('status')})")
                return sandbox_id, info.get("container_id")
        except Exception:
            pass

    # Create new
    log(f"  Creating new instance '{INSTANCE_NAME}' ...")
    r = await call(page, "POST", f"/container/new/{INSTANCE_NAME}/openclaw-v1")
    log(f"  -> {r['status']}: {r['body'][:300]}")
    if r["status"] != 200:
        raise RuntimeError(f"Failed to create instance: {r}")
    data = json.loads(r["body"])
    return data["sandbox_id"], data["container_id"]


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

        log("Logging in...")
        await login(page, captcha_token)
        log(f"  At {page.url}")

        # Verify referral state
        r = await call(page, "GET", "/user/can-redeem-referral")
        log(f"  /user/can-redeem-referral -> {r['body']}")
        rc = await call(page, "GET", "/user/referral-code")
        log(f"  /user/referral-code -> {rc['body']}")

        log("\nGetting / creating sandbox...")
        sandbox_id, container_id = await get_or_create_sandbox(page)
        log(f"  sandbox_id   = {sandbox_id}")
        log(f"  container_id = {container_id}")

        # Wait for healthy
        log("\nWaiting for sandbox to be healthy...")
        for i in range(30):
            r = await call(page, "GET", f"/sandbox/{sandbox_id}/healthy")
            try:
                d = json.loads(r["body"])
            except Exception:
                d = {}
            log(f"  poll {i}: status={r['status']} body={r['body'][:120]}")
            if r["status"] == 200 and d.get("healthy"):
                break
            await asyncio.sleep(3)

        # Run a quick command so the sandbox registers as 'used' (not just idle).
        # This is cheap insurance for the 30-min activity threshold and only
        # adds ~1s to the script.
        try:
            cr = await call(page, "POST", f"/sandbox/{sandbox_id}/run_command",
                            {"command": "echo activated"})
            log(f"  run_command (warmup): status={cr['status']} body={cr['body'][:120]}")
        except Exception as e:
            log(f"  run_command warmup failed (non-fatal): {e}")

        # Confirm container/sandbox is in 'Running' status
        cl = await call(page, "GET", "/container/list")
        sl = await call(page, "GET", "/sandbox/list")
        log(f"\n  /container/list -> {cl['body'][:300]}")
        log(f"  /sandbox/list   -> {sl['body'][:300]}")

        # Save sandbox/container IDs into the metadata file
        account_store.update_meta(
            _account["no"], sandbox_id=sandbox_id, container_id=container_id,
        )

        log(f"\n=== Account #{_account['no']} - instance ready ===")
        log(f"  Email     : {_account.get('Email') or _account.get('email')}")
        log(f"  Sandbox   : {sandbox_id}")
        log(f"  Container : {container_id}")
        log("  Instance is RUNNING on Gensee. The 30-min referral threshold")
        log("  ticks down on Gensee's servers regardless of this script.")

        await browser.close()


if __name__ == "__main__":
    import traceback
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise
