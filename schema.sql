DROP TABLE IF EXISTS telemetry;
DROP TABLE IF EXISTS unit_history;
DROP TABLE IF EXISTS issues;
DROP TABLE IF EXISTS leases;
DROP TABLE IF EXISTS units;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  name    TEXT NOT NULL,
  email   TEXT UNIQUE NOT NULL,
  phone   TEXT,
  address TEXT
);

CREATE TABLE units (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  serial        TEXT UNIQUE NOT NULL,
  model         TEXT NOT NULL,
  configuration TEXT NOT NULL,      -- JSON string
  status        TEXT NOT NULL DEFAULT 'READY',
  last_seen_at  TEXT
);

CREATE TABLE leases (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id       INTEGER NOT NULL REFERENCES units(id),
  customer_id   INTEGER NOT NULL REFERENCES customers(id),
  status        TEXT NOT NULL DEFAULT 'ACTIVE',
  deposit_cents INTEGER NOT NULL DEFAULT 0,
  daily_cents   INTEGER NOT NULL DEFAULT 0,
  delivered_at  TEXT,
  returned_at   TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE issues (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id     INTEGER NOT NULL REFERENCES units(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  category    TEXT NOT NULL,
  severity    TEXT NOT NULL DEFAULT 'MEDIUM',
  status      TEXT NOT NULL DEFAULT 'OPEN',
  description TEXT NOT NULL,
  photo_urls  TEXT NOT NULL DEFAULT '[]',
  resolution  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE unit_history (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id INTEGER NOT NULL REFERENCES units(id),
  event   TEXT NOT NULL,
  detail  TEXT,
  at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE telemetry (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id     INTEGER NOT NULL REFERENCES units(id),
  online      INTEGER NOT NULL,
  os_info     TEXT,
  camera_ok   INTEGER,
  error_log   TEXT,
  received_at TEXT NOT NULL DEFAULT (datetime('now'))
);