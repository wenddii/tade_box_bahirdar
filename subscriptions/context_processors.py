from .models import ClubSettings
from .utils import get_simulated_month_status


def club_settings_context(request):
    """
    Exposes club settings and current subscription month (including simulation state) to all templates.
    """
    settings = ClubSettings.get_settings()
    status_info = get_simulated_month_status(request)
    return {
        'club_settings': settings,
        'current_subscription_month': status_info['current_month'],
        'current_subscription_month_display': status_info['current_display'],
        'real_subscription_month': status_info['real_month'],
        'real_subscription_month_display': status_info['real_display'],
        'is_simulated_month': status_info['is_simulated'],
        'simulation_allowed': status_info['simulation_allowed'],
    }
