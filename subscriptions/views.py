from datetime import date
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
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


def is_coach_check(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def coach_required(view_func):
    """Decorator for views accessible exclusively to coach/administrators."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser):
            return render(request, 'subscriptions/access_denied.html', status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def member_profile_required(view_func):
    """Decorator ensuring user is authenticated and has an active MemberProfile."""
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
# Authentication & Navigation Routing
# ==============================================================================

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Welcome back, {user.first_name or user.username}!")
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect('dashboard_redirect')
        else:
            messages.error(request, "Invalid username or password. Please try again.")
    else:
        form = AuthenticationForm()

    return render(request, 'registration/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')


@login_required
def dashboard_redirect(request):
    """Smart router sending coach/staff to coach dashboard, and members to member dashboard."""
    if request.user.is_staff or request.user.is_superuser:
        return redirect('coach_dashboard')
    if hasattr(request.user, 'member_profile'):
        return redirect('member_dashboard')
    return render(request, 'subscriptions/access_denied.html', {
        'message': 'Your account is authenticated, but no membership profile is active.'
    }, status=403)


# ==============================================================================
# Member Views
# ==============================================================================

@member_profile_required
def member_dashboard(request):
    member = request.user.member_profile
    current_month = get_current_subscription_month()
    club_settings = ClubSettings.get_settings()

    # Safely ensure current month subscription exists
    subscription = ensure_subscription_for_member(member, current_month)

    # Fetch recent submissions
    latest_submission = subscription.latest_submission
    past_subscriptions = member.subscriptions.exclude(
        subscription_month=current_month
    ).order_by('-subscription_month')[:5]

    # Calculate deadline date for the current month
    deadline_day = min(club_settings.payment_deadline_day, 28)
    deadline_date = date(current_month.year, current_month.month, deadline_day)

    context = {
        'member': member,
        'subscription': subscription,
        'latest_submission': latest_submission,
        'past_subscriptions': past_subscriptions,
        'deadline_date': deadline_date,
        'club_settings': club_settings,
    }
    return render(request, 'subscriptions/member_dashboard.html', context)


@member_profile_required
def member_payment_submit(request):
    member = request.user.member_profile
    current_month = get_current_subscription_month()
    subscription = ensure_subscription_for_member(member, current_month)
    club_settings = ClubSettings.get_settings()

    if subscription.status == Subscription.Status.PAID:
        messages.info(request, f"Your subscription for {subscription.month_display} is already paid and verified!")
        return redirect('member_dashboard')

    latest_submission = subscription.latest_submission

    if request.method == 'POST':
        form = PaymentSubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                submission = form.save(commit=False)
                submission.subscription = subscription
                submission.member = member
                submission.status = PaymentSubmission.Status.PENDING
                submission.save()

                # Update subscription status to pending
                subscription.status = Subscription.Status.PENDING
                subscription.save(update_fields=['status', 'updated_at'])

            record_audit(
                actor=request.user,
                action_type=AuditLog.Action.PAYMENT_APPROVED, # audit entry recorded as submission
                details=f"Payment screenshot submitted by {member.display_name} for {subscription.month_display}",
                target_member=member,
                request=request
            )

            messages.success(
                request,
                f"Your payment screenshot for {subscription.month_display} has been submitted! Coach Tade will review and verify it shortly."
            )
            return redirect('member_dashboard')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PaymentSubmissionForm()

    context = {
        'member': member,
        'subscription': subscription,
        'latest_submission': latest_submission,
        'form': form,
        'club_settings': club_settings,
    }
    return render(request, 'subscriptions/member_payment_submit.html', context)


@member_profile_required
def member_history(request):
    member = request.user.member_profile
    current_month = get_current_subscription_month()
    ensure_subscription_for_member(member, current_month)

    subscriptions = member.subscriptions.all().order_by('-subscription_month').prefetch_related('submissions')

    context = {
        'member': member,
        'subscriptions': subscriptions,
    }
    return render(request, 'subscriptions/member_history.html', context)


@member_profile_required
def member_profile_view(request):
    member = request.user.member_profile
    if request.method == 'POST':
        phone = request.POST.get('phone_number', '').strip()
        emergency = request.POST.get('emergency_contact', '').strip()
        member.phone_number = phone
        member.emergency_contact = emergency
        member.save(update_fields=['phone_number', 'emergency_contact', 'updated_at'])
        messages.success(request, "Your contact information has been updated.")
        return redirect('member_profile')

    return render(request, 'subscriptions/member_profile.html', {'member': member})


# ==============================================================================
# Public / Club-wide Payment Status Page
# ==============================================================================

@login_required
def club_status_page(request):
    """
    Public club payment-status page showing members who have paid, pending,
    and unpaid for the current subscription month.
    Respectful display: Shows only member display name and status.
    Excludes sensitive data (screenshots, tx refs, phone numbers, rejection notes).
    """
    current_month = get_current_subscription_month()

    # Automatically ensure active members have records for current month
    sync_active_members_subscriptions(current_month)

    active_members = MemberProfile.objects.filter(is_active_member=True).select_related('user')
    subscriptions = Subscription.objects.filter(
        subscription_month=current_month,
        member__is_active_member=True
    ).select_related('member')

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

    # Sort alphabetically by display name
    paid_list.sort(key=lambda m: m.display_name.lower())
    pending_list.sort(key=lambda m: m.display_name.lower())
    unpaid_list.sort(key=lambda m: m.display_name.lower())

    total_active = len(active_members)

    context = {
        'current_month': current_month,
        'total_active': total_active,
        'paid_list': paid_list,
        'pending_list': pending_list,
        'unpaid_list': unpaid_list,
        'paid_count': len(paid_list),
        'pending_count': len(pending_list),
        'unpaid_count': len(unpaid_list),
    }
    return render(request, 'subscriptions/club_status.html', context)


# ==============================================================================
# Coach / Admin Views
# ==============================================================================

@coach_required
def coach_dashboard(request):
    current_month = get_current_subscription_month()
    sync_active_members_subscriptions(current_month)

    active_count = MemberProfile.objects.filter(is_active_member=True).count()
    current_subs = Subscription.objects.filter(
        subscription_month=current_month,
        member__is_active_member=True
    )

    paid_count = current_subs.filter(status=Subscription.Status.PAID).count()
    pending_count = current_subs.filter(status=Subscription.Status.PENDING).count()
    unpaid_count = current_subs.filter(status=Subscription.Status.UNPAID).count()

    # Pending submissions needing verification
    pending_submissions = PaymentSubmission.objects.filter(
        status=PaymentSubmission.Status.PENDING
    ).select_related('member', 'subscription').order_by('-submitted_at')[:10]

    # Recent activity audit log
    recent_audits = AuditLog.objects.select_related('actor', 'target_member').order_by('-timestamp')[:8]

    context = {
        'current_month': current_month,
        'active_count': active_count,
        'paid_count': paid_count,
        'pending_count': pending_count,
        'unpaid_count': unpaid_count,
        'pending_submissions': pending_submissions,
        'recent_audits': recent_audits,
    }
    return render(request, 'subscriptions/coach_dashboard.html', context)


@coach_required
def coach_pending_submissions(request):
    """Detailed queue of all payment submissions awaiting coach review."""
    pending_submissions = PaymentSubmission.objects.filter(
        status=PaymentSubmission.Status.PENDING
    ).select_related('member', 'subscription').order_by('submitted_at')

    return render(request, 'subscriptions/coach_pending_submissions.html', {
        'pending_submissions': pending_submissions
    })


@coach_required
def coach_review_submission(request, submission_id):
    """
    Coach inspection view: shows full screenshot preview, details,
    and handles Approve or Reject with feedback.
    """
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
                        details=f"Payment of {subscription.amount_due} ETB approved for {member.display_name} ({subscription.month_display}). Notes: {admin_notes}",
                        target_member=member,
                        request=request
                    )
                    messages.success(request, f"Payment for {member.display_name} has been approved and marked Paid!")

                elif action == 'REJECT':
                    submission.status = PaymentSubmission.Status.REJECTED
                    submission.reviewed_at = now
                    submission.reviewed_by = request.user
                    submission.rejection_reason = rejection_reason
                    submission.admin_notes = admin_notes
                    submission.save()

                    # Subscription reverts to UNPAID
                    subscription.status = Subscription.Status.UNPAID
                    subscription.paid_at = None
                    subscription.verified_by = None
                    subscription.save(update_fields=['status', 'paid_at', 'verified_by', 'updated_at'])

                    record_audit(
                        actor=request.user,
                        action_type=AuditLog.Action.PAYMENT_REJECTED,
                        details=f"Payment rejected for {member.display_name} ({subscription.month_display}). Reason: {rejection_reason}",
                        target_member=member,
                        request=request
                    )
                    messages.warning(request, f"Submission rejected. Feedback has been sent to {member.display_name}.")

            return redirect('coach_pending_submissions')
    else:
        form = PaymentReviewForm(initial={'action': 'APPROVE'})

    context = {
        'submission': submission,
        'subscription': subscription,
        'member': member,
        'form': form,
    }
    return render(request, 'subscriptions/coach_review_submission.html', context)


@coach_required
def coach_member_list(request):
    """
    Coach member directory with search and status filtering.
    """
    current_month = get_current_subscription_month()
    sync_active_members_subscriptions(current_month)

    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'ALL').upper()

    members = MemberProfile.objects.all().select_related('user')

    if search_query:
        members = members.filter(
            Q(display_name__icontains=search_query) |
            Q(user__username__icontains=search_query) |
            Q(phone_number__icontains=search_query)
        )

    # Attach current month subscription to each member
    subs = {s.member_id: s for s in Subscription.objects.filter(subscription_month=current_month)}
    member_data = []

    for m in members:
        sub = subs.get(m.id)
        current_status = sub.status if sub else 'UNPAID'
        if not m.is_active_member:
            current_status = 'INACTIVE'

        if status_filter != 'ALL':
            if status_filter == 'INACTIVE' and m.is_active_member:
                continue
            elif status_filter in ['PAID', 'PENDING', 'UNPAID']:
                if not m.is_active_member or current_status != status_filter:
                    continue

        member_data.append({
            'profile': m,
            'current_subscription': sub,
            'status': current_status,
        })

    context = {
        'member_data': member_data,
        'search_query': search_query,
        'status_filter': status_filter,
        'current_month': current_month,
    }
    return render(request, 'subscriptions/coach_member_list.html', context)


@coach_required
def coach_member_create(request):
    if request.method == 'POST':
        form = MemberCreateForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            display_name = form.cleaned_data['display_name']
            email = form.cleaned_data.get('email', '')
            phone_number = form.cleaned_data.get('phone_number', '')
            password = form.cleaned_data['password']
            emergency_contact = form.cleaned_data.get('emergency_contact', '')
            notes = form.cleaned_data.get('notes', '')

            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=display_name.split()[0],
                    last_name=' '.join(display_name.split()[1:]) if len(display_name.split()) > 1 else ''
                )
                profile = MemberProfile.objects.create(
                    user=user,
                    display_name=display_name,
                    phone_number=phone_number,
                    emergency_contact=emergency_contact,
                    notes=notes,
                    is_active_member=True
                )
                # Auto-initialize current month subscription
                ensure_subscription_for_member(profile)

            record_audit(
                actor=request.user,
                action_type=AuditLog.Action.MEMBER_CREATED,
                details=f"Created member account for {display_name} (@{username})",
                target_member=profile,
                request=request
            )
            messages.success(request, f"Member {display_name} created successfully!")
            return redirect('coach_member_detail', member_id=profile.id)
    else:
        form = MemberCreateForm()

    return render(request, 'subscriptions/coach_member_create.html', {'form': form})


@coach_required
def coach_member_detail(request, member_id):
    member = get_object_or_404(MemberProfile.objects.select_related('user'), id=member_id)
    current_month = get_current_subscription_month()
    current_sub = ensure_subscription_for_member(member, current_month)

    subscriptions = member.subscriptions.all().order_by('-subscription_month').prefetch_related('submissions')
    audits = AuditLog.objects.filter(target_member=member).order_by('-timestamp')[:10]

    context = {
        'member': member,
        'current_sub': current_sub,
        'subscriptions': subscriptions,
        'audits': audits,
        'current_month': current_month,
    }
    return render(request, 'subscriptions/coach_member_detail.html', context)


@coach_required
def coach_member_edit(request, member_id):
    member = get_object_or_404(MemberProfile.objects.select_related('user'), id=member_id)
    if request.method == 'POST':
        form = MemberEditForm(request.POST, instance=member)
        if form.is_valid():
            form.save()
            record_audit(
                actor=request.user,
                action_type=AuditLog.Action.MEMBER_UPDATED,
                details=f"Updated profile for {member.display_name}",
                target_member=member,
                request=request
            )
            messages.success(request, f"Profile for {member.display_name} updated successfully.")
            return redirect('coach_member_detail', member_id=member.id)
    else:
        form = MemberEditForm(instance=member)

    return render(request, 'subscriptions/coach_member_edit.html', {'member': member, 'form': form})


@coach_required
@require_POST
def coach_member_toggle_active(request, member_id):
    member = get_object_or_404(MemberProfile, id=member_id)
    member.is_active_member = not member.is_active_member
    member.save(update_fields=['is_active_member', 'updated_at'])

    action = AuditLog.Action.MEMBER_REACTIVATED if member.is_active_member else AuditLog.Action.MEMBER_DEACTIVATED
    record_audit(
        actor=request.user,
        action_type=action,
        details=f"Member {'reactivated' if member.is_active_member else 'deactivated'} by coach.",
        target_member=member,
        request=request
    )
    status_str = "reactivated" if member.is_active_member else "deactivated"
    messages.info(request, f"Member {member.display_name} has been {status_str}.")
    return redirect('coach_member_detail', member_id=member.id)


@coach_required
def coach_manual_status_change(request, subscription_id):
    """
    Allows coach to change a member's payment status with mandatory audit record.
    """
    subscription = get_object_or_404(
        Subscription.objects.select_related('member', 'member__user'),
        id=subscription_id
    )

    if request.method == 'POST':
        form = ManualStatusUpdateForm(request.POST)
        if form.is_valid():
            new_status = form.cleaned_data['status']
            reason = form.cleaned_data['reason']
            old_status = subscription.status

            with transaction.atomic():
                subscription.status = new_status
                if new_status == Subscription.Status.PAID:
                    subscription.paid_at = timezone.now()
                    subscription.verified_by = request.user
                elif new_status == Subscription.Status.UNPAID:
                    subscription.paid_at = None
                    subscription.verified_by = None
                subscription.save()

                record_audit(
                    actor=request.user,
                    action_type=AuditLog.Action.STATUS_MODIFIED,
                    details=f"Manually changed status for {subscription.member.display_name} ({subscription.month_display}) from {old_status} to {new_status}. Reason: {reason}",
                    target_member=subscription.member,
                    request=request
                )

            messages.success(
                request,
                f"Status for {subscription.member.display_name} updated to {new_status}."
            )
            return redirect('coach_member_detail', member_id=subscription.member.id)
    else:
        form = ManualStatusUpdateForm(initial={'status': subscription.status})

    return render(request, 'subscriptions/coach_manual_status_change.html', {
        'subscription': subscription,
        'form': form,
    })


@coach_required
def coach_historical_reports(request):
    """
    View payment history organized by month, with aggregate statistics.
    """
    # Get distinct months in database
    distinct_months = Subscription.objects.order_by('-subscription_month').values_list(
        'subscription_month', flat=True
    ).distinct()

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
    ).select_related('member', 'verified_by').order_by('status', 'member__display_name')

    total_count = subscriptions.count()
    paid_count = subscriptions.filter(status=Subscription.Status.PAID).count()
    pending_count = subscriptions.filter(status=Subscription.Status.PENDING).count()
    unpaid_count = subscriptions.filter(status=Subscription.Status.UNPAID).count()

    total_collected = sum(
        s.amount_due for s in subscriptions if s.status == Subscription.Status.PAID
    )

    context = {
        'distinct_months': distinct_months,
        'selected_month': selected_month,
        'subscriptions': subscriptions,
        'total_count': total_count,
        'paid_count': paid_count,
        'pending_count': pending_count,
        'unpaid_count': unpaid_count,
        'total_collected': total_collected,
    }
    return render(request, 'subscriptions/coach_historical_reports.html', context)


@coach_required
def coach_club_settings(request):
    club_settings = ClubSettings.get_settings()
    if request.method == 'POST':
        form = ClubSettingsForm(request.POST, instance=club_settings)
        if form.is_valid():
            form.save()
            record_audit(
                actor=request.user,
                action_type=AuditLog.Action.SETTINGS_UPDATED,
                details=f"Club settings updated (Monthly fee: {club_settings.monthly_subscription_amount} ETB, Deadline: day {club_settings.payment_deadline_day})",
                request=request
            )
            messages.success(request, "Club settings updated successfully.")
            return redirect('coach_club_settings')
    else:
        form = ClubSettingsForm(instance=club_settings)

    return render(request, 'subscriptions/coach_club_settings.html', {'form': form, 'club_settings': club_settings})


@coach_required
def coach_audit_logs(request):
    logs = AuditLog.objects.select_related('actor', 'target_member').order_by('-timestamp')[:100]
    return render(request, 'subscriptions/coach_audit_logs.html', {'logs': logs})
