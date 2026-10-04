from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import sqlite3
import os
import re

app = Flask(__name__)
app.secret_key = 'event_registration_secret_key_2024_secure'
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'events.db')

EVENT_TYPES = [
    'Cultural Event', 'Technical Event', 'Sports Event', 'Academic Event',
    'Workshop', 'Seminar', 'Fest', 'Career Event', 'Other'
]

# --------------- Database Helpers ---------------

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=()):
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid


def table_exists(table_name):
    result = query_db(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,), one=True
    )
    return result is not None


def column_exists(table_name, column_name):
    try:
        cols = query_db(f"PRAGMA table_info({table_name})")
        return any(c['name'] == column_name for c in cols)
    except Exception:
        return False


def init_db():
    """Create tables if they don't exist and migrate missing columns safely."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Admins table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')

    # Events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'Other',
            event_date TEXT NOT NULL,
            registration_deadline TEXT NOT NULL,
            venue TEXT NOT NULL DEFAULT '',
            capacity INTEGER NOT NULL DEFAULT 100,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Open',
            team_size INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Registrations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            registration_date TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'Registered',
            team_members TEXT DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    ''')

    # Migrate missing columns for events (safe for existing DB)
    event_columns = {
        'event_type': "TEXT NOT NULL DEFAULT 'Other'",
        'venue': "TEXT NOT NULL DEFAULT ''",
        'capacity': "INTEGER NOT NULL DEFAULT 100",
        'description': "TEXT DEFAULT ''",
        'status': "TEXT NOT NULL DEFAULT 'Open'",
        'created_at': "TEXT DEFAULT CURRENT_TIMESTAMP",
        'registration_deadline': "TEXT NOT NULL DEFAULT ''",
        'event_date': "TEXT NOT NULL DEFAULT ''",
        'event_name': "TEXT NOT NULL DEFAULT ''",
        'team_size': "INTEGER NOT NULL DEFAULT 1"
    }
    for col, definition in event_columns.items():
        try:
            cursor.execute(f"SELECT {col} FROM events LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute(f"ALTER TABLE events ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass

    # Migrate users columns
    user_columns = {
        'phone': "TEXT",
        'created_at': "TEXT DEFAULT CURRENT_TIMESTAMP",
        'name': "TEXT NOT NULL DEFAULT ''",
        'email': "TEXT UNIQUE NOT NULL DEFAULT ''",
        'password': "TEXT NOT NULL DEFAULT ''"
    }
    for col, definition in user_columns.items():
        try:
            cursor.execute(f"SELECT {col} FROM users LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass

    # Migrate registrations columns
    reg_columns = {
        'status': "TEXT NOT NULL DEFAULT 'Registered'",
        'registration_date': "TEXT DEFAULT CURRENT_TIMESTAMP",
        'name': "TEXT NOT NULL DEFAULT ''",
        'email': "TEXT NOT NULL DEFAULT ''",
        'phone': "TEXT",
        'user_id': "INTEGER",
        'event_id': "INTEGER",
        'team_members': "TEXT DEFAULT ''"
    }
    for col, definition in reg_columns.items():
        try:
            cursor.execute(f"SELECT {col} FROM registrations LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute(f"ALTER TABLE registrations ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass

    # Create default admin if not exists
    admin = cursor.execute(
        "SELECT id FROM admins WHERE email = ?", ('admin@gmail.com',)
    ).fetchone()
    if not admin:
        hashed = generate_password_hash('admin123')
        cursor.execute(
            "INSERT INTO admins (name, email, password) VALUES (?, ?, ?)",
            ('Administrator', 'admin@gmail.com', hashed)
        )

    db.commit()
    db.close()


def update_event_statuses():
    """Automatically mark events as Expired if deadline or event date has passed."""
    today = date.today().isoformat()
    db = get_db()
    # Close events past registration deadline but still open
    db.execute(
        """UPDATE events SET status = 'Expired'
           WHERE status = 'Open' AND registration_deadline < ?""",
        (today,)
    )
    # Also expire if event date has passed
    db.execute(
        """UPDATE events SET status = 'Expired'
           WHERE status IN ('Open', 'Closed') AND event_date < ?""",
        (today,)
    )
    db.commit()


# --------------- Validation Helpers ---------------

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def is_valid_phone(phone):
    if not phone:
        return False
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    return cleaned.isdigit() and 7 <= len(cleaned) <= 15

def has_name_with_initial(name):
    """Accept full name (First Last) or name with initial (e.g. Rahul K / Rahul K. Sharma).
    Must have at least two parts so people with the same first name can be distinguished.
    """
    if not name or not name.strip():
        return False
    parts = name.strip().split()
    # Need at least first name + last name OR first name + initial
    if len(parts) < 2:
        return False
    # Each part should contain letters
    return all(any(c.isalpha() for c in p) for p in parts)


def normalize_name(name):
    return ' '.join(name.strip().lower().split())


def get_registered_names_for_event(event_id):
    """Return set of normalized names already registered for an event (lead + team members)."""
    regs = query_db(
        "SELECT name, team_members FROM registrations WHERE event_id = ? AND status = 'Registered'",
        (event_id,)
    )
    names = set()
    for r in regs:
        if r['name']:
            names.add(normalize_name(r['name']))
        if r['team_members']:
            for m in r['team_members'].split(','):
                m = m.strip()
                if m:
                    names.add(normalize_name(m))
    return names



def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Admin access required.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def get_registered_count(event_id):
    row = query_db(
        "SELECT COUNT(*) as cnt FROM registrations WHERE event_id = ? AND status = 'Registered'",
        (event_id,), one=True
    )
    return row['cnt'] if row else 0


def get_available_seats(event):
    registered = get_registered_count(event['id'])
    return max(0, event['capacity'] - registered)


# --------------- Routes: Public / User ---------------

@app.route('/')
def index():
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    if 'user_id' in session:
        return redirect(url_for('user_dashboard'))
    return render_template('landing.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'user_id' in session:
        return redirect(url_for('user_dashboard'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        errors = []
        if not name:
            errors.append('Full name is required.')
        if not email:
            errors.append('Email is required.')
        elif not is_valid_email(email):
            errors.append('Invalid email format.')
        if not phone:
            errors.append('Phone number is required.')
        elif not is_valid_phone(phone):
            errors.append('Invalid phone number.')
        if not password:
            errors.append('Password is required.')
        elif len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('signup.html', name=name, email=email, phone=phone)

        existing = query_db("SELECT id FROM users WHERE email = ?", (email,), one=True)
        if existing:
            flash('Email already exists. Please login or use another email.', 'error')
            return render_template('signup.html', name=name, email=email, phone=phone)

        hashed = generate_password_hash(password)
        try:
            execute_db(
                "INSERT INTO users (name, email, password, phone, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, email, hashed, phone, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            )
            flash('Signup successful! Please login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already exists.', 'error')
            return render_template('signup.html', name=name, email=email, phone=phone)

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('user_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Email and password are required.', 'error')
            return render_template('login.html', email=email)

        user = query_db("SELECT * FROM users WHERE email = ?", (email,), one=True)
        if user and check_password_hash(user['password'], password):
            session.clear()
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            flash('Login successful!', 'success')
            return redirect(url_for('user_dashboard'))
        else:
            flash('Invalid email or password.', 'error')
            return render_template('login.html', email=email)

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


@app.route('/user-dashboard')
@login_required
def user_dashboard():
    update_event_statuses()
    today = date.today().isoformat()
    total_open = query_db(
        "SELECT COUNT(*) as cnt FROM events WHERE status = 'Open'", one=True
    )['cnt']
    my_regs = query_db(
        "SELECT COUNT(*) as cnt FROM registrations WHERE user_id = ? AND status = 'Registered'",
        (session['user_id'],), one=True
    )['cnt']
    upcoming = query_db(
        "SELECT COUNT(*) as cnt FROM events WHERE status = 'Open' AND event_date >= ?",
        (today,), one=True
    )['cnt']
    completed = query_db(
        "SELECT COUNT(*) as cnt FROM events WHERE status = 'Expired' OR event_date < ?",
        (today,), one=True
    )['cnt']

    recent_events = query_db(
        "SELECT * FROM events WHERE status = 'Open' ORDER BY event_date ASC LIMIT 5"
    )
    return render_template(
        'user_dashboard.html',
        total_open=total_open,
        my_regs=my_regs,
        upcoming=upcoming,
        completed=completed,
        recent_events=recent_events
    )


@app.route('/events')
@login_required
def events():
    update_event_statuses()
    search = request.args.get('search', '').strip()
    event_type = request.args.get('type', 'All')
    status_filter = request.args.get('status', 'Open')

    query = "SELECT * FROM events WHERE 1=1"
    params = []

    if search:
        query += " AND (event_name LIKE ? OR event_type LIKE ? OR venue LIKE ?)"
        like = f'%{search}%'
        params.extend([like, like, like])

    if event_type and event_type != 'All':
        query += " AND event_type = ?"
        params.append(event_type)

    if status_filter and status_filter != 'All':
        query += " AND status = ?"
        params.append(status_filter)

    query += " ORDER BY event_date ASC"
    events_list = query_db(query, params)

    events_with_seats = []
    for e in events_list:
        e_dict = dict(e)
        e_dict['registered'] = get_registered_count(e['id'])
        e_dict['available'] = get_available_seats(e)
        events_with_seats.append(e_dict)

    return render_template(
        'events.html',
        events=events_with_seats,
        search=search,
        selected_type=event_type,
        selected_status=status_filter,
        event_types=EVENT_TYPES
    )


@app.route('/event/<int:event_id>')
@login_required
def event_details(event_id):
    update_event_statuses()
    event = query_db("SELECT * FROM events WHERE id = ?", (event_id,), one=True)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('events'))

    registered = get_registered_count(event_id)
    available = get_available_seats(event)
    already_registered = query_db(
        "SELECT id FROM registrations WHERE user_id = ? AND event_id = ? AND status = 'Registered'",
        (session['user_id'], event_id), one=True
    )

    today = date.today().isoformat()
    deadline_passed = event['registration_deadline'] < today
    is_full = available <= 0
    can_register = (
        event['status'] == 'Open'
        and not deadline_passed
        and not is_full
        and not already_registered
    )

    try:
        team_size = int(event['team_size']) if event['team_size'] else 1
    except (KeyError, TypeError, ValueError):
        team_size = 1

    return render_template(
        'event_details.html',
        event=event,
        registered=registered,
        available=available,
        already_registered=bool(already_registered),
        deadline_passed=deadline_passed,
        is_full=is_full,
        can_register=can_register,
        team_size=team_size
    )


@app.route('/register/<int:event_id>', methods=['POST'])
@login_required
def register_event(event_id):
    update_event_statuses()
    event = query_db("SELECT * FROM events WHERE id = ?", (event_id,), one=True)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('events'))

    if event['status'] != 'Open':
        flash('Registration is closed for this event.', 'error')
        return redirect(url_for('event_details', event_id=event_id))

    today = date.today().isoformat()
    if event['registration_deadline'] < today:
        flash('Registration deadline has passed. Registration closed.', 'error')
        return redirect(url_for('event_details', event_id=event_id))

    available = get_available_seats(event)
    if available <= 0:
        flash('Event is full. No seats available.', 'error')
        return redirect(url_for('event_details', event_id=event_id))

    existing = query_db(
        "SELECT id FROM registrations WHERE user_id = ? AND event_id = ? AND status = 'Registered'",
        (session['user_id'], event_id), one=True
    )
    if existing:
        flash('You have already registered for this event.', 'error')
        return redirect(url_for('event_details', event_id=event_id))

    user = query_db("SELECT * FROM users WHERE id = ?", (session['user_id'],), one=True)

    # Team size (default 1 for older events without the column)
    try:
        max_team = int(event['team_size']) if event['team_size'] else 1
    except (KeyError, TypeError, ValueError):
        max_team = 1

    # Lead participant name (must include initial)
    lead_name = request.form.get('lead_name', '').strip()
    if not lead_name:
        lead_name = user['name']

    if not has_name_with_initial(lead_name):
        flash('Enter your full name (e.g. "Udtha Ramitha" or "Rahul K"). At least first and last name required.', 'error')
        return redirect(url_for('event_details', event_id=event_id))

    team_members = []
    if max_team > 1:
        for i in range(2, max_team + 1):
            member = request.form.get(f'team_member_{i}', '').strip()
            if member:
                if not has_name_with_initial(member):
                    flash(f'Team member {i}: enter full name (e.g. "Priya Sharma" or "Priya S").', 'error')
                    return redirect(url_for('event_details', event_id=event_id))
                team_members.append(member)

        # If team event, require exactly max_team names (lead + members)
        if len(team_members) + 1 != max_team:
            flash(f'This event requires a team of {max_team}. Please enter all {max_team} member names with initials.', 'error')
            return redirect(url_for('event_details', event_id=event_id))

    # Collect all names for this registration
    all_names = [lead_name] + team_members

    # Check duplicates within the submitted team
    normalized_submitted = [normalize_name(n) for n in all_names]
    if len(normalized_submitted) != len(set(normalized_submitted)):
        flash('Duplicate names in your team are not allowed. Use full name with initials.', 'error')
        return redirect(url_for('event_details', event_id=event_id))

    # Check against already registered names for this event
    existing_names = get_registered_names_for_event(event_id)
    for n in all_names:
        if normalize_name(n) in existing_names:
            flash(f'Name "{n}" is already registered for this event. Use a different name with initials.', 'error')
            return redirect(url_for('event_details', event_id=event_id))

    team_members_str = ', '.join(team_members) if team_members else ''

    execute_db(
        """INSERT INTO registrations
           (user_id, event_id, name, email, phone, registration_date, status, team_members)
           VALUES (?, ?, ?, ?, ?, ?, 'Registered', ?)""",
        (user['id'], event_id, lead_name, user['email'], user['phone'],
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), team_members_str)
    )
    flash('Registration successful!', 'success')
    return redirect(url_for('registration_confirmation', event_id=event_id))


@app.route('/registration-confirmation/<int:event_id>')
@login_required
def registration_confirmation(event_id):
    event = query_db("SELECT * FROM events WHERE id = ?", (event_id,), one=True)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('events'))
    reg = query_db(
        """SELECT * FROM registrations WHERE user_id = ? AND event_id = ?
           AND status = 'Registered' ORDER BY id DESC""",
        (session['user_id'], event_id), one=True
    )
    return render_template('registration_confirmation.html', event=event, registration=reg)


@app.route('/my-registrations')
@login_required
def my_registrations():
    regs = query_db(
        """SELECT r.*, e.event_name, e.event_type, e.event_date, e.venue, e.status as event_status
           FROM registrations r
           JOIN events e ON r.event_id = e.id
           WHERE r.user_id = ?
           ORDER BY r.registration_date DESC""",
        (session['user_id'],)
    )
    return render_template('my_registrations.html', registrations=regs)


@app.route('/cancel-registration/<int:registration_id>', methods=['POST'])
@login_required
def cancel_registration(registration_id):
    reg = query_db(
        "SELECT * FROM registrations WHERE id = ? AND user_id = ?",
        (registration_id, session['user_id']), one=True
    )
    if not reg:
        flash('Registration not found.', 'error')
        return redirect(url_for('my_registrations'))

    if reg['status'] == 'Cancelled':
        flash('Registration is already cancelled.', 'error')
        return redirect(url_for('my_registrations'))

    execute_db(
        "UPDATE registrations SET status = 'Cancelled' WHERE id = ?",
        (registration_id,)
    )
    flash('Registration cancelled successfully.', 'success')
    return redirect(url_for('my_registrations'))


@app.route('/profile')
@login_required
def profile():
    user = query_db("SELECT * FROM users WHERE id = ?", (session['user_id'],), one=True)
    reg_count = query_db(
        "SELECT COUNT(*) as cnt FROM registrations WHERE user_id = ? AND status = 'Registered'",
        (session['user_id'],), one=True
    )['cnt']
    return render_template('profile.html', user=user, reg_count=reg_count)


# --------------- Admin Routes ---------------

@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Email and password are required.', 'error')
            return render_template('admin_login.html', email=email)

        admin = query_db("SELECT * FROM admins WHERE email = ?", (email,), one=True)
        if admin and check_password_hash(admin['password'], password):
            session.clear()
            session['admin_id'] = admin['id']
            session['admin_name'] = admin['name']
            session['admin_email'] = admin['email']
            flash('Admin login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid admin credentials.', 'error')
            return render_template('admin_login.html', email=email)

    return render_template('admin_login.html')


@app.route('/admin-logout')
def admin_logout():
    session.clear()
    flash('Admin logged out successfully.', 'success')
    return redirect(url_for('admin_login'))


@app.route('/admin-dashboard')
@admin_required
def admin_dashboard():
    update_event_statuses()
    total_users = query_db("SELECT COUNT(*) as cnt FROM users", one=True)['cnt']
    total_events = query_db("SELECT COUNT(*) as cnt FROM events", one=True)['cnt']
    open_events = query_db("SELECT COUNT(*) as cnt FROM events WHERE status = 'Open'", one=True)['cnt']
    closed_events = query_db("SELECT COUNT(*) as cnt FROM events WHERE status = 'Closed'", one=True)['cnt']
    expired_events = query_db("SELECT COUNT(*) as cnt FROM events WHERE status = 'Expired'", one=True)['cnt']
    total_regs = query_db(
        "SELECT COUNT(*) as cnt FROM registrations WHERE status = 'Registered'", one=True
    )['cnt']

    type_stats = {}
    for t in EVENT_TYPES:
        type_stats[t] = query_db(
            "SELECT COUNT(*) as cnt FROM events WHERE event_type = ?", (t,), one=True
        )['cnt']

    recent_regs = query_db(
        """SELECT r.*, e.event_name, e.event_type
           FROM registrations r
           JOIN events e ON r.event_id = e.id
           ORDER BY r.registration_date DESC LIMIT 10"""
    )

    return render_template(
        'admin_dashboard.html',
        total_users=total_users,
        total_events=total_events,
        open_events=open_events,
        closed_events=closed_events,
        expired_events=expired_events,
        total_regs=total_regs,
        type_stats=type_stats,
        recent_regs=recent_regs
    )


@app.route('/manage-events')
@admin_required
def manage_events():
    update_event_statuses()
    search = request.args.get('search', '').strip()
    event_type = request.args.get('type', 'All')
    status_filter = request.args.get('status', 'All')

    query = "SELECT * FROM events WHERE 1=1"
    params = []
    if search:
        query += " AND (event_name LIKE ? OR venue LIKE ?)"
        like = f'%{search}%'
        params.extend([like, like])
    if event_type and event_type != 'All':
        query += " AND event_type = ?"
        params.append(event_type)
    if status_filter and status_filter != 'All':
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY id ASC"

    events_list = query_db(query, params)
    events_data = []
    for e in events_list:
        d = dict(e)
        d['registered'] = get_registered_count(e['id'])
        d['available'] = get_available_seats(e)
        events_data.append(d)

    return render_template(
        'manage_events.html',
        events=events_data,
        search=search,
        selected_type=event_type,
        selected_status=status_filter,
        event_types=EVENT_TYPES
    )


@app.route('/add-event', methods=['GET', 'POST'])
@admin_required
def add_event():
    if request.method == 'POST':
        event_name = request.form.get('event_name', '').strip()
        event_type = request.form.get('event_type', '').strip()
        event_date = request.form.get('event_date', '').strip()
        registration_deadline = request.form.get('registration_deadline', '').strip()
        venue = request.form.get('venue', '').strip()
        capacity = request.form.get('capacity', '').strip()
        team_size = request.form.get('team_size', '1').strip()
        description = request.form.get('description', '').strip()
        status = request.form.get('status', 'Open').strip()

        errors = []
        if not event_name:
            errors.append('Event name is required.')
        if not event_type or event_type not in EVENT_TYPES:
            errors.append('Valid event type is required.')
        if not event_date:
            errors.append('Event date is required.')
        if not registration_deadline:
            errors.append('Registration deadline is required.')
        if not venue:
            errors.append('Venue is required.')
        if not capacity or not capacity.isdigit() or int(capacity) < 1:
            errors.append('Capacity must be a positive integer.')
        if not team_size or not team_size.isdigit() or int(team_size) < 1:
            errors.append('Team size must be a positive integer (1 = individual).')
        if not description:
            errors.append('Description is required.')
        if status not in ('Open', 'Closed', 'Expired'):
            errors.append('Invalid status.')

        if event_date and registration_deadline:
            try:
                ed = datetime.strptime(event_date, '%Y-%m-%d').date()
                rd = datetime.strptime(registration_deadline, '%Y-%m-%d').date()
                if rd > ed:
                    errors.append('Registration deadline cannot be after event date.')
            except ValueError:
                errors.append('Invalid date format.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template(
                'add_event.html',
                event_types=EVENT_TYPES,
                form=request.form
            )

        execute_db(
            """INSERT INTO events
               (event_name, event_type, event_date, registration_deadline, venue,
                capacity, description, status, team_size, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_name, event_type, event_date, registration_deadline, venue,
             int(capacity), description, status, int(team_size),
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        flash('Event created successfully!', 'success')
        return redirect(url_for('manage_events'))

    return render_template('add_event.html', event_types=EVENT_TYPES, form={})


@app.route('/edit-event/<int:event_id>', methods=['GET', 'POST'])
@admin_required
def edit_event(event_id):
    event = query_db("SELECT * FROM events WHERE id = ?", (event_id,), one=True)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('manage_events'))

    registered = get_registered_count(event_id)

    if request.method == 'POST':
        event_name = request.form.get('event_name', '').strip()
        event_type = request.form.get('event_type', '').strip()
        event_date = request.form.get('event_date', '').strip()
        registration_deadline = request.form.get('registration_deadline', '').strip()
        venue = request.form.get('venue', '').strip()
        capacity = request.form.get('capacity', '').strip()
        team_size = request.form.get('team_size', '1').strip()
        description = request.form.get('description', '').strip()
        status = request.form.get('status', 'Open').strip()

        errors = []
        if not event_name:
            errors.append('Event name is required.')
        if not event_type or event_type not in EVENT_TYPES:
            errors.append('Valid event type is required.')
        if not event_date:
            errors.append('Event date is required.')
        if not registration_deadline:
            errors.append('Registration deadline is required.')
        if not venue:
            errors.append('Venue is required.')
        if not capacity or not capacity.isdigit() or int(capacity) < 1:
            errors.append('Capacity must be a positive integer.')
        elif int(capacity) < registered:
            errors.append(f'Capacity cannot be lower than current registrations ({registered}).')
        if not team_size or not team_size.isdigit() or int(team_size) < 1:
            errors.append('Team size must be a positive integer (1 = individual).')
        if not description:
            errors.append('Description is required.')
        if status not in ('Open', 'Closed', 'Expired'):
            errors.append('Invalid status.')

        if event_date and registration_deadline:
            try:
                ed = datetime.strptime(event_date, '%Y-%m-%d').date()
                rd = datetime.strptime(registration_deadline, '%Y-%m-%d').date()
                if rd > ed:
                    errors.append('Registration deadline cannot be after event date.')
            except ValueError:
                errors.append('Invalid date format.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template(
                'edit_event.html',
                event=event,
                event_types=EVENT_TYPES,
                registered=registered,
                form=request.form
            )

        execute_db(
            """UPDATE events SET
               event_name=?, event_type=?, event_date=?, registration_deadline=?,
               venue=?, capacity=?, description=?, status=?, team_size=?
               WHERE id=?""",
            (event_name, event_type, event_date, registration_deadline,
             venue, int(capacity), description, status, int(team_size), event_id)
        )
        flash('Event updated successfully!', 'success')
        return redirect(url_for('manage_events'))

    return render_template(
        'edit_event.html',
        event=event,
        event_types=EVENT_TYPES,
        registered=registered,
        form={}
    )


