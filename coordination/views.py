from datetime import date, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_POST

from .forms import (
    AssignmentForm,
    CancellationForm,
    ClientProfileForm,
    ClientRegistrationForm,
    CompanyProfileForm,
    CompanyRegistrationForm,
    ComplaintForm,
    DeclineRequestForm,
    MessageForm,
    ProjectUpdateForm,
    RatingForm,
    ReviewCompanyForm,
    ServiceRequestForm,
    VerifiedLoginForm,
    WorkerForm,
    mask_email,
)
from .models import (
    Attendance,
    AuditLog,
    ClientProfile,
    Company,
    Complaint,
    Incident,
    Message,
    Notification,
    Project,
    Rating,
    ReportPhoto,
    Service,
    ServiceRequest,
    User,
    Worker,
    WorkerAssignment,
)
from .access import can_manage_project, can_supervise_project, get_project, operating_company, visible_projects
from .dashboards import LABOUR_COST, attendance_between, labour_cost_between, manager_dashboard, supervisor_dashboard, worker_dashboard
from .utils import log_event, notify, notify_admins
from .verification import can_resend, email_verification_token, mark_verified, send_verification_email, show_link_on_screen


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied("Your account does not have access to this page.")
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


def paginated(queryset, request, per_page=25):
    page_obj = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return page_obj, query.urlencode()


def filters_without(request, *names):
    """The current query string minus the given parameters, for building toggle links."""
    query = request.GET.copy()
    for name in names:
        query.pop(name, None)
    return query.urlencode()


def recommended_trade(service_request):
    if service_request.preferred_trade:
        return service_request.preferred_trade.strip()
    service_name = service_request.service.name.casefold() if service_request.service else ""
    mappings = [
        ("electr", "electrician"), ("masonry", "mason"), ("carpent", "carpenter"),
        ("plumb", "plumber"), ("weld", "welder"), ("paint", "painter"),
        ("tiling", "tiler"), ("steel", "steel fixer"), ("roof", "roofer"),
        ("heavy equipment", "heavy equipment operator"), ("survey", "survey assistant"),
    ]
    return next((trade for keyword, trade in mappings if keyword in service_name), "")


def home(request):
    companies = Company.objects.filter(status=Company.Status.APPROVED, owner__is_active=True).annotate(rating_average=Avg("ratings__overall_rating")).prefetch_related("services")[:6]
    return render(request, "coordination/home.html", {"featured_companies": companies})


@login_required
@require_POST
def session_heartbeat(request):
    """Refresh an active user's session through the normal security middleware."""
    return HttpResponse(status=204)


def register_company(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = CompanyRegistrationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"].strip(), email=data["email"], password=data["password"],
                first_name=data["first_name"].strip(), last_name=data["last_name"].strip(), role=User.Role.COMPANY,
            )
            company = Company.objects.create(
                owner=user, name=data["name"].strip(), registration_number=data["registration_number"],
                company_type=data["company_type"], established_year=data["established_year"], description=data["description"],
                telephone=data["telephone"], contact_person=data["contact_person"], contact_position=data["contact_position"],
                address=data["address"], city=data["city"], area=data["area"], logo=data["logo"],
                registration_document=data["registration_document"], status=Company.Status.PENDING,
            )
            company.services.set(data["services"])
            log_event(request, "company_registered", "companies", f"{company.name} submitted a company application.", company.pk)
            notify(user, "Application submitted", "Your company application is pending administrator review.", "/dashboard/")
        return start_email_verification(request, user)
    return render(request, "coordination/register.html", {"form": form, "account_kind": "company"})


def register_client(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = ClientRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"].strip(), email=data["email"], password=data["password"],
                first_name=data["first_name"].strip(), last_name=data["last_name"].strip(), role=User.Role.CLIENT,
            )
            ClientProfile.objects.create(
                user=user, telephone=data["telephone"], address=data["address"], location=data["location"],
                organization_name=data["organization_name"], client_type=data["client_type"],
            )
            log_event(request, "client_registered", "accounts", f"Client account {user.username} was created.", user.pk)
        return start_email_verification(request, user)
    return render(request, "coordination/register.html", {"form": form, "account_kind": "client"})


PENDING_VERIFICATION_KEY = "pending_verification_user"


def start_email_verification(request, user):
    """Send the confirmation link and show the 'check your email' page."""
    link = send_verification_email(user)
    request.session[PENDING_VERIFICATION_KEY] = user.pk
    if show_link_on_screen():
        request.session["dev_verification_link"] = link
    return redirect("verification_sent")


def pending_user(request):
    user_id = request.session.get(PENDING_VERIFICATION_KEY)
    return User.objects.filter(pk=user_id, email_verified=False, is_active=True).first() if user_id else None


def verification_sent(request):
    """'Check your email' page, with a rate-limited resend button."""
    user = pending_user(request)
    if user is None:
        return redirect("login")
    if request.method == "POST":
        if can_resend(user):
            link = send_verification_email(user)
            if show_link_on_screen():
                request.session["dev_verification_link"] = link
            log_event(request, "verification_resent", "accounts", f"Confirmation email resent to {user.username}.", user.pk)
            messages.success(request, "A new confirmation link is on its way.")
        else:
            messages.error(request, "A link was sent less than two minutes ago. Check your inbox and spam folder before asking again.")
        return redirect("verification_sent")
    return render(request, "coordination/verification_sent.html", {
        "masked_email": mask_email(user.email), "dev_link": request.session.get("dev_verification_link") if show_link_on_screen() else None,
    })


