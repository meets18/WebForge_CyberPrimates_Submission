import random

from django.db import models
from django.utils import timezone


def generate_card_number():
    """Generate a unique WF-YY-XXXX library card number."""
    year = timezone.now().year % 100  # last 2 digits, e.g. 26 for 2026
    digits = random.randint(1000, 9999)
    return f"WF-{year:02d}-{digits}"


class Student(models.Model):
    card_number = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, unique=True)
    college_name = models.CharField(max_length=200)
    college_id = models.CharField(max_length=100, unique=True)
    password_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} ({self.card_number})"


class Desk(models.Model):
    STATUS_CHOICES = [
        ("available", "Available"),
        ("occupied", "Occupied"),
        ("away", "Away"),
        ("abandoned", "Abandoned"),
    ]

    number = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="available")

    # Who is sitting here
    occupied_by = models.ForeignKey(
        Student, null=True, blank=True, on_delete=models.SET_NULL, related_name="active_desk"
    )
    purpose = models.CharField(max_length=200, blank=True)
    approximate_time = models.CharField(max_length=100, blank=True)

    # Server-side authoritative timestamps (never stored in browser)
    session_started_at = models.DateTimeField(null=True, blank=True)
    away_started_at = models.DateTimeField(null=True, blank=True)
    break_end_at = models.DateTimeField(null=True, blank=True)   # actual break expiry (set by take_break)
    presence_check_sent_at = models.DateTimeField(null=True, blank=True)
    presence_verified = models.BooleanField(default=False)
    logged_out_at = models.DateTimeField(null=True, blank=True)

    # Notification state
    notice = models.TextField(blank=True, null=True)
    notice_kind = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return f"{self.number} ({self.status})"
