"""Wage payments through Orange Money and Afrimoney.

This release ships a sandbox gateway that simulates the providers: it validates the
request, returns a realistic provider reference, and never moves real money. A live
integration would implement the same `disburse()` method against the provider's API and
be selected with settings.MOBILE_MONEY_GATEWAY.

Sandbox test numbers: any valid Sierra Leone number succeeds, except numbers ending in
0000, which fail with "wallet not found" so the failure path can be demonstrated.
"""

import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from .messaging import normalize_phone, send_sms
from .models import MobileMoneyProvider, MobileMoneyTransaction, PayrollRecord


class PaymentError(Exception):
    pass


class SandboxMobileMoneyGateway:
    sandbox = True
    prefixes = {MobileMoneyProvider.ORANGE_MONEY: "OM", MobileMoneyProvider.AFRIMONEY: "AFM"}

    def disburse(self, provider, msisdn, amount, reference):
        """Return (succeeded, provider_reference, message)."""
        if msisdn.endswith("0000"):
            return False, "", "Recipient wallet not found (sandbox test number)."
        provider_reference = f"{self.prefixes[provider]}-SBX-{secrets.token_hex(5).upper()}"
        return True, provider_reference, f"SLE {amount:,.2f} sent to {msisdn}."


def gateway():
    return import_string(getattr(settings, "MOBILE_MONEY_GATEWAY", "coordination.payments.SandboxMobileMoneyGateway"))()


def pay_wages(record, initiated_by):
    """Send an approved payroll record's net pay to the worker's mobile money wallet."""
    worker = record.worker
    if record.status != PayrollRecord.Status.APPROVED:
        raise PaymentError("Only approved payroll records can be paid.")
    if record.net_pay <= 0:
        raise PaymentError("There is nothing to pay for this period.")
    if not worker.mobile_money_provider or not worker.mobile_money_number:
        raise PaymentError(f"Add a mobile money provider and number to {worker.full_name}'s record first.")
    msisdn = normalize_phone(worker.mobile_money_number)
    if msisdn is None:
        raise PaymentError(f"{worker.mobile_money_number} is not a valid Sierra Leone mobile number.")
    if record.transactions.filter(status=MobileMoneyTransaction.Status.SUCCESSFUL).exists():
        raise PaymentError("This payroll record has already been paid.")

    active_gateway = gateway()
    with transaction.atomic():
        locked = PayrollRecord.objects.select_for_update().get(pk=record.pk)
        if locked.status != PayrollRecord.Status.APPROVED:
            raise PaymentError("This payroll record changed while you were paying it. Refresh and try again.")
        txn = MobileMoneyTransaction.objects.create(
            payroll=locked, provider=worker.mobile_money_provider, msisdn=msisdn, amount=locked.net_pay,
            reference=f"BL-PAY-{timezone.now():%Y%m%d}-{secrets.token_hex(4).upper()}",
            sandbox=getattr(active_gateway, "sandbox", False), initiated_by=initiated_by,
        )
        succeeded, provider_reference, message = active_gateway.disburse(txn.provider, msisdn, txn.amount, txn.reference)
        txn.status = MobileMoneyTransaction.Status.SUCCESSFUL if succeeded else MobileMoneyTransaction.Status.FAILED
        txn.provider_reference, txn.message = provider_reference, message[:240]
        txn.save(update_fields=["status", "provider_reference", "message"])
        if succeeded:
            locked.status, locked.paid_at = PayrollRecord.Status.PAID, timezone.now()
            locked.save(update_fields=["status", "paid_at"])
    if succeeded:
        send_sms(msisdn, f"BuildLink: your wages of SLE {txn.amount:,.2f} for {locked.period_start:%d %b}–{locked.period_end:%d %b %Y} were sent via {txn.get_provider_display()}. Ref {provider_reference}.", user=worker.user)
    return txn