def verify_email(request, uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None
    if user is not None and user.email_verified:
        messages.info(request, "Your email address is already confirmed. Please sign in.")
        return redirect("login")
    if user is None or not user.is_active or not email_verification_token.check_token(user, token):
        return render(request, "coordination/verification_invalid.html", {"can_resend": pending_user(request) is not None}, status=400)
    mark_verified(user)
    request.session.pop(PENDING_VERIFICATION_KEY, None)
    request.session.pop("dev_verification_link", None)
    log_event(request, "email_verified", "accounts", f"{user.username} confirmed their email address.", user.pk)
    company = getattr(user, "company", None) if user.role == User.Role.COMPANY else None
    if company is not None:
        notify_admins("Company application received", f"{company.name} is waiting for verification.", "/admin-portal/companies/")
    login(request, user)
    messages.success(request, "Email confirmed. Welcome to BuildLink." + (" Your company application is now with an administrator for review." if company else ""))
    return redirect("dashboard")


class PortalLoginView(LoginView):
    """Sign-in that sends people with an unconfirmed email to the resend page."""

    template_name = "coordination/login.html"
    authentication_form = VerifiedLoginForm

    def form_invalid(self, form):
        if form.unverified_user is not None:
            self.request.session[PENDING_VERIFICATION_KEY] = form.unverified_user.pk
        return super().form_invalid(form)


@login_required
def dashboard(request):
    user = request.user
    if user.role == User.Role.ADMIN:
        context = {
            "company_total": Company.objects.count(),
            "pending_companies": Company.objects.filter(status__in=[Company.Status.PENDING, Company.Status.UNDER_REVIEW]).count(),
            "approved_companies": Company.objects.filter(status=Company.Status.APPROVED).count(),
            "worker_total": Worker.objects.count(),
            "client_total": User.objects.filter(role=User.Role.CLIENT).count(),
            "request_total": ServiceRequest.objects.count(),
            "active_projects": Project.objects.filter(status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD]).count(),
            "completed_projects": Project.objects.filter(status=Project.Status.COMPLETED).count(),
            "recent_companies": Company.objects.order_by("-created_at")[:5],
            "recent_requests": ServiceRequest.objects.select_related("company", "client").order_by("-created_at")[:6],
            "service_demand": Service.objects.annotate(request_count=Count("requests")).order_by("-request_count")[:5],
            "location_counts": Company.objects.filter(status=Company.Status.APPROVED).values("city").annotate(count=Count("id")).order_by("-count")[:5],
        }
        return render(request, "coordination/admin_dashboard.html", context)
    if user.role == User.Role.COMPANY:
        company = get_object_or_404(Company, owner=user)
        context = {"company": company}
        if company.status == Company.Status.APPROVED:
            context.update({
                "worker_total": company.workers.filter(employment_status=Worker.Employment.ACTIVE).count(),
                "available_workers": company.workers.filter(availability=Worker.Availability.AVAILABLE, employment_status=Worker.Employment.ACTIVE).count(),
                "active_requests": company.service_requests.filter(status__in=[ServiceRequest.Status.SUBMITTED, ServiceRequest.Status.UNDER_REVIEW]).count(),
                "active_projects": company.projects.filter(status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD]).count(),
                "recent_requests": company.service_requests.select_related("client").order_by("-created_at")[:5],
                "recent_projects": company.projects.order_by("-created_at")[:5],
                "rating_average": company.average_rating,
            })
        if company.status == Company.Status.APPROVED:
            context.update(manager_dashboard(company))
        return render(request, "coordination/company_dashboard.html", context)
    if user.role == User.Role.MANAGER:
        company = operating_company(user)
        if company is None:
            raise PermissionDenied("Your company is not active.")
        context = {"company": company, **manager_dashboard(company)}
        return render(request, "coordination/ops/manager_dashboard.html", context)
    if user.role == User.Role.SUPERVISOR:
        if operating_company(user) is None:
            raise PermissionDenied("Your company is not active.")
        return render(request, "coordination/ops/supervisor_dashboard.html", supervisor_dashboard(user))
    if user.role == User.Role.WORKER:
        worker = getattr(user, "worker_profile", None)
        if worker is None or operating_company(user) is None:
            raise PermissionDenied("This worker account is not linked to an active worker record.")
        return render(request, "coordination/ops/worker_dashboard.html", worker_dashboard(worker))
    if user.role == User.Role.CLIENT:
        context = {
            "active_requests": user.service_requests.filter(status__in=[ServiceRequest.Status.SUBMITTED, ServiceRequest.Status.UNDER_REVIEW, ServiceRequest.Status.ACCEPTED]).count(),
            "active_projects": user.projects.filter(status__in=[Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD]).count(),
            "completed_projects": user.projects.filter(status=Project.Status.COMPLETED).count(),
            "recent_requests": user.service_requests.select_related("company").order_by("-created_at")[:5],
            "recent_projects": user.projects.order_by("-created_at")[:5],
        }
        return render(request, "coordination/client_dashboard.html", context)
    raise PermissionDenied("This account has no portal role.")


