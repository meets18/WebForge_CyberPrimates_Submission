"""
views.py — DeskGuard library views.

Authentication:
  - Students: register with real details, get auto-issued WF-YY-XXXX card,
    log in with card_number + password (stored hashed in Student model).
  - Librarian: single fixed account, password = "librarian123".

Timers are authoritative from the DB (Desk.session_started_at).
Browser polls /api/session/ every second for the live clock.
"""
import io
import json
import random
from datetime import timedelta
from functools import wraps

from django.contrib.auth.hashers import check_password, make_password
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .data import desk_counts, get_all_desks, get_desk, set_desk_state

# ── Constants ────────────────────────────────────────────────────────────────

LIBRARIAN_CARD   = "WF-26-LB01"
LIBRARIAN_PASSWORD = "librarian123"
SESSION_DURATION_MINUTES = 120
AWAY_DURATION_MINUTES = 20


# ── Session helpers ───────────────────────────────────────────────────────────

def _current_user(request):
    return request.session.get("deskguard_user")


def _role(request):
    user = _current_user(request)
    return user["role"] if user else None


def _is_logged_in(request):
    return _current_user(request) is not None


def _assigned_desk_number(request):
    return request.session.get("deskguard_desk")


# ── Auth decorators ───────────────────────────────────────────────────────────

def login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not _is_logged_in(request):
            return redirect("login")
        return view_func(request, *args, **kwargs)
    return wrapper


def role_required(required_role):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not _is_logged_in(request):
                return redirect("login")
            if _role(request) != required_role:
                return redirect("librarian-dashboard" if _role(request) == "librarian" else "student-dashboard")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ── Context builders ──────────────────────────────────────────────────────────

def _auth_context(request):
    user = _current_user(request)
    return {
        "current_user": user,
        "is_logged_in": bool(user),
        "current_role": user["role"] if user else None,
        "assigned_desk_number": _assigned_desk_number(request),
    }


def _session_view_context(request):
    """Build context for the student session page from DB state."""
    desk_number = _assigned_desk_number(request)
    if not desk_number:
        return {}

    desk = get_desk(desk_number)
    if not desk:
        return {}

    now = timezone.now()

    # If desk was abandoned or freed while student is watching, signal that
    if desk["status"] in ("available", "abandoned"):
        return {"desk": desk, "desk_released": True}

    elapsed_secs = desk["elapsed_seconds"]
    remaining_secs = desk["remaining_seconds"]
    remaining_minutes = remaining_secs // 60

    # Away break info
    on_break = desk["status"] == "away"
    break_remaining = None
    break_end_time = None
    show_away_warning = False
    away_warning_seconds = 0
    if on_break and desk["break_end_at"]:
        from datetime import datetime
        break_end_dt = datetime.fromisoformat(desk["break_end_at"])
        if break_end_dt > now:
            break_remaining_secs = int((break_end_dt - now).total_seconds())
            break_remaining = f"{break_remaining_secs // 60}m {break_remaining_secs % 60:02d}s"
            break_end_time = timezone.localtime(break_end_dt).strftime("%I:%M %p")
        else:
            grace_expiry = break_end_dt + timedelta(seconds=60)
            if grace_expiry > now:
                show_away_warning = True
                away_warning_seconds = int((grace_expiry - now).total_seconds())
                break_remaining = "0m 00s"

    # Scrub purpose and approximate time for the student
    desk["purpose"] = ""
    desk["approximate_time"] = ""

    def fmt(secs):
        s = max(int(secs), 0)
        h, r = divmod(s, 3600)
        m = r // 60
        if h:
            return f"{h}h {m:02d}m"
        return f"{m}m"

    return {
        "desk": desk,
        "session_started_at": desk["checked_in"],
        "elapsed_time": fmt(elapsed_secs),
        "remaining_time": fmt(remaining_secs),
        "on_break": on_break,
        "break_end_time": break_end_time,
        "break_remaining": break_remaining,
        "show_away_warning": show_away_warning,
        "away_warning_seconds": away_warning_seconds,
        "warning_active": 0 < remaining_minutes <= 10,
        "remaining_minutes": remaining_minutes,
        "desk_notice": desk.get("notice"),
        "desk_notice_kind": desk.get("notice_kind"),
        # Raw values for the JS clock bootstrap
        "elapsed_seconds_init": desk["elapsed_seconds"],
        "remaining_seconds_init": desk["remaining_seconds"],
        "away_remaining_seconds_init": desk.get("away_remaining_seconds", 0),
    }


