"""Dashboard and report data for company managers, supervisors and workers."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .charts import column_chart
from .models import (
    Announcement,
    Attendance,
    AuditLog,
    Site,
    User,
    Incident,
    MaterialRequest,
    PayrollRecord,
    Project,
    Task,
    Worker,
    WorkerAssignment,
)
from .services import STANDARD_DAY_HOURS, workforce_on_site

ACTIVE_PROJECT_STATUSES = (Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD)

LABOUR_COST = ExpressionWrapper(
    F("hours_worked") * F("worker__daily_rate") / STANDARD_DAY_HOURS,
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def attendance_trend(attendance, days=14):
    """Workers present per day over the last `days` days."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    counts = dict(
        attendance.filter(date__gte=start, status=Attendance.Status.PRESENT)
        .values_list("date").annotate(count=Count("id"))
    )
    points = [((start + timedelta(days=offset)).strftime("%d %b"), counts.get(start + timedelta(days=offset), 0)) for offset in range(days)]
    return column_chart(points)


def labour_cost_by_month(attendance, months=6):
    """Labour cost in Leones per month, from attendance hours × daily rate."""
    today = timezone.localdate().replace(day=1)
    first_months = []
    year, month = today.year, today.month
    for _ in range(months):
        first_months.append(today.replace(year=year, month=month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    first_months.reverse()
    totals = {
        row["month"].date() if hasattr(row["month"], "date") else row["month"]: row["cost"]
        for row in attendance.filter(date__gte=first_months[0], status=Attendance.Status.PRESENT)
        .annotate(month=TruncMonth("date")).values("month").annotate(cost=Sum(LABOUR_COST))
    }
    return column_chart([(start.strftime("%b %Y"), totals.get(start, 0) or 0) for start in first_months], value_prefix="SLE ")


ACTIVITY_ICONS = {
    "attendance": "clock", "tasks": "clipboard", "projects": "crane", "safety": "cone", "materials": "toolbox",
    "payroll": "wallet", "reports": "clipboard", "workers": "hardhat", "accounts": "users", "communication": "megaphone",
    "requests": "inbox", "companies": "building",
}


def site_markers(projects, on_site):
    """Active project sites with coordinates, for the dashboard map."""
    counts = {}
    for record in on_site:
        if record.site_id:
            counts[record.site_id] = counts.get(record.site_id, 0) + 1
    markers = []
    for site in Site.objects.filter(project__in=projects, latitude__isnull=False, longitude__isnull=False).select_related("project"):
        markers.append({
            "lat": float(site.latitude), "lng": float(site.longitude), "radius": site.radius_m,
            "site": site.name, "project": site.project.title, "progress": site.project.progress_percentage,
            "on_site": counts.get(site.pk, 0), "url": f"/projects/{site.project_id}/",
        })
    return markers


def manager_dashboard(company):
    today = timezone.localdate()
    attendance = Attendance.objects.filter(worker__company=company)
    active_projects = company.projects.filter(status__in=ACTIVE_PROJECT_STATUSES)
    team_size = WorkerAssignment.objects.filter(project__in=active_projects, status=WorkerAssignment.Status.ACTIVE).values("worker").distinct().count()
    today_records = attendance.filter(date=today)
    on_site = workforce_on_site(company)
    open_tasks = Task.objects.filter(project__company=company).exclude(status=Task.Status.COMPLETED)
    labour_this_month = attendance.filter(date__gte=today.replace(day=1), status=Attendance.Status.PRESENT).aggregate(value=Sum(LABOUR_COST))["value"] or Decimal("0")
    present_today = today_records.filter(status=Attendance.Status.PRESENT).count()
    open_incidents = Incident.objects.filter(project__company=company).exclude(status=Incident.Status.CLOSED)
    company_users = User.objects.filter(Q(pk=company.owner_id) | Q(employer=company))
    return {
        "active_project_count": active_projects.count(),
        "on_hold_count": active_projects.filter(status=Project.Status.ON_HOLD).count(),
        "attendance_rate": round(100 * present_today / team_size) if team_size else None,
        "serious_incident_count": open_incidents.filter(severity__in=[Incident.Severity.HIGH, Incident.Severity.CRITICAL]).count(),
        "map_sites": site_markers(active_projects, on_site),
        "activity": [
            {"entry": entry, "icon": ACTIVITY_ICONS.get(entry.module, "file")}
            for entry in AuditLog.objects.filter(user__in=company_users).select_related("user")[:8]
        ],
        "on_site_now": on_site,
        "on_site_count": on_site.count(),
        "team_size": team_size,
        "present_today": present_today,
        "absent_today": today_records.exclude(status=Attendance.Status.PRESENT).count(),
        "open_task_count": open_tasks.count(),
        "overdue_task_count": open_tasks.filter(due_date__lt=today).count(),
        "open_incident_count": open_incidents.count(),
        "pending_material_count": MaterialRequest.objects.filter(project__company=company, status=MaterialRequest.Status.PENDING).count(),
        "labour_this_month": labour_this_month,
        "project_progress": active_projects.order_by("-updated_at")[:6],
        "attendance_chart": attendance_trend(attendance),
        "labour_chart": labour_cost_by_month(attendance),
        "recent_incidents": Incident.objects.filter(project__company=company).select_related("project")[:4],
    }


def supervisor_dashboard(user):
    today = timezone.localdate()
    projects = user.supervised_projects.filter(status__in=ACTIVE_PROJECT_STATUSES).select_related("company")
    rows = []
    for project in projects:
        team = WorkerAssignment.objects.filter(project=project, status=WorkerAssignment.Status.ACTIVE).count()
        present = Attendance.objects.filter(project=project, date=today, status=Attendance.Status.PRESENT).count()
        rows.append({"project": project, "team": team, "present": present,
                     "open_tasks": project.tasks.exclude(status=Task.Status.COMPLETED).count()})
    attendance = Attendance.objects.filter(project__in=projects)
    return {
        "project_rows": rows,
        "attendance_chart": attendance_trend(attendance),
        "my_reports_today": user.site_reports.filter(date=today).count(),
        "pending_requests": MaterialRequest.objects.filter(requested_by=user, status=MaterialRequest.Status.PENDING).count(),
        "open_incidents": Incident.objects.filter(project__in=projects).exclude(status=Incident.Status.CLOSED).count(),
    }


def worker_dashboard(worker):
    today = timezone.localdate()
    projects = Project.objects.filter(assignments__worker=worker, assignments__status=WorkerAssignment.Status.ACTIVE, status__in=ACTIVE_PROJECT_STATUSES).distinct()
    tasks = Task.objects.filter(assignments__worker=worker).select_related("project", "site").distinct()
    month_start = today.replace(day=1)
    month_hours = worker.attendance.filter(date__gte=month_start, status=Attendance.Status.PRESENT).aggregate(value=Sum("hours_worked"))["value"] or Decimal("0")
    return {
        "worker": worker,
        "today_record": worker.attendance.filter(date=today).first(),
        "active_projects": projects,
        "open_tasks": tasks.exclude(status=Task.Status.COMPLETED).order_by("due_date")[:8],
        "today_tasks": tasks.exclude(status=Task.Status.COMPLETED).filter(
            Q(status=Task.Status.IN_PROGRESS) | Q(due_date__lte=today) | Q(due_date__isnull=True)).order_by("due_date")[:6],
        "schedule": tasks.exclude(status=Task.Status.COMPLETED).filter(due_date__gt=today).exclude(status=Task.Status.IN_PROGRESS).order_by("due_date")[:10],
        "completed_task_count": tasks.filter(status=Task.Status.COMPLETED).count(),
        "month_hours": month_hours,
        "recent_attendance": worker.attendance.select_related("project")[:7],
        "recent_pay": worker.payroll_records.exclude(status=PayrollRecord.Status.DRAFT)[:3],
        "announcements": Announcement.objects.filter(company=worker.company).filter(Q(project__isnull=True) | Q(project__in=projects)).select_related("project")[:3],
    }


def available_worker_count(company):
    return company.workers.filter(employment_status=Worker.Employment.ACTIVE, availability=Worker.Availability.AVAILABLE).count()


def attendance_between(attendance, start, end):
    """Workers present per day (or per week for long ranges) between two dates."""
    days = (end - start).days + 1
    counts = dict(attendance.filter(date__range=(start, end), status=Attendance.Status.PRESENT).values_list("date").annotate(count=Count("id")))
    if days <= 62:
        points = [((start + timedelta(days=offset)).strftime("%d %b"), counts.get(start + timedelta(days=offset), 0)) for offset in range(days)]
    else:
        points = []
        week_start = start
        while week_start <= end:
            week_end = min(week_start + timedelta(days=6), end)
            total = sum(count for day, count in counts.items() if week_start <= day <= week_end)
            points.append((f"w/c {week_start:%d %b}", total))
            week_start = week_end + timedelta(days=1)
    return column_chart(points)


def labour_cost_between(attendance, start, end):
    """Labour cost in Leones per month between two dates."""
    totals = {
        (row["month"].year, row["month"].month): row["cost"]
        for row in attendance.filter(date__range=(start, end), status=Attendance.Status.PRESENT)
        .annotate(month=TruncMonth("date")).values("month").annotate(cost=Sum(LABOUR_COST))
    }
    points, cursor = [], start.replace(day=1)
    while cursor <= end:
        points.append((cursor.strftime("%b %Y"), totals.get((cursor.year, cursor.month), 0) or 0))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return column_chart(points, value_prefix="SLE ")