@login_required
def company_directory(request):
    companies = Company.objects.filter(status=Company.Status.APPROVED, owner__is_active=True).select_related("owner").prefetch_related("services").annotate(
        rating_average=Avg("ratings__overall_rating"),
        available_workers=Count("workers", filter=Q(workers__availability=Worker.Availability.AVAILABLE, workers__employment_status=Worker.Employment.ACTIVE), distinct=True),
    )
    query = request.GET.get("q", "").strip()
    location = request.GET.get("location", "").strip()
    service = request.GET.get("service", "").strip()
    minimum_rating = request.GET.get("rating", "").strip()
    experience = request.GET.get("experience", "").strip()
    availability = request.GET.get("availability", "").strip()
    if query:
        companies = companies.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(services__name__icontains=query))
    if location:
        companies = companies.filter(Q(city__icontains=location) | Q(area__icontains=location))
    if service:
        try:
            companies = companies.filter(services__id=int(service), services__active=True)
        except ValueError:
            pass
    if minimum_rating:
        try:
            rating = int(minimum_rating)
            if 1 <= rating <= 5:
                companies = companies.filter(rating_average__gte=rating)
        except ValueError:
            pass
    if experience:
        try:
            years = int(experience)
            if 0 <= years <= 100:
                companies = companies.filter(established_year__lte=timezone.now().year - years)
        except ValueError:
            pass
    if availability == "available":
        companies = companies.filter(available_workers__gt=0)
    page_obj, page_query = paginated(companies.distinct(), request, 9)
    return render(request, "coordination/company_directory.html", {"companies": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "total_count": page_obj.paginator.count, "services": Service.objects.filter(active=True), "filters": request.GET})


def company_public_profile(request, company_id):
    company = get_object_or_404(Company.objects.filter(status=Company.Status.APPROVED, owner__is_active=True).prefetch_related("services"), pk=company_id)
    ratings = company.ratings.select_related("client", "project")[:6]
    trade_counts = company.workers.filter(employment_status=Worker.Employment.ACTIVE).values("primary_trade").annotate(count=Count("id")).order_by("-count")
    active_worker_count = company.workers.filter(employment_status=Worker.Employment.ACTIVE).count()
    return render(request, "coordination/company_profile.html", {"company": company, "ratings": ratings, "trade_counts": trade_counts, "active_worker_count": active_worker_count})


@role_required(User.Role.COMPANY)
def company_profile_edit(request):
    company = get_object_or_404(Company, owner=request.user)
    if company.status != Company.Status.APPROVED:
        messages.error(request, "Company profile editing is available after approval.")
        return redirect("dashboard")
    form = CompanyProfileForm(request.POST or None, request.FILES or None, instance=company)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_event(request, "profile_updated", "companies", f"{company.name} updated its public profile.", company.pk)
        messages.success(request, "Company profile updated.")
        return redirect("company_profile_edit")
    return render(request, "coordination/form_page.html", {"form": form, "eyebrow": "Company profile", "title": "Update your public profile", "submit_label": "Save profile"})


@role_required(User.Role.COMPANY, User.Role.MANAGER)
def worker_list(request):
    company = operating_company(request.user)
    if company is None:
        messages.error(request, "Workforce tools are available after company approval.")
        return redirect("dashboard")
    workers = company.workers.all()
    query = request.GET.get("q", "").strip()
    if query:
        workers = workers.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(worker_id__icontains=query) | Q(primary_trade__icontains=query) | Q(skills__icontains=query))
    if request.GET.get("availability"):
        workers = workers.filter(availability=request.GET["availability"])
    if request.GET.get("employment"):
        workers = workers.filter(employment_status=request.GET["employment"])
    if request.GET.get("min_experience"):
        try:
            minimum = int(request.GET["min_experience"])
            if 0 <= minimum <= 70:
                workers = workers.filter(experience_years__gte=minimum)
        except ValueError:
            pass
    view_mode = "cards" if request.GET.get("view") == "cards" else "table"
    page_obj, page_query = paginated(workers, request, 24 if view_mode == "cards" else 25)
    return render(request, "coordination/worker_list.html", {
        "workers": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "total_count": page_obj.paginator.count,
        "filters": request.GET, "availability_choices": Worker.Availability.choices, "view_mode": view_mode,
        "view_query": filters_without(request, "view", "page"),
    })


@role_required(User.Role.COMPANY, User.Role.MANAGER)
def worker_create(request):
    company = operating_company(request.user)
    if company is None:
        raise PermissionDenied
    form = WorkerForm(request.POST or None, request.FILES or None, company=company)
    if request.method == "POST" and form.is_valid():
        worker = form.save(commit=False)
        worker.company = company
        worker.save()
        log_event(request, "worker_added", "workers", f"{company.name} added {worker.first_name} {worker.last_name} ({worker.worker_id}).", worker.pk)
        messages.success(request, "Worker added to your workforce.")
        return redirect("worker_list")
    return render(request, "coordination/form_page.html", {"form": form, "eyebrow": "Workforce", "title": "Add a worker", "submit_label": "Add worker"})


@role_required(User.Role.COMPANY, User.Role.MANAGER)
def worker_edit(request, worker_id):
    company = operating_company(request.user)
    if company is None:
        raise PermissionDenied
    worker = get_object_or_404(Worker, pk=worker_id, company=company)
    form = WorkerForm(request.POST or None, request.FILES or None, instance=worker, company=company)
    if request.method == "POST" and form.is_valid():
        updated = form.save()
        if updated.employment_status == Worker.Employment.FORMER:
            updated.availability = Worker.Availability.INACTIVE
            updated.save(update_fields=["availability"])
        log_event(request, "worker_updated", "workers", f"{company.name} updated worker {worker.worker_id}.", worker.pk)
        messages.success(request, "Worker record updated.")
        return redirect("worker_list")
    return render(request, "coordination/form_page.html", {"form": form, "eyebrow": "Workforce", "title": f"Edit {worker.first_name} {worker.last_name}", "submit_label": "Save changes"})


