"""Residential proxy helper.

Builds a proxy URL with a per-process sticky session so the entire signup +
keep-alive flow uses ONE residential IP (jumping countries mid-session would
trip fraud heuristics on most SaaS auth systems).

Reads from .env:
    PROXY_HOST     host:port            (required to enable proxy)
    PROXY_USER     base username        (required)
    PROXY_PASS     password             (required)
    PROXY_REGION   country code, e.g. 'us'   (optional)
    PROXY_SESSION  fixed session id     (optional; otherwise random per run)
"""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ProxyConfig:
    host: str            # "ip:port"
    username: str        # full user string with zone/region/session modifiers
    password: str

    @property
    def url(self) -> str:
        return f"http://{self.username}:{self.password}@{self.host}"

    @property
    def playwright(self) -> dict:
        return {
            "server": f"http://{self.host}",
            "username": self.username,
            "password": self.password,
        }

    @property
    def requests(self) -> dict:
        u = self.url
        return {"http": u, "https": u}


_DEFAULT_SESSION = os.environ.get("PROXY_SESSION") or secrets.token_hex(6)


def get_proxy(session_id: str | None = None) -> ProxyConfig | None:
    """Return a proxy config. If session_id is provided, pin to that residential IP;
    otherwise reuse the per-process default (or PROXY_SESSION env var if set)."""
    host = os.environ.get("PROXY_HOST")
    user = os.environ.get("PROXY_USER")
    pw = os.environ.get("PROXY_PASS")
    if not (host and user and pw):
        return None
    region = (os.environ.get("PROXY_REGION") or "").strip().lower()
    sess = session_id or _DEFAULT_SESSION

    parts = [user]
    if region:
        parts.append(f"region-{region}")
    parts.append(f"session-{sess}")
    full_user = "-".join(parts)
    return ProxyConfig(host=host, username=full_user, password=pw)


def verify_ip(proxy: ProxyConfig | None) -> dict:
    """Hit ipinfo.io through the proxy (or directly if no proxy) and return geo info."""
    import requests
    proxies = proxy.requests if proxy else None
    try:
        r = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=20)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def log_ip(proxy: ProxyConfig | None) -> None:
    """Print external IP info to stderr."""
    info = verify_ip(proxy)
    sys.stderr.write(f"[proxy] external IP: {info.get('ip')!r} "
                     f"city={info.get('city')!r} country={info.get('country')!r} "
                     f"org={info.get('org', '')[:60]!r}\n")
    sys.stderr.flush()


if __name__ == "__main__":
    p = get_proxy()
    if not p:
        print("No proxy configured (PROXY_HOST / USER / PASS missing).")
    else:
        print("Username:", p.username)
        print("Server  :", p.host)
        log_ip(p)
