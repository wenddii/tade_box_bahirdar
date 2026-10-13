from datetime import date
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    MemberProfile,
    Subscription,
    PaymentSubmission,
    ClubSettings,
    AuditLog
)
from .forms import (
    PaymentSubmissionForm,
    PaymentReviewForm,
    ManualStatusUpdateForm,
    MemberCreateForm,
    MemberEditForm,
    ClubSettingsForm
)
from .utils import (
    get_current_subscription_month,
    normalize_to_month_start,
    ensure_subscription_for_member,
    sync_active_members_subscriptions,
    record_audit
)

User = get_user_model()


def coach_required(view_func):
    """Decorator ensuring access is restricted to coach/staff users."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser):
            return render(request, 'subscriptions/access_denied.html', status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def member_profile_required(view_func):
    """Decorator ensuring user is an authenticated boxer member."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if not hasattr(request.user, 'member_profile'):
            if request.user.is_staff or request.user.is_superuser:
                return redirect('coach_dashboard')
            return render(request, 'subscriptions/access_denied.html', {
                'message': 'No boxer member profile is associated with this account.'
            }, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


# ==============================================================================
# Authentication & Root Routing
# ==============================================================================

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Welcome, {user.first_name or user.username}!")
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect('dashboard_redirect')
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()

    return render(request, 'registration/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')


@login_required
def dashboard_redirect(request):
    """Routes coach to coach dashboard, and member to single member dashboard."""
    if request.user.is_staff or request.user.is_superuser:
        return redirect('coach_dashboard')
    if hasattr(request.user, 'member_profile'):
        return redirect('member_dashboard')
    return render(request, 'subscriptions/access_denied.html', {
        'message': 'No membership profile found for your account.'
    }, status=403)


# ==============================================================================
# Single Member Page (Status, Upload Screenshot, & Current Month Lists)
# ==============================================================================

@member_profile_required
def member_dashboard(request):
    """
    ONE simple, mobile-friendly dashboard for members:
    - View current month status: Pending, Paid, or Unpaid
    - Upload payment screenshot
    - View lists of paid and unpaid members for the current month
    - View how many have paid and how many haven't
    """
    member = request.user.member_profile
    current_month = get_current_subscription_month()
    club_settings = ClubSettings.get_settings()

    # Safely ensure current month subscription exists for this member
    subscription = ensure_subscription_for_member(member, current_month)

    # Handle screenshot upload directly on the dashboard
    if request.method == 'POST':
        form = PaymentSubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                submission = form.save(commit=False)
                submission.subscription = subscription
                submission.member = member
                submission.status = PaymentSubmission.Status.PENDING
                submission.save()

                # A screenshot submission sets status to PENDING (never marks as paid!)
                subscription.status = Subscription.Status.PENDING
                subscription.save(update_fields=['status', 'updated_at'])

            record_audit(
                actor=request.user,
                action_type=AuditLog.Action.PAYMENT_APPROVED, # audit logged for receipt submission
                details=f"Payment screenshot uploaded by {member.display_name} for {subscription.month_display}",
                target_member=member,
                request=request
            )
            messages.success(
                request,
                f"Screenshot uploaded successfully! Coach Tade will review and verify your payment shortly."
            )
            return redirect('member_dashboard')
        else:
            messages.error(request, "Please check the uploaded file. File must be an image (JPEG, PNG, WebP) under 5MB.")
    else:
        form = PaymentSubmissionForm()

    latest_submission = subscription.latest_submission

    # Synchronize all active members so current month lists are live & complete
    sync_active_members_subscriptions(current_month)

    active_members = MemberProfile.objects.filter(is_active_member=True).select_related('user')
    current_subs = {s.member_id: s for s in Subscription.objects.filter(subscription_month=current_month)}

    paid_members = []
    unpaid_members = []
    pending_count = 0

    for m in active_members:
        sub = current_subs.get(m.id)
        status = sub.status if sub else 'UNPAID'
        if status == Subscription.Status.PAID:
            paid_members.append({'profile': m, 'status': 'PAID'})
        elif status == Subscription.Status.PENDING:
            pending_count += 1
            unpaid_members.append({'profile': m, 'status': 'PENDING'})
        else:
            unpaid_members.append({'profile': m, 'status': 'UNPAID'})

    # Alphabetical order
    paid_members.sort(key=lambda x: x['profile'].display_name.lower())
    unpaid_members.sort(key=lambda x: (0 if x['status'] == 'PENDING' else 1, x['profile'].display_name.lower()))

    total_active = len(active_members)
    paid_count = len(paid_members)
    unpaid_count = len(unpaid_members)

    # Deadline date
    deadline_day = min(club_settings.payment_deadline_day, 28)
    deadline_date = date(current_month.year, current_month.month, deadline_day)

    context = {
        'member': member,
        'subscription': subscription,
        'latest_submission': latest_submission,
        'form': form,
        'club_settings': club_settings,
        'current_month': current_month,
        'deadline_date': deadline_date,
        'total_active': total_active,
        'paid_count': paid_count,
        'unpaid_count': unpaid_count,
        'pending_count': pending_count,
        'paid_members': paid_members,
        'unpaid_members': unpaid_members,
    }
    return render(request, 'subscriptions/member_dashboard.html', context)


# Route aliases for member links
@member_profile_required
def member_payment_submit(request):
    """Redirects to the unified member dashboard where the upload form is embedded."""
    return redirect('member_dashboard')


@member_profile_required
def member_history(request):
    member = request.user.member_profile
    current_month = get_current_subscription_month()
    ensure_subscription_for_member(member, current_month)
    subscriptions = member.subscriptions.all().order_by('-subscription_month').prefetch_related('submissions')
    return render(request, 'subscriptions/member_history.html', {'member': member, 'subscriptions': subscriptions})


@member_profile_required
def member_profile_view(request):
    member = request.user.member_profile
    if request.method == 'POST':
        phone = request.POST.get('phone_number', '').strip()
        emergency = request.POST.get('emergency_contact', '').strip()
        member.phone_number = phone
        member.emergency_contact = emergency
        member.save(update_fields=['phone_number', 'emergency_contact', 'updated_at'])
        messages.success(request, "Contact info updated.")
        return redirect('member_profile')
    return render(request, 'subscriptions/member_profile.html', {'member': member})


@login_required
def club_status_page(request):
    """Public club payment-status page."""
    current_month = get_current_subscription_month()
    sync_active_members_subscriptions(current_month)

    active_members = MemberProfile.objects.filter(is_active_member=True).select_related('user')
    subscriptions = Subscription.objects.filter(subscription_month=current_month, member__is_active_member=True)

    paid_list = []
    pending_list = []
    unpaid_list = []
    sub_map = {s.member_id: s for s in subscriptions}

    for member in active_members:
        sub = sub_map.get(member.id)
        if sub and sub.status == Subscription.Status.PAID:
            paid_list.append(member)
        elif sub and sub.status == Subscription.Status.PENDING:
            pending_list.append(member)
        else:
            unpaid_list.append(member)

    paid_list.sort(key=lambda m: m.display_name.lower())
    pending_list.sort(key=lambda m: m.display_name.lower())
    unpaid_list.sort(key=lambda m: m.display_name.lower())

    context = {
        'current_month': current_month,
        'total_active': len(active_members),
        'paid_list': paid_list,
        'pending_list': pending_list,
        'unpaid_list': unpaid_list,
        'paid_count': len(paid_list),
        'pending_count': len(pending_list),
        'unpaid_count': len(unpaid_list),
    }
    return render(request, 'subscriptions/club_status.html', context)


# ==============================================================================
# Single Coach Page (All Members, Screenshots, Inline Approve & Reject)
# ==============================================================================

@coach_required
def coach_dashboard(request):
    """
    ONE simple, comprehensive dashboard for the Coach:
    - See all members and their current monthly payment status.
    - View submitted payment screenshots.
    - Click Approve to mark a payment as Paid.
    - Click Reject to return payment to Unpaid with reason.
    - Add new members with account creation right on the dashboard.
    - Filter by month or status.
    """
    current_month = get_current_subscription_month()
    selected_month_str = request.GET.get('month')

    if selected_month_str:
        try:
            target_month = normalize_to_month_start(selected_month_str)
        except Exception:
            target_month = current_month
    else:
        target_month = current_month

    # Ensure all active members have records for the target month
    sync_active_members_subscriptions(target_month)

    # Distinct months in history
    distinct_months = Subscription.objects.order_by('-subscription_month').values_list(
        'subscription_month', flat=True
    ).distinct()

    # Search and status filters
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'ALL').upper()

    members_qs = MemberProfile.objects.all().select_related('user')
    if search_query:
        members_qs = members_qs.filter(
            Q(display_name__icontains=search_query) |
            Q(user__username__icontains=search_query) |
            Q(phone_number__icontains=search_query)
        )

    # Fetch subscriptions for target month
    subs_map = {
        s.member_id: s
        for s in Subscription.objects.filter(subscription_month=target_month).prefetch_related('submissions')
    }

    # Build member roster with their latest submission
    roster = []
    paid_count = 0
    pending_count = 0
    unpaid_count = 0
    active_count = 0

    for m in members_qs:
        sub = subs_map.get(m.id)
        status = sub.status if sub else 'UNPAID'
        latest_submission = sub.latest_submission if sub else None

        if m.is_active_member:
            active_count += 1
            if status == Subscription.Status.PAID:
                paid_count += 1
            elif status == Subscription.Status.PENDING:
                pending_count += 1
            else:
                unpaid_count += 1

        # Apply status filter
        if status_filter != 'ALL':
            if status_filter == 'INACTIVE' and m.is_active_member:
                continue
            elif status_filter in ['PAID', 'PENDING', 'UNPAID']:
                if not m.is_active_member or status != status_filter:
                    continue

        roster.append({
            'member': m,
            'subscription': sub,
            'status': status,
            'submission': latest_submission,
            'is_active': m.is_active_member,
        })

    # Sort: Pending verifications first, then Unpaid, then Paid
    def sort_key(item):
        if not item['is_active']:
            return (3, item['member'].display_name.lower())
        if item['status'] == 'PENDING':
            return (0, item['member'].display_name.lower())
        if item['status'] == 'UNPAID':
            return (1, item['member'].display_name.lower())
        return (2, item['member'].display_name.lower())

    roster.sort(key=sort_key)

    context = {
        'target_month': target_month,
        'current_month': current_month,
        'distinct_months': distinct_months,
        'roster': roster,
        'total_active': active_count,
        'paid_count': paid_count,
        'pending_count': pending_count,
        'unpaid_count': unpaid_count,
        'status_filter': status_filter,
        'search_query': search_query,
    }
    return render(request, 'subscriptions/coach_dashboard.html', context)