@role_required(User.Role.CLIENT)
def service_request_create(request, company_id):
    company = get_object_or_404(Company, pk=company_id, status=Company.Status.APPROVED, owner__is_active=True)
    form = ServiceRequestForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        service_request = form.save(commit=False)
        service_request.client = request.user
        service_request.company = company
        service_request.title = f"{service_request.service.name} in {service_request.location}"[:180]
        if service_request.service and not company.services.filter(pk=service_request.service_id).exists():
            form.add_error("service", "This company does not list the selected service.")
        else:
            service_request.save()
            log_event(request, "request_submitted", "requests", f"{request.user} submitted request {service_request.pk} to {company.name}.", service_request.pk)
            notify(company.owner, "New service request", f"{request.user.get_full_name() or request.user.username} sent a request: {service_request.title}.", f"/requests/{service_request.pk}/")
            messages.success(request, "Your request has been sent to the company.")
            return redirect("service_request_detail", request_id=service_request.pk)
    form.fields["service"].queryset = company.services.filter(active=True)
    return render(request, "coordination/form_page.html", {"form": form, "eyebrow": company.name, "title": "Request construction services", "submit_label": "Send request", "multipart": True})


@login_required
def request_list(request):
    if request.user.role == User.Role.COMPANY:
        company = get_object_or_404(Company, owner=request.user)
        requests = company.service_requests.select_related("client", "service")
    elif request.user.role == User.Role.CLIENT:
        requests = request.user.service_requests.select_related("company", "service")
    elif request.user.role == User.Role.ADMIN:
        requests = ServiceRequest.objects.select_related("client", "company", "service")
    else:
        raise PermissionDenied
    status_filter = request.GET.get("status", "")
    if status_filter:
        requests = requests.filter(status=status_filter)
    page_obj, page_query = paginated(requests, request)
    return render(request, "coordination/request_list.html", {"requests": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "status_choices": ServiceRequest.Status.choices, "filters": request.GET})


@login_required
def service_request_detail(request, request_id):
    service_request = get_object_or_404(ServiceRequest.objects.select_related("company", "client", "service"), pk=request_id)
    is_client = request.user == service_request.client
    is_company = request.user == service_request.company.owner
    is_admin = request.user.role == User.Role.ADMIN
    if not (is_client or is_company or is_admin):
        raise PermissionDenied
    if not is_admin and request.user.role not in [User.Role.CLIENT, User.Role.COMPANY]:
        raise PermissionDenied
    form = MessageForm()
    decline_form = DeclineRequestForm()
    cancel_form = CancellationForm()
    complaint_form = ComplaintForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "message" and (is_client or is_company):
            form = MessageForm(request.POST)
            if form.is_valid():
                message = form.save(commit=False)
                message.request = service_request
                message.sender = request.user
                message.save()
                recipient = service_request.company.owner if is_client else service_request.client
                notify(recipient, "New project conversation message", f"New message about {service_request.title}.", f"/requests/{service_request.pk}/")
                log_event(request, "message_sent", "messaging", f"Message sent on request {service_request.pk}.", message.pk)
                messages.success(request, "Message sent.")
                return redirect("service_request_detail", request_id=service_request.pk)
        elif action == "review" and is_company:
            new_status = request.POST.get("status")
            allowed = [ServiceRequest.Status.UNDER_REVIEW, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.DECLINED]
            if service_request.status not in [ServiceRequest.Status.SUBMITTED, ServiceRequest.Status.UNDER_REVIEW] or new_status not in allowed:
                raise PermissionDenied
            if new_status == ServiceRequest.Status.DECLINED:
                decline_form = DeclineRequestForm(request.POST)
                if decline_form.is_valid():
                    service_request.decline_reason = decline_form.cleaned_data["reason"]
                else:
                    form = MessageForm()
            if new_status != ServiceRequest.Status.DECLINED or decline_form.is_valid():
                with transaction.atomic():
                    service_request.status = new_status
                    service_request.save()
                    if new_status == ServiceRequest.Status.ACCEPTED:
                        project, _ = Project.objects.get_or_create(
                            request=service_request,
                            defaults={"client": service_request.client, "company": service_request.company, "title": service_request.title,
                                      "description": service_request.description, "start_date": service_request.preferred_start_date,
                                      "location": service_request.location},
                        )
                        notify(service_request.client, "Request accepted", f"{service_request.company.name} accepted your request. A project is now open.", f"/projects/{project.pk}/")
                    elif new_status == ServiceRequest.Status.DECLINED:
                        notify(service_request.client, "Request declined", f"{service_request.company.name} declined your request.", f"/requests/{service_request.pk}/")
                    else:
                        notify(service_request.client, "Request under review", f"{service_request.company.name} is reviewing your request.", f"/requests/{service_request.pk}/")
                    log_event(request, f"request_{new_status}", "requests", f"{service_request.company.name} changed request {service_request.pk} to {service_request.get_status_display()}.", service_request.pk)
                messages.success(request, "Request status updated.")
                return redirect("service_request_detail", request_id=service_request.pk)
        elif action == "cancel" and is_client:
            cancel_form = CancellationForm(request.POST)
            cancellable = service_request.status in [ServiceRequest.Status.SUBMITTED, ServiceRequest.Status.UNDER_REVIEW, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS]
            if not cancellable:
                cancel_form.add_error(None, "This request can no longer be cancelled.")
            elif cancel_form.is_valid():
                with transaction.atomic():
                    service_request.status = ServiceRequest.Status.CANCELLED
                    service_request.cancellation_reason = cancel_form.cleaned_data["reason"]
                    service_request.cancelled_by = request.user
                    service_request.cancelled_at = timezone.now()
                    service_request.save()
                    if hasattr(service_request, "project"):
                        project = service_request.project
                        project.status = Project.Status.CANCELLED
                        project.save(update_fields=["status", "updated_at"])
                        active_assignments = project.assignments.filter(status=WorkerAssignment.Status.ACTIVE)
                        worker_ids = list(active_assignments.values_list("worker_id", flat=True))
                        active_assignments.update(status=WorkerAssignment.Status.REMOVED, removed_at=timezone.now())
                        Worker.objects.filter(pk__in=worker_ids, availability=Worker.Availability.ASSIGNED).update(availability=Worker.Availability.AVAILABLE)
                notify(service_request.company.owner, "Request cancelled", f"The client cancelled {service_request.title}.", f"/requests/{service_request.pk}/")
                log_event(request, "request_cancelled", "requests", f"Client cancelled request {service_request.pk}.", service_request.pk)
                messages.success(request, "Request cancelled.")
                return redirect("service_request_detail", request_id=service_request.pk)
        elif action == "complaint" and (is_client or is_company):
            complaint_form = ComplaintForm(request.POST)
            if complaint_form.is_valid():
                complaint = complaint_form.save(commit=False)
                complaint.request = service_request
                complaint.submitted_by = request.user
                complaint.save()
                notify_admins("Complaint submitted", f"A complaint was filed about request {service_request.pk}.", "/admin-portal/complaints/")
                log_event(request, "complaint_submitted", "complaints", f"Complaint opened for request {service_request.pk}.", complaint.pk)
                messages.success(request, "Your concern was sent to the administrator.")
                return redirect("service_request_detail", request_id=service_request.pk)
        else:
            raise PermissionDenied
    if is_client or is_company:
        unread = service_request.messages.filter(read_at__isnull=True).exclude(sender=request.user)
        unread.update(read_at=timezone.now())
    return render(request, "coordination/request_detail.html", {
        "service_request": service_request,
        "messages_thread": service_request.messages.select_related("sender"),
        "message_form": form, "decline_form": decline_form, "cancel_form": cancel_form, "complaint_form": complaint_form,
        "can_manage": is_company, "can_cancel": is_client and service_request.status in [ServiceRequest.Status.SUBMITTED, ServiceRequest.Status.UNDER_REVIEW, ServiceRequest.Status.ACCEPTED, ServiceRequest.Status.IN_PROGRESS],
    })


