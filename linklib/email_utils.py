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
from html import escape as _h

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


# ---------------------------------------------------------------------------
# Admin-editable templates. Defaults live here; /admin/emails lets Brian
# override subject/body/sign-off per email, stored as plain settings-table
# strings and passed in as the *_template/signoff args below. A blank/missing
# override falls back to these defaults.
# ---------------------------------------------------------------------------

WARM_INTRO_PLACEHOLDERS = [
    "vendor_name", "requester_name", "requester_email",
    "requester_company", "requester_company_size", "tool_name",
]
WARM_INTRO_SUBJECT_DEFAULT = "Introduction: {requester_name} <> {tool_name}"
WARM_INTRO_BODY_DEFAULT = """\
Hi {vendor_name},

I'd like to introduce you to {requester_name} at {requester_company} ({requester_company_size} employees)—a member of the CFO Toolbox community I run at bmweis.com. They came across {tool_name} in the directory and asked for a warm intro to your team.

{requester_name}
{requester_email}
{requester_company}—{requester_company_size} employees

I've cc'd {requester_name} directly so the two of you can take it from here."""
WARM_INTRO_SIGNOFF_DEFAULT = "My best,\nBrian Weisberg\nbmweis.com / CFO Toolbox"

WELCOME_PLACEHOLDERS = ["name", "username", "temp_password", "login_url", "to"]
WELCOME_SUBJECT_DEFAULT = "Your bmweis.com account"
WELCOME_BODY_DEFAULT = """\
Hi {name},

Your account at bmweis.com is ready, confirmed for {to}.

Username: {username}
Temporary password: {temp_password}

Sign in here, then use "Forgot your password?" on that page to set your own password—worth doing soon, since this one was just generated for you:
{login_url}"""
WELCOME_SIGNOFF_DEFAULT = "My best,\nBrian Weisberg"

PASSWORD_RESET_PLACEHOLDERS = ["username", "reset_url", "to"]
PASSWORD_RESET_SUBJECT_DEFAULT = "Reset your bmweis.com password"
PASSWORD_RESET_BODY_DEFAULT = """\
Hi {username},

Someone (hopefully you) requested a password reset for your bmweis.com account, confirmed for {to}.

Reset your password here—this link expires in 1 hour:
{reset_url}

If you didn't request this, you can ignore this email; your password won't change."""
PASSWORD_RESET_SIGNOFF_DEFAULT = "My best,\nBrian Weisberg"

TOOL_SUBMISSION_PLACEHOLDERS = ["tool_name", "tool_url", "description", "submitted_by"]
TOOL_SUBMISSION_SUBJECT_DEFAULT = "Got your submission: {tool_name}"
TOOL_SUBMISSION_BODY_DEFAULT = """\
Hi there,

Thanks for submitting {tool_name} to the CFO Toolbox, confirmed from {submitted_by}.

{tool_name}
{tool_url}
{description}

I'll take a look and follow up once it's reviewed. Reply directly to this email if you have questions or need to change anything above."""
TOOL_SUBMISSION_SIGNOFF_DEFAULT = "My best,\nBrian Weisberg"

CONTACT_CONFIRMATION_PLACEHOLDERS = ["name", "email", "message"]
CONTACT_CONFIRMATION_SUBJECT_DEFAULT = "Got your message"
CONTACT_CONFIRMATION_BODY_DEFAULT = """\
Hi {name},

Thanks for reaching out—here's a copy of what you sent:

{message}

I'll get back to you directly at {email} shortly."""
CONTACT_CONFIRMATION_SIGNOFF_DEFAULT = "My best,\nBrian Weisberg"


def validate_template(template: str, placeholder_keys: list[str]) -> str:
    """Try rendering a subject/body template with dummy values for every
    known placeholder. Returns an empty string if it renders cleanly, else a
    human-readable error describing what's wrong — used by the /admin/emails
    save handlers so a typo'd template gets caught at save time, not send
    time."""
    sample = {k: f"[{k}]" for k in placeholder_keys}
    try:
        template.format(**sample)
    except (KeyError, IndexError) as e:
        allowed = ", ".join("{" + k + "}" for k in placeholder_keys)
        return f"Unknown placeholder {{{e.args[0]}}}. Allowed placeholders: {allowed}"
    except ValueError as e:
        return f"Invalid template syntax: {e}"
    return ""


def _render_html_block(text: str) -> str:
    """Render already-substituted plain text as escaped HTML paragraphs —
    blank lines start a new <p>, single newlines within a paragraph become
    <br>. Escaping happens after placeholder substitution, so both the
    admin-authored template text and any user-supplied values (names,
    companies, etc.) get escaped exactly once."""
    paras = [p.strip("\n") for p in text.strip("\n").split("\n\n") if p.strip()]
    return "".join(
        "<p>" + "<br>".join(_h(line) for line in p.split("\n")) + "</p>"
        for p in paras
    )


