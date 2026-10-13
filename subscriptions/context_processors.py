from .models import ClubSettings
from .utils import get_current_subscription_month


def club_settings_context(request):
    """
    Exposes club settings and current subscription month to all templates.
    """
    settings = ClubSettings.get_settings()
    current_month = get_current_subscription_month()
    return {
        'club_settings': settings,
        'current_subscription_month': current_month,
        'current_subscription_month_display': current_month.strftime('%B %Y'),
    }
