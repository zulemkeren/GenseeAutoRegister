"""mail.tm tempmail client for receiving OTP."""
import re
import secrets
import string
import time

import requests

from proxy_helper import get_proxy

API = "https://api.mail.tm"
HEADERS = {"Accept": "application/ld+json", "Content-Type": "application/json"}


def _proxies():
    """Lazy proxy lookup so scripts can change PROXY_SESSION before first request."""
    p = get_proxy()
    return p.requests if p else None


def _rand(n: int = 12) -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def get_domain() -> str:
    r = requests.get(f"{API}/domains?page=1", headers=HEADERS, proxies=_proxies(), timeout=30)
    r.raise_for_status()
    domains = r.json().get("hydra:member") or r.json().get("member") or []
    if not domains:
        raise RuntimeError("No mail.tm domains available")
    return domains[0]["domain"]


def create_account() -> dict:
    """Returns {address, password, token}."""
    domain = get_domain()
    address = f"{_rand(10)}@{domain}"
    password = _rand(16)
    r = requests.post(
        f"{API}/accounts",
        headers=HEADERS,
        json={"address": address, "password": password},
        proxies=_proxies(),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"create_account failed: {r.status_code} {r.text}")
    r2 = requests.post(
        f"{API}/token",
        headers=HEADERS,
        json={"address": address, "password": password},
        proxies=_proxies(),
        timeout=30,
    )
    r2.raise_for_status()
    token = r2.json()["token"]
    return {"address": address, "password": password, "token": token}


def list_messages(token: str) -> list:
    r = requests.get(
        f"{API}/messages",
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        proxies=_proxies(),
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    return j.get("hydra:member") or j.get("member") or []


def get_message(token: str, msg_id: str) -> dict:
    r = requests.get(
        f"{API}/messages/{msg_id}",
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        proxies=_proxies(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


CODE_RE = re.compile(r"\b(\d{6})\b")
# Only consider 6-digit numbers that are NOT preceded by '#' (CSS hex colors).
NON_HEX_CODE_RE = re.compile(r"(?<!#)\b(\d{6})\b")


def _strip_html(html: str) -> str:
    """Remove <style>...</style> blocks and tags so CSS colors don't pollute OTP scan."""
    no_style = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    no_tags = re.sub(r"<[^>]+>", " ", no_style)
    return no_tags


def extract_otp(message: dict) -> str | None:
    """Extract a 6-digit OTP from a mail.tm message, ignoring CSS hex colors."""
    html_parts = " ".join(message.get("html", []) or [])
    text = message.get("text", "") or ""
    intro = message.get("intro", "") or ""
    subject = message.get("subject", "") or ""

    cleaned_html = _strip_html(html_parts)
    haystack = " | ".join([subject, intro, text, cleaned_html])

    # Prefer a code adjacent to keywords like "code", "verification"
    keyword_re = re.compile(
        r"(?:code|verification|otp|pin)[^0-9]{0,40}(\d{6})|(\d{6})[^0-9]{0,40}(?:code|verification|otp|pin)",
        re.IGNORECASE,
    )
    m = keyword_re.search(haystack)
    if m:
        return m.group(1) or m.group(2)

    m = NON_HEX_CODE_RE.search(haystack)
    if m:
        return m.group(1)
    return None


def _parse_iso(s: str) -> float:
    if not s:
        return 0.0
    try:
        from datetime import datetime
        # mail.tm uses ISO 8601 with timezone, e.g. "2026-04-25T18:19:01+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def wait_for_otp(token: str, since_ts: float, timeout: int = 180, poll: float = 3.0) -> str:
    """Poll inbox until a 6-digit code arrives in a message NEWER than since_ts."""
    deadline = time.time() + timeout
    seen = set()
    # Pre-mark messages that already exist so we never return their codes
    try:
        for m in list_messages(token):
            ts = _parse_iso(m.get("createdAt", ""))
            if ts and ts < since_ts:
                seen.add(m.get("id"))
    except Exception:
        pass

    while time.time() < deadline:
        try:
            msgs = list_messages(token)
        except Exception as e:
            print(f"  [tempmail] list error: {e}")
            time.sleep(poll)
            continue
        for m in msgs:
            mid = m.get("id")
            if mid in seen:
                continue
            ts = _parse_iso(m.get("createdAt", ""))
            if ts and ts < since_ts - 5:  # 5s slack for clock skew
                seen.add(mid)
                continue
            seen.add(mid)
            try:
                full = get_message(token, mid)
            except Exception:
                continue
            code = extract_otp(full)
            if code:
                return code
        time.sleep(poll)
    raise TimeoutError(f"No OTP received within {timeout}s")


if __name__ == "__main__":
    acc = create_account()
    print("Account:", acc["address"])
    print("Token:", acc["token"][:30] + "...")
    print("Now waiting for OTP (testing — send something manually)...")
