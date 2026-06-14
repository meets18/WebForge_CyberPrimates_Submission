"""
data.py — ORM adapter.

All functions return plain dicts (same shape as before) so every existing
template and view continues to work without modification.
"""
from datetime import timedelta

from django.utils import timezone

SESSION_DURATION = timedelta(hours=2)
AWAY_DURATION = timedelta(minutes=20)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fmt_duration(delta):
    total_secs = max(int(delta.total_seconds()), 0)
    hours, rem = divmod(total_secs, 3600)
    mins = rem // 60
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def _desk_to_dict(desk):
    """Convert a Desk ORM object → dict that templates expect."""
    now = timezone.now()

    # ── time_remaining (for occupied / away / abandoned) ──────────────────
    if desk.session_started_at and desk.status in ("occupied", "away", "abandoned"):
        elapsed = now - desk.session_started_at
        remaining = SESSION_DURATION - elapsed
        time_remaining = _fmt_duration(remaining)
        elapsed_seconds = int(elapsed.total_seconds())
        remaining_seconds = max(int(remaining.total_seconds()), 0)
    else:
        time_remaining = "2h 00m"
        elapsed_seconds = 0
        remaining_seconds = 7200

    # ── away_remaining ────────────────────────────────────────────────────
    if desk.status == "away":
        if desk.break_end_at:
            # Authoritative: student-requested expiry stored at check-in
            away_remaining_delta = desk.break_end_at - now
        elif desk.away_started_at:
            # Legacy fallback: cap at 20 min
            away_remaining_delta = AWAY_DURATION - (now - desk.away_started_at)
        else:
            away_remaining_delta = AWAY_DURATION
        away_remaining = _fmt_duration(away_remaining_delta)
        away_remaining_seconds = max(int(away_remaining_delta.total_seconds()), 0)
    else:
        away_remaining = ""
        away_remaining_seconds = 0

    # ── logout_release_remaining ──────────────────────────────────────────
    logout_release_remaining = ""
    logout_release_remaining_seconds = 0
    if desk.logged_out_at and desk.status in ("occupied", "away"):
        release_at = desk.logged_out_at + timedelta(minutes=5)
        release_delta = release_at - now
        total_secs = max(int(release_delta.total_seconds()), 0)
        logout_release_remaining_seconds = total_secs
        mins, secs = divmod(total_secs, 60)
        logout_release_remaining = f"{mins}m {secs:02d}s"

    # ── checked_in display string ─────────────────────────────────────────
    if desk.session_started_at:
        checked_in = timezone.localtime(desk.session_started_at).strftime("%I:%M %p")
    else:
        checked_in = ""

    occupied_by_name = desk.occupied_by.full_name if desk.occupied_by else None
    occupied_by_card = desk.occupied_by.card_number if desk.occupied_by else None

    return {
        "id": desk.id,
        "number": desk.number,
        "status": desk.status,
        "checked_in": checked_in,
        "time_remaining": time_remaining,
        "away_remaining": away_remaining,
        # Raw seconds for the live JS clock
        "elapsed_seconds": elapsed_seconds,
        "remaining_seconds": remaining_seconds,
        "away_remaining_seconds": away_remaining_seconds,
        "logout_release_remaining": logout_release_remaining,
        "logout_release_remaining_seconds": logout_release_remaining_seconds,
        # ISO timestamps
        "session_started_at": desk.session_started_at.isoformat() if desk.session_started_at else None,
        "away_started_at": desk.away_started_at.isoformat() if desk.away_started_at else None,
        "break_end_at": desk.break_end_at.isoformat() if desk.break_end_at else None,
        "logged_out_at": desk.logged_out_at.isoformat() if desk.logged_out_at else None,
        # Who's sitting here
        "occupied_by": occupied_by_name,
        "occupied_by_card": occupied_by_card,
        "purpose": desk.purpose or "",
        "approximate_time": desk.approximate_time or "",
        # Presence / notice
        "notice": desk.notice,
        "notice_kind": desk.notice_kind,
        "presence_verified": desk.presence_verified,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def get_desk(desk_number):
    from .models import Desk
    try:
        desk = Desk.objects.select_related("occupied_by").get(number=desk_number)
        return _desk_to_dict(desk)
    except Desk.DoesNotExist:
        return None


def get_all_desks():
    from .models import Desk
    desks = Desk.objects.select_related("occupied_by").order_by("number")
    return [_desk_to_dict(d) for d in desks]


def set_desk_state(desk_number, **updates):
    """
    Update desk fields. Accepts the same kwargs as before PLUS:
      - occupied_by_student: Student ORM object (or None to clear)
    """
    from .models import Desk
    try:
        desk = Desk.objects.select_related("occupied_by").get(number=desk_number)
    except Desk.DoesNotExist:
        return None

    orm_fields = {
        "status", "purpose", "approximate_time",
        "session_started_at", "away_started_at", "break_end_at",
        "presence_check_sent_at", "presence_verified",
        "notice", "notice_kind", "logged_out_at",
    }

    for key, value in updates.items():
        if key in orm_fields:
            setattr(desk, key, value)
        elif key == "occupied_by_student":
            desk.occupied_by = value        # Student object or None
        elif key == "occupied_by" and value is None:
            desk.occupied_by = None         # Legacy clear path
        # Ignore legacy computed keys: checked_in, time_remaining, away_remaining,
        # break_until, session_started_at as string, etc. — computed on read.

    desk.save()
    return _desk_to_dict(desk)


def desk_counts():
    from django.db.models import Count
    from .models import Desk
    counts = {"available": 0, "occupied": 0, "away": 0, "abandoned": 0}
    for row in Desk.objects.values("status").annotate(c=Count("id")):
        if row["status"] in counts:
            counts[row["status"]] = row["c"]
    return counts


def ensure_desks_seeded():
    """Create desks D-101 → D-116 if the table is empty."""
    from .models import Desk
    if not Desk.objects.exists():
        desk_numbers = [f"D-{100 + i}" for i in range(1, 17)]
        Desk.objects.bulk_create([Desk(number=n) for n in desk_numbers])


def ensure_default_accounts():
    """
    Create built-in accounts that always exist in the DB:
      • Test student  WF-26-0001 / password: iron
    The librarian (WF-26-LB01) is checked in the view and has no DB row.
    """
    from django.contrib.auth.hashers import make_password
    from .models import Student

    defaults = [
        {
            "card_number": "WF-26-0001",
            "full_name": "Test Student",
            "email": "test@deskguard.demo",
            "phone": "9000000001",       # normalized 10-digit
            "college_name": "Demo College of Engineering",
            "college_id": "26CTE001",
            "password_hash": make_password("iron"),
        },
    ]
    for d in defaults:
        Student.objects.get_or_create(
            card_number=d["card_number"],
            defaults={k: v for k, v in d.items() if k != "card_number"},
        )

