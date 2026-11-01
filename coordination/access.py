"""Role and ownership rules for company staff and site operations.

Every site-operations view goes through these helpers so that access is checked the
same way everywhere: managers (the company owner or a project manager) control their
company's projects, supervisors work on the projects they are assigned to, workers see
their own projects and records, and clients get read-only access to their own projects.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Company, Project, User

MANAGER_ROLES = (User.Role.COMPANY, User.Role.MANAGER)
SITE_ROLES = (User.Role.COMPANY, User.Role.MANAGER, User.Role.SUPERVISOR)
OPERATIONS_ROLES = (*SITE_ROLES, User.Role.WORKER)


def operating_company(user):
    """The approved company a staff account works for, or None."""
    company = user.company_scope if user.is_authenticated else None
    if company and company.status == Company.Status.APPROVED and company.owner.is_active:
        return company
    return None


def require_operating_company(user, roles=OPERATIONS_ROLES):
    if user.role not in roles:
        raise PermissionDenied("Your account does not have access to this page.")
    company = operating_company(user)
    if company is None:
        raise PermissionDenied("Site operations are available once your company is approved.")
    return company


def is_manager(user):
    return user.role in MANAGER_ROLES


def worker_profile(user):
    return getattr(user, "worker_profile", None) if user.role == User.Role.WORKER else None


def visible_projects(user):
    """Projects the user may open."""
    if user.role == User.Role.ADMIN:
        return Project.objects.all()
    if user.role == User.Role.CLIENT:
        return Project.objects.filter(client=user)
    company = operating_company(user)
    if company is None:
        return Project.objects.none()
    if is_manager(user):
        return company.projects.all()
    if user.role == User.Role.SUPERVISOR:
        return company.projects.filter(supervisors=user)
    worker = worker_profile(user)
    if worker is None:
        return Project.objects.none()
    return company.projects.filter(pk__in=worker.assignments.values("project")).distinct()


def can_manage_project(user, project):
    return is_manager(user) and operating_company(user) == project.company


def can_supervise_project(user, project):
    if can_manage_project(user, project):
        return True
    return (
        user.role == User.Role.SUPERVISOR
        and operating_company(user) == project.company
        and project.supervisors.filter(pk=user.pk).exists()
    )


def get_project(user, project_id, level="view"):
    """Fetch a project the user may access at `level` (view, supervise or manage)."""
    project = get_object_or_404(Project.objects.select_related("company", "client", "request"), pk=project_id)
    allowed = {
        "view": lambda: visible_projects(user).filter(pk=project.pk).exists(),
        "supervise": lambda: can_supervise_project(user, project),
        "manage": lambda: can_manage_project(user, project),
    }[level]()
    if not allowed:
        raise PermissionDenied("You do not have access to this project.")
    return project
