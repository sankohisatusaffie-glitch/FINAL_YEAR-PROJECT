"""Load realistic demonstration data for a Freetown construction company.

Safe to run repeatedly: records are matched by natural keys and created only if missing.
Nothing existing is changed or deleted. Refuses to run when DEBUG is off unless
--allow-production is given, so demo logins never reach a live database by accident.

    python manage.py seed_demo
"""

import random
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from coordination.models import (
    Announcement,
    Attendance,
    Company,
    Incident,
    MaterialRequest,
    PayrollRecord,
    Phase,
    Project,
    Service,
    Site,
    SiteReport,
    Skill,
    Task,
    TaskAssignment,
    User,
    Worker,
    WorkerAssignment,
)
from coordination.services import generate_payroll, recompute_project_progress, shift_hours

DEMO_PASSWORD = "BuildLink-Demo-2026!"

SKILLS = [
    ("Bricklaying", "Masonry"), ("Blockwork", "Masonry"), ("Plastering", "Masonry"), ("Concrete work", "Masonry"),
    ("Formwork", "Carpentry"), ("Roof framing", "Carpentry"), ("Wiring", "Electrical"), ("Solar installation", "Electrical"),
    ("Pipe fitting", "Plumbing"), ("Tiling", "Finishing"), ("Painting", "Finishing"), ("Steel fixing", "Structural"),
    ("Welding", "Structural"), ("Roofing", "Roofing"), ("Excavator operation", "Plant"), ("General labour", "General"),
]

# (worker id, first, last, trade, skills, years, daily rate SLE, mobile money provider)
WORKERS = [
    ("SB-001", "Mohamed", "Bangura", "Mason", ["Bricklaying", "Blockwork", "Plastering"], 11, "320.00", "orange_money"),
    ("SB-002", "Ibrahim", "Koroma", "Carpenter", ["Formwork", "Roof framing"], 9, "320.00", "afrimoney"),
    ("SB-003", "Alusine", "Turay", "Electrician", ["Wiring", "Solar installation"], 7, "400.00", "orange_money"),
    ("SB-004", "Kadiatu", "Jalloh", "Plumber", ["Pipe fitting"], 6, "350.00", "afrimoney"),
    ("SB-005", "Sorie", "Mansaray", "Steel fixer", ["Steel fixing", "Concrete work"], 8, "300.00", "orange_money"),
    ("SB-006", "Hawa", "Kargbo", "Painter", ["Painting", "Plastering"], 5, "250.00", "orange_money"),
    ("SB-007", "Abdul", "Kanu", "General laborer", ["General labour", "Concrete work"], 3, "180.00", "afrimoney"),
    ("SB-008", "Isatu", "Fofanah", "Tiler", ["Tiling"], 6, "280.00", "orange_money"),
    ("SB-009", "Osman", "Sesay", "Welder", ["Welding", "Steel fixing"], 10, "330.00", "afrimoney"),
    ("SB-010", "Aminata", "Koroma", "General laborer", ["General labour"], 2, "180.00", "orange_money"),
    ("SB-011", "Lansana", "Conteh", "Roofer", ["Roofing", "Roof framing"], 12, "300.00", "orange_money"),
    ("SB-012", "Yusufu", "Bah", "Heavy equipment operator", ["Excavator operation"], 14, "500.00", "afrimoney"),
]