STOCK_PROJECT_PHOTOS = ["coordination/images/dashboard-workers.jpg", "coordination/images/dashboard-cranes.jpg"]


def decorate_project_card(project):
    """Cover photo, current phase and delay flag for a project card."""
    photo = ReportPhoto.objects.filter(report__project=project).order_by("-uploaded_at").first()
    project.cover_url = reverse("operations_file", args=["report-photo", photo.pk]) if photo else static(STOCK_PROJECT_PHOTOS[project.pk % len(STOCK_PROJECT_PHOTOS)])
    phases = list(project.phases.all())
    project.current_phase = next((phase for phase in phases if phase.status == "in_progress"), None) or next((phase for phase in phases if phase.status == "not_started"), None)
    open_statuses = (Project.Status.PLANNED, Project.Status.IN_PROGRESS, Project.Status.ON_HOLD)
    project.is_delayed = bool(project.expected_end_date and project.status in open_statuses and project.expected_end_date < timezone.localdate())
    return project


@login_required
def project_list(request):
    projects = visible_projects(request.user).select_related("client", "company", "request").prefetch_related("phases")
    status = request.GET.get("status", "")
    if status in Project.Status.values:
        projects = projects.filter(status=status)
    can_create = request.user.role in (User.Role.COMPANY, User.Role.MANAGER) and operating_company(request.user) is not None
    page_obj, page_query = paginated(projects, request, 12)
    cards = [decorate_project_card(project) for project in page_obj.object_list]
    return render(request, "coordination/project_list.html", {
        "projects": cards, "page_obj": page_obj, "page_query": page_query, "can_create": can_create,
        "status_filter": status, "status_choices": Project.Status.choices,
    })


