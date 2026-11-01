# Acceptance checklist

## Automated tests

Unit and workflow tests live in `coordination/tests.py` (release 1 workflows and form validation) and `coordination/test_operations.py` (site operations). Run them after installing dependencies:

```powershell
python manage.py check
python manage.py test coordination
```

Last recorded run: **65 tests, all passing**, plus `manage.py check` with no issues (Django 5.2, Python 3.14, SQLite, 24 October 2026).

The operations tests cover:

| Area | What is verified |
| --- | --- |
| Geofencing | Haversine distance; inside, outside, missing location, poor accuracy; unenforced boundary |
| Attendance | Clock-in then clock-out with hours; outside-site rejection; worker not on team; offline replay stored once; stale or future offline times rejected; manual entry needs a reason; 16-hour cap |
| Task matching | Required skill ranks first with its reason; busy workers on other projects excluded; assigning a free worker adds them to the team |
| Progress | 100% completes a task; project progress is the average of its tasks |
| Payroll | Pay = hours ÷ 8 × daily rate; absences unpaid; approved payslips never recalculated |
| Mobile money | Sandbox success marks the payslip paid and texts the worker; failure leaves it unpaid; drafts and double payment refused |
| Messaging and QR | Sierra Leone number formats; project announcements reach the team and send SMS; QR payload round trip |
| Access control | Unassigned supervisor, client, rival company and worker are each refused what they should not see; only supervisors can scan QR cards; rejecting a material request needs a reason |
| Demo flow | Create project → assign best-matched worker → GPS clock-in → site report updates progress → manager dashboard shows the worker on site and the progress |
| Offline reports | Re-sending the same offline report stores it once and flags it as synced |
| Interface (release 3) | Kanban moves set status and progress (JSON and no-JavaScript); only supervisors and assigned workers can move cards; board and list views render; project Workers, Materials and Incidents tabs refuse clients; the project selector only opens visible projects; report export is an Excel-friendly CSV; worker photos are private to the company |

## User acceptance testing

Load the demo data first (`python manage.py seed_demo`); the password for every demo account is `BuildLink-Demo-2026!`. Tick each scenario after demonstrating it in a browser. GPS and camera features need `localhost` on the same machine, or HTTPS on a phone.

### Release 1 workflows

| ✓ | Scenario | Expected result |
| --- | --- | --- |
| ☐ | Company submits an application | A company account is created with pending status; administrators are notified. |
| ☐ | Administrator reviews a company | Approval activates company tools and public discovery; a decision note reaches the company. |
| ☐ | Pending or rejected company uses workforce tools | Access is denied. |
| ☐ | Client searches the directory | Only approved companies appear; service, location, rating and experience filters work. |
| ☐ | Client submits a request; company accepts it | The request is accepted and exactly one project is created. |
| ☐ | Client confirms and reviews completed work | One rating is allowed, only after company completion and client confirmation. |
| ☐ | Company A requests Company B's worker URL | Not found; no other company's worker details are exposed. |
| ☐ | New client registers | The "Check your inbox" page appears; signing in is refused until the emailed link is used; the link signs them in and works only once. |
| ☐ | New company registers and confirms its email | Administrators are notified only after the confirmation. |
| ☐ | Register with a temporary inbox (e.g. yopmail.com), a foreign or fake phone (12345, 77777777), or a name like "Aaaa" | Each field is refused with a specific reason. |
| ☐ | Register with a phone number already used by another client | Refused: the number is already registered. |
| ☐ | Enter a worker aged 25 with 20 years' experience, or an emergency contact without a number | Refused with an explanation. |
| ☐ | Any form is submitted with missing or invalid fields | Inline messages appear next to each field and a summary at the top; nothing is saved. |

### Release 2: site operations