# ── Validation helpers ────────────────────────────────────────────────────────

import re

# College ID pattern: yyCTExxx where yy ∈ {22..26}, xxx ∈ {001..010}
_COLLEGE_ID_RE = re.compile(r'^(2[2-6])CTE(00[1-9]|010)$', re.IGNORECASE)

# Indian mobile: 10 digits starting with 6-9 (stored normalised, no country code)
_PHONE_RE = re.compile(r'^[6-9]\d{9}$')

# Strong password: 8+ chars, upper, lower, digit, special
_SPECIAL_RE = re.compile(r'[!@#$%^&*(),.?":{}|<>_\-+=/\\\[\];\'`~]')


def _validate_phone(raw: str):
    """
    Normalise and validate an Indian mobile number.
    Accepts: +91XXXXXXXXXX, 91XXXXXXXXXX, 0XXXXXXXXXX, or XXXXXXXXXX.
    Returns the clean 10-digit string, or None if invalid.
    """
    digits = re.sub(r'[\s\-\(\)]', '', raw)  # strip whitespace/dashes/parens
    if digits.startswith('+91'):
        digits = digits[3:]
    elif digits.startswith('91') and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith('0') and len(digits) == 11:
        digits = digits[1:]
    if _PHONE_RE.match(digits):
        return digits
    return None


def _validate_password(pw: str):
    """
    Returns an error string, or None if password passes all rules.
    """
    if len(pw) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r'[A-Z]', pw):
        return "Password must contain at least one uppercase letter."
    if not re.search(r'[a-z]', pw):
        return "Password must contain at least one lowercase letter."
    if not re.search(r'\d', pw):
        return "Password must contain at least one number."
    if not _SPECIAL_RE.search(pw):
        return "Password must contain at least one special character (!@#$%^&* etc.)."
    return None


def _mask_email(email):
    if not email or "@" not in email:
        return "your email"
    local, domain = email.split("@", 1)
    visible = local[:2]
    return f"{visible}{'*' * max(len(local) - 2, 2)}@{domain}"


def _ensure_desk_still_assigned(request):
    """
    Returns True if assignment is still valid or was recently freed/abandoned
    so the student can see the release reason before returning to the map.
    """
    desk_number = _assigned_desk_number(request)
    if not desk_number:
        user = _current_user(request)
        if user and user.get("role") == "student":
            from .models import Desk, Student
            try:
                student = Student.objects.get(card_number=user["card_number"])
                active_desk = Desk.objects.filter(occupied_by=student, status__in=["occupied", "away"]).first()
                if active_desk:
                    request.session["deskguard_desk"] = active_desk.number
                    desk_number = active_desk.number
                    if active_desk.logged_out_at is not None:
                        active_desk.logged_out_at = None
                        active_desk.save()
            except Student.DoesNotExist:
                pass

    if not desk_number:
        return False
    desk = get_desk(desk_number)
    if not desk:
        request.session.pop("deskguard_desk", None)
        return False
    
    # If the desk was freed or abandoned, let the student load the released screen.
    if desk["status"] in ("available", "abandoned"):
        return True

    # If occupied/away, verify it belongs to the current user
    user = _current_user(request)
    if desk["status"] in ("occupied", "away"):
        if user and desk["occupied_by_card"] != user["card_number"]:
            request.session.pop("deskguard_desk", None)
            return False
            
    return True


# ── Core pages ────────────────────────────────────────────────────────────────

def index(request):
    if not _is_logged_in(request):
        return render(request, "library/splash.html", {"hide_chrome": True})
    if _role(request) == "librarian":
        return redirect("librarian-dashboard")
    return redirect("student-dashboard")


