import json, os, sqlite3, uuid, smtplib, csv, io
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from flask import Flask, g, jsonify, render_template, request, redirect, url_for, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "fleet.db")

app = Flask(__name__)
app.secret_key = "ofm-super-secret-key-change-in-production"

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "jeffwaithima400@gmail.com"
SMTP_PASS = "aogbqtsjysvpmvwj"

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
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db: db.close()

def rows(sql, args=()):
    return [dict(r) for r in get_db().execute(sql, args).fetchall()]

def row(sql, args=()):
    r = get_db().execute(sql, args).fetchone()
    return dict(r) if r else None

def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid

def parse_config(u):
    if isinstance(u.get("configuration"), str):
        try: u["configuration"] = json.loads(u["configuration"])
        except: u["configuration"] = {}
    return u

def send_email(to_email, subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = SMTP_USER
    msg['To'] = to_email
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"📧 Real email sent to {to_email}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

def run_daily_checks():
    today = datetime.utcnow().date().isoformat()
    tomorrow = (datetime.utcnow().date() + timedelta(days=1)).isoformat()
    
    expired_leases = rows("SELECT * FROM leases WHERE status = 'ACTIVE' AND end_date < ?", (today,))
    for l in expired_leases:
        execute("UPDATE leases SET status='COMPLETED' WHERE id=?", (l['id'],))
        execute("UPDATE units SET status='READY' WHERE id=?", (l['unit_id'],))
        execute("INSERT INTO unit_history (unit_id,event,detail) VALUES (?,?,?)",
                (l['unit_id'], "RETURNED", "Lease automatically expired."))

    ending_soon = rows("SELECT * FROM leases WHERE status = 'ACTIVE' AND end_date = ? AND last_reminded_at IS NULL", (tomorrow,))
    for l in ending_soon:
        client = row("SELECT * FROM clients WHERE id=?", (l['client_id'],))
        unit = row("SELECT * FROM units WHERE id=?", (l['unit_id'],))
        magic_link = f"http://localhost:5000/my-portal/{client['access_token']}"
        
        body = f"Hello {client['name']},\n\nThis is a friendly reminder that your lease for {unit['serial']} ends TOMORROW ({tomorrow}).\n\nIf you would like to keep it for longer, please visit your portal and click 'Renew Lease':\n{magic_link}\n\nThank you!"
        send_email(client['email'], "Lease Ending Reminder", body)
        execute("UPDATE leases SET last_reminded_at=datetime('now') WHERE id=?", (l['id'],))

@app.route("/")
def index():
    run_daily_checks()
    units = [parse_config(u) for u in rows("SELECT * FROM units ORDER BY serial")]
    active_leases = rows("""
        SELECT unit_id, end_date FROM leases 
        WHERE status IN ('ACTIVE', 'PENDING') 
        AND id IN (SELECT MAX(id) FROM leases WHERE status IN ('ACTIVE', 'PENDING') GROUP BY unit_id)
    """)
    lease_map = {l['unit_id']: l['end_date'][:10] for l in active_leases}

    for u in units:
        u['booked_until'] = lease_map.get(u['id'])
            
    return render_template("index.html", units=units)

@app.route("/lease/<int:unit_id>", methods=["GET", "POST"])
def lease_request(unit_id):
    unit = row("SELECT * FROM units WHERE id=?", (unit_id,))
    if not unit or unit['status'] in ('MAINTENANCE', 'DECOMMISSIONED'):
        return "Unit is currently unavailable for leasing.", 404
    
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        county = request.form.get("county")
        institution = request.form.get("institution")
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        
        client = row("SELECT * FROM clients WHERE email=?", (email,))
        if not client:
            token = str(uuid.uuid4())
            client_id = execute("INSERT INTO clients (name, email, phone, county, institution, access_token) VALUES (?,?,?,?,?,?)",
                                (name, email, phone, county, institution, token))
            client = row("SELECT * FROM clients WHERE id=?", (client_id,))
        else:
            execute("UPDATE clients SET name=?, phone=?, county=?, institution=? WHERE id=?", 
                    (name, phone, county, institution, client['id']))
        
        existing_lease = row("SELECT * FROM leases WHERE unit_id=? AND client_id=? AND status IN ('PENDING', 'ACTIVE')", (unit_id, client['id']))
        
        if existing_lease:
            execute("UPDATE leases SET start_date=?, end_date=? WHERE id=?", (start_date, end_date, existing_lease['id']))
        else:
            execute("""INSERT INTO leases (unit_id, client_id, status, start_date, end_date)
                       VALUES (?, ?, 'PENDING', ?, ?)""",
                    (unit_id, client['id'], start_date, end_date))
            
            execute("UPDATE units SET status='RESERVED' WHERE id=?", (unit_id,))
            magic_link = f"http://localhost:5000/my-portal/{client['access_token']}"
            body = f"Hello {name},\n\nYour request for {unit['serial']} is pending approval.\n\nPortal: {magic_link}"
            send_email(email, "OFM Lease Request", body)
        
        magic_link = f"http://localhost:5000/my-portal/{client['access_token']}"
        return render_template("lease_success.html", magic_link=magic_link, email=email)

    return render_template("lease_form.html", unit=unit)

@app.route("/my-portal/<token>")
def client_portal(token):
    run_daily_checks()
    client = row("SELECT * FROM clients WHERE access_token=?", (token,))
    if not client: return "Invalid link.", 404
    
    leases = rows("""
        SELECT l.*, u.serial, u.model, u.image_url, u.status AS unit_status
        FROM leases l JOIN units u ON u.id = l.unit_id
        WHERE l.client_id = ? AND l.id IN (
            SELECT MAX(id) FROM leases WHERE client_id = ? GROUP BY unit_id
        )
        ORDER BY l.id DESC
    """, (client["id"], client["id"]))
    
    today = datetime.utcnow().date()
    for l in leases:
        end_date = datetime.strptime(l['end_date'], "%Y-%m-%d").date()
        l['show_renew'] = (end_date - today).days <= 1 and l['status'] == 'ACTIVE'
        
    return render_template("client_portal.html", client=client, leases=leases, token=token)

@app.route("/api/client/renew/<int:lease_id>", methods=["POST"])
def api_renew_lease(lease_id):
    data = request.get_json()
    new_end_date = data.get("new_end_date")
    lease = row("SELECT * FROM leases WHERE id=?", (lease_id,))
    if lease:
        execute("UPDATE leases SET end_date=?, last_reminded_at=NULL WHERE id=?", (new_end_date, lease_id))
        return jsonify({"ok": True})
    return jsonify({"error": "Lease not found"}), 404

@app.route("/api/client/issues", methods=["POST"])
def api_client_issue():
    data = request.get_json()
    execute("""INSERT INTO issues (unit_id, client_id, category, severity, description, photo_urls)
               VALUES (?,?,?,?,?,?)""",
            (data["unitId"], data["clientId"], data["category"], data["severity"], data["description"], "[]"))
    execute("UPDATE units SET status='ISSUE_REPORTED' WHERE id=?", (data["unitId"],))
    return jsonify({"ok": True}), 201

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if check_password_hash(ADMIN_HASH, request.form.get("password")):
            login_user(AdminUser("admin"))
            return redirect(url_for("admin"))
        flash("Invalid password", "error")
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
    
    units = rows("""
        SELECT u.*, c.name AS client_name, l.end_date
        FROM units u
        LEFT JOIN leases l ON l.id = (
            SELECT id FROM leases WHERE unit_id = u.id AND status IN ('ACTIVE', 'PENDING') ORDER BY id DESC LIMIT 1
        )
        LEFT JOIN clients c ON c.id = l.client_id
        ORDER BY u.serial
    """)
    units = [parse_config(u) for u in units]
    
    all_leases = rows("""
        SELECT l.*, c.name AS client_name, c.email, c.phone, c.county, c.institution, u.serial, u.model
        FROM leases l
        JOIN clients c ON c.id = l.client_id
        JOIN units u ON u.id = l.unit_id
        ORDER BY l.created_at DESC
    """)
    
    issues = rows("SELECT * FROM issues")
    total = len(units) or 1
    leased = sum(1 for u in units if u["status"] in ("LEASED", "ISSUE_REPORTED", "RESERVED"))
    
    return render_template("admin.html", units=units, leases=all_leases, stats=dict(
        utilization=round(leased / total * 100, 1),
        open_issues=sum(1 for i in issues if i["status"] == "OPEN"),
        fleet=len(units)
    ), active="admin")

@app.route("/api/admin/units/<int:unit_id>/status", methods=["POST"])
@login_required
def api_update_status(unit_id):
    data = request.get_json()
    execute("UPDATE units SET status=? WHERE id=?", (data["status"], unit_id))
    return jsonify({"ok": True})

@app.route("/admin/live")
@login_required
def admin_live():
    units = rows("""
        SELECT u.id, u.serial, u.model, u.status, t.online, t.camera_ok, t.received_at
        FROM units u
        LEFT JOIN telemetry t ON t.id = (SELECT id FROM telemetry WHERE unit_id = u.id ORDER BY received_at DESC LIMIT 1)
        ORDER BY u.serial
    """)
    
    now = datetime.utcnow()
    for u in units:
        if u["received_at"]:
            try:
                seen = datetime.fromisoformat(u["received_at"])
                age = (now - seen).total_seconds()
                u["live"] = age < 180
                u["age_seconds"] = int(age)
            except Exception:
                u["live"] = False
                u["age_seconds"] = None
        else:
            u["live"] = False
            u["age_seconds"] = None
            
    return render_template("admin_live.html", units=units, active="admin")

@app.route("/admin/requests")
@login_required
def admin_requests():
    leases = rows("""
        SELECT l.*, c.name AS client_name, c.email, c.phone, c.county, c.institution, u.serial, u.model
        FROM leases l 
        JOIN clients c ON c.id = l.client_id
        JOIN units u ON u.id = l.unit_id
        WHERE l.status = 'PENDING'
    """)
    return render_template("admin_requests.html", leases=leases, active="admin")

@app.route("/api/admin/requests/<int:lease_id>/approve", methods=["POST"])
@login_required
def api_approve_lease(lease_id):
    lease = row("SELECT * FROM leases WHERE id=?", (lease_id,))
    execute("UPDATE leases SET status='ACTIVE' WHERE id=?", (lease_id,))
    execute("UPDATE units SET status='LEASED' WHERE id=?", (lease["unit_id"],))
    
    client = row("SELECT * FROM clients WHERE id=?", (lease["client_id"],))
    unit = row("SELECT * FROM units WHERE id=?", (lease["unit_id"],))
    magic_link = f"http://localhost:5000/my-portal/{client['access_token']}"
    
    body = f"Hello {client['name']},\n\nGreat news! Your lease for {unit['serial']} has been approved.\n\nAccess your portal here:\n{magic_link}"
    send_email(client["email"], "Lease Approved!", body)
                    
    return jsonify({"ok": True})

@app.route("/api/admin/requests/<int:lease_id>/disapprove", methods=["POST"])
@login_required
def api_disapprove_lease(lease_id):
    lease = row("SELECT * FROM leases WHERE id=?", (lease_id,))
    execute("DELETE FROM leases WHERE id=?", (lease_id,))
    execute("UPDATE units SET status='READY' WHERE id=?", (lease["unit_id"],))
    
    client = row("SELECT * FROM clients WHERE id=?", (lease["client_id"],))
    unit = row("SELECT * FROM units WHERE id=?", (lease["unit_id"],))
    body = f"Hello {client['name']},\n\nUnfortunately, your lease request for {unit['serial']} was not approved."
    send_email(client["email"], "Lease Request Update", body)
                    
    return jsonify({"ok": True})

@app.route("/admin/export-csv")
@login_required
def export_csv():
    leases = rows("""
        SELECT l.id, u.serial, u.model, c.name, c.email, c.phone, c.county, c.institution, 
               l.start_date, l.end_date, l.status, l.created_at
        FROM leases l
        JOIN clients c ON c.id = l.client_id
        JOIN units u ON u.id = l.unit_id
        ORDER BY l.created_at DESC
    """)
    
    def generate():
        data = io.StringIO()
        writer = csv.writer(data)
        writer.writerow(["ID", "Microscope Serial", "Model", "Client Name", "Email", "Phone", "County", "Institution", "Start Date", "End Date", "Status", "Date Requested"])
        for l in leases:
            writer.writerow([l['id'], l['serial'], l['model'], l['name'], l['email'], l['phone'], l['county'], l['institution'], l['start_date'], l['end_date'], l['status'], l['created_at']])
        yield data.getvalue()

    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=ofm_leases.csv"})

@app.route("/api/admin/units", methods=["POST"])
@login_required
def api_add_unit():
    data = request.get_json()
    try:
        execute("INSERT INTO units (serial, model, configuration, status, image_url) VALUES (?, ?, ?, 'READY', ?)",
                (data["serial"], data["model"], json.dumps({}), data.get("image_url", "")))
        return jsonify({"ok": True}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Serial already exists"}), 400

@app.route("/api/admin/units/<int:unit_id>", methods=["DELETE"])
@login_required
def api_delete_unit(unit_id):
    execute("DELETE FROM telemetry WHERE unit_id=?", (unit_id,))
    execute("DELETE FROM unit_history WHERE unit_id=?", (unit_id,))
    execute("DELETE FROM issues WHERE unit_id=?", (unit_id,))
    execute("DELETE FROM leases WHERE unit_id=?", (unit_id,))
    execute("DELETE FROM units WHERE id=?", (unit_id,))
    return jsonify({"ok": True})

# --- AUTO-CREATE DATABASE ON STARTUP FOR RENDER ---
if not os.path.exists(DB):
    with open(os.path.join(BASE, "schema.sql"), encoding="utf-8") as f:
        schema = f.read()
    con = sqlite3.connect(DB)
    con.executescript(schema)
    con.close()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
