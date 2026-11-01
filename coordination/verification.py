"""Email address verification for self-registered accounts.

The confirmation link carries a signed token (Django's password-reset token machinery). It
expires after settings.PASSWORD_RESET_TIMEOUT (3 days by default) and stops working once the
address is verified, because the verified flag is part of the signed value.
"""

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .messaging import send_email_message

RESEND_INTERVAL_SECONDS = 120


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "coordination.verification.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{user.email_verified}{timestamp}"


email_verification_token = EmailVerificationTokenGenerator()


def verification_url(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    path = reverse("verify_email", args=[uid, email_verification_token.make_token(user)])
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def send_verification_email(user):
    """Email the confirmation link. Returns the link (shown on screen only in development)."""
    link = verification_url(user)
    days = max(1, settings.PASSWORD_RESET_TIMEOUT // 86400)
    body = (
        f"Hello {user.first_name or user.username},\n\n"
        f"Confirm your email address to finish setting up your BuildLink account:\n\n{link}\n\n"
        f"The link works once and expires in {days} days. If you did not create this account, ignore this email."
    )
    send_email_message(user, "Confirm your BuildLink email address", body)
    cache.set(f"verification-sent:{user.pk}", timezone.now().timestamp(), RESEND_INTERVAL_SECONDS)
    return link


def can_resend(user):
    return cache.get(f"verification-sent:{user.pk}") is None


def mark_verified(user):
    user.email_verified, user.email_verified_at = True, timezone.now()
    user.save(update_fields=["email_verified", "email_verified_at"])


def show_link_on_screen():
    """In development the console email backend is the only 'inbox', so show the link on the page."""
    return settings.DEBUG and settings.EMAIL_BACKEND.endswith("console.EmailBackend")
