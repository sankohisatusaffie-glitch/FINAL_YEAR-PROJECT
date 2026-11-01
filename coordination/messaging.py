"""SMS and email notifications, recorded in the OutboundMessage outbox.

SMS goes through the backend named in settings.SMS_BACKEND. The default sandbox backend
only logs the message, so the system can be demonstrated without an SMS provider account.
To go live, write a backend with the same `send(phone, body)` method that calls a
provider's API and point SMS_BACKEND at it.
"""

import logging
import re

from django.conf import settings
from django.core.mail import send_mail
from django.utils.module_loading import import_string

from .models import OutboundMessage, User, WorkerAssignment
from .utils import notify

logger = logging.getLogger(__name__)


class SandboxSmsBackend:
    name = "sandbox-sms"

    def send(self, phone, body):
        logger.info("Sandbox SMS to %s: %s", phone, body)


def normalize_phone(value):
    """Return a Sierra Leone number as +232XXXXXXXX, or None if it is not one."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("232"):
        return f"+{digits}"
    if len(digits) == 9 and digits.startswith("0"):
        return f"+232{digits[1:]}"
    if len(digits) == 8:
        return f"+232{digits}"
    return None


def send_sms(phone, body, user=None):
    number = normalize_phone(phone)
    if number is None:
        return OutboundMessage.objects.create(channel=OutboundMessage.Channel.SMS, recipient=phone or "", user=user, body=body,
                                              status=OutboundMessage.Status.FAILED, error="Not a valid Sierra Leone mobile number.")
    backend = import_string(getattr(settings, "SMS_BACKEND", "coordination.messaging.SandboxSmsBackend"))()
    try:
        backend.send(number, body[:459])  # Three SMS segments at most.
        status, error = OutboundMessage.Status.SENT, ""
    except Exception as exc:  # A provider outage must not break the action that triggered the SMS.
        logger.exception("SMS to %s failed", number)
        status, error = OutboundMessage.Status.FAILED, str(exc)[:240]
    return OutboundMessage.objects.create(channel=OutboundMessage.Channel.SMS, recipient=number, user=user, body=body,
                                          status=status, provider=getattr(backend, "name", ""), error=error)


def send_email_message(user, subject, body):
    if not user.email:
        return None
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
        status, error = OutboundMessage.Status.SENT, ""
    except Exception as exc:
        logger.exception("Email to %s failed", user.email)
        status, error = OutboundMessage.Status.FAILED, str(exc)[:240]
    return OutboundMessage.objects.create(channel=OutboundMessage.Channel.EMAIL, recipient=user.email, user=user, subject=subject,
                                          body=body, status=status, provider=settings.EMAIL_BACKEND.rsplit(".", 1)[-1], error=error)


def alert(user, title, message, link="", sms=False, email=False):
    """In-app notification, plus optional SMS and email copies."""
    if user is None or not user.is_active:
        return
    notify(user, title, message, link)
    if sms and user.phone:
        send_sms(user.phone, f"BuildLink: {title}. {message}", user=user)
    if email:
        send_email_message(user, f"BuildLink — {title}", f"{message}\n\n{settings.SITE_URL.rstrip('/')}{link}" if link else message)


def announcement_audience(announcement):
    """Accounts and phone numbers an announcement reaches."""
    company = announcement.company
    users = {company.owner, *User.objects.filter(employer=company, is_active=True)}
    workers = company.workers.filter(employment_status="active")
    if announcement.project_id:
        project = announcement.project
        team_ids = WorkerAssignment.objects.filter(project=project, status=WorkerAssignment.Status.ACTIVE).values("worker")
        workers = workers.filter(pk__in=team_ids)
        worker_users = {worker.user for worker in workers if worker.user_id}
        users = {company.owner, project.manager, *project.supervisors.all(), *worker_users} - {None}
    return users, workers


def broadcast(announcement):
    """Deliver an announcement. Returns (in-app count, SMS count, email count)."""
    users, workers = announcement_audience(announcement)
    link = "/announcements/"
    sms_sent = email_sent = 0
    for user in users:
        if user == announcement.author or not user.is_active:
            continue
        notify(user, announcement.title, announcement.body[:280], link)
        if announcement.send_email and send_email_message(user, f"BuildLink — {announcement.title}", announcement.body):
            email_sent += 1
    if announcement.send_sms:
        # Text every worker with a phone number, including workers without a sign-in account.
        numbers = {}
        for worker in workers:
            if worker.telephone:
                numbers[worker.telephone] = worker.user
        for user in users:
            if user.phone and user != announcement.author:
                numbers.setdefault(user.phone, user)
        for phone, user in numbers.items():
            if send_sms(phone, f"BuildLink: {announcement.title}. {announcement.body}", user=user).status == OutboundMessage.Status.SENT:
                sms_sent += 1
    in_app = len([user for user in users if user != announcement.author])
    return in_app, sms_sent, email_sent
