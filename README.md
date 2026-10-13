# Tade Boxing Club — Monthly Subscription Management System

A production-ready monthly subscription and payment verification web application designed for **Tade Boxing Club** in Addis Ababa, Ethiopia (~30 members).

The application completely eliminates the coach's manual spreadsheet tracking and awkward back-and-forth conversations with members by providing a transparent public status board, mobile payment screenshot submissions, and an audit-logged coach verification dashboard.

---

## 1. Key Features

- **Automatic Monthly Cycle**:
  - The billing cycle begins on the 1st day of each month in `Africa/Addis_Ababa` (UTC+3).
  - Every active member defaults to **UNPAID** on the first day of every month.
  - Previous months' paid statuses remain intact and never roll over as paid into the new month.
  - Safe, on-demand dynamic record resolution prevents missing member states even if scheduled background jobs fail.
  - Idempotent management command `initialize_monthly_subscriptions`.
- **Public Club Accountability Board**:
  - Transparent monthly status divided into three clear groups: **Paid Members**, **Pending Verification**, and **Unpaid Members**.
  - Respectful and privacy-safe: Displays only boxer display names and statuses. Keeps receipts, phone numbers, and private rejection notes strictly private.
- **Member Self-Service Portal**:
  - Clean mobile-first dashboard displaying current month amount (1,500 ETB) and next payment deadline (5th of each month).
  - Direct payment instructions for **Commercial Bank of Ethiopia (CBE)** and **Telebirr**.
  - Drag-and-drop or camera mobile screenshot upload with live image preview.
  - Personal payment history with coach review timestamps and feedback reasons.
- **Coach / Administrative Portal**:
  - Quick KPI stats: Total Active Boxers, Paid, Unpaid, and Pending Verification.
  - Verification queue with high-res screenshot lightbox inspection.
  - Single-click **Approve** (marks member Paid) or **Reject** (requires rejection reason sent to the member).
  - Manual status override tool with required audit reason.
  - Boxer roster directory with search and status filtering (Paid, Unpaid, Pending, Inactive).
  - Historical monthly archive browser with collection percentages and ETB totals.
  - Immutable audit trail recording every administrative decision.

---

## 2. Technology Stack

- **Backend**: Python 3.12+ / 3.14 & Django 6.0
- **Frontend**: Server-rendered Django Templates with Tailwind CSS (Dark charcoal & Boxing Red theme)
- **Database**: Neon PostgreSQL via `dj-database-url` (with zero-config SQLite local development fallback)
- **Media & Proof Storage**: Cloudinary via `django-cloudinary-storage`
- **Static Asset Serving**: WhiteNoise
- **Timezone**: `Africa/Addis_Ababa`

---

## 3. Project Structure

