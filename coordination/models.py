import os
import uuid

from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.utils import timezone


def validate_upload(file):
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
    extension = os.path.splitext(file.name)[1].lower()
    if extension not in allowed:
        raise ValidationError("Upload a PDF or an image (PNG, JPG, JPEG, or WEBP).")
    if file.size > 5 * 1024 * 1024:
        raise ValidationError("Files must be 5 MB or smaller.")
    file.seek(0)
    header = file.read(12)
    file.seek(0)
    signatures = {
        ".pdf": lambda value: value.startswith(b"%PDF-"),
        ".png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": lambda value: value.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        ".webp": lambda value: value.startswith(b"RIFF") and value[8:12] == b"WEBP",
    }
    if not signatures[extension](header):
        raise ValidationError("The file contents do not match the selected file type.")


def validate_image_upload(file):
    extension = os.path.splitext(file.name)[1].lower()
    if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValidationError("Upload a PNG, JPG, JPEG, or WEBP image.")
    validate_upload(file)


def private_document_path(instance, filename):
    return f"private/company-documents/{instance.pk or uuid.uuid4()}/{os.path.basename(filename)}"


def company_logo_path(instance, filename):
    return f"company-logos/{instance.pk or uuid.uuid4()}/{os.path.basename(filename)}"


def private_request_path(instance, filename):
    return f"private/request-attachments/{instance.pk or uuid.uuid4()}/{os.path.basename(filename)}"


PRIVATE_STORAGE = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


class PortalUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("email_verified", True)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "System administrator"
        COMPANY = "company", "Construction company"
        CLIENT = "client", "Client"
        # Company staff accounts. The company owner (COMPANY) acts as the lead project manager.
        MANAGER = "manager", "Project manager"
        SUPERVISOR = "supervisor", "Site supervisor"
        WORKER = "worker", "Worker"

    STAFF_ROLES = (Role.MANAGER, Role.SUPERVISOR, Role.WORKER)

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.CLIENT, db_index=True)
    employer = models.ForeignKey(
        "Company", null=True, blank=True, on_delete=models.PROTECT, related_name="staff",
        help_text="The company a project manager, supervisor or worker account belongs to.",
    )
    phone = models.CharField(max_length=40, blank=True, help_text="Used for SMS notifications.")
    # Self-registered accounts must confirm their email before signing in. Accounts created by an
    # administrator or a company manager are verified by whoever created them.
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    objects = PortalUserManager()

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def company_scope(self):
        """The company whose records this account works with, or None."""
        if self.role == self.Role.COMPANY:
            return getattr(self, "company", None)
        if self.role in self.STAFF_ROLES:
            return self.employer
        return None


