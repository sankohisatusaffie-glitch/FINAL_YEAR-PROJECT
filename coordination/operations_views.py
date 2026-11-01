"""Views for company site operations: staff, projects, sites, tasks, attendance, reporting,
safety, materials, payroll, announcements and offline support.

Access is enforced in every view through `coordination.access`; business rules live in
`coordination.services` so forms, QR scans and offline syncs all follow the same rules.
"""

import csv
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from .access import (
    MANAGER_ROLES,
    OPERATIONS_ROLES,
    SITE_ROLES,
    can_manage_project,
    can_supervise_project,
    get_project,
    is_manager,
    operating_company,
    require_operating_company,
    visible_projects,
    worker_profile,
)
from .forms import (
    AbsenceForm,
    AnnouncementForm,
    IncidentForm,
    IncidentUpdateForm,
    ManualAttendanceForm,
    MaterialRequestForm,
    MaterialReviewForm,
    PayrollAdjustForm,
    PayrollPeriodForm,
    PhaseForm,
    ProjectForm,
    SiteForm,
    SiteReportForm,
    StaffAccountForm,
    TaskAssignForm,
    TaskForm,
    TaskProgressForm,
)
from .messaging import alert, broadcast
from .models import (
    Announcement,
    Attendance,
    Incident,
    MaterialRequest,
    PayrollRecord,
    Phase,
    Project,
    ReportPhoto,
    Site,
    Task,
    TaskAssignment,
    User,
    Worker,
    WorkerAssignment,
    new_qr_token,
)
from .payments import PaymentError, pay_wages
from .qr import parse_qr_payload, qr_svg
from .services import (
    AssignmentError,
    AttendanceError,
    assign_worker_to_task,
    generate_payroll,
    mark_absence,
    rank_workers_for_task,
    record_clock,
    set_task_progress,
)
from .utils import log_event
from .views import paginated


def operations_view(*roles):
    """Require an operations role in an approved company; sets request.company."""
    allowed = roles or OPERATIONS_ROLES

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(request, *args, **kwargs):
            request.company = require_operating_company(request.user, allowed)
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


def wants_json(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def form_page(request, form, *, eyebrow, title, submit_label, intro="", multipart=False, cancel_url=None):
    return render(request, "coordination/form_page.html", {
        "form": form, "eyebrow": eyebrow, "title": title, "intro": intro,
        "submit_label": submit_label, "multipart": multipart, "cancel_url": cancel_url,
    })


def team_workers(project):
    return project.company.workers.filter(
        assignments__project=project, assignments__status=WorkerAssignment.Status.ACTIVE,
    ).distinct().order_by("last_name", "first_name")


def project_nav_context(request, project, active):
    manage, supervise = can_manage_project(request.user, project), can_supervise_project(request.user, project)
    return {
        "project": project, "active_tab": active, "can_manage": manage, "can_supervise": supervise,
        "nav_manage": manage, "nav_supervise": supervise,
    }


# --- Staff accounts -------------------------------------------------------------

@operations_view(*MANAGER_ROLES)
def staff_list(request):
    company = request.company
    staff = User.objects.filter(employer=company).select_related("worker_profile").order_by("role", "last_name", "first_name")
    return render(request, "coordination/ops/staff_list.html", {"staff": staff, "company": company})


@operations_view(*MANAGER_ROLES)
def staff_create(request):
    company = request.company
    form = StaffAccountForm(request.POST or None, company=company, creator=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"], email=data["email"], password=data["password"],
                first_name=data["first_name"].strip(), last_name=data["last_name"].strip(),
                role=data["role"], employer=company, phone=data.get("phone") or "",
                # The manager creating the account vouches for it, so it does not need email confirmation.
                email_verified=True, email_verified_at=timezone.now(),
            )
            if data["worker"]:
                worker = Worker.objects.select_for_update().get(pk=data["worker"].pk, company=company)
                worker.user = user
                worker.save(update_fields=["user"])
        log_event(request, "staff_created", "accounts", f"{request.user} created {user.get_role_display().lower()} account {user.username}.", user.pk)
        alert(user, "Welcome to BuildLink", f"{company.name} created a {user.get_role_display().lower()} account for you.", "/dashboard/")
        messages.success(request, f"Account created for {user.get_full_name()}. Share the username and password with them securely.")
        return redirect("staff_list")
    return form_page(request, form, eyebrow="Team accounts", title="Add a team account", submit_label="Create account",
                     intro="Give project managers, supervisors and workers their own sign-in.", cancel_url=reverse("staff_list"))


@operations_view(*MANAGER_ROLES)
@require_POST
def staff_toggle(request, user_id):
    member = get_object_or_404(User, pk=user_id, employer=request.company)
    if member.role == User.Role.MANAGER and request.user.role != User.Role.COMPANY:
        raise PermissionDenied("Only the company owner can change project manager accounts.")
    member.is_active = not member.is_active
    member.save(update_fields=["is_active"])
    state = "reactivated" if member.is_active else "deactivated"
    log_event(request, f"staff_{state}", "accounts", f"{request.user} {state} {member.username}.", member.pk)
    messages.success(request, f"{member.get_full_name() or member.username} was {state}.")
    return redirect("staff_list")


# --- Projects, sites and phases -------------------------------------------------

@login_required
def project_jump(request):
    """Target of the header's project selector (works without JavaScript too)."""
    try:
        project = get_project(request.user, int(request.GET.get("project", "")), "view")
    except (ValueError, Http404, PermissionDenied):
        return redirect("project_list")
    return redirect("project_detail", project_id=project.pk)


@operations_view(*MANAGER_ROLES)
def project_create(request):
    form = ProjectForm(request.POST or None, company=request.company, initial={"manager": request.user})
    if request.method == "POST" and form.is_valid():
        project = form.save(commit=False)
        project.company = request.company
        project.save()
        form.save_m2m()
        log_event(request, "project_created", "projects", f"{request.user} created project {project.title}.", project.pk)
        for person in {project.manager, *project.supervisors.all(), project.client} - {None, request.user}:
            alert(person, "Added to a project", f"You were added to {project.title}.", f"/projects/{project.pk}/")
        messages.success(request, "Project created. Next, add a site and the project phases.")
        return redirect("project_plan", project_id=project.pk)
    return form_page(request, form, eyebrow="Projects", title="Create a project", submit_label="Create project",
                     intro="Set up a project directly. Accepted client requests still create projects automatically.", cancel_url=reverse("project_list"))


@login_required
def project_edit(request, project_id):
    project = get_project(request.user, project_id, "manage")
    before = set(project.supervisors.values_list("pk", flat=True))
    form = ProjectForm(request.POST or None, instance=project, company=project.company)
    if request.method == "POST" and form.is_valid():
        project = form.save()
        log_event(request, "project_edited", "projects", f"{request.user} edited project {project.title}.", project.pk)
        for supervisor in project.supervisors.exclude(pk__in=before):
            alert(supervisor, "Assigned as supervisor", f"You now supervise {project.title}.", f"/projects/{project.pk}/")
        messages.success(request, "Project details saved.")
        return redirect("project_plan", project_id=project.pk)
    return form_page(request, form, eyebrow=project.title, title="Edit project details", submit_label="Save project", cancel_url=reverse("project_plan", args=[project.pk]))


def phase_timeline(project, phases):
    """Bar positions (as % of the project window) for a Gantt-style phase timeline."""
    dated = [phase for phase in phases if phase.start_date or phase.end_date]
    starts = [day for day in [project.start_date, *(phase.start_date for phase in dated)] if day]
    ends = [day for day in [project.expected_end_date, *(phase.end_date for phase in dated)] if day]
    if not starts or not ends or max(ends) <= min(starts):
        return None
    begin, finish = min(starts), max(ends)
    span = (finish - begin).days
    rows = []
    for phase in phases:
        start = phase.start_date or phase.end_date
        end = phase.end_date or phase.start_date
        if not start:
            rows.append({"phase": phase, "dated": False})
            continue
        rows.append({
            "phase": phase, "dated": True,
            "left": round(100 * (start - begin).days / span, 2),
            "width": max(round(100 * (end - start).days / span, 2), 1.2),
            "is_late": phase.status != Phase.Status.COMPLETED and end < timezone.localdate(),
        })
    today = timezone.localdate()
    months, cursor = [], begin.replace(day=1)
    while cursor <= finish:
        if cursor >= begin:
            months.append({"label": cursor.strftime("%b %Y"), "left": round(100 * (cursor - begin).days / span, 2)})
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        "rows": rows, "months": months, "begin": begin, "finish": finish,
        "today": round(100 * (today - begin).days / span, 2) if begin <= today <= finish else None,
    }


