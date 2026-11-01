"""Unit and workflow tests for site operations: geofencing, attendance, matching,
progress, payroll, mobile money, messaging, access control and offline sync."""

import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .messaging import broadcast, normalize_phone
from .models import (
    Announcement,
    Attendance,
    Company,
    MaterialRequest,
    MobileMoneyTransaction,
    OutboundMessage,
    PayrollRecord,
    Project,
    Site,
    SiteReport,
    Skill,
    Task,
    TaskAssignment,
    User,
    Worker,
    WorkerAssignment,
)
from .payments import PaymentError, pay_wages
from .qr import parse_qr_payload, qr_payload
from .services import (
    AttendanceError,
    assign_worker_to_task,
    calculate_gross_pay,
    check_geofence,
    distance_m,
    generate_payroll,
    rank_workers_for_task,
    record_clock,
    set_task_progress,
    shift_hours,
)

SITE_LAT, SITE_LNG = Decimal("8.455900"), Decimal("-13.262800")  # Lumley, Freetown
PASSWORD = "A-strong-ops-pass-937!"


def aware(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class OperationsTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", email="owner@example.test", password=PASSWORD, role=User.Role.COMPANY, first_name="Samuel", last_name="Williams")
        self.company = Company.objects.create(owner=self.owner, name="Salone Builders", registration_number="SB-1", telephone="+232 76 000 001",
                                              contact_person="Samuel Williams", address="42 Kissy Road", status=Company.Status.APPROVED)
        self.manager = User.objects.create_user(username="pm", email="pm@example.test", password=PASSWORD, role=User.Role.MANAGER, employer=self.company)
        self.supervisor = User.objects.create_user(username="sup", email="sup@example.test", password=PASSWORD, role=User.Role.SUPERVISOR, employer=self.company, phone="076 555 102")
        self.client_user = User.objects.create_user(username="client", email="client@example.test", password=PASSWORD, role=User.Role.CLIENT)
        self.blockwork = Skill.objects.create(name="Blockwork")
        self.wiring = Skill.objects.create(name="Wiring")
        self.mason = self.make_worker("W-1", "Mohamed", "Bangura", "Mason", [self.blockwork], years=11, rate="320.00")
        self.electrician = self.make_worker("W-2", "Alusine", "Turay", "Electrician", [self.wiring], years=7, rate="400.00")
        self.worker_user = User.objects.create_user(username="mohamed", email="mohamed@example.test", password=PASSWORD, role=User.Role.WORKER, employer=self.company)
        self.mason.user = self.worker_user
        self.mason.save()
        self.project = Project.objects.create(company=self.company, title="Lumley Block A", location="Lumley", manager=self.manager,
                                              client=self.client_user, status=Project.Status.IN_PROGRESS)
        self.project.supervisors.add(self.supervisor)
        self.site = Site.objects.create(project=self.project, name="Block A", latitude=SITE_LAT, longitude=SITE_LNG, radius_m=150)
        WorkerAssignment.objects.create(project=self.project, worker=self.mason, assigned_by=self.owner)

    def make_worker(self, code, first, last, trade, skills, years=5, rate="300.00", company=None):
        worker = Worker.objects.create(company=company or self.company, worker_id=code, first_name=first, last_name=last,
                                       primary_trade=trade, experience_years=years, daily_rate=Decimal(rate), telephone="+232 76 555 200")
        worker.skill_set.add(*skills)
        return worker


class GeofenceTests(OperationsTestCase):
    def test_distance_between_known_points(self):
        # About 111 m per 0.001 degree of latitude.
        self.assertAlmostEqual(distance_m(8.4559, -13.2628, 8.4569, -13.2628), 111, delta=1)

    def test_inside_outside_and_missing_location(self):
        self.assertTrue(check_geofence(self.site, SITE_LAT, SITE_LNG, 10).allowed)
        outside = check_geofence(self.site, Decimal("8.459000"), SITE_LNG, 10)
        self.assertFalse(outside.allowed)
        self.assertIn("must be within 150 m", outside.message)
        self.assertFalse(check_geofence(self.site, None, None).allowed)
        self.assertFalse(check_geofence(self.site, SITE_LAT, SITE_LNG, 500).allowed)

    def test_unenforced_or_missing_boundary_allows_clock_in(self):
        self.site.enforce_geofence = False
        self.assertTrue(check_geofence(self.site, None, None).allowed)
        self.assertTrue(check_geofence(None, None, None).allowed)


class AttendanceTests(OperationsTestCase):
    def test_clock_in_then_out_records_hours(self):
        day = timezone.localdate()
        start = timezone.now() - timedelta(hours=8, minutes=30)
        result = record_clock(self.mason, self.project, method=Attendance.Method.GPS, recorded_by=self.worker_user,
                              latitude=SITE_LAT, longitude=SITE_LNG, accuracy=12, at=start)
        self.assertEqual(result.action, "clock_in")
        self.assertEqual(result.attendance.site, self.site)
        result = record_clock(self.mason, self.project, method=Attendance.Method.GPS, recorded_by=self.worker_user,
                              latitude=SITE_LAT, longitude=SITE_LNG, accuracy=12)
        self.assertEqual(result.action, "clock_out")
        self.assertEqual(result.attendance.date, day)
        self.assertAlmostEqual(float(result.attendance.hours_worked), 8.5, delta=0.05)
        with self.assertRaisesMessage(AttendanceError, "already clocked out"):
            record_clock(self.mason, self.project, method=Attendance.Method.GPS, recorded_by=self.worker_user, latitude=SITE_LAT, longitude=SITE_LNG)

    def test_clock_in_outside_site_is_rejected(self):
        with self.assertRaisesMessage(AttendanceError, "You must be within"):
            record_clock(self.mason, self.project, method=Attendance.Method.GPS, recorded_by=self.worker_user, latitude=Decimal("8.47"), longitude=SITE_LNG)
        self.assertFalse(Attendance.objects.exists())

    def test_worker_not_on_team_cannot_clock_in(self):
        with self.assertRaisesMessage(AttendanceError, "not on the team"):
            record_clock(self.electrician, self.project, method=Attendance.Method.QR, recorded_by=self.supervisor, latitude=SITE_LAT, longitude=SITE_LNG)

    def test_offline_replay_is_stored_once(self):
        token = uuid.uuid4()
        first = record_clock(self.mason, self.project, method=Attendance.Method.GPS, recorded_by=self.worker_user, latitude=SITE_LAT,
                             longitude=SITE_LNG, at=timezone.now() - timedelta(hours=1), client_uuid=token, offline=True)
        again = record_clock(self.mason, self.project, method=Attendance.Method.GPS, recorded_by=self.worker_user, latitude=SITE_LAT,
                             longitude=SITE_LNG, at=timezone.now() - timedelta(hours=1), client_uuid=token, offline=True)
        self.assertEqual(again.action, "duplicate")
        self.assertEqual(first.attendance.pk, again.attendance.pk)
        self.assertTrue(first.attendance.synced_offline)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_stale_or_future_offline_records_are_rejected(self):
        for at in (timezone.now() - timedelta(days=4), timezone.now() + timedelta(hours=1)):
            with self.assertRaises(AttendanceError):
                record_clock(self.mason, self.project, method=Attendance.Method.GPS, recorded_by=self.worker_user, latitude=SITE_LAT, longitude=SITE_LNG, at=at, offline=True)

    def test_manual_entry_requires_reason_and_skips_geofence(self):
        with self.assertRaisesMessage(AttendanceError, "reason"):
            record_clock(self.mason, self.project, method=Attendance.Method.MANUAL, recorded_by=self.supervisor)
        result = record_clock(self.mason, self.project, method=Attendance.Method.MANUAL, recorded_by=self.supervisor, note="Phone has no GPS")
        self.assertEqual(result.action, "clock_in")

    def test_shift_hours_are_capped(self):
        day = timezone.localdate()
        self.assertEqual(shift_hours(aware(day, 7), aware(day, 15, 30)), Decimal("8.50"))
        self.assertEqual(shift_hours(aware(day, 0), aware(day, 23)), Decimal("16"))


class TaskTests(OperationsTestCase):
    def test_skill_match_ranks_first_and_explains_why(self):
        task = Task.objects.create(project=self.project, title="Wire the ground floor", required_skill=self.wiring)
        matches = rank_workers_for_task(task)
        self.assertEqual(matches[0].worker, self.electrician)
        self.assertIn("Has skill: Wiring", matches[0].reasons)

    def test_busy_workers_from_other_projects_are_not_suggested(self):
        other = Project.objects.create(company=self.company, title="Other", location="Kissy")
        WorkerAssignment.objects.create(project=other, worker=self.electrician, assigned_by=self.owner)
        self.electrician.availability = Worker.Availability.ASSIGNED
        self.electrician.save()
        task = Task.objects.create(project=self.project, title="Wire", required_skill=self.wiring)
        self.assertNotIn(self.electrician, [match.worker for match in rank_workers_for_task(task)])

    def test_assigning_a_free_worker_adds_them_to_the_team(self):
        task = Task.objects.create(project=self.project, title="Wire", required_skill=self.wiring)
        assignment, joined = assign_worker_to_task(task, self.electrician, self.supervisor)
        self.assertTrue(joined)
        self.electrician.refresh_from_db()
        self.assertEqual(self.electrician.availability, Worker.Availability.ASSIGNED)
        self.assertTrue(WorkerAssignment.objects.filter(project=self.project, worker=self.electrician, status="active").exists())

    def test_progress_updates_status_and_rolls_up_to_project(self):
        first = Task.objects.create(project=self.project, title="Walls")
        second = Task.objects.create(project=self.project, title="Slab")
        set_task_progress(first, 100)
        set_task_progress(second, 40)
        first.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(first.status, Task.Status.COMPLETED)
        self.assertIsNotNone(first.completed_at)
        self.assertEqual(self.project.progress_percentage, 70)


class PayrollTests(OperationsTestCase):
    def add_day(self, worker, day, hours):
        Attendance.objects.create(worker=worker, project=self.project, date=day, clock_in=aware(day, 7), clock_out=aware(day, 7) + timedelta(hours=hours),
                                  hours_worked=Decimal(str(hours)), method=Attendance.Method.QR)

    def test_pay_is_hours_over_eight_times_daily_rate(self):
        self.assertEqual(calculate_gross_pay(Decimal("8"), Decimal("320")), Decimal("320.00"))
        self.assertEqual(calculate_gross_pay(Decimal("6"), Decimal("320")), Decimal("240.00"))

    def test_generate_payroll_and_protect_approved_records(self):
        start = timezone.localdate() - timedelta(days=10)
        self.add_day(self.mason, start, 8)
        self.add_day(self.mason, start + timedelta(days=1), 4)
        Attendance.objects.create(worker=self.mason, project=self.project, date=start + timedelta(days=2), status=Attendance.Status.ABSENT)
        records, skipped = generate_payroll(self.company, start, start + timedelta(days=6), self.manager)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record.days_present, record.hours_worked, record.gross_pay), (2, Decimal("12.00"), Decimal("480.00")))
        record.status = PayrollRecord.Status.APPROVED
        record.save()
        self.add_day(self.mason, start + timedelta(days=3), 8)
        records, skipped = generate_payroll(self.company, start, start + timedelta(days=6), self.manager)
        self.assertEqual((records, skipped), ([], 1))
        record.refresh_from_db()
        self.assertEqual(record.gross_pay, Decimal("480.00"))


