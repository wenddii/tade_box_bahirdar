from django.urls import path
from . import views

urlpatterns = [
    # Auth & Routing
    path('', views.dashboard_redirect, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_redirect, name='dashboard_redirect'),

    # Public / Club-wide status page
    path('club-status/', views.club_status_page, name='club_status_page'),

    # Member Portal
    path('member/dashboard/', views.member_dashboard, name='member_dashboard'),
    path('member/pay/', views.member_payment_submit, name='member_payment_submit'),
    path('member/history/', views.member_history, name='member_history'),
    path('member/profile/', views.member_profile_view, name='member_profile'),

    # Coach / Admin Portal
    path('coach/dashboard/', views.coach_dashboard, name='coach_dashboard'),
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
]