@login_required
def project_plan(request, project_id):
    project = get_project(request.user, project_id, "view")
    phases = list(project.phases.annotate(task_count=Count("tasks")))
    context = project_nav_context(request, project, "plan")
    context.update({"sites": project.sites.all(), "phases": phases, "timeline": phase_timeline(project, phases)})
    return render(request, "coordination/ops/project_plan.html", context)


@login_required
def project_workers(request, project_id):
    """The project crew: who is on the team, their trade, today's attendance and open tasks."""
    project = get_project(request.user, project_id, "view")
    if request.user.role == User.Role.CLIENT:
        raise PermissionDenied("The crew list is for the company's own staff.")
    today = timezone.localdate()
    assignments = project.assignments.select_related("worker").order_by("status", "worker__last_name")
    attendance = {record.worker_id: record for record in Attendance.objects.filter(project=project, date=today)}
    open_tasks = dict(
        TaskAssignment.objects.filter(task__project=project).exclude(task__status=Task.Status.COMPLETED)
        .values_list("worker").annotate(count=Count("id"))
    )
    rows = [{"assignment": item, "worker": item.worker, "today": attendance.get(item.worker_id), "open_tasks": open_tasks.get(item.worker_id, 0)}
            for item in assignments]
    context = project_nav_context(request, project, "workers")
    context.update({"rows": rows, "active_count": sum(1 for row in rows if row["assignment"].status == WorkerAssignment.Status.ACTIVE)})
    return render(request, "coordination/ops/project_workers.html", context)


@login_required
def project_materials(request, project_id):
    project = get_project(request.user, project_id, "supervise")
    items = project.material_requests.select_related("site", "requested_by", "reviewed_by")
    context = project_nav_context(request, project, "materials")
    context.update({"items": items, "can_review": can_manage_project(request.user, project)})
    return render(request, "coordination/ops/project_materials.html", context)


@login_required
def project_incidents(request, project_id):
    project = get_project(request.user, project_id, "view")
    if request.user.role not in OPERATIONS_ROLES:
        raise PermissionDenied("Incident records are for the company's own staff.")
    incidents = project.incidents.select_related("site", "reported_by")
    if not can_supervise_project(request.user, project):
        incidents = incidents.filter(reported_by=request.user)
    context = project_nav_context(request, project, "incidents")
    context.update({"incidents": incidents})
    return render(request, "coordination/ops/project_incidents.html", context)


@login_required
def site_form(request, project_id, site_id=None):
    project = get_project(request.user, project_id, "manage")
    site = get_object_or_404(Site, pk=site_id, project=project) if site_id else None
    form = SiteForm(request.POST or None, instance=site)
    if request.method == "POST" and form.is_valid():
        site = form.save(commit=False)
        site.project = project
        site.save()
        log_event(request, "site_saved", "projects", f"{request.user} saved site {site.name} on {project.title}.", site.pk)
        messages.success(request, "Site saved.")
        return redirect("project_plan", project_id=project.pk)
    return render(request, "coordination/ops/site_form.html", {"form": form, "project": project, "site": site})