class PaymentTests(OperationsTestCase):
    def approved_record(self, number="+232 76 555 301"):
        self.mason.mobile_money_provider, self.mason.mobile_money_number = "orange_money", number
        self.mason.save()
        today = timezone.localdate()
        return PayrollRecord.objects.create(company=self.company, worker=self.mason, period_start=today, period_end=today, daily_rate=Decimal("320"),
                                            hours_worked=Decimal("8"), gross_pay=Decimal("320"), net_pay=Decimal("320"), status=PayrollRecord.Status.APPROVED)

    def test_sandbox_payment_marks_record_paid_and_texts_worker(self):
        record = self.approved_record()
        txn = pay_wages(record, self.manager)
        record.refresh_from_db()
        self.assertEqual(txn.status, MobileMoneyTransaction.Status.SUCCESSFUL)
        self.assertTrue(txn.provider_reference.startswith("OM-SBX-"))
        self.assertEqual(record.status, PayrollRecord.Status.PAID)
        self.assertTrue(OutboundMessage.objects.filter(channel="sms", recipient="+23276555301").exists())
        with self.assertRaises(PaymentError):
            pay_wages(record, self.manager)

    def test_failed_payment_leaves_record_unpaid(self):
        record = self.approved_record("+232 76 550 000")
        txn = pay_wages(record, self.manager)
        record.refresh_from_db()
        self.assertEqual(txn.status, MobileMoneyTransaction.Status.FAILED)
        self.assertEqual(record.status, PayrollRecord.Status.APPROVED)

    def test_draft_records_cannot_be_paid(self):
        record = self.approved_record()
        record.status = PayrollRecord.Status.DRAFT
        record.save()
        with self.assertRaisesMessage(PaymentError, "approved"):
            pay_wages(record, self.manager)