@coach_required
@require_POST
def coach_approve_payment(request, submission_id):
    """Direct inline action: Marks payment submission as Approved and Subscription as Paid."""
    submission = get_object_or_404(
        PaymentSubmission.objects.select_related('subscription', 'member'),
        id=submission_id
    )
    subscription = submission.subscription
    member = submission.member
    now = timezone.now()

    with transaction.atomic():
        submission.status = PaymentSubmission.Status.APPROVED
        submission.reviewed_at = now
        submission.reviewed_by = request.user
        submission.admin_notes = request.POST.get('admin_notes', 'Verified by Coach Tade')
        submission.save()

        subscription.status = Subscription.Status.PAID
        subscription.paid_at = now
        subscription.verified_by = request.user
        subscription.save(update_fields=['status', 'paid_at', 'verified_by', 'updated_at'])

        record_audit(
            actor=request.user,
            action_type=AuditLog.Action.PAYMENT_APPROVED,
            details=f"Payment of {subscription.amount_due} ETB approved for {member.display_name} ({subscription.month_display}).",
            target_member=member,
            request=request
        )

    messages.success(request, f"✓ Payment for {member.display_name} marked as PAID.")
    return redirect(request.META.get('HTTP_REFERER', 'coach_dashboard'))


@coach_required
@require_POST
def coach_reject_payment(request, submission_id):
    """Direct inline action: Rejects submission, returns status to Unpaid with reason."""
    submission = get_object_or_404(
        PaymentSubmission.objects.select_related('subscription', 'member'),
        id=submission_id
    )
    subscription = submission.subscription
    member = submission.member
    reason = request.POST.get('rejection_reason', '').strip()

    if not reason:
        reason = "Screenshot invalid or unreadable. Please transfer and upload a clear receipt."

    now = timezone.now()
    with transaction.atomic():
        submission.status = PaymentSubmission.Status.REJECTED
        submission.reviewed_at = now
        submission.reviewed_by = request.user
        submission.rejection_reason = reason
        submission.save()

        # Subscription reverts to UNPAID
        subscription.status = Subscription.Status.UNPAID
        subscription.paid_at = None
        subscription.verified_by = None
        subscription.save(update_fields=['status', 'paid_at', 'verified_by', 'updated_at'])

        record_audit(
            actor=request.user,
            action_type=AuditLog.Action.PAYMENT_REJECTED,
            details=f"Payment rejected for {member.display_name} ({subscription.month_display}). Reason: {reason}",
            target_member=member,
            request=request
        )

    messages.warning(request, f"Submission rejected. {member.display_name} remains Unpaid and can submit a new screenshot.")
    return redirect(request.META.get('HTTP_REFERER', 'coach_dashboard'))


