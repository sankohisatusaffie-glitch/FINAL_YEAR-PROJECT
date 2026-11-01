# Technical design

## Architecture

BuildLink is a server-rendered Django application with one domain app (`coordination`), Django authentication, a relational database, Django templates, and local static assets. SQLite is the default development database. PostgreSQL can be selected with environment settings.

The application boundary follows the company-mediated model:

```text
Administrator ──verifies──> Company ──owns──> Worker records
                                  │
Client ──service request──────────┘
  │                               │
  └────────messages / status──────┘
                                  ↓
                         Project assignments
                                  ↓
                     Client confirmation / rating
```

The browser never receives worker personal records from public company profile or directory views. Uploaded registration documents and request attachments are held under `PRIVATE_MEDIA_ROOT` and served through a view that checks the administrator, owning company, or related client. Public logos are stored under `MEDIA_ROOT`.

## Main relational entities

- `User`: Django password-hashed account plus administrator, company, or client role.
- `Company`: one-to-one owner, registration and public profile fields, review state, approval metadata, and offered services.
- `ClientProfile`: client contact and account type.
- `Service`: platform service catalog shared by companies and requests.
- `Worker`: one company-owned workforce record with skills, experience, employment state, and availability. Company and worker ID are unique together.
- `ServiceRequest`: client-to-company request, files, lifecycle state, and cancellation audit fields.
- `Project`: one-to-one continuation of an accepted request, progress, dates, and client confirmation.
- `WorkerAssignment`: project-to-worker history, unique per project/worker, including assigner and status.
- `Rating`: one per project, attached to its client and company.
- `Message`, `Notification`, `Complaint`, and `AuditLog`: private conversation, user updates, administrative concerns, and accountability records.

The initial schema is in `coordination/migrations/0001_initial.py`.

## Authorization rules

| Capability | Administrator | Company owner | Client |
| --- | --- | --- | --- |
| Review company applications | Yes | No | No |
| Manage company public profile | Oversight | Own approved company | No |
| Read/manage worker records | Read-only oversight | Own company's workers | No |
| Discover approved companies | Yes | Yes | Yes |
| Submit a service request | No | No | Yes |
| Review requests and message the other party | Oversight | Own company's requests | Own requests |
| Create a project and assign workers | No | Own company's project | No |
| Confirm project completion and review | No | No | Own completed project |

Views check the authenticated role and ownership before returning a record or accepting a change. Company worker lists and worker-edit routes filter by the authenticated owner's company. Assignment queries and writes enforce the project's company as well as worker availability. User-submitted forms apply field validation; uploads enforce a 5 MB limit, allowed extension, and file signature.

## Workflow states

- Company: pending → under review → approved / rejected. An approved company may later be suspended or deactivated. Only approved companies appear in the client directory and receive new requests.
- Request: submitted → under review → accepted / declined. Acceptance creates a project. Project execution updates the request to in progress and completion updates it to completed. Client cancellation stores actor, timestamp, and reason.
- Project: planned → in progress / on hold → completed. The client confirms completion before a rating can be submitted.
- Worker: employment is active or former; availability is tracked independently. Active assignments block a worker from recommendations for a second concurrent project.

## Operational settings

| Setting | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required secret key; a placeholder is accepted only while debug is enabled. |
| `DJANGO_DEBUG` | Set to `0` in deployment. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames accepted by Django. |
| `DJANGO_SECURE_SSL_REDIRECT` | HTTPS redirect override. Defaults on when debug is off. |
| `DJANGO_DB_ENGINE` | `sqlite` by default, or `postgresql`. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | PostgreSQL connection details. |

Production should use PostgreSQL, HTTPS, an environment-managed secret, restricted database and file-store access, and a web server that serves only collected static files and public media. Do not expose `private_uploads/` through a static or media URL.

## Backup and recovery

For SQLite, stop the web process and copy `db.sqlite3`, `media/`, and `private_uploads/` together to encrypted storage. To restore, stop the process, restore all three paths, verify file permissions, and restart. For PostgreSQL, back up with `pg_dump` and back up both file stores in the same backup window. Periodically restore a backup into a separate environment and verify representative users, companies, requests, assignments, private attachments, and audit records.

## Release 2: site operations

Release 2 adds workforce operations on top of the company-mediated model above. Diagrams (architecture, use cases, ER, activity flows) are in [`DIAGRAMS.md`](DIAGRAMS.md).

### Roles

Roles are stored on `User.role`. The three original roles are unchanged; three company staff roles were added. Staff accounts belong to a company through `User.employer`. The company owner acts as the lead project manager.

| Role | Created by | Works on |
| --- | --- | --- |
| Administrator | `createsuperuser` | The whole platform (verification, users, complaints, audit) |
| Company (owner) | Self-registration, then admin approval | Everything in their company |
| Project manager | Company owner | Their company: projects, workforce, payroll, approvals, team accounts (not other managers) |
| Site supervisor | Owner or project manager | Projects they are assigned to: tasks, attendance, QR scanning, site reports, incidents, material requests |
| Worker | Owner or project manager, linked to a worker record | Their own tasks, attendance and payslips; GPS clock-in; incident reports |
| Client | Self-registration | Read-only progress on their own projects, plus the original request workflow |

