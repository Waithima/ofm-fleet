import json, os, sqlite3, uuid, smtplib, csv, io
import psycopg2
import psycopg2.extras
import resend
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from flask import Flask, g, jsonify, render_template, request, redirect, url_for, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_URL = os.environ.get("DATABASE_URL", "")

app = Flask(__name__)
app.secret_key = "ofm-super-secret-key-change-in-production"
resend.api_key = os.environ.get("RESEND_API_KEY", "")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "admin_login"

ADMIN_HASH = generate_password_hash("admin123")

class AdminUser(UserMixin):
    def __init__(self, id): self.id = id

@login_manager.user_loader
def load_user(user_id):
    if user_id == "admin": return AdminUser("admin")
    return None

def get_db():
    if "db" not in g:
        if DB_URL:
            url = DB_URL.replace("postgres://", "postgresql://", 1)
            g.db = psycopg2.connect(url)
            g.db.autocommit = True
        else:
            g.db = sqlite3.connect(os.path.join(BASE, "fleet.db"))
            g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db: db.close()

def rows(sql, args=()):
    db = get_db()
    if DB_URL:
        sql = sql.replace("?", "%s").replace("datetime('now')", "CURRENT_TIMESTAMP")
        cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    else:
        return [dict(r) for r in db.execute(sql, args).fetchall()]

