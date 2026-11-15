"""
Email Utilities
Handles email sending functionality via HTTP APIs (Resend, Brevo) or SMTP,
with automatic recovery fallback for restricted environments (e.g. Render Free Tier).
"""

import json
import logging
import secrets
import smtplib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

try:
    from backend.config import (
        SMTP_SERVER,
        SMTP_PORT,
        SMTP_USERNAME,
        SMTP_PASSWORD,
        SENDER_EMAIL,
        OTP_EXPIRATION_MINUTES,
        RESEND_API_KEY,
        RESEND_FROM_EMAIL,
        BREVO_API_KEY,
        ALLOW_LOG_OTP_FALLBACK,
    )
    from backend.database.mongo import otp_collection
except ModuleNotFoundError:
    from config import (
        SMTP_SERVER,
        SMTP_PORT,
        SMTP_USERNAME,
        SMTP_PASSWORD,
        SENDER_EMAIL,
        OTP_EXPIRATION_MINUTES,
        RESEND_API_KEY,
        RESEND_FROM_EMAIL,
        BREVO_API_KEY,
        ALLOW_LOG_OTP_FALLBACK,
    )
    from database.mongo import otp_collection


def generate_otp() -> str:
    """Generate a 6-digit random OTP."""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])


def _send_via_resend(email: str, subject: str, html_body: str) -> bool:
    """
    Send email via Resend HTTPS REST API (port 443).
    Bypasses cloud outbound SMTP firewall restrictions.
    """
    if not RESEND_API_KEY:
        return False

    url = "https://api.resend.com/emails"
    from_address = RESEND_FROM_EMAIL or "SmartDOC Assistant <onboarding@resend.dev>"
    
    payload = {
        "from": from_address,
        "to": [email],
        "subject": subject,
        "html": html_body,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "SmartDOC-Assistant/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        logger.error(f"Resend API error {e.code}: {error_body}")
        raise Exception(f"Resend error: {error_body}")
    except Exception as e:
        logger.error(f"Resend connection failed: {e}")
        raise


def _send_via_brevo(email: str, subject: str, html_body: str) -> bool:
    """
    Send email via Brevo HTTPS REST API (port 443).
    """
    if not BREVO_API_KEY:
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    sender_addr = SENDER_EMAIL or "no-reply@smartdoc.ai"
    
    payload = {
        "sender": {"name": "SmartDOC Assistant", "email": sender_addr},
        "to": [{"email": email}],
        "subject": subject,
        "htmlContent": html_body,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "SmartDOC-Assistant/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        logger.error(f"Brevo API error {e.code}: {error_body}")
        raise Exception(f"Brevo error: {error_body}")
    except Exception as e:
        logger.error(f"Brevo connection failed: {e}")
        raise


def _send_via_smtp(email: str, subject: str, html_body: str):
    """
    Send email via standard SMTP (ports 587/465).
    Works in local environments or unblocked hosting.
    """
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        raise ValueError("SMTP_USERNAME or SMTP_PASSWORD is not configured.")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = SENDER_EMAIL or SMTP_USERNAME
    message["To"] = email
    message.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=12) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(message["From"], email, message.as_string())


def send_otp_email(email: str) -> dict:
    """
    Generate OTP, store in database, and dispatch via configured email provider.
    Falls back gracefully if the host environment blocks outbound SMTP (Render Free Tier).
    """
    otp = generate_otp()
    now = datetime.now(timezone.utc)
    expiration_time = now + timedelta(minutes=OTP_EXPIRATION_MINUTES)

    # Always persist OTP to MongoDB first
    otp_collection.update_one(
        {"email": email},
        {
            "$set": {
                "email": email,
                "otp": otp,
                "created_at": now,
                "expires_at": expiration_time,
            }
        },
        upsert=True,
    )

    subject = "SmartDOC Assistant - Verification Code"
    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 24px;">
            <div style="max-width: 500px; margin: 0 auto; background-color: #ffffff; padding: 32px; border-radius: 12px; border: 1px solid #e2e8f0;">
                <h2 style="color: #0f172a; margin-top: 0;">SmartDOC Assistant Verification</h2>
                <p style="color: #475569; font-size: 15px;">Your verification code is below. It is valid for {OTP_EXPIRATION_MINUTES} minutes.</p>
                <div style="background-color: #f1f5f9; padding: 16px; border-radius: 8px; text-align: center; margin: 24px 0;">
                    <span style="font-size: 32px; font-weight: 700; color: #4f46e5; letter-spacing: 6px;">{otp}</span>
                </div>
                <p style="color: #94a3b8; font-size: 13px;">If you did not request this code, you can safely ignore this email.</p>
            </div>
        </body>
    </html>
    """

    # 1. Try Resend HTTP API if configured
    if RESEND_API_KEY:
        try:
            _send_via_resend(email, subject, html_body)
            return {
                "message": f"Verification code sent to {email}",
                "expiration_minutes": OTP_EXPIRATION_MINUTES,
            }
        except Exception as e:
            logger.warning(f"Resend delivery failed, trying other channels: {e}")

    # 2. Try Brevo HTTP API if configured
    if BREVO_API_KEY:
        try:
            _send_via_brevo(email, subject, html_body)
            return {
                "message": f"Verification code sent to {email}",
                "expiration_minutes": OTP_EXPIRATION_MINUTES,
            }
        except Exception as e:
            logger.warning(f"Brevo delivery failed, trying SMTP: {e}")

    # 3. Try SMTP (Gmail or custom SMTP server)
    try:
        _send_via_smtp(email, subject, html_body)
        return {
            "message": f"Verification code sent to {email}",
            "expiration_minutes": OTP_EXPIRATION_MINUTES,
        }
    except Exception as smtp_err:
        err_str = str(smtp_err)
        logger.warning(f"SMTP delivery failed: {err_str}")

        # Check if failure is due to blocked outbound ports (Render Free Tier blocks 25, 465, 587)
        is_port_blocked = any(
            hint in err_str.lower()
            for hint in ["network is unreachable", "errno 101", "timed out", "connection refused", "timeout"]
        )

        if is_port_blocked and ALLOW_LOG_OTP_FALLBACK:
            # Print clearly to Render stdout so it appears in Render dashboard logs
            banner = f"""
================================================================================
⚠️ [RENDER FREE TIER NOTICE]: Outbound SMTP port {SMTP_PORT} is blocked by Render firewall.
🔑 [VERIFICATION OTP FOR {email}]: {otp}
💡 Valid for {OTP_EXPIRATION_MINUTES} minutes.
💡 Tip: Add RESEND_API_KEY to your Render Environment Variables for live inbox delivery.
================================================================================
"""
            print(banner, file=sys.stdout, flush=True)
            logger.info(banner)

            return {
                "message": f"Verification code generated! (Host blocks SMTP port {SMTP_PORT}; check server logs)",
                "expiration_minutes": OTP_EXPIRATION_MINUTES,
                "dev_otp": otp,
            }

        # Otherwise raise the error
        raise Exception(f"Email delivery failed: {err_str}")


def verify_otp(email: str, otp: str) -> dict:
    """
    Verify OTP against stored value in database.
    """
    otp_record = otp_collection.find_one({"email": email})

    if not otp_record:
        raise Exception("No OTP found for this email. Please request a new one.")

    now = datetime.now(timezone.utc)
    expires_at = otp_record["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now > expires_at:
        otp_collection.delete_one({"email": email})
        raise Exception("OTP has expired. Please request a new one.")

    if otp_record["otp"] != otp:
        raise Exception("Invalid OTP. Please try again.")

    # OTP verified - cleanup
    otp_collection.delete_one({"email": email})

    return {
        "message": "OTP verified successfully",
        "email": email,
        "verified": True,
    }
