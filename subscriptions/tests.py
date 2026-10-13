import io
import os
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
    get_real_subscription_month,
    normalize_to_month_start,
    is_simulation_allowed,
    is_month_simulated,
    set_simulated_month,
    clear_simulated_month,
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

    # --------------------------------------------------------------------------
    # 15. Member direct upload on single dashboard sets status to PENDING
    # --------------------------------------------------------------------------
    def test_member_direct_dashboard_upload(self):
        self.client.login(username='boxer_a', password='password123')
        test_file = create_test_image_file()

        response = self.client.post(
            reverse('member_dashboard'),
            {'screenshot': test_file, 'transaction_reference': 'TX-DIRECT-123'}
        )
        self.assertEqual(response.status_code, 302)

        sub = Subscription.objects.get(member=self.member_a, subscription_month=get_current_subscription_month())
        self.assertEqual(sub.status, Subscription.Status.PENDING)
        self.assertTrue(sub.submissions.filter(transaction_reference='TX-DIRECT-123').exists())

    # --------------------------------------------------------------------------
    # 16. Coach inline approve and reject actions
    # --------------------------------------------------------------------------
    def test_coach_inline_approve_and_reject(self):
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

        self.client.login(username='coach_tade', password='password123')

        # Approve inline
        app_res = self.client.post(reverse('coach_approve_payment', args=[submission.id]))
        self.assertEqual(app_res.status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PAID)

        # Reject inline
        rej_res = self.client.post(
            reverse('coach_reject_payment', args=[submission.id]),
            {'rejection_reason': 'Invalid receipt amount'}
        )
        self.assertEqual(rej_res.status_code, 302)
        sub.refresh_from_db()
        submission.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.UNPAID)
        self.assertEqual(submission.status, PaymentSubmission.Status.REJECTED)
        self.assertEqual(submission.rejection_reason, 'Invalid receipt amount')

    # --------------------------------------------------------------------------
    # 17. Coach quick add member
    # --------------------------------------------------------------------------
    def test_coach_quick_add_member(self):
        self.client.login(username='coach_tade', password='password123')
        res = self.client.post(
            reverse('coach_quick_add_member'),
            {
                'display_name': 'New Boxer Kidane',
                'username': 'kidane_k',
                'phone_number': '0911555555',
                'password': 'boxer2026'
            }
        )
        self.assertEqual(res.status_code, 302)
        new_profile = MemberProfile.objects.get(user__username='kidane_k')
        self.assertEqual(new_profile.display_name, 'New Boxer Kidane')
        self.assertTrue(new_profile.is_active_member)
        # Verify current month subscription was automatically created as UNPAID
        current_month = get_current_subscription_month()
        sub = Subscription.objects.get(member=new_profile, subscription_month=current_month)
        self.assertEqual(sub.status, Subscription.Status.UNPAID)


