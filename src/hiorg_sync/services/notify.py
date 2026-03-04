from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .email_settings import load_email_settings


def send_mail(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    cfg = load_email_settings()

    host = str(cfg.get("SMTP_HOST", "") or "").strip()
    port = int(cfg.get("SMTP_PORT", 587) or 587)
    user = str(cfg.get("SMTP_USER", "") or "").strip()
    pw = str(cfg.get("SMTP_PASS", "") or "")

    starttls = bool(cfg.get("SMTP_STARTTLS", True))
    use_ssl = bool(cfg.get("SMTP_SSL", False))

    mail_from = (
        str(cfg.get("NOTIFY_FROM", "") or "").strip()
        or str(cfg.get("SMTP_FROM", "") or "").strip()
        or user
        or "hiorg-sync@localhost"
    )

    if not host:
        return False, "SMTP_HOST not configured"
    if not to_addr:
        return False, "notify recipient empty"
    if not subject:
        return False, "subject empty"
    if not mail_from:
        return False, "mail_from empty"

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                if user and pw:
                    s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                if starttls:
                    s.starttls()
                    s.ehlo()
                if user and pw:
                    s.login(user, pw)
                s.send_message(msg)
        return True, ""
    except Exception as e:
        return False, str(e)