class Service(models.Model):
    name = models.CharField(max_length=90, unique=True)
    description = models.CharField(max_length=240, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Company(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"
        DEACTIVATED = "deactivated", "Deactivated"

    owner = models.OneToOneField(User, on_delete=models.CASCADE, related_name="company")
    name = models.CharField(max_length=160, db_index=True)
    registration_number = models.CharField(max_length=90, unique=True)
    company_type = models.CharField(max_length=100, blank=True)
    established_year = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    services = models.ManyToManyField(Service, blank=True, related_name="companies")
    telephone = models.CharField(max_length=40)
    contact_person = models.CharField(max_length=140)
    contact_position = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=240)
    city = models.CharField(max_length=100, default="Freetown", db_index=True)
    area = models.CharField(max_length=120, blank=True, db_index=True)
    logo = models.FileField(upload_to=company_logo_path, validators=[validate_image_upload], blank=True)
    registration_document = models.FileField(upload_to=private_document_path, storage=PRIVATE_STORAGE, validators=[validate_upload], blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    review_note = models.TextField(blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_companies")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def years_in_operation(self):
        return max(0, timezone.now().year - self.established_year) if self.established_year else None

    @property
    def average_rating(self):
        return self.ratings.aggregate(value=models.Avg("overall_rating"))["value"] or 0

    @property
    def completed_projects_count(self):
        return self.projects.filter(status=Project.Status.COMPLETED, client_confirmed=True).count()


class ClientProfile(models.Model):
    class ClientType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        HOMEOWNER = "homeowner", "Homeowner"
        BUSINESS = "business", "Business"
        DEVELOPER = "developer", "Developer"
        ORGANIZATION = "organization", "Organization"
        CONTRACTOR = "contractor", "Contractor"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="client_profile")
    telephone = models.CharField(max_length=40)
    address = models.CharField(max_length=240, blank=True)
    location = models.CharField(max_length=120, blank=True)
    organization_name = models.CharField(max_length=160, blank=True)
    client_type = models.CharField(max_length=20, choices=ClientType.choices, default=ClientType.INDIVIDUAL)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Skill(models.Model):
    """A named trade skill used for task matching, such as Bricklaying or Wiring."""

    name = models.CharField(max_length=80, unique=True)
    category = models.CharField(max_length=60, blank=True, help_text="Trade group, for example Electrical.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def new_qr_token():
    return uuid.uuid4().hex


class MobileMoneyProvider(models.TextChoices):
    ORANGE_MONEY = "orange_money", "Orange Money"
    AFRIMONEY = "afrimoney", "Afrimoney"


def private_worker_photo_path(instance, filename):
    return f"private/worker-photos/{uuid.uuid4()}/{os.path.basename(filename)}"


class Worker(models.Model):
    class Availability(models.TextChoices):
        AVAILABLE = "available", "Available"
        ASSIGNED = "assigned", "Assigned"
        BUSY = "busy", "Busy"
        LEAVE = "leave", "On leave"
        UNAVAILABLE = "unavailable", "Unavailable"
        INACTIVE = "inactive", "Inactive"

    class Employment(models.TextChoices):
        ACTIVE = "active", "Active"
        FORMER = "former", "Former worker"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="workers")
    worker_id = models.CharField(max_length=50)
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80)
    gender = models.CharField(max_length=30, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    telephone = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=240, blank=True)
    emergency_contact = models.CharField(max_length=160, blank=True)
    primary_trade = models.CharField(max_length=90, db_index=True)
    skills = models.CharField(max_length=500, blank=True, help_text="Separate additional skills with commas.")
    experience_years = models.PositiveSmallIntegerField(default=0)
    qualifications = models.TextField(blank=True)
    certifications = models.TextField(blank=True)
    employment_date = models.DateField(null=True, blank=True)
    employment_status = models.CharField(max_length=12, choices=Employment.choices, default=Employment.ACTIVE, db_index=True)
    availability = models.CharField(max_length=12, choices=Availability.choices, default=Availability.AVAILABLE, db_index=True)
    skill_set = models.ManyToManyField(Skill, blank=True, related_name="workers", verbose_name="skills (for task matching)")
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)], help_text="Pay for a standard 8-hour day, in Leones (SLE).")
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="worker_profile", help_text="Optional sign-in account for this worker.")
    qr_token = models.CharField(max_length=32, unique=True, default=new_qr_token, editable=False, help_text="Secret printed on the worker's QR ID card.")
    mobile_money_provider = models.CharField(max_length=16, choices=MobileMoneyProvider.choices, blank=True)
    mobile_money_number = models.CharField(max_length=20, blank=True)
    photo = models.FileField(upload_to=private_worker_photo_path, storage=PRIVATE_STORAGE, validators=[validate_image_upload], blank=True,
                             help_text="A clear head-and-shoulders photo for the QR ID card. Stored privately.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [models.UniqueConstraint(fields=["company", "worker_id"], name="unique_worker_id_per_company")]
        indexes = [models.Index(fields=["company", "primary_trade", "availability"], name="worker_company_trade_avail")]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.worker_id})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class ServiceRequest(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        DISPUTED = "disputed", "Disputed"

    client = models.ForeignKey(User, on_delete=models.PROTECT, related_name="service_requests")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="service_requests")
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.SET_NULL, related_name="requests")
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=180)
    preferred_start_date = models.DateField(null=True, blank=True)
    expected_duration = models.CharField(max_length=100, blank=True)
    workers_required = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(500)])
    preferred_trade = models.CharField(max_length=90, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    additional_requirements = models.TextField(blank=True)
    attachment = models.FileField(upload_to=private_request_path, storage=PRIVATE_STORAGE, validators=[validate_upload], blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    decline_reason = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="cancelled_requests")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"], name="request_company_status"), models.Index(fields=["client", "status"], name="request_client_status")]

    def __str__(self):
        return self.title


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    # Projects come from an accepted client request, or are created directly by a manager
    # (in which case there is no request and the client is optional).
    request = models.OneToOneField(ServiceRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="project")
    client = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="projects")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="projects")
    budget = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], help_text="Total budget in Leones (SLE).")
    manager = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_projects")
    supervisors = models.ManyToManyField(User, blank=True, related_name="supervised_projects")
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    expected_end_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=180)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED, db_index=True)
    progress_percentage = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    progress_note = models.TextField(blank=True)
    client_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"], name="project_company_status"), models.Index(fields=["client", "status"], name="project_client_status")]

    def __str__(self):
        return self.title


class WorkerAssignment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        REMOVED = "removed", "Removed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="assignments")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="assignments")
    role = models.CharField(max_length=100, blank=True)
    assigned_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="worker_assignments_made")
    assigned_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "worker"], name="unique_worker_per_project")]


