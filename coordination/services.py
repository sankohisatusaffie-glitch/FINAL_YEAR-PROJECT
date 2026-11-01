"""Business rules for site operations: geofencing, attendance, task matching, progress and payroll.

Views call these functions instead of changing models directly, so the rules (and the
audit trail) stay the same whether a change comes from a form, a QR scan or an offline sync.
"""

import math
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from .models import (
    Attendance,
    Project,
    PayrollRecord,
    Task,
    TaskAssignment,
    User,
    Worker,
    WorkerAssignment,
)

EARTH_RADIUS_M = 6_371_000
MAX_LOCATION_ACCURACY_M = 100   # Reject GPS fixes less precise than this.
STANDARD_DAY_HOURS = Decimal("8")
MAX_SHIFT_HOURS = Decimal("16")  # Caps a forgotten clock-out.
OFFLINE_MAX_AGE = timedelta(hours=72)
CLOCK_SKEW_ALLOWANCE = timedelta(minutes=5)
OPEN_PROJECT_STATUSES = (Project.Status.PLANNED, Project.Status.IN_PROGRESS)


class AttendanceError(Exception):
    """A clock-in or clock-out that breaks an attendance rule. The message is shown to the user."""


# --- Geofencing -------------------------------------------------------------

def distance_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres between two points (haversine formula)."""
    lat1, lng1, lat2, lng2 = (math.radians(float(value)) for value in (lat1, lng1, lat2, lng2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass
class GeofenceResult:
    allowed: bool
    distance: int | None = None
    message: str = ""


def check_geofence(site, latitude, longitude, accuracy=None):
    """Decide whether a location is inside a site's attendance boundary."""
    has_location = latitude is not None and longitude is not None
    distance = round(distance_m(latitude, longitude, site.latitude, site.longitude)) if site and site.has_boundary and has_location else None
    if site is None or not site.has_boundary or not site.enforce_geofence:
        return GeofenceResult(True, distance)
    if not has_location:
        return GeofenceResult(False, None, "Your location is needed to clock in at this site. Allow location access and try again.")
    if accuracy is not None and float(accuracy) > MAX_LOCATION_ACCURACY_M:
        return GeofenceResult(False, distance, f"Your location is only accurate to about {round(float(accuracy))} m. Move into the open and try again.")
    if distance > site.radius_m:
        return GeofenceResult(False, distance, f"You are {distance} m from {site.name}. You must be within {site.radius_m} m of the site to clock in.")
    return GeofenceResult(True, distance)


# --- Attendance -------------------------------------------------------------

def shift_hours(clock_in, clock_out):
    """Hours between clock-in and clock-out, rounded to 2 decimals and capped at MAX_SHIFT_HOURS."""
    hours = Decimal((clock_out - clock_in).total_seconds()) / Decimal(3600)
    return min(hours, MAX_SHIFT_HOURS).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def on_project_team(worker, project):
    return WorkerAssignment.objects.filter(project=project, worker=worker, status=WorkerAssignment.Status.ACTIVE).exists()


def default_site(project):
    sites = list(project.sites.all()[:2])
    return sites[0] if len(sites) == 1 else None


@dataclass
class ClockResult:
    attendance: Attendance
    action: str          # "clock_in", "clock_out" or "duplicate"
    distance: int | None = None