@coach_required
@require_POST
def coach_quick_add_member(request):
    """Inline action: Coach creates a member account directly from the dashboard."""
    username = request.POST.get('username', '').strip()
    display_name = request.POST.get('display_name', '').strip()
    phone_number = request.POST.get('phone_number', '').strip()
    password = request.POST.get('password', '').strip() or 'boxer2026'

    if not username or not display_name:
        messages.error(request, "Username and full name are required.")
        return redirect('coach_dashboard')

    if User.objects.filter(username=username).exists():
        messages.error(request, f"Username '{username}' already exists. Please choose another.")
        return redirect('coach_dashboard')

    with transaction.atomic():
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=display_name.split()[0] if display_name else username
        )
        profile = MemberProfile.objects.create(
            user=user,
            display_name=display_name,
            phone_number=phone_number,
            is_active_member=True
        )
        # Auto-create current month record as UNPAID
        ensure_subscription_for_member(profile)

    record_audit(
        actor=request.user,
        action_type=AuditLog.Action.MEMBER_CREATED,
        details=f"Created boxer {display_name} (@{username})",
        target_member=profile,
        request=request
    )
    messages.success(request, f"Boxer {display_name} created successfully (Password: {password}).")
    return redirect('coach_dashboard')


# ==============================================================================
# Backward-compatibility routes for test suite and deep links
# ==============================================================================

