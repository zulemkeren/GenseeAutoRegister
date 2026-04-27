"""Account list store.

Two files for clarity:
  accounts.json  - clean list, only the fields a human cares about.
  _meta.json     - tokens / sandbox IDs / proxy session, keyed by account no.

Scripts use add_account() / latest_account() / compat_dict() and don't need
to know how the data is split.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_DIR = Path(__file__).parent
ACCOUNTS_FILE = _DIR / "accounts.json"
META_FILE = _DIR / "_meta.json"
LEGACY_FILE = _DIR / "credentials.json"


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _migrate_legacy() -> None:
    """One-time: convert old credentials.json to the new two-file layout."""
    if not LEGACY_FILE.exists() or ACCOUNTS_FILE.exists():
        return
    old = _read(LEGACY_FILE, {})
    if not old.get("email"):
        return
    no = 1
    accounts = [{
        "no": no,
        "Email": old.get("email", ""),
        "Mail pass": old.get("mail_password", ""),
        "Name": f"{old.get('first_name', '')} {old.get('last_name', '')}".strip(),
        "Referral": old.get("referral", ""),
        "Created": time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(old.get("ts", time.time()))),
        "Status": "SUCCESS" if old.get("sandbox_id") else "INCOMPLETE",
    }]
    meta = {str(no): {
        "mail_tm_token": old.get("mail_tm_token"),
        "proxy_session": old.get("proxy_session"),
        "sandbox_id": old.get("sandbox_id"),
        "container_id": old.get("container_id"),
    }}
    _write(ACCOUNTS_FILE, accounts)
    _write(META_FILE, meta)


def load_accounts() -> list[dict]:
    _migrate_legacy()
    return _read(ACCOUNTS_FILE, [])


def load_meta() -> dict:
    return _read(META_FILE, {})


def save_accounts(accounts: list[dict]) -> None:
    _write(ACCOUNTS_FILE, accounts)


def save_meta(meta: dict) -> None:
    _write(META_FILE, meta)


def add_account(
    *,
    email: str,
    mail_password: str,
    mail_tm_token: str,
    first_name: str,
    last_name: str,
    referral: str,
    sandbox_id: str | None = None,
    container_id: str | None = None,
    proxy_session: str | None = None,
) -> dict:
    """Append a new account. Returns the public entry (with assigned `no`)."""
    accounts = load_accounts()
    no = max((a["no"] for a in accounts), default=0) + 1
    entry = {
        "no": no,
        "Email": email,
        "Mail pass": mail_password,
        "Name": f"{first_name} {last_name}",
        "Referral": referral,
        "Created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "Status": "SUCCESS" if sandbox_id else "INSTANCE FAILED",
    }
    accounts.append(entry)
    save_accounts(accounts)

    meta = load_meta()
    meta[str(no)] = {
        "mail_tm_token": mail_tm_token,
        "proxy_session": proxy_session,
        "sandbox_id": sandbox_id,
        "container_id": container_id,
    }
    save_meta(meta)
    return entry


def update_meta(no: int, **fields) -> None:
    """Update metadata fields for an existing account (e.g. attach a sandbox_id later)."""
    meta = load_meta()
    cur = meta.get(str(no), {})
    cur.update({k: v for k, v in fields.items() if v is not None})
    meta[str(no)] = cur
    save_meta(meta)


def latest_account() -> dict | None:
    accs = load_accounts()
    return accs[-1] if accs else None


def get_account(no: int) -> dict | None:
    for a in load_accounts():
        if a["no"] == no:
            return a
    return None


def compat_dict(account: dict) -> dict:
    """Return account merged with metadata, with field names downstream scripts expect."""
    meta = load_meta().get(str(account["no"]), {})
    name = account.get("Name", "")
    parts = name.split(" ", 1)
    return {
        "email": account.get("Email"),
        "mail_password": account.get("Mail pass"),
        "first_name": parts[0] if parts else "",
        "last_name": parts[1] if len(parts) > 1 else "",
        "referral": account.get("Referral"),
        "no": account.get("no"),
        # metadata
        "mail_tm_token": meta.get("mail_tm_token"),
        "proxy_session": meta.get("proxy_session"),
        "sandbox_id": meta.get("sandbox_id"),
        "container_id": meta.get("container_id"),
    }


if __name__ == "__main__":
    accs = load_accounts()
    if not accs:
        print("No accounts yet. Run register.py first.")
    else:
        for a in accs:
            print(f"=== Account #{a['no']} ===")
            for k in ("Email", "Mail pass", "Name", "Referral", "Created", "Status"):
                print(f"  {k:<10}: {a.get(k, '')}")
            print()