```text
tadebox/
├── config/
│   ├── __init__.py
│   ├── asgi.py
│   ├── settings.py           # Production settings, Neon, Cloudinary, WhiteNoise
│   ├── urls.py               # Root URL configuration
│   └── wsgi.py               # WSGI entry point (exposes 'app' for Vercel)
├── subscriptions/
│   ├── migrations/           # Database migration files
│   ├── management/
│   │   └── commands/
│   │       ├── initialize_monthly_subscriptions.py  # Idempotent monthly initializer
│   │       └── seed_club_data.py                    # Seeds Coach + 30 Ethiopian boxers
│   ├── admin.py              # Django Admin model registrations
│   ├── apps.py
│   ├── context_processors.py # Injects club settings & current month into templates
│   ├── forms.py              # Payment upload, review, member edit & settings forms
│   ├── models.py             # MemberProfile, Subscription, PaymentSubmission, etc.
│   ├── tests.py              # Comprehensive test suite covering all 14 test cases
│   ├── urls.py               # App routes
│   ├── utils.py              # Timezone, date normalization, & atomic subscription logic
│   └── views.py              # Member portal & coach administration views
├── templates/
│   ├── base.html             # Base layout with responsive mobile navigation & theme
│   ├── registration/
│   │   └── login.html        # Clean login page with test credentials helper
│   └── subscriptions/
│       ├── access_denied.html
│       ├── club_status.html                # Public 3-column club status board
│       ├── coach_audit_logs.html           # Full administrative audit trail
│       ├── coach_club_settings.html        # Fee & bank accounts settings
│       ├── coach_dashboard.html            # Main coach KPI dashboard & urgent queue
│       ├── coach_historical_reports.html   # Monthly archive reports
│       ├── coach_manual_status_change.html # Status override with mandatory audit reason
│       ├── coach_member_create.html        # Register new boxer
│       ├── coach_member_detail.html        # Member profile & complete payment log
│       ├── coach_member_edit.html          # Update member profile
│       ├── coach_member_list.html          # Boxer directory with search & filters
│       ├── coach_pending_submissions.html  # Verification queue
│       ├── coach_review_submission.html    # Screenshot preview & decision form
│       ├── member_dashboard.html           # Member dashboard & deadline card
│       ├── member_history.html             # Member's own payment submissions
│       ├── member_payment_submit.html      # Transfer instructions & upload form
│       └── member_profile.html             # Member contact information
├── .env.example              # Environment variables template
├── .gitignore
├── build_files.sh            # Vercel build script
├── manage.py
├── requirements.txt
└── vercel.json               # Serverless deployment configuration
```

---

## 4. Local Installation & Setup

### Step 1: Clone and Enter Directory
```bash
git clone <repository-url>
cd tadebox
```

### Step 2: Create and Activate Virtual Environment
```bash
python -m venv venv

# Windows (PowerShell):
.\venv\Scripts\Activate.ps1

# Linux / macOS:
source venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
copy .env.example .env
```
Edit `.env` to configure your values:
- `SECRET_KEY`: A secure random secret key.
- `DEBUG`: `True` for local development.
- `DATABASE_URL`: (Optional) Neon PostgreSQL connection string. If left blank, SQLite (`db.sqlite3`) is used locally.
- `CLOUDINARY_*`: (Optional) Cloudinary credentials for uploaded screenshots. If omitted in local dev, local file storage is used automatically.

### Step 5: Run Database Migrations
```bash
python manage.py migrate
```

### Step 6: Seed Database with Club Data & 30 Members
Run the custom seeder to populate **Coach Tade**, club settings, and **30 Ethiopian boxers**:
```bash
python manage.py seed_club_data
```

This creates:
- **Coach Superuser Account**:
  - Username: `coach_tade`
  - Password: `boxing2026`
- **30 Realistic Club Boxers** (password: `boxer2026` for each member, e.g. `abebe_b`, `haile_g`, `kenenisa_b`, `derartu_t`).
- Historical records for September 2026 and current month records (14 Paid, 6 Pending with sample receipts, 10 Unpaid).

### Step 7: Run Local Development Server
```bash
python manage.py runserver
```
Visit `http://127.0.0.1:8000/` in your browser.

---

## 5. How the Automatic Monthly Unpaid Logic Works

1. **Normalized Billing Identifier**:
   Every subscription record is keyed by the member and the normalized 1st calendar day of the month (`YYYY-MM-01`).
   The timezone is configured as `Africa/Addis_Ababa` (UTC+3).

2. **Uniqueness Constraint**:
   A database-level `UniqueConstraint(fields=['member', 'subscription_month'])` prevents duplicate subscriptions for any member in a given month.

3. **Dynamic Zero-Cron Resilience**:
   The application does not depend exclusively on a midnight cron job.
   When any member opens their dashboard or the public club board is loaded:
   - The system checks for a subscription record matching `member` and the current month (`YYYY-MM-01`).
   - If no record exists, `ensure_subscription_for_member()` atomically creates one with status `UNPAID`.
   - Therefore, on November 1st, a member who paid in October will immediately show as `UNPAID` for November, while October's record remains `PAID` and untouched.

