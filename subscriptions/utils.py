import os
import threading
from datetime import date, datetime
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction, IntegrityError
from django.utils import timezone
from .models import MemberProfile, Subscription, ClubSettings, AuditLog

# Thread-local storage to hold the active HTTP request in development
_thread_locals = threading.local()


def set_current_request(request):
    """Stores the active request for thread-local resolution in dev/test."""
    _thread_locals.request = request


def get_current_request():
    """Retrieves the active request from thread-local storage."""
    return getattr(_thread_locals, 'request', None)


def is_simulation_allowed() -> bool:
    """
    STRICT PRODUCTION SAFEGUARD:
    Simulation is only permitted when DEBUG is True or when explicitly
    enabled in automated test suites via ENABLE_MONTH_SIMULATION_IN_TESTS=True.
    In production (DEBUG=False), simulation is NEVER allowed under any circumstance.
    """
    if getattr(settings, 'DEBUG', False):
        return True
    if getattr(settings, 'ENABLE_MONTH_SIMULATION_IN_TESTS', False):
        return True
    return False


def get_real_subscription_month() -> date:
    """
    Returns the real first day of the current calendar month in Africa/Addis_Ababa timezone.
    This is strictly used in production and serves as the baseline in development.
    """
    local_now = timezone.localtime(timezone.now())
    return date(local_now.year, local_now.month, 1)


def normalize_to_month_start(target_date) -> date:
    """
    Normalizes any date, datetime, or date string to the first day of that month: date(YYYY, MM, 1).
    Supported string formats: 'YYYY-MM', 'YYYY-MM-DD', 'October 2026', 'Nov 2026', etc.
    """
    if target_date is None:
        raise ValueError("Cannot normalize None to a month start date.")
    if isinstance(target_date, datetime):
        return date(target_date.year, target_date.month, 1)
    if isinstance(target_date, date):
        return date(target_date.year, target_date.month, 1)
    if isinstance(target_date, str):
        target_str = target_date.strip()
        # Handle ISO-like formats: 2026-11, 2026-11-01, 2026/11
        if '-' in target_str or '/' in target_str:
            delimiter = '-' if '-' in target_str else '/'
            parts = [p.strip() for p in target_str.split(delimiter)]
            if len(parts) >= 2:
                # Year first: 2026-11
                if len(parts[0]) == 4:
                    return date(int(parts[0]), int(parts[1]), 1)
                # Month first: 11-2026
                elif len(parts[1]) == 4:
                    return date(int(parts[1]), int(parts[0]), 1)
        # Handle named month formats: "October 2026", "Nov 2026"
        for fmt in ('%B %Y', '%b %Y', '%B, %Y', '%b, %Y'):
            try:
                parsed = datetime.strptime(target_str, fmt)
                return date(parsed.year, parsed.month, 1)
            except ValueError:
                continue
    raise ValueError(f"Cannot normalize '{target_date}' to month start date.")


def get_simulated_subscription_month_raw(request=None):
    """
    Resolves the raw simulated subscription month configuration.
    Priority order:
    1. Django setting: settings.SIMULATED_SUBSCRIPTION_MONTH (highest, ideal for automated tests)
    2. Session value: request.session['simulated_subscription_month'] (interactive dev UI)
    3. Environment variable: SIMULATED_SUBSCRIPTION_MONTH (.env file)
    Returns None if no simulation is configured or if simulation is disallowed.
    """
    if not is_simulation_allowed():
        return None

    # 1. Django settings override (e.g. from @override_settings or test configuration)
    settings_val = getattr(settings, 'SIMULATED_SUBSCRIPTION_MONTH', None)
    if settings_val:
        return settings_val

    # 2. Session override from request (passed or thread-local)
    req = request or get_current_request()
    if req and hasattr(req, 'session'):
        session_val = req.session.get('simulated_subscription_month')
        if session_val:
            return session_val

    # 3. Environment variable (e.g., from .env)
    env_val = os.getenv('SIMULATED_SUBSCRIPTION_MONTH')
    if env_val:
        return env_val

    return None


def is_month_simulated(request=None) -> bool:
    """
    Returns True if a development/test simulated month is actively overriding the real month.
    Always returns False in production (when simulation is disallowed).
    """
    if not is_simulation_allowed():
        return False
    return get_simulated_subscription_month_raw(request) is not None


def get_current_subscription_month(request=None) -> date:
    """
    CENTRALIZED SOURCE OF TRUTH for subscription dates.
    All views, services, commands, and models query this function.

    - Production (DEBUG=False): Strictly returns the real month in Africa/Addis_Ababa timezone.
    - Development/Testing: Returns the simulated month if configured, otherwise falls back to real month.
    """
    if not is_simulation_allowed():
        return get_real_subscription_month()

    raw_val = get_simulated_subscription_month_raw(request)
    if raw_val:
        try:
            return normalize_to_month_start(raw_val)
        except (ValueError, TypeError):
            pass

    return get_real_subscription_month()


def set_simulated_month(request, target_month) -> date:
    """
    Safely sets the simulated month in the developer's session.
    Strictly raises PermissionDenied if accessed in production.
    """
    if not is_simulation_allowed():
        raise PermissionDenied("Month simulation is strictly disabled in production.")
    normalized = normalize_to_month_start(target_month)
    request.session['simulated_subscription_month'] = normalized.strftime('%Y-%m-%d')
    return normalized


def clear_simulated_month(request):
    """
    Clears the simulated month from the session, immediately restoring real calendar dates.
    """
    if hasattr(request, 'session') and 'simulated_subscription_month' in request.session:
        del request.session['simulated_subscription_month']


def get_simulated_month_status(request=None) -> dict:
    """
    Returns a complete status dictionary for templates and UI banners.
    """
    real_month = get_real_subscription_month()
    current_month = get_current_subscription_month(request)
    simulated = is_month_simulated(request)
    return {
        'is_simulated': simulated,
        'current_month': current_month,
        'real_month': real_month,
        'current_display': current_month.strftime('%B %Y'),
        'real_display': real_month.strftime('%B %Y'),
        'simulation_allowed': is_simulation_allowed(),
    }


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
    club_settings = ClubSettings.get_settings()
    try:
        with transaction.atomic():
            subscription, created = Subscription.objects.get_or_create(
                member=member,
                subscription_month=target_month,
                defaults={
                    'amount_due': club_settings.monthly_subscription_amount,
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

    club_settings = ClubSettings.get_settings()
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
                    amount_due=club_settings.monthly_subscription_amount,
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