def row(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None

def execute(sql, args=()):
    db = get_db()
    if DB_URL:
        sql = sql.replace("?", "%s").replace("datetime('now')", "CURRENT_TIMESTAMP")
        cur = db.cursor()
        if "RETURNING" not in sql.upper() and sql.strip().upper().startswith("INSERT"):
            sql += " RETURNING id"
        cur.execute(sql, args)
        if "RETURNING" in sql.upper():
            return cur.fetchone()[0]
        return None
    else:
        cur = db.execute(sql, args)
        db.commit()
        return cur.lastrowid

def parse_config(u):
    if isinstance(u.get("configuration"), str):
        try: u["configuration"] = json.loads(u["configuration"])
        except: u["configuration"] = {}
    return u

def send_email(to_email, subject, body):
    if not to_email: return False
    try:
        resend.Emails.send({"from": "onboarding@resend.dev", "to": [to_email], "subject": subject, "text": body})
        print(f"📧 Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Failed to send: {e}")
        return False

def run_daily_checks():
    today = datetime.utcnow().date().isoformat()
    tomorrow = (datetime.utcnow().date() + timedelta(days=1)).isoformat()
    
    expired_leases = rows("SELECT * FROM leases WHERE status = 'ACTIVE' AND end_date < ?", (today,))
    for l in expired_leases:
        execute("UPDATE leases SET status='COMPLETED' WHERE id=?", (l['id'],))
        execute("UPDATE units SET status='READY' WHERE id=?", (l['unit_id'],))

    ending_soon = rows("SELECT * FROM leases WHERE status = 'ACTIVE' AND end_date = ? AND last_reminded_at IS NULL", (tomorrow,))
    for l in ending_soon:
        client = row("SELECT * FROM clients WHERE id=?", (l['client_id'],))
        unit = row("SELECT * FROM units WHERE id=?", (l['unit_id'],))
        magic_link = f"https://ofm-fleet.onrender.com/my-portal/{client['access_token']}"
        body = f"Hello {client['name']},\n\nThis is a friendly reminder that your lease for {unit['serial']} ends TOMORROW.\n\nRenew here:\n{magic_link}"
        send_email(client['email'], "Lease Ending Reminder", body)
        execute("UPDATE leases SET last_reminded_at=datetime('now') WHERE id=?", (l['id'],))

@app.route("/")
def index():
    run_daily_checks()
    units = [parse_config(u) for u in rows("SELECT * FROM units ORDER BY serial")]
    active_leases = rows("SELECT unit_id, end_date FROM leases WHERE status IN ('ACTIVE', 'PENDING') AND id IN (SELECT MAX(id) FROM leases WHERE status IN ('ACTIVE', 'PENDING') GROUP BY unit_id)")
    lease_map = {l['unit_id']: l['end_date'][:10] for l in active_leases}
    for u in units: u['booked_until'] = lease_map.get(u['id'])
    return render_template("index.html", units=units)

@app.route("/lease/<int:unit_id>", methods=["GET", "POST"])
def lease_request(unit_id):
    unit = row("SELECT * FROM units WHERE id=?", (unit_id,))
    if not unit or unit['status'] in ('MAINTENANCE', 'DECOMMISSIONED'): return "Unit unavailable", 404
    if request.method == "POST":
        name, email, phone = request.form.get("name"), request.form.get("email"), request.form.get("phone")
        county, institution = request.form.get("county"), request.form.get("institution")
        start_date, end_date = request.form.get("start_date"), request.form.get("end_date")
        
        client = row("SELECT * FROM clients WHERE email=?", (email,))
        if not client:
            token = str(uuid.uuid4())
            client_id = execute("INSERT INTO clients (name, email, phone, county, institution, access_token) VALUES (?,?,?,?,?,?)", (name, email, phone, county, institution, token))
            client = row("SELECT * FROM clients WHERE id=?", (client_id,))
        else:
            execute("UPDATE clients SET name=?, phone=?, county=?, institution=? WHERE id=?", (name, phone, county, institution, client['id']))
        
        existing_lease = row("SELECT * FROM leases WHERE unit_id=? AND client_id=? AND status IN ('PENDING', 'ACTIVE')", (unit_id, client['id']))
        if existing_lease:
            execute("UPDATE leases SET start_date=?, end_date=? WHERE id=?", (start_date, end_date, existing_lease['id']))
        else:
            execute("INSERT INTO leases (unit_id, client_id, status, start_date, end_date) VALUES (?, ?, 'PENDING', ?, ?)", (unit_id, client['id'], start_date, end_date))
            execute("UPDATE units SET status='RESERVED' WHERE id=?", (unit_id,))
            magic_link = f"https://ofm-fleet.onrender.com/my-portal/{client['access_token']}"
            send_email(email, "OFM Lease Request", f"Hello {name},\n\nYour request for {unit['serial']} is pending approval.\n\nPortal: {magic_link}")
        
        magic_link = f"https://ofm-fleet.onrender.com/my-portal/{client['access_token']}"
        return render_template("lease_success.html", magic_link=magic_link, email=email)
    return render_template("lease_form.html", unit=unit)

@app.route("/my-portal/<token>")
def client_portal(token):
    run_daily_checks()
    client = row("SELECT * FROM clients WHERE access_token=?", (token,))
    if not client: return "Invalid link.", 404
    leases = rows("SELECT l.*, u.serial, u.model, u.image_url, u.status AS unit_status FROM leases l JOIN units u ON u.id = l.unit_id WHERE l.client_id = ? AND l.id IN (SELECT MAX(id) FROM leases WHERE client_id = ? GROUP BY unit_id) ORDER BY l.id DESC", (client["id"], client["id"]))
    today = datetime.utcnow().date()
    for l in leases:
        end_date = datetime.strptime(l['end_date'], "%Y-%m-%d").date()
        l['show_renew'] = (end_date - today).days <= 1 and l['status'] == 'ACTIVE'
    return render_template("client_portal.html", client=client, leases=leases, token=token)

@app.route("/api/client/renew/<int:lease_id>", methods=["POST"])
def api_renew_lease(lease_id):
    execute("UPDATE leases SET end_date=?, last_reminded_at=NULL WHERE id=?", (request.get_json().get("new_end_date"), lease_id))
    return jsonify({"ok": True})

@app.route("/api/client/issues", methods=["POST"])
def api_client_issue():
    data = request.get_json()
    execute("INSERT INTO issues (unit_id, client_id, category, severity, description, photo_urls) VALUES (?,?,?,?,?,?)", (data["unitId"], data["clientId"], data["category"], data["severity"], data["description"], "[]"))
    execute("UPDATE units SET status='ISSUE_REPORTED' WHERE id=?", (data["unitId"],))
    return jsonify({"ok": True}), 201

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST" and check_password_hash(ADMIN_HASH, request.form.get("password")):
        login_user(AdminUser("admin"))
        return redirect(url_for("admin"))
    return render_template("admin_login.html")

@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for("index"))

@app.route("/admin")
@login_required
def admin():
    run_daily_checks()
    units = rows("SELECT u.*, c.name AS client_name, l.end_date FROM units u LEFT JOIN leases l ON l.id = (SELECT id FROM leases WHERE unit_id = u.id AND status IN ('ACTIVE', 'PENDING') ORDER BY id DESC LIMIT 1) LEFT JOIN clients c ON c.id = l.client_id ORDER BY u.serial")
    units = [parse_config(u) for u in units]
    all_leases = rows("SELECT l.*, c.name AS client_name, c.email, c.phone, c.county, c.institution, u.serial, u.model FROM leases l JOIN clients c ON c.id = l.client_id JOIN units u ON u.id = l.unit_id ORDER BY l.created_at DESC")
    issues = rows("SELECT * FROM issues")
    total = len(units) or 1
    leased = sum(1 for u in units if u["status"] in ("LEASED", "ISSUE_REPORTED", "RESERVED"))
    return render_template("admin.html", units=units, leases=all_leases, stats=dict(utilization=round(leased / total * 100, 1), open_issues=sum(1 for i in issues if i["status"] == "OPEN"), fleet=len(units)), active="admin")