def login_view(request):
    if _is_logged_in(request):
        return redirect("index")

    if request.method == "POST":
        role        = request.POST.get("role", "student")
        card_number = request.POST.get("card_number", "").strip().upper()
        password    = request.POST.get("password", "").strip()

        def login_fail(msg):
            return render(request, "library/login.html", {
                "error": msg,
                "prefill_card": card_number,
                **_auth_context(request),
            })

        if not card_number or not password:
            return login_fail("Please enter your card number and password.")

        # ── Librarian path ────────────────────────────────────────────────
        if role == "librarian":
            if card_number != LIBRARIAN_CARD:
                return login_fail(f"Librarian card number not recognised. Expected {LIBRARIAN_CARD}.")
            if password != LIBRARIAN_PASSWORD:
                return login_fail("Incorrect librarian password.")
            request.session["deskguard_user"] = {
                "name": "Librarian Admin",
                "role": "librarian",
                "card_number": LIBRARIAN_CARD,
            }
            return redirect("librarian-dashboard")

        # ── Student path ──────────────────────────────────────────────────
        from .models import Student
        try:
            student = Student.objects.get(card_number=card_number)
        except Student.DoesNotExist:
            return login_fail("No account found with that card number.")

        if not check_password(password, student.password_hash):
            return login_fail("Incorrect password. Please try again.")

        request.session["deskguard_user"] = {
            "name": student.full_name,
            "role": "student",
            "card_number": student.card_number,
            "student_id": student.id,
        }

        # Restore active desk to session and clear logout grace timer if applicable
        from .models import Desk
        try:
            active_desk = Desk.objects.get(occupied_by=student, status__in=["occupied", "away"])
            request.session["deskguard_desk"] = active_desk.number
            if active_desk.logged_out_at is not None:
                active_desk.logged_out_at = None
                active_desk.save()
        except Desk.DoesNotExist:
            pass

        return redirect("student-dashboard")

    return render(request, "library/login.html", _auth_context(request))


def register_view(request):
    if _is_logged_in(request):
        return redirect("index")

    if request.method == "POST":
        full_name        = request.POST.get("full_name", "").strip()
        email            = request.POST.get("email", "").strip().lower()
        phone_raw        = request.POST.get("phone", "").strip()
        college_name     = request.POST.get("college_name", "").strip()
        college_id_raw   = request.POST.get("college_id", "").strip()
        password         = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        def fail(msg):
            return render(request, "library/register.html", {
                "error": msg,
                **_auth_context(request),
                "pf_full_name": full_name,
                "pf_email": email,
                "pf_phone": phone_raw,
                "pf_college_name": college_name,
                "pf_college_id": college_id_raw,
            })

        # ── Required fields ───────────────────────────────────────────────
        if not all([full_name, email, phone_raw, college_name, college_id_raw, password, confirm_password]):
            return fail("All fields are required.")

        # ── Password strength ─────────────────────────────────────────────
        if password != confirm_password:
            return fail("Passwords do not match.")
        pw_error = _validate_password(password)
        if pw_error:
            return fail(pw_error)

        # ── Phone format ──────────────────────────────────────────────────
        phone = _validate_phone(phone_raw)
        if not phone:
            return fail(
                "Enter a valid 10-digit Indian mobile number (starting with 6–9). "
                "You can include +91 or 0 prefix."
            )

        # ── College ID format: yyCTExxx, year 22-26, roll 001-010 ─────────
        college_id = college_id_raw.upper()
        if not _COLLEGE_ID_RE.match(college_id):
            return fail(
                "College ID must be in the format yyCTExxx — e.g. 26CTE007. "
                "Year must be 22–26 and roll number must be 001–010."
            )

        from .models import Student, generate_card_number
        from django.db import IntegrityError

        # ── Pair uniqueness: phone AND email are each independently unique ─
        email_taken = Student.objects.filter(email=email).exists()
        phone_taken = Student.objects.filter(phone=phone).exists()
        if email_taken and phone_taken:
            return fail("Both this email and phone number are already registered.")
        if email_taken:
            return fail("An account already uses this email address.")
        if phone_taken:
            return fail("An account already uses this phone number.")
        if Student.objects.filter(college_id=college_id).exists():
            return fail("An account is already registered with this college ID.")

        # Generate a unique card number
        for _ in range(10):  # retry if collision (extremely rare)
            card_number = generate_card_number()
            if not Student.objects.filter(card_number=card_number).exists():
                break

        try:
            student = Student.objects.create(
                card_number=card_number,
                full_name=full_name,
                email=email,
                phone=phone,
                college_name=college_name,
                college_id=college_id,
                password_hash=make_password(password),
            )
        except IntegrityError:
            return fail("Registration failed due to a conflict. Please try again.")

        # Show success page with the card number
        return render(request, "library/register_success.html", {
            "card_number": student.card_number,
            "full_name": student.full_name,
            "masked_email": _mask_email(email),
            "hide_chrome": False,
            **_auth_context(request),
        })

    return render(request, "library/register.html", _auth_context(request))


