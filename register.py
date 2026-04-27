"""Automate Gensee.ai signup using mail.tm (OTP) + 2captcha (reCAPTCHA v2)."""
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path


def log(msg: str):
    """Always-flushed log to stderr (Windows stdout-redirect-buffering safe)."""
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()

from dotenv import load_dotenv
from faker import Faker
from playwright.async_api import async_playwright
from twocaptcha import TwoCaptcha

import account_store
import tempmail
from proxy_helper import get_proxy, log_ip

load_dotenv()

# Locale for generated names. en_US matches a US residential proxy.
# Change to e.g. 'id_ID' for Indonesian, 'en_GB' for British, etc.
NAME_LOCALE = os.environ.get("NAME_LOCALE", "en_US")
_fake = Faker(NAME_LOCALE)

TWOCAPTCHA_KEY = os.environ["TWOCAPTCHA_API_KEY"]
REFERRAL = os.environ.get("REFERRAL_CODE", "W3EWKVAJ")

SIGNUP_URL = (
    "https://auth.gensee.ai/html/signup"
    f"?next=https%3A%2F%2Fwebapp.gensee.ai%2Fui%2F%3Freferral%3D{REFERRAL}"
)
RECAPTCHA_SITEKEY = "6Le2LJ8rAAAAABUmhUAQB0UO8lnMnaddjieC1xN6"
RECAPTCHA_PAGE_URL = "https://auth.gensee.ai/html/signup"

def _make_name() -> tuple[str, str]:
    """Return (first_name, last_name) - gender-consistent realistic name."""
    gender = random.choice(["male", "female"])
    if gender == "male":
        first = _fake.first_name_male()
    else:
        first = _fake.first_name_female()
    last = _fake.last_name()
    return first, last


