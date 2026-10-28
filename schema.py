"""Additive, repeatable migrations for one isolated shop per database.

Never deletes customers, points, orders or existing operator settings.
The advisory transaction lock also serializes parallel Gunicorn startups.
"""
import json
import os
import secrets
from werkzeug.security import generate_password_hash


def foundation(cur):
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('kebab_schema_v2'))")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS einstellungen (
            schluessel TEXT PRIMARY KEY, wert TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mitarbeiter (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, pin TEXT NOT NULL,
            aktiv BOOLEAN NOT NULL DEFAULT TRUE,
            erstellt_am TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            letzter_login TIMESTAMP);
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, angewendet_am TIMESTAMPTZ DEFAULT NOW());
        ALTER TABLE mitarbeiter ALTER COLUMN pin TYPE TEXT;
        ALTER TABLE einstellungen ALTER COLUMN wert TYPE TEXT;
    """)


def complete(cur):
    cur.execute("""
        ALTER TABLE kunden ADD COLUMN IF NOT EXISTS passwort TEXT;
        ALTER TABLE punkte_bewegungen ADD COLUMN IF NOT EXISTS mitarbeiter_id INTEGER REFERENCES mitarbeiter(id);
        CREATE TABLE IF NOT EXISTS praemien (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, punkte INTEGER NOT NULL,
            bild TEXT NOT NULL DEFAULT '', farbe TEXT NOT NULL DEFAULT '#c41e3a',
            aktiv BOOLEAN NOT NULL DEFAULT TRUE, reihenfolge INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS push_nachrichten (
            id SERIAL PRIMARY KEY, titel TEXT NOT NULL DEFAULT 'Neues aus dem Laden',
            nachricht TEXT NOT NULL, aktiv BOOLEAN NOT NULL DEFAULT TRUE,
            erstellt_am TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
        ALTER TABLE push_nachrichten ADD COLUMN IF NOT EXISTS titel TEXT NOT NULL DEFAULT 'Neues aus dem Laden';
        ALTER TABLE push_nachrichten ADD COLUMN IF NOT EXISTS aktiv BOOLEAN NOT NULL DEFAULT TRUE;
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id SERIAL PRIMARY KEY, kunde_id INTEGER NOT NULL UNIQUE REFERENCES kunden(id) ON DELETE CASCADE,
            endpoint TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
            erstellt_am TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS personal_sitzungen (
            token_hash TEXT PRIMARY KEY, rolle TEXT NOT NULL CHECK (rolle IN ('chef','mitarbeiter')),
            mitarbeiter_id INTEGER REFERENCES mitarbeiter(id) ON DELETE CASCADE,
            credential_version TEXT NOT NULL, erstellt_am TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ablauf_am TIMESTAMPTZ NOT NULL, widerrufen BOOLEAN NOT NULL DEFAULT FALSE);
        CREATE INDEX IF NOT EXISTS idx_personal_ablauf ON personal_sitzungen(ablauf_am);
        CREATE TABLE IF NOT EXISTS login_versuche (
            bucket TEXT PRIMARY KEY, versuche INTEGER NOT NULL DEFAULT 0,
            fenster_am TIMESTAMPTZ NOT NULL DEFAULT NOW());
        CREATE TABLE IF NOT EXISTS bonus_katalog (
            produkt_id INTEGER PRIMARY KEY, varianten JSONB NOT NULL,
            aktiv BOOLEAN NOT NULL DEFAULT TRUE);
        ALTER TABLE bestellungen ADD COLUMN IF NOT EXISTS anfrage_id TEXT;
        ALTER TABLE bestellungen ADD COLUMN IF NOT EXISTS anfrage_hash TEXT;
        ALTER TABLE bestellungen ADD COLUMN IF NOT EXISTS vergebene_punkte INTEGER;
        UPDATE bestellungen SET vergebene_punkte=CASE WHEN kunde_id IS NOT NULL AND zahlungsart<>'BONUSPUNKTE' THEN FLOOR(gesamtbetrag)::integer ELSE 0 END WHERE status='BEZAHLT' AND vergebene_punkte IS NULL;
        ALTER TABLE punkte_bewegungen ADD COLUMN IF NOT EXISTS referenz TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_punkte_referenz ON punkte_bewegungen(referenz) WHERE referenz IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bestellungen_anfrage ON bestellungen(anfrage_id) WHERE anfrage_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_punkte_kunde ON punkte_bewegungen(kunde_id);
        CREATE INDEX IF NOT EXISTS idx_bestellpositionen_bestellung ON bestellpositionen(bestellung_id);
        CREATE TABLE IF NOT EXISTS sicherheitsprotokoll (
            id BIGSERIAL PRIMARY KEY, ereignis TEXT NOT NULL, rolle TEXT,
            objekt_id TEXT, erstellt_am TIMESTAMPTZ NOT NULL DEFAULT NOW());
        CREATE TABLE IF NOT EXISTS terminal_zugaenge (
            token_hash TEXT PRIMARY KEY, station TEXT NOT NULL,
            aktiv BOOLEAN NOT NULL DEFAULT TRUE, ablauf_am TIMESTAMPTZ NOT NULL);
        CREATE TABLE IF NOT EXISTS terminal_aktivierungen (
            code_hash TEXT PRIMARY KEY, station TEXT NOT NULL, ablauf_am TIMESTAMPTZ NOT NULL);
    """)
    # Stable across workers and deployments without disclosing the secret.
    cur.execute("INSERT INTO einstellungen(schluessel,wert) VALUES ('_session_secret',%s) ON CONFLICT DO NOTHING", (secrets.token_hex(48),))
    shop_key = os.environ.get('SHOP_KEY', 'kebab-hoehle-xxl').strip()
    if not shop_key or len(shop_key) > 100:
        raise RuntimeError('SHOP_KEY ist ungültig.')
    cur.execute("INSERT INTO einstellungen(schluessel,wert) VALUES ('_installation_shop_key',%s) ON CONFLICT DO NOTHING", (shop_key,))
    cur.execute("SELECT wert FROM einstellungen WHERE schluessel='_installation_shop_key'")
    if cur.fetchone()[0] != shop_key:
        raise RuntimeError('Diese Datenbank gehört zu einem anderen Laden. Eigene DATABASE_URL verwenden.')
    # Preserve all existing PINs while storing only salted password hashes.
    cur.execute('SELECT id,pin FROM mitarbeiter')
    for employee_id, pin in cur.fetchall():
        if pin and not pin.startswith(('scrypt:', 'pbkdf2:')):
            cur.execute('UPDATE mitarbeiter SET pin=%s WHERE id=%s', (generate_password_hash(pin), employee_id))
    cur.execute("SELECT wert FROM einstellungen WHERE schluessel='chef_pin'")
    row = cur.fetchone()
    pin = row[0] if row else os.environ.get('CHEF_PIN', '').strip()
    if pin and not pin.startswith(('scrypt:', 'pbkdf2:')):
        pin = generate_password_hash(pin)
    if pin:
        cur.execute("INSERT INTO einstellungen(schluessel,wert) VALUES ('chef_pin',%s) ON CONFLICT(schluessel) DO UPDATE SET wert=EXCLUDED.wert", (pin,))
    from praemien_katalog import PRAEMIEN_KATALOG
    for product_id, product in PRAEMIEN_KATALOG.items():
        cur.execute('INSERT INTO bonus_katalog(produkt_id,varianten) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING', (product_id, json.dumps(product['varianten'])))
    cur.execute('INSERT INTO schema_migrations(version) VALUES (2) ON CONFLICT DO NOTHING')