def forgot_password_view(request):
    if _is_logged_in(request):
        return redirect("index")
    otp_sent = False
    generated_otp = None
    masked_email = None

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        otp_input = request.POST.get("otp", "").strip()
        action = request.POST.get("action", "")

        if action == "send-otp" and email:
            generated_otp = f"{random.randint(100000, 999999)}"
            request.session["deskguard_reset_otp"] = generated_otp
            request.session["deskguard_reset_email"] = email
            otp_sent = True
            masked_email = _mask_email(email)
        elif action == "verify-otp":
            stored_otp = request.session.get("deskguard_reset_otp")
            if otp_input and stored_otp and otp_input == stored_otp:
                return render(request, "library/forgot_password.html", {
                    "success": "OTP verified. You can now create a new password.",
                    "otp_verified": True,
                    **_auth_context(request),
                })
            return render(request, "library/forgot_password.html", {
                "error": "That OTP does not match.",
                "otp_sent": True,
                "masked_email": _mask_email(request.session.get("deskguard_reset_email", email)),
                **_auth_context(request),
            })

    if request.method == "GET" and request.session.get("deskguard_reset_email"):
        otp_sent = True
        masked_email = _mask_email(request.session["deskguard_reset_email"])

    return render(request, "library/forgot_password.html", {
        "otp_sent": otp_sent,
        "generated_otp": generated_otp,
        "masked_email": masked_email,
        **_auth_context(request),
    })


def logout_view(request):
    desk_number = _assigned_desk_number(request)
    if desk_number:
        from .models import Desk
        try:
            desk = Desk.objects.get(number=desk_number)
            if desk.status in ("occupied", "away"):
                desk.logged_out_at = timezone.now()
                desk.save()
        except Desk.DoesNotExist:
            pass

    request.session.flush()
    return redirect("login")


# ── Student views ─────────────────────────────────────────────────────────────

@role_required("student")
def student_dashboard(request):
    user = _current_user(request)
    from .models import Desk, Student
    try:
        student = Student.objects.get(card_number=user["card_number"])
        active_desk = Desk.objects.filter(occupied_by=student, status__in=["occupied", "away"]).first()
        if active_desk:
            request.session["deskguard_desk"] = active_desk.number
            if active_desk.logged_out_at is not None:
                active_desk.logged_out_at = None
                active_desk.save()
        else:
            request.session.pop("deskguard_desk", None)
    except Student.DoesNotExist:
        request.session.pop("deskguard_desk", None)

    desks = get_all_desks()
    for d in desks:
        d["purpose"] = ""
        d["approximate_time"] = ""

    return render(request, "library/library_map.html", {
        "desks": desks,
        "counts": desk_counts(),
        "page_title": "Student Portal",
        "page_subtitle": "Choose an open seat and complete check-in.",
        "page_badge": "Student View",
        "desk_released_notice": request.GET.get("released") == "1",
        "desk_expired_notice": request.GET.get("expired") == "1",
        **_auth_context(request),
    })


@role_required("student")
def student_session(request):
    if not _ensure_desk_still_assigned(request):
        return redirect("student-dashboard")
    show_verified_toast = request.session.pop("show_verified_toast", False)
    return render(request, "library/student_session.html", {
        **_auth_context(request),
        **_session_view_context(request),
        "show_verified_toast": show_verified_toast,
    })


@role_required("student")
def checkin(request, desk_number):
    user = _current_user(request)

    # Enforce single seat allocation: Check if student has any active desk in the database
    from .models import Desk, Student
    try:
        student = Student.objects.get(card_number=user["card_number"])
        active_desk = Desk.objects.filter(occupied_by=student, status__in=["occupied", "away"]).first()
        if active_desk:
            request.session["deskguard_desk"] = active_desk.number
            if active_desk.logged_out_at is not None:
                active_desk.logged_out_at = None
                active_desk.save()
            return redirect("student-session")
    except Student.DoesNotExist:
        return redirect("login")

    desk = get_desk(desk_number)
    if not desk:
        raise Http404("Desk not found")

    if desk["status"] != "available":
        return redirect("student-dashboard")

    if request.method == "POST":
        purpose          = request.POST.get("purpose", "").strip()
        approximate_time = request.POST.get("approximate_time", "").strip()

        from .models import Student
        try:
            student = Student.objects.get(card_number=user["card_number"])
        except Student.DoesNotExist:
            return redirect("login")

        now = timezone.now()
        set_desk_state(
            desk_number,
            status="occupied",
            occupied_by_student=student,
            purpose=purpose,
            approximate_time=approximate_time,
            session_started_at=now,
            away_started_at=None,
            presence_check_sent_at=None,
            presence_verified=False,
            notice=None,
            notice_kind=None,
        )
        request.session["deskguard_desk"] = desk_number
        return redirect("student-session")

    return render(request, "library/checkin_form.html", {
        **_auth_context(request),
        "desk": desk,
        "prefill_name": user["name"] if user else "",
    })