4. **Idempotent Management Command**:
   ```bash
   python manage.py initialize_monthly_subscriptions
   ```
   Or for a specific month:
   ```bash
   python manage.py initialize_monthly_subscriptions --month=2026-11
   ```
   This command scans all active members (`is_active_member=True`), creates any missing records with status `UNPAID`, and logs the action in the audit trail. Running it repeatedly will never duplicate records or overwrite verified payments.

---

## 6. Neon PostgreSQL & Cloudinary Setup

### Neon PostgreSQL Setup
1. Create a free PostgreSQL database at [neon.tech](https://neon.tech).
2. Copy the connection string (with SSL mode enabled):
   ```text
   DATABASE_URL=postgresql://user:password@ep-sample-12345.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
3. Place this URL in your `.env` file or hosting provider's environment variables.
4. Run `python manage.py migrate`.

### Cloudinary Image Storage Setup
1. Create a free account at [cloudinary.com](https://cloudinary.com).
2. In the Cloudinary Console Dashboard, copy your **Cloud Name**, **API Key**, and **API Secret**.
3. Add them to `.env`:
   ```env
   CLOUDINARY_CLOUD_NAME=my_cloud_name
   CLOUDINARY_API_KEY=123456789012345
   CLOUDINARY_API_SECRET=abcdefghijklmnopqrstuvwxyz123
   ```
4. All payment screenshots uploaded by boxers will automatically be stored securely on Cloudinary CDN.

---

## 7. Deployment Instructions & Vercel Considerations

The project includes `vercel.json`, `build_files.sh`, and `config/wsgi.py` (with `app = application`).

### Vercel Deployment Steps:
1. Push your repository to GitHub.
2. In the Vercel Dashboard, select **Add New Project** and import the repository.
3. In **Settings -> Environment Variables**, configure:
   - `SECRET_KEY`
   - `DEBUG=False`
   - `ALLOWED_HOSTS=.vercel.app`
   - `DATABASE_URL` (Your Neon connection string)
   - `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`
   - `CSRF_TRUSTED_ORIGINS=https://*.vercel.app`
4. Deploy the application.

### Important Hosting / Serverless Notes:
- **Serverless Ephemeral Filesystem**: Vercel serverless functions have ephemeral local filesystems. Uploaded screenshots must use **Cloudinary** (configured in settings).
- **Scheduled Monthly Cron**: While the application dynamically defaults missing records to unpaid on access, you can run `initialize_monthly_subscriptions` on the 1st of each month via Vercel Cron Jobs (`vercel.json` cron expression `0 0 1 * *`), GitHub Actions schedule, or a free cron trigger service calling a protected endpoint or CLI.

---

## 8. Test Suite & Results

All 14 specified test cases are implemented in `subscriptions/tests.py`:

```bash
python manage.py test
```

### Verified Test Cases:
1. `test_new_member_is_unpaid_for_current_month` — Verified.
2. `test_member_paid_in_october_is_unpaid_in_november` — Verified.
3. `test_october_paid_record_unchanged_after_november_begins` — Verified.
4. `test_missing_monthly_records_created_safely` — Verified.
5. `test_running_initialize_command_twice_creates_no_duplicates` — Verified.
6. `test_approved_payment_submission_marks_subscription_paid` — Verified.
7. `test_rejected_submissions_do_not_mark_paid` — Verified.
8. `test_member_cannot_approve_own_payment` — Verified (returns 403).
9. `test_member_cannot_view_another_member_private_screenshot` — Verified (returns 403).
10. `test_ordinary_member_cannot_access_coach_views` — Verified (returns 403).
11. `test_inactive_members_handled_consistently` — Verified (excluded from monthly cycles).
12. `test_simultaneous_initialization_no_duplicate_subscriptions` — Verified (unique constraint enforcement).
13. `test_status_list_correctly_separates_paid_unpaid_pending` — Verified.
14. `test_payment_history_correctly_scoped_to_month` — Verified.

**Result: 14/14 Tests Passed (100% OK).**