class Rating(models.Model):
    project = models.OneToOneField(Project, on_delete=models.PROTECT, related_name="rating")
    client = models.ForeignKey(User, on_delete=models.PROTECT, related_name="ratings_given")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ratings")
    overall_rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    quality_rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    professionalism_rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    punctuality_rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    communication_rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Message(models.Model):
    request = models.ForeignKey(ServiceRequest, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sent_messages")
    body = models.TextField(max_length=4000)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=140)
    message = models.CharField(max_length=280)
    link = models.CharField(max_length=240, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    action = models.CharField(max_length=100, db_index=True)
    module = models.CharField(max_length=80, db_index=True)
    record_id = models.CharField(max_length=80, blank=True)
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class Complaint(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        UNDER_REVIEW = "under_review", "Under review"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints")
    request = models.ForeignKey(ServiceRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints")
    submitted_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="complaints")
    subject = models.CharField(max_length=160)
    details = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    admin_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# Site operations: sites, phases, tasks, attendance, reporting, payroll.
# ---------------------------------------------------------------------------

def private_report_photo_path(instance, filename):
    return f"private/site-report-photos/{uuid.uuid4()}/{os.path.basename(filename)}"


def private_incident_photo_path(instance, filename):
    return f"private/incident-photos/{uuid.uuid4()}/{os.path.basename(filename)}"


class Site(models.Model):
    """A physical work location for a project, with an optional geofence for attendance."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="sites")
    name = models.CharField(max_length=140)
    address = models.CharField(max_length=240, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])
    radius_m = models.PositiveIntegerField(default=150, validators=[MinValueValidator(20), MaxValueValidator(5000)], verbose_name="geofence radius (metres)")
    enforce_geofence = models.BooleanField(default=True, help_text="Only allow clock-in from inside the site boundary.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["project", "name"]

    def __str__(self):
        return f"{self.name} ({self.project.title})"

    @property
    def has_boundary(self):
        return self.latitude is not None and self.longitude is not None


class Phase(models.Model):
    """A stage of a project, such as Foundation. Milestones are phases flagged as checkpoints."""

    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="phases")
    name = models.CharField(max_length=140)
    order = models.PositiveSmallIntegerField(default=1)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_milestone = models.BooleanField(default=False, help_text="A checkpoint, for example Roof complete.")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NOT_STARTED)

    class Meta:
        ordering = ["project", "order", "start_date"]

    def __str__(self):
        return self.name

    @property
    def progress_percentage(self):
        value = self.tasks.aggregate(value=models.Avg("progress_percentage"))["value"]
        if value is None:
            return 100 if self.status == self.Status.COMPLETED else 0
        return round(value)


class Task(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    phase = models.ForeignKey(Phase, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    required_skill = models.ForeignKey(Skill, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    workers_needed = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    progress_percentage = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="tasks_created")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "created_at"]
        indexes = [models.Index(fields=["project", "status"], name="task_project_status")]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status != self.Status.COMPLETED and self.due_date < timezone.localdate())


class TaskAssignment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="assignments")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="task_assignments")
    assigned_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="task_assignments_made")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["assigned_at"]
        constraints = [models.UniqueConstraint(fields=["task", "worker"], name="unique_worker_per_task")]


class Attendance(models.Model):
    class Method(models.TextChoices):
        GPS = "gps", "GPS self check-in"
        QR = "qr", "QR scan by supervisor"
        MANUAL = "manual", "Manual entry by supervisor"

    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "On leave"
        SICK = "sick", "Sick"

    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="attendance")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="attendance")
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="attendance")
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PRESENT)
    clock_in = models.DateTimeField(null=True, blank=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    method = models.CharField(max_length=8, choices=Method.choices, default=Method.GPS)
    note = models.CharField(max_length=240, blank=True, help_text="Reason for absence or manual entry.")
    clock_in_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    clock_in_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    clock_in_distance_m = models.PositiveIntegerField(null=True, blank=True)
    recorded_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="attendance_recorded")
    synced_offline = models.BooleanField(default=False, help_text="Captured without a connection and synced later.")
    client_uuid = models.UUIDField(null=True, blank=True, unique=True, help_text="De-duplicates offline clock-in submissions.")
    clock_out_uuid = models.UUIDField(null=True, blank=True, unique=True, help_text="De-duplicates offline clock-out submissions.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-clock_in"]
        constraints = [models.UniqueConstraint(fields=["worker", "date"], name="one_attendance_per_worker_per_day")]
        indexes = [models.Index(fields=["project", "date"], name="attendance_project_date")]
        verbose_name_plural = "attendance"

    def __str__(self):
        return f"{self.worker} · {self.date}"

    @property
    def is_open(self):
        return self.clock_in is not None and self.clock_out is None


class SiteReport(models.Model):
    """A supervisor's daily report from site."""

    class Weather(models.TextChoices):
        SUNNY = "sunny", "Sunny"
        CLOUDY = "cloudy", "Cloudy"
        RAIN = "rain", "Rain"
        HEAVY_RAIN = "heavy_rain", "Heavy rain"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="site_reports")
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports", help_text="Task whose progress this report updates.")
    task_progress = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(100)])
    date = models.DateField(default=timezone.localdate)
    summary = models.CharField(max_length=200)
    work_completed = models.TextField()
    issues = models.TextField(blank=True, help_text="Delays, shortages or blockers.")
    weather = models.CharField(max_length=12, choices=Weather.choices, blank=True)
    workers_on_site = models.PositiveSmallIntegerField(default=0)
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="site_reports")
    synced_offline = models.BooleanField(default=False)
    client_uuid = models.UUIDField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.project.title} · {self.date}"


