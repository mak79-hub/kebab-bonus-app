"""Integration tests use ONLY an explicitly supplied localhost test database."""
import os
import pathlib
import secrets
import sys
from urllib.parse import urlsplit
import pytest
import psycopg2
from werkzeug.security import generate_password_hash

uri=os.environ.get('TEST_DATABASE_URL')
if not uri:
    pytest.exit('TEST_DATABASE_URL is required; production database access is forbidden',returncode=2)
parsed=urlsplit(uri)
if parsed.hostname not in ('127.0.0.1','localhost') or parsed.path not in ('/kebab_test','/postgres'):
    pytest.exit('Only a localhost test database is allowed',returncode=2)
os.environ['DATABASE_URL']=uri
os.environ['COOKIE_SECURE']='false'
os.environ['CHEF_PIN']=''
os.environ['SHOP_KEY']='kebab-hoehle-xxl'
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]))
import app as application


@pytest.fixture
def env(monkeypatch):
    app=application.app
    app.config['TESTING']=True
    chef_pin=''.join(secrets.choice('0123456789') for _ in range(8))
    staff_pin=''.join(secrets.choice('0123456789') for _ in range(8))
    customer_password=secrets.token_urlsafe(16)
    with psycopg2.connect(uri) as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE kunden,mitarbeiter,bestellungen,bestellpositionen,punkte_bewegungen,kunden_laeden,push_subscriptions,push_nachrichten,personal_sitzungen,login_versuche,terminal_zugaenge,terminal_aktivierungen,druckauftraege,sicherheitsprotokoll RESTART IDENTITY CASCADE')
            cur.execute("DELETE FROM einstellungen WHERE schluessel NOT IN ('_session_secret','_installation_shop_key')")
            cur.execute("INSERT INTO einstellungen(schluessel,wert) VALUES ('chef_pin',%s),('punkte_pro_euro','1')",(generate_password_hash(chef_pin),))
            cur.execute('INSERT INTO mitarbeiter(name,pin,aktiv) VALUES (%s,%s,TRUE) RETURNING id',('Synthetic Employee',generate_password_hash(staff_pin)));staff_id=cur.fetchone()[0]
            cur.execute("INSERT INTO kunden(kunden_id,vorname,nachname,geburtsdatum,passwort) VALUES ('KH-TEST-1','Synthetic','Customer','2000-01-01',%s) RETURNING id",(generate_password_hash(customer_password),));customer_id=cur.fetchone()[0]
            cur.execute('INSERT INTO kunden_laeden(kunde_id,laden_id) VALUES (%s,1)',(customer_id,))
            cur.execute('UPDATE bonus_katalog SET aktiv=TRUE')
            from praemien_katalog import PRAEMIEN_KATALOG
            for pid,p in PRAEMIEN_KATALOG.items():
                import json
                cur.execute('UPDATE bonus_katalog SET varianten=%s::jsonb WHERE produkt_id=%s',(json.dumps(p['varianten']),pid))
    original_status=application.aktueller_bestellstatus
    monkeypatch.setattr(application,'aktueller_bestellstatus',lambda zeitpunkt=None:{'offen':True,'ruhetag':False,'naechste_oeffnung':None,'server_zeit':'2026-10-28T13:00:00+01:00'})
    def forbidden(*a,**kw): raise AssertionError('External network access forbidden in integration tests')
    monkeypatch.setattr(application.urllib.request,'urlopen',forbidden)
    monkeypatch.setattr(application,'webpush',forbidden)
    return {'app':app,'module':application,'uri':uri,'chef_pin':chef_pin,'staff_pin':staff_pin,
            'customer_password':customer_password,'customer_id':customer_id,'staff_id':staff_id,'status':original_status}