@coach_required
def coach_review_submission(request, submission_id):
    """Dedicated inspection view for submission review."""
    submission = get_object_or_404(
        PaymentSubmission.objects.select_related('member', 'subscription'),
        id=submission_id
    )
    subscription = submission.subscription
    member = submission.member

    if request.method == 'POST':
        form = PaymentReviewForm(request.POST)
        if form.is_valid():
            action = form.cleaned_data['action']
            rejection_reason = form.cleaned_data.get('rejection_reason', '')
            admin_notes = form.cleaned_data.get('admin_notes', '')

            now = timezone.now()
            with transaction.atomic():
                if action == 'APPROVE':
                    submission.status = PaymentSubmission.Status.APPROVED
                    submission.reviewed_at = now
                    submission.reviewed_by = request.user
                    submission.admin_notes = admin_notes
                    submission.save()

                    subscription.status = Subscription.Status.PAID
                    subscription.paid_at = now
                    subscription.verified_by = request.user
                    subscription.save(update_fields=['status', 'paid_at', 'verified_by', 'updated_at'])

                    record_audit(
                        actor=request.user,
                        action_type=AuditLog.Action.PAYMENT_APPROVED,
                        details=f"Payment approved for {member.display_name}",
                        target_member=member,
                        request=request
                    )
                    messages.success(request, f"Payment for {member.display_name} marked as PAID.")
                else:
                    submission.status = PaymentSubmission.Status.REJECTED
                    submission.reviewed_at = now
                    submission.reviewed_by = request.user
                    submission.rejection_reason = rejection_reason
                    submission.save()

                    subscription.status = Subscription.Status.UNPAID
                    subscription.paid_at = None
                    subscription.verified_by = None
                    subscription.save(update_fields=['status', 'paid_at', 'verified_by', 'updated_at'])

                    record_audit(
                        actor=request.user,
                        action_type=AuditLog.Action.PAYMENT_REJECTED,
                        details=f"Payment rejected for {member.display_name}. Reason: {rejection_reason}",
                        target_member=member,
                        request=request
                    )
                    messages.warning(request, f"Submission rejected.")
            return redirect('coach_dashboard')
    else:
        form = PaymentReviewForm(initial={'action': 'APPROVE'})

    return render(request, 'subscriptions/coach_review_submission.html', {
        'submission': submission,
        'subscription': subscription,
        'member': member,
        'form': form,
    })