@role_required("student")
def take_break(request):
    if not _ensure_desk_still_assigned(request):
        return redirect("student-dashboard")

    desk_number = _assigned_desk_number(request)
    desk = get_desk(desk_number)
    if not desk or desk["status"] == "away":
        return redirect("student-session")

    if request.method == "POST":
        break_minutes_str = request.POST.get("break_minutes", "").strip()
        if break_minutes_str.isdigit():
            minutes = int(break_minutes_str)
            minutes = max(1, min(minutes, AWAY_DURATION_MINUTES))  # clamp to [1, 20]
            now = timezone.now()
            set_desk_state(
                desk_number,
                status="away",
                away_started_at=now,
                break_end_at=now + timedelta(minutes=minutes),   # exact expiry stored
                notice=None,
                notice_kind=None,
            )
            return redirect("student-session")

    return render(request, "library/take_break.html", {
        **_auth_context(request),
        **_session_view_context(request),
        "max_away_minutes": AWAY_DURATION_MINUTES,
    })


@role_required("student")
def extend_session(request):
    """Student clicks 'I'm Still Here' from the 10-min warning banner."""
    if not _ensure_desk_still_assigned(request):
        return redirect("student-dashboard")

    desk_number = _assigned_desk_number(request)
    desk = get_desk(desk_number)
    if not desk:
        return redirect("student-dashboard")

    remaining = desk["remaining_seconds"]
    if remaining <= 600:  # only allow when inside the 10-min window
        set_desk_state(
            desk_number,
            status="occupied",
            session_started_at=timezone.now(),  # reset the 2-h clock
            away_started_at=None,
            presence_check_sent_at=None,
            presence_verified=True,
            notice=None,
            notice_kind=None,
        )
        request.session["show_verified_toast"] = True
    return redirect("student-session")


@role_required("student")
def student_confirm_presence(request):
    """Student responds to a librarian's manual presence check."""
    if request.method != "POST":
        return redirect("student-session")

    if not _ensure_desk_still_assigned(request):
        return redirect("student-dashboard")

    desk_number = _assigned_desk_number(request)
    desk = get_desk(desk_number)
    if not desk:
        return redirect("student-dashboard")

    set_desk_state(
        desk_number,
        status="occupied",
        # Do NOT reset session_started_at — the 2h clock keeps running.
        # Confirming presence just clears the warning banner.
        presence_check_sent_at=None,
        presence_verified=True,
        notice="Presence confirmed. ✓",
        notice_kind="verified",
    )
    request.session["show_verified_toast"] = True
    return redirect("student-session")


@role_required("student")
def end_break(request):
    """Student ends their break early and returns to occupied status."""
    if request.method != "POST":
        return redirect("student-session")

    if not _ensure_desk_still_assigned(request):
        return redirect("student-dashboard")

    desk_number = _assigned_desk_number(request)
    desk = get_desk(desk_number)
    if not desk or desk["status"] != "away":
        return redirect("student-session")

    show_toast = False
    if desk.get("break_end_at"):
        from datetime import datetime
        break_end_dt = datetime.fromisoformat(desk["break_end_at"])
        if break_end_dt <= timezone.now():
            show_toast = True

    set_desk_state(
        desk_number,
        status="occupied",
        away_started_at=None,
        break_end_at=None,
        notice=None,
        notice_kind=None,
    )
    if show_toast:
        request.session["show_verified_toast"] = True
    return redirect("student-session")


