# BuildLink user guide

BuildLink connects clients with verified construction companies and runs their sites: projects, tasks, attendance, site reports, safety, materials and payroll. Company managers control their workforce and remain the point of contact throughout a project.

## For clients

1. Choose **Create account** and register as a client. Client accounts are active immediately.
2. Open **Find companies**. Search by company, area, service, rating, or years in business.
3. Open a verified company profile to review its services, location, contact details, team skill summary, and client ratings.
4. Choose **Request a service** and describe the work, location, expected timing, workforce needs, and any supporting files.
5. Open **My requests** to follow the status and message the company. A company may accept, decline, or mark the request under review.
6. An accepted request opens a project. Follow progress and keep project questions in the request conversation.
7. If plans change, cancel the request from its detail page and provide a reason. If work has begun, the cancellation reason remains part of the project record.
8. After the company marks the project complete, review the work and confirm completion. You can then submit one rating and review for that project.

Clients do not hire, assign, or manage individual workers through BuildLink.

## For construction companies

1. Choose **Register your company** and submit your account, business profile, service areas, and registration information.
2. Sign in to see the application status. The administrator may approve, reject, suspend, or ask for corrections. Pending and rejected companies are not listed to clients.
3. Once approved, update your public profile and add workers under **Workers**. Use a company-specific worker ID, record skills and experience, and keep availability current.
4. Review incoming work from **Requests**. Read the brief, ask questions in the private conversation, and accept, decline with a reason, or mark the request under review.
5. An accepted request creates a project. Update its progress and dates as work proceeds.
6. Assign workers from the project page. Recommendations prioritize trade match, experience, availability, and prior project history. Your company makes each assignment.
7. When work is finished, mark the project complete. The client can confirm completion and leave a review.
8. Mark a departing worker as a former worker to preserve historical assignments and project records.

Company managers can only view and manage workforce records owned by their own company. Public profiles show workforce skill counts, not worker contact details.

## For project managers (and the company owner)

The company owner can do everything below. They can also add project managers under **Team accounts**.

Use the **project selector** in the top bar to jump straight to any of your projects. Each project has tabs for Overview, Phases, Tasks, Workers, Attendance, Reports, Materials and Incidents.

1. **Add your team.** In **Team accounts**, choose **Add an account** and pick *Project manager*, *Site supervisor* or *Worker*. A worker account must be linked to the worker's record so their tasks, attendance and pay follow them.
2. **Set up workers for pay and matching.** Edit each worker under **Workforce**: tick their **Skills**, set the **Daily rate (SLE)** for an 8-hour day, add their Orange Money or Afrimoney number, and upload a **photo** for their ID card. The Workforce page can be shown as a table or as cards.
3. **Print QR ID cards.** Open a worker's ID card from the Workforce list (`/company/workers/<id>/id-card/`) and choose **Print card**. If a card is lost, **Reissue card** stops the old one working.
4. **Create a project.** **Projects → New project**: enter the title, location, dates, budget in Leones, the project manager and the supervisors. To give a client read-only access, enter the email address of their BuildLink account.
5. **Add sites and phases.** On the project's **Sites & phases** tab, add each site. Stand on site and press **Use my current location** so the attendance boundary is accurate; 150 m suits most plots. Add phases such as Foundation, Blockwork and Roofing, and tick *milestone* for checkpoints such as Handover. Phases with dates appear on the **Gantt timeline**.
6. **Approve requests.** **Materials** lists what supervisors have asked for. Open a pending request to approve or reject it (a reason is required to reject), and mark it delivered when it arrives.
7. **Run payroll.** **Payroll**: choose the period (up to one month) and **Calculate payroll**. Open each payslip, add any deductions, then **Approve**. Approved payslips can be paid with **Pay via Orange Money / Afrimoney**. **Download CSV** exports the period.
8. **Watch the dashboard.** The overview shows a **map of your active sites**, who is on site now, today's attendance, open tasks, safety incidents, requests to approve, labour cost this month, the attendance trend, labour cost by month, and progress for each project. **Reports** can be filtered by date range and project, saved as PDF with **Export PDF** (your browser's print dialog), and exported with **Export Excel** (a CSV file that opens in Excel).
9. **Post announcements** to the whole company or one project team. Tick **SMS** to reach workers without smartphones.