@login_required
def project_detail(request, project_id):
    project = get_project(request.user, project_id, "view")
    is_company = can_manage_project(request.user, project)
    is_client = project.client_id is not None and request.user == project.client
    update_form = ProjectUpdateForm(instance=project)
    recommendation_trade = recommended_trade(project.request) if project.request else ""
    assignment_form = AssignmentForm(company=project.company, preferred_trade=recommendation_trade, project=project)
    rating_form = RatingForm()
    complaint_form = ComplaintForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update" and is_company:
            if project.status in [Project.Status.COMPLETED, Project.Status.CANCELLED]:
                raise PermissionDenied
            update_form = ProjectUpdateForm(request.POST, instance=project)
            if update_form.is_valid():
                updated = update_form.save(commit=False)
                completed = updated.status == Project.Status.COMPLETED
                if completed:
                    updated.actual_end_date = date.today()
                    updated.progress_percentage = 100
                with transaction.atomic():
                    if completed and project.request:
                        project.request.status = ServiceRequest.Status.COMPLETED
                        project.request.save(update_fields=["status", "updated_at"])
                    elif updated.status == Project.Status.IN_PROGRESS and project.request:
                        project.request.status = ServiceRequest.Status.IN_PROGRESS
                        project.request.save(update_fields=["status", "updated_at"])
                    updated.save()
                    if completed:
                        active_assignments = project.assignments.filter(status=WorkerAssignment.Status.ACTIVE)
                        worker_ids = list(active_assignments.values_list("worker_id", flat=True))
                        active_assignments.update(status=WorkerAssignment.Status.COMPLETED, removed_at=timezone.now())
                        Worker.objects.filter(pk__in=worker_ids, availability=Worker.Availability.ASSIGNED).update(availability=Worker.Availability.AVAILABLE)
                if completed:
                    notify(project.client, "Project completed", f"{project.company.name} marked {project.title} complete. Please review and confirm.", f"/projects/{project.pk}/")
                else:
                    notify(project.client, "Project updated", f"{project.company.name} updated progress on {project.title}.", f"/projects/{project.pk}/")
                log_event(request, "project_updated", "projects", f"{project.company.name} updated project {project.pk}.", project.pk)
                messages.success(request, "Project update saved.")
                return redirect("project_detail", project_id=project.pk)
        elif action == "assign" and is_company:
            assignment_form = AssignmentForm(request.POST, company=project.company, preferred_trade=recommendation_trade, project=project)
            if project.status in [Project.Status.COMPLETED, Project.Status.CANCELLED]:
                assignment_form.add_error(None, "Workers cannot be assigned to a closed project.")
            elif assignment_form.is_valid():
                worker = assignment_form.cleaned_data["worker"]
                try:
                    with transaction.atomic():
                        worker_locked = Worker.objects.select_for_update().get(pk=worker.pk, company=project.company)
                        if worker_locked.availability != Worker.Availability.AVAILABLE or worker_locked.employment_status != Worker.Employment.ACTIVE:
                            raise ValueError("This worker is no longer available for assignment.")
                        assignment = WorkerAssignment.objects.create(project=project, worker=worker_locked, role=assignment_form.cleaned_data["role"], assigned_by=request.user)
                        worker_locked.availability = Worker.Availability.ASSIGNED
                        worker_locked.save(update_fields=["availability"])
                        log_event(request, "worker_assigned", "projects", f"{worker_locked} assigned to project {project.pk}.", assignment.pk)
                except ValueError as exc:
                    assignment_form.add_error("worker", str(exc))
                except IntegrityError:
                    assignment_form.add_error("worker", "This worker is already assigned to the project.")
                else:
                    notify(project.client, "Project team updated", f"A company worker was assigned to {project.title}.", f"/projects/{project.pk}/")
                    messages.success(request, "Worker assigned.")
                    return redirect("project_detail", project_id=project.pk)
        elif action == "remove" and is_company:
            assignment = get_object_or_404(WorkerAssignment, pk=request.POST.get("assignment_id"), project=project, status=WorkerAssignment.Status.ACTIVE, worker__company=project.company)
            assignment.status = WorkerAssignment.Status.REMOVED
            assignment.removed_at = timezone.now()
            assignment.save(update_fields=["status", "removed_at"])
            assignment.worker.availability = Worker.Availability.AVAILABLE
            assignment.worker.save(update_fields=["availability"])
            log_event(request, "worker_unassigned", "projects", f"{assignment.worker} removed from project {project.pk}.", assignment.pk)
            messages.success(request, "Worker removed from project.")
            return redirect("project_detail", project_id=project.pk)
        elif action == "confirm" and is_client:
            if project.status != Project.Status.COMPLETED:
                raise PermissionDenied
            project.client_confirmed = True
            project.save(update_fields=["client_confirmed", "updated_at"])
            log_event(request, "completion_confirmed", "projects", f"Client confirmed completion of project {project.pk}.", project.pk)
            notify(project.company.owner, "Completion confirmed", f"The client confirmed completion of {project.title}.", f"/projects/{project.pk}/")
            messages.success(request, "Completion confirmed. You can now leave a review.")
            return redirect("project_detail", project_id=project.pk)
        elif action == "rate" and is_client:
            rating_form = RatingForm(request.POST)
            if project.status != Project.Status.COMPLETED or not project.client_confirmed or hasattr(project, "rating"):
                raise PermissionDenied
            if rating_form.is_valid():
                rating = rating_form.save(commit=False)
                rating.project = project
                rating.client = request.user
                rating.company = project.company
                rating.save()
                log_event(request, "company_rated", "ratings", f"Client rated {project.company.name} for project {project.pk}.", rating.pk)
                notify(project.company.owner, "New company review", f"A client reviewed your work on {project.title}.", f"/projects/{project.pk}/")
                messages.success(request, "Thank you for reviewing the company.")
                return redirect("project_detail", project_id=project.pk)
        elif action == "complaint" and (is_client or request.user == project.company.owner):
            complaint_form = ComplaintForm(request.POST)
            if complaint_form.is_valid():
                complaint = complaint_form.save(commit=False)
                complaint.project = project
                complaint.request = project.request
                complaint.submitted_by = request.user
                complaint.save()
                notify_admins("Complaint submitted", f"A complaint was filed about project {project.pk}.", "/admin-portal/complaints/")
                log_event(request, "complaint_submitted", "complaints", f"Complaint opened for project {project.pk}.", complaint.pk)
                messages.success(request, "Your concern was sent to the administrator.")
                return redirect("project_detail", project_id=project.pk)
        else:
            raise PermissionDenied
    return render(request, "coordination/project_detail.html", {
        "active_tab": "overview", "nav_manage": is_company, "nav_supervise": can_supervise_project(request.user, project),
        "project": project, "update_form": update_form, "assignment_form": assignment_form,
        "rating_form": rating_form, "complaint_form": complaint_form, "can_manage": is_company and project.status not in [Project.Status.COMPLETED, Project.Status.CANCELLED],
        "can_confirm": is_client and project.status == Project.Status.COMPLETED and not project.client_confirmed,
        "can_rate": is_client and project.status == Project.Status.COMPLETED and project.client_confirmed and not hasattr(project, "rating"),
    })


@login_required
def notifications(request):
    items = request.user.notifications.all()
    if request.method == "POST":
        request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
        return redirect("notifications")
    return render(request, "coordination/notifications.html", {"items": items})