@login_required
def phase_form(request, project_id, phase_id=None):
    project = get_project(request.user, project_id, "manage")
    phase = get_object_or_404(Phase, pk=phase_id, project=project) if phase_id else None
    initial = {} if phase else {"order": project.phases.count() + 1}
    form = PhaseForm(request.POST or None, instance=phase, initial=initial)
    if request.method == "POST" and form.is_valid():
        phase = form.save(commit=False)
        phase.project = project
        phase.save()
        log_event(request, "phase_saved", "projects", f"{request.user} saved phase {phase.name} on {project.title}.", phase.pk)
        messages.success(request, "Phase saved.")
        return redirect("project_plan", project_id=project.pk)
    return form_page(request, form, eyebrow=project.title, title="Edit phase" if phase else "Add a phase", submit_label="Save phase", cancel_url=reverse("project_plan", args=[project.pk]))


# --- Tasks ------------------------------------------------------------------------

@login_required
def task_list(request, project_id):
    project = get_project(request.user, project_id, "view")
    tasks = project.tasks.select_related("phase", "site", "required_skill").prefetch_related("assignments__worker")
    status = request.GET.get("status", "")
    if status in Task.Status.values:
        tasks = tasks.filter(status=status)
    worker = worker_profile(request.user)
    if worker and not can_supervise_project(request.user, project):
        tasks = tasks.filter(assignments__worker=worker)
    view_mode = "list" if request.GET.get("view") == "list" else "board"
    context = project_nav_context(request, project, "tasks")
    columns = [{"status": value, "label": label, "tasks": [task for task in tasks if task.status == value]} for value, label in Task.Status.choices]
    context.update({"tasks": tasks, "status_filter": status, "status_choices": Task.Status.choices, "view_mode": view_mode, "columns": columns})
    return render(request, "coordination/ops/task_list.html", context)


@login_required
@require_POST
def task_move(request, project_id, task_id):
    """Move a task between board columns (drag and drop, or the 'Move to' control)."""
    project = get_project(request.user, project_id, "view")
    task = get_object_or_404(Task, pk=task_id, project=project)
    worker = worker_profile(request.user)
    allowed = can_supervise_project(request.user, project) or bool(worker and task.assignments.filter(worker=worker).exists())
    status = request.POST.get("status")
    if not allowed or status not in Task.Status.values:
        if wants_json(request):
            return JsonResponse({"ok": False, "message": "You cannot move this task."}, status=403)
        raise PermissionDenied
    if status == Task.Status.PENDING:
        progress = 0
    elif status == Task.Status.COMPLETED:
        progress = 100
    else:
        progress = task.progress_percentage if 0 < task.progress_percentage < 100 else 10
    previous = task.get_status_display()
    set_task_progress(task, progress)
    log_event(request, "task_moved", "tasks", f"{request.user} moved {task.title} from {previous} to {task.get_status_display()}.", task.pk)
    if wants_json(request):
        project.refresh_from_db(fields=["progress_percentage"])
        return JsonResponse({"ok": True, "status": task.status, "progress": task.progress_percentage,
                             "project_progress": project.progress_percentage, "message": f"{task.title} moved to {task.get_status_display()}."})
    messages.success(request, f"{task.title} moved to {task.get_status_display()}.")
    return redirect("task_list", project_id=project.pk)


@login_required
def task_form(request, project_id, task_id=None):
    project = get_project(request.user, project_id, "supervise")
    task = get_object_or_404(Task, pk=task_id, project=project) if task_id else None
    form = TaskForm(request.POST or None, instance=task, project=project)
    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        task.project = project
        task.created_by = task.created_by or request.user
        task.save()
        log_event(request, "task_saved", "tasks", f"{request.user} saved task {task.title} on {project.title}.", task.pk)
        messages.success(request, "Task saved. Pick workers from the suggestions below.")
        return redirect("task_detail", project_id=project.pk, task_id=task.pk)
    return form_page(request, form, eyebrow=project.title, title="Edit task" if task else "Add a task", submit_label="Save task", cancel_url=reverse("task_list", args=[project.pk]))


@login_required
def task_detail(request, project_id, task_id):
    project = get_project(request.user, project_id, "view")
    task = get_object_or_404(Task.objects.select_related("phase", "site", "required_skill"), pk=task_id, project=project)
    can_assign = can_supervise_project(request.user, project)
    worker = worker_profile(request.user)
    is_assignee = bool(worker and task.assignments.filter(worker=worker).exists())
    if worker and not can_assign and not is_assignee:
        raise PermissionDenied("This task is not assigned to you.")
    matches = rank_workers_for_task(task, limit=8) if can_assign and task.status != Task.Status.COMPLETED else []
    progress_form = TaskProgressForm(initial={"progress": task.progress_percentage})

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "assign" and can_assign:
            assign_form = TaskAssignForm(request.POST, candidates=matches)
            if assign_form.is_valid():
                try:
                    assignment, joined = assign_worker_to_task(task, assign_form.cleaned_data["worker"], request.user)
                except AssignmentError as exc:
                    messages.error(request, str(exc))
                else:
                    chosen = assignment.worker
                    log_event(request, "task_assigned", "tasks", f"{request.user} assigned {chosen} to task {task.title}.", assignment.pk)
                    alert(chosen.user, "New task assigned", f"{task.title} on {project.title}" + (f", due {task.due_date:%d %b}" if task.due_date else "") + ".",
                          f"/projects/{project.pk}/tasks/{task.pk}/", sms=True)
                    messages.success(request, f"{chosen.full_name} assigned{' and added to the project team' if joined else ''}.")
            else:
                messages.error(request, "Choose one of the suggested workers.")
            return redirect("task_detail", project_id=project.pk, task_id=task.pk)
        if action == "unassign" and can_assign:
            assignment = get_object_or_404(TaskAssignment, pk=request.POST.get("assignment_id"), task=task)
            log_event(request, "task_unassigned", "tasks", f"{request.user} removed {assignment.worker} from task {task.title}.", assignment.pk)
            assignment.delete()
            messages.success(request, "Worker removed from the task.")
            return redirect("task_detail", project_id=project.pk, task_id=task.pk)
        if action == "progress" and (can_assign or is_assignee):
            progress_form = TaskProgressForm(request.POST)
            if progress_form.is_valid():
                set_task_progress(task, progress_form.cleaned_data["progress"])
                log_event(request, "task_progress", "tasks", f"{request.user} set {task.title} to {task.progress_percentage}%.", task.pk)
                if project.manager and project.manager != request.user and task.status == Task.Status.COMPLETED:
                    alert(project.manager, "Task completed", f"{task.title} on {project.title} is complete.", f"/projects/{project.pk}/tasks/{task.pk}/")
                messages.success(request, "Progress updated.")
                return redirect("task_detail", project_id=project.pk, task_id=task.pk)
        elif action not in {"assign", "unassign", "progress"} or not (can_assign or is_assignee):
            raise PermissionDenied

    context = project_nav_context(request, project, "tasks")
    context.update({"task": task, "matches": matches, "can_assign": can_assign, "can_update": can_assign or is_assignee,
                    "progress_form": progress_form, "assignments": task.assignments.select_related("worker")})
    return render(request, "coordination/ops/task_detail.html", context)


