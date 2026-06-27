"""Lightweight email helper for outbound notifications (lead intros, etc.).

Reads configuration from environment variables — all optional. If SMTP is not
configured, send_lead_email() returns False and the lead is stored in the DB
only (no error raised).

Required env vars to enable sending:
    LINKLIB_SMTP_HOST   e.g. smtp.gmail.com
    LINKLIB_SMTP_PORT   e.g. 587
    LINKLIB_SMTP_USER   e.g. hello@yourdomain.com
    LINKLIB_SMTP_PASS   Gmail app password or SMTP password
    LINKLIB_FROM_EMAIL  Display sender address (defaults to SMTP_USER)

TODO: Once hello@[domain].com is set up in Google Workspace, set all five
      vars in Railway and uncomment send calls below as needed.
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


def send_lead_email(
    to: str,
    tool_name: str,
    name: str,
    email: str,
    company: str,
    company_size: str,
) -> bool:
    """Send a warm-intro lead notification to the vendor.

    Returns True if sent, False if SMTP is not configured (graceful no-op).
    Raises on SMTP errors so the caller can log or alert.
    """
    if not is_configured():
        return False

    subject = f"Warm intro request: {name} at {company} → {tool_name}"

    body = f"""\
New warm intro request via bmweis.com — CFO Toolbox

Someone expressed interest in {tool_name} and asked for a warm intro.

Name:         {name}
Email:        {email}
Company:      {company}
Company size: {company_size}

Reply directly to {email} to follow up.

—
Brian Weisberg
bmweis.com / CFO Toolbox
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Brian Weisberg via CFO Toolbox <{_FROM_EMAIL}>"
    msg["To"]      = to
    msg["Reply-To"] = email
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(_SMTP_USER, _SMTP_PASS)
        s.sendmail(_FROM_EMAIL, [to], msg.as_string())

    return True