@role_required(User.Role.CLIENT, User.Role.COMPANY)
def complaint_create(request):
    form = ComplaintForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        complaint = form.save(commit=False)
        complaint.submitted_by = request.user
        complaint.save()
        notify_admins("Complaint submitted", f"A platform complaint was submitted by {request.user}.", "/admin-portal/complaints/")
        log_event(request, "complaint_submitted", "complaints", f"Platform complaint submitted by {request.user}.", complaint.pk)
        messages.success(request, "Your complaint has been sent to the administrator.")
        return redirect("dashboard")
    return render(request, "coordination/form_page.html", {"form": form, "eyebrow": "Platform support", "title": "Submit a complaint", "submit_label": "Send complaint"})


@role_required(User.Role.ADMIN)
def admin_companies(request):
    companies = Company.objects.select_related("owner", "approved_by").all()
    status_filter = request.GET.get("status", "")
    if status_filter:
        companies = companies.filter(status=status_filter)
    page_obj, page_query = paginated(companies, request)
    return render(request, "coordination/admin_companies.html", {"companies": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "status_choices": Company.Status.choices, "filters": request.GET})


@role_required(User.Role.ADMIN)
def admin_company_review(request, company_id):
    company = get_object_or_404(Company.objects.select_related("owner"), pk=company_id)
    form = ReviewCompanyForm(request.POST or None, initial={"status": company.status, "note": company.review_note})
    if request.method == "POST" and form.is_valid():
        company.status = form.cleaned_data["status"]
        company.review_note = form.cleaned_data["note"]
        if company.status == Company.Status.APPROVED:
            company.approved_by = request.user
            company.approved_at = timezone.now()
        else:
            company.approved_by = None
            company.approved_at = None
        company.save()
        notify(company.owner, f"Company application {company.get_status_display().lower()}", company.review_note or f"Your company is now {company.get_status_display().lower()}.", "/dashboard/")
        log_event(request, f"company_{company.status}", "companies", f"Administrator set {company.name} to {company.get_status_display()}.", company.pk)
        messages.success(request, f"{company.name} status updated.")
        return redirect("admin_companies")
    return render(request, "coordination/company_review.html", {"company": company, "form": form})


@role_required(User.Role.ADMIN)
def admin_users(request):
    users = User.objects.all().order_by("-date_joined")
    role = request.GET.get("role", "")
    query = request.GET.get("q", "").strip()
    if role:
        users = users.filter(role=role)
    if query:
        users = users.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query))
    page_obj, page_query = paginated(users, request, 50)
    return render(request, "coordination/admin_users.html", {"users": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "filters": request.GET, "roles": User.Role.choices})