# --- Attendance ---------------------------------------------------------------------

@login_required
def attendance_board(request, project_id):
    project = get_project(request.user, project_id, "supervise")
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    workers = team_workers(project)
    records = {record.worker_id: record for record in Attendance.objects.filter(project=project, date=day).select_related("recorded_by")}
    manual_form = ManualAttendanceForm(workers=workers)
    absence_form = AbsenceForm(workers=workers, initial={"date": day})

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "manual":
            manual_form = ManualAttendanceForm(request.POST, workers=workers)
            if manual_form.is_valid():
                try:
                    result = record_clock(manual_form.cleaned_data["worker"], project, method=Attendance.Method.MANUAL,
                                          recorded_by=request.user, note=manual_form.cleaned_data["note"])
                except AttendanceError as exc:
                    manual_form.add_error(None, str(exc))
                else:
                    verb = "clocked in" if result.action == "clock_in" else "clocked out"
                    log_event(request, f"attendance_{result.action}", "attendance", f"{request.user} manually {verb} {result.attendance.worker}: {manual_form.cleaned_data['note']}", result.attendance.pk)
                    messages.success(request, f"{result.attendance.worker.full_name} {verb}.")
                    return redirect("attendance_board", project_id=project.pk)
        elif action == "absence":
            absence_form = AbsenceForm(request.POST, workers=workers)
            if absence_form.is_valid():
                data = absence_form.cleaned_data
                try:
                    record = mark_absence(data["worker"], project, data["date"], data["status"], data["note"], request.user)
                except AttendanceError as exc:
                    absence_form.add_error(None, str(exc))
                else:
                    log_event(request, "attendance_absence", "attendance", f"{request.user} marked {record.worker} {record.get_status_display().lower()} on {record.date}.", record.pk)
                    messages.success(request, f"{record.worker.full_name} marked {record.get_status_display().lower()}.")
                    return redirect(f"{reverse('attendance_board', args=[project.pk])}?date={data['date']:%Y-%m-%d}")
        else:
            raise PermissionDenied

    rows = [{"worker": worker, "record": records.get(worker.pk)} for worker in workers]
    context = project_nav_context(request, project, "attendance")
    context.update({
        "day": day, "rows": rows, "manual_form": manual_form, "absence_form": absence_form,
        "present": sum(1 for row in rows if row["record"] and row["record"].status == Attendance.Status.PRESENT),
        "on_site": sum(1 for row in rows if row["record"] and row["record"].is_open),
        "is_today": day == timezone.localdate(), "previous_day": day - timedelta(days=1), "next_day": day + timedelta(days=1),
    })
    return render(request, "coordination/ops/attendance_board.html", context)


@login_required
def attendance_scanner(request, project_id):
    project = get_project(request.user, project_id, "supervise")
    context = project_nav_context(request, project, "attendance")
    context["sites"] = project.sites.all()
    return render(request, "coordination/ops/attendance_scanner.html", context)


@login_required
def clock_page(request):
    worker = worker_profile(request.user)
    if worker is None:
        raise PermissionDenied("Clock-in is for worker accounts. Supervisors can scan QR cards instead.")
    projects = visible_projects(request.user).filter(
        assignments__worker=worker, assignments__status=WorkerAssignment.Status.ACTIVE,
        status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS],
    ).prefetch_related("sites").distinct()
    return render(request, "coordination/ops/clock.html", {
        "worker": worker, "projects": projects, "today_record": worker.attendance.filter(date=timezone.localdate()).select_related("project").first(),
    })


def parse_decimal(value):
    try:
        return Decimal(value) if value not in (None, "") else None
    except (InvalidOperation, TypeError):
        return None


