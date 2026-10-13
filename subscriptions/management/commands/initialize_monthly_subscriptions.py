from datetime import date
from django.core.management.base import BaseCommand
from subscriptions.models import AuditLog
from subscriptions.utils import (
    get_current_subscription_month,
    normalize_to_month_start,
    sync_active_members_subscriptions,
    record_audit
)


class Command(BaseCommand):
    help = "Idempotently initializes monthly subscription records for all active members."

    def add_arguments(self, parser):
        parser.add_argument(
            '--month',
            type=str,
            help="Target month in YYYY-MM format (defaults to current month in Africa/Addis_Ababa)."
        )

    def handle(self, *args, **options):
        month_str = options.get('month')
        if month_str:
            try:
                target_month = normalize_to_month_start(month_str)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Invalid month format '{month_str}'. Expected YYYY-MM."))
                return
        else:
            target_month = get_current_subscription_month()

        self.stdout.write(f"Initializing subscription records for {target_month.strftime('%B %Y')}...")

        result = sync_active_members_subscriptions(target_month)

        record_audit(
            actor=None,
            action_type=AuditLog.Action.SUBSCRIPTIONS_INITIALIZED,
            details=f"System initialized monthly subscriptions for {target_month.strftime('%B %Y')}. Created: {result['created']}, Existing: {result['existing']}."
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Completed successfully for {target_month.strftime('%B %Y')}: "
                f"{result['created']} new unpaid records created, "
                f"{result['existing']} existing records untouched "
                f"(Total active members: {result['total_active']})."
            )
        )