@role_required(User.Role.ADMIN)
@require_POST
def admin_user_toggle(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        messages.error(request, "You cannot deactivate your own administrator account.")
    elif target.role == User.Role.ADMIN and target.is_active and User.objects.filter(role=User.Role.ADMIN, is_active=True).count() <= 1:
        messages.error(request, "At least one active administrator account must remain.")
    else:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        action = "user_activated" if target.is_active else "user_deactivated"
        log_event(request, action, "accounts", f"Administrator {'activated' if target.is_active else 'deactivated'} {target.username}.", target.pk)
        messages.success(request, f"{target.username} account {'activated' if target.is_active else 'deactivated'}.")
    return redirect("admin_users")


@role_required(User.Role.ADMIN)
def admin_complaints(request):
    complaints = Complaint.objects.select_related("submitted_by", "request", "project").all()
    if request.method == "POST":
        complaint = get_object_or_404(Complaint, pk=request.POST.get("complaint_id"))
        status = request.POST.get("status")
        allowed = dict(Complaint.Status.choices)
        if status not in allowed:
            raise PermissionDenied
        complaint.status = status
        complaint.admin_note = request.POST.get("admin_note", "")[:4000]
        complaint.save(update_fields=["status", "admin_note", "updated_at"])
        notify(complaint.submitted_by, "Complaint updated", f"Your complaint is {complaint.get_status_display().lower()}.", "/notifications/")
        log_event(request, "complaint_reviewed", "complaints", f"Administrator updated complaint {complaint.pk}.", complaint.pk)
        messages.success(request, "Complaint updated.")
        return redirect("admin_complaints")
    page_obj, page_query = paginated(complaints, request)
    return render(request, "coordination/admin_complaints.html", {"complaints": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "status_choices": Complaint.Status.choices})


@role_required(User.Role.ADMIN)
def admin_audit(request):
    logs = AuditLog.objects.select_related("user").all()
    module = request.GET.get("module", "")
    if module:
        logs = logs.filter(module=module)
    page_obj, page_query = paginated(logs, request, 50)
    return render(request, "coordination/admin_audit.html", {"logs": page_obj.object_list, "page_obj": page_obj, "page_query": page_query, "modules": AuditLog.objects.order_by().values_list("module", flat=True).distinct(), "selected_module": module})


def report_scope(request):
    """Records the user may report on, plus the date-range and project filters from the query string."""
    if request.user.role == User.Role.ADMIN:
        company = None
        companies = Company.objects.all()
        workers = Worker.objects.all()
        projects = Project.objects.all()
        request_records = ServiceRequest.objects.all()
    elif request.user.role in (User.Role.COMPANY, User.Role.MANAGER):
        company = operating_company(request.user) or get_object_or_404(Company, owner=request.user)
        companies = Company.objects.filter(pk=company.pk)
        workers = company.workers.all()
        projects = company.projects.all()
        request_records = company.service_requests.all()
    else:
        raise PermissionDenied
    today = timezone.localdate()
    try:
        end_date = date.fromisoformat(request.GET.get("end", "")) if request.GET.get("end") else today
        start_date = date.fromisoformat(request.GET.get("start", "")) if request.GET.get("start") else end_date - timedelta(days=29)
    except ValueError:
        start_date, end_date = today - timedelta(days=29), today
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    start_date = max(start_date, end_date - timedelta(days=366))
    selected_project = projects.filter(pk=request.GET.get("project")).first() if request.GET.get("project", "").isdigit() else None
    attendance = Attendance.objects.filter(worker__in=workers, date__range=(start_date, end_date))
    incidents = Incident.objects.filter(project__in=projects, occurred_at__date__range=(start_date, end_date))
    if selected_project:
        attendance = attendance.filter(project=selected_project)
        incidents = incidents.filter(project=selected_project)
    return {
        "company": company, "companies": companies, "workers": workers, "projects": projects, "request_records": request_records,
        "start_date": start_date, "end_date": end_date, "selected_project": selected_project,
        "attendance": attendance, "incidents": incidents,
        "labour_by_project": attendance.filter(status=Attendance.Status.PRESENT).values("project__title")
            .annotate(days=Count("id"), hours=Sum("hours_worked"), cost=Sum(LABOUR_COST)).order_by("-hours"),
        "incident_summary": [
            {"label": dict(Incident.Kind.choices)[row["kind"]], "count": row["count"]}
            for row in incidents.values("kind").annotate(count=Count("id")).order_by("-count")
        ],
    }


@role_required(User.Role.ADMIN, User.Role.COMPANY, User.Role.MANAGER)
def reports(request):
    scope = report_scope(request)
    company, workers, request_records = scope["company"], scope["workers"], scope["request_records"]
    attendance = scope["attendance"]
    present = attendance.filter(status=Attendance.Status.PRESENT)
    return render(request, "coordination/reports.html", {
        **scope,
        "trade_counts": workers.values("primary_trade").annotate(count=Count("id")).order_by("-count"),
        "service_demand": Service.objects.annotate(request_count=Count("requests", filter=Q(requests__in=request_records))).order_by("-request_count")[:10],
        "average_rating": company.average_rating if company else Rating.objects.aggregate(value=Avg("overall_rating"))["value"] or 0,
        "attendance_chart": attendance_between(attendance, scope["start_date"], scope["end_date"]),
        "labour_chart": labour_cost_between(attendance, scope["start_date"], scope["end_date"]),
        "period_hours": present.aggregate(value=Sum("hours_worked"))["value"] or 0,
        "period_cost": present.aggregate(value=Sum(LABOUR_COST))["value"] or 0,
        "period_worker_days": present.count(),
        "export_query": request.GET.urlencode(),
    })


@role_required(User.Role.ADMIN, User.Role.COMPANY, User.Role.MANAGER)
def reports_export(request):
    """The filtered report as a CSV file that opens directly in Excel."""
    import csv

    scope = report_scope(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="buildlink-report-{scope["start_date"]}-to-{scope["end_date"]}.csv"'
    response.write("\ufeff")  # Byte order mark so Excel reads UTF-8 correctly.
    writer = csv.writer(response)

    def safe(value):
        text = str(value)
        return f"'{text}" if text[:1] in "=+-@" else text

    def amount(value):
        return f"{value:.2f}" if value is not None else ""

    writer.writerow(["BuildLink report", f"{scope['start_date']} to {scope['end_date']}", safe(scope["selected_project"].title) if scope["selected_project"] else "All projects"])
    writer.writerow([])
    writer.writerow(["Labour by project", "Worker-days", "Hours", "Labour cost (SLE)"])
    for row in scope["labour_by_project"]:
        writer.writerow([safe(row["project__title"]), row["days"], amount(row["hours"]), amount(row["cost"])])
    writer.writerow([])
    writer.writerow(["Attendance by day", "Present", "Absent / leave / sick", "Hours"])
    daily = scope["attendance"].values("date").annotate(
        present=Count("id", filter=Q(status=Attendance.Status.PRESENT)), absent=Count("id", filter=~Q(status=Attendance.Status.PRESENT)),
        hours=Sum("hours_worked")).order_by("date")
    for row in daily:
        writer.writerow([row["date"], row["present"], row["absent"], amount(row["hours"])])
    writer.writerow([])
    writer.writerow(["Incidents by type", "Reports"])
    for row in scope["incident_summary"]:
        writer.writerow([row["label"], row["count"]])
    log_event(request, "report_exported", "reports", f"{request.user} exported the report for {scope['start_date']} to {scope['end_date']}.")
    return response


@login_required
def private_file(request, kind, record_id):
    if kind == "company-document":
        company = get_object_or_404(Company, pk=record_id)
        if request.user.role != User.Role.ADMIN and request.user != company.owner:
            raise PermissionDenied
        field = company.registration_document
    elif kind == "request-attachment":
        service_request = get_object_or_404(ServiceRequest.objects.select_related("company"), pk=record_id)
        if request.user.role != User.Role.ADMIN and request.user not in [service_request.client, service_request.company.owner]:
            raise PermissionDenied
        field = service_request.attachment
    else:
        raise Http404
    if not field:
        raise Http404
    return FileResponse(field.open("rb"), as_attachment=True, filename=field.name.rsplit("/", 1)[-1])


@login_required
def profile(request):
    if request.user.role == User.Role.COMPANY:
        return redirect("company_profile_edit")
    if request.user.role == User.Role.CLIENT:
        client_profile, _ = ClientProfile.objects.get_or_create(user=request.user, defaults={"telephone": ""})
        if request.method == "POST":
            form = ClientProfileForm(request.POST, instance=client_profile)
            if form.is_valid():
                form.save()
                messages.success(request, "Your profile was updated.")
                return redirect("profile")
        else:
            form = ClientProfileForm(instance=client_profile)
        return render(request, "coordination/form_page.html", {"form": form, "eyebrow": "Account", "title": "Edit client profile", "submit_label": "Save profile"})
    raise PermissionDenied