@login_required
@require_POST
def clock_api(request):
    """Record a clock-in/out from GPS self check-in or a supervisor's QR scan. Returns JSON.

    Also used by the offline queue: replays carry the original `captured_at` time and a
    `client_uuid`, so a record synced twice is only stored once.
    """
    data = request.POST
    try:
        project = get_project(request.user, data.get("project"), "view")
    except (Http404, ValueError, PermissionDenied):
        return JsonResponse({"ok": False, "message": "Choose a project you are assigned to."}, status=403)
    method = data.get("method")
    if method == Attendance.Method.QR:
        if not can_supervise_project(request.user, project):
            return JsonResponse({"ok": False, "message": "Only this project's supervisors can scan worker cards."}, status=403)
        token = parse_qr_payload(data.get("code"))
        worker = Worker.objects.filter(qr_token=token, company=project.company).first() if token else None
        if worker is None:
            return JsonResponse({"ok": False, "message": "This QR code is not a worker card for this company."}, status=400)
    elif method == Attendance.Method.GPS:
        worker = worker_profile(request.user)
        if worker is None:
            return JsonResponse({"ok": False, "message": "GPS clock-in is for worker accounts."}, status=403)
    else:
        return JsonResponse({"ok": False, "message": "Unknown check-in method."}, status=400)

    site = project.sites.filter(pk=data.get("site")).first() if data.get("site") else None
    captured_at = parse_datetime(data.get("captured_at") or "")
    if captured_at and timezone.is_naive(captured_at):
        captured_at = timezone.make_aware(captured_at)
    try:
        client_uuid = uuid.UUID(data["client_uuid"]) if data.get("client_uuid") else None
    except ValueError:
        client_uuid = None
    offline = data.get("offline") == "1"
    try:
        result = record_clock(
            worker, project, method=method, recorded_by=request.user, site=site,
            latitude=parse_decimal(data.get("latitude")), longitude=parse_decimal(data.get("longitude")),
            accuracy=parse_decimal(data.get("accuracy")), at=captured_at if offline else None,
            client_uuid=client_uuid, offline=offline,
        )
    except AttendanceError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    record = result.attendance
    if result.action != "duplicate":
        verb = "clocked in" if result.action == "clock_in" else "clocked out"
        where = f" ({result.distance} m from site)" if result.distance is not None else ""
        log_event(request, f"attendance_{result.action}", "attendance",
                  f"{worker} {verb} on {project.title} by {record.get_method_display().lower()}{where}{' — synced offline' if offline else ''}.", record.pk)
    stamp = record.clock_out if result.action == "clock_out" else record.clock_in
    message = {
        "clock_in": f"{worker.full_name} clocked in at {timezone.localtime(stamp):%H:%M}.",
        "clock_out": f"{worker.full_name} clocked out at {timezone.localtime(stamp):%H:%M} · {record.hours_worked} h worked.",
        "duplicate": "Already recorded.",
    }[result.action]
    return JsonResponse({"ok": True, "action": result.action, "message": message, "worker": worker.full_name,
                         "hours": str(record.hours_worked), "distance": result.distance})


@login_required
def worker_id_card(request, worker_id):
    company = require_operating_company(request.user, OPERATIONS_ROLES)
    worker = get_object_or_404(Worker, pk=worker_id, company=company)
    own_card = worker_profile(request.user) == worker
    if not (own_card or request.user.role in SITE_ROLES):
        raise PermissionDenied
    can_reissue = is_manager(request.user)
    if request.method == "POST":
        if not can_reissue:
            raise PermissionDenied
        worker.qr_token = new_qr_token()
        worker.save(update_fields=["qr_token"])
        log_event(request, "qr_card_reissued", "workers", f"{request.user} reissued the QR ID card for {worker}.", worker.pk)
        messages.success(request, "New card issued. Print it now; the old card no longer works.")
        return redirect("worker_id_card", worker_id=worker.pk)
    return render(request, "coordination/ops/id_card.html", {"worker": worker, "qr_svg": qr_svg(worker), "company": company, "can_reissue": can_reissue})


# --- Site reports -------------------------------------------------------------------

@login_required
def site_report_list(request, project_id):
    project = get_project(request.user, project_id, "view")
    reports = project.site_reports.select_related("author", "site", "task").prefetch_related("photos")
    page_obj, page_query = paginated(reports, request, 10)
    context = project_nav_context(request, project, "reports")
    context.update({"reports": page_obj.object_list, "page_obj": page_obj, "page_query": page_query})
    return render(request, "coordination/ops/site_report_list.html", context)


@login_required
def site_report_create(request, project_id):
    """Daily report form. Also accepts offline replays (JSON response, de-duplicated by client_uuid)."""
    project = get_project(request.user, project_id, "supervise")
    form = SiteReportForm(request.POST or None, request.FILES or None, project=project,
                          initial={"workers_on_site": Attendance.objects.filter(project=project, date=timezone.localdate(), status=Attendance.Status.PRESENT).count()})
    if request.method == "POST":
        try:
            client_uuid = uuid.UUID(request.POST["client_uuid"]) if request.POST.get("client_uuid") else None
        except ValueError:
            client_uuid = None
        if client_uuid and project.site_reports.filter(client_uuid=client_uuid).exists():
            return JsonResponse({"ok": True, "message": "Report already synced."}) if wants_json(request) else redirect("site_report_list", project_id=project.pk)
        if form.is_valid():
            with transaction.atomic():
                report = form.save(commit=False)
                report.project, report.author, report.client_uuid = project, request.user, client_uuid
                report.synced_offline = request.POST.get("offline") == "1"
                report.save()
                for image in form.cleaned_data["photos"]:
                    ReportPhoto.objects.create(report=report, image=image)
                if report.task and report.task_progress is not None:
                    set_task_progress(report.task, report.task_progress)
            log_event(request, "site_report_submitted", "reports", f"{request.user} submitted a site report for {project.title} ({report.date}).", report.pk)
            if project.manager and project.manager != request.user:
                alert(project.manager, "New site report", f"{request.user.get_full_name() or request.user}: {report.summary}", f"/projects/{project.pk}/reports/")
            if wants_json(request):
                return JsonResponse({"ok": True, "message": "Report synced.", "redirect": reverse("site_report_list", args=[project.pk])})
            messages.success(request, "Site report submitted.")
            return redirect("site_report_list", project_id=project.pk)
        if wants_json(request):
            return JsonResponse({"ok": False, "message": "The saved report has errors. Open it again to fix them.", "errors": form.errors.get_json_data()}, status=400)
    context = project_nav_context(request, project, "reports")
    context["form"] = form
    return render(request, "coordination/ops/site_report_form.html", context)


