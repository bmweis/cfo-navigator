"""Lightweight email helper for outbound notifications (contact form, tool
submissions, welcome emails, password resets, warm intros), sent via the
Gmail REST API.

Why the Gmail API and not SMTP: Railway's Hobby plan blocks outbound SMTP
ports (25/465/587) — unblocking them is a Pro-plan feature. The Gmail API
sends over HTTPS (port 443), which is unrestricted on every plan, and it
reuses the exact same Google Cloud OAuth client + refresh token as the Drive
backup (linklib/backup.py) — just with the ``gmail.send`` scope granted
alongside ``drive.file`` when the refresh token is minted.

Reads configuration from environment variables — all optional. If Google
OAuth is not configured, every send_*() function here returns False and the
record is still stored in the DB (no error raised) — callers should always
save the record first and treat email as best-effort on top of that. Errors
from a configured-but-failing send propagate as exceptions — callers should
route those through webapp.app._send_email_safely rather than a bare
try/except, so a failure lands in the email_failures table (surfaced on
/admin/email-failures) and not just a stdout print nobody's watching.

Required env vars to enable sending (same three as the Drive backup):
    GOOGLE_OAUTH_CLIENT_ID       Google Cloud OAuth client ID
    GOOGLE_OAUTH_CLIENT_SECRET   Google Cloud OAuth client secret
    GOOGLE_OAUTH_REFRESH_TOKEN   OAuth refresh token minted with the
                                 gmail.send (+ drive.file) scopes

Recommended:
    LINKLIB_FROM_EMAIL     Sender address for the From header. Must be the
                           Workspace mailbox that authorized the refresh
                           token (or one of its configured send-as aliases) —
                           Gmail rewrites the From address to the
                           authenticated account otherwise, though it keeps
                           the display name either way.

Optional:
    LINKLIB_CONTACT_EMAIL  Where contact-form submissions are emailed
                           (defaults to LINKLIB_FROM_EMAIL)
"""
from __future__ import annotations

import base64
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

_FROM_EMAIL = os.environ.get("LINKLIB_FROM_EMAIL", "")


def is_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        and os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    )


def default_notify_email() -> str:
    """Where to send notifications when no more specific address is set."""
    return _FROM_EMAIL


def _access_token() -> str:
    # Same refresh-token exchange as linklib/backup.py — kept local so each
    # module stays self-contained (the repo's convention for this plumbing).
    r = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _send(msg: MIMEMultipart) -> None:
    """Send a fully-built MIME message via the Gmail API. Recipients are
    taken from the message's To/Cc headers. Raises on HTTP errors."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    r = requests.post(
        _SEND_URL,
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"raw": raw},
        timeout=30,
    )
    r.raise_for_status()


def send_notification_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text notification to Brian (e.g. a new contact-form
    submission). Returns True if sent, False if Google OAuth is not
    configured (graceful no-op) — callers should always save the record
    first and treat this as best-effort on top of that. Raises on API errors.
    """
    if not is_configured():
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if _FROM_EMAIL:
        msg["From"] = _FROM_EMAIL
    msg["To"] = to
    msg.attach(MIMEText(body, "plain"))
    _send(msg)
    return True


def send_welcome_email(to: str, username: str, temp_password: str, login_url: str) -> bool:
    """Send a new member their temporary password and a link to sign in.
    Returns True if sent, False if Google OAuth is not configured (graceful
    no-op) — the account still exists either way, so callers should tell
    the admin plainly when this comes back False (share the password
    another way). Raises on API errors.
    """
    if not is_configured():
        return False

    subject = "Your bmweis.com account"
    body = f"""\
Hi {username},

An account has been created for you at bmweis.com.

Username: {username}
Temporary password: {temp_password}

Sign in here, then use "Forgot your password?" on that page any time you'd \
like to set your own password:
{login_url}

Best,
Brian Weisberg
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if _FROM_EMAIL:
        msg["From"] = _FROM_EMAIL
    msg["To"] = to
    msg.attach(MIMEText(body, "plain"))
    _send(msg)
    return True


def send_password_reset_email(to: str, username: str, reset_url: str) -> bool:
    """Send a self-service password reset link. Returns True if sent, False
    if Google OAuth is not configured (graceful no-op) — the reset request
    is still recorded either way, so Brian can reset it by hand from
    /admin/users if the email never arrives. Raises on API errors.
    """
    if not is_configured():
        return False

    subject = "Reset your bmweis.com password"
    body = f"""\
Hi {username},

Someone (hopefully you) requested a password reset for your bmweis.com account.

Reset your password here — this link expires in 1 hour:
{reset_url}

If you didn't request this, you can ignore this email; your password won't change.

Best,
Brian Weisberg
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if _FROM_EMAIL:
        msg["From"] = _FROM_EMAIL
    msg["To"] = to
    msg.attach(MIMEText(body, "plain"))
    _send(msg)
    return True


def send_warm_intro_email(
    to: str,
    vendor_contact_name: str,
    cc: str,
    tool_name: str,
    requester_name: str,
    requester_email: str,
    requester_company: str,
    requester_company_size: str,
) -> bool:
    """Send a warm-intro email connecting a CFO Toolbox member with a named
    vendor contact — an actual introduction (addressed to the vendor contact
    by name, naming the requester), not a lead-notification form dump.

    Sent with the requesting member cc'd, so the intro lands as a real
    three-way thread the two of them can take from there. Reply-To points at
    the requester, so a vendor hitting "reply" (not "reply all") still
    reaches a real person. Set LINKLIB_FROM_EMAIL to the sending mailbox on
    the secured domain (e.g. hello@bmweis.com).

    Returns True if sent, False if Google OAuth is not configured (graceful
    no-op). Raises on API errors so the caller can log or alert.
    """
    if not is_configured():
        return False

    vendor_greeting = vendor_contact_name.strip() or "there"
    subject = f"Introduction: {requester_name} <> {tool_name}"

    body = f"""\
Hi {vendor_greeting},

I'd like to introduce you to {requester_name} at {requester_company} ({requester_company_size} \
employees) — a member of the CFO Toolbox community I run at bmweis.com. They came across \
{tool_name} in the directory and asked for a warm intro to your team.

{requester_name}
{requester_email}
{requester_company} — {requester_company_size} employees

I've cc'd {requester_name} directly so the two of you can take it from here.

Best,
Brian Weisberg
bmweis.com / CFO Toolbox
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if _FROM_EMAIL:
        msg["From"] = f"CFO Toolbox (no-reply) <{_FROM_EMAIL}>"
    msg["To"]      = to
    msg["Cc"]      = cc
    msg["Reply-To"] = requester_email
    msg.attach(MIMEText(body, "plain"))
    _send(msg)
    return True