class Command(BaseCommand):
    help = "Add realistic demo data (company, staff, workers, projects, attendance, payroll). Never deletes anything."

    def add_arguments(self, parser):
        parser.add_argument("--allow-production", action="store_true", help="Run even when DEBUG is off.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError("seed_demo creates accounts with a shared demo password. Run it with DEBUG on, or pass --allow-production.")
        self.rng = random.Random(2026)  # Same data every run.
        with transaction.atomic():
            self.seed()
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"Sign in with any of these usernames and the password {DEMO_PASSWORD}:")
        for username, role in [("salone.owner", "company owner / lead project manager"), ("fatmata.pm", "project manager"),
                               ("abubakarr.supervisor", "site supervisor"), ("mohamed.worker", "worker"), ("aminata.client", "client")]:
            self.stdout.write(f"  {username:<22} {role}")

    def user(self, username, first, last, role, **extra):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first, "last_name": last, "role": role, "email": f"{username}@example.sl",
                      "email_verified": True, "email_verified_at": timezone.now(), **extra},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user

    def seed(self):
        owner = self.user("salone.owner", "Samuel", "Williams", User.Role.COMPANY, phone="+232 76 555 100")
        company, _ = Company.objects.get_or_create(owner=owner, defaults={
            "name": "Salone Builders Ltd", "registration_number": "SL-CAC-2014-0458", "company_type": "Building contractor",
            "established_year": 2014, "telephone": "+232 76 555 100", "contact_person": "Samuel Williams",
            "contact_position": "Managing Director", "address": "42 Kissy Road", "city": "Freetown", "area": "Kissy",
            "description": "Residential and commercial building contractor working across the Western Area.",
            "status": Company.Status.APPROVED, "approved_at": timezone.now(),
        })
        company.services.add(*Service.objects.filter(name__in=["General construction", "Masonry", "Carpentry", "Electrical installation", "Plumbing", "Roofing"]))

        manager = self.user("fatmata.pm", "Fatmata", "Sesay", User.Role.MANAGER, employer=company, phone="+232 76 555 101")
        supervisor = self.user("abubakarr.supervisor", "Abu Bakarr", "Kamara", User.Role.SUPERVISOR, employer=company, phone="+232 77 555 102")
        supervisor_2 = self.user("mariama.supervisor", "Mariama", "Conteh", User.Role.SUPERVISOR, employer=company, phone="+232 77 555 103")
        client = self.user("aminata.client", "Aminata", "Kamara", User.Role.CLIENT)
        from coordination.models import ClientProfile
        ClientProfile.objects.get_or_create(user=client, defaults={"telephone": "+232 78 555 104", "location": "Lumley, Freetown", "client_type": "homeowner"})

        skills = {name: Skill.objects.get_or_create(name=name, defaults={"category": category})[0] for name, category in SKILLS}
        workers = {}
        for index, (code, first, last, trade, skill_names, years, rate, provider) in enumerate(WORKERS):
            worker, _ = Worker.objects.get_or_create(company=company, worker_id=code, defaults={
                "first_name": first, "last_name": last, "primary_trade": trade, "experience_years": years,
                "daily_rate": Decimal(rate), "telephone": f"+232 76 555 2{index:02d}", "gender": "",
                "mobile_money_provider": provider, "mobile_money_number": f"+232 76 555 3{index:02d}",
                "emergency_contact": "Family member · +232 76 555 999", "employment_date": timezone.localdate() - timedelta(days=365 * min(years, 6)),
            })
            worker.skill_set.add(*(skills[name] for name in skill_names))
            workers[code] = worker
        for code, username in [("SB-001", "mohamed.worker"), ("SB-003", "alusine.worker")]:
            worker = workers[code]
            account = self.user(username, worker.first_name, worker.last_name, User.Role.WORKER, employer=company, phone=worker.telephone)
            if worker.user_id is None:
                worker.user = account
                worker.save(update_fields=["user"])

        today = timezone.localdate()
        lumley = self.project(company, manager, [supervisor], client, "Lumley Residential Block A", "Lumley Beach Road, Freetown",
                              today - timedelta(days=40), today + timedelta(days=120), "1850000.00",
                              ("Block A site", "Off Lumley Beach Road", "8.455900", "-13.262800", 150))
        wellington = self.project(company, owner, [supervisor_2], None, "Wellington Warehouse Extension", "Wellington Industrial Estate, Freetown",
                                  today - timedelta(days=20), today + timedelta(days=75), "950000.00",
                                  ("Warehouse yard", "Wellington Industrial Estate", "8.426700", "-13.165800", 200))

        self.plan(lumley, supervisor, workers, skills, [
            ("Foundation", Phase.Status.COMPLETED, False, [("Excavate and pour strip footings", "Concrete work", 100, ["SB-005", "SB-007", "SB-012"])]),
            ("Blockwork", Phase.Status.IN_PROGRESS, False, [
                ("Ground-floor block walls", "Blockwork", 70, ["SB-001", "SB-010"]),
                ("First-floor slab formwork", "Formwork", 35, ["SB-002"]),
                ("Electrical conduit in walls", "Wiring", 20, ["SB-003"]),
            ]),
            ("Roofing", Phase.Status.NOT_STARTED, False, [("Roof trusses and zinc sheets", "Roofing", 0, [])]),
            ("Handover", Phase.Status.NOT_STARTED, True, []),
        ])
        self.plan(wellington, supervisor_2, workers, skills, [
            ("Steel frame", Phase.Status.IN_PROGRESS, False, [("Weld portal frames", "Welding", 45, ["SB-009", "SB-011"])]),
            ("Services", Phase.Status.NOT_STARTED, False, [("Install drainage and water supply", "Pipe fitting", 10, ["SB-004"])]),
        ])

        self.attendance(lumley, supervisor, days=38)
        self.attendance(wellington, supervisor_2, days=18)

        SiteReport.objects.get_or_create(project=lumley, date=today - timedelta(days=1), author=supervisor, defaults={
            "site": lumley.sites.first(), "summary": "Ground-floor walls about 70% complete", "weather": SiteReport.Weather.SUNNY,
            "work_completed": "Laid blockwork on the east and north walls. Lintels cast over two window openings.",
            "issues": "Cement delivery arrived two hours late, slowing the morning shift.", "workers_on_site": 6,
        })
        SiteReport.objects.get_or_create(project=wellington, date=today - timedelta(days=1), author=supervisor_2, defaults={
            "site": wellington.sites.first(), "summary": "Two portal frames welded and inspected", "weather": SiteReport.Weather.RAIN,
            "work_completed": "Frames 3 and 4 welded and primed. Bolted to base plates.", "workers_on_site": 3,
            "issues": "Afternoon rain stopped welding for 90 minutes.",
        })

        Incident.objects.get_or_create(project=lumley, kind=Incident.Kind.NEAR_MISS, reported_by=supervisor, defaults={
            "site": lumley.sites.first(), "severity": Incident.Severity.MEDIUM, "occurred_at": timezone.now() - timedelta(days=3, hours=4),
            "description": "A loose block fell from the scaffold edge and landed near a labourer. No one was hurt.",
            "action_taken": "Toe boards fitted along the scaffold. Toolbox talk on stacking blocks held next morning.",
            "status": Incident.Status.CLOSED, "closed_by": manager, "ppe_compliant": True,
        })
        incident, created = Incident.objects.get_or_create(project=wellington, kind=Incident.Kind.PPE, reported_by=supervisor_2, defaults={
            "site": wellington.sites.first(), "severity": Incident.Severity.HIGH, "occurred_at": timezone.now() - timedelta(days=1, hours=2),
            "description": "Welder seen working without a face shield during frame welding.", "ppe_compliant": False,
            "status": Incident.Status.INVESTIGATING,
        })
        if created:
            incident.workers_involved.add(workers["SB-009"])

        for item, quantity, unit, cost, status, project, by in [
            ("Cement (50 kg bags)", "120", "bags", "21600.00", MaterialRequest.Status.PENDING, lumley, supervisor),
            ("12 mm reinforcement bar", "2.5", "tonnes", "47500.00", MaterialRequest.Status.APPROVED, lumley, supervisor),
            ("Welding rods (E6013)", "20", "packets", "3800.00", MaterialRequest.Status.DELIVERED, wellington, supervisor_2),
            ("Mobile scaffold tower", "2", "weeks", "6000.00", MaterialRequest.Status.PENDING, wellington, supervisor_2),
        ]:
            MaterialRequest.objects.get_or_create(project=project, item=item, defaults={
                "quantity": Decimal(quantity), "unit": unit, "estimated_cost": Decimal(cost), "status": status, "requested_by": by,
                "reason": "Needed to keep the current phase on schedule.", "needed_by": today + timedelta(days=5),
                "kind": MaterialRequest.Kind.EQUIPMENT if unit == "weeks" else MaterialRequest.Kind.MATERIAL,
                "reviewed_by": manager if status != MaterialRequest.Status.PENDING else None,
                "reviewed_at": timezone.now() if status != MaterialRequest.Status.PENDING else None,
            })

        Announcement.objects.get_or_create(company=company, title="Safety briefing on Monday", defaults={
            "body": "All site teams: 07:15 toolbox talk on scaffold safety before work starts. Hard hats and boots are compulsory.",
            "author": manager,
        })

        # Last month's payroll, approved and ready to pay.
        last_month_end = today.replace(day=1) - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        if not PayrollRecord.objects.filter(company=company, period_start=last_month_start).exists():
            records, _ = generate_payroll(company, last_month_start, last_month_end, manager)
            PayrollRecord.objects.filter(pk__in=[record.pk for record in records]).update(status=PayrollRecord.Status.APPROVED)

    def project(self, company, manager, supervisors, client, title, location, start, end, budget, site):
        project, created = Project.objects.get_or_create(company=company, title=title, defaults={
            "location": location, "start_date": start, "expected_end_date": end, "budget": Decimal(budget),
            "manager": manager, "client": client, "status": Project.Status.IN_PROGRESS,
            "description": f"{title}: structural works through to handover.",
        })
        if created:
            project.supervisors.add(*supervisors)
            name, address, lat, lng, radius = site
            Site.objects.create(project=project, name=name, address=address, latitude=Decimal(lat), longitude=Decimal(lng), radius_m=radius)
        return project

    def plan(self, project, supervisor, workers, skills, phases):
        today = timezone.localdate()
        for order, (name, status, milestone, tasks) in enumerate(phases, start=1):
            phase, _ = Phase.objects.get_or_create(project=project, name=name, defaults={
                "order": order, "status": status, "is_milestone": milestone,
                "start_date": project.start_date + timedelta(days=(order - 1) * 25), "end_date": project.start_date + timedelta(days=order * 25),
            })
            for title, skill, progress, codes in tasks:
                task, created = Task.objects.get_or_create(project=project, title=title, defaults={
                    "phase": phase, "site": project.sites.first(), "required_skill": skills[skill], "progress_percentage": progress,
                    "status": Task.Status.COMPLETED if progress == 100 else (Task.Status.IN_PROGRESS if progress else Task.Status.PENDING),
                    "priority": Task.Priority.HIGH if progress and progress < 50 else Task.Priority.MEDIUM, "workers_needed": max(len(codes), 1),
                    "due_date": today + timedelta(days=7 * order), "created_by": supervisor,
                    "completed_at": timezone.now() if progress == 100 else None,
                })
                for code in codes:
                    worker = workers[code]
                    WorkerAssignment.objects.get_or_create(project=project, worker=worker, defaults={"assigned_by": supervisor, "role": worker.primary_trade})
                    if worker.availability == Worker.Availability.AVAILABLE:
                        worker.availability = Worker.Availability.ASSIGNED
                        worker.save(update_fields=["availability"])
                    if created:
                        TaskAssignment.objects.get_or_create(task=task, worker=worker, defaults={"assigned_by": supervisor})
        recompute_project_progress(project)

    def attendance(self, project, supervisor, days):
        """Working days (Monday to Saturday) for the project's team, with a few absences."""
        today = timezone.localdate()
        site = project.sites.first()
        team = Worker.objects.filter(assignments__project=project, assignments__status=WorkerAssignment.Status.ACTIVE).distinct()
        for offset in range(days, -1, -1):
            day = today - timedelta(days=offset)
            if day.weekday() == 6:  # Sunday
                continue
            for worker in team:
                if Attendance.objects.filter(worker=worker, date=day).exists():
                    continue
                roll = self.rng.random()
                if roll < 0.06:
                    Attendance.objects.create(worker=worker, project=project, date=day, status=Attendance.Status.SICK, method=Attendance.Method.MANUAL, recorded_by=supervisor, note="Reported sick by phone")
                    continue
                if roll < 0.1:
                    Attendance.objects.create(worker=worker, project=project, date=day, status=Attendance.Status.ABSENT, method=Attendance.Method.MANUAL, recorded_by=supervisor, note="No show")
                    continue
                start = timezone.make_aware(datetime.combine(day, time(7, 15))) + timedelta(minutes=self.rng.randint(0, 40))
                end = timezone.make_aware(datetime.combine(day, time(16, 0))) + timedelta(minutes=self.rng.randint(0, 60))
                if day == today:
                    if timezone.now() < start:
                        continue
                    end = None  # Still on site today.
                # Workers with a sign-in account sometimes check in by GPS; everyone else is scanned.
                method = Attendance.Method.GPS if worker.user_id and self.rng.random() < 0.6 else Attendance.Method.QR
                Attendance.objects.create(
                    worker=worker, project=project, site=site, date=day, clock_in=start, clock_out=end,
                    hours_worked=shift_hours(start, end) if end else Decimal("0"), method=method,
                    recorded_by=supervisor if method == Attendance.Method.QR else worker.user,
                    clock_in_latitude=site.latitude, clock_in_longitude=site.longitude, clock_in_distance_m=self.rng.randint(5, 90),
                )