def solve_recaptcha() -> str:
    """Solve reCAPTCHA v2 using 2captcha. Returns g-recaptcha-response token."""
    import sys, traceback
    def err(msg):
        sys.stderr.write(msg + "\n"); sys.stderr.flush()
    err(f"[2captcha] Submitting reCAPTCHA (sitekey={RECAPTCHA_SITEKEY[:12]}...)")
    try:
        solver = TwoCaptcha(TWOCAPTCHA_KEY, defaultTimeout=300, pollingInterval=5)
        err("[2captcha] Solver created, calling recaptcha()...")
        result = solver.recaptcha(sitekey=RECAPTCHA_SITEKEY, url=RECAPTCHA_PAGE_URL)
        token = result["code"]
        err(f"[2captcha] Got token (len={len(token)}) id={result.get('captchaId')}")
        return token
    except BaseException as e:
        err(f"[2captcha] EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
        raise


async def inject_recaptcha_token(page, token: str, widget_index: int = 1):
    """Inject token into the page so grecaptcha.getResponse(widget) returns it."""
    await page.evaluate(
        """([token, idx]) => {
            // Fill all g-recaptcha-response textareas
            document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response, #g-recaptcha-response-1')
                .forEach(t => {
                    t.style.display = '';
                    t.value = token;
                });
            // Override grecaptcha.getResponse so the page reads our token
            if (window.grecaptcha) {
                const orig = window.grecaptcha.getResponse;
                window.grecaptcha.getResponse = function(widgetId) { return token; };
            }
        }""",
        [token, widget_index],
    )


async def fill_survey(page):
    """Answer dynamic survey questions (first option for choice, default text otherwise)."""
    # Wait for survey to render
    try:
        await page.wait_for_selector("#dynamic-survey-questions", timeout=15000)
    except Exception:
        return
    await page.wait_for_timeout(1500)

    # Click the first radio of every survey-question group
    questions = await page.query_selector_all(".survey-question, #dynamic-survey-questions > div")
    print(f"[survey] Found {len(questions)} question blocks")

    for q in questions:
        # Try radios first
        radios = await q.query_selector_all('input[type="radio"]')
        if radios:
            # Pick a non-"other" radio if possible (avoid forcing follow-up text)
            picked = None
            for r in radios:
                val = (await r.get_attribute("value")) or ""
                if "other" not in val.lower():
                    picked = r
                    break
            if picked is None:
                picked = radios[0]
            try:
                await picked.check(force=True)
            except Exception:
                await picked.click()
            continue

        # Checkboxes — tick first
        checks = await q.query_selector_all('input[type="checkbox"]')
        if checks:
            try:
                await checks[0].check(force=True)
            except Exception:
                await checks[0].click()
            continue

        # Text inputs (open-ended)
        text_inputs = await q.query_selector_all('input[type="text"], textarea')
        for t in text_inputs:
            tid = (await t.get_attribute("id")) or ""
            if tid in ("signup-first-name", "signup-last-name"):
                continue
            await t.fill("Personal exploration and learning")


async def main(mail, first, last, captcha_token, proxy):
    out_dir = Path(__file__).parent

    launch_kwargs = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    context_kwargs = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 900},
    }
    if proxy is not None:
        # Browser-level proxy. New context inherits it.
        launch_kwargs["proxy"] = proxy.playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        ctx = await browser.new_context(**context_kwargs)
        page = await ctx.new_page()

        # Capture network calls for diagnostics
        net = []

        def _on_resp(res):
            if "/login/email" in res.url or "/agreement" in res.url or "/survey" in res.url:
                net.append((res.status, res.url))
                log(f"  [net] {res.status} {res.url}")

        page.on("response", _on_resp)

        log("[3/7] Loading signup page...")
        await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2000)

        await page.wait_for_selector("#create-email-btn", timeout=15000)
        await page.click("#create-email-btn")
        await page.wait_for_selector("#signup-email-input", state="visible", timeout=10000)
        await page.fill("#signup-email-input", mail["address"])
        await page.wait_for_timeout(500)

        await page.wait_for_function(
            """() => typeof grecaptcha !== 'undefined' && document.getElementById('g-recaptcha-response-1')""",
            timeout=20000,
        )

        await inject_recaptcha_token(page, captcha_token, widget_index=1)
        await page.wait_for_timeout(800)

        log("[4/7] Sending verification code...")
        send_ts = time.time()
        await page.click("#signup-send-code-btn")

        try:
            await page.wait_for_selector("#signup-verification-section:not(.hidden)", timeout=20000)
        except Exception:
            status = await page.text_content("#signup-email-auth-status") or ""
            try:
                await page.screenshot(path=str(out_dir / "send_code_fail.png"), full_page=True, timeout=15000)
            except Exception:
                pass
            raise RuntimeError(f"Send-code failed. Status: {status!r}")
        log("      Code requested OK, polling tempmail...")

        otp = tempmail.wait_for_otp(mail["token"], since_ts=send_ts, timeout=240, poll=4.0)
        log(f"      OTP received: {otp}")

        await page.fill("#signup-verification-code-input", otp)
        await page.click("#signup-verify-code-btn")
        log("      Clicked verify, awaiting response...")

        # Wait for either survey section to appear OR error to show
        try:
            await page.wait_for_selector(
                "#survey-section:not(.hidden), #signup-email-auth-status:not(.hidden)",
                timeout=15000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        # Check what state we're in
        survey_visible = await page.evaluate(
            """() => {
                const s = document.getElementById('survey-section');
                return s && !s.classList.contains('hidden');
            }"""
        )
        status_text = (await page.text_content("#signup-email-auth-status")) or ""
        log(f"      survey_visible={survey_visible} status={status_text!r}")
        try:
            await page.screenshot(path=str(out_dir / "after_verify.png"), full_page=True, timeout=15000)
        except Exception:
            pass

        if not survey_visible:
            log(f"      Network: {net[-5:]}")
            raise RuntimeError(
                f"OTP verify did not advance to survey. Status: {status_text!r}"
            )

        log("[5/7] Filling survey & profile...")
        await page.fill("#signup-first-name", first)
        await page.fill("#signup-last-name", last)
        await fill_survey(page)

        await page.check("#terms-checkbox", force=True)
        await page.wait_for_timeout(500)

        await page.wait_for_function(
            """() => !document.getElementById('complete-signup-btn').disabled""",
            timeout=15000,
        )
        try:
            await page.screenshot(path=str(out_dir / "before_submit.png"), full_page=True, timeout=15000)
        except Exception:
            pass
        await page.click("#complete-signup-btn")

        log("[6/7] Submitting signup...")
        success = False
        for _ in range(40):
            await page.wait_for_timeout(500)
            cur = page.url
            status = (await page.text_content("#agreement-status")) or ""
            if "webapp.gensee.ai" in cur and "/signup" not in cur:
                success = True
                break
            if any(k in status.lower() for k in ("complete", "success", "redirect")):
                success = True
                break
            if "error" in status.lower():
                try:
                    await page.screenshot(path=str(out_dir / "submit_fail.png"), full_page=True, timeout=15000)
                except Exception:
                    pass
                raise RuntimeError(f"Signup failed: {status}")

        try:
            await page.screenshot(path=str(out_dir / "result.png"), full_page=True, timeout=15000)
        except Exception:
            pass

        # ----- [7/7] Create instance using the same browser session -----
        # We're already logged in - the auth cookies are set. No need to
        # re-login (which would burn another captcha solve and add 30-60s).
        sandbox_id = container_id = None
        if success:
            log("[7/7] Creating instance in same session...")

            # ALWAYS navigate to webapp.gensee.ai. After signup the page can
            # be on auth.gensee.ai (signup form still showing 'Redirecting...')
            # - hitting /container/new from there returns 404 because the
            # endpoint lives on webapp.gensee.ai.
            authenticated = False
            for attempt in range(3):
                try:
                    await page.goto(
                        "https://webapp.gensee.ai/ui/?referral=" + REFERRAL,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    await page.wait_for_timeout(2500)
                except Exception as e:
                    log(f"      Nav attempt {attempt + 1} error: {e}")
                    await page.wait_for_timeout(3000)
                    continue

                # Verify auth cookies are scoped to webapp.gensee.ai
                ui = await page.evaluate(
                    """async () => {
                        const r = await fetch('/user/info', {credentials:'include'});
                        return {status: r.status, body: (await r.text()).slice(0,160)};
                    }"""
                )
                if ui["status"] == 200:
                    log(f"      Authenticated on webapp (URL: {page.url})")
                    authenticated = True
                    break
                log(f"      /user/info -> {ui['status']} (attempt {attempt + 1}); retrying...")
                await page.wait_for_timeout(4000)

            if not authenticated:
                log("      Auth not established on webapp.gensee.ai - skipping instance creation")

            try:
                if not authenticated:
                    raise RuntimeError("not authenticated on webapp")
                cr = await page.evaluate(
                    """async (name) => {
                        const r = await fetch(`/container/new/${encodeURIComponent(name)}/openclaw-v1`, {
                            method: 'POST', credentials: 'include',
                        });
                        return {status: r.status, body: await r.text()};
                    }""",
                    "AutoInstance",
                )
                log(f"      POST /container/new -> {cr['status']}: {cr['body'][:200]}")
                if cr["status"] == 200:
                    data = json.loads(cr["body"])
                    sandbox_id = data.get("sandbox_id")
                    container_id = data.get("container_id")

                    # Poll healthy
                    for i in range(20):
                        hr = await page.evaluate(
                            """async (sid) => {
                                const r = await fetch(`/sandbox/${sid}/healthy`, {credentials:'include'});
                                return {status: r.status, body: await r.text()};
                            }""",
                            sandbox_id,
                        )
                        try:
                            hd = json.loads(hr["body"])
                        except Exception:
                            hd = {}
                        if hr["status"] == 200 and hd.get("healthy"):
                            log(f"      Sandbox {sandbox_id} is HEALTHY")
                            break
                        await page.wait_for_timeout(3000)

                    # Warmup command so the sandbox registers as 'used'
                    try:
                        wr = await page.evaluate(
                            """async (sid) => {
                                const r = await fetch(`/sandbox/${sid}/run_command`, {
                                    method: 'POST', credentials: 'include',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({command: 'echo activated'}),
                                });
                                return {status: r.status, body: await r.text()};
                            }""",
                            sandbox_id,
                        )
                        log(f"      run_command (warmup): {wr['status']} {wr['body'][:120]}")
                    except Exception as e:
                        log(f"      warmup failed (non-fatal): {e}")
            except Exception as e:
                log(f"      Instance creation FAILED: {e}")

        proxy_session = None
        if proxy is not None:
            proxy_session = proxy.username.split("session-")[-1]

        entry = account_store.add_account(
            email=mail["address"],
            mail_password=mail["password"],
            mail_tm_token=mail["token"],
            first_name=first,
            last_name=last,
            referral=REFERRAL,
            sandbox_id=sandbox_id,
            container_id=container_id,
            proxy_session=proxy_session,
        )
        log(f"\n=== Account #{entry['no']} ===")
        log(f"  Email     : {entry['Email']}")
        log(f"  Mail pass : {entry['Mail pass']}")
        log(f"  Name      : {entry['Name']}")
        log(f"  Referral  : {entry['Referral']}")
        log(f"  Created   : {entry['Created']}")
        log(f"  Status    : {entry['Status']}")
        log(f"  Saved to  : {account_store.ACCOUNTS_FILE}")

        await browser.close()


def _prepare():
    log("=" * 60)
    log("Gensee.ai automated registration")
    log("=" * 60)

    proxy = get_proxy()
    if proxy:
        log(f"[proxy] sticky session = {proxy.username.split('session-')[-1]}")
        log_ip(proxy)
    else:
        log("[proxy] no proxy configured - using direct connection")

    log("[1/7] Creating tempmail account...")
    mail = tempmail.create_account()
    log(f"      Email: {mail['address']}")

    first, last = _make_name()
    log(f"      Name : {first} {last}  (locale={NAME_LOCALE})")

    log("[2/7] Solving reCAPTCHA via 2captcha (30-90s typical)...")
    captcha_token = solve_recaptcha()
    return mail, first, last, captcha_token, proxy


if __name__ == "__main__":
    import traceback
    try:
        mail, first, last, captcha_token, proxy = _prepare()
        asyncio.run(main(mail, first, last, captcha_token, proxy))
    except Exception:
        traceback.print_exc()
        raise