| ✓ | Scenario | Sign in as | Expected result |
| --- | --- | --- | --- |
| ☐ | Create a team account for a supervisor and for a worker | `salone.owner` | Both can sign in; the worker account is linked to its worker record. |
| ☐ | Create a project with a budget, manager, supervisor and client email | `fatmata.pm` | The project opens on Sites & phases; the supervisor and client are notified. |
| ☐ | Add a site with **Use my current location** and a phase marked as milestone | `fatmata.pm` | Coordinates fill in; the site shows its boundary; the milestone is marked ◆. |
| ☐ | Add a task needing *Blockwork*; open **Suggested workers** | `abubakarr.supervisor` | Workers with the skill rank first, with reasons; assigning one alerts the worker. |
| ☐ | Worker clocks in from inside the boundary | `mohamed.worker` | "Clocked in at HH:MM"; the button changes to Clock out. |
| ☐ | Worker tries to clock in from far away (deny location or use another place) | `mohamed.worker` | Refused with the distance and the allowed radius; nothing is saved. |
| ☐ | Supervisor scans a printed QR ID card (Android Chrome), or types its code | `abubakarr.supervisor` | The scan log shows clock-in; the second scan clocks out with hours. |
| ☐ | Record a manual entry and a sick day on the register | `abubakarr.supervisor` | Both appear on the day's register; the manual reason is in the audit trail. |
| ☐ | Submit a daily report with two photos and a task progress update | `abubakarr.supervisor` | The report lists with photos; task, phase and project progress update. |
| ☐ | Turn on airplane mode, submit a daily report and a clock-in, then reconnect | supervisor / worker | "Saved on this phone" with a waiting badge; after reconnecting, "Synced" toasts and the records appear once. |
| ☐ | Report a high-severity PPE incident | `abubakarr.supervisor` | Managers get in-app, SMS (outbox) and email (console) alerts; the manager can investigate and close it with an action. |
| ☐ | Request cement; manager approves; then marks it delivered | supervisor, then `fatmata.pm` | Status moves pending → approved → delivered; the requester is notified each time. Rejecting without a reason is refused. |
| ☐ | Generate payroll for this month, add a deduction, approve and pay | `fatmata.pm` | Pay matches hours ÷ 8 × rate; payment returns an `OM-SBX-…` or `AFM-SBX-…` reference and the payslip becomes Paid. A wallet number ending 0000 fails cleanly. |
| ☐ | Download a payslip as PDF and the period as CSV | `fatmata.pm` / `mohamed.worker` | The print view shows only the payslip; the CSV opens in a spreadsheet. |
| ☐ | Post an announcement to one project with SMS | `fatmata.pm` | Only that team receives it; SMS entries appear in the outbox (Django admin → Outbound messages). |
| ☐ | Open the dashboard | `salone.owner`, `fatmata.pm` | Workforce on site now, today's attendance, open tasks, incidents, approvals, labour cost, attendance trend, labour cost chart and project progress all show. Each chart has **Show as table**. |
| ☐ | Client opens their project | `aminata.client` | Can view progress, phases and site reports; cannot open tasks, attendance or payroll. |
| ☐ | Worker opens another worker's payslip URL | `mohamed.worker` | Access denied. |
| ☐ | Administrator reviews the audit trail | administrator | Attendance, task, report, incident, material, payroll and payment actions are listed with who and when. |

### Release 3: construction interface

| ✓ | Scenario | Sign in as | Expected result |
| --- | --- | --- | --- |
| ☐ | Open the login page on a laptop and a phone | anyone | Hard-hat logo, yellow sign-in button with dark text, construction photo panel; no sideways scrolling on the phone. |
| ☐ | Open the dashboard | `fatmata.pm` | Five KPI cards, the site map with a pin for each site, who is on site, both charts, project progress and recent activity. |
| ☐ | Use the header project selector | `fatmata.pm` | Choosing a project opens its overview; the breadcrumb shows Overview / Projects / project. |
| ☐ | Drag a task from Pending to In progress, then to Completed | `abubakarr.supervisor` | The card moves, column counts update, a toast confirms, and the project progress changes. |
| ☐ | Open a project's Phases tab | `fatmata.pm` | The Gantt timeline shows each phase's dates and progress. |
| ☐ | Switch Workforce between table and cards; upload a photo and open the ID card | `fatmata.pm` | Both views list the same workers with trade badges; the photo appears on the card. |
| ☐ | Open the supervisor and worker overview on a phone | `abubakarr.supervisor`, `mohamed.worker` | Large action buttons, sync status strip and the bottom navigation bar. Turning on airplane mode changes the sync status to offline. |
| ☐ | Report an incident | `abubakarr.supervisor` | Hazard-striped header and a severity picker; required fields are marked and explained. |
| ☐ | Filter reports to last month and one project; export and print | `fatmata.pm` | Figures change with the filter; the CSV opens in Excel; the print view shows the logo and hides navigation. |
| ☐ | Remove a worker from a task | `abubakarr.supervisor` | A confirmation dialog appears before anything is changed. |

Record the date, tester and any defects found for each scenario in the hand-in.
