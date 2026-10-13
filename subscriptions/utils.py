from datetime import date
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.utils import timezone
from .models import MemberProfile, Subscription, ClubSettings, AuditLog


def get_current_subscription_month() -> date:
    """
    Returns the first day of the current calendar month in the local timezone (Africa/Addis_Ababa).
    For example, on 2026-10-13, returns date(2026, 10, 1).
    """
    local_now = timezone.localtime(timezone.now())
    return date(local_now.year, local_now.month, 1)


def normalize_to_month_start(target_date: date) -> date:
    """
    Normalizes any date to the first day of that month.
    """
    if isinstance(target_date, str):
        # Parses YYYY-MM or YYYY-MM-DD
        parts = target_date.split('-')
        return date(int(parts[0]), int(parts[1]), 1)
    return date(target_date.year, target_date.month, 1)


def ensure_subscription_for_member(member: MemberProfile, target_month: date = None) -> Subscription:
    """
    Safely and idempotently retrieves or creates a subscription record for a member
    for the specified month (defaults to current subscription month).
    If a record doesn't exist yet, it is created with status='UNPAID'.
    Thread-safe and concurrency-safe via database atomic block and unique constraint.
    """
    if target_month is None:
        target_month = get_current_subscription_month()
    else:
        target_month = normalize_to_month_start(target_month)

    # First attempt a fast read
    subscription = Subscription.objects.filter(
        member=member,
        subscription_month=target_month
    ).first()

    if subscription:
        return subscription

    # If missing, create safely in atomic transaction
    settings = ClubSettings.get_settings()
    try:
        with transaction.atomic():
            subscription, created = Subscription.objects.get_or_create(
                member=member,
                subscription_month=target_month,
                defaults={
                    'amount_due': settings.monthly_subscription_amount,
                    'status': Subscription.Status.UNPAID,
                }
            )
            return subscription
    except IntegrityError:
        # Another concurrent request just created it
        return Subscription.objects.get(
            member=member,
            subscription_month=target_month
        )


def sync_active_members_subscriptions(target_month: date = None) -> dict:
    """
    Idempotent operation to create missing monthly subscription records for all active members.
    - Does NOT modify existing records or overwrite paid/pending statuses.
    - Only creates missing records with status='UNPAID'.
    Returns a dictionary summary: {'created': count, 'existing': count, 'total_active': count, 'month': target_month}
    """
    if target_month is None:
        target_month = get_current_subscription_month()
    else:
        target_month = normalize_to_month_start(target_month)

    settings = ClubSettings.get_settings()
    active_members = MemberProfile.objects.filter(is_active_member=True)

    created_count = 0
    existing_count = 0

    # Fetch existing subscriptions for this month to minimize database calls
    existing_member_ids = set(
        Subscription.objects.filter(
            subscription_month=target_month
        ).values_list('member_id', flat=True)
    )

    new_subscriptions = []
    for member in active_members:
        if member.id in existing_member_ids:
            existing_count += 1
        else:
            new_subscriptions.append(
                Subscription(
                    member=member,
                    subscription_month=target_month,
                    amount_due=settings.monthly_subscription_amount,
                    status=Subscription.Status.UNPAID,
                )
            )

    if new_subscriptions:
        # bulk_create with ignore_conflicts for high-performance and race-condition safety
        created = Subscription.objects.bulk_create(
            new_subscriptions,
            ignore_conflicts=True
        )
        created_count = len(created)

    return {
        'created': created_count,
        'existing': existing_count,
        'total_active': active_members.count(),
        'month': target_month,
    }


def record_audit(actor, action_type, details="", target_member=None, request=None):
    """
    Helper function to reliably create audit records.
    """
    ip_address = None
    if request:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip_address = x_forwarded_for.split(',')[0].strip()
        else:
            ip_address = request.META.get('REMOTE_ADDR')

    AuditLog.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        target_member=target_member,
        action_type=action_type,
        details=details,
        ip_address=ip_address
    )
