from django.contrib import admin
from .models import MemberProfile, Subscription, PaymentSubmission, ClubSettings, AuditLog


@admin.register(MemberProfile)
class MemberProfileAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'user', 'phone_number', 'is_active_member', 'join_date')
    list_filter = ('is_active_member', 'join_date')
    search_fields = ('display_name', 'user__username', 'phone_number')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('member', 'subscription_month', 'amount_due', 'status', 'paid_at', 'verified_by')
    list_filter = ('status', 'subscription_month')
    search_fields = ('member__display_name', 'member__user__username')


@admin.register(PaymentSubmission)
class PaymentSubmissionAdmin(admin.ModelAdmin):
    list_display = ('member', 'subscription', 'status', 'submitted_at', 'reviewed_at', 'reviewed_by')
    list_filter = ('status', 'submitted_at')
    search_fields = ('member__display_name', 'transaction_reference')


@admin.register(ClubSettings)
class ClubSettingsAdmin(admin.ModelAdmin):
    list_display = ('club_name', 'monthly_subscription_amount', 'payment_deadline_day', 'updated_at')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'actor', 'action_type', 'target_member')
    list_filter = ('action_type', 'timestamp')
    search_fields = ('actor__username', 'target_member__display_name', 'details')
