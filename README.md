# DeskGuard — Library Seat Reservation & Guard System

DeskGuard is an intelligent, real-time library seat booking and monitoring dashboard. Built with a premium, responsive glassmorphic design, it provides students with a seamless check-in experience and librarians with full floor visibility, authoritative timers, presence verification workflows, and logout grace-period controls.

---

## Key Features

- **Interactive Seat Map**: Live interactive library floor layout with hot-reload status polling.
- **Authoritative Server-Side Timers**: Real-time session timing (2 hours) and away/break tracking (20 minutes) authoritatively tracked in the database, independent of the browser.
- **Single-Seat Allocation**: Enforces strict database-level checks preventing a student from reserving more than one seat at a time.
- **Presence Verification**: Quick confirmation prompts ("Still Here?") to ensure users are active, flagging idle seats as "Abandoned" for librarian resets.
- **Logout Grace Period**: When students log out, their seat enters a 5-minute "Pending Release" state with a live countdown ticker, allowing the librarian to cancel the release (if the student is physically there) or let it auto-release. Logging back in before expiry automatically restores the session.
- **Printable QR Codes**: Automatic printable QR codes for easy desk scanning.


---

## Tech Stack

- **Backend**: Python 3.12, Django 5.x, Django ORM
- **Database**: SQLite (Local Dev) / PostgreSQL (Production)
- **Frontend**: Vanilla JavaScript, Bootstrap 5.3, Bootstrap Icons
- **Production Server**: Gunicorn & WhiteNoise (static files compiler)

---

## Running Locally

Follow these steps to set up and run DeskGuard on your local machine:

### 1. Clone the repository
```bash
git clone <your-repo-link>
cd Webforge
```

### 2. Set up virtual environment (optional but recommended)
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Apply Database Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Start the Development Server
```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/` in your browser.

---

## Demo Credentials

The database is pre-seeded with a default test student. The librarian admin is built into the authentication layer.

* **Student Account**:
  - **Card Number**: `WF-26-0001`
  - **Password**: `iron`
* **Librarian Account**:
  - **Card Number**: `WF-26-LB01`
  - **Password**: `librarian123`

---