@login_required
def operations_file(request, kind, record_id):
    """Serve private site photos to people who can view the related project."""
    if kind == "report-photo":
        photo = get_object_or_404(ReportPhoto.objects.select_related("report__project"), pk=record_id)
        get_project(request.user, photo.report.project_id, "view")
        field = photo.image
    elif kind == "worker-photo":
        worker = get_object_or_404(Worker, pk=record_id)
        company = operating_company(request.user)
        if worker_profile(request.user) != worker and not (company == worker.company and request.user.role in SITE_ROLES):
            raise PermissionDenied
        field = worker.photo
    elif kind == "incident-photo":
        incident = get_object_or_404(Incident, pk=record_id)
        if not can_supervise_project(request.user, incident.project) and incident.reported_by != request.user:
            raise PermissionDenied
        field = incident.photo
    else:
        raise Http404
    if not field:
        raise Http404
    return FileResponse(field.open("rb"), filename=field.name.rsplit("/", 1)[-1])


# --- Safety incidents -------------------------------------------------------------------

def incident_projects(user):
    if is_manager(user):
        return visible_projects(user)
    if user.role == User.Role.SUPERVISOR:
        return visible_projects(user)
    return Project.objects.none()


@operations_view()
def incident_list(request):
    if request.user.role == User.Role.WORKER:
        incidents = Incident.objects.filter(reported_by=request.user)
    else:
        incidents = Incident.objects.filter(project__in=incident_projects(request.user))
    incidents = incidents.select_related("project", "site", "reported_by")
    for name, choices in (("status", Incident.Status.values), ("severity", Incident.Severity.values)):
        if request.GET.get(name) in choices:
            incidents = incidents.filter(**{name: request.GET[name]})
    page_obj, page_query = paginated(incidents, request, 20)
    return render(request, "coordination/ops/incident_list.html", {
        "incidents": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "filters": request.GET,
        "status_choices": Incident.Status.choices, "severity_choices": Incident.Severity.choices,
        "projects": visible_projects(request.user).filter(status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD]),
        "summary": [
            (dict(Incident.Kind.choices)[row["kind"]], row["count"])
            for row in Incident.objects.filter(project__in=incident_projects(request.user)).values("kind").annotate(count=Count("id")).order_by("-count")
        ] if request.user.role != User.Role.WORKER else [],
    })


@login_required
def incident_create(request, project_id):
    project = get_project(request.user, project_id, "view")
    if request.user.role not in OPERATIONS_ROLES:
        raise PermissionDenied("Only company staff can report site incidents.")
    form = IncidentForm(request.POST or None, request.FILES or None, project=project, initial={"occurred_at": timezone.localtime().replace(second=0, microsecond=0)})
    if request.method == "POST" and form.is_valid():
        incident = form.save(commit=False)
        incident.project, incident.reported_by = project, request.user
        incident.save()
        form.save_m2m()
        log_event(request, "incident_reported", "safety", f"{request.user} reported a {incident.get_severity_display().lower()} {incident.get_kind_display().lower()} on {project.title}.", incident.pk)
        urgent = incident.severity in (Incident.Severity.HIGH, Incident.Severity.CRITICAL)
        for person in {project.manager, project.company.owner, *project.supervisors.all()} - {None, request.user}:
            alert(person, f"Safety incident: {incident.get_kind_display()}", f"{incident.get_severity_display()} severity on {project.title}.", f"/incidents/{incident.pk}/", sms=urgent, email=urgent)
        messages.success(request, "Incident reported. The project team has been alerted.")
        return redirect("incident_detail", incident_id=incident.pk)
    return render(request, "coordination/ops/incident_form.html", {"form": form, "project": project})


@operations_view()
def incident_detail(request, incident_id):
    incident = get_object_or_404(Incident.objects.select_related("project", "site", "reported_by", "closed_by"), pk=incident_id)
    can_update = can_supervise_project(request.user, incident.project)
    if not can_update and incident.reported_by != request.user:
        raise PermissionDenied
    form = IncidentUpdateForm(request.POST or None, instance=incident)
    if request.method == "POST":
        if not can_update:
            raise PermissionDenied
        if form.is_valid():
            incident = form.save(commit=False)
            incident.closed_by = request.user if incident.status == Incident.Status.CLOSED else None
            incident.save()
            log_event(request, "incident_updated", "safety", f"{request.user} set incident {incident.pk} to {incident.get_status_display().lower()}.", incident.pk)
            messages.success(request, "Incident updated.")
            return redirect("incident_detail", incident_id=incident.pk)
    return render(request, "coordination/ops/incident_detail.html", {"incident": incident, "form": form, "can_update": can_update})


# --- Material and equipment requests -----------------------------------------------------------

@operations_view(*SITE_ROLES)
def material_list(request):
    items = MaterialRequest.objects.filter(project__in=visible_projects(request.user))
    if request.user.role == User.Role.SUPERVISOR:
        items = items.filter(Q(requested_by=request.user) | Q(project__supervisors=request.user)).distinct()
    if request.GET.get("status") in MaterialRequest.Status.values:
        items = items.filter(status=request.GET["status"])
    items = items.select_related("project", "site", "requested_by", "reviewed_by")
    page_obj, page_query = paginated(items, request, 20)
    pending_cost = MaterialRequest.objects.filter(project__in=visible_projects(request.user), status=MaterialRequest.Status.PENDING).aggregate(value=Sum("estimated_cost"))["value"]
    return render(request, "coordination/ops/material_list.html", {
        "items": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "filters": request.GET,
        "status_choices": MaterialRequest.Status.choices, "pending_cost": pending_cost, "can_review": is_manager(request.user),
        "projects": visible_projects(request.user).filter(status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD]),
    })