@app.route('/delete-event/<int:event_id>', methods=['POST'])
@admin_required
def delete_event(event_id):
    event = query_db("SELECT * FROM events WHERE id = ?", (event_id,), one=True)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('manage_events'))

    reg_count = query_db(
        "SELECT COUNT(*) as cnt FROM registrations WHERE event_id = ?",
        (event_id,), one=True
    )['cnt']

    if reg_count > 0:
        flash(
            'This event has registrations and cannot be deleted. You can close the event.',
            'error'
        )
        return redirect(url_for('manage_events'))

    execute_db("DELETE FROM events WHERE id = ?", (event_id,))
    flash('Event deleted successfully!', 'success')
    return redirect(url_for('manage_events'))


@app.route('/close-event/<int:event_id>', methods=['POST'])
@admin_required
def close_event(event_id):
    event = query_db("SELECT * FROM events WHERE id = ?", (event_id,), one=True)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('manage_events'))
    execute_db("UPDATE events SET status = 'Closed' WHERE id = ?", (event_id,))
    flash('Event closed successfully.', 'success')
    return redirect(url_for('manage_events'))


@app.route('/all-registrations')
@admin_required
def all_registrations():
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', 'All')
    event_type = request.args.get('type', 'All')

    query = """
        SELECT r.*, e.event_name, e.event_type, e.event_date
        FROM registrations r
        JOIN events e ON r.event_id = e.id
        WHERE 1=1
    """
    params = []
    if search:
        query += " AND (r.name LIKE ? OR r.email LIKE ? OR e.event_name LIKE ? OR r.phone LIKE ?)"
        like = f'%{search}%'
        params.extend([like, like, like, like])
    if status_filter and status_filter != 'All':
        query += " AND r.status = ?"
        params.append(status_filter)
    if event_type and event_type != 'All':
        query += " AND e.event_type = ?"
        params.append(event_type)
    query += " ORDER BY r.registration_date DESC"

    regs = query_db(query, params)
    return render_template(
        'all_registrations.html',
        registrations=regs,
        search=search,
        selected_status=status_filter,
        selected_type=event_type,
        event_types=EVENT_TYPES
    )


