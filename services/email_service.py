import os
import random
import string
import logging

import resend

logger = logging.getLogger("tenderiq.email")

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "TenderIQ <info@tenderiq.sooru.ai>")
EMAIL_DEV_MODE = os.getenv("EMAIL_DEV_MODE", "true").lower() == "true"


def generate_otp(length: int = 4) -> str:
    return "".join(random.choices(string.digits, k=length))


def _build_otp_html(otp_code: str, purpose: str = "signup") -> str:
    if purpose == "reset_password":
        heading = "Password Reset Code"
        message = "Use this code to reset your TenderIQ password."
    else:
        heading = "Verify Your Email"
        message = "Use this code to complete your TenderIQ registration."

    digits = "".join(
        f'<td style="font-size:28px;font-weight:700;font-family:monospace;'
        f'color:#111;padding:8px 12px;background:#8BC53F;border-radius:8px;'
        f'text-align:center;letter-spacing:2px">{d}</td>'
        for d in otp_code
    )

    return f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:40px 0">
<tr><td align="center">
<table width="420" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">

<!-- Header -->
<tr><td style="background:#002B50;padding:28px 30px;text-align:center">
  <span style="font-size:22px;font-weight:700;color:#fff;letter-spacing:-0.02em">Tender</span><span style="font-size:22px;font-weight:700;color:#8BC53F;letter-spacing:-0.02em">IQ</span>
</td></tr>

<!-- Body -->
<tr><td style="padding:36px 30px 20px;text-align:center">
  <div style="font-size:18px;font-weight:600;color:#111;margin-bottom:8px">{heading}</div>
  <div style="font-size:14px;color:#666;margin-bottom:28px">{message}</div>
  <table cellpadding="0" cellspacing="6" style="margin:0 auto">
    <tr>{digits}</tr>
  </table>
  <div style="margin-top:28px;font-size:12px;color:#999">This code expires in <strong>5 minutes</strong>.</div>
  <div style="margin-top:6px;font-size:12px;color:#999">If you didn't request this, you can safely ignore this email.</div>
</td></tr>

<!-- Footer -->
<tr><td style="padding:20px 30px;border-top:1px solid #eee;text-align:center">
  <span style="font-size:11px;color:#aaa">Sch&uuml;co &times; Sooru.AI</span>
</td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def send_otp_email(email: str, otp_code: str, purpose: str = "signup") -> None:
    subject = (
        "Your TenderIQ password reset code"
        if purpose == "reset_password"
        else "Your TenderIQ verification code"
    )
    html = _build_otp_html(otp_code, purpose)

    logger.info("Sending OTP email to %s (purpose=%s)", email, purpose)

    if EMAIL_DEV_MODE:
        logger.warning("[DEV MODE] OTP for %s: %s (email not sent)", email, otp_code)
        return

    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [email],
            "subject": subject,
            "html": html,
        })
    except Exception as e:
        logger.error("Failed to send OTP email to %s: %s", email, e)
        raise RuntimeError(f"Failed to send verification email: {e}") from e