@role_required("student")
def release_my_seat(request):
    if request.method != "POST":
        return redirect("student-session")

    desk_number = _assigned_desk_number(request)
    if not desk_number:
        return redirect("student-dashboard")

    set_desk_state(
        desk_number,
        status="available",
        occupied_by_student=None,
        purpose="",
        approximate_time="",
        session_started_at=None,
        away_started_at=None,
        break_end_at=None,
        presence_check_sent_at=None,
        presence_verified=False,
        notice=None,
        notice_kind=None,
    )
    request.session.pop("deskguard_desk", None)
    return redirect("student-dashboard")


# ── Librarian views ───────────────────────────────────────────────────────────

@role_required("librarian")
def librarian_dashboard(request):
    all_desks = get_all_desks()
    return render(request, "library/librarian_dashboard.html", {
        "desks": all_desks,
        "counts": desk_counts(),
        **_auth_context(request),
    })


@role_required("librarian")
def desk_detail(request, desk_number):
    desk = get_desk(desk_number)
    if not desk:
        raise Http404("Desk not found")
    return render(request, "library/desk_detail.html", {
        "desk": desk,
        "status_label": desk["status"].title(),
        **_auth_context(request),
    })


@role_required("librarian")
def verify_presence(request, desk_number):
    desk = get_desk(desk_number)
    if not desk:
        raise Http404("Desk not found")
    set_desk_state(
        desk_number,
        status=desk["status"],
        notice="Presence verification requested by librarian.",
        notice_kind="warning",
        presence_check_sent_at=timezone.now(),
        presence_verified=False,
    )
    return render(request, "library/workflow_step.html", {
        **_auth_context(request),
        "desk": get_desk(desk_number),
        "status_label": desk["status"].title(),
        "headline": "Presence Check Sent",
        "body_message": f"A presence check was sent for desk {desk_number}.",
        "next_action_url": reverse("librarian-dashboard"),
        "next_action_label": "Back to Dashboard",
        "illustration_state": desk["status"],
    })


@role_required("librarian")
def checkout(request, desk_number):
    if request.method != "POST":
        return redirect("desk-detail", desk_number=desk_number)

    desk = get_desk(desk_number)
    if not desk:
        raise Http404("Desk not found")

    was_in_use = desk["status"] != "available"
    reason = request.POST.get("release_reason", "").strip()
    notice = "Released by librarian."
    if reason:
        notice += f" Reason: {reason}"

    set_desk_state(
        desk_number,
        status="available",
        occupied_by_student=None,
        purpose="",
        approximate_time="",
        session_started_at=None,
        away_started_at=None,
        presence_check_sent_at=None,
        presence_verified=False,
        notice=notice if was_in_use else None,
        notice_kind="info" if was_in_use else None,
    )
    return render(request, "library/workflow_step.html", {
        **_auth_context(request),
        "desk": get_desk(desk_number),
        "status_label": "Available",
        "headline": "Desk Released",
        "body_message": f"Desk {desk_number} is now available.",
        "next_action_url": reverse("librarian-dashboard"),
        "next_action_label": "Back to Dashboard",
        "illustration_state": "available",
    })


@role_required("librarian")
def reset_abandoned_desk(request, desk_number):
    """Reset an abandoned desk back to available."""
    if request.method != "POST":
        return redirect("librarian-dashboard")

    desk = get_desk(desk_number)
    if not desk:
        raise Http404("Desk not found")

    set_desk_state(
        desk_number,
        status="available",
        occupied_by_student=None,
        purpose="",
        approximate_time="",
        session_started_at=None,
        away_started_at=None,
        presence_check_sent_at=None,
        presence_verified=False,
        notice=None,
        notice_kind=None,
    )
    return render(request, "library/workflow_step.html", {
        **_auth_context(request),
        "desk": get_desk(desk_number),
        "status_label": "Available",
        "headline": "Abandoned Desk Reset",
        "body_message": f"Desk {desk_number} has been cleared and is now available.",
        "next_action_url": reverse("librarian-dashboard"),
        "next_action_label": "Back to Dashboard",
        "illustration_state": "available",
    })