class ReportPhoto(models.Model):
    report = models.ForeignKey(SiteReport, on_delete=models.CASCADE, related_name="photos")
    image = models.FileField(upload_to=private_report_photo_path, storage=PRIVATE_STORAGE, validators=[validate_image_upload])
    caption = models.CharField(max_length=160, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Incident(models.Model):
    class Kind(models.TextChoices):
        ACCIDENT = "accident", "Accident / injury"
        NEAR_MISS = "near_miss", "Near miss"
        PPE = "ppe", "PPE non-compliance"
        HAZARD = "hazard", "Unsafe condition"
        PROPERTY = "property", "Property or equipment damage"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        CLOSED = "closed", "Closed"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="incidents")
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="incidents")
    kind = models.CharField(max_length=10, choices=Kind.choices, verbose_name="type")
    severity = models.CharField(max_length=8, choices=Severity.choices, default=Severity.LOW, db_index=True)
    occurred_at = models.DateTimeField()
    description = models.TextField()
    workers_involved = models.ManyToManyField(Worker, blank=True, related_name="incidents")
    ppe_compliant = models.BooleanField(null=True, blank=True, verbose_name="PPE worn correctly", help_text="Leave blank if not applicable.")
    action_taken = models.TextField(blank=True)
    photo = models.FileField(upload_to=private_incident_photo_path, storage=PRIVATE_STORAGE, validators=[validate_image_upload], blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN, db_index=True)
    reported_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="incidents_reported")
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="incidents_closed")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.project.title}"


class MaterialRequest(models.Model):
    class Kind(models.TextChoices):
        MATERIAL = "material", "Material"
        EQUIPMENT = "equipment", "Equipment"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        DELIVERED = "delivered", "Delivered"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="material_requests")
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="material_requests")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.MATERIAL, verbose_name="type")
    item = models.CharField(max_length=160)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    unit = models.CharField(max_length=30, help_text="For example bags, tonnes, pieces or days.")
    needed_by = models.DateField(null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], help_text="In Leones (SLE).")
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="material_requests")
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="material_requests_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.quantity} {self.unit} {self.item}"


class PayrollRecord(models.Model):
    """Wages for one worker over a pay period, computed from attendance hours and daily rate."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payroll_records")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="payroll_records")
    period_start = models.DateField()
    period_end = models.DateField()
    days_present = models.PositiveSmallIntegerField(default=0)
    hours_worked = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, help_text="Rate at the time payroll was generated.")
    gross_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    generated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="payroll_generated")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_end", "worker__last_name"]
        constraints = [models.UniqueConstraint(fields=["worker", "period_start", "period_end"], name="one_payroll_per_worker_period")]

    def __str__(self):
        return f"{self.worker} · {self.period_start} to {self.period_end}"


class MobileMoneyTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESSFUL = "successful", "Successful"
        FAILED = "failed", "Failed"

    payroll = models.ForeignKey(PayrollRecord, on_delete=models.PROTECT, related_name="transactions")
    provider = models.CharField(max_length=16, choices=MobileMoneyProvider.choices)
    msisdn = models.CharField(max_length=20, verbose_name="mobile number")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=40, unique=True)
    provider_reference = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    message = models.CharField(max_length=240, blank=True)
    sandbox = models.BooleanField(default=True)
    initiated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="payments_initiated")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Announcement(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="announcements")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="announcements", help_text="Leave blank to send to the whole company.")
    title = models.CharField(max_length=140)
    body = models.TextField(max_length=1000)
    send_sms = models.BooleanField(default=False, verbose_name="Also send by SMS")
    send_email = models.BooleanField(default=False, verbose_name="Also send by email")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="announcements")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class OutboundMessage(models.Model):
    """Outbox for SMS and email notifications, so every message sent is traceable."""

    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    channel = models.CharField(max_length=6, choices=Channel.choices)
    recipient = models.CharField(max_length=254)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="outbound_messages")
    subject = models.CharField(max_length=160, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.SENT)
    provider = models.CharField(max_length=40, blank=True)
    error = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