### Modules and where they live

| Module | Models | Main code |
| --- | --- | --- |
| Authentication and roles | `User` (roles, employer) | `access.py`: every operations view calls `get_project()` or `require_operating_company()` |
| Worker management | `Worker` (adds `daily_rate`, `skill_set`, `qr_token`, mobile money), `Skill` | `WorkerForm`, `views.worker_*` |
| Projects and sites | `Project` (adds budget, manager, supervisors; request and client now optional), `Site`, `Phase` | `operations_views.project_*`, `site_form`, `phase_form` |
| Tasks and scheduling | `Task`, `TaskAssignment` | `services.rank_workers_for_task`, `assign_worker_to_task`, `set_task_progress` |
| Attendance and time | `Attendance` | `services.record_clock`, `check_geofence`, `shift_hours`, `mark_absence`; `clock_api` |
| Communication | `Announcement`, `Notification`, `OutboundMessage` | `messaging.py` (in-app, SMS backend, email) |
| Progress reporting | `SiteReport`, `ReportPhoto` | `site_report_create`; task progress rolls up to phases and the project |
| Payroll | `PayrollRecord`, `MobileMoneyTransaction` | `services.generate_payroll`, `payments.pay_wages` |
| Dashboard and reports | (no new tables) | `dashboards.py`, `charts.py` (server-rendered SVG) |
| Safety | `Incident` | `incident_*` views; high and critical incidents alert by SMS and email |
| Materials | `MaterialRequest` | `material_*` views |
| Offline mode | `client_uuid` and `clock_out_uuid` on records | `static/coordination/sw.js`, `operations.js` |
| Audit | `AuditLog` | `utils.log_event`, called by every new write |

The schema change is one migration, `0003_site_operations.py`. It only adds tables and columns, and makes `Project.request` and `Project.client` optional. Existing rows are preserved: each existing worker gets its own QR token, and each worker's free-text trade and skills become linked `Skill` rows.

### Key rules