def record_clock(worker, project, *, method, recorded_by, site=None, latitude=None, longitude=None,
                 accuracy=None, at=None, client_uuid=None, offline=False, note=""):
    """Clock a worker in, or out if they are already clocked in today.

    Raises AttendanceError with a user-facing message when a rule is broken.
    Repeated offline submissions with the same client_uuid are ignored safely.
    """
    if client_uuid:
        existing = Attendance.objects.filter(Q(client_uuid=client_uuid) | Q(clock_out_uuid=client_uuid)).first()
        if existing:
            return ClockResult(existing, "duplicate")

    now = timezone.now()
    at = at or now
    if at > now + CLOCK_SKEW_ALLOWANCE:
        raise AttendanceError("The recorded time is in the future. Check the device clock and try again.")
    if now - at > OFFLINE_MAX_AGE:
        raise AttendanceError("This record is more than 72 hours old. Ask a supervisor to enter it manually.")
    if worker.company_id != project.company_id or worker.employment_status != Worker.Employment.ACTIVE:
        raise AttendanceError(f"{worker.full_name} is not an active worker for this project's company.")
    if project.status not in OPEN_PROJECT_STATUSES:
        raise AttendanceError("Attendance can only be recorded on planned or in-progress projects.")
    if not on_project_team(worker, project):
        raise AttendanceError(f"{worker.full_name} is not on the team for {project.title}.")

    site = site or default_site(project)
    if site is not None and site.project_id != project.pk:
        raise AttendanceError("The selected site does not belong to this project.")
    if method == Attendance.Method.MANUAL:
        if not note.strip():
            raise AttendanceError("Give a reason for the manual entry.")
        geofence = GeofenceResult(True)
    else:
        geofence = check_geofence(site, latitude, longitude, accuracy)
        if not geofence.allowed:
            raise AttendanceError(geofence.message)

    day = timezone.localdate(at)
    try:
        with transaction.atomic():
            record = Attendance.objects.select_for_update().filter(worker=worker, date=day).first()
            if record is None or (record.clock_in is None and record.status != Attendance.Status.PRESENT):
                record = record or Attendance(worker=worker, project=project, date=day)
                record.project, record.site, record.status = project, site, Attendance.Status.PRESENT
                record.clock_in, record.method, record.recorded_by = at, method, recorded_by
                record.note, record.synced_offline, record.client_uuid = note.strip(), offline, client_uuid
                if latitude is not None and longitude is not None:
                    record.clock_in_latitude = round(Decimal(str(latitude)), 6)
                    record.clock_in_longitude = round(Decimal(str(longitude)), 6)
                record.clock_in_distance_m = geofence.distance
                record.save()
                return ClockResult(record, "clock_in", geofence.distance)
            if record.project_id != project.pk:
                raise AttendanceError(f"{worker.full_name} is already clocked in on {record.project.title} today.")
            if not record.is_open:
                raise AttendanceError(f"{worker.full_name} has already clocked out today.")
            if at <= record.clock_in:
                raise AttendanceError("Clock-out time must be after the clock-in time.")
            record.clock_out, record.clock_out_uuid = at, client_uuid
            record.hours_worked = shift_hours(record.clock_in, at)
            record.synced_offline = record.synced_offline or offline
            record.save(update_fields=["clock_out", "clock_out_uuid", "hours_worked", "synced_offline"])
            return ClockResult(record, "clock_out", geofence.distance)
    except IntegrityError:
        # Another device recorded the same worker at the same moment; report the stored state.
        raise AttendanceError("This attendance was just recorded from another device. Refresh to see it.")


def mark_absence(worker, project, day, status, note, recorded_by):
    if status == Attendance.Status.PRESENT:
        raise AttendanceError("Use clock-in to mark a worker present.")
    record, created = Attendance.objects.get_or_create(
        worker=worker, date=day,
        defaults={"project": project, "status": status, "note": note, "method": Attendance.Method.MANUAL, "recorded_by": recorded_by},
    )
    if not created:
        if record.clock_in:
            raise AttendanceError(f"{worker.full_name} already clocked in on {day:%d %b %Y}.")
        record.status, record.note, record.recorded_by, record.project = status, note, recorded_by, project
        record.save(update_fields=["status", "note", "recorded_by", "project"])
    return record


# --- Task matching and assignment ---------------------------------------------

@dataclass
class WorkerMatch:
    worker: Worker
    score: int
    reasons: list = field(default_factory=list)