class MessagingAndQrTests(OperationsTestCase):
    def test_phone_numbers_normalise_to_sierra_leone_format(self):
        self.assertEqual(normalize_phone("076 123 456"), "+23276123456")
        self.assertEqual(normalize_phone("+232 76 123 456"), "+23276123456")
        self.assertIsNone(normalize_phone("12345"))

    def test_project_announcement_reaches_team_and_sends_sms(self):
        announcement = Announcement.objects.create(company=self.company, project=self.project, title="Concrete pour",
                                                   body="Pour starts at 06:00.", send_sms=True, author=self.manager)
        in_app, sms, _ = broadcast(announcement)
        self.assertTrue(self.worker_user.notifications.filter(title="Concrete pour").exists())
        self.assertTrue(self.supervisor.notifications.filter(title="Concrete pour").exists())
        self.assertFalse(self.manager.notifications.filter(title="Concrete pour").exists())
        self.assertGreaterEqual(in_app, 3)
        self.assertGreaterEqual(sms, 1)

    def test_qr_payload_round_trip(self):
        self.assertEqual(parse_qr_payload(qr_payload(self.mason)), self.mason.qr_token)
        self.assertEqual(parse_qr_payload(f"  {self.mason.qr_token.upper()} "), self.mason.qr_token)
        self.assertIsNone(parse_qr_payload("https://example.com"))


