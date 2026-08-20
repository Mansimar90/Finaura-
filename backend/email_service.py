"""Email delivery service — Resend with prototype console fallback."""
import os
import logging
from typing import Optional

log = logging.getLogger("finaura.email")


def _sender() -> str:
    return os.environ.get("RESEND_FROM_EMAIL", "Finaura <onboarding@resend.dev>")


async def send_email(to: str, subject: str, html: str, text: str) -> dict:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        # Prototype fallback: log content so developer can copy the link from server logs
        log.warning("=" * 60)
        log.warning("[EMAIL FALLBACK — no RESEND_API_KEY]")
        log.warning("To: %s", to)
        log.warning("Subject: %s", subject)
        log.warning("Body:\n%s", text)
        log.warning("=" * 60)
        return {"id": "console-fallback", "delivered": False}

    try:
        import resend
        resend.api_key = api_key
        result = resend.Emails.send({
            "from": _sender(),
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        })
        return {"id": result.get("id", "unknown"), "delivered": True}
    except Exception as exc:  # pragma: no cover
        log.exception("Resend delivery failed: %s", exc)
        return {"id": None, "delivered": False, "error": str(exc)}


def verify_email_template(name: str, link: str) -> tuple[str, str]:
    subject = "Verify your Finaura email"
    text = f"Hi {name},\n\nWelcome to Finaura. Verify your email by clicking:\n{link}\n\nThis link expires in 1 hour."
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:520px;margin:auto;padding:32px;background:#f9f6f1;border-radius:12px">
      <h1 style="color:#1a1a1a">Welcome to Finaura</h1>
      <p style="color:#4a4a4a">Hi {name}, verify your email to secure your account.</p>
      <a href="{link}" style="display:inline-block;padding:14px 22px;background:#0f172a;color:#fff;border-radius:8px;text-decoration:none;font-weight:600">Verify email</a>
      <p style="color:#94a3b8;font-size:13px;margin-top:24px">This link expires in 1 hour.</p>
    </div>
    """
    return subject, html, text  # type: ignore


def reset_password_template(name: str, link: str) -> tuple[str, str, str]:
    subject = "Reset your Finaura password"
    text = f"Hi {name},\n\nWe received a request to reset your Finaura password.\nReset link (expires in 15 minutes):\n{link}\n\nIf you didn't request this, ignore this email."
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:520px;margin:auto;padding:32px;background:#f9f6f1;border-radius:12px">
      <h1 style="color:#1a1a1a">Reset your password</h1>
      <p style="color:#4a4a4a">Hi {name}, click below to set a new password.</p>
      <a href="{link}" style="display:inline-block;padding:14px 22px;background:#0f172a;color:#fff;border-radius:8px;text-decoration:none;font-weight:600">Reset password</a>
      <p style="color:#94a3b8;font-size:13px;margin-top:24px">This link expires in 15 minutes. If you didn't request this, ignore it.</p>
    </div>
    """
    return subject, html, text