_HTML_WRAPPER = (
    '<html><body style="font-family:-apple-system,BlinkMacSystemFont,'
    "'Segoe UI',Roboto,Arial,sans-serif;font-size:15px;line-height:1.55;"
    'color:#1a1a1a;">{content}</body></html>'
)


def _build_templated_message(
    subject_template: str, body_template: str, signoff: str, placeholders: dict,
    to: str, cc: str | None = None, reply_to: str | None = None, from_header: str | None = None,
) -> MIMEMultipart:
    """Render a subject/body/sign-off template against placeholders and build
    the multipart/alternative message (plain-text + HTML) shared by every
    admin-editable email below."""
    subject = subject_template.format(**placeholders)
    body_rendered = body_template.format(**placeholders)
    body = f"{body_rendered}\n\n{signoff}\n"
    html_body = _HTML_WRAPPER.format(
        content=_render_html_block(body_rendered) + _render_html_block(signoff)
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if from_header or _FROM_EMAIL:
        msg["From"] = from_header or _FROM_EMAIL
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    # Plain-text part first, HTML second — clients that support HTML render
    # the last part in a multipart/alternative; plain-text-only clients fall
    # back to the first part.
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


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


# Human-readable subject tags for the internal notifications below, keyed by
# the same context string each call site already passes to
# _send_email_safely (see webapp/app.py) — so a filter rule matching the
# bracketed tag in the subject lines up with what shows on
# /admin/email-failures too.
NOTIFICATION_TYPE_LABELS = {
    "contact": "Contact Form",
    "tool_submission": "Tool Submission",
    "password_reset_no_email": "Password Reset",
}


def send_notification_email(to: str, subject: str, body: str, notification_type: str = "") -> bool:
    """Send an HTML+plain-text notification to Brian (e.g. a new contact-form
    submission). Returns True if sent, False if Google OAuth is not
    configured (graceful no-op) — callers should always save the record
    first and treat this as best-effort on top of that. Raises on API errors.

    notification_type tags the message so a mail-client rule can auto-file
    it: pass one of NOTIFICATION_TYPE_LABELS' keys to get both a bracketed
    label prefixed onto the subject (e.g. "[Contact Form] ...") and an
    X-CFO-Notification-Type header carrying the raw type — use whichever
    your mail client's filters can match on (most only expose subject/body
    text, not custom headers).
    """
    if not is_configured():
        return False

    label = NOTIFICATION_TYPE_LABELS.get(notification_type, "")
    full_subject = f"[{label}] {subject}" if label else subject

    msg = MIMEMultipart("alternative")
    msg["Subject"] = full_subject
    if _FROM_EMAIL:
        msg["From"] = _FROM_EMAIL
    msg["To"] = to
    if notification_type:
        msg["X-CFO-Notification-Type"] = notification_type
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(_HTML_WRAPPER.format(content=_render_html_block(body)), "html"))
    _send(msg)
    return True


def send_welcome_email(
    to: str,
    username: str,
    temp_password: str,
    login_url: str,
    name: str = "",
    subject_template: str | None = None,
    body_template: str | None = None,
    signoff: str | None = None,
) -> bool:
    """Send a new member a warm welcome with their account details and a link
    to sign in. Returns True if sent, False if Google OAuth is not configured
    (graceful no-op) — the account still exists either way, so callers should
    tell the admin plainly when this comes back False (share the password
    another way). Raises on API errors.

    subject_template/body_template/signoff default to the WELCOME_* module
    constants — pass overrides from /admin/emails to customize copy without
    a redeploy. Body/subject templates may use any of WELCOME_PLACEHOLDERS.
    """
    if not is_configured():
        return False

    subject_template = subject_template or WELCOME_SUBJECT_DEFAULT
    body_template = body_template or WELCOME_BODY_DEFAULT
    signoff = WELCOME_SIGNOFF_DEFAULT if signoff is None else signoff

    placeholders = dict(
        name=name.strip() or username, username=username,
        temp_password=temp_password, login_url=login_url, to=to,
    )
    msg = _build_templated_message(subject_template, body_template, signoff, placeholders, to=to)
    _send(msg)
    return True