@login_required
def material_create(request, project_id):
    project = get_project(request.user, project_id, "supervise")
    form = MaterialRequestForm(request.POST or None, project=project)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.project, item.requested_by = project, request.user
        if can_manage_project(request.user, project):
            item.status, item.reviewed_by, item.reviewed_at = MaterialRequest.Status.APPROVED, request.user, timezone.now()
            item.review_note = "Raised and approved by the project manager."
        item.save()
        log_event(request, "material_requested", "materials", f"{request.user} requested {item} for {project.title}.", item.pk)
        if item.status == MaterialRequest.Status.PENDING:
            for person in {project.manager, project.company.owner} - {None}:
                alert(person, "Material request to approve", f"{item} for {project.title}.", "/materials/", sms=True)
        messages.success(request, "Request submitted for approval." if item.status == MaterialRequest.Status.PENDING else "Request recorded as approved.")
        return redirect("material_list")
    return form_page(request, form, eyebrow=project.title, title="Request materials or equipment", submit_label="Submit request", cancel_url=reverse("material_list"))


@operations_view(*MANAGER_ROLES)
def material_review(request, request_id):
    item = get_object_or_404(MaterialRequest.objects.select_related("project", "requested_by"), pk=request_id, project__company=request.company)
    form = MaterialReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        decision = form.cleaned_data["decision"]
        allowed = {
            MaterialRequest.Status.APPROVED: [MaterialRequest.Status.PENDING],
            MaterialRequest.Status.REJECTED: [MaterialRequest.Status.PENDING],
            MaterialRequest.Status.DELIVERED: [MaterialRequest.Status.APPROVED],
        }[decision]
        if item.status not in allowed:
            messages.error(request, f"A request that is {item.get_status_display().lower()} cannot be changed that way.")
            return redirect("material_review", request_id=item.pk)
        item.status, item.review_note = decision, form.cleaned_data["note"] or item.review_note
        if decision != MaterialRequest.Status.DELIVERED:
            item.reviewed_by, item.reviewed_at = request.user, timezone.now()
        item.save()
        log_event(request, f"material_{decision}", "materials", f"{request.user} marked request {item.pk} ({item}) {item.get_status_display().lower()}.", item.pk)
        alert(item.requested_by, f"Request {item.get_status_display().lower()}", f"{item} for {item.project.title}." + (f" Note: {item.review_note}" if item.review_note else ""), "/materials/", sms=True)
        messages.success(request, f"Request {item.get_status_display().lower()}.")
        return redirect("material_list")
    return render(request, "coordination/ops/material_review.html", {"item": item, "form": form})


# --- Payroll --------------------------------------------------------------------------

@operations_view(*MANAGER_ROLES)
def payroll_list(request):
    company = request.company
    today = timezone.localdate()
    default_start = today.replace(day=1)
    form = PayrollPeriodForm(request.POST or None, initial={"period_start": default_start, "period_end": today})
    if request.method == "POST" and form.is_valid():
        start, end = form.cleaned_data["period_start"], form.cleaned_data["period_end"]
        records, skipped = generate_payroll(company, start, end, request.user)
        log_event(request, "payroll_generated", "payroll", f"{request.user} generated payroll for {start} to {end} ({len(records)} workers).")
        note = f" {skipped} approved or paid record{'s were' if skipped != 1 else ' was'} left unchanged." if skipped else ""
        messages.success(request, f"Payroll calculated for {len(records)} worker{'s' if len(records) != 1 else ''}.{note}" if records or skipped else "No attendance hours in that period.")
        return redirect(f"{reverse('payroll_list')}?start={start}&end={end}")
    records = company.payroll_records.select_related("worker")
    start, end = request.GET.get("start"), request.GET.get("end")
    if start and end:
        records = records.filter(period_start=start, period_end=end)
    totals = records.aggregate(gross=Sum("gross_pay"), net=Sum("net_pay"), hours=Sum("hours_worked"))
    periods = company.payroll_records.values("period_start", "period_end").annotate(count=Count("id"), net=Sum("net_pay")).order_by("-period_end")[:12]
    page_obj, page_query = paginated(records, request, 50)
    return render(request, "coordination/ops/payroll_list.html", {
        "form": form, "records": page_obj.object_list, "page_obj": page_obj, "page_query": page_query,
        "totals": totals, "periods": periods, "selected_start": start, "selected_end": end,
    })


def payroll_record_for(request, record_id):
    record = get_object_or_404(PayrollRecord.objects.select_related("worker", "company", "generated_by"), pk=record_id)
    own = worker_profile(request.user) == record.worker
    manages = is_manager(request.user) and operating_company(request.user) == record.company
    if not (own or manages):
        raise PermissionDenied
    if own and not manages and record.status == PayrollRecord.Status.DRAFT:
        raise Http404("This payslip is not ready yet.")
    return record, manages