@role_required("librarian")
def cancel_release(request, desk_number):
    if request.method != "POST":
        return redirect("librarian-dashboard")

    from .models import Desk
    try:
        desk = Desk.objects.get(number=desk_number)
        desk.logged_out_at = None
        desk.save()
    except Desk.DoesNotExist:
        raise Http404("Desk not found")

    return render(request, "library/workflow_step.html", {
        **_auth_context(request),
        "desk": get_desk(desk_number),
        "status_label": desk.status.title(),
        "headline": "Release Cancelled",
        "body_message": f"Pending release for desk {desk_number} has been cancelled. The seat remains occupied.",
        "next_action_url": reverse("librarian-dashboard"),
        "next_action_label": "Back to Dashboard",
        "illustration_state": desk.status,
    })


@role_required("librarian")
def away_mode(request, desk_number):
    desk = get_desk(desk_number)
    if not desk:
        raise Http404("Desk not found")
    return render(request, "library/workflow_step.html", {
        **_auth_context(request),
        "desk": desk,
        "status_label": "Away",
        "headline": "Away Mode",
        "body_message": f"Desk {desk['number']} is away. Returning in {desk['away_remaining'] or '—'}.",
        "next_action_url": f"/desk/{desk_number}/",
        "next_action_label": "Back to Desk",
        "illustration_state": "away",
    })


@role_required("librarian")
def session_expiring(request, desk_number):
    desk = get_desk(desk_number)
    if not desk:
        raise Http404("Desk not found")
    return render(request, "library/session_expiring.html", {
        **_auth_context(request),
        "desk": desk,
        "status_label": desk["status"].title(),
        "warning_minutes": 10,
    })


# ── QR Code views ─────────────────────────────────────────────────────────────

@role_required("librarian")
def qr_codes_page(request):
    """Printable page showing QR codes for all desks."""
    desks = get_all_desks()
    host = request.build_absolute_uri("/")
    return render(request, "library/qr_codes.html", {
        **_auth_context(request),
        "desks": desks,
        "host": host.rstrip("/"),
    })


def qr_code_image(request, desk_number):
    """Return a PNG QR code image encoding the check-in URL for a desk."""
    try:
        import qrcode
        from qrcode.image.pure import PyPNGImage
    except ImportError:
        return HttpResponse("qrcode library not installed. Run: pip install qrcode[pil]", status=500)

    checkin_url = request.build_absolute_uri(f"/checkin/{desk_number}/")
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(checkin_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return HttpResponse(buf.getvalue(), content_type="image/png")


# ── API endpoints (JSON) ──────────────────────────────────────────────────────

@require_GET
def api_desk_status(request):
    """Return all desk states as JSON for live map updates."""
    desks = get_all_desks()
    is_lib = _role(request) == "librarian"
    if not is_lib:
        for d in desks:
            d["purpose"] = ""
            d["approximate_time"] = ""
    return JsonResponse({
        "desks": desks,
        "assigned_desk_number": _assigned_desk_number(request),
        "counts": desk_counts(),
    })


@login_required
@require_GET
def api_session_clock(request):
    """
    Return authoritative session timing for the logged-in student.
    Polled every second by the student session page.
    """
    desk_number = _assigned_desk_number(request)
    if not desk_number:
        return JsonResponse({"assigned": False})

    desk = get_desk(desk_number)
    if not desk:
        return JsonResponse({"assigned": False})

    # If the desk has been freed or occupied by another student, return assigned: False
    user = _current_user(request)
    if desk["status"] in ("available", "abandoned") or (desk["status"] in ("occupied", "away") and user and desk["occupied_by_card"] != user["card_number"]):
        return JsonResponse({"assigned": False})

    # Compute away_warning_seconds if they are on break and standard time ran out
    on_break = desk["status"] == "away"
    away_warning_seconds = 0
    if on_break and desk.get("break_end_at"):
        from datetime import datetime
        break_end_dt = datetime.fromisoformat(desk["break_end_at"])
        now = timezone.now()
        if break_end_dt <= now:
            grace_expiry = break_end_dt + timedelta(seconds=60)
            if grace_expiry > now:
                away_warning_seconds = int((grace_expiry - now).total_seconds())

    return JsonResponse({
        "assigned": True,
        "desk_number": desk_number,
        "status": desk["status"],
        "elapsed_seconds": desk["elapsed_seconds"],
        "remaining_seconds": desk["remaining_seconds"],
        "away_remaining_seconds": desk.get("away_remaining_seconds", 0),
        "away_warning_seconds": away_warning_seconds,
        "notice_kind": desk["notice_kind"],
        "notice": desk["notice"],
        "presence_verified": desk["presence_verified"],
    })