- **Geofence.** A clock-in by GPS (worker) or QR (the supervisor's phone) must be within the site's radius, using a GPS fix accurate to 100 m or better. Distance uses the haversine formula. Supervisors can record a manual entry with a mandatory reason when GPS or QR is impossible; the reason is audited.
- **One attendance record per worker per day** (database constraint). Workers must be on the project's active team. Hours are capped at 16 per shift.
- **QR cards** contain only `BUILDLINK-WORKER:<32-hex token>`, never personal data. Tokens are looked up inside the scanning supervisor's company only, and a manager can reissue a card, which invalidates the old one.
- **Matching** ranks active workers who are free or already on the project: +50 for the required skill, +20 for being on the team, plus years of experience (up to 20), plus recent attendance (up to 10), minus 5 per open task. The supervisor always makes the final choice.
- **Pay** is hours worked ÷ 8 × daily rate (SLE), minus deductions. Re-running a period refreshes drafts only; approved and paid payslips never change. Payment needs an approved payslip and a valid Sierra Leone wallet number.
- **Offline.** Queued submissions are tagged with the signed-in user and replayed only for that user. The server ignores a repeated `client_uuid`, and offline times older than 72 hours or in the future are rejected. Signing out clears cached pages.

### Permission matrix (site operations)

| Capability | Admin | Owner | Project manager | Supervisor | Worker | Client |
| --- | --- | --- | --- | --- | --- | --- |
| Create or edit projects, sites, phases | – | ✓ | ✓ | – | – | – |
| Create tasks, assign workers | – | ✓ | ✓ | Assigned projects | – | – |
| Update task progress | – | ✓ | ✓ | Assigned projects | Own tasks | – |
| Scan QR, manual attendance, absences | – | ✓ | ✓ | Assigned projects | – | – |
| GPS clock-in | – | – | – | – | ✓ | – |
| Submit site reports | – | ✓ | ✓ | Assigned projects | – | – |
| View site reports and plan | All | ✓ | ✓ | Assigned projects | Own projects | Own projects |
| Report incidents | – | ✓ | ✓ | ✓ | Own projects | – |
| Request materials | – | ✓ | ✓ | Assigned projects | – | – |
| Approve materials, run payroll, pay | – | ✓ | ✓ | – | – | – |
| View payslips | – | ✓ | ✓ | – | Own, once approved | – |
| Create team accounts | – | All staff roles | Supervisors and workers | – | – | – |

### Security

- Passwords use Django's PBKDF2 hashing and the configured password validators.
- Self-registered accounts confirm their email through a signed, single-use link (`verification.py`) that expires after 3 days; sign-in is refused until then. The refusal appears only after a correct password, so it does not reveal which emails are registered. Resends are limited to one every two minutes. Company applications reach administrators only once confirmed.
- Authentic-data rules (`forms.py`): phone numbers must be Sierra Leone numbers (+232 and 8 digits starting 2, 3, 7, 8 or 9; placeholders like 77777777 are refused) and are stored in one format; mobile money and SMS numbers must be mobile, not landline; the same phone cannot be registered twice; throwaway email domains are refused; names and addresses are tidied and junk such as "Aaaa" is rejected; a worker's emergency contact must include someone else's number, and experience cannot exceed age minus 14.
- Every route checks role and ownership on the server (`access.py`); hiding a link is never the only control.
- All database access goes through the Django ORM with parameterised queries; there is no raw SQL.
- Templates auto-escape output. Client-side code writes untrusted text with `textContent`, never `innerHTML`.
- Forms validate on the server; browser checks are a convenience. Uploads are limited to 5 MB with extension and file-signature checks. Site photos are stored privately and served only to people who can view the project.
- CSV exports prefix cells that start with `=`, `+`, `-` or `@` to block spreadsheet formula injection.
- CSRF protection covers every POST, including offline replays (the current token is attached at sync time).

### Notifications and payments configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `BUILDLINK_SMS_BACKEND` | `coordination.messaging.SandboxSmsBackend` | Class with `send(phone, body)`. The sandbox only logs. |
| `DJANGO_EMAIL_BACKEND` | Console backend | Use SMTP settings in deployment. |
| `DJANGO_DEFAULT_FROM_EMAIL` | `BuildLink <no-reply@buildlink.local>` | Sender address. |
| `DJANGO_SITE_URL` | `http://127.0.0.1:8000` | Used for links in emails. |
| `BUILDLINK_MOBILE_MONEY_GATEWAY` | `coordination.payments.SandboxMobileMoneyGateway` | Class with `disburse(provider, msisdn, amount, reference)`. The sandbox never moves money; numbers ending in `0000` simulate a failure. |

Every SMS and email is recorded in `OutboundMessage`, and every payment attempt in `MobileMoneyTransaction`, so delivery can be audited.

## Release 3: construction interface

The interface keeps the same stack: server-rendered Django templates, one stylesheet and plain JavaScript.

| Concern | Where it lives |
| --- | --- |
| Design tokens | `:root` in `static/coordination/site.css`: `--safety-yellow` #F5B700, `--construction-orange` #E8672A, `--charcoal` #1F2328, `--steel` #5B6670, `--concrete` #F2F2EF; statuses success #2E9E5B, warning #F5B700, danger #D93B3B, info #2F6FB5. Text on yellow is always charcoal. |
| Typography | Barlow Condensed (headings) and Inter (body) from Google Fonts. |
| Icons | Inline SVG sprite in `templates/coordination/_icons.html` (`i-*` general, `t-*` trades); `{% icon 'name' %}`, `{% trade_badge trade %}` in `templatetags/coordination_tags.py`. |
| Brand | `_logo_mark.html` and `static/coordination/brand/favicon.svg`. |
| Navigation | `context_processors.portal_context` builds the sidebar per role, the phone bottom bar, page titles, breadcrumbs (a project name appears only if the user can see that project) and the header project list. |
| Components | Status badges, buttons, empty states, skeletons, toasts (from Django messages) and the confirmation `<dialog>` (any `form[data-confirm]`), in `base.html`, `site.css` and `operations.js`. |
| Site map | `dashboards.site_markers()` supplies sites through `json_script`; `operations.js` draws them with Leaflet 1.9.4 (SRI-pinned). Popups are built with `textContent`. |
| Gantt | `operations_views.phase_timeline()` turns phase dates into percentage offsets; rendered with CSS only. |
| Kanban | `task_move` (POST `status`) sets Pending to 0%, In progress to at least 10% and Completed to 100% through `set_task_progress`, so progress still rolls up. Supervisors move any card; workers only their own. Answers JSON for drag and drop, or redirects without JavaScript. |
| Reports | `views.report_scope()` applies the date range and project filters; `reports_export` writes UTF-8 CSV with a byte order mark, and prefixes cells starting with `= + - @` to block spreadsheet formula injection. |
| Worker photos | `Worker.photo` (migration `0005_worker_photo`) in private storage, served by `operations_file` kind `worker-photo` only to the worker and site roles of the same company. |

Single-series charts use construction orange, because safety yellow is too light against white for chart marks.

### Remaining scope limits

- SMS and mobile money run in sandbox mode. Going live needs provider accounts and a backend class for each provider's API; the interfaces are in place.
- QR camera scanning uses the browser's built-in `BarcodeDetector` (Chrome and Edge on Android). On other browsers, supervisors type the code printed under the QR.
- GPS and camera access require HTTPS, or `localhost` during development.
- Offline mode covers clock-in and clock-out, QR scanning and daily site reports. Other pages need a connection.
- Payslips download as PDF through the browser's print dialog ("Save as PDF") and as CSV; no server-side PDF library is used.
- Client-to-company service requests stay with the company owner account; project managers work on projects once they exist.