# ==============================================================================
# 18. Month Simulation Service & Safe Development Month Transitions
# ==============================================================================

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MonthSimulationTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.club_settings = ClubSettings.get_settings()
        self.club_settings.monthly_subscription_amount = Decimal('1500.00')
        self.club_settings.save()

        self.coach_user = User.objects.create_superuser(
            username='coach_sim',
            email='coach_sim@tadebox.com',
            password='password123'
        )

        self.user = User.objects.create_user(
            username='sim_boxer',
            password='password123'
        )
        self.member = MemberProfile.objects.create(
            user=self.user,
            display_name='Simulated Boxer',
            is_active_member=True,
            join_date=date(2026, 1, 1)
        )

    def test_normalize_to_month_start(self):
        self.assertEqual(normalize_to_month_start(date(2026, 10, 15)), date(2026, 10, 1))
        self.assertEqual(normalize_to_month_start('2026-11'), date(2026, 11, 1))
        self.assertEqual(normalize_to_month_start('2026-12-25'), date(2026, 12, 1))
        self.assertEqual(normalize_to_month_start('November 2026'), date(2026, 11, 1))
        with self.assertRaises(ValueError):
            normalize_to_month_start('invalid-date-string')

    @override_settings(ENABLE_MONTH_SIMULATION_IN_TESTS=True, SIMULATED_SUBSCRIPTION_MONTH=date(2026, 11, 1))
    def test_simulation_via_settings_override(self):
        self.assertTrue(is_simulation_allowed())
        self.assertTrue(is_month_simulated())
        self.assertEqual(get_current_subscription_month(), date(2026, 11, 1))

    @override_settings(ENABLE_MONTH_SIMULATION_IN_TESTS=True)
    def test_simulation_via_env_var(self):
        original = os.environ.get('SIMULATED_SUBSCRIPTION_MONTH')
        try:
            os.environ['SIMULATED_SUBSCRIPTION_MONTH'] = '2026-12'
            self.assertTrue(is_month_simulated())
            self.assertEqual(get_current_subscription_month(), date(2026, 12, 1))
        finally:
            if original is not None:
                os.environ['SIMULATED_SUBSCRIPTION_MONTH'] = original
            else:
                os.environ.pop('SIMULATED_SUBSCRIPTION_MONTH', None)

    @override_settings(ENABLE_MONTH_SIMULATION_IN_TESTS=True)
    def test_simulation_via_dev_endpoint_and_session(self):
        # 1. Simulate November 2026 via GET endpoint
        res = self.client.get(reverse('dev_simulate_month') + '?month=2026-11')
        self.assertEqual(res.status_code, 302)
        session = self.client.session
        self.assertEqual(session.get('simulated_subscription_month'), '2026-11-01')

        # 2. Log in as member and view dashboard
        self.client.login(username='sim_boxer', password='password123')
        dash_res = self.client.get(reverse('member_dashboard'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['current_month'], date(2026, 11, 1))
        self.assertEqual(dash_res.context['is_simulated_month'], True)

        # 3. Reset to real date
        reset_res = self.client.get(reverse('dev_simulate_month') + '?action=reset')
        self.assertEqual(reset_res.status_code, 302)
        session = self.client.session
        self.assertNotIn('simulated_subscription_month', session)

    @override_settings(DEBUG=False, ENABLE_MONTH_SIMULATION_IN_TESTS=False)
    def test_strict_production_safeguard(self):
        # Even if settings or env or session has a value, production strictly rejects simulation
        self.assertFalse(is_simulation_allowed())
        self.assertFalse(is_month_simulated())
        self.assertEqual(get_current_subscription_month(), get_real_subscription_month())

        # Calling the dev simulation endpoint in production raises 403 PermissionDenied
        res = self.client.get(reverse('dev_simulate_month') + '?month=2026-11')
        self.assertEqual(res.status_code, 403)

    @override_settings(ENABLE_MONTH_SIMULATION_IN_TESTS=True)
    def test_month_transitions_and_payment_history_preservation(self):
        """
        Requirements 3, 4, and 5:
        - Paid in October appears Unpaid in November if no November payment approved.
        - Previous months' payment records are NEVER deleted or modified.
        - Full payment history remains intact across simulated transitions.
        """
        # Step 1: Simulate October 2026
        oct_month = date(2026, 10, 1)
        with override_settings(SIMULATED_SUBSCRIPTION_MONTH=oct_month):
            self.assertEqual(get_current_subscription_month(), oct_month)
            oct_sub = ensure_subscription_for_member(self.member, oct_month)
            self.assertEqual(oct_sub.status, Subscription.Status.UNPAID)

            # Member uploads screenshot and coach approves for October
            img = create_test_image_file()
            subm = PaymentSubmission.objects.create(
                subscription=oct_sub,
                member=self.member,
                screenshot=img,
                status=PaymentSubmission.Status.PENDING
            )
            oct_sub.status = Subscription.Status.PAID
            oct_sub.paid_at = timezone.now()
            oct_sub.verified_by = self.coach_user
            oct_sub.save()

            # Verify October status
            self.client.login(username='sim_boxer', password='password123')
            res_oct = self.client.get(reverse('member_dashboard'))
            self.assertEqual(res_oct.status_code, 200)
            self.assertEqual(res_oct.context['subscription'].status, Subscription.Status.PAID)

        # Step 2: Transition to November 2026 (No payment made yet)
        nov_month = date(2026, 11, 1)
        with override_settings(SIMULATED_SUBSCRIPTION_MONTH=nov_month):
            self.assertEqual(get_current_subscription_month(), nov_month)

            # Visiting dashboard in November must automatically show UNPAID for November
            res_nov = self.client.get(reverse('member_dashboard'))
            self.assertEqual(res_nov.status_code, 200)
            self.assertEqual(res_nov.context['current_month'], nov_month)
            self.assertEqual(res_nov.context['subscription'].status, Subscription.Status.UNPAID)

            # Coach dashboard in November must list member under UNPAID
            self.client.login(username='coach_sim', password='password123')
            coach_res = self.client.get(reverse('coach_dashboard'))
            self.assertEqual(coach_res.status_code, 200)
            self.assertEqual(coach_res.context['unpaid_count'], 1)
            self.assertEqual(coach_res.context['paid_count'], 0)

            # CRITICAL CHECK: October record MUST still exist and still be PAID!
            oct_sub_check = Subscription.objects.get(member=self.member, subscription_month=oct_month)
            self.assertEqual(oct_sub_check.status, Subscription.Status.PAID)
            self.assertIsNotNone(oct_sub_check.paid_at)

            # Now Member pays for November
            self.client.login(username='sim_boxer', password='password123')
            nov_sub = Subscription.objects.get(member=self.member, subscription_month=nov_month)
            nov_img = create_test_image_file()
            nov_subm = PaymentSubmission.objects.create(
                subscription=nov_sub,
                member=self.member,
                screenshot=nov_img,
                status=PaymentSubmission.Status.PENDING
            )
            # Coach approves November
            self.client.login(username='coach_sim', password='password123')
            app_res = self.client.post(reverse('coach_approve_payment', args=[nov_subm.id]))
            self.assertEqual(app_res.status_code, 302)
            nov_sub.refresh_from_db()
            self.assertEqual(nov_sub.status, Subscription.Status.PAID)

        # Step 3: Transition to December 2026
        dec_month = date(2026, 12, 1)
        with override_settings(SIMULATED_SUBSCRIPTION_MONTH=dec_month):
            self.assertEqual(get_current_subscription_month(), dec_month)

            # Member visits dashboard in December: must be UNPAID
            self.client.login(username='sim_boxer', password='password123')
            res_dec = self.client.get(reverse('member_dashboard'))
            self.assertEqual(res_dec.status_code, 200)
            self.assertEqual(res_dec.context['subscription'].status, Subscription.Status.UNPAID)

            # CRITICAL CHECK: History check - All past records preserved!
            all_subs = self.member.subscriptions.order_by('-subscription_month')
            self.assertEqual(all_subs.count(), 3)
            self.assertEqual(all_subs[0].subscription_month, dec_month)
            self.assertEqual(all_subs[0].status, Subscription.Status.UNPAID)
            self.assertEqual(all_subs[1].subscription_month, nov_month)
            self.assertEqual(all_subs[1].status, Subscription.Status.PAID)
            self.assertEqual(all_subs[2].subscription_month, oct_month)
            self.assertEqual(all_subs[2].status, Subscription.Status.PAID)

            # View Member History page: displays all 3 records
            hist_res = self.client.get(reverse('member_history'))
            self.assertEqual(hist_res.status_code, 200)
            self.assertEqual(len(hist_res.context['subscriptions']), 3)

    def test_coach_manual_status_change_to_paid_without_screenshot(self):
        """
        Coach marks a member as Paid for cash without requiring a screenshot.
        """
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member, current_month)
        self.assertEqual(sub.status, Subscription.Status.UNPAID)
        self.assertEqual(sub.submissions.count(), 0)

        # Coach logs in and marks as Paid
        self.client.login(username='coach_sim', password='password123')
        res = self.client.post(
            reverse('coach_manual_subscription_status', args=[sub.id]),
            {'status': 'PAID'}
        )
        self.assertEqual(res.status_code, 302)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PAID)
        self.assertEqual(sub.verified_by, self.coach_user)
        self.assertIsNotNone(sub.paid_at)
        # Verify no screenshot was required or created
        self.assertEqual(sub.submissions.count(), 0)

        # Verify audit log recorded coach and change
        log = AuditLog.objects.filter(target_member=self.member, action_type=AuditLog.Action.STATUS_MODIFIED).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.coach_user)
        self.assertIn("Cash", log.details)

        # Member sees Paid immediately on member dashboard
        self.client.login(username='sim_boxer', password='password123')
        dash_res = self.client.get(reverse('member_dashboard'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['subscription'].status, Subscription.Status.PAID)

    def test_coach_manual_status_change_back_to_unpaid(self):
        """
        Coach changes a member's status back to Unpaid if necessary.
        """
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member, current_month)
        sub.status = Subscription.Status.PAID
        sub.paid_at = timezone.now()
        sub.verified_by = self.coach_user
        sub.save()

        self.client.login(username='coach_sim', password='password123')
        res = self.client.post(
            reverse('coach_manual_subscription_status', args=[sub.id]),
            {'status': 'UNPAID'}
        )
        self.assertEqual(res.status_code, 302)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.UNPAID)
        self.assertIsNone(sub.paid_at)
        self.assertIsNone(sub.verified_by)

        # Member sees Unpaid immediately on member dashboard
        self.client.login(username='sim_boxer', password='password123')
        dash_res = self.client.get(reverse('member_dashboard'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['subscription'].status, Subscription.Status.UNPAID)

    def test_manual_status_change_preserves_previous_months(self):
        """
        Manual status change in current month strictly leaves previous months unchanged.
        """
        prev_month = date(2026, 9, 1)
        curr_month = date(2026, 10, 1)

        prev_sub = ensure_subscription_for_member(self.member, prev_month)
        prev_sub.status = Subscription.Status.PAID
        prev_sub.paid_at = timezone.now()
        prev_sub.save()

        curr_sub = ensure_subscription_for_member(self.member, curr_month)
        curr_sub.status = Subscription.Status.UNPAID
        curr_sub.save()

        # Coach marks current month as Paid
        self.client.login(username='coach_sim', password='password123')
        self.client.post(
            reverse('coach_manual_subscription_status', args=[curr_sub.id]),
            {'status': 'PAID'}
        )

        curr_sub.refresh_from_db()
        prev_sub.refresh_from_db()
        self.assertEqual(curr_sub.status, Subscription.Status.PAID)
        self.assertEqual(prev_sub.status, Subscription.Status.PAID)

        # Now coach reverts current month to Unpaid
        self.client.post(
            reverse('coach_manual_subscription_status', args=[curr_sub.id]),
            {'status': 'UNPAID'}
        )

        curr_sub.refresh_from_db()
        prev_sub.refresh_from_db()
        self.assertEqual(curr_sub.status, Subscription.Status.UNPAID)
        self.assertEqual(prev_sub.status, Subscription.Status.PAID)  # Unchanged!

    def test_unauthorized_user_cannot_change_manual_status(self):
        """
        Regular boxer/member cannot access coach_manual_subscription_status.
        """
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member, current_month)

        self.client.login(username='sim_boxer', password='password123')
        res = self.client.post(
            reverse('coach_manual_subscription_status', args=[sub.id]),
            {'status': 'PAID'}
        )
        self.assertEqual(res.status_code, 403)  # HTTP 403 Forbidden (not coach)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.UNPAID)

    @override_settings(DEBUG=False, ENABLE_MONTH_SIMULATION_IN_TESTS=False)
    def test_paid_with_cash_button_visible_and_functional_with_debug_false(self):
        """
        Verify that in production (DEBUG=False):
        1. 'Paid with Cash' button is rendered on the Coach Dashboard for unpaid members.
        2. Development simulation controls/banners are NOT exposed.
        3. Coach can mark payment as Paid (cash), which updates the record and creates an audit log.
        4. Previous months remain unchanged.
        5. Member immediately sees Paid status on member dashboard.
        """
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member, current_month)
        sub.status = Subscription.Status.UNPAID
        sub.save()

        # Step 1: Coach visits dashboard with DEBUG=False
        self.client.login(username='coach_sim', password='password123')
        dash_res = self.client.get(reverse('coach_dashboard'))
        self.assertEqual(dash_res.status_code, 200)

        # 'Paid with Cash' button is visible in rendered HTML
        content = dash_res.content.decode('utf-8')
        self.assertIn('Paid with Cash', content)

        # Dev-only month simulation banner/controls must NOT be present
        self.assertNotIn('Dev Simulation Active', content)
        self.assertNotIn('DEV MODE', content)

        # Step 2: Coach clicks 'Paid with Cash'
        post_res = self.client.post(
            reverse('coach_manual_subscription_status', args=[sub.id]),
            {'status': 'PAID'}
        )
        self.assertEqual(post_res.status_code, 302)

        # Record is updated to PAID
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PAID)
        self.assertEqual(sub.verified_by, self.coach_user)
        self.assertIsNotNone(sub.paid_at)

        # Audit trail is preserved
        audit = AuditLog.objects.filter(target_member=self.member, action_type=AuditLog.Action.STATUS_MODIFIED).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.coach_user)
        self.assertIn('Cash', audit.details)

        # Step 3: Member dashboard immediately displays PAID
        self.client.login(username='sim_boxer', password='password123')
        member_dash = self.client.get(reverse('member_dashboard'))
        self.assertEqual(member_dash.status_code, 200)
        self.assertEqual(member_dash.context['subscription'].status, Subscription.Status.PAID)

    @override_settings(DEBUG=False, ENABLE_MONTH_SIMULATION_IN_TESTS=False)
    def test_paid_with_cash_action_forbidden_for_unauthorized_users_with_debug_false(self):
        """
        Verify that in production (DEBUG=False):
        1. Unauthenticated users are redirected to login.
        2. Ordinary members receive HTTP 403 Forbidden.
        3. Subscription remains Unpaid.
        """
        current_month = get_current_subscription_month()
        sub = ensure_subscription_for_member(self.member, current_month)
        sub.status = Subscription.Status.UNPAID
        sub.save()

        # Unauthenticated request
        self.client.logout()
        unauth_res = self.client.post(
            reverse('coach_manual_subscription_status', args=[sub.id]),
            {'status': 'PAID'}
        )
        self.assertEqual(unauth_res.status_code, 302)
        self.assertIn('login', unauth_res.url)

        # Ordinary member request
        self.client.login(username='sim_boxer', password='password123')
        forbidden_res = self.client.post(
            reverse('coach_manual_subscription_status', args=[sub.id]),
            {'status': 'PAID'}
        )
        self.assertEqual(forbidden_res.status_code, 403)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.UNPAID)



