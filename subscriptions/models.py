from decimal import Decimal
from datetime import date
from django.conf import settings
from django.db import models
from django.utils import timezone


class MemberProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='member_profile'
    )
    display_name = models.CharField(max_length=120)
    phone_number = models.CharField(max_length=30, blank=True, default='')
    is_active_member = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Inactive members are excluded from monthly payment cycles."
    )
    join_date = models.DateField(default=timezone.now)
    emergency_contact = models.CharField(max_length=120, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_name']
        verbose_name = 'Member Profile'
        verbose_name_plural = 'Member Profiles'

    def __str__(self):
        return f"{self.display_name} ({'Active' if self.is_active_member else 'Inactive'})"

    def get_subscription_for_month(self, target_month):
        """Returns the subscription object for target_month (first day of month), or None."""
        return self.subscriptions.filter(subscription_month=target_month).first()


class Subscription(models.Model):
    class Status(models.TextChoices):
        UNPAID = 'UNPAID', 'Unpaid'
        PENDING = 'PENDING', 'Pending Verification'
        PAID = 'PAID', 'Paid'

    member = models.ForeignKey(
        MemberProfile,
        on_delete=models.CASCADE,
        related_name='subscriptions'
    )
    subscription_month = models.DateField(
        db_index=True,
        help_text="Normalized to 1st day of the calendar month (YYYY-MM-01)"
    )
    amount_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('1500.00')
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNPAID,
        db_index=True
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_subscriptions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-subscription_month', 'member__display_name']
        constraints = [
            models.UniqueConstraint(
                fields=['member', 'subscription_month'],
                name='unique_member_subscription_month'
            )
        ]
        verbose_name = 'Monthly Subscription'
        verbose_name_plural = 'Monthly Subscriptions'

    def __str__(self):
        return f"{self.member.display_name} - {self.subscription_month.strftime('%B %Y')} [{self.status}]"

    @property
    def month_display(self):
        return self.subscription_month.strftime('%B %Y')

    @property
    def latest_submission(self):
        return self.submissions.order_by('-submitted_at').first()


class PaymentSubmission(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Verification'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='submissions'
    )
    member = models.ForeignKey(
        MemberProfile,
        on_delete=models.CASCADE,
        related_name='payment_submissions'
    )
    screenshot = models.ImageField(
        upload_to='payment_screenshots/%Y/%m/',
        help_text="Uploaded bank or mobile money transfer confirmation screenshot"
    )
    transaction_reference = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="Optional bank or Telebirr transaction reference code"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_submissions'
    )
    rejection_reason = models.TextField(
        blank=True,
        default='',
        help_text="Feedback provided to the member explaining why the proof was rejected"
    )
    admin_notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Payment Submission'
        verbose_name_plural = 'Payment Submissions'

    def __str__(self):
        return f"Submission by {self.member.display_name} for {self.subscription.month_display} [{self.status}]"


class ClubSettings(models.Model):
    club_name = models.CharField(max_length=150, default="Tade Boxing Club")
    monthly_subscription_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('1500.00'),
        help_text="Standard monthly membership fee in ETB"
    )
    currency_symbol = models.CharField(max_length=10, default="ETB")
    payment_deadline_day = models.PositiveSmallIntegerField(
        default=5,
        help_text="Calendar day of the month when subscriptions are due (e.g. 5th of every month)"
    )
    payment_instructions = models.TextField(
        default=(
            "1. Commercial Bank of Ethiopia (CBE):\n"
            "   Account Name: Tade Boxing Club\n"
            "   Account Number: 1000234567890\n\n"
            "2. Telebirr:\n"
            "   Mobile / Merchant Number: 0911223344 (Coach Tade)\n\n"
            "3. Awash Bank:\n"
            "   Account Number: 01425678912300\n\n"
            "Note: Please transfer the exact monthly subscription amount and take a clear screenshot of the confirmation receipt showing the transaction reference, date, and amount."
        ),
        help_text="Bank and mobile money payment details shown to members on the payment page"
    )
    contact_phone = models.CharField(max_length=50, default="+251 91 123 4567", blank=True)
    contact_email = models.EmailField(default="coach@tadebox.com", blank=True)
    location = models.CharField(max_length=255, default="Addis Ababa, Ethiopia", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Club Settings'
        verbose_name_plural = 'Club Settings'

    def __str__(self):
        return self.club_name

    @classmethod
    def get_settings(cls):
        """Retrieves or initializes the singleton club settings."""
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj


class AuditLog(models.Model):
    class Action(models.TextChoices):
        PAYMENT_APPROVED = 'PAYMENT_APPROVED', 'Payment Approved'
        PAYMENT_REJECTED = 'PAYMENT_REJECTED', 'Payment Rejected'
        STATUS_MODIFIED = 'STATUS_MODIFIED', 'Status Modified Manually'
        MEMBER_CREATED = 'MEMBER_CREATED', 'Member Created'
        MEMBER_UPDATED = 'MEMBER_UPDATED', 'Member Updated'
        MEMBER_DEACTIVATED = 'MEMBER_DEACTIVATED', 'Member Deactivated'
        MEMBER_REACTIVATED = 'MEMBER_REACTIVATED', 'Member Reactivated'
        SETTINGS_UPDATED = 'SETTINGS_UPDATED', 'Club Settings Updated'
        SUBSCRIPTIONS_INITIALIZED = 'SUBSCRIPTIONS_INITIALIZED', 'Monthly Subscriptions Initialized'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    target_member = models.ForeignKey(
        MemberProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='target_audit_logs'
    )
    action_type = models.CharField(max_length=50, choices=Action.choices, db_index=True)
    details = models.TextField(blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'

    def __str__(self):
        actor_name = self.actor.username if self.actor else 'System'
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {actor_name}: {self.get_action_type_display()}"