class AccessTests(OperationsTestCase):
    def test_unassigned_supervisor_cannot_open_attendance(self):
        other = User.objects.create_user(username="sup2", email="sup2@example.test", password=PASSWORD, role=User.Role.SUPERVISOR, employer=self.company)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("attendance_board", args=[self.project.pk])).status_code, 403)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse("attendance_board", args=[self.project.pk])).status_code, 200)

    def test_client_is_read_only(self):
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(reverse("project_plan", args=[self.project.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_report_list", args=[self.project.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("task_create", args=[self.project.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("payroll_list")).status_code, 403)

    def test_other_company_manager_cannot_see_project_or_payroll(self):
        rival_owner = User.objects.create_user(username="rival", email="rival@example.test", password=PASSWORD, role=User.Role.COMPANY)
        Company.objects.create(owner=rival_owner, name="Rival", registration_number="R-1", telephone="+232 76 000 009",
                               contact_person="Rival", address="1 Main", status=Company.Status.APPROVED)
        today = timezone.localdate()
        record = PayrollRecord.objects.create(company=self.company, worker=self.mason, period_start=today, period_end=today, daily_rate=Decimal("1"))
        self.client.force_login(rival_owner)
        self.assertEqual(self.client.get(reverse("project_detail", args=[self.project.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("payroll_detail", args=[record.pk])).status_code, 403)

    def test_worker_sees_only_own_approved_payslips(self):
        today = timezone.localdate()
        draft = PayrollRecord.objects.create(company=self.company, worker=self.mason, period_start=today, period_end=today, daily_rate=Decimal("1"))
        theirs = PayrollRecord.objects.create(company=self.company, worker=self.electrician, period_start=today, period_end=today,
                                              daily_rate=Decimal("1"), status=PayrollRecord.Status.APPROVED)
        self.client.force_login(self.worker_user)
        self.assertEqual(self.client.get(reverse("payroll_detail", args=[draft.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("payroll_detail", args=[theirs.pk])).status_code, 403)

    def test_only_supervisors_can_scan_qr_cards(self):
        payload = {"project": self.project.pk, "method": "qr", "code": qr_payload(self.mason), "latitude": SITE_LAT, "longitude": SITE_LNG, "accuracy": 10}
        self.client.force_login(self.worker_user)
        self.assertEqual(self.client.post(reverse("clock_api"), payload).status_code, 403)
        self.client.force_login(self.supervisor)
        response = self.client.post(reverse("clock_api"), payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["action"], "clock_in")

    def test_rejecting_a_material_request_needs_a_reason(self):
        item = MaterialRequest.objects.create(project=self.project, item="Cement", quantity=Decimal("10"), unit="bags", reason="Walls", requested_by=self.supervisor)
        self.client.force_login(self.manager)
        response = self.client.post(reverse("material_review", args=[item.pk]), {"decision": "rejected", "note": ""})
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, MaterialRequest.Status.PENDING)
        self.client.post(reverse("material_review", args=[item.pk]), {"decision": "approved", "note": ""})
        item.refresh_from_db()
        self.assertEqual(item.status, MaterialRequest.Status.APPROVED)
        self.assertTrue(self.supervisor.notifications.filter(title__icontains="approved").exists())


class DemoFlowTests(OperationsTestCase):
    """The end-to-end demo: create project → assign workers → worker clocks in →
    supervisor reports progress → manager views the dashboard."""

    def test_demo_flow(self):
        # 1. The manager creates a project with a supervisor, then adds its site and a task.
        self.client.force_login(self.manager)
        response = self.client.post(reverse("project_create"), {
            "title": "Aberdeen Clinic", "location": "Aberdeen, Freetown", "manager": self.manager.pk,
            "supervisors": [self.supervisor.pk], "budget": "750000.00", "client_email": "client@example.test",
        })
        project = Project.objects.get(title="Aberdeen Clinic")
        self.assertRedirects(response, reverse("project_plan", args=[project.pk]))
        self.assertEqual(project.client, self.client_user)
        self.client.post(reverse("site_create", args=[project.pk]), {"name": "Clinic plot", "latitude": "8.487000", "longitude": "-13.287000", "radius_m": 150, "enforce_geofence": "on"})
        site = project.sites.get()
        self.client.post(reverse("task_create", args=[project.pk]), {"title": "Wire the clinic", "required_skill": self.wiring.pk, "workers_needed": 1, "priority": "high"})
        task = project.tasks.get()

        # 2. The supervisor assigns the best-matched worker.
        self.client.force_login(self.supervisor)
        page = self.client.get(reverse("task_detail", args=[project.pk, task.pk]))
        self.assertEqual(page.context["matches"][0].worker, self.electrician)
        self.client.post(reverse("task_detail", args=[project.pk, task.pk]), {"action": "assign", "worker": self.electrician.pk})
        self.assertTrue(TaskAssignment.objects.filter(task=task, worker=self.electrician).exists())

        # 3. The worker clocks in from inside the site boundary.
        electrician_user = User.objects.create_user(username="alusine", email="alusine@example.test", password=PASSWORD, role=User.Role.WORKER, employer=self.company)
        self.electrician.user = electrician_user
        self.electrician.save()
        self.client.force_login(electrician_user)
        response = self.client.post(reverse("clock_api"), {"project": project.pk, "site": site.pk, "method": "gps", "latitude": "8.487100", "longitude": "-13.287050", "accuracy": "12"})
        self.assertEqual(response.json()["action"], "clock_in", response.content)

        # 4. The supervisor files a daily report that moves the task to 60%.
        self.client.force_login(self.supervisor)
        response = self.client.post(reverse("site_report_create", args=[project.pk]), {
            "date": timezone.localdate().isoformat(), "site": site.pk, "summary": "Conduits in", "work_completed": "Ran conduits on the ground floor.",
            "workers_on_site": 1, "task": task.pk, "task_progress": 60,
        })
        self.assertRedirects(response, reverse("site_report_list", args=[project.pk]))
        task.refresh_from_db()
        project.refresh_from_db()
        self.assertEqual((task.progress_percentage, task.status, project.progress_percentage), (60, Task.Status.IN_PROGRESS, 60))

        # 5. The manager's dashboard shows the worker on site and the project's progress.
        self.client.force_login(self.manager)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context["on_site_count"], 1)
        self.assertContains(dashboard, "Aberdeen Clinic")
        self.assertContains(dashboard, "60%")

    def test_offline_site_report_sync_is_idempotent(self):
        self.client.force_login(self.supervisor)
        token = str(uuid.uuid4())
        data = {"date": timezone.localdate().isoformat(), "summary": "Walls", "work_completed": "Blockwork", "workers_on_site": 4, "client_uuid": token, "offline": "1"}
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        first = self.client.post(reverse("site_report_create", args=[self.project.pk]), data, **headers)
        second = self.client.post(reverse("site_report_create", args=[self.project.pk]), data, **headers)
        self.assertTrue(first.json()["ok"])
        self.assertTrue(second.json()["ok"])
        self.assertEqual(SiteReport.objects.filter(client_uuid=token).count(), 1)
        self.assertTrue(SiteReport.objects.get(client_uuid=token).synced_offline)


class ThemeFeatureTests(OperationsTestCase):
    def make_task(self, status=Task.Status.PENDING, progress=0):
        return Task.objects.create(project=self.project, title="Lay blocks", status=status, progress_percentage=progress, created_by=self.manager)

    def test_supervisor_moves_task_across_board(self):
        task = self.make_task()
        self.client.force_login(self.supervisor)
        url = reverse("task_move", args=[self.project.pk, task.pk])
        response = self.client.post(url, {"status": Task.Status.IN_PROGRESS}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], Task.Status.IN_PROGRESS)
        task.refresh_from_db()
        self.assertEqual(task.progress_percentage, 10)
        self.assertRedirects(self.client.post(url, {"status": Task.Status.COMPLETED}), reverse("task_list", args=[self.project.pk]))
        task.refresh_from_db()
        self.assertEqual((task.status, task.progress_percentage), (Task.Status.COMPLETED, 100))

    def test_only_assigned_workers_and_supervisors_move_tasks(self):
        task = self.make_task()
        url = reverse("task_move", args=[self.project.pk, task.pk])
        self.client.force_login(self.worker_user)
        self.assertEqual(self.client.post(url, {"status": Task.Status.COMPLETED}, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 403)
        TaskAssignment.objects.create(task=task, worker=self.mason, assigned_by=self.supervisor)
        self.assertEqual(self.client.post(url, {"status": Task.Status.COMPLETED}, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 200)
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.post(url, {"status": Task.Status.PENDING}).status_code, 403)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.post(url, {"status": "nonsense"}).status_code, 403)

    def test_board_and_list_views_render(self):
        self.make_task()
        self.client.force_login(self.manager)
        board = self.client.get(reverse("task_list", args=[self.project.pk]))
        self.assertContains(board, "data-kanban")
        self.assertContains(self.client.get(reverse("task_list", args=[self.project.pk]), {"view": "list"}), "Lay blocks")

    def test_project_tabs_respect_roles(self):
        tabs = ("project_workers", "project_materials", "project_incidents")
        self.client.force_login(self.manager)
        for name in tabs:
            self.assertEqual(self.client.get(reverse(name, args=[self.project.pk])).status_code, 200, name)
        self.assertContains(self.client.get(reverse("project_workers", args=[self.project.pk])), "Mohamed Bangura")
        self.client.force_login(self.client_user)
        for name in tabs:
            self.assertEqual(self.client.get(reverse(name, args=[self.project.pk])).status_code, 403, name)

    def test_project_selector_redirects_only_to_visible_projects(self):
        self.client.force_login(self.manager)
        jump = reverse("project_jump")
        self.assertRedirects(self.client.get(jump, {"project": self.project.pk}), reverse("project_detail", args=[self.project.pk]))
        self.assertRedirects(self.client.get(jump, {"project": "abc"}), reverse("project_list"))
        rival_owner = User.objects.create_user(username="rival", email="rival@example.test", password=PASSWORD, role=User.Role.COMPANY)
        self.client.force_login(rival_owner)
        self.assertRedirects(self.client.get(jump, {"project": self.project.pk}), reverse("project_list"), fetch_redirect_response=False)

    def test_report_export_is_excel_friendly_csv(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("reports_export"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.decode("utf-8").startswith("\ufeff"))
        self.client.force_login(self.worker_user)
        self.assertNotEqual(self.client.get(reverse("reports_export")).status_code, 200)

    def test_worker_photos_are_private_to_the_company(self):
        url = reverse("operations_file", args=["worker-photo", self.mason.pk])
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        rival_owner = User.objects.create_user(username="rival", email="rival@example.test", password=PASSWORD, role=User.Role.COMPANY)
        Company.objects.create(owner=rival_owner, name="Rival", registration_number="R-1", telephone="+232 76 000 009",
                               contact_person="Rival", address="1 Main", status=Company.Status.APPROVED)
        self.client.force_login(rival_owner)
        self.assertEqual(self.client.get(url).status_code, 403)
