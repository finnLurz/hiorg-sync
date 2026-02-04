# src/hiorg_sync/services/notify.py
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_mail(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    pw = os.getenv("SMTP_PASS", "").strip()

    starttls = os.getenv("SMTP_STARTTLS", "true").lower() in ("1", "true", "yes", "on")
    use_ssl = os.getenv("SMTP_SSL", "false").lower() in ("1", "true", "yes", "on")

    mail_from = (
        os.getenv("NOTIFY_FROM", "").strip()
        or os.getenv("SMTP_FROM", "").strip()
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