@coach_required
def coach_pending_submissions(request):
    return redirect('coach_dashboard')


@coach_required
def coach_member_list(request):
    return redirect('coach_dashboard')


@coach_required
def coach_member_create(request):
    if request.method == 'POST':
        return coach_quick_add_member(request)
    form = MemberCreateForm()
    return render(request, 'subscriptions/coach_member_create.html', {'form': form})


@coach_required
def coach_member_detail(request, member_id):
    member = get_object_or_404(MemberProfile.objects.select_related('user'), id=member_id)
    current_month = get_current_subscription_month()
    current_sub = ensure_subscription_for_member(member, current_month)
    subscriptions = member.subscriptions.all().order_by('-subscription_month').prefetch_related('submissions')
    return render(request, 'subscriptions/coach_member_detail.html', {
        'member': member,
        'current_sub': current_sub,
        'subscriptions': subscriptions,
        'current_month': current_month,
    })


@coach_required
def coach_member_edit(request, member_id):
    member = get_object_or_404(MemberProfile.objects.select_related('user'), id=member_id)
    if request.method == 'POST':
        form = MemberEditForm(request.POST, instance=member)
        if form.is_valid():
            form.save()
            messages.success(request, f"Member updated.")
            return redirect('coach_dashboard')
    else:
        form = MemberEditForm(instance=member)
    return render(request, 'subscriptions/coach_member_edit.html', {'member': member, 'form': form})


@coach_required
@require_POST
def coach_member_toggle_active(request, member_id):
    member = get_object_or_404(MemberProfile, id=member_id)
    member.is_active_member = not member.is_active_member
    member.save(update_fields=['is_active_member', 'updated_at'])
    status_str = "reactivated" if member.is_active_member else "deactivated"
    messages.info(request, f"Member {member.display_name} has been {status_str}.")
    return redirect('coach_dashboard')


@coach_required
def coach_manual_status_change(request, subscription_id):
    subscription = get_object_or_404(Subscription.objects.select_related('member'), id=subscription_id)
    if request.method == 'POST':
        form = ManualStatusUpdateForm(request.POST)
        if form.is_valid():
            new_status = form.cleaned_data['status']
            reason = form.cleaned_data['reason']
            subscription.status = new_status
            if new_status == Subscription.Status.PAID:
                subscription.paid_at = timezone.now()
                subscription.verified_by = request.user
            else:
                subscription.paid_at = None
                subscription.verified_by = None
            subscription.save()
            messages.success(request, f"Status updated to {new_status}.")
            return redirect('coach_dashboard')
    else:
        form = ManualStatusUpdateForm(initial={'status': subscription.status})
    return render(request, 'subscriptions/coach_manual_status_change.html', {'subscription': subscription, 'form': form})


@coach_required
def coach_historical_reports(request):
    current_month = get_current_subscription_month()
    selected_month_str = request.GET.get('month')
    if selected_month_str:
        try:
            selected_month = normalize_to_month_start(selected_month_str)
        except Exception:
            selected_month = current_month
    else:
        selected_month = current_month

    subscriptions = Subscription.objects.filter(
        subscription_month=selected_month
    ).select_related('member')

    paid_count = subscriptions.filter(status=Subscription.Status.PAID).count()
    unpaid_count = subscriptions.filter(status=Subscription.Status.UNPAID).count()
    pending_count = subscriptions.filter(status=Subscription.Status.PENDING).count()

    context = {
        'selected_month': selected_month,
        'subscriptions': subscriptions,
        'paid_count': paid_count,
        'unpaid_count': unpaid_count,
        'pending_count': pending_count,
    }
    return render(request, 'subscriptions/coach_historical_reports.html', context)


@coach_required
def coach_club_settings(request):
    club_settings = ClubSettings.get_settings()
    if request.method == 'POST':
        form = ClubSettingsForm(request.POST, instance=club_settings)
        if form.is_valid():
            form.save()
            messages.success(request, "Club settings updated.")
            return redirect('coach_dashboard')
    else:
        form = ClubSettingsForm(instance=club_settings)
    return render(request, 'subscriptions/coach_club_settings.html', {'form': form, 'club_settings': club_settings})


@coach_required
def coach_audit_logs(request):
    logs = AuditLog.objects.select_related('actor', 'target_member').order_by('-timestamp')[:100]
    return render(request, 'subscriptions/coach_audit_logs.html', {'logs': logs})