## For site supervisors

1. Your **Overview** lists the projects you supervise, with today's attendance and quick buttons.
2. **Check workers in.** Choose **Scan QR cards**, then **Start camera**, and hold each worker's card in the frame. The first scan clocks them in and the second clocks them out. Your phone's location is checked against the site boundary. If the camera is not supported, type the code printed under the QR.
3. **Register.** The **Attendance** tab shows everyone on the team for the day. If a worker has no card or phone, use **Manual clock in / out** and give a reason. Record sick days, leave and no-shows with **Mark absent or on leave**.
4. **Tasks.** The **Tasks** tab is a board with Pending, In progress and Completed columns: drag a card to another column, or use **Move to** on the card. **Add task** with the skill needed, priority and deadline. On the task page, **Suggested workers** ranks who fits best and explains why; press **Assign**. Workers get an in-app and SMS alert.
5. **Daily report.** At the end of the day, open **Site reports → New daily report**: summary, work done, issues, weather, workers on site, and up to six photos. You can update one task's progress in the same report.
6. **Safety and supplies.** Use **Report incident** for accidents, near misses and PPE breaches; high and critical reports alert managers by SMS. Use **Request materials** for cement, steel, equipment hire and so on.
7. **No signal?** Scanning and daily reports keep working. Records are saved on the phone, a badge shows how many are waiting, and they are sent automatically when signal returns. Open the scanner and report pages once while online so the phone has them saved.

## For workers

1. Sign in on your phone with the account your company created.
2. **Clock in / out**: pick the project and press the big button. You must be at the site and allow location access. Without signal, the clock-in is saved and sent later.
3. **Overview** shows your status today, your hours this month, your open tasks, announcements and your latest payslips.
4. Open a task to update its progress (100% marks it complete), or move your own card on the project's task board.
5. **My pay** lists approved payslips; open one and choose **Download PDF** to keep a copy. When your manager pays you, you also get an SMS with the mobile money reference.
6. **My QR ID card** shows your card if a supervisor needs to scan you in.
7. Report any accident, near miss or unsafe condition from your project's page with **Report incident**.

## For clients (project progress)

If your contractor links your account to a project, **My projects** shows its progress, phases and milestones, and the daily site reports with photos. These pages are read-only.

## Trying the system with demo data

Run `python manage.py seed_demo` once (see the README). It adds Salone Builders Ltd with two projects in Lumley and Wellington, 12 workers, a month of attendance, tasks, site reports, incidents, material requests and last month's approved payroll. All demo accounts use the password `BuildLink-Demo-2026!`:

| Username | Role |
| --- | --- |
| `salone.owner` | Company owner (lead project manager) |
| `fatmata.pm` | Project manager |
| `abubakarr.supervisor` | Site supervisor, Lumley |
| `mariama.supervisor` | Site supervisor, Wellington |
| `mohamed.worker`, `alusine.worker` | Workers |
| `aminata.client` | Client of the Lumley project |

## For administrators

- Sign in with the account created by `createsuperuser`.
- Use the administrator dashboard to review pending companies, track platform activity, and open reports.
- In **Companies**, review submitted details and the protected registration document. Set the outcome and enter a note that will be sent to the applicant.
- In **Users**, suspend or reactivate user sign-in. A user's project and audit history is preserved.
- In **Complaints**, change the complaint status and leave an update for the reporter.
- In **Audit trail**, filter recorded business and administrator actions by module.
- The Django administration site is a read-only record viewer for platform data. Service catalog entries can be managed there; company verification and workforce actions should use the BuildLink portal.

## Account recovery

Password reset email delivery is not configured in this prototype. An administrator can help restore access through the configured deployment's account-management process. Do not share administrator credentials.
