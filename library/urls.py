from django.urls import path

from . import views

urlpatterns = [
    # ── Core ──────────────────────────────────────────────────────────────
    path("", views.index, name="index"),
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("forgot-password/", views.forgot_password_view, name="forgot-password"),
    path("logout/", views.logout_view, name="logout"),

    # ── Student ───────────────────────────────────────────────────────────
    path("student/", views.student_dashboard, name="student-dashboard"),
    path("session/", views.student_session, name="student-session"),
    path("session/break/", views.take_break, name="take-break"),
    path("session/return/", views.end_break, name="end-break"),
    path("session/extend/", views.extend_session, name="extend-session"),
    path("session/confirm-presence/", views.student_confirm_presence, name="confirm-presence"),
    path("session/release/", views.release_my_seat, name="release-my-seat"),
    path("checkin/<str:desk_number>/", views.checkin, name="checkin"),

    # ── Librarian ─────────────────────────────────────────────────────────
    path("librarian/", views.librarian_dashboard, name="librarian-dashboard"),
    path("desk/<str:desk_number>/", views.desk_detail, name="desk-detail"),
    path("desk/<str:desk_number>/verify/", views.verify_presence, name="verify-presence"),
    path("desk/<str:desk_number>/away/", views.away_mode, name="away-mode"),
    path("desk/<str:desk_number>/checkout/", views.checkout, name="checkout"),
    path("desk/<str:desk_number>/reset/", views.reset_abandoned_desk, name="reset-abandoned"),
    path("desk/<str:desk_number>/cancel-release/", views.cancel_release, name="cancel-release"),
    path("session-expiring/<str:desk_number>/", views.session_expiring, name="session-expiring"),
    path("qr-codes/", views.qr_codes_page, name="qr-codes"),
    path("qr/<str:desk_number>/", views.qr_code_image, name="qr-image"),

    # ── API ───────────────────────────────────────────────────────────────
    path("api/desks/", views.api_desk_status, name="api-desk-status"),
    path("api/session/", views.api_session_clock, name="api-session-clock"),
]