@app.route("/api/admin/units/<int:unit_id>/status", methods=["POST"])
@login_required
def api_update_status(unit_id):
    execute("UPDATE units SET status=? WHERE id=?", (request.get_json().get("status"), unit_id))
    return jsonify({"ok": True})

@app.route("/admin/live")
@login_required
def admin_live():
    units = rows("SELECT u.id, u.serial, u.model, u.status, t.online, t.camera_ok, t.received_at FROM units u LEFT JOIN telemetry t ON t.id = (SELECT id FROM telemetry WHERE unit_id = u.id ORDER BY received_at DESC LIMIT 1) ORDER BY u.serial")
    now = datetime.utcnow()
    for u in units:
        if u["received_at"]:
            try:
                age = (now - datetime.fromisoformat(u["received_at"])).total_seconds()
                u["live"], u["age_seconds"] = age < 180, int(age)
            except: u["live"] = False
        else: u["live"] = False
    return render_template("admin_live.html", units=units, active="admin")

@app.route("/admin/requests")
@login_required
def admin_requests():
    leases = rows("SELECT l.*, c.name AS client_name, c.email, c.phone, c.county, c.institution, u.serial, u.model FROM leases l JOIN clients c ON c.id = l.client_id JOIN units u ON u.id = l.unit_id WHERE l.status = 'PENDING'")
    return render_template("admin_requests.html", leases=leases, active="admin")

@app.route("/api/admin/requests/<int:lease_id>/approve", methods=["POST"])
@login_required
def api_approve_lease(lease_id):
    lease = row("SELECT * FROM leases WHERE id=?", (lease_id,))
    execute("UPDATE leases SET status='ACTIVE' WHERE id=?", (lease_id,))
    execute("UPDATE units SET status='LEASED' WHERE id=?", (lease["unit_id"],))
    client = row("SELECT * FROM clients WHERE id=?", (lease["client_id"],))
    unit = row("SELECT * FROM units WHERE id=?", (lease["unit_id"],))
    magic_link = f"https://ofm-fleet.onrender.com/my-portal/{client['access_token']}"
    send_email(client["email"], "Lease Approved!", f"Hello {client['name']},\n\nGreat news! Your lease for {unit['serial']} has been approved.\n\nAccess your portal here:\n{magic_link}")
    return jsonify({"ok": True})

@app.route("/api/admin/requests/<int:lease_id>/disapprove", methods=["POST"])
@login_required
def api_disapprove_lease(lease_id):
    lease = row("SELECT * FROM leases WHERE id=?", (lease_id,))
    execute("DELETE FROM leases WHERE id=?", (lease_id,))
    execute("UPDATE units SET status='READY' WHERE id=?", (lease["unit_id"],))
    client = row("SELECT * FROM clients WHERE id=?", (lease["client_id"],))
    unit = row("SELECT * FROM units WHERE id=?", (lease["unit_id"],))
    send_email(client["email"], "Lease Request Update", f"Hello {client['name']},\n\nUnfortunately, your lease request for {unit['serial']} was not approved.")
    return jsonify({"ok": True})

