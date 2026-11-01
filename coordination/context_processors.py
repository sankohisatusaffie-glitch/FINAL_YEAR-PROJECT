"""Shared portal context: notifications, role navigation, page titles, breadcrumbs and the project selector."""

from django.urls import NoReverseMatch, reverse

from .models import Notification, Project, User

PROJECT_PAGES = {
    "project_detail", "project_plan", "project_edit", "site_create", "site_edit", "phase_create", "phase_edit",
    "task_list", "task_detail", "task_create", "task_edit", "attendance_board", "attendance_scanner",
    "site_report_list", "site_report_create", "incident_create", "material_create",
    "project_workers", "project_materials", "project_incidents",
}

# (label, url name, icon, url names that mark the link active)
OVERVIEW = ("Overview", "dashboard", "home", {"dashboard"})
PROJECTS = ("Projects", "project_list", "crane", {"project_list", "project_create", *PROJECT_PAGES})
WORKFORCE = ("Workforce", "worker_list", "hardhat", {"worker_list", "worker_create", "worker_edit", "worker_id_card"})
MATERIALS = ("Materials", "material_list", "toolbox", {"material_list", "material_review"})
PAYROLL = ("Payroll", "payroll_list", "wallet", {"payroll_list", "payroll_detail"})
SAFETY = ("Safety incidents", "incident_list", "cone", {"incident_list", "incident_detail"})
ANNOUNCEMENTS = ("Announcements", "announcement_list", "megaphone", {"announcement_list", "announcement_create"})
REPORTS = ("Reports", "reports", "chart", {"reports"})
TEAM = ("Team accounts", "staff_list", "users", {"staff_list", "staff_create"})
NOTIFICATIONS = ("Notifications", "notifications", "bell", {"notifications"})

NAVIGATION = {
    User.Role.ADMIN: [
        ("Workspace", [OVERVIEW, ("Companies", "admin_companies", "building", {"admin_companies", "admin_company_review"}),
                       ("People & access", "admin_users", "users", {"admin_users"}),
                       ("Service requests", "request_list", "inbox", {"request_list", "service_request_detail"}), PROJECTS, REPORTS]),
        ("Governance", [("Complaints", "admin_complaints", "help", {"admin_complaints"}), ("Audit trail", "admin_audit", "shield", {"admin_audit"})]),
    ],
    User.Role.COMPANY: [
        ("Workspace", [OVERVIEW, PROJECTS, WORKFORCE, MATERIALS, PAYROLL, SAFETY, ANNOUNCEMENTS, REPORTS,
                       ("Client requests", "request_list", "inbox", {"request_list", "service_request_detail"})]),
        ("Company", [TEAM, ("Company profile", "company_profile_edit", "building", {"company_profile_edit"}),
                     ("Get support", "complaint_create", "help", {"complaint_create"})]),
    ],
    User.Role.MANAGER: [
        ("Workspace", [OVERVIEW, PROJECTS, WORKFORCE, MATERIALS, PAYROLL, SAFETY, ANNOUNCEMENTS, REPORTS]),
        ("Company", [TEAM]),
    ],
    User.Role.SUPERVISOR: [
        ("Site", [OVERVIEW, PROJECTS, MATERIALS, SAFETY, ANNOUNCEMENTS]),
    ],
    User.Role.WORKER: [
        ("My work", [OVERVIEW, ("Clock in / out", "clock_page", "clock", {"clock_page"}),
                     ("My projects", "project_list", "crane", PROJECTS[3]),
                     ("My attendance", "my_attendance", "calendar", {"my_attendance"}),
                     ("My pay", "my_pay", "wallet", {"my_pay", "payroll_detail"}), ANNOUNCEMENTS,
                     ("My incident reports", "incident_list", "cone", SAFETY[3])]),
    ],
    User.Role.CLIENT: [
        ("Workspace", [OVERVIEW, ("Find a company", "company_directory", "search", {"company_directory", "company_public_profile"}),
                       ("My requests", "request_list", "inbox", {"request_list", "service_request_detail", "service_request_create"}),
                       ("My projects", "project_list", "crane", PROJECTS[3])]),
        ("Account", [("Profile", "profile", "user", {"profile"}), ("Get support", "complaint_create", "help", {"complaint_create"})]),
    ],
}

# Up to four destinations for the mobile bottom bar; "Menu" opens the full list.
BOTTOM_NAV = {
    User.Role.ADMIN: ["dashboard", "admin_companies", "project_list", "reports"],
    User.Role.COMPANY: ["dashboard", "project_list", "worker_list", "incident_list"],
    User.Role.MANAGER: ["dashboard", "project_list", "worker_list", "incident_list"],
    User.Role.SUPERVISOR: ["dashboard", "project_list", "incident_list", "material_list"],
    User.Role.WORKER: ["dashboard", "clock_page", "my_pay", "project_list"],
    User.Role.CLIENT: ["dashboard", "company_directory", "request_list", "project_list"],
}