def rank_workers_for_task(task, limit=None):
    """Suggest workers for a task, best first, using skills, availability, experience and workload.

    Candidates are active workers who are free, or already on this project's team.
    The manager or supervisor makes the final choice.
    """
    project = task.project
    team_ids = set(WorkerAssignment.objects.filter(project=project, status=WorkerAssignment.Status.ACTIVE).values_list("worker_id", flat=True))
    candidates = (
        Worker.objects.filter(company=project.company, employment_status=Worker.Employment.ACTIVE)
        .filter(Q(availability=Worker.Availability.AVAILABLE) | Q(pk__in=team_ids))
        .exclude(task_assignments__task=task)
        .prefetch_related("skill_set")
        .annotate(open_tasks=Count("task_assignments", filter=~Q(task_assignments__task__status=Task.Status.COMPLETED), distinct=True))
    )
    since = timezone.localdate() - timedelta(days=30)
    present_days = dict(
        Attendance.objects.filter(worker__in=candidates, date__gte=since, status=Attendance.Status.PRESENT)
        .values_list("worker").annotate(days=Count("id"))
    )
    skill = task.required_skill
    matches = []
    for worker in candidates:
        score, reasons = 0, []
        if skill:
            has_skill = skill in worker.skill_set.all() or worker.primary_trade.casefold() == skill.name.casefold()
            if has_skill:
                score += 50
                reasons.append(f"Has skill: {skill.name}")
        if worker.pk in team_ids:
            score += 20
            reasons.append("Already on this project")
        experience = min(worker.experience_years, 20)
        score += experience
        if worker.experience_years:
            reasons.append(f"{worker.experience_years} yrs experience")
        days = present_days.get(worker.pk, 0)
        score += min(days // 2, 10)
        if days:
            reasons.append(f"Present {days} of last 30 days")
        score -= 5 * worker.open_tasks
        if worker.open_tasks:
            reasons.append(f"{worker.open_tasks} open task{'s' if worker.open_tasks != 1 else ''}")
        matches.append(WorkerMatch(worker, score, reasons))
    matches.sort(key=lambda match: (-match.score, match.worker.last_name, match.worker.first_name))
    return matches[:limit] if limit else matches


class AssignmentError(Exception):
    pass


def assign_worker_to_task(task, worker, assigned_by):
    """Assign a worker to a task, adding them to the project team first if they are free."""
    project = task.project
    if worker.company_id != project.company_id or worker.employment_status != Worker.Employment.ACTIVE:
        raise AssignmentError("Only active workers from this company can be assigned.")
    if task.status == Task.Status.COMPLETED:
        raise AssignmentError("This task is already completed.")
    with transaction.atomic():
        locked = Worker.objects.select_for_update().get(pk=worker.pk)
        joined_team = False
        if not on_project_team(locked, project):
            if locked.availability != Worker.Availability.AVAILABLE:
                raise AssignmentError(f"{locked.full_name} is not available.")
            WorkerAssignment.objects.update_or_create(
                project=project, worker=locked,
                defaults={"status": WorkerAssignment.Status.ACTIVE, "assigned_by": assigned_by, "removed_at": None,
                          "role": task.required_skill.name if task.required_skill else ""},
            )
            locked.availability = Worker.Availability.ASSIGNED
            locked.save(update_fields=["availability"])
            joined_team = True
        assignment, created = TaskAssignment.objects.get_or_create(task=task, worker=locked, defaults={"assigned_by": assigned_by})
        if not created:
            raise AssignmentError(f"{locked.full_name} is already assigned to this task.")
    return assignment, joined_team


# --- Progress -----------------------------------------------------------------

def set_task_progress(task, progress):
    """Update a task's progress and status together, then roll the change up to the project."""
    progress = max(0, min(100, int(progress)))
    task.progress_percentage = progress
    if progress == 100:
        task.status, task.completed_at = Task.Status.COMPLETED, task.completed_at or timezone.now()
    else:
        task.status = Task.Status.IN_PROGRESS if progress > 0 else Task.Status.PENDING
        task.completed_at = None
    task.save(update_fields=["progress_percentage", "status", "completed_at"])
    recompute_project_progress(task.project)
    return task


def recompute_project_progress(project):
    """Project progress is the average of its tasks' progress, when it has tasks."""
    average = project.tasks.aggregate(value=Avg("progress_percentage"))["value"]
    if average is None:
        return project
    project.progress_percentage = round(average)
    fields = ["progress_percentage", "updated_at"]
    if project.status == Project.Status.PLANNED and average > 0:
        project.status = Project.Status.IN_PROGRESS
        fields.append("status")
    project.save(update_fields=fields)
    return project


# --- Payroll ------------------------------------------------------------------

def calculate_gross_pay(hours, daily_rate):
    """Pay = hours worked ÷ standard 8-hour day × daily rate, rounded to the nearest cent."""
    return (Decimal(hours) / STANDARD_DAY_HOURS * Decimal(daily_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def generate_payroll(company, period_start, period_end, generated_by):
    """Create or refresh draft payroll records for every worker with hours in the period.

    Approved and paid records are never changed. Open shifts (no clock-out) earn nothing
    until they are closed.
    """
    if period_end < period_start:
        raise ValueError("The period end must be on or after the start.")
    totals = (
        Attendance.objects.filter(worker__company=company, date__range=(period_start, period_end), status=Attendance.Status.PRESENT)
        .values("worker").annotate(hours=Sum("hours_worked"), days=Count("id", filter=Q(hours_worked__gt=0)))
    )
    records, skipped = [], 0
    with transaction.atomic():
        for row in totals:
            worker = Worker.objects.get(pk=row["worker"])
            hours = row["hours"] or Decimal("0")
            record = PayrollRecord.objects.filter(worker=worker, period_start=period_start, period_end=period_end).first()
            if record and record.status != PayrollRecord.Status.DRAFT:
                skipped += 1
                continue
            record = record or PayrollRecord(company=company, worker=worker, period_start=period_start, period_end=period_end)
            record.days_present, record.hours_worked, record.daily_rate = row["days"], hours, worker.daily_rate
            record.gross_pay = calculate_gross_pay(hours, worker.daily_rate)
            record.net_pay = max(record.gross_pay - record.deductions, Decimal("0"))
            record.generated_by = generated_by
            record.save()
            records.append(record)
    return records, skipped


def workforce_on_site(company):
    """Workers clocked in right now (today, with no clock-out)."""
    return Attendance.objects.filter(
        worker__company=company, date=timezone.localdate(), clock_in__isnull=False, clock_out__isnull=True,
    ).select_related("worker", "project", "site")


def user_for_worker(worker):
    user = worker.user
    return user if user and user.is_active and user.role == User.Role.WORKER else None
