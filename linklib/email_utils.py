"""Lightweight email helper for outbound notifications (contact form, warm
intros, etc.).

Reads configuration from environment variables — all optional. If SMTP is not
configured, send_notification_email()/send_warm_intro_email() return False
and the record is still stored in the DB (no error raised) — callers should
always save the record first and treat email as best-effort on top of that.

Required env vars to enable sending:
    LINKLIB_SMTP_HOST     e.g. smtp.gmail.com
    LINKLIB_SMTP_PORT     e.g. 587
    LINKLIB_SMTP_USER     e.g. hello@bmweis.com
    LINKLIB_SMTP_PASS     Gmail app password or SMTP password
    LINKLIB_FROM_EMAIL    Display sender address (defaults to SMTP_USER)

Optional:
    LINKLIB_CONTACT_EMAIL  Where contact-form submissions are emailed
                           (defaults to LINKLIB_FROM_EMAIL)
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

_SMTP_HOST  = os.environ.get("LINKLIB_SMTP_HOST", "")
_SMTP_PORT  = int(os.environ.get("LINKLIB_SMTP_PORT", "587"))
_SMTP_USER  = os.environ.get("LINKLIB_SMTP_USER", "")
_SMTP_PASS  = os.environ.get("LINKLIB_SMTP_PASS", "")
_FROM_EMAIL = os.environ.get("LINKLIB_FROM_EMAIL", "") or _SMTP_USER


def is_configured() -> bool:
    return bool(_SMTP_HOST and _SMTP_USER and _SMTP_PASS)


def default_notify_email() -> str:
    """Where to send notifications when no more specific address is set."""
    return _FROM_EMAIL


def send_notification_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text notification to Brian (e.g. a new contact-form
    submission). Returns True if sent, False if SMTP is not configured
    (graceful no-op) — callers should always save the record first and treat
    this as best-effort on top of that. Raises on SMTP errors.
    """
    if not is_configured():
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _FROM_EMAIL
    msg["To"] = to
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(_SMTP_USER, _SMTP_PASS)
        s.sendmail(_FROM_EMAIL, [to], msg.as_string())

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

    Sent from a no-reply address with the requesting member cc'd, so the
    intro lands as a real three-way thread the two of them can take from
    there. Reply-To points at the requester, so a vendor hitting "reply"
    (not "reply all") still reaches a real person instead of the no-reply
    sender. Set LINKLIB_FROM_EMAIL to an address on the secured domain
    (e.g. hello@bmweis.com) once Workspace SMTP is configured.

    Returns True if sent, False if SMTP is not configured (graceful no-op).
    Raises on SMTP errors so the caller can log or alert.
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
    msg["From"]    = f"CFO Toolbox (no-reply) <{_FROM_EMAIL}>"
    msg["To"]      = to
    msg["Cc"]      = cc
    msg["Reply-To"] = requester_email
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(_SMTP_USER, _SMTP_PASS)
        s.sendmail(_FROM_EMAIL, [to, cc], msg.as_string())

    return True
