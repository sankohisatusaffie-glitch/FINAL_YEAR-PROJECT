import re
from datetime import timedelta
from urllib.parse import urlparse

from django.core import mail
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from .forms import ClientRegistrationForm, ProjectUpdateForm, ReviewCompanyForm, ServiceRequestForm, WorkerForm
from .models import (
    ClientProfile,
    Company,
    Notification,
    Project,
    Rating,
    Service,
    ServiceRequest,
    User,
    Worker,
    WorkerAssignment,
)


class CoordinationWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="platform-admin", email="admin@example.test", password="A-strong-admin-pass-937!",
        )
        self.company_user = User.objects.create_user(
            username="builder-one", email="builder@example.test", password="A-strong-company-pass-937!",
            first_name="Amadu", last_name="Koroma", role=User.Role.COMPANY,
        )
        self.client_user = User.objects.create_user(
            username="client-one", email="client@example.test", password="A-strong-client-pass-937!",
            first_name="Mariama", last_name="Kamara", role=User.Role.CLIENT,
        )
        ClientProfile.objects.create(user=self.client_user, telephone="+232 76 000 000")
        self.service, _ = Service.objects.get_or_create(name="Electrical installation")
        self.company = self.make_company(self.company_user, "BuildLink Electric", "REG-001", Company.Status.APPROVED)
        self.company.services.add(self.service)
        self.worker = Worker.objects.create(
            company=self.company, worker_id="W-001", first_name="Ibrahim", last_name="Sesay",
            primary_trade="Electrician", experience_years=7,
        )

    def make_company(self, owner, name, registration_number, status):
        return Company.objects.create(
            owner=owner,
            name=name,
            registration_number=registration_number,
            telephone="+232 76 000 001",
            contact_person=owner.get_full_name() or owner.username,
            address="12 Wilkinson Road",
            city="Freetown",
            area="Lumley",
            status=status,
        )

    def make_request(self, status=ServiceRequest.Status.ACCEPTED):
        return ServiceRequest.objects.create(
            client=self.client_user,
            company=self.company,
            service=self.service,
            title="House electrical installation",
            description="Wire a three-bedroom home.",
            location="Lumley, Freetown",
            workers_required=1,
            preferred_trade="Electrician",
            status=status,
        )

    def test_company_registration_starts_pending_and_notifies_admin(self):
        response = self.client.post(reverse("register_company"), {
            "username": "new-builder",
            "email": "new-builder@example.test",
            "first_name": "Sorie",
            "last_name": "Bangura",
            "password": "Masonry-work!47829",
            "password_confirm": "Masonry-work!47829",
            "name": "New Freetown Builders",
            "registration_number": "REG-NEW-001",
            "telephone": "+232 76 000 010",
            "contact_person": "Sorie Bangura",
            "address": "8 Kissy Road",
            "city": "Freetown",
            "area": "Kissy",
        })
        self.assertRedirects(response, reverse("verification_sent"))
        company = Company.objects.get(registration_number="REG-NEW-001")
        self.assertEqual(company.status, Company.Status.PENDING)
        owner = company.owner
        self.assertFalse(owner.email_verified)
        # Administrators only hear about the application once the email address is confirmed.
        self.assertFalse(Notification.objects.filter(user=self.admin, title="Company application received").exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new-builder@example.test"])

        # Signing in before confirming is refused, with a clear reason.
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": "new-builder", "password": "Masonry-work!47829"})
        self.assertContains(response, "Confirm your email address before signing in")
        self.assertNotIn("_auth_user_id", self.client.session)

        link = urlparse(re.search(r"https?://\S+/verify-email/\S+/", mail.outbox[0].body).group(0)).path
        response = self.client.get(link)
        self.assertRedirects(response, reverse("dashboard"))
        owner.refresh_from_db()
        self.assertTrue(owner.email_verified)
        self.assertEqual(int(self.client.session["_auth_user_id"]), owner.pk)
        self.assertTrue(Notification.objects.filter(user=self.admin, title="Company application received").exists())

        # The link works once.
        self.client.logout()
        response = self.client.get(link)
        self.assertRedirects(response, reverse("login"))

    def test_admin_approval_activates_company_and_records_approver(self):
        pending_owner = User.objects.create_user(
            username="pending-builder", email="pending@example.test", password="A-strong-pending-pass-937!",
            role=User.Role.COMPANY,
        )
        pending = self.make_company(pending_owner, "Pending Works", "REG-PENDING", Company.Status.PENDING)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin_company_review", args=[pending.pk]), {
            "status": Company.Status.APPROVED,
            "note": "Registration details reviewed.",
        })
        self.assertEqual(response.status_code, 302)
        pending.refresh_from_db()
        self.assertEqual(pending.status, Company.Status.APPROVED)
        self.assertEqual(pending.approved_by, self.admin)
        self.assertIsNotNone(pending.approved_at)
        self.assertTrue(Notification.objects.filter(user=pending_owner, title__icontains="approved").exists())

    def test_directory_displays_approved_companies_only(self):
        pending_owner = User.objects.create_user(
            username="pending-builder", email="pending@example.test", password="A-strong-pending-pass-937!",
            role=User.Role.COMPANY,
        )
        self.make_company(pending_owner, "Hidden Pending Works", "REG-PENDING", Company.Status.PENDING)
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("company_directory"))
        self.assertEqual(response.status_code, 200)
        company_names = [company.name for company in response.context["companies"]]
        self.assertIn(self.company.name, company_names)
        self.assertNotIn("Hidden Pending Works", company_names)

    def test_back_button_is_hidden_on_home_and_dashboard_but_available_elsewhere(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-history-back')

        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-history-back', count=1)

        self.client.force_login(self.client_user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-history-back')

        response = self.client.get(reverse("company_directory"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-history-back', count=1)

    def test_client_construction_request_uses_simplified_fields(self):
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("service_request_create", args=[self.company.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(list(form.fields), [
            "service", "attachment", "location", "preferred_start_date",
            "expected_duration", "preferred_trade",
        ])
        self.assertNotIn("title", form.fields)
        self.assertNotIn("description", form.fields)
        self.assertNotIn("workers_required", form.fields)
        self.assertNotIn("budget", form.fields)
        self.assertNotIn("additional_requirements", form.fields)
        self.assertEqual(form.fields["preferred_trade"].widget.input_type, "select")
        trade_values = [value for value, _label in form.fields["preferred_trade"].choices]
        self.assertIn("Electrician", trade_values)
        self.assertIn("Carpenter", trade_values)
        self.assertIn("Heavy equipment operator", trade_values)

        response = self.client.post(reverse("service_request_create", args=[self.company.pk]), {
            "service": self.service.pk,
            "location": "Lumley, Freetown",
            "preferred_start_date": (timezone.localdate() + timedelta(days=30)).isoformat(),
            "expected_duration": "6 weeks",
            "preferred_trade": "Mason",
        })
        self.assertEqual(response.status_code, 302)
        service_request = ServiceRequest.objects.get(client=self.client_user, company=self.company)
        self.assertEqual(service_request.title, "Electrical installation in Lumley, Freetown")
        self.assertEqual(service_request.description, "")
        self.assertIsNone(service_request.workers_required)
        detail_response = self.client.get(reverse("service_request_detail", args=[service_request.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Team size")
        self.assertContains(detail_response, "To be agreed")

    def test_company_cannot_open_another_company_worker_record(self):
        other_user = User.objects.create_user(
            username="builder-two", email="builder-two@example.test", password="A-strong-other-pass-937!",
            role=User.Role.COMPANY,
        )
        other_company = self.make_company(other_user, "Other Construction", "REG-002", Company.Status.APPROVED)
        other_worker = Worker.objects.create(
            company=other_company, worker_id="W-002", first_name="Abdul", last_name="Conteh",
            primary_trade="Mason",
        )
        self.client.force_login(self.company_user)
        response = self.client.get(reverse("worker_edit", args=[other_worker.pk]))
        self.assertEqual(response.status_code, 404)

    def test_unapproved_company_cannot_use_workforce_tools(self):
        owner = User.objects.create_user(
            username="not-approved", email="not-approved@example.test", password="A-strong-unapproved-pass-937!",
            role=User.Role.COMPANY,
        )
        self.make_company(owner, "Not Approved Works", "REG-003", Company.Status.PENDING)
        self.client.force_login(owner)
        response = self.client.get(reverse("worker_create"))
        self.assertEqual(response.status_code, 403)

    def test_accepting_request_creates_project_and_notifies_client(self):
        service_request = self.make_request(ServiceRequest.Status.SUBMITTED)
        service_request.description = ""
        service_request.workers_required = None
        service_request.save(update_fields=["description", "workers_required"])
        self.client.force_login(self.company_user)
        response = self.client.post(reverse("service_request_detail", args=[service_request.pk]), {
            "action": "review",
            "status": ServiceRequest.Status.ACCEPTED,
        })
        self.assertEqual(response.status_code, 302)
        service_request.refresh_from_db()
        self.assertEqual(service_request.status, ServiceRequest.Status.ACCEPTED)
        self.assertEqual(Project.objects.filter(request=service_request).count(), 1)
        project = Project.objects.get(request=service_request)
        project_response = self.client.get(reverse("project_detail", args=[project.pk]))
        self.assertEqual(project_response.status_code, 200)
        self.assertContains(project_response, "Electrical installation")
        self.assertContains(project_response, "To be agreed")
        self.assertTrue(Notification.objects.filter(user=self.client_user, title="Request accepted").exists())

    def test_company_assigns_only_its_available_worker_and_cancellation_releases_them(self):
        service_request = self.make_request()
        project = Project.objects.create(
            request=service_request,
            client=self.client_user,
            company=self.company,
            title=service_request.title,
            description=service_request.description,
            location=service_request.location,
        )
        self.client.force_login(self.company_user)
        response = self.client.post(reverse("project_detail", args=[project.pk]), {
            "action": "assign", "worker": self.worker.pk, "role": "Electrician",
        })
        self.assertEqual(response.status_code, 302)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.availability, Worker.Availability.ASSIGNED)
        self.assertTrue(WorkerAssignment.objects.filter(project=project, worker=self.worker, status=WorkerAssignment.Status.ACTIVE).exists())

        self.client.force_login(self.client_user)
        response = self.client.post(reverse("service_request_detail", args=[service_request.pk]), {
            "action": "cancel", "reason": "The site is no longer available.",
        })
        self.assertEqual(response.status_code, 302)
        service_request.refresh_from_db()
        project.refresh_from_db()
        self.worker.refresh_from_db()
        assignment = WorkerAssignment.objects.get(project=project, worker=self.worker)
        self.assertEqual(service_request.status, ServiceRequest.Status.CANCELLED)
        self.assertEqual(project.status, Project.Status.CANCELLED)
        self.assertEqual(assignment.status, WorkerAssignment.Status.REMOVED)
        self.assertEqual(self.worker.availability, Worker.Availability.AVAILABLE)
        self.assertEqual(service_request.cancelled_by, self.client_user)
        self.assertIsNotNone(service_request.cancelled_at)

    def test_client_can_review_only_after_company_completion_and_confirmation(self):
        service_request = self.make_request(ServiceRequest.Status.COMPLETED)
        project = Project.objects.create(
            request=service_request,
            client=self.client_user,
            company=self.company,
            title=service_request.title,
            description=service_request.description,
            location=service_request.location,
            status=Project.Status.COMPLETED,
            progress_percentage=100,
        )
        self.client.force_login(self.client_user)
        response = self.client.post(reverse("project_detail", args=[project.pk]), {
            "action": "rate", "overall_rating": 5, "quality_rating": 5,
            "professionalism_rating": 5, "punctuality_rating": 4,
            "communication_rating": 5, "comment": "Clear communication and excellent work.",
        })
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("project_detail", args=[project.pk]), {"action": "confirm"})
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db()
        self.assertTrue(project.client_confirmed)
        response = self.client.post(reverse("project_detail", args=[project.pk]), {
            "action": "rate", "overall_rating": 5, "quality_rating": 5,
            "professionalism_rating": 5, "punctuality_rating": 4,
            "communication_rating": 5, "comment": "Clear communication and excellent work.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Rating.objects.filter(project=project, client=self.client_user, company=self.company).count(), 1)


class FormValidationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="validator-co", email="validator@example.test", password="A-strong-pass-937!", role=User.Role.COMPANY)
        self.company = Company.objects.create(
            owner=self.owner, name="Validator Builders", registration_number="REG-VAL", telephone="+232 76 000 002",
            contact_person="Fatmata Turay", address="3 Siaka Stevens Street", status=Company.Status.APPROVED,
        )
        Worker.objects.create(company=self.company, worker_id="W-100", first_name="Alusine", last_name="Kanu", primary_trade="Mason")

    def client_registration(self, **overrides):
        data = {
            "username": "new-client", "email": "new-client@example.test", "first_name": "Aminata", "last_name": "Jalloh",
            "password": "Roofing-plans!48213", "password_confirm": "Roofing-plans!48213",
            "telephone": "+232 76 555 123", "client_type": ClientProfile.ClientType.INDIVIDUAL,
        }
        data.update(overrides)
        return ClientRegistrationForm(data)

    def test_registration_accepts_valid_details(self):
        form = self.client_registration()
        self.assertTrue(form.is_valid(), form.errors)

    def test_registration_rejects_bad_phone_names_and_mismatched_passwords(self):
        form = self.client_registration(telephone="call me", first_name="R2D2", password_confirm="Something-else!1")
        self.assertFalse(form.is_valid())
        self.assertIn("telephone", form.errors)
        self.assertIn("first_name", form.errors)
        self.assertIn("password_confirm", form.errors)
        self.assertFalse(self.client_registration(telephone="12345").is_valid())

    def test_registration_rejects_whitespace_only_required_fields(self):
        form = self.client_registration(first_name="   ")
        self.assertFalse(form.is_valid())
        self.assertIn("first_name", form.errors)

    def test_worker_form_rejects_duplicate_id_underage_and_future_dates(self):
        today = timezone.localdate()
        form = WorkerForm({
            "worker_id": "w-100", "first_name": "Musa", "last_name": "Bah", "primary_trade": "Carpenter",
            "experience_years": 3, "employment_status": Worker.Employment.ACTIVE, "availability": Worker.Availability.AVAILABLE,
            "date_of_birth": (today - timedelta(days=365 * 10)).isoformat(),
            "employment_date": (today + timedelta(days=5)).isoformat(),
        }, company=self.company)
        self.assertFalse(form.is_valid())
        self.assertIn("worker_id", form.errors)
        self.assertIn("date_of_birth", form.errors)
        self.assertIn("employment_date", form.errors)

    def test_service_request_start_date_cannot_be_in_the_past(self):
        service, _ = Service.objects.get_or_create(name="Masonry")
        form = ServiceRequestForm({
            "service": service.pk, "location": "Lumley",
            "preferred_start_date": (timezone.localdate() - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertIn("preferred_start_date", form.errors)

    def test_project_end_date_cannot_precede_start_date(self):
        form = ProjectUpdateForm({
            "status": Project.Status.IN_PROGRESS, "progress_percentage": 40,
            "start_date": "2026-05-10", "expected_end_date": "2026-05-01",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("expected_end_date", form.errors)

    def test_rejecting_a_company_requires_a_note(self):
        self.assertFalse(ReviewCompanyForm({"status": Company.Status.REJECTED, "note": ""}).is_valid())
        self.assertTrue(ReviewCompanyForm({"status": Company.Status.APPROVED, "note": ""}).is_valid())

    def test_decline_reason_is_not_browser_required_so_accept_can_submit(self):
        service_request = ServiceRequest.objects.create(
            client=User.objects.create_user(username="c2", email="c2@example.test", password="A-strong-pass-937!"),
            company=self.company, service=Service.objects.get_or_create(name="Masonry")[0], title="Wall repair", location="Kissy",
        )
        self.client.force_login(self.owner)
        response = self.client.get(reverse("service_request_detail", args=[service_request.pk]))
        self.assertContains(response, 'data-require-field="reason"')
        self.assertNotContains(response, 'name="reason" cols="40" rows="3" class="field-control" maxlength="1000" minlength="10" required')
        response = self.client.post(reverse("service_request_detail", args=[service_request.pk]), {"action": "review", "status": ServiceRequest.Status.DECLINED, "reason": "short"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value has at least 10 characters")
        service_request.refresh_from_db()
        self.assertEqual(service_request.status, ServiceRequest.Status.SUBMITTED)

    def test_form_pages_render_shared_field_markup(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("worker_create"), {"worker_id": "W-100", "first_name": "", "last_name": "Bah"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-validate')
        self.assertContains(response, 'class="form-alert"')
        self.assertContains(response, 'id="id_worker_id_error"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, "Another worker in your company already uses this ID.")

    def test_request_and_project_pages_render_when_service_was_removed(self):
        client_user = User.objects.create_user(username="c3", email="c3@example.test", password="A-strong-pass-937!")
        service_request = ServiceRequest.objects.create(client=client_user, company=self.company, service=None, title="Roof repair", location="Kissy")
        project = Project.objects.create(request=service_request, client=client_user, company=self.company, title="Roof repair", location="Kissy")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("service_request_detail", args=[service_request.pk]))
        self.assertContains(response, "General construction")
        response = self.client.get(reverse("project_detail", args=[project.pk]))
        self.assertContains(response, "General construction")

    def test_form_sections_include_every_field(self):
        from .templatetags.coordination_tags import form_sections
        for form in [WorkerForm(company=self.company), ServiceRequestForm(), ClientRegistrationForm()]:
            rendered = [field.name for section in form_sections(form) for field in section["fields"]]
            self.assertCountEqual(rendered, list(form.fields))

    def test_primary_trade_is_a_dropdown_that_keeps_legacy_values(self):
        form = WorkerForm(company=self.company)
        self.assertEqual(form.fields["primary_trade"].widget.input_type, "select")
        base = {"worker_id": "W-200", "first_name": "Musa", "last_name": "Bah", "experience_years": 3,
                "employment_status": Worker.Employment.ACTIVE, "availability": Worker.Availability.AVAILABLE, "daily_rate": "250.00"}
        self.assertFalse(WorkerForm({**base, "primary_trade": "Astronaut"}, company=self.company).is_valid())
        self.assertTrue(WorkerForm({**base, "primary_trade": "Carpenter"}, company=self.company).is_valid())
        legacy = Worker.objects.create(company=self.company, worker_id="W-201", first_name="Brima", last_name="Conteh", primary_trade="masonary")
        edit = WorkerForm({**base, "worker_id": "W-201", "primary_trade": "masonary"}, instance=legacy)
        self.assertIn(("masonary", "masonary (current, not in list)"), edit.fields["primary_trade"].choices)
        self.assertTrue(edit.is_valid(), edit.errors)


class AuthenticDataTests(TestCase):
    """Field rules that keep account and worker information genuine."""

    def setUp(self):
        self.owner = User.objects.create_user(username="real-co", email="real@example.test", password="A-strong-pass-937!", role=User.Role.COMPANY)
        self.company = Company.objects.create(owner=self.owner, name="Real Builders", registration_number="REG-REAL", telephone="+232 76 111 222",
                                              contact_person="Fatmata Turay", address="3 Siaka Stevens Street", status=Company.Status.APPROVED)

    def client_data(self, **overrides):
        data = {"username": "aminata", "email": "aminata@gmail.com", "first_name": "aminata", "last_name": "JALLOH",
                "password": "Roofing-plans!48213", "password_confirm": "Roofing-plans!48213",
                "telephone": "076555123", "client_type": ClientProfile.ClientType.INDIVIDUAL,
                "address": "14  wilkinson   road", "location": "lumley"}
        data.update(overrides)
        return ClientRegistrationForm(data)

    def test_valid_details_are_tidied_into_one_format(self):
        form = self.client_data()
        self.assertTrue(form.is_valid(), form.errors)
        data = form.cleaned_data
        self.assertEqual((data["first_name"], data["last_name"]), ("Aminata", "Jalloh"))
        self.assertEqual(data["telephone"], "+232 76 555 123")
        self.assertEqual((data["address"], data["location"]), ("14 Wilkinson Road", "Lumley"))

    def test_phone_numbers_must_be_real_sierra_leone_numbers(self):
        for number in ["12345", "+44 20 7946 0958", "077777777", "012345678", "065 123 456"]:
            self.assertIn("telephone", self.client_data(telephone=number).errors, number)
        for number in ["+232 76 123 456", "23276123456", "076-123-456", "+232 22 223 344"]:
            self.assertTrue(self.client_data(telephone=number).is_valid(), number)

    def test_duplicate_phone_and_throwaway_email_are_refused(self):
        ClientProfile.objects.create(user=User.objects.create_user(username="first", email="first@example.test"), telephone="+232 76 555 123")
        self.assertIn("telephone", self.client_data().errors)
        self.assertIn("email", self.client_data(email="someone@mailinator.com", telephone="076 999 888").errors)

    def test_junk_names_and_addresses_are_refused(self):
        form = self.client_data(first_name="Aaaaron", address="12", telephone="076 999 888")
        self.assertIn("first_name", form.errors)
        self.assertIn("address", form.errors)

    def test_worker_details_must_be_consistent(self):
        born = timezone.localdate() - timedelta(days=365 * 25)
        data = {"worker_id": "w 7", "first_name": "musa", "last_name": "bah", "primary_trade": "Mason", "experience_years": 20,
                "employment_status": Worker.Employment.ACTIVE, "availability": Worker.Availability.AVAILABLE, "daily_rate": "250.00",
                "date_of_birth": born.isoformat(), "telephone": "076 222 333", "emergency_contact": "Fatu Bah (sister)",
                "mobile_money_number": "022 223 344", "gender": "Male"}
        form = WorkerForm(data, company=self.company)
        self.assertFalse(form.is_valid())
        self.assertIn("experience_years", form.errors)       # 20 years' experience at 25 is not possible.
        self.assertIn("emergency_contact", form.errors)      # No phone number in the contact.
        self.assertIn("mobile_money_number", form.errors)    # A landline cannot hold a mobile money wallet.
        form = WorkerForm({**data, "experience_years": 6, "emergency_contact": "Fatu Bah (sister) 076 444 555", "mobile_money_number": "076 222 333"}, company=self.company)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["worker_id"], form.cleaned_data["first_name"]), ("W 7", "Musa"))
        same = WorkerForm({**data, "experience_years": 6, "emergency_contact": "Self 076 222 333", "mobile_money_number": "076 222 333"}, company=self.company)
        self.assertIn("emergency_contact", same.errors)      # The emergency contact cannot be the worker's own number.

    def test_staff_accounts_created_by_managers_are_already_verified(self):
        worker = Worker.objects.create(company=self.company, worker_id="W-9", first_name="Abu", last_name="Kamara", primary_trade="Mason")
        self.client.force_login(self.owner)
        self.client.post(reverse("staff_create"), {
            "role": User.Role.WORKER, "worker": worker.pk, "username": "abu.kamara", "email": "abu@gmail.com",
            "first_name": "Abu", "last_name": "Kamara", "phone": "077 123 789",
            "password": "Site-work-pass!8821", "password_confirm": "Site-work-pass!8821",
        })
        account = User.objects.get(username="abu.kamara")
        self.assertTrue(account.email_verified)
        self.assertEqual(account.phone, "+232 77 123 789")