@app.route('/reports')
@admin_required
def reports():
    update_event_statuses()
    total_users = query_db("SELECT COUNT(*) as cnt FROM users", one=True)['cnt']
    total_events = query_db("SELECT COUNT(*) as cnt FROM events", one=True)['cnt']
    total_regs = query_db(
        "SELECT COUNT(*) as cnt FROM registrations WHERE status = 'Registered'", one=True
    )['cnt']
    open_events = query_db("SELECT COUNT(*) as cnt FROM events WHERE status = 'Open'", one=True)['cnt']
    closed_events = query_db("SELECT COUNT(*) as cnt FROM events WHERE status = 'Closed'", one=True)['cnt']
    expired_events = query_db("SELECT COUNT(*) as cnt FROM events WHERE status = 'Expired'", one=True)['cnt']

    # Registrations by Event
    regs_by_event = query_db(
        """SELECT e.event_name, e.event_type, e.event_date,
                  COUNT(CASE WHEN r.status = 'Registered' THEN 1 END) as reg_count
           FROM events e
           LEFT JOIN registrations r ON e.id = r.event_id
           GROUP BY e.id
           ORDER BY reg_count DESC"""
    )

    # Registrations by Event Type
    regs_by_type = []
    for t in EVENT_TYPES:
        cnt = query_db(
            """SELECT COUNT(*) as cnt FROM registrations r
               JOIN events e ON r.event_id = e.id
               WHERE e.event_type = ? AND r.status = 'Registered'""",
            (t,), one=True
        )['cnt']
        regs_by_type.append({'type': t, 'count': cnt})

    today = date.today().isoformat()
    upcoming = query_db(
        "SELECT * FROM events WHERE event_date >= ? AND status = 'Open' ORDER BY event_date ASC LIMIT 10",
        (today,)
    )

    popular = query_db(
        """SELECT e.*, COUNT(CASE WHEN r.status = 'Registered' THEN 1 END) as reg_count
           FROM events e
           LEFT JOIN registrations r ON e.id = r.event_id
           GROUP BY e.id
           ORDER BY reg_count DESC
           LIMIT 10"""
    )

    return render_template(
        'reports.html',
        total_users=total_users,
        total_events=total_events,
        total_regs=total_regs,
        open_events=open_events,
        closed_events=closed_events,
        expired_events=expired_events,
        regs_by_event=regs_by_event,
        regs_by_type=regs_by_type,
        upcoming=upcoming,
        popular=popular
    )


@app.route('/admin-profile')
@admin_required
def admin_profile():
    admin = query_db("SELECT * FROM admins WHERE id = ?", (session['admin_id'],), one=True)
    return render_template('admin_profile.html', admin=admin)


# --------------- Error Handlers ---------------

@app.errorhandler(404)
def not_found(e):
    flash('Page not found.', 'error')
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    if 'user_id' in session:
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))


@app.errorhandler(500)
def server_error(e):
    flash('An internal error occurred. Please try again.', 'error')
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    if 'user_id' in session:
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))


# --------------- Main ---------------

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
