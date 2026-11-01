# BuildLink — Workforce Construction Coordination System

BuildLink is a Django web application for coordinating construction companies, their workforce and their clients in Freetown, Sierra Leone. Clients request work from verified companies; companies run the work on site with projects, tasks, attendance, daily reports, safety records, material requests and payroll.

## What is included

**Release 1: company and client coordination**
- Separate company and client registration with **email confirmation**, secure password storage and role-based portal access.
- Authentic data rules on every form: Sierra Leone phone numbers (stored as `+232 76 123 456`), no duplicate phones, no throwaway email domains, tidied names and addresses, and consistency checks such as experience against age.
- Company applications start pending; administrators approve, reject, request corrections, suspend or reactivate them. Public profiles and the directory show approved companies only.
- Client service requests with private supporting files, messages, cancellation reasons and notifications. Accepted requests create projects; clients confirm completion and leave one review.
- Administrator dashboard, account monitoring, complaints, reports and an audit trail.

**Release 2: site operations**
- **Roles:** administrator, company owner (lead project manager), project manager, site supervisor, worker (mobile-friendly) and client (read-only progress).
- **Workers:** skills, certifications, contact details, daily rate in Leones, availability, mobile money wallet and a printable **QR ID card**.
- **Projects and sites:** created directly or from a client request, with GPS site boundaries, phases, milestones, dates and a budget.
- **Tasks:** priorities, deadlines and Pending → In progress → Completed. **Skill-based matching** suggests the best available workers and explains why.
- **Attendance:** **GPS geofenced** self clock-in, **QR check-in** scanned by the supervisor, manual entries with a reason, absences, and automatic hours.
- **Daily site reports** with photos; task progress rolls up to phases and the project.
- **Safety incidents** (accidents, near misses, PPE breaches) and **material and equipment requests** with manager approval.
- **Payroll** from attendance hours × daily rate, approval, printable payslips (PDF) and CSV export. Wages can be paid through **Orange Money or Afrimoney** (sandbox gateway).
- **Announcements and alerts** in the app, by **SMS** (sandbox backend) and by **email**.
- **Dashboard and reports:** workforce on site now, attendance trends, labour cost and project progress.
- **Offline mode:** clock-ins, QR scans and daily reports work without signal and sync automatically.
- **Audit log** of who changed what, and when, across every module.