@login_required
def payroll_detail(request, record_id):
    record, manages = payroll_record_for(request, record_id)
    adjust_form = PayrollAdjustForm(record=record)
    if request.method == "POST":
        if not manages:
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "adjust" and record.status == PayrollRecord.Status.DRAFT:
            adjust_form = PayrollAdjustForm(request.POST, record=record)
            if adjust_form.is_valid():
                record.deductions = adjust_form.cleaned_data["deductions"]
                record.net_pay = record.gross_pay - record.deductions
                record.save(update_fields=["deductions", "net_pay"])
                log_event(request, "payroll_adjusted", "payroll", f"{request.user} set deductions of SLE {record.deductions} on payroll {record.pk}.", record.pk)
                messages.success(request, "Deductions saved.")
                return redirect("payroll_detail", record_id=record.pk)
        elif action == "approve" and record.status == PayrollRecord.Status.DRAFT:
            record.status = PayrollRecord.Status.APPROVED
            record.save(update_fields=["status"])
            log_event(request, "payroll_approved", "payroll", f"{request.user} approved payroll {record.pk} for {record.worker} (SLE {record.net_pay}).", record.pk)
            alert(record.worker.user, "Payslip ready", f"Your payslip for {record.period_start:%d %b}–{record.period_end:%d %b} is ready.", f"/payroll/{record.pk}/")
            messages.success(request, "Payroll approved. You can now pay it.")
            return redirect("payroll_detail", record_id=record.pk)
        elif action == "pay":
            try:
                txn = pay_wages(record, request.user)
            except PaymentError as exc:
                messages.error(request, str(exc))
            else:
                log_event(request, f"payment_{txn.status}", "payroll", f"{request.user} sent SLE {txn.amount} to {record.worker} via {txn.get_provider_display()} ({txn.status}, ref {txn.reference}).", txn.pk)
                (messages.success if txn.status == txn.Status.SUCCESSFUL else messages.error)(request, txn.message)
            return redirect("payroll_detail", record_id=record.pk)
        else:
            messages.error(request, "That action is not available for this payroll record.")
            return redirect("payroll_detail", record_id=record.pk)
    attendance = record.worker.attendance.filter(date__range=(record.period_start, record.period_end)).select_related("project")
    return render(request, "coordination/ops/payslip.html", {
        "record": record, "manages": manages, "adjust_form": adjust_form, "attendance": attendance,
        "transactions": record.transactions.all(),
    })


@operations_view(*MANAGER_ROLES)
def payroll_export(request):
    start, end = request.GET.get("start"), request.GET.get("end")
    records = request.company.payroll_records.select_related("worker")
    if start and end:
        records = records.filter(period_start=start, period_end=end)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="payroll-{start or "all"}-{end or ""}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Worker ID", "Worker", "Period start", "Period end", "Days present", "Hours", "Daily rate (SLE)", "Gross (SLE)", "Deductions (SLE)", "Net (SLE)", "Status"])
    for record in records:
        # Prefix cells that a spreadsheet could treat as a formula.
        name = record.worker.full_name
        safe_name = f"'{name}" if name[:1] in "=+-@" else name
        writer.writerow([record.worker.worker_id, safe_name, record.period_start, record.period_end, record.days_present, record.hours_worked,
                         record.daily_rate, record.gross_pay, record.deductions, record.net_pay, record.get_status_display()])
    log_event(request, "payroll_exported", "payroll", f"{request.user} exported payroll ({start or 'all periods'}).")
    return response


@login_required
def my_pay(request):
    worker = worker_profile(request.user)
    if worker is None:
        raise PermissionDenied
    records = worker.payroll_records.exclude(status=PayrollRecord.Status.DRAFT)
    return render(request, "coordination/ops/my_pay.html", {"worker": worker, "records": records})


@login_required
def my_attendance(request):
    worker = worker_profile(request.user)
    if worker is None:
        raise PermissionDenied
    records = worker.attendance.select_related("project", "site")
    page_obj, page_query = paginated(records, request, 31)
    month_hours = records.filter(date__gte=timezone.localdate().replace(day=1)).aggregate(value=Sum("hours_worked"))["value"] or 0
    return render(request, "coordination/ops/my_attendance.html", {"worker": worker, "records": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "month_hours": month_hours})


# --- Announcements --------------------------------------------------------------------------

@operations_view()
def announcement_list(request):
    company = request.company
    items = Announcement.objects.filter(company=company).select_related("author", "project")
    if not is_manager(request.user):
        items = items.filter(Q(project__isnull=True) | Q(project__in=visible_projects(request.user)))
    page_obj, page_query = paginated(items, request, 15)
    return render(request, "coordination/ops/announcement_list.html", {
        "items": page_obj.object_list, "page_obj": page_obj, "page_query": page_query,
        "can_post": request.user.role in SITE_ROLES,
    })


@operations_view(*SITE_ROLES)
def announcement_create(request):
    projects = visible_projects(request.user).filter(status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD])
    form = AnnouncementForm(request.POST or None, projects=projects)
    if request.user.role == User.Role.SUPERVISOR:
        form.fields["project"].required = True
        form.fields["project"].help_text = "Supervisors post to the projects they run."
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.company, item.author = request.company, request.user
        item.save()
        in_app, sms, email = broadcast(item)
        log_event(request, "announcement_posted", "communication", f"{request.user} posted '{item.title}' to {item.project or 'the whole company'} ({in_app} in-app, {sms} SMS, {email} email).", item.pk)
        messages.success(request, f"Announcement sent: {in_app} in-app" + (f", {sms} SMS" if item.send_sms else "") + (f", {email} email" if item.send_email else "") + ".")
        return redirect("announcement_list")
    return form_page(request, form, eyebrow="Communication", title="Post an announcement", submit_label="Send announcement",
                     intro="Reach your site teams in the app, and by SMS for workers without smartphones.", cancel_url=reverse("announcement_list"))


# --- Offline support ------------------------------------------------------------------------

def service_worker(request):
    """Served from the site root so it can control every page (see static/coordination/sw.js)."""
    path = settings.BASE_DIR / "static" / "coordination" / "sw.js"
    response = HttpResponse(path.read_text(encoding="utf-8"), content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


def offline_page(request):
    return render(request, "coordination/ops/offline.html")


def web_manifest(request):
    manifest = {
        "name": "BuildLink", "short_name": "BuildLink", "start_url": "/dashboard/", "display": "standalone",
        "background_color": "#fbfcf9", "theme_color": "#153d32",
        "description": "Construction workforce coordination for Freetown sites.",
    }
    return HttpResponse(json.dumps(manifest), content_type="application/manifest+json")