def send_password_reset_email(
    to: str,
    username: str,
    reset_url: str,
    subject_template: str | None = None,
    body_template: str | None = None,
    signoff: str | None = None,
) -> bool:
    """Send a self-service password reset link. Returns True if sent, False
    if Google OAuth is not configured (graceful no-op) — the reset request
    is still recorded either way, so Brian can reset it by hand from
    /admin/users if the email never arrives. Raises on API errors.

    subject_template/body_template/signoff default to the PASSWORD_RESET_*
    module constants — pass overrides from /admin/emails to customize copy
    without a redeploy. Body/subject templates may use any of
    PASSWORD_RESET_PLACEHOLDERS.
    """
    if not is_configured():
        return False

    subject_template = subject_template or PASSWORD_RESET_SUBJECT_DEFAULT
    body_template = body_template or PASSWORD_RESET_BODY_DEFAULT
    signoff = PASSWORD_RESET_SIGNOFF_DEFAULT if signoff is None else signoff

    placeholders = dict(username=username, reset_url=reset_url, to=to)
    msg = _build_templated_message(subject_template, body_template, signoff, placeholders, to=to)
    _send(msg)
    return True


def send_tool_submission_confirmation_email(
    to: str,
    tool_name: str,
    tool_url: str,
    description: str,
    subject_template: str | None = None,
    body_template: str | None = None,
    signoff: str | None = None,
) -> bool:
    """Confirm a member's CFO Toolbox submission back to them — what they
    submitted, and an invitation to reply if they have questions or need to
    change anything. Returns True if sent, False if Google OAuth is not
    configured (graceful no-op) — the submission is still recorded either
    way. Raises on API errors.

    subject_template/body_template/signoff default to the
    TOOL_SUBMISSION_* module constants. Body/subject templates may use any
    of TOOL_SUBMISSION_PLACEHOLDERS.
    """
    if not is_configured():
        return False

    subject_template = subject_template or TOOL_SUBMISSION_SUBJECT_DEFAULT
    body_template = body_template or TOOL_SUBMISSION_BODY_DEFAULT
    signoff = TOOL_SUBMISSION_SIGNOFF_DEFAULT if signoff is None else signoff

    placeholders = dict(tool_name=tool_name, tool_url=tool_url, description=description, submitted_by=to)
    msg = _build_templated_message(subject_template, body_template, signoff, placeholders, to=to)
    _send(msg)
    return True


def send_contact_confirmation_email(
    to: str,
    name: str,
    message: str,
    subject_template: str | None = None,
    body_template: str | None = None,
    signoff: str | None = None,
) -> bool:
    """Send the contact-form submitter a copy of their own message, so they
    have a record of what they sent and know it went through. Returns True
    if sent, False if Google OAuth is not configured (graceful no-op) — the
    submission is still recorded either way. Raises on API errors.

    subject_template/body_template/signoff default to the
    CONTACT_CONFIRMATION_* module constants. Body/subject templates may use
    any of CONTACT_CONFIRMATION_PLACEHOLDERS.
    """
    if not is_configured():
        return False

    subject_template = subject_template or CONTACT_CONFIRMATION_SUBJECT_DEFAULT
    body_template = body_template or CONTACT_CONFIRMATION_BODY_DEFAULT
    signoff = CONTACT_CONFIRMATION_SIGNOFF_DEFAULT if signoff is None else signoff

    placeholders = dict(name=name, email=to, message=message)
    msg = _build_templated_message(subject_template, body_template, signoff, placeholders, to=to)
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
    subject_template: str | None = None,
    body_template: str | None = None,
    signoff: str | None = None,
) -> bool:
    """Send a warm-intro email connecting a CFO Toolbox member with a named
    vendor contact — an actual introduction (addressed to the vendor contact
    by name, naming the requester), not a lead-notification form dump.

    Sent with the requesting member cc'd, so the intro lands as a real
    three-way thread the two of them can take from there. Reply-To points at
    the requester, so a vendor hitting "reply" (not "reply all") still
    reaches a real person. Set LINKLIB_FROM_EMAIL to the sending mailbox on
    the secured domain (e.g. hello@bmweis.com).

    subject_template/body_template/signoff default to the WARM_INTRO_*
    module constants — pass overrides from /admin/emails to customize copy
    without a redeploy. Body/subject templates may use any of
    WARM_INTRO_PLACEHOLDERS.

    Returns True if sent, False if Google OAuth is not configured (graceful
    no-op). Raises on API errors so the caller can log or alert.
    """
    if not is_configured():
        return False

    subject_template = subject_template or WARM_INTRO_SUBJECT_DEFAULT
    body_template = body_template or WARM_INTRO_BODY_DEFAULT
    signoff = WARM_INTRO_SIGNOFF_DEFAULT if signoff is None else signoff

    placeholders = dict(
        vendor_name=vendor_contact_name.strip() or "there",
        requester_name=requester_name, requester_email=requester_email,
        requester_company=requester_company, requester_company_size=requester_company_size,
        tool_name=tool_name,
    )
    msg = _build_templated_message(
        subject_template, body_template, signoff, placeholders, to=to, cc=cc,
        reply_to=requester_email,
        from_header=f"CFO Toolbox (no-reply) <{_FROM_EMAIL}>" if _FROM_EMAIL else None,
    )
    _send(msg)
    return True