**Release 3: construction interface**
- **Brand and theme:** hard-hat logo (sidebar, login, favicon, payslips, reports), a safety-yellow, orange and charcoal palette, Barlow Condensed headings with Inter body text, hazard-stripe accents and a construction icon set with trade badges.
- **Layout:** charcoal sidebar with a yellow active item, a top header with page title, breadcrumbs, project selector, sync status, notifications and the signed-in role, and a bottom navigation bar on phones.
- **Manager dashboard** with KPI cards, a **site map** (Leaflet and OpenStreetMap), who is on site, charts, project progress and an activity feed.
- **Projects** as photo cards with status filters; project tabs for Overview, Phases (with a **Gantt timeline**), Tasks, Workers, Attendance, Reports, Materials and Incidents.
- **Kanban task board** with drag and drop (and a keyboard-friendly "Move to" control), plus a list view.
- **Workforce** as a table or as cards, with worker photos on the QR ID card; attendance shows GPS verified, QR scanned or Manual entry.
- **Field views** for supervisors (large action buttons, sync status) and workers (clock banner, today's tasks, schedule, pay).
- **Reports** filtered by date range and project, printable to PDF and exported to Excel-ready CSV.
- Shared components: status badges, primary, secondary and danger buttons, illustrated empty states, loading skeletons, toasts and confirmation dialogs.

## Requirements

- Python 3.10 or newer (Django 5.2 LTS).
- pip.
- PostgreSQL is optional; SQLite is configured by default.
- Dependencies (`requirements.txt`): Django and **segno** (QR code generation; pure Python, no other dependencies).

## Run locally on Windows

From this folder, open PowerShell and run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo        # optional: realistic demo company, workers and history
python manage.py runserver
```

Open `http://127.0.0.1:8000/`. The administrator account created with `createsuperuser` receives the administrator role.

**Email confirmation:** new client and company accounts must click a link emailed to them before they can sign in. In development, emails print to the console running `runserver`, and the "Check your inbox" page also shows the link (only while `DJANGO_DEBUG` is on with the console email backend). Accounts created by a company manager, the administrator, demo accounts, and accounts that existed before this feature are already confirmed. Configure SMTP (`DJANGO_EMAIL_BACKEND` and Django's `EMAIL_HOST` settings) before going live. The service catalogue is filled in when migrations run.

If PowerShell blocks virtual environment activation, run the venv's executable directly, for example `.\.venv\Scripts\python.exe manage.py runserver`.

### Demo data

`python manage.py seed_demo` adds Salone Builders Ltd with two Freetown projects (Lumley and Wellington), 12 workers with trades and daily rates in Leones, a month of attendance, tasks, site reports, incidents, material requests and last month's approved payroll. It never changes or deletes existing records, can be run again safely, and refuses to run with `DJANGO_DEBUG=0`.

Every demo account uses the password **`BuildLink-Demo-2026!`**:

| Username | Role |
| --- | --- |
| `salone.owner` | Company owner / lead project manager |
| `fatmata.pm` | Project manager |
| `abubakarr.supervisor` | Site supervisor (Lumley) |
| `mariama.supervisor` | Site supervisor (Wellington) |
| `mohamed.worker`, `alusine.worker` | Workers |
| `aminata.client` | Client (Lumley project) |

### Demo flow

1. Sign in as `fatmata.pm`. Go to **Projects → New project**, add a supervisor, then add a site (with coordinates) and a phase.
2. Sign in as `abubakarr.supervisor`. On the project's **Tasks** tab, add a task, then assign the top **Suggested worker**.
3. Sign in as the worker. Use **Clock in / out** from inside the site boundary, or have the supervisor **Scan QR cards**.
4. As the supervisor, submit a **Daily report** that updates the task's progress.
5. Sign in as `fatmata.pm` and open **Overview**: the worker shows as on site, and the project progress, attendance trend and labour cost are updated.

The same flow runs automatically in `DemoFlowTests` (see Tests).

### Testing GPS, camera and offline on a phone

Browsers only allow location, camera and service workers on **HTTPS** or on `localhost`. On the development machine, `http://127.0.0.1:8000` works. To test from a phone, serve the app over HTTPS, for example through an HTTPS tunnel or a reverse proxy with a certificate, and add the hostname to `DJANGO_ALLOWED_HOSTS`. QR camera scanning uses the browser's built-in barcode detector (Chrome on Android). Elsewhere, supervisors type the code printed under the QR.

## Tests

```powershell
python manage.py check
python manage.py test coordination
```

There are 65 tests in `coordination/tests.py` and `coordination/test_operations.py`, covering email confirmation, authentic-data rules, geofencing, attendance and hours, offline de-duplication, task matching, progress roll-up, payroll, mobile money, messaging, QR codes, access control, the Kanban board, project tabs, the project selector, report export, worker photo privacy, and the end-to-end demo flow. The user acceptance checklist is in [`docs/ACCEPTANCE_CHECKLIST.md`](docs/ACCEPTANCE_CHECKLIST.md).

## Main routes

| Area | Route |
| --- | --- |
| Public homepage | `/` |
| Company directory | `/companies/` |
| Company / client registration | `/register/company/`, `/register/client/` |
| Login | `/login/` |
| Role-based dashboard | `/dashboard/` |
| Workforce and ID cards | `/company/workers/`, `/company/workers/<id>/id-card/` |
| Team accounts | `/company/team/` |
| Projects | `/projects/`, `/projects/new/` |
| Project sites, phases, tasks | `/projects/<id>/plan/`, `/projects/<id>/tasks/` (`?view=list` for the list) |
| Project workers, materials, incidents | `/projects/<id>/workers/`, `/projects/<id>/materials/`, `/projects/<id>/incidents/` |
| Attendance register / QR scanner | `/projects/<id>/attendance/`, `/projects/<id>/attendance/scan/` |
| Worker clock-in | `/attendance/clock/` |
| Daily site reports | `/projects/<id>/reports/` |
| Safety incidents | `/incidents/` |
| Materials and equipment | `/materials/` |
| Payroll | `/payroll/` |
| Worker pay and attendance | `/my/pay/`, `/my/attendance/` |
| Announcements | `/announcements/` |
| Requests | `/requests/` |
| Reports and CSV export | `/reports/`, `/reports/export/` |
| Administrator review | `/admin-portal/companies/` |
| Django administration (read-only records) | `/django-admin/` |

## Notifications and payments

The defaults are safe for development and demonstration:

| Channel | Default | To go live |
| --- | --- | --- |
| Email | Printed to the server console | Set `DJANGO_EMAIL_BACKEND` and Django's SMTP settings |
| SMS | Sandbox: logged and stored in the outbox, never sent | Write a class with `send(phone, body)` for your provider and set `BUILDLINK_SMS_BACKEND` |
| Orange Money / Afrimoney | Sandbox gateway: realistic references, no money moves; wallet numbers ending `0000` simulate a failure | Implement `disburse(provider, msisdn, amount, reference)` against the provider API and set `BUILDLINK_MOBILE_MONEY_GATEWAY` |

Every SMS and email is stored in the `OutboundMessage` table, and every payment attempt in `MobileMoneyTransaction`; both are visible in the Django administration.

## Data and file handling

- The development database is `db.sqlite3`.
- Public company logos are stored under `media/`.
- Registration papers, request attachments, site report photos and incident photos are stored under `private_uploads/`, outside public media. They are served only to users allowed to see the related record.
- Uploads are limited to PDF and PNG/JPG/JPEG/WEBP, at most 5 MB, and content signatures are checked as well as extensions.
- Important actions across every module are recorded in the audit log.

For a local SQLite backup, stop the server and copy `db.sqlite3` plus both upload folders to secure storage. To restore, stop the server, restore those files to the same locations, then restart.

## PostgreSQL

```powershell
python -m pip install -r requirements-postgres.txt
$env:DJANGO_DB_ENGINE = 'postgresql'
$env:POSTGRES_DB = 'buildlink'
$env:POSTGRES_USER = 'buildlink'
$env:POSTGRES_PASSWORD = 'replace-with-a-secret'
$env:POSTGRES_HOST = 'localhost'
$env:POSTGRES_PORT = '5432'
python manage.py migrate
```

Create database backups with `pg_dump` and verify restoration as part of deployment operations.

## Production configuration

Do not use the development settings in production. Set `DJANGO_DEBUG=0`, a unique `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_SITE_URL`. HTTPS redirects and secure cookies are enabled when debug mode is off. HTTPS is also required for GPS, camera and offline features. If TLS terminates at a trusted reverse proxy, set `DJANGO_TRUST_X_FORWARDED_PROTO=1`. Serve collected static files and public logos from the web server, and never expose `private_uploads/`. Use PostgreSQL for a shared deployment and schedule encrypted database and file backups. Do not run `seed_demo` on a live system.

```text
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=use-a-long-random-secret-here
DJANGO_ALLOWED_HOSTS=your-domain.example
DJANGO_SITE_URL=https://your-domain.example
DJANGO_DB_ENGINE=postgresql
POSTGRES_DB=buildlink
POSTGRES_USER=buildlink
POSTGRES_PASSWORD=...
POSTGRES_HOST=...
POSTGRES_PORT=5432
```

## Project structure

```text
construction_system/          Django settings and URL configuration
coordination/                 The application
  models.py                   All tables (release 1 and site operations)
  migrations/                 0001–0002 release 1, 0003 site operations, 0004 email confirmation, 0005 worker photos
  access.py                   Role and ownership rules for every operations view
  services.py                 Geofencing, attendance, matching, progress, payroll
  messaging.py, payments.py   SMS/email outbox and mobile money gateway (sandbox)
  qr.py, charts.py            QR ID cards and server-rendered SVG charts
  context_processors.py       Role navigation, page titles, breadcrumbs and the header project selector
  templatetags/               icon, trade_badge and form helpers
  dashboards.py               Dashboard and report data per role
  views.py                    Release 1 views (with role extensions)
  operations_views.py         Site operations views
  forms.py                    Forms and validation
  management/commands/        seed_demo
  tests.py, test_operations.py
templates/coordination/       Pages; ops/ holds the site operations pages
static/coordination/          site.css (theme), site.js (forms), operations.js (GPS, QR, offline, Kanban, map, toasts), sw.js, brand/
docs/                         Technical design, diagrams, user guide, acceptance checklist
```

## Documentation

- [`docs/TECHNICAL_DESIGN.md`](docs/TECHNICAL_DESIGN.md): architecture, entities, permission matrix, rules and security.
- [`docs/DIAGRAMS.md`](docs/DIAGRAMS.md): system architecture, use cases, ER diagram, and activity diagrams for clock-in, payroll, material requests, offline sync and the demo flow.
- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md): step-by-step guide for every role.
- [`docs/ACCEPTANCE_CHECKLIST.md`](docs/ACCEPTANCE_CHECKLIST.md): automated test coverage and the UAT checklist.

## Scope notes

SMS and mobile money run against sandboxes until provider accounts are configured. The dashboard site map loads Leaflet 1.9.4 from unpkg (pinned with a subresource integrity hash) and OpenStreetMap tiles; fonts come from Google Fonts. Without internet access the map is skipped and the rest of the page still works. The system does not provide direct client-to-worker hiring or native mobile apps; the worker and supervisor screens are mobile-first web pages.
