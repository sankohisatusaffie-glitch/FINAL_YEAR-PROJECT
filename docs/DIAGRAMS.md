# System diagrams

These diagrams are written in [Mermaid](https://mermaid.js.org/) and render directly on GitHub and in most Markdown viewers (VS Code: install a Mermaid preview extension). To export images for a report, paste a diagram into the [Mermaid Live Editor](https://mermaid.live) and download it as PNG or SVG.

## 1. System architecture

BuildLink is a single server-rendered Django application. Browsers talk to it over HTTPS; phones on site also use a service worker and a local IndexedDB queue so attendance and reports keep working without signal.

```mermaid
flowchart LR
    subgraph Devices["User devices"]
        Office["Office browser<br/>admin · company · project manager"]
        Phone["Site phone<br/>supervisor · worker"]
        Client["Client browser<br/>read-only progress"]
    end

    subgraph PhoneRuntime["On the phone"]
        SW["Service worker<br/>sw.js — caches site tools"]
        Queue[("IndexedDB queue<br/>offline clock-ins & reports")]
        Sensors["GPS · camera<br/>BarcodeDetector"]
    end

    subgraph Server["Django 5.2 application (coordination app)"]
        Views["Views<br/>views.py · operations_views.py"]
        Access["Access rules<br/>access.py"]
        Services["Business rules<br/>services.py"]
        Channels["Messaging & payments<br/>messaging.py · payments.py"]
        Templates["Templates + static CSS/JS"]
    end

    DB[("Relational database<br/>SQLite (dev) / PostgreSQL")]
    Files[("Private uploads<br/>photos, documents")]
    SMS["SMS provider<br/>(sandbox backend)"]
    Mail["Email server<br/>(console backend in dev)"]
    MoMo["Orange Money / Afrimoney<br/>(sandbox gateway)"]

    Office --> Views
    Client --> Views
    Phone --> SW --> Views
    Phone --- Sensors
    Phone --- Queue
    Queue -. "syncs when online" .-> Views
    Views --> Access
    Views --> Services --> DB
    Views --> Templates
    Services --> Files
    Views --> Channels
    Channels --> SMS
    Channels --> Mail
    Channels --> MoMo
```

## 2. Use cases

```mermaid
flowchart LR
    Admin(["👤 Administrator"])
    PM(["👤 Project manager<br/>(company owner or manager)"])
    Sup(["👤 Site supervisor"])
    Worker(["👤 Worker"])
    Client(["👤 Client"])

    subgraph System["BuildLink"]
        UC1["Verify companies & manage users"]
        UC2["View audit trail & platform reports"]
        UC3["Create projects, sites, phases, budget"]
        UC4["Manage workers, skills, daily rates"]
        UC5["Create team accounts"]
        UC6["Approve material & equipment requests"]
        UC7["Run payroll & pay by mobile money"]
        UC8["View dashboard & reports"]
        UC9["Create and assign tasks<br/>(skill-based suggestions)"]
        UC10["Scan QR cards / record attendance"]
        UC11["Submit daily site report with photos"]
        UC12["Report safety incident"]
        UC13["Request materials or equipment"]
        UC14["Post announcement (in-app, SMS, email)"]
        UC15["Clock in / out by GPS"]
        UC16["View own tasks, hours and payslips"]
        UC17["View project progress (read-only)"]
        UC18["Request a service & review the company"]
    end

    Admin --- UC1 & UC2
    PM --- UC3 & UC4 & UC5 & UC6 & UC7 & UC8 & UC9 & UC14
    Sup --- UC9 & UC10 & UC11 & UC12 & UC13 & UC14
    Worker --- UC15 & UC16 & UC12
    Client --- UC17 & UC18
```

## 3. Entity-relationship diagram

Primary keys are `id` (auto-increment) on every table. `User.role` holds the role; roles are a fixed set of choices rather than a separate table, because each role's permissions are defined in code (`coordination/access.py`), not configured at runtime.

```mermaid
erDiagram
    USER ||--o| COMPANY : "owns (company role)"
    COMPANY ||--o{ USER : "employs (manager / supervisor / worker roles)"
    USER ||--o| CLIENT_PROFILE : has
    COMPANY ||--o{ WORKER : "keeps records of"
    WORKER |o--o| USER : "signs in as"
    WORKER }o--o{ SKILL : has
    COMPANY }o--o{ SERVICE : offers
    USER ||--o{ SERVICE_REQUEST : "submits (client)"
    COMPANY ||--o{ SERVICE_REQUEST : receives
    SERVICE_REQUEST |o--o| PROJECT : "becomes"
    COMPANY ||--o{ PROJECT : runs
    USER |o--o{ PROJECT : "client of"
    USER |o--o{ PROJECT : "manages"
    PROJECT }o--o{ USER : "supervised by"
    PROJECT ||--o{ SITE : has
    PROJECT ||--o{ PHASE : "has (incl. milestones)"
    PROJECT ||--o{ TASK : has
    PHASE |o--o{ TASK : groups
    SITE |o--o{ TASK : "located at"
    SKILL |o--o{ TASK : requires
    TASK ||--o{ TASK_ASSIGNMENT : has
    WORKER ||--o{ TASK_ASSIGNMENT : "assigned via"
    PROJECT ||--o{ WORKER_ASSIGNMENT : "team"
    WORKER ||--o{ WORKER_ASSIGNMENT : "member via"
    WORKER ||--o{ ATTENDANCE : records
    PROJECT ||--o{ ATTENDANCE : "worked on"
    SITE |o--o{ ATTENDANCE : "checked in at"
    PROJECT ||--o{ SITE_REPORT : has
    SITE_REPORT ||--o{ REPORT_PHOTO : has
    TASK |o--o{ SITE_REPORT : "progress updated by"
    PROJECT ||--o{ INCIDENT : has
    INCIDENT }o--o{ WORKER : involves
    PROJECT ||--o{ MATERIAL_REQUEST : has
    WORKER ||--o{ PAYROLL_RECORD : "paid via"
    COMPANY ||--o{ PAYROLL_RECORD : issues
    PAYROLL_RECORD ||--o{ MOBILE_MONEY_TRANSACTION : "paid by"
    COMPANY ||--o{ ANNOUNCEMENT : posts
    PROJECT |o--o{ ANNOUNCEMENT : "targets"
    USER ||--o{ NOTIFICATION : receives
    USER |o--o{ OUTBOUND_MESSAGE : "SMS / email to"
    USER |o--o{ AUDIT_LOG : "acted in"
    PROJECT ||--o| RATING : "rated by client"
    SERVICE_REQUEST ||--o{ MESSAGE : conversation

    USER {
        bigint id PK
        string username UK
        string email UK
        string role "admin|company|client|manager|supervisor|worker"
        bigint employer_id FK
        string phone
        string password "PBKDF2 hash"
    }
    COMPANY {
        bigint id PK
        bigint owner_id FK
        string name
        string registration_number UK
        string status
    }
    WORKER {
        bigint id PK
        bigint company_id FK
        string worker_id "unique per company"
        string primary_trade
        decimal daily_rate "SLE per 8 h"
        string qr_token UK
        bigint user_id FK
        string mobile_money_provider
        string mobile_money_number
    }
    SKILL {
        bigint id PK
        string name UK
        string category
    }
    PROJECT {
        bigint id PK
        bigint company_id FK
        bigint request_id FK "nullable"
        bigint client_id FK "nullable"
        bigint manager_id FK
        decimal budget
        date start_date
        date expected_end_date
        string status
        int progress_percentage
    }
    SITE {
        bigint id PK
        bigint project_id FK
        decimal latitude
        decimal longitude
        int radius_m
        bool enforce_geofence
    }
    PHASE {
        bigint id PK
        bigint project_id FK
        int order
        bool is_milestone
        string status
    }
    TASK {
        bigint id PK
        bigint project_id FK
        bigint phase_id FK
        bigint site_id FK
        bigint required_skill_id FK
        string priority
        string status "pending|in_progress|completed"
        int progress_percentage
        date due_date
    }
    TASK_ASSIGNMENT {
        bigint id PK
        bigint task_id FK
        bigint worker_id FK "unique with task"
    }
    WORKER_ASSIGNMENT {
        bigint id PK
        bigint project_id FK
        bigint worker_id FK
        string status
    }
    ATTENDANCE {
        bigint id PK
        bigint worker_id FK "unique with date"
        bigint project_id FK
        bigint site_id FK
        date date
        datetime clock_in
        datetime clock_out
        decimal hours_worked
        string method "gps|qr|manual"
        string status "present|absent|leave|sick"
        uuid client_uuid UK "offline de-dup"
    }
    SITE_REPORT {
        bigint id PK
        bigint project_id FK
        bigint author_id FK
        bigint task_id FK
        date date
        int task_progress
        uuid client_uuid UK
    }
    REPORT_PHOTO {
        bigint id PK
        bigint report_id FK
        file image "private storage"
    }
    INCIDENT {
        bigint id PK
        bigint project_id FK
        string kind
        string severity
        bool ppe_compliant
        string status
    }
    MATERIAL_REQUEST {
        bigint id PK
        bigint project_id FK
        bigint requested_by_id FK
        bigint reviewed_by_id FK
        decimal quantity
        decimal estimated_cost
        string status "pending|approved|rejected|delivered"
    }
    PAYROLL_RECORD {
        bigint id PK
        bigint worker_id FK "unique with period"
        date period_start
        date period_end
        decimal hours_worked
        decimal gross_pay
        decimal net_pay
        string status "draft|approved|paid"
    }
    MOBILE_MONEY_TRANSACTION {
        bigint id PK
        bigint payroll_id FK
        string provider
        string reference UK
        string status
    }
    ANNOUNCEMENT {
        bigint id PK
        bigint company_id FK
        bigint project_id FK
        bool send_sms
        bool send_email
    }
    OUTBOUND_MESSAGE {
        bigint id PK
        string channel "sms|email"
        string recipient
        string status
    }
    AUDIT_LOG {
        bigint id PK
        bigint user_id FK
        string action
        string module
        datetime created_at
    }
```

Tables carried over unchanged from release 1 (`Service`, `ClientProfile`, `ServiceRequest`, `Rating`, `Message`, `Notification`, `Complaint`) keep the columns described in `TECHNICAL_DESIGN.md`.

## 4. Activity: worker clocks in (GPS or QR)

```mermaid
flowchart TD
    A([Worker arrives on site]) --> B{Has a smartphone<br/>with a BuildLink login?}
    B -- Yes --> C[Open Clock in / out<br/>tap the button]
    B -- No --> D[Supervisor scans the<br/>worker's QR ID card]
    C --> E[Phone reads GPS position]
    D --> E2[Supervisor's phone reads GPS position]
    E --> F{Online?}
    E2 --> F
    F -- No --> Q[Save to IndexedDB queue<br/>with time and a unique id] --> R([Synced automatically<br/>when signal returns]) --> G
    F -- Yes --> G[Server checks: active worker,<br/>on project team, project open]
    G -- Fails --> X([Show reason, nothing saved])
    G -- OK --> H{Site boundary enforced?}
    H -- Yes --> I{Within radius and<br/>accuracy ≤ 100 m?}
    I -- No --> X
    I -- Yes --> J
    H -- No --> J{Already clocked in today?}
    J -- No --> K[Create attendance record<br/>status present, clock-in time, distance]
    J -- Yes, open --> L[Set clock-out<br/>hours = out − in, capped at 16 h]
    J -- Yes, closed --> X
    K --> M([Audit log entry<br/>dashboard shows worker on site])
    L --> M
```

## 5. Activity: payroll and mobile money payment

```mermaid
flowchart TD
    A([Manager opens Payroll]) --> B[Choose pay period<br/>maximum one month]
    B --> C[Sum present hours per worker<br/>from attendance]
    C --> D[Gross = hours ÷ 8 × daily rate<br/>save or refresh DRAFT payslips]
    D --> E{Approved or paid<br/>payslip already exists?}
    E -- Yes --> F[Leave it unchanged]
    E -- No --> G[Manager reviews payslip<br/>adds deductions]
    G --> H[Approve payslip]
    H --> I[Worker notified; payslip visible<br/>and printable as PDF]
    H --> J{Worker has mobile money<br/>provider and number?}
    J -- No --> K([Add details to worker record])
    J -- Yes --> L[Send net pay via Orange Money<br/>or Afrimoney gateway]
    L --> M{Gateway result}
    M -- Success --> N[Payslip marked PAID<br/>reference stored, SMS receipt to worker]
    M -- Failure --> O[Transaction FAILED<br/>payslip stays approved for retry]
    N --> P([Audit log entry])
    O --> P
```

## 6. Activity: material or equipment request

```mermaid
flowchart TD
    A([Supervisor needs materials]) --> B[Fill request: item, quantity,<br/>unit, needed-by date, cost, reason]
    B --> C{Requester is the<br/>project manager?}
    C -- Yes --> D[Recorded as APPROVED]
    C -- No --> E[Status PENDING<br/>manager alerted in-app and by SMS]
    E --> F{Manager decision}
    F -- Approve --> D
    F -- Reject --> G[Reason required<br/>status REJECTED]
    D --> H[Requester notified]
    G --> H
    D --> I[Goods arrive on site]
    I --> J[Manager marks DELIVERED]
    J --> K([Audit log entry])
    H --> K
```

## 7. Activity: offline sync

```mermaid
sequenceDiagram
    participant U as Supervisor / worker
    participant P as Page (operations.js)
    participant Q as IndexedDB queue
    participant S as Server
    U->>P: Submit clock-in or daily report
    alt No connection
        P->>Q: Store fields, photos, time, client_uuid, user id
        P-->>U: "Saved on this phone"
    else Online
        P->>S: POST (XMLHttpRequest)
        S-->>P: JSON result
    end
    Note over P,Q: On reconnect, on page load and every 60 s
    P->>Q: Read entries for the signed-in user only
    P->>S: Replay with offline=1 and original capture time
    S->>S: Ignore if client_uuid already stored
    S-->>P: ok / rejected with a reason
    P->>Q: Remove entry
    P-->>U: Toast: synced, or why it was rejected
```

## 8. Demo flow

```mermaid
flowchart LR
    A[Manager creates project<br/>with site, phase, supervisor] --> B[Supervisor creates task<br/>and assigns the top suggested worker]
    B --> C[Worker clocks in by GPS<br/>inside the site boundary]
    C --> D[Supervisor submits daily report<br/>task progress → 60%]
    D --> E[Manager dashboard: workforce on site,<br/>progress, attendance trend, labour cost]
```
