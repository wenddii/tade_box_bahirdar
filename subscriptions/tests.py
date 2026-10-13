import io
from datetime import date
from decimal import Decimal
from PIL import Image

from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from django.db import IntegrityError, transaction

from subscriptions.models import (
    MemberProfile,
    Subscription,
    PaymentSubmission,
    ClubSettings,
    AuditLog
)
from subscriptions.utils import (
    get_current_subscription_month,
    ensure_subscription_for_member,
    sync_active_members_subscriptions
)

User = get_user_model()


def create_test_image_file():
    """Generates an in-memory PNG image for testing uploads."""
    file = io.BytesIO()
    image = Image.new('RGB', size=(100, 100), color=(200, 50, 50))
    image.save(file, 'JPEG')
    file.name = 'receipt_test.jpg'
    file.seek(0)
    return SimpleUploadedFile(file.name, file.read(), content_type='image/jpeg')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BoxingClubSubscriptionTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # Initialize Club Settings
        self.club_settings = ClubSettings.get_settings()
        self.club_settings.club_name = "Tade Boxing Club"
        self.club_settings.monthly_subscription_amount = Decimal('1500.00')
        self.club_settings.payment_deadline_day = 5
        self.club_settings.save()

        # Create Coach Superuser
        self.coach_user = User.objects.create_superuser(
            username='coach_tade',
            email='coach@tadebox.com',
            password='password123'
        )

        # Create Member A
        self.user_a = User.objects.create_user(
            username='boxer_a',
            email='boxer_a@tadebox.com',
            password='password123'
        )
        self.member_a = MemberProfile.objects.create(
            user=self.user_a,
            display_name='Boxer Alpha',
            phone_number='0911000001',
            is_active_member=True,
            join_date=date(2026, 1, 1)
        )

        # Create Member B
        self.user_b = User.objects.create_user(
            username='boxer_b',
            email='boxer_b@tadebox.com',
            password='password123'
        )
        self.member_b = MemberProfile.objects.create(
            user=self.user_b,
            display_name='Boxer Beta',
            phone_number='0911000002',
            is_active_member=True,
            join_date=date(2026, 1, 1)
        )

    # --------------------------------------------------------------------------
    # 1. A new member is unpaid for the current month
    # --------------------------------------------------------------------------
    def test_new_member_is_unpaid_for_current_month(self):
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member_a, current_month)

        self.assertEqual(sub.member, self.member_a)
        self.assertEqual(sub.subscription_month, current_month)
        self.assertEqual(sub.status, Subscription.Status.UNPAID)
        self.assertEqual(sub.amount_due, Decimal('1500.00'))

    # --------------------------------------------------------------------------
    # 2. A member who paid in October is unpaid in November
    # --------------------------------------------------------------------------
    def test_member_paid_in_october_is_unpaid_in_november(self):
        october = date(2026, 10, 1)
        november = date(2026, 11, 1)

        # Mark Member A as Paid in October
        oct_sub = Subscription.objects.create(
            member=self.member_a,
            subscription_month=october,
            amount_due=Decimal('1500.00'),
            status=Subscription.Status.PAID,
            paid_at=timezone.now(),
            verified_by=self.coach_user
        )
        self.assertEqual(oct_sub.status, Subscription.Status.PAID)

        # In November, the system initializes November record dynamically
        nov_sub = ensure_subscription_for_member(self.member_a, november)

        # November must be separate and UNPAID!
        self.assertEqual(nov_sub.subscription_month, november)
        self.assertEqual(nov_sub.status, Subscription.Status.UNPAID)
        self.assertIsNone(nov_sub.paid_at)

    # --------------------------------------------------------------------------
    # 3. October's paid record remains unchanged after November begins
    # --------------------------------------------------------------------------
    def test_october_paid_record_unchanged_after_november_begins(self):
        october = date(2026, 10, 1)
        november = date(2026, 11, 1)

        oct_sub = Subscription.objects.create(
            member=self.member_a,
            subscription_month=october,
            amount_due=Decimal('1500.00'),
            status=Subscription.Status.PAID,
            paid_at=timezone.now(),
            verified_by=self.coach_user
        )

        # Initialize November
        sync_active_members_subscriptions(november)

        # Re-fetch October record from database
        oct_sub.refresh_from_db()
        self.assertEqual(oct_sub.status, Subscription.Status.PAID)
        self.assertIsNotNone(oct_sub.paid_at)
        self.assertEqual(oct_sub.verified_by, self.coach_user)

    # --------------------------------------------------------------------------
    # 4. Missing monthly records are created safely
    # --------------------------------------------------------------------------
    def test_missing_monthly_records_created_safely(self):
        current_month = get_current_subscription_month()
        self.assertFalse(Subscription.objects.filter(member=self.member_a, subscription_month=current_month).exists())

        sub = ensure_subscription_for_member(self.member_a, current_month)
        self.assertIsNotNone(sub.id)
        self.assertEqual(sub.status, Subscription.Status.UNPAID)

    # --------------------------------------------------------------------------
    # 5. Running the monthly initialization command twice creates no duplicates
    # --------------------------------------------------------------------------
    def test_running_initialize_command_twice_creates_no_duplicates(self):
        current_month = get_current_subscription_month()

        # Run command first time
        call_command('initialize_monthly_subscriptions', f'--month={current_month.strftime("%Y-%m")}')
        count_first = Subscription.objects.filter(subscription_month=current_month).count()
        self.assertEqual(count_first, 2)  # Member A and Member B

        # Run command second time (idempotent)
        call_command('initialize_monthly_subscriptions', f'--month={current_month.strftime("%Y-%m")}')
        count_second = Subscription.objects.filter(subscription_month=current_month).count()
        self.assertEqual(count_second, count_first)

    # --------------------------------------------------------------------------
    # 6. Approved payment submissions mark the correct monthly subscription as paid
    # --------------------------------------------------------------------------
    def test_approved_payment_submission_marks_subscription_paid(self):
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member_a, current_month)

        # Upload proof
        submission = PaymentSubmission.objects.create(
            subscription=sub,
            member=self.member_a,
            screenshot=create_test_image_file(),
            status=PaymentSubmission.Status.PENDING
        )
        sub.status = Subscription.Status.PENDING
        sub.save()

        # Coach approves via POST
        self.client.login(username='coach_tade', password='password123')
        response = self.client.post(
            reverse('coach_review_submission', args=[submission.id]),
            {
                'action': 'APPROVE',
                'admin_notes': 'Bank confirmation verified'
            }
        )
        self.assertEqual(response.status_code, 302)

        sub.refresh_from_db()
        submission.refresh_from_db()

        self.assertEqual(submission.status, PaymentSubmission.Status.APPROVED)
        self.assertEqual(sub.status, Subscription.Status.PAID)
        self.assertIsNotNone(sub.paid_at)
        self.assertEqual(sub.verified_by, self.coach_user)

    # --------------------------------------------------------------------------
    # 7. Rejected submissions do not mark a member as paid
    # --------------------------------------------------------------------------
    def test_rejected_submissions_do_not_mark_paid(self):
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member_a, current_month)

        submission = PaymentSubmission.objects.create(
            subscription=sub,
            member=self.member_a,
            screenshot=create_test_image_file(),
            status=PaymentSubmission.Status.PENDING
        )
        sub.status = Subscription.Status.PENDING
        sub.save()

        # Coach rejects
        self.client.login(username='coach_tade', password='password123')
        response = self.client.post(
            reverse('coach_review_submission', args=[submission.id]),
            {
                'action': 'REJECT',
                'rejection_reason': 'Screenshot is blurry. Transaction number not legible.'
            }
        )
        self.assertEqual(response.status_code, 302)

        sub.refresh_from_db()
        submission.refresh_from_db()

        self.assertEqual(submission.status, PaymentSubmission.Status.REJECTED)
        self.assertEqual(submission.rejection_reason, 'Screenshot is blurry. Transaction number not legible.')
        # Subscription reverts to UNPAID
        self.assertEqual(sub.status, Subscription.Status.UNPAID)
        self.assertIsNone(sub.paid_at)

    # --------------------------------------------------------------------------
    # 8. A member cannot approve their own payment
    # --------------------------------------------------------------------------
    def test_member_cannot_approve_own_payment(self):
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member_a, current_month)
        submission = PaymentSubmission.objects.create(
            subscription=sub,
            member=self.member_a,
            screenshot=create_test_image_file(),
            status=PaymentSubmission.Status.PENDING
        )

        # Log in as Member A and attempt to post to coach review endpoint
        self.client.login(username='boxer_a', password='password123')
        response = self.client.post(
            reverse('coach_review_submission', args=[submission.id]),
            {'action': 'APPROVE'}
        )
        # Must return 403 Access Denied
        self.assertEqual(response.status_code, 403)

        sub.refresh_from_db()
        submission.refresh_from_db()
        # Status must still be PENDING
        self.assertEqual(sub.status, Subscription.Status.UNPAID)
        self.assertEqual(submission.status, PaymentSubmission.Status.PENDING)

    # --------------------------------------------------------------------------
    # 9. A member cannot view another member's private screenshot
    # --------------------------------------------------------------------------
    def test_member_cannot_view_another_member_private_screenshot(self):
        current_month = get_current_subscription_month()
        sub_b = ensure_subscription_for_member(self.member_b, current_month)
        submission_b = PaymentSubmission.objects.create(
            subscription=sub_b,
            member=self.member_b,
            screenshot=create_test_image_file(),
            status=PaymentSubmission.Status.PENDING
        )

        # Log in as Member A and attempt to access coach review page of Member B's submission
        self.client.login(username='boxer_a', password='password123')
        response = self.client.get(reverse('coach_review_submission', args=[submission_b.id]))
        self.assertEqual(response.status_code, 403)

        # Also verify Member A's history view does not contain Member B's submission
        history_response = self.client.get(reverse('member_history'))
        self.assertEqual(history_response.status_code, 200)
        self.assertNotContains(history_response, 'Boxer Beta')

    # --------------------------------------------------------------------------
    # 10. An ordinary member cannot access coach-only views
    # --------------------------------------------------------------------------
    def test_ordinary_member_cannot_access_coach_views(self):
        self.client.login(username='boxer_a', password='password123')

        protected_urls = [
            reverse('coach_dashboard'),
            reverse('coach_pending_submissions'),
            reverse('coach_member_list'),
            reverse('coach_member_create'),
            reverse('coach_historical_reports'),
            reverse('coach_club_settings'),
            reverse('coach_audit_logs'),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, f"Expected 403 for {url}")

    # --------------------------------------------------------------------------
    # 11. Inactive members are handled consistently
    # --------------------------------------------------------------------------
    def test_inactive_members_handled_consistently(self):
        # Create an inactive member
        user_inactive = User.objects.create_user(
            username='boxer_inactive',
            email='inactive@tadebox.com',
            password='password123'
        )
        inactive_member = MemberProfile.objects.create(
            user=user_inactive,
            display_name='Inactive Boxer',
            is_active_member=False
        )

        current_month = get_current_subscription_month()
        # Run sync
        result = sync_active_members_subscriptions(current_month)

        # Inactive member should NOT have a subscription created
        has_sub = Subscription.objects.filter(member=inactive_member, subscription_month=current_month).exists()
        self.assertFalse(has_sub)

        # Inactive member should not appear on public club status page
        self.client.login(username='boxer_a', password='password123')
        status_page = self.client.get(reverse('club_status_page'))
        self.assertNotContains(status_page, 'Inactive Boxer')

    # --------------------------------------------------------------------------
    # 12. Two simultaneous initialization attempts cannot create duplicate subscriptions
    # --------------------------------------------------------------------------
    def test_simultaneous_initialization_no_duplicate_subscriptions(self):
        target_month = date(2027, 1, 1)

        # Attempt to create first
        Subscription.objects.create(
            member=self.member_a,
            subscription_month=target_month,
            amount_due=Decimal('1500.00'),
            status=Subscription.Status.UNPAID
        )

        # Direct duplicate creation must raise IntegrityError inside atomic sub-transaction
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Subscription.objects.create(
                    member=self.member_a,
                    subscription_month=target_month,
                    amount_due=Decimal('1500.00'),
                    status=Subscription.Status.UNPAID
                )

        # ensure_subscription_for_member must handle this gracefully without duplicate
        sub = ensure_subscription_for_member(self.member_a, target_month)
        self.assertEqual(sub.subscription_month, target_month)
        self.assertEqual(Subscription.objects.filter(member=self.member_a, subscription_month=target_month).count(), 1)

    # --------------------------------------------------------------------------
    # 13. The status list correctly separates paid, unpaid, and pending members
    # --------------------------------------------------------------------------
    def test_status_list_correctly_separates_paid_unpaid_pending(self):
        current_month = get_current_subscription_month()

        # Member A: PAID
        sub_a = ensure_subscription_for_member(self.member_a, current_month)
        sub_a.status = Subscription.Status.PAID
        sub_a.paid_at = timezone.now()
        sub_a.save()

        # Member B: PENDING
        sub_b = ensure_subscription_for_member(self.member_b, current_month)
        sub_b.status = Subscription.Status.PENDING
        sub_b.save()

        # Member C: UNPAID
        user_c = User.objects.create_user(username='boxer_c', password='password123')
        member_c = MemberProfile.objects.create(user=user_c, display_name='Boxer Charlie', is_active_member=True)
        sub_c = ensure_subscription_for_member(member_c, current_month)
        sub_c.status = Subscription.Status.UNPAID
        sub_c.save()

        self.client.login(username='boxer_a', password='password123')
        response = self.client.get(reverse('club_status_page'))
        self.assertEqual(response.status_code, 200)

        # Check context lists
        paid_list = response.context['paid_list']
        pending_list = response.context['pending_list']
        unpaid_list = response.context['unpaid_list']

        self.assertIn(self.member_a, paid_list)
        self.assertIn(self.member_b, pending_list)
        self.assertIn(member_c, unpaid_list)

        self.assertEqual(response.context['paid_count'], 1)
        self.assertEqual(response.context['pending_count'], 1)
        self.assertEqual(response.context['unpaid_count'], 1)

    # --------------------------------------------------------------------------
    # 14. Payment history is correctly scoped to the selected month
    # --------------------------------------------------------------------------
    def test_payment_history_correctly_scoped_to_month(self):
        sep_month = date(2026, 9, 1)
        oct_month = date(2026, 10, 1)

        sub_sep = Subscription.objects.create(
            member=self.member_a,
            subscription_month=sep_month,
            amount_due=Decimal('1500.00'),
            status=Subscription.Status.PAID,
            paid_at=timezone.now()
        )
        sub_oct = Subscription.objects.create(
            member=self.member_a,
            subscription_month=oct_month,
            amount_due=Decimal('1500.00'),
            status=Subscription.Status.UNPAID
        )

        self.client.login(username='coach_tade', password='password123')

        # Query September historical report
        response_sep = self.client.get(f"{reverse('coach_historical_reports')}?month=2026-09")
        self.assertEqual(response_sep.status_code, 200)
        self.assertEqual(response_sep.context['selected_month'], sep_month)
        self.assertEqual(response_sep.context['paid_count'], 1)
        self.assertEqual(response_sep.context['unpaid_count'], 0)

        # Query October historical report
        response_oct = self.client.get(f"{reverse('coach_historical_reports')}?month=2026-10")
        self.assertEqual(response_oct.status_code, 200)
        self.assertEqual(response_oct.context['selected_month'], oct_month)
        self.assertEqual(response_oct.context['unpaid_count'], 1)