# --- NEW: RESOLVE ISSUES ROUTE ---
@app.route("/api/admin/issues/<int:issue_id>/resolve", methods=["POST"])
@login_required
def api_resolve_issue(issue_id):
    data = request.get_json()
    issue = row("SELECT i.*, c.name AS client_name, c.email, u.serial FROM issues i JOIN clients c ON c.id = i.client_id JOIN units u ON u.id = i.unit_id WHERE i.id = ?", (issue_id,))
    
    action = data.get("action")
    message = data.get("message", "")
    
    if action == "email":
        body = f"Hello {issue['client_name']},\n\nRegarding your issue with {issue['serial']}:\n\n{message}\n\nThank you,\nOFM Support"
        send_email(issue['email'], f"Update on your {issue['serial']} Issue", body)
        execute("UPDATE issues SET status='RESOLVED', resolution=? WHERE id=?", (f"Emailed: {message}", issue_id))
        execute("UPDATE units SET status='READY' WHERE id=?", (issue['unit_id'],))
        
    elif action == "dropoff":
        body = f"Hello {issue['client_name']},\n\nPlease bring your microscope ({issue['serial']}) to our office at Meru University for repair.\n\nMessage from Admin:\n{message}\n\nThank you."
        send_email(issue['email'], "Please bring microscope to office", body)
        execute("UPDATE issues SET status='IN_REPAIR', resolution=? WHERE id=?", (f"Bring to office: {message}", issue_id))
        
    elif action == "visit":
        date = data.get("date")
        body = f"Hello {issue['client_name']},\n\nWe will visit your institution on {date} to repair your microscope ({issue['serial']}).\n\nMessage from Admin:\n{message}\n\nThank you."
        send_email(issue['email'], "Technician Visit Scheduled", body)
        execute("UPDATE issues SET status='IN_REPAIR', resolution=? WHERE id=?", (f"Visit on {date}: {message}", issue_id))
        
    return jsonify({"ok": True})

# --- FIXED CSV EXPORT ---
@app.route("/admin/export-csv")
@login_required
def export_csv():
    leases = rows("SELECT l.id, u.serial, u.model, c.name, c.email, c.phone, c.county, c.institution, l.start_date, l.end_date, l.status, l.created_at FROM leases l JOIN clients c ON c.id = l.client_id JOIN units u ON u.id = l.unit_id ORDER BY l.created_at DESC")
    def generate():
        data = io.StringIO()
        writer = csv.writer(data, delimiter='\t')
        writer.writerow(["Lease ID", "Microscope Serial", "Microscope Model", "Client Name", "Email Address", "Phone Number", "County (Delivery)", "Institution (Delivery)", "Start Date", "End Date", "Current Status", "Date Requested"])
        for l in leases:
            # Adding a space forces Excel to treat the phone as text (preventing 1.12E+08)
            phone = f" {l['phone']}" if l['phone'] else ""
            writer.writerow([l['id'], l['serial'], l['model'], l['name'], l['email'], phone, l['county'], l['institution'], l['start_date'], l['end_date'], l['status'], l['created_at']])
        yield data.getvalue()
    return Response(generate(), mimetype='application/vnd.ms-excel', headers={"Content-Disposition": "attachment;filename=ofm_leases.xls"})

@app.route("/api/admin/units", methods=["POST"])
@login_required
def api_add_unit():
    data = request.get_json()
    try:
        execute("INSERT INTO units (serial, model, configuration, status, image_url) VALUES (?, ?, ?, 'READY', ?)", (data["serial"], data["model"], json.dumps({}), data.get("image_url", "")))
        return jsonify({"ok": True}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/admin/units/<int:unit_id>", methods=["DELETE"])
@login_required
def api_delete_unit(unit_id):
    execute("DELETE FROM telemetry WHERE unit_id=?", (unit_id,))
    execute("DELETE FROM issues WHERE unit_id=?", (unit_id,))
    execute("DELETE FROM leases WHERE unit_id=?", (unit_id,))
    execute("DELETE FROM units WHERE id=?", (unit_id,))
    return jsonify({"ok": True})

if DB_URL:
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (id SERIAL PRIMARY KEY, name TEXT, email TEXT UNIQUE, phone TEXT, county TEXT, institution TEXT, access_token TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS units (id SERIAL PRIMARY KEY, serial TEXT UNIQUE, model TEXT, configuration TEXT, image_url TEXT, status TEXT DEFAULT 'READY', last_seen_at TIMESTAMP);
            CREATE TABLE IF NOT EXISTS leases (id SERIAL PRIMARY KEY, unit_id INTEGER REFERENCES units(id), client_id INTEGER REFERENCES clients(id), status TEXT DEFAULT 'PENDING', deposit_cents INTEGER DEFAULT 0, daily_cents INTEGER DEFAULT 0, start_date TEXT, end_date TEXT, last_reminded_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS issues (id SERIAL PRIMARY KEY, unit_id INTEGER REFERENCES units(id), client_id INTEGER REFERENCES clients(id), category TEXT, severity TEXT, status TEXT DEFAULT 'OPEN', description TEXT, photo_urls TEXT DEFAULT '[]', resolution TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS telemetry (id SERIAL PRIMARY KEY, unit_id INTEGER REFERENCES units(id), online INTEGER, os_info TEXT, camera_ok INTEGER, error_log TEXT, received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Setup error:", e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