PAGE_TITLES = {
    "dashboard": "Overview", "project_create": "New project", "project_detail": "Project overview", "project_plan": "Phases & sites",
    "project_edit": "Edit project", "site_create": "Add site", "site_edit": "Edit site", "phase_create": "Add phase", "phase_edit": "Edit phase",
    "task_list": "Task board", "task_detail": "Task", "task_create": "New task", "task_edit": "Edit task",
    "attendance_board": "Attendance", "attendance_scanner": "Scan QR cards", "site_report_list": "Site reports",
    "site_report_create": "Daily report", "incident_create": "Report incident", "material_create": "Request materials",
    "project_workers": "Project workers", "project_materials": "Project materials", "project_incidents": "Project incidents",
    "incident_detail": "Incident", "material_review": "Review request", "payroll_detail": "Payslip", "worker_create": "Add worker",
    "worker_edit": "Edit worker", "worker_id_card": "QR ID card", "staff_create": "Add team account", "announcement_create": "New announcement",
    "service_request_detail": "Request", "service_request_create": "Request a service", "admin_company_review": "Review company",
    "clock_page": "Clock in / out", "my_attendance": "My attendance", "my_pay": "My pay", "company_public_profile": "Company profile",
    "notifications": "Notifications", "profile": "Profile", "complaint_create": "Get support", "company_profile_edit": "Company profile",
}

PROJECT_TAB_PARENT = {
    "task_detail": ("Task board", "task_list"), "task_create": ("Task board", "task_list"), "task_edit": ("Task board", "task_list"),
    "site_create": ("Phases & sites", "project_plan"), "site_edit": ("Phases & sites", "project_plan"),
    "phase_create": ("Phases & sites", "project_plan"), "phase_edit": ("Phases & sites", "project_plan"),
    "attendance_scanner": ("Attendance", "attendance_board"), "site_report_create": ("Site reports", "site_report_list"),
}

SECTION_PARENT = {
    "incident_detail": ("Safety incidents", "incident_list"), "material_review": ("Materials", "material_list"),
    "worker_create": ("Workforce", "worker_list"), "worker_edit": ("Workforce", "worker_list"), "worker_id_card": ("Workforce", "worker_list"),
    "staff_create": ("Team accounts", "staff_list"), "announcement_create": ("Announcements", "announcement_list"),
    "service_request_detail": ("Requests", "request_list"), "admin_company_review": ("Companies", "admin_companies"),
    "project_create": ("Projects", "project_list"), "company_public_profile": ("Find a company", "company_directory"),
}


def safe_reverse(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def build_navigation(role, current):
    sections = []
    for label, items in NAVIGATION.get(role, []):
        links = [{"label": text, "url": safe_reverse(name), "icon": icon, "name": name, "active": current in active}
                 for text, name, icon, active in items]
        sections.append({"label": label, "links": [link for link in links if link["url"]]})
    return sections


def visible_project_title(user, project_id):
    from .access import visible_projects

    return visible_projects(user).filter(pk=project_id).values_list("title", flat=True).first()


def build_breadcrumbs(user, current, kwargs, title):
    if current in (None, "dashboard"):
        return []
    trail = [("Overview", reverse("dashboard"))]
    project_id = kwargs.get("project_id")
    if current in PROJECT_PAGES and project_id:
        project_title = visible_project_title(user, project_id)
        if project_title is None:
            return []
        trail.append(("Projects", reverse("project_list")))
        trail.append((project_title, reverse("project_detail", args=[project_id])))
        if current in PROJECT_TAB_PARENT:
            label, name = PROJECT_TAB_PARENT[current]
            trail.append((label, reverse(name, args=[project_id])))
        if current != "project_detail":
            trail.append((title, None))
        else:
            trail[-1] = (project_title, None)
        return trail
    if current == "payroll_detail":
        parent = ("My pay", "my_pay") if user.role == User.Role.WORKER else ("Payroll", "payroll_list")
        trail.append((parent[0], reverse(parent[1])))
    elif current in SECTION_PARENT:
        label, name = SECTION_PARENT[current]
        trail.append((label, reverse(name)))
    trail.append((title, None))
    return trail


def portal_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"unread_notification_count": 0}
    match = getattr(request, "resolver_match", None)
    current = match.url_name if match else None
    kwargs = match.kwargs if match else {}
    navigation = build_navigation(user.role, current)
    flat = {link["name"]: link for section in navigation for link in section["links"]}
    title = PAGE_TITLES.get(current) or next((link["label"] for link in flat.values() if link["active"]), "BuildLink")
    context = {
        "unread_notification_count": Notification.objects.filter(user=user, read_at__isnull=True).count(),
        "nav_sections": navigation,
        "notifications_link": {"url": reverse("notifications"), "active": current == "notifications"},
        "bottom_nav": [flat[name] for name in BOTTOM_NAV.get(user.role, []) if name in flat],
        "page_title": title,
        "breadcrumbs": build_breadcrumbs(user, current, kwargs, title),
        "current_project_id": kwargs.get("project_id"),
    }
    if user.role != User.Role.CLIENT:
        from .access import visible_projects

        context["header_projects"] = visible_projects(user).filter(
            status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD]
        ).order_by("title").values("pk", "title")[:40]
    return context
