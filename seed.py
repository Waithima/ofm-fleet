import sqlite3, json, os
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "fleet.db")

def now(offset_days=0):
    return (datetime.utcnow() + timedelta(days=offset_days)).isoformat()

with open(os.path.join(BASE, "schema.sql"), encoding="utf-8") as f:
    schema = f.read()

con = sqlite3.connect(DB)
con.executescript(schema)
cur = con.cursor()

cur.execute("INSERT INTO clients (name, email, phone, county, institution, access_token) VALUES (?,?,?,?,?,?)",
            ("Alice Bio Lab", "alice@biolab.example", "+1-555-0101", "Nairobi", "University of Nairobi", "client-token-abc123"))
c1 = cur.lastrowid

units = [
    ("OFM-0001", "OFM-Brightfield-v7",  {"illumination": "LED", "objective": "40x"}, "READY", "https://images.unsplash.com/photo-1576086213369-97a306d36557?auto=format&fit=crop&q=80&w=600"),
    ("OFM-0002", "OFM-Fluorescence-v7", {"illumination": "LED+Fluo", "objective": "100x"}, "READY", "https://images.unsplash.com/photo-1579154204601-01588f351e67?auto=format&fit=crop&q=80&w=600"),
    ("OFM-0003", "OFM-Brightfield-v7",  {"illumination": "LED", "objective": "20x"}, "READY", "https://images.unsplash.com/photo-1532094349884-543bc11b234d?auto=format&fit=crop&q=80&w=600"),
    ("OFM-0004", "OFM-Polarization-v7", {"illumination": "LED", "objective": "50x"}, "MAINTENANCE", "https://images.unsplash.com/photo-1581093458791-9f302e6d8659?auto=format&fit=crop&q=80&w=600"),
    ("OFM-0005", "OFM-Fluorescence-v7", {"illumination": "LED+Fluo", "objective": "100x"}, "LEASED", "https://images.unsplash.com/photo-1582719508461-905c673771fd?auto=format&fit=crop&q=80&w=600"),
]
unit_ids = []
for serial, model, cfg, status, img in units:
    cur.execute("INSERT INTO units (serial,model,configuration,status,image_url) VALUES (?,?,?,?,?)",
                (serial, model, json.dumps(cfg), status, img))
    unit_ids.append(cur.lastrowid)

# Make OFM-0005 leased
start = now(-3)
end = now(30)
cur.execute("INSERT INTO leases (unit_id,client_id,status,deposit_cents,daily_cents,start_date,end_date) VALUES (?,?,?,?,?,?,?)",
            (unit_ids[4], c1, "ACTIVE", 20000, 1500, start, end))

con.commit()
con.close()
print("Seeded database at", DB)
