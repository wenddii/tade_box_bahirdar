from django.urls import path
from . import views

urlpatterns = [
    # Auth & Smart Routing
    path('', views.dashboard_redirect, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_redirect, name='dashboard_redirect'),

    # Public / Club-wide status page
    path('club-status/', views.club_status_page, name='club_status_page'),

    # 1. Single Member Dashboard (status, upload screenshot, and current month paid/unpaid lists)
    path('member/', views.member_dashboard, name='member_dashboard'),
    path('member/dashboard/', views.member_dashboard, name='member_dashboard_alt'),
    path('member/pay/', views.member_payment_submit, name='member_payment_submit'),
    path('member/history/', views.member_history, name='member_history'),
    path('member/profile/', views.member_profile_view, name='member_profile'),

    # 2. Single Coach Dashboard (all members, screenshots, inline approve & reject, member addition)
    path('coach/', views.coach_dashboard, name='coach_dashboard'),
    path('coach/dashboard/', views.coach_dashboard, name='coach_dashboard_alt'),
    path('coach/approve/<int:submission_id>/', views.coach_approve_payment, name='coach_approve_payment'),
    path('coach/reject/<int:submission_id>/', views.coach_reject_payment, name='coach_reject_payment'),
    path('coach/subscriptions/<int:subscription_id>/status/', views.coach_manual_subscription_status, name='coach_manual_subscription_status'),
    path('coach/quick-add/', views.coach_quick_add_member, name='coach_quick_add_member'),

    # Backward compatibility routes
    path('coach/submissions/', views.coach_pending_submissions, name='coach_pending_submissions'),
    path('coach/submissions/<int:submission_id>/review/', views.coach_review_submission, name='coach_review_submission'),
    path('coach/members/', views.coach_member_list, name='coach_member_list'),
    path('coach/members/new/', views.coach_member_create, name='coach_member_create'),
    path('coach/members/<int:member_id>/', views.coach_member_detail, name='coach_member_detail'),
    path('coach/members/<int:member_id>/edit/', views.coach_member_edit, name='coach_member_edit'),
    path('coach/members/<int:member_id>/toggle-active/', views.coach_member_toggle_active, name='coach_member_toggle_active'),
    path('coach/subscriptions/<int:subscription_id>/manual-status/', views.coach_manual_status_change, name='coach_manual_status_change'),
    path('coach/reports/', views.coach_historical_reports, name='coach_historical_reports'),
    path('coach/settings/', views.coach_club_settings, name='coach_club_settings'),
    path('coach/audit-logs/', views.coach_audit_logs, name='coach_audit_logs'),

    # 3. Development-only Month Simulation Controls
    path('dev/simulate-month/', views.dev_simulate_month, name='dev_simulate_month'),
]
