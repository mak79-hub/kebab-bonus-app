import os
import base64
import urllib.parse
import urllib.request
from io import BytesIO
from datetime import timedelta
from zoneinfo import ZoneInfo
import json
import psycopg2
import qrcode
from flask import Flask, request, render_template, url_for, redirect, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from pywebpush import webpush, WebPushException

app = Flask(__name__)


app.secret_key = os.environ.get("SECRET_KEY", "kebab-hoehe-test-secret-key")
app.permanent_session_lifetime = timedelta(days=365)

DATABASE_URL = os.environ.get("DATABASE_URL")
MITARBEITER_PIN = os.environ.get("MITARBEITER_PIN", "1234")
CHEF_PIN = os.environ.get("CHEF_PIN", "9999")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT")

PRAEMIEN = [
    {"name": "Ayran 0,25l", "punkte": 100, "bild": "ayran.png", "farbe": "#22c55e"},
    {"name": "Softdrink 0,33l", "punkte": 250, "bild": "cola.png", "farbe": "#ef4444"},
    {"name": "Pommes Classic", "punkte": 400, "bild": "pommes.png", "farbe": "#facc15"},
    {"name": "Pommes XXL", "punkte": 500, "bild": "pommes.png", "farbe": "#a855f7"},
    {"name": "Döner Sandwich Chicken Classic", "punkte": 800, "bild": "doener.png", "farbe": "#f97316"},
    {"name": "Döner Sandwich Beef Classic", "punkte": 1000, "bild": "doener.png", "farbe": "#dc2626"},
    {"name": "Wrap Chicken Classic", "punkte": 800, "bild": "durum.png", "farbe": "#06b6d4"},
    {"name": "Lahmacun Beef Classic", "punkte": 1100, "bild": "lahmacun.png", "farbe": "#8b5a2b"},
    {"name": "Pizza Margherita 32cm", "punkte": 800, "bild": "pizza.png", "farbe": "#3b82f6"},
    {"name": "Pizza Döner Chicken 32cm", "punkte": 1050, "bild": "pizza.png", "farbe": "#14b8a6"},
    {"name": "Pizza Mexicano 32cm", "punkte": 1250, "bild": "pizza.png", "farbe": "#ec4899"},
    {"name": "Döner Teller Chicken Classic", "punkte": 1200, "bild": "doenerteller.png", "farbe": "#eab308"},
    {"name": "Döner Teller Beef Classic", "punkte": 1400, "bild": "doenerteller.png", "farbe": "#6366f1"},
]


def get_db_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)

    return psycopg2.connect(
        host="localhost",
        database="kebab_assistent",
        user="postgres",
        password="Auto2026!"
    )

def get_einstellung(schluessel, standardwert=""):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT wert
        FROM einstellungen
        WHERE schluessel = %s
    """, (schluessel,))

    ergebnis = cur.fetchone()

    cur.close()
    conn.close()

    return ergebnis[0] if ergebnis else standardwert

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS kunden (
            id SERIAL PRIMARY KEY,
            kunden_id TEXT UNIQUE,
            vorname TEXT NOT NULL,
            nachname TEXT NOT NULL,
            geburtsdatum DATE NOT NULL,
            telefon TEXT,
            adresse TEXT,
            werbeeinwilligung BOOLEAN DEFAULT FALSE,
            werbeeinwilligung_am TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            erstellt_am TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS kunden_laeden (
            id SERIAL PRIMARY KEY,
            kunde_id INTEGER REFERENCES kunden(id),
            laden_id INTEGER DEFAULT 1,
            punkte INTEGER DEFAULT 0,
            letzter_besuch TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(kunde_id, laden_id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS punkte_bewegungen (
            id SERIAL PRIMARY KEY,
            kunde_id INTEGER REFERENCES kunden(id),
            typ TEXT NOT NULL,
            punkte INTEGER NOT NULL,
            erstellt_am TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cur.close()
    conn.close()


def make_qr_code(data):
    img = qrcode.make(data)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def get_punktestand(kunde_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(SUM(punkte), 0)
        FROM punkte_bewegungen
        WHERE kunde_id = %s
    """, (kunde_id,))

    punktestand = cur.fetchone()[0]

    cur.close()
    conn.close()

    return punktestand


def ist_mitarbeiter():
    return session.get("mitarbeiter_angemeldet") is True


def ist_chef():
    return session.get("chef_angemeldet") is True


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "Telegram ist nicht eingerichtet."

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, "Nachricht wurde erfolgreich an Telegram gesendet."
            return False, f"Telegram Fehler: Status {response.status}"
    except Exception as e:
        return False, f"Telegram Fehler: {str(e)}"

DE_ZEITZONE = ZoneInfo("Europe/Berlin")


def format_datetime(dt):
    if not dt:
        return "-"

    return dt.replace(tzinfo=ZoneInfo("UTC")) \
             .astimezone(DE_ZEITZONE) \
             .strftime("%d.%m.%Y %H:%M")


def app_style():
    return """
    <link rel="manifest" href="/static/manifest.webmanifest">
    <meta name="theme-color" content="#ff2b2b">
    <style>
        * { box-sizing: border-box; }

        
        :root {
            --page-bg: #080808;
            --card-bg: #171717;
            --box-bg: #252525;
            --box-bg-2: #1d1d1d;
            --input-bg: #0f0f0f;
            --table-bg: #222222;
            --table-head-bg: #111111;
        
            --text-main: #ffffff;
            --text-muted: #bbbbbb;
            --text-soft: #dddddd;
        
            --border-main: #333333;
            --border-soft: #444444;
        
            --shadow-main: rgba(0, 0, 0, 0.55);
        }
        
        html[data-theme="hell"] {
            --page-bg: #f2f4f7;
            --card-bg: #ffffff;
            --box-bg: #f4f5f7;
            --box-bg-2: #ffffff;
            --input-bg: #ffffff;
            --table-bg: #ffffff;
            --table-head-bg: #eceff3;
        
            --text-main: #171717;
            --text-muted: #555555;
            --text-soft: #333333;
        
            --border-main: #d1d5db;
            --border-soft: #c5c9d0;
        
            --shadow-main: rgba(0, 0, 0, 0.16);
        }
        
        
        
        body {
            margin: 0;
            font-family: Arial, Helvetica, sans-serif;
            background: var(--page-bg);
            color: var(--text-main);
            transition: background .25s, color .25s;
        }

        .page {
            min-height: 100vh;
            padding: 8px;
            display: flex;
            justify-content: center;
            align-items: flex-start;
        }

        .card {
            width: 100%;
            max-width: 820px;
            background: var(--card-bg);
            border: 1px solid var(--border-main);
            border-radius: 28px;
            padding: 34px;
            box-shadow: 0 20px 55px var(--shadow-main);
            transition: background .25s, color .25s, border-color .25s;
        }

        .wide-card {
            max-width: 1150px;
        }

        .logo {
            text-align: center;
            font-size: 48px;
            font-weight: 900;
            color: #ff2b2b;
            margin-bottom: 8px;
            letter-spacing: 1px;
        }

        .subtitle {
            text-align: center;
            color: var(--text-muted);
            margin-bottom: 34px;
            font-size: 30px;
            font-weight: 800;
        }

        .success-icon {
            width: 112px;
            height: 112px;
            border-radius: 50%;
            background: #1fa463;
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 0 auto 26px auto;
            font-size: 64px;
            font-weight: 900;
        }

        .success-title {
            text-align: center;
            font-size: 44px;
            margin-bottom: 12px;
        }

        .success-subtitle {
            text-align: center;
            color: var(--text-muted);
            font-size: 28px;
            margin-bottom: 32px;
        }

        .info-box {
            background: linear-gradient(180deg,#262626,#1d1d1d);
            border-radius: 28px;
            padding: 24px;
            margin-bottom: 24px;
        
            border: 2px solid #3a3a3a;
            border-left: 6px solid #ff2b2b;
        
            box-shadow: 0 10px 28px rgba(0,0,0,.45);
        
            transition: .25s;
        }

        html[data-theme="hell"] .info-box {
            background: linear-gradient(180deg, #ffffff, #f3f4f6);
            border-color: var(--border-main);
            border-left-color: #ff2b2b;
            box-shadow: 0 10px 28px rgba(0,0,0,.12);
        }

        html[data-theme="hell"] .info-box .label {
            color: #555555;
        }
        
        html[data-theme="hell"] .info-box .value {
            color: #171717;
        }
        
        .label {
            color: var(--text-muted);
            font-size: 24px;
            margin-bottom: 8px;
            font-weight: 800;
        }

        .value {
            font-size: 46px;
            font-weight: 900;
            margin-bottom: 24px;
            word-break: break-word;
        }

        .value:last-child {
            margin-bottom: 0;
        }

        .points {
            background: linear-gradient(135deg, #9d0000, #ff2b2b);
            border-radius: 28px;
            padding: 34px 18px;
            text-align: center;
            margin-bottom: 32px;
            box-shadow: 0 12px 30px rgba(255,43,43,0.25);
        }

        .points .number {
            font-size: 120px;
            line-height: 0.95;
            font-weight: 900;
        }

        .points .text {
            margin-top: 10px;
            font-size: 32px;
            color: #ffe2e2;
            font-weight: 900;
            letter-spacing: 1px;
            text-transform: uppercase;
        }

        .qr-box {
            background: white;
            border-radius: 30px;
            padding: 24px;
            text-align: center;
            margin: 28px auto;
            max-width: 500px;
        }

        .qr-box img {
            width: 430px;
            max-width: 100%;
        }

        .hint {
            background: var(--box-bg);
            border: 1px solid var(--border-soft);
            border-radius: 20px;
            padding: 22px;
            color: var(--text-soft);
            font-size: 24px;
            line-height: 1.45;
            margin-bottom: 26px;
        }

        input, textarea {
            width: 100%;
            padding: 28px;
            border-radius: 20px;
            border: 1px solid var(--border-soft);
            background: var(--input-bg);
            color: var(--text-main);
            font-size: 38px;
            margin-top: 12px;
            margin-bottom: 28px;
            outline: none;
            font-family: Arial, Helvetica, sans-serif;
        }

        textarea {
            min-height: 260px;
            resize: vertical;
            line-height: 1.35;
        }

        input:focus, textarea:focus { border-color: #ff2b2b; }

        button, .btn {
            width: 100%;
            border: none;
            border-radius: 20px;
            padding: 28px;
            font-size: 34px;
            font-weight: 900;
            cursor: pointer;
            text-decoration: none;
            display: block;
            text-align: center;
        }

        .btn + .btn {
            margin-top: 18px;
        }
        .menu-grid{
        display:grid;
        grid-template-columns:repeat(2,1fr);
        gap:18px;
        margin:30px 0;
        }

        .menu-box{
        display:flex;
        align-items:center;
        justify-content:center;
        background:linear-gradient(180deg,#2a2a2a,#1d1d1d);
        border:2px solid #3a3a3a;
        border-radius:22px;
        color:white;
        text-decoration:none;
        font-size:28px;
        font-weight:900;
        min-height:110px;
        transition:.2s;
        }

        .menu-box:hover{
        border-color:#ff2b2b;
        box-shadow:0 0 20px rgba(255,43,43,.35);
        transform:translateY(-2px);
        }
        
        .menu-blue{
        border-color:#3b82f6;
        background:linear-gradient(180deg,#1e293b,#172033);
        }

        .menu-green{
        border-color:#22c55e;
        background:linear-gradient(180deg,#12351f,#10281a);
        }

        .menu-orange{
        border-color:#f59e0b;
        background:linear-gradient(180deg,#3a2710,#261a0b);
        }

        .menu-purple{
        border-color:#a855f7;
        background:linear-gradient(180deg,#2b1d3a,#1d1328);
        }

        .menu-red{
        border-color:#ef4444;
        background:linear-gradient(180deg,#3a1616,#261010);
        }

        .menu-gray{
        border-color:#6b7280;
        background:linear-gradient(180deg,#2a2a2a,#1c1c1c);
        }
        .btn-red {
            background: #ff2b2b;
            color: white;
        }

        .btn-dark {
            background: #2b2b2b;
            color: white;
            border: 1px solid #444;
            margin-top: 18px;
        }

        .btn-green {
            background: #1fa463;
            color: white;
        }

        .btn-orange {
            background: #ff9800;
            color: #111;
        }

        .message {
            padding: 24px;
            border-radius: 20px;
            margin-bottom: 26px;
            background: var(--box-bg);
            border: 1px solid var(--border-soft);
            text-align: center;
            font-size: 28px;
            font-weight: 900;
        }

        .section-title {
            font-size: 38px;
            font-weight: 900;
            margin-bottom: 18px;
        }

        .divider {
            height: 1px;
            background: #333;
            margin: 36px 0;
        }
        .section-title-locked{
            background:linear-gradient(90deg,#ff9800,#ffb300);
            color:white;
            padding:28px 32px;
            border-radius:18px;
            display:block;
            width:100%;
            box-sizing:border-box;
            font-size:34px;
            font-weight:900;
            box-shadow:0 0 25px rgba(255,152,0,.45);
            margin:28px 0 24px 0;
        }
        .small-link {
            display: block;
            text-align: center;
            color: #aaa;
            margin-top: 26px;
            text-decoration: none;
            font-size: 24px;
        }

        .danger-note {
            color: #ffb3b3;
            background: #351818;
            border: 1px solid #6b2222;
            padding: 22px;
            border-radius: 20px;
            margin-bottom: 24px;
            font-size: 24px;
            line-height: 1.4;
        }

        #reader {
            background: #0f0f0f;
            border: 2px solid #555;
            border-radius: 30px;
            overflow: hidden;
            margin-bottom: 26px;
            min-height: 620px;
        }

        #reader video {
            width: 100% !important;
            min-height: 560px !important;
            object-fit: cover !important;
            border-radius: 30px;
        }

        #reader select {
            font-size: 24px !important;
            padding: 16px !important;
            height: auto !important;
            min-height: 58px !important;
            border-radius: 14px !important;
            margin-top: 12px !important;
            margin-bottom: 12px !important;
        }

        #reader button {
            font-size: 30px !important;
            padding: 22px !important;
            border-radius: 18px !important;
            font-weight: 900 !important;
        }

        .stat-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 18px;
            margin-bottom: 30px;
        }

        .stat-box {
            background: var(--box-bg);
            border: 1px solid var(--border-soft);
            border-radius: 22px;
            padding: 24px;
            text-align: center;
        }

        .stat-label {
            color: var(--text-muted);
            font-size: 20px;
            font-weight: 800;
            margin-bottom: 10px;
        }

        .stat-value {
            font-size: 46px;
            font-weight: 900;
            color: var(--text-main);
        }

        .history-table-wrap {
            width: 100%;
            overflow-x: auto;
            background: var(--table-bg);
            border: 1px solid var(--border-soft);
            border-radius: 20px;
            margin-bottom: 30px;
        }

        .history-table {
            width: 100%;
            border-collapse: collapse;
            min-width: 850px;
        }

        .history-table th,
        .history-table td {
            padding: 16px;
            border-bottom: 1px solid var(--border-soft);
            text-align: left;
            font-size: 18px;
        }

        .history-table th {
            background: var(--table-head-bg);
            color: #ffb3b3;
            font-size: 18px;
        }

        .history-table tr:last-child td {
            border-bottom: none;
        }

        @media (max-width: 600px) {
            .page {
                padding: 6px;
            }

            .card {
                max-width: none;
                width: 100%;
                border-radius: 24px;
                padding: 28px;
            }

            .logo {
                font-size: 42px;
            }

            .subtitle {
                font-size: 28px;
                margin-bottom: 30px;
            }

            .success-icon {
                width: 104px;
                height: 104px;
                font-size: 60px;
            }

            .success-title {
                font-size: 40px;
            }

            .success-subtitle {
                font-size: 26px;
            }

            .label {
                font-size: 24px;
            }

            .value {
                font-size: 44px;
            }

            .points .number {
                font-size: 118px;
            }

            .points .text {
                font-size: 32px;
            }

            .section-title {
                font-size: 38px;
            }

            input, textarea {
                font-size: 38px;
                padding: 28px;
            }

            button, .btn {
                font-size: 34px;
                padding: 28px;
            }

            .qr-box {
                max-width: 430px;
                padding: 22px;
            }

            .qr-box img {
                width: 385px;
            }

            #reader {
                min-height: 650px !important;
                max-width: 100% !important;
            }

            #reader video {
                min-height: 590px !important;
            }

            #reader select {
                font-size: 24px !important;
                padding: 16px !important;
                min-height: 58px !important;
            }

            #reader button {
                font-size: 30px !important;
                padding: 22px !important;
            }

            .stat-grid {
                grid-template-columns: 1fr;
            }
        }.status-banner{
            background:linear-gradient(90deg,#0d5f16,#1dbb35);
            color:#fff;
            padding:20px;
            border-radius:18px;
            margin:25px 0;
            box-shadow:0 0 20px rgba(0,255,100,.35);
            font-size:34px;
            font-weight:900;
        }
        
        .status-sub{
            font-size:18px;
            font-weight:600;
            margin-top:8px;
            color:#eaffea;
        }
        .product-row{
            display:flex;
            align-items:center;
            gap:18px;
        }
        .product-image{
            width:110px;
            height:110px;
            object-fit:cover;
            border-radius:18px;
            flex-shrink:0;
            box-shadow:0 0 18px rgba(255,43,43,.25);
        }
        
        .product-info{
            flex:1;
        }
        
        .product-name{
            font-size:34px;
            font-weight:900;
            line-height:1.15;
            margin-bottom:14px;
        }
        
        .points-badge{
            display:inline-block;
            background:linear-gradient(135deg,#9d0000,#ff2b2b);
            color:#fff;
            padding:10px 16px;
            border-radius:16px;
            font-size:22px;
            font-weight:900;
            box-shadow:0 0 14px rgba(255,43,43,.35);
        }
        
        .status-ok{
            background:linear-gradient(135deg,#0b7a15,#20d63a);
            color:#fff;
            padding:12px 16px;
            border-radius:16px;
            font-size:18px;
            font-weight:900;
            text-align:center;
            min-width:105px;
            box-shadow:0 0 14px rgba(0,255,80,.35);
        }
        
        .status-no{
            background:linear-gradient(135deg,#ff9800,#ffb300);
            color:white;
            padding:12px 16px;
            border-radius:16px;
            font-size:18px;
            font-weight:900;
            text-align:center;
            min-width:105px;
            box-shadow:0 0 14px rgba(255,152,0,.35);
        }
        
        .info-box{
            overflow:hidden;
        }
    </style>
    <script>
    (function() {{
        const gespeicherterModus =
            localStorage.getItem("farbmodus") || "dunkel";
    
        document.documentElement.setAttribute(
            "data-theme",
            gespeicherterModus
        );
    }})();
    
    function farbmodusWechseln(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
    
        const aktuellerModus =
            document.documentElement.getAttribute("data-theme") || "dunkel";
    
        const neuerModus =
            aktuellerModus === "hell" ? "dunkel" : "hell";
    
        document.documentElement.setAttribute("data-theme", neuerModus);
    
        localStorage.setItem("farbmodus", neuerModus);
    
        return false;
    }}
    </script>
    
    <script>
    if ("serviceWorker" in navigator) {
        window.addEventListener("load", function () {
            navigator.serviceWorker.register("/sw.js");
        });
    }
    </script>
    
    """


def auto_back_to_scanner_page(titel, text):
    return f"""
    {app_style()}
    <meta http-equiv="refresh" content="2;url=/scanner">
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">{titel}</div>

            <div class="success-icon">✓</div>

            <div class="message">{text}</div>

            <div class="hint">
                Du wirst automatisch zurück zum Scanner geleitet.
            </div>

            <a class="btn btn-red" href="/scanner">Sofort zurück zum Scanner</a>
            <a class="btn btn-dark" href="/mitarbeiter">Manuell suchen</a>
        </div>
    </div>
    """


init_db()

@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(
        "static",
        "service-worker.js",
        mimetype="application/javascript"
    )


@app.route("/push-public-key")
def push_public_key():
    return {
        "publicKey": VAPID_PUBLIC_KEY
    }


@app.route("/push-subscribe", methods=["POST"])
def push_subscribe():
    daten = request.get_json(silent=True)

    if not daten:
        return {
            "success": False,
            "message": "Keine Push-Daten empfangen."
        }, 400

    kunde_id = daten.get("kunde_id")
    endpoint = daten.get("endpoint")
    keys = daten.get("keys", {})

    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not kunde_id or not endpoint or not p256dh or not auth:
        return {
            "success": False,
            "message": "Push-Daten sind unvollständig."
        }, 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO push_subscriptions (
                kunde_id,
                endpoint,
                p256dh,
                auth
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (kunde_id)
            DO UPDATE SET
                endpoint = EXCLUDED.endpoint,
                p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth,
                erstellt_am = CURRENT_TIMESTAMP
        """, (
            kunde_id,
            endpoint,
            p256dh,
            auth
        ))

        conn.commit()

        return {
            "success": True,
            "message": "Push-Benachrichtigungen wurden aktiviert."
        }

    except Exception as fehler:
        conn.rollback()
        print("Push-Abonnement Fehler:", fehler)

        return {
            "success": False,
            "message": "Push-Abonnement konnte nicht gespeichert werden."
        }, 500

    finally:
        cur.close()
        conn.close()
    
@app.route("/")
def startseite():

    if session.get("chef_angemeldet"):
        return redirect("/chef-dashboard")

    if session.get("mitarbeiter_angemeldet"):
        return redirect("/mitarbeiter")

    kunden_id = session.get("kunde_id")

    if kunden_id:
        return redirect(
            url_for("kunde", kunden_id=kunden_id)
        )

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">DÖNI BONUS</div>
            <div class="subtitle">Willkommen</div>

            <a class="btn btn-red" href="/register">
                Registrieren
            </a>

            <a class="btn btn-dark" href="/login">
                Einloggen
            </a>
        </div>
    </div>
    """
@app.route("/login", methods=["GET", "POST"])
def login():
    fehler = ""

    if request.method == "POST":
        kennung = request.form.get("kennung", "").strip()
        passwort = request.form.get("passwort", "").strip()

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, kunden_id, vorname, nachname, telefon, passwort
            FROM kunden
            WHERE kunden_id = %s
               OR telefon = %s
               OR LOWER(vorname || ' ' || nachname) = LOWER(%s)
            LIMIT 1
        """, (
            kennung,
            kennung,
            kennung
        ))

        kunde = cur.fetchone()

        cur.close()
        conn.close()

        if kunde and kunde[5] and check_password_hash(kunde[5], passwort):
            session.permanent = True
            session["kunde_id"] = kunde[1]
        
            return redirect(
                url_for("kunde", kunden_id=kunde[1])
            )

        fehler = "❌ Kundenangaben oder Passwort sind falsch."

    fehler_html = ""

    if fehler:
        fehler_html = f"""
        <div style="
            background: #3b1111;
            color: #ff6b6b;
            border: 2px solid #ff2b2b;
            border-radius: 12px;
            padding: 14px;
            margin-bottom: 20px;
            text-align: center;
            font-weight: bold;
        ">
            {fehler}
        </div>
        """

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">DÖNİ BONUS</div>
            <div class="subtitle">Einloggen</div>

            {fehler_html}

            <form method="POST">

                <label>Name, Telefon oder Kunden-ID</label>
                <input
                    type="text"
                    name="kennung"
                    value="{request.form.get('kennung', '')}"
                    required
                >

                <label>Passwort</label>
                <input type="password" name="passwort" required>

                <button class="btn btn-red" type="submit">
                    Einloggen
                </button>

                <a class="btn btn-dark" href="/">
                    Zurück
                </a>

            </form>
        </div>
    </div>
    """

    
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        vorname = request.form.get("vorname", "").strip()
        nachname = request.form.get("nachname", "").strip()
        geburtsdatum = request.form.get("geburtsdatum")
        telefon = request.form.get("telefon", "").strip()
        adresse = request.form.get("adresse", "").strip()
        
        passwort = request.form.get("passwort", "").strip()
        passwort2 = request.form.get("passwort2", "").strip()
        
        angebote = request.form.get("angebote") == "on"
        if passwort != passwort2:
            return "Die Passwörter stimmen nicht überein."
        
        if len(passwort) < 4:
            return "Das Passwort muss mindestens 4 Zeichen lang sein."
        
        passwort_hash = generate_password_hash(passwort)
        
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, kunden_id
            FROM kunden
            WHERE LOWER(vorname) = LOWER(%s)
              AND LOWER(nachname) = LOWER(%s)
              AND geburtsdatum = %s
            LIMIT 1
        """, (vorname, nachname, geburtsdatum))

        vorhandener_kunde = cur.fetchone()

        if vorhandener_kunde:
            kunde_db_id = vorhandener_kunde[0]
            kunden_id = vorhandener_kunde[1]
        else:
            cur.execute("""
                INSERT INTO kunden (
                    vorname, nachname, geburtsdatum, telefon, adresse,
                    passwort,
                    werbeeinwilligung, werbeeinwilligung_am
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (
                vorname,
                nachname,
                geburtsdatum,
                telefon,
                adresse,
                passwort_hash,
                angebote
            ))

            kunde_db_id = cur.fetchone()[0]
            kunden_id = f"KH-{30000 + kunde_db_id}"

            cur.execute("""
                UPDATE kunden
                SET kunden_id = %s
                WHERE id = %s
            """, (kunden_id, kunde_db_id))

        cur.execute("""
            INSERT INTO kunden_laeden (kunde_id, laden_id, punkte, letzter_besuch)
            VALUES (%s, 1, 0, CURRENT_TIMESTAMP)
            ON CONFLICT (kunde_id, laden_id) DO NOTHING
        """, (kunde_db_id,))

        conn.commit()
        cur.close()
        conn.close()

        qr_data = url_for("kunde", kunden_id=kunden_id, _external=True)
        qr_code = make_qr_code(qr_data)
        punktestand = get_punktestand(kunde_db_id)

        return f"""
        {app_style()}
        <div class="page">
            <div class="card">
                <div class="logo">KEBAB HÖHLE</div>
                <div class="subtitle">Bonusprogramm</div>

                <div class="success-icon">✓</div>

                <h1 class="success-title">Registrierung erfolgreich</h1>
                <p class="success-subtitle">
                    Willkommen {vorname} {nachname}
                </p>

                <div class="info-box">
                    <div class="label">Kunden-ID</div>
                    <div class="value">{kunden_id}</div>

                    <div class="label">Name</div>
                    <div class="value">{vorname} {nachname}</div>
                </div>

                <div class="points">
                    <div class="number">{punktestand}</div>
                    <div class="text">Punkte</div>
                </div>

                <div class="qr-box">
                    <img src="data:image/png;base64,{qr_code}">
                </div>

                <div class="hint">
                    Bitte speichere diesen QR-Code oder mache einen Screenshot.
                    Dieser QR-Code wird im Laden zum Sammeln und Einlösen der Punkte benötigt.
                </div>
                <a class="btn btn-red" href="/kunde/{kunden_id}/praemien">
                    🎁 Prämien ansehen
                </a>
                
                
                <a class="small-link" href="/">Neuen Kunden registrieren</a>
            </div>
        </div>
        """

    return render_template("register.html")


@app.route("/kunde/<kunden_id>")
def kunde(kunden_id):
    kunden_id = kunden_id.strip().upper()

    if ist_mitarbeiter():
        return redirect(f"/mitarbeiter/{kunden_id}")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, kunden_id, vorname, nachname
        FROM kunden
        WHERE kunden_id = %s
    """, (kunden_id,))

    kunde_daten = cur.fetchone()

    cur.execute("""
        SELECT
            name,
            punkte,
            bild,
            farbe
        FROM praemien
        WHERE aktiv = TRUE
        ORDER BY reihenfolge, id
    """)

    praemien_daten = cur.fetchall()
    angebot_id = request.args.get("angebot", type=int)

    if angebot_id:
        cur.execute("""
            SELECT id, titel, nachricht, erstellt_am
            FROM push_nachrichten
            WHERE id = %s
              AND aktiv = TRUE
            LIMIT 1
        """, (angebot_id,))
    else:
        cur.execute("""
            SELECT id, titel, nachricht, erstellt_am
            FROM push_nachrichten
            WHERE aktiv = TRUE
            ORDER BY erstellt_am DESC, id DESC
            LIMIT 1
        """)

    aktuelles_angebot = cur.fetchone()
    
    cur.close()
    conn.close()

    if not kunde_daten:
        return f"""
        {app_style()}
        <div class="page">
            <div class="card">
                <div class="logo">KEBAB HÖHLE</div>
                <div class="subtitle">Bonusprogramm</div>
                <div class="message">❌ Kunde nicht gefunden.</div>
                <a class="btn btn-red" href="/">Zur Registrierung</a>
            </div>
        </div>
        """

    punktestand = get_punktestand(kunde_daten[0])
    qr_data = url_for("kunde", kunden_id=kunde_daten[1], _external=True)
    qr_code = make_qr_code(qr_data)

    angebot_html = ""

    if aktuelles_angebot:
        angebot_datum = format_datetime(aktuelles_angebot[3])

        angebot_html = f"""
        <div class="info-box" style="
            margin-top:24px;
            border-left:6px solid #ffcc00;
        ">
            <div style="
                font-size:25px;
                font-weight:900;
                margin-bottom:14px;
            ">
                📢 Aktuelles Angebot
            </div>

            <div style="
                font-size:22px;
                font-weight:800;
                margin-bottom:12px;
            ">
                {aktuelles_angebot[1]}
            </div>

            <div style="
                font-size:20px;
                line-height:1.5;
                white-space:pre-wrap;
            ">{aktuelles_angebot[2]}</div>

            <div style="
                margin-top:16px;
                font-size:15px;
                opacity:0.7;
            ">
                Gesendet am {angebot_datum}
            </div>
        </div>
        """
    
    
    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Bonusprogramm</div>

            <div class="info-box">
                <div class="label">Kunden-ID</div>
                <div class="value">{kunde_daten[1]}</div>

                <div class="label">Name</div>
                <div class="value">{kunde_daten[2]} {kunde_daten[3]}</div>
            </div>

            <div class="points">
                <div class="number">{punktestand}</div>
                <div class="text">Punkte</div>
            </div>

            <div class="qr-box">
                <img src="data:image/png;base64,{qr_code}">
            </div>

            <div class="hint">
                Zeige diesen QR-Code im Laden vor, um Punkte zu sammeln oder einzulösen.
            </div>
            
            <a class="btn btn-red" href="/kunde/{kunde_daten[1]}/praemien">
                🎁 Prämien ansehen
            </a>
            
            <button
                id="pushButton"
                class="btn btn-dark"
                type="button"
                onclick="pushAktivieren()"
            >
                🔔 Benachrichtigungen aktivieren
            </button>
            
            <div
                id="pushMeldung"
                class="hint"
                style="display:none;"
            ></div>
            {angebot_html}
            <script>
            function urlBase64ToUint8Array(base64String) {{
                const padding = "=".repeat((4 - base64String.length % 4) % 4);
                const base64 = (base64String + padding)
                    .replace(/-/g, "+")
                    .replace(/_/g, "/");
            
                const rawData = window.atob(base64);
                return Uint8Array.from(
                    [...rawData].map(char => char.charCodeAt(0))
                );
            }}
            
            function zeigePushMeldung(text, erfolgreich = false) {{
                const meldung = document.getElementById("pushMeldung");
            
                meldung.style.display = "block";
                meldung.textContent = text;
            
                if (erfolgreich) {{
                    meldung.style.borderColor = "#22c55e";
                    meldung.style.color = "#22c55e";
                }} else {{
                    meldung.style.borderColor = "#ff2b2b";
                    meldung.style.color = "#ff6b6b";
                }}
            }}
            
            async function pushAktivieren() {{
                const button = document.getElementById("pushButton");
                button.disabled = true;
                button.textContent = "Bitte warten …";
            
                try {{
                    if (!("serviceWorker" in navigator)) {{
                        throw new Error(
                            "Dieser Browser unterstützt keine Benachrichtigungen."
                        );
                    }}
            
                    if (!("PushManager" in window)) {{
                        throw new Error(
                            "Push-Benachrichtigungen werden auf diesem Gerät nicht unterstützt."
                        );
                    }}
            
                    const erlaubnis = await Notification.requestPermission();
            
                    if (erlaubnis !== "granted") {{
                        throw new Error(
                            "Die Benachrichtigungen wurden nicht erlaubt."
                        );
                    }}
            
                    const registration = await navigator.serviceWorker.register(
                        "/service-worker.js"
                    );
            
                    await navigator.serviceWorker.ready;
            
                    const keyAntwort = await fetch("/push-public-key");
            
                    if (!keyAntwort.ok) {{
                        throw new Error(
                            "Der öffentliche Push-Schlüssel konnte nicht geladen werden."
                        );
                    }}
            
                    const keyDaten = await keyAntwort.json();
            
                    if (!keyDaten.publicKey) {{
                        throw new Error(
                            "Der öffentliche Push-Schlüssel fehlt."
                        );
                    }}
            
                    let subscription =
                        await registration.pushManager.getSubscription();
            
                    if (!subscription) {{
                        subscription = await registration.pushManager.subscribe({{
                            userVisibleOnly: true,
                            applicationServerKey: urlBase64ToUint8Array(
                                keyDaten.publicKey
                            )
                        }});
                    }}
            
                    const subscriptionDaten = subscription.toJSON();
            
                    const speichernAntwort = await fetch("/push-subscribe", {{
                        method: "POST",
                        headers: {{
                            "Content-Type": "application/json"
                        }},
                        body: JSON.stringify({{
                            kunde_id: {kunde_daten[0]},
                            endpoint: subscription.endpoint,
                            keys: subscriptionDaten.keys
                        }})
                    }});
            
                    const speichernDaten = await speichernAntwort.json();
            
                    if (!speichernAntwort.ok || !speichernDaten.success) {{
                        throw new Error(
                            speichernDaten.message ||
                            "Das Abonnement konnte nicht gespeichert werden."
                        );
                    }}
            
                    zeigePushMeldung(
                        "✅ Benachrichtigungen wurden aktiviert.",
                        true
                    );
            
                    button.textContent = "✅ Benachrichtigungen aktiviert";
                    button.disabled = true;
            
                }} catch (fehler) {{
                    console.error("Push-Fehler:", fehler);
            
                    zeigePushMeldung(
                        "❌ " + fehler.message
                    );
            
                    button.textContent = "🔔 Erneut versuchen";
                    button.disabled = false;
                }}
            }}
            </script>
            
            </div>
            </div>
            """
@app.route("/kunde/<kunden_id>/praemien")
def kunde_praemien(kunden_id):
    kunden_id = kunden_id.strip().upper()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, kunden_id, vorname, nachname
        FROM kunden
        WHERE kunden_id = %s
    """, (kunden_id,))
    
    kunde_daten = cur.fetchone()
    
    cur.execute("""
        SELECT
            name,
            punkte,
            bild,
            farbe
        FROM praemien
        WHERE aktiv = TRUE
        ORDER BY reihenfolge, id
    """)
    
    praemien_daten = cur.fetchall()
    
    cur.close()
    conn.close()

    if not kunde_daten:
        return f"""
        {app_style()}
        <div class="page">
            <div class="card">
                <div class="logo">KEBAB HÖHLE</div>
                <div class="subtitle">Prämien</div>
                <div class="message">❌ Kunde nicht gefunden.</div>
                <a class="btn btn-red" href="/">Zur Registrierung</a>
            </div>
        </div>
        """

    punktestand = get_punktestand(kunde_daten[0])

    einloesbar_html = ""
    nicht_einloesbar_html = ""

    praemien = []

    for p in praemien_daten:
        praemien.append({
            "name": p[0],
            "punkte": p[1],
            "bild": p[2],
            "farbe": p[3]
        })
    
    for praemie in sorted(
        praemien,
        key=lambda p: (
            0 if punktestand >= p["punkte"] else 1,
            0 if punktestand >= p["punkte"] else p["punkte"] - punktestand
        )
    ):
        if punktestand >= praemie["punkte"]:
            einloesbar_html += f"""
            <div class="info-box" style="border-left:6px solid {praemie['farbe']};">
                <div class="product-row">
                    <img src="/static/images/{praemie['bild']}" class="product-image">

                    <div class="product-info">
                        <div class="product-name">{praemie["name"]}</div>
                        <div class="points-badge">🪙 {praemie["punkte"]} Punkte</div>
                    </div>

                    <div class="status-ok">✅ Einlösbar</div>
                </div>
            </div>
            """
        else:
            fehlt = praemie["punkte"] - punktestand
            nicht_einloesbar_html += f"""
            <div class="info-box" style="border-left:6px solid {praemie['farbe']};">
                <div class="product-row locked">
                    <img src="/static/images/{praemie['bild']}" class="product-image">

                    <div class="product-info">
                        <div class="product-name">{praemie["name"]}</div>
                        <div class="points-badge">🪙 {praemie["punkte"]} Punkte</div>
                    </div>

                    <div class="status-no">🔒 Noch {fehlt}</div>
                </div>
            </div>
            """

    if not einloesbar_html:
        einloesbar_html = """
        <div class="hint">
            Aktuell ist noch keine Prämie einlösbar.
        </div>
        """

    if not nicht_einloesbar_html:
        nicht_einloesbar_html = """
        <div class="hint">
            Du kannst aktuell alle verfügbaren Prämien einlösen.
        </div>
        """

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Deine Prämien</div>

            <div class="info-box">
                <div class="label">Kunde</div>
                <div class="value">{kunde_daten[2]} {kunde_daten[3]}</div>

                <div class="label">Aktuelle Punkte</div>
                <div class="value">{punktestand}</div>
            </div>

            <div class="status-banner">
                <div class="status-icon">🎁</div>

                <div class="status-text">
                    <div class="status-title">SOFORT EINLÖSBAR</div>
                    <div class="status-sub">
                        Wähle deine Prämie und löse sie direkt ein.
                    </div>
                </div>
            </div>

            {einloesbar_html}

            <div class="divider"></div>
            <div class="section-title section-title-locked">
                🔒 Noch nicht einlösbar
            </div>

            {nicht_einloesbar_html}

            <a class="btn btn-dark" href="/kunde/{kunde_daten[1]}">Zurück zur Kundenkarte</a>
        </div>
    </div>
    """

@app.route("/mitarbeiter-login", methods=["GET", "POST"])
def mitarbeiter_login():
    meldung = ""
    next_url = request.args.get("next", "/mitarbeiter")

    if request.method == "POST":
        pin = request.form.get("pin", "").strip()
        next_url = request.form.get("next", "/mitarbeiter")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, name
            FROM mitarbeiter
            WHERE pin = %s
              AND aktiv = TRUE
            LIMIT 1
        """, (pin,))

        mitarbeiter = cur.fetchone()

        if mitarbeiter:
            cur.execute("""
                UPDATE mitarbeiter
                SET letzter_login = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (mitarbeiter[0],))

            conn.commit()
            cur.close()
            conn.close()

            session.permanent = True
            session["mitarbeiter_angemeldet"] = True
            session["mitarbeiter_id"] = mitarbeiter[0]
            session["mitarbeiter_name"] = mitarbeiter[1]

            return redirect(next_url)

        cur.close()
        conn.close()
        meldung = "❌ Falsche PIN oder Mitarbeiter ist inaktiv."

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiter Login</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <form method="POST">

                <div class="section-title">
                    Persönliche Mitarbeiter-PIN
                </div>

                <label class="label">PIN</label>

                <input
                    type="password"
                    name="pin"
                    placeholder="Persönliche PIN eingeben"
                    inputmode="numeric"
                    required
                >

                <input
                    type="hidden"
                    name="next"
                    value="{next_url}"
                >

                <button class="btn-red" type="submit">
                    Einloggen
                </button>

            </form>

            <a class="small-link" href="/">
                Zur Registrierung
            </a>

        </div>
    </div>
    """


@app.route("/mitarbeiter-logout")
def mitarbeiter_logout():
    session.pop("mitarbeiter_angemeldet", None)
    session.pop("mitarbeiter_id", None)
    session.pop("mitarbeiter_name", None)

    return redirect("/mitarbeiter-login")


@app.route("/mitarbeiter", methods=["GET", "POST"])
def mitarbeiter():
    if not ist_mitarbeiter():
        return redirect("/mitarbeiter-login?next=/mitarbeiter")
    mitarbeiter_name = session.get("mitarbeiter_name", "Unbekannter Mitarbeiter")
   
    if request.method == "POST":
        kunden_id = request.form.get("kunden_id", "").strip().upper()
        return redirect(f"/mitarbeiter/{kunden_id}")

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiterbereich</div>

            <div class="info-box">
                <div class="label">Angemeldet als</div>
                <div class="value">👤 {mitarbeiter_name}</div>
            </div>

            <a class="btn btn-red" href="/scanner">QR-Code scannen</a>

            <div class="divider"></div>

            <form method="POST">
                <div class="section-title">Kunden manuell suchen</div>
                <label class="label">Kunden-ID eingeben</label>
                <input type="text" name="kunden_id" placeholder="KH-30001" required oninput="this.value = this.value.toUpperCase()">
                <button class="btn-red" type="submit">Kunden öffnen</button>
            </form>

            <a class="btn btn-dark" href="/mitarbeiter-logout">Abmelden</a>
        </div>
    </div>
    """


@app.route("/scanner")
def scanner():
    if not ist_mitarbeiter():
        return redirect("/mitarbeiter-login?next=/scanner")

    return f"""
    {app_style()}
    <script src="https://unpkg.com/html5-qrcode"></script>

    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">QR-Code Scanner</div>

            <div class="hint">
                Kunden-QR-Code vor die Kamera halten. Der Kunde wird automatisch geöffnet.
            </div>

            <div id="reader" style="width:100%; max-width:780px; margin:0 auto 26px auto;"></div>
            <div id="scan-message" class="message" style="display:none;"></div>

            <a class="btn btn-dark" href="/mitarbeiter">Manuell suchen</a>
            <a class="small-link" href="/mitarbeiter-logout">Abmelden</a>
        </div>
    </div>

    <script>
        let alreadyScanned = false;

        function showMessage(text) {{
            const box = document.getElementById("scan-message");
            box.style.display = "block";
            box.innerText = text;
        }}

        function onScanSuccess(decodedText) {{
            if (alreadyScanned) return;

            let kundenId = null;
            decodedText = decodedText.trim();

            const matchUrl = decodedText.match(/\\/kunde\\/(KH-\\d+)/i);
            const matchPlain = decodedText.match(/^(KH-\\d+)$/i);

            if (matchUrl) {{
                kundenId = matchUrl[1].toUpperCase();
            }} else if (matchPlain) {{
                kundenId = matchPlain[1].toUpperCase();
            }}

            if (kundenId) {{
                alreadyScanned = true;
                showMessage("✅ Kunde erkannt: " + kundenId);
                window.location.href = "/mitarbeiter/" + kundenId;
            }} else {{
                showMessage("❌ Kein gültiger Kebab-Höhle Kunden-QR-Code.");
            }}
        }}

        function onScanFailure(error) {{
            // Keine dauernden Fehlermeldungen anzeigen.
        }}

        const scanner = new Html5QrcodeScanner(
            "reader",
            {{
                fps: 10,
                qrbox: {{ width: 460, height: 460 }},
                rememberLastUsedCamera: true,
                supportedScanTypes: [Html5QrcodeScanType.SCAN_TYPE_CAMERA]
            }},
            false
        );

        scanner.render(onScanSuccess, onScanFailure);
    </script>
    """


@app.route("/mitarbeiter/<kunden_id>", methods=["GET", "POST"])
def mitarbeiter_kunde(kunden_id):
    kunden_id = kunden_id.strip().upper()

    if not ist_mitarbeiter():
        return redirect(f"/mitarbeiter-login?next=/mitarbeiter/{kunden_id}")

    meldung = ""

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, kunden_id, vorname, nachname
        FROM kunden
        WHERE kunden_id = %s
    """, (kunden_id,))

    kunde_daten = cur.fetchone()

    if not kunde_daten:
        cur.close()
        conn.close()
        return f"""
        {app_style()}
        <div class="page">
            <div class="card">
                <div class="logo">KEBAB HÖHLE</div>
                <div class="subtitle">Kunde nicht gefunden</div>
                <div class="message">❌ Diese Kunden-ID wurde nicht gefunden.</div>
                <a class="btn btn-red" href="/mitarbeiter">Zurück</a>
                <a class="btn btn-dark" href="/scanner">QR-Code scannen</a>
            </div>
        </div>
        """

    kunde_db_id = kunde_daten[0]

    if request.method == "POST":
        betrag_text = request.form.get("betrag", "0").replace(",", ".")

        try:
            betrag = float(betrag_text)
            punkte = int(betrag)
        except ValueError:
            punkte = 0

        if punkte > 0:
            mitarbeiter_id = session.get("mitarbeiter_id")
        
            if not mitarbeiter_id:
                cur.close()
                conn.close()
                return redirect("/mitarbeiter-login")
        
            cur.execute("""
                INSERT INTO punkte_bewegungen
                    (kunde_id, typ, punkte, mitarbeiter_id)
                VALUES
                    (%s, %s, %s, %s)
            """, (
                kunde_db_id,
                "GUTSCHRIFT",
                punkte,
                mitarbeiter_id
            ))

            conn.commit()
            cur.close()
            conn.close()

            return auto_back_to_scanner_page(
                "Punkte gutgeschrieben",
                f"✅ {punkte} Punkte wurden erfolgreich gutgeschrieben."
            )

        meldung = "❌ Bitte gültigen Einkaufsbetrag eingeben."

    cur.close()
    conn.close()

    punktestand = get_punktestand(kunde_db_id)

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiterbereich</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="info-box">
                <div class="label">Kunden-ID</div>
                <div class="value">{kunde_daten[1]}</div>

                <div class="label">Name</div>
                <div class="value">{kunde_daten[2]} {kunde_daten[3]}</div>
            </div>

            <div class="points">
                <div class="number">{punktestand}</div>
                <div class="text">Punkte</div>
            </div>

            <form method="POST">
                <div class="section-title">Punkte gutschreiben</div>
                <label class="label">Einkaufsbetrag in Euro</label>
                <input type="number" step="0.01" name="betrag" placeholder="z.B. 12.50" required>
                <button class="btn-green" type="submit">Punkte gutschreiben</button>
            </form>

            <div class="divider"></div>

            <a class="btn btn-orange" href="/mitarbeiter/{kunde_daten[1]}/einloesen">Punkte einlösen</a>
            <a class="btn btn-red" href="/scanner">Nächsten Kunden scannen</a>
            <a class="btn btn-dark" href="/mitarbeiter">Manuell suchen</a>
            <a class="small-link" href="/mitarbeiter-logout">Abmelden</a>
        </div>
    </div>
    """


@app.route("/mitarbeiter/<kunden_id>/einloesen", methods=["GET", "POST"])
def punkte_einloesen(kunden_id):
    kunden_id = kunden_id.strip().upper()

    if not ist_mitarbeiter():
        return redirect(f"/mitarbeiter-login?next=/mitarbeiter/{kunden_id}/einloesen")

    meldung = ""

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, kunden_id, vorname, nachname
        FROM kunden
        WHERE kunden_id = %s
    """, (kunden_id,))

    kunde_daten = cur.fetchone()

    if not kunde_daten:
        cur.close()
        conn.close()
        return f"""
        {app_style()}
        <div class="page">
            <div class="card">
                <div class="logo">KEBAB HÖHLE</div>
                <div class="subtitle">Kunde nicht gefunden</div>
                <div class="message">❌ Diese Kunden-ID wurde nicht gefunden.</div>
                <a class="btn btn-red" href="/mitarbeiter">Zurück</a>
                <a class="btn btn-dark" href="/scanner">QR-Code scannen</a>
            </div>
        </div>
        """

    kunde_db_id = kunde_daten[0]
    punktestand = get_punktestand(kunde_db_id)

    if request.method == "POST":
        punkte_text = request.form.get("punkte_einloesen", "0")
    
        try:
            punkte_einloesen = int(punkte_text)
        except ValueError:
            punkte_einloesen = 0
    
        aktueller_stand = get_punktestand(kunde_db_id)
    
        if punkte_einloesen <= 0:
            meldung = "❌ Bitte gültige Punkte eingeben."
    
        elif punkte_einloesen > aktueller_stand:
            meldung = "❌ Nicht genug Punkte vorhanden."
    
        else:
            mitarbeiter_id = session.get("mitarbeiter_id")
    
            if not mitarbeiter_id:
                cur.close()
                conn.close()
                return redirect("/mitarbeiter-login")
    
            cur.execute("""
                INSERT INTO punkte_bewegungen
                    (kunde_id, typ, punkte, mitarbeiter_id)
                VALUES
                    (%s, %s, %s, %s)
            """, (
                kunde_db_id,
                "EINLOESUNG",
                -punkte_einloesen,
                mitarbeiter_id
            ))
    
            conn.commit()
            cur.close()
            conn.close()
    
            return auto_back_to_scanner_page(
                "Punkte eingelöst",
                f"✅ {punkte_einloesen} Punkte wurden erfolgreich eingelöst."
            )

    cur.close()
    conn.close()

    punktestand = get_punktestand(kunde_db_id)

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Punkte einlösen</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="info-box">
                <div class="label">Kunden-ID</div>
                <div class="value">{kunde_daten[1]}</div>

                <div class="label">Name</div>
                <div class="value">{kunde_daten[2]} {kunde_daten[3]}</div>
            </div>

            <div class="points">
                <div class="number">{punktestand}</div>
                <div class="text">Punkte</div>
            </div>

            <div class="danger-note">
                Achtung: Punkte nur einlösen, wenn der Kunde wirklich damit bezahlt oder einen Bonus erhalten soll.
            </div>

            <form method="POST">
                <div class="section-title">Einlösung</div>
                <label class="label">Punkte einlösen</label>
                <input type="number" name="punkte_einloesen" placeholder="z.B. 250" required>
                <button class="btn-orange" type="submit">Punkte jetzt einlösen</button>
            </form>

            <a class="btn btn-dark" href="/mitarbeiter/{kunde_daten[1]}">Zurück zur Kundenseite</a>
            <a class="btn btn-red" href="/scanner">Nächsten Kunden scannen</a>
            <a class="small-link" href="/mitarbeiter-logout">Abmelden</a>
        </div>
    </div>
    """


@app.route("/chef-login", methods=["GET", "POST"])
def chef_login():
    meldung = ""
    
    if request.method == "POST":
        pin = request.form.get("pin", "").strip()
        
        chef_pin = get_einstellung("chef_pin", CHEF_PIN)

        if pin == chef_pin:
            session.permanent = True
            session["chef_angemeldet"] = True
            return redirect("/chef-dashboard")
        else:
            meldung = "❌ Falscher Chef-PIN."

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Chef Login</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <form method="POST">
                <div class="section-title">Chef-PIN eingeben</div>
                <label class="label">PIN</label>
                <input type="password" name="pin" placeholder="Chef-PIN eingeben" required>
                <button class="btn-red" type="submit">Einloggen</button>
            </form>

            <a class="small-link" href="/">Zur Registrierung</a>
        </div>
    </div>
    """


@app.route("/chef-logout")
def chef_logout():
    session.pop("chef_angemeldet", None)
    return redirect("/chef-login")


@app.route("/chef-dashboard")
def chef_dashboard():
    if not ist_chef():
        return redirect("/chef-login")
    von = request.args.get("von", "").strip()
    bis = request.args.get("bis", "").strip()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM kunden")
    kunden_gesamt = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(punkte), 0)
        FROM punkte_bewegungen
        WHERE typ = 'GUTSCHRIFT'
          AND erstellt_am::date = CURRENT_DATE
    """)
    punkte_heute = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(ABS(punkte)), 0)
        FROM punkte_bewegungen
        WHERE typ = 'EINLOESUNG'
          AND erstellt_am::date = CURRENT_DATE
    """)
    einloesungen_heute = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM punkte_bewegungen
        WHERE erstellt_am::date = CURRENT_DATE
    """)
    buchungen_heute = cur.fetchone()[0]

    cur.execute("""
        SELECT
            p.id,
            k.kunden_id,
            k.vorname,
            k.nachname,
            p.typ,
            p.punkte,
            p.erstellt_am
        FROM punkte_bewegungen p
        JOIN kunden k ON p.kunde_id = k.id
        ORDER BY p.id DESC
        LIMIT 100
    """)
    bewegungen = cur.fetchall()

    cur.close()
    conn.close()

    rows = ""
    for b in bewegungen:
        typ_text = "Einlösung" if b[4] == "EINLOESUNG" else "Gutschrift"
        rows += f"""
            <tr>
                <td>{b[0]}</td>
                <td>{b[1]}</td>
                <td>{b[2]} {b[3]}</td>
                <td>{typ_text}</td>
                <td>{b[5]}</td>
                <td>{b[6]}</td>
            </tr>
        """

    if not rows:
        rows = "<tr><td colspan='6'>Noch keine Bewegungen vorhanden.</td></tr>"

    return f"""
    {app_style()}
    <div class="page">
        <div class="card wide-card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Chef Dashboard</div>

            <div class="stat-grid">
                <div class="stat-box">
                    <div class="stat-label">Kunden gesamt</div>
                    <div class="stat-value">{kunden_gesamt}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Buchungen heute</div>
                    <div class="stat-value">{buchungen_heute}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Gutgeschriebene Punkte heute</div>
                    <div class="stat-value">{punkte_heute}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Eingelöste Punkte heute</div>
                    <div class="stat-value">{einloesungen_heute}</div>
                </div>
            </div>
            <div class="menu-grid">

            <a class="menu-box menu-blue" href="/chef-kunden">👥 Kunden</a>
            <a class="menu-box menu-green" href="/chef-gutschriften">💰 Gutschriften</a>
            <a class="menu-box menu-orange" href="/chef-einloesungen">🎁 Einlösungen</a>
            <a class="menu-box menu-purple" href="/chef-statistiken">📊 Statistiken</a>
            <a class="menu-box menu-red" href="/chef-nachrichten">📢 Nachrichten</a>
            <a class="menu-box menu-gray" href="/chef-einstellungen">⚙️ Einstellungen</a>

            </div>
            
            <div style="
                margin-top:35px;
                padding:24px;
                border:2px solid #ff2b2b;
                border-radius:20px;
            ">
                <div style="
                    font-size:28px;
                    font-weight:900;
                    color:#ff6b6b;
                    margin-bottom:15px;
                ">
                    ⚠️ Nur für den Teststart
                </div>
            
                <div class="hint">
                    Dieser Vorgang löscht alle bisherigen Testkunden,
                    Punktebewegungen und Testnachrichten endgültig.
                </div>
            
                <form
                    method="POST"
                    action="/chef-testdaten-loeschen"
                    onsubmit="return confirm('ACHTUNG! Wirklich ALLE bisherigen Testdaten löschen? Dieser Vorgang kann nicht rückgängig gemacht werden.');"
                >
                    <button class="btn btn-red" type="submit">
                        🗑️ TESTDATEN ENDGÜLTIG LÖSCHEN
                    </button>
                </form>
            </div>

            <div class="section-title">Letzte Punktebewegungen</div>
            <div class="hint">
                Diese Historie ist nur für Chef/Admin gedacht.
                Mitarbeiter sehen weiterhin keine Gesamthistorie.
            </div>

            <div class="history-table-wrap">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Kunden-ID</th>
                            <th>Kunde</th>
                            <th>Aktion</th>
                            <th>Punkte</th>
                            <th>Zeitpunkt</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>

    
            <a class="btn btn-dark" href="/chef-logout">Chef abmelden</a>
            <a class="small-link" href="/">Zur Registrierung</a>
        </div>
    </div>
    """

@app.route("/chef-kunden")
def chef_kunden():
    if not ist_chef():
        return redirect("/chef-login")

    suche = request.args.get("suche", "").strip()

    conn = get_db_connection()
    cur = conn.cursor()

    if suche:
        cur.execute("""
            SELECT
                k.id,
                k.kunden_id,
                k.vorname,
                k.nachname,
                k.geburtsdatum,
                k.telefon,
                COALESCE(SUM(p.punkte), 0) AS punktestand
            FROM kunden k
            LEFT JOIN punkte_bewegungen p ON k.id = p.kunde_id
            WHERE
                k.kunden_id ILIKE %s
                OR k.vorname ILIKE %s
                OR k.nachname ILIKE %s
                OR k.telefon ILIKE %s
            GROUP BY k.id, k.kunden_id, k.vorname, k.nachname, k.geburtsdatum, k.telefon
            ORDER BY k.id DESC
        """, (f"%{suche}%", f"%{suche}%", f"%{suche}%", f"%{suche}%"))
    else:
        cur.execute("""
            SELECT
                k.id,
                k.kunden_id,
                k.vorname,
                k.nachname,
                k.geburtsdatum,
                k.telefon,
                COALESCE(SUM(p.punkte), 0) AS punktestand
            FROM kunden k
            LEFT JOIN punkte_bewegungen p ON k.id = p.kunde_id
            GROUP BY k.id, k.kunden_id, k.vorname, k.nachname, k.geburtsdatum, k.telefon
            ORDER BY k.id DESC
        """)

    kunden = cur.fetchall()
    cur.close()
    conn.close()

    rows = ""
    for k in kunden:
        rows += f"""
        <tr>
            <td>{k[1]}</td>
            <td>{k[2]} {k[3]}</td>
            <td>{k[4]}</td>
            <td>{k[5] or ""}</td>
            <td>{k[6]}</td>
        </tr>
        """

    if not rows:
        rows = "<tr><td colspan='5'>Keine Kunden gefunden.</td></tr>"

    return f"""
    {app_style()}
    <div class="page">
        <div class="card wide-card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Kundenverwaltung</div>

            <form method="GET">
                <label class="label">Kunde suchen</label>
                <input type="text" name="suche" value="{suche}" placeholder="Name, Kunden-ID oder Telefon">
                <button class="btn-red" type="submit">Suchen</button>
            </form>

            <div class="history-table-wrap">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>Kunden-ID</th>
                            <th>Name</th>
                            <th>Geburtsdatum</th>
                            <th>Telefon</th>
                            <th>Punkte</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>

            <a class="btn btn-dark" href="/chef-dashboard">Zurück zum Dashboard</a>
        </div>
    </div>
    """
@app.route("/chef-gutschriften")
def chef_gutschriften():
    if not ist_chef():
        return redirect("/chef-login")

    von = request.args.get("von", "").strip()
    bis = request.args.get("bis", "").strip()

    conn = get_db_connection()
    cur = conn.cursor()

    sql = """
        SELECT
            p.id,
            k.kunden_id,
            k.vorname || ' ' || k.nachname,
            p.punkte,
            p.erstellt_am,
            COALESCE(m.name, 'Nicht erfasst') AS mitarbeiter
        FROM punkte_bewegungen p
        JOIN kunden k ON k.id = p.kunde_id
        LEFT JOIN mitarbeiter m ON m.id = p.mitarbeiter_id
        WHERE p.typ = 'GUTSCHRIFT'
    """

    params = []

    if von:
        sql += " AND DATE(p.erstellt_am) >= %s"
        params.append(von)

    if bis:
        sql += " AND DATE(p.erstellt_am) <= %s"
        params.append(bis)

    sql += " ORDER BY p.erstellt_am DESC"

    cur.execute(sql, params)
    daten = cur.fetchall()

    cur.close()
    conn.close()

    rows = ""
    for d in daten:
        rows += f"""
        <tr>
            <td>{d[0]}</td>
            <td>{d[1]}</td>
            <td>{d[2]}</td>
            <td>{d[3]}</td>
            <td>{format_datetime(d[4])}</td>
            <td>{d[5]}</td>
        </tr>
        """

    if not rows:
        rows = "<tr><td colspan='6'>Keine Gutschriften gefunden.</td></tr>"

    return f"""
    {app_style()}
    <div class="page">
        <div class="card wide-card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Gutschriften</div>

            <form method="GET">
                <label class="label">Von</label>
                <input type="date" name="von" value="{von}">

                <label class="label">Bis</label>
                <input type="date" name="bis" value="{bis}">

                <button class="btn-red" type="submit">Filtern</button>
            </form>

            <div class="history-table-wrap">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Kunden-ID</th>
                            <th>Kunde</th>
                            <th>Punkte</th>
                            <th>Zeitpunkt</th>
                            <th>Mitarbeiter</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>

            <a class="btn btn-dark" href="/chef-dashboard">Zurück</a>

        </div>
    </div>
    """


@app.route("/chef-einloesungen")
def chef_einloesungen():
    if not ist_chef():
        return redirect("/chef-login")

    von = request.args.get("von", "").strip()
    bis = request.args.get("bis", "").strip()

    conn = get_db_connection()
    cur = conn.cursor()

    sql = """
        SELECT
            p.id,
            k.kunden_id,
            k.vorname || ' ' || k.nachname,
            ABS(p.punkte),
            p.erstellt_am,
            COALESCE(m.name, 'Nicht erfasst') AS mitarbeiter
        FROM punkte_bewegungen p
        JOIN kunden k ON k.id = p.kunde_id
        LEFT JOIN mitarbeiter m ON m.id = p.mitarbeiter_id
        WHERE p.typ = 'EINLOESUNG'
    """

    params = []

    if von:
        sql += " AND DATE(p.erstellt_am) >= %s"
        params.append(von)

    if bis:
        sql += " AND DATE(p.erstellt_am) <= %s"
        params.append(bis)

    sql += " ORDER BY p.erstellt_am DESC"

    cur.execute(sql, params)
    daten = cur.fetchall()

    cur.close()
    conn.close()

    rows = ""

    for d in daten:
        rows += f"""
        <tr>
            <td>{d[0]}</td>
            <td>{d[1]}</td>
            <td>{d[2]}</td>
            <td>{d[3]}</td>
            <td>{format_datetime(d[4])}</td>
            <td>{d[5]}</td>
        </tr>
        """

    if not rows:
        rows = "<tr><td colspan='6'>Keine Einlösungen gefunden.</td></tr>"

    return f"""
    {app_style()}
    <div class="page">
        <div class="card wide-card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Einlösungen</div>

            <form method="GET">
                <label class="label">Von</label>
                <input type="date" name="von" value="{von}">

                <label class="label">Bis</label>
                <input type="date" name="bis" value="{bis}">

                <button class="btn-red" type="submit">Filtern</button>
            </form>

            <div class="history-table-wrap">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Kunden-ID</th>
                            <th>Kunde</th>
                            <th>Punkte</th>
                            <th>Zeitpunkt</th>
                            <th>Mitarbeiter</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>

            <a class="btn btn-dark" href="/chef-dashboard">
                Zurück
            </a>

        </div>
    </div>
    """

@app.route("/chef-statistiken")
def chef_statistiken():
    if not ist_chef():
        return redirect("/chef-login")

    von = request.args.get("von", "").strip()
    bis = request.args.get("bis", "").strip()

    conn = get_db_connection()
    cur = conn.cursor()

    bedingungen = []
    params = []

    if von:
        bedingungen.append("erstellt_am::date >= %s")
        params.append(von)

    if bis:
        bedingungen.append("erstellt_am::date <= %s")
        params.append(bis)

    datum_filter = ""
    if bedingungen:
        datum_filter = " WHERE " + " AND ".join(bedingungen)

    cur.execute(
        "SELECT COUNT(*) FROM punkte_bewegungen" + datum_filter,
        params
    )
    buchungen_gesamt = cur.fetchone()[0]

    gutschrift_bedingungen = ["typ = 'GUTSCHRIFT'"]
    gutschrift_params = []

    if von:
        gutschrift_bedingungen.append("erstellt_am::date >= %s")
        gutschrift_params.append(von)

    if bis:
        gutschrift_bedingungen.append("erstellt_am::date <= %s")
        gutschrift_params.append(bis)

    cur.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(punkte), 0)
        FROM punkte_bewegungen
        WHERE
        """ + " AND ".join(gutschrift_bedingungen),
        gutschrift_params
    )

    gutschriften_anzahl, gutschriften_punkte = cur.fetchone()

    einloesung_bedingungen = ["typ = 'EINLOESUNG'"]
    einloesung_params = []

    if von:
        einloesung_bedingungen.append("erstellt_am::date >= %s")
        einloesung_params.append(von)

    if bis:
        einloesung_bedingungen.append("erstellt_am::date <= %s")
        einloesung_params.append(bis)

    cur.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(ABS(punkte)), 0)
        FROM punkte_bewegungen
        WHERE
        """ + " AND ".join(einloesung_bedingungen),
        einloesung_params
    )

    einloesungen_anzahl, einloesungen_punkte = cur.fetchone()

    kunden_bedingungen = []
    kunden_params = []

    if von:
        kunden_bedingungen.append("erstellt_am::date >= %s")
        kunden_params.append(von)

    if bis:
        kunden_bedingungen.append("erstellt_am::date <= %s")
        kunden_params.append(bis)

    kunden_filter = ""
    if kunden_bedingungen:
        kunden_filter = " WHERE " + " AND ".join(kunden_bedingungen)

    cur.execute(
        "SELECT COUNT(*) FROM kunden" + kunden_filter,
        kunden_params
    )
    neue_kunden = cur.fetchone()[0]

    aktiv_bedingungen = []
    aktiv_params = []

    if von:
        aktiv_bedingungen.append("p.erstellt_am::date >= %s")
        aktiv_params.append(von)

    if bis:
        aktiv_bedingungen.append("p.erstellt_am::date <= %s")
        aktiv_params.append(bis)

    aktiv_filter = ""
    if aktiv_bedingungen:
        aktiv_filter = " WHERE " + " AND ".join(aktiv_bedingungen)

    cur.execute(
        """
        SELECT
            k.kunden_id,
            k.vorname,
            k.nachname,
            COUNT(p.id) AS buchungen
        FROM punkte_bewegungen p
        JOIN kunden k ON k.id = p.kunde_id
        """ + aktiv_filter + """
        GROUP BY k.id, k.kunden_id, k.vorname, k.nachname
        ORDER BY buchungen DESC
        LIMIT 10
        """,
        aktiv_params
    )

    aktive_kunden = cur.fetchall()

    cur.close()
    conn.close()

    kunden_rows = ""

    for kunde in aktive_kunden:
        kunden_rows += f"""
        <tr>
            <td>{kunde[0]}</td>
            <td>{kunde[1]} {kunde[2]}</td>
            <td>{kunde[3]}</td>
        </tr>
        """

    if not kunden_rows:
        kunden_rows = """
        <tr>
            <td colspan="3">Keine Buchungen im ausgewählten Zeitraum.</td>
        </tr>
        """

    zeitraum_text = "Gesamter Zeitraum"

    if von and bis:
        zeitraum_text = f"{von} bis {bis}"
    elif von:
        zeitraum_text = f"Ab {von}"
    elif bis:
        zeitraum_text = f"Bis {bis}"

    return f"""
    {app_style()}
    <div class="page">
        <div class="card wide-card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Statistiken</div>

            <form method="GET">
                <label class="label">Von</label>
                <input type="date" name="von" value="{von}">

                <label class="label">Bis</label>
                <input type="date" name="bis" value="{bis}">

                <button class="btn-red" type="submit">Zeitraum anzeigen</button>
            </form>

            <div class="hint">
                Ausgewählter Zeitraum: {zeitraum_text}
            </div>

            <div class="stat-grid">
                <div class="stat-box">
                    <div class="stat-label">Neue Kunden</div>
                    <div class="stat-value">{neue_kunden}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Buchungen</div>
                    <div class="stat-value">{buchungen_gesamt}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Gutschriften</div>
                    <div class="stat-value">{gutschriften_anzahl}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Vergebene Punkte</div>
                    <div class="stat-value">{gutschriften_punkte}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Einlösungen</div>
                    <div class="stat-value">{einloesungen_anzahl}</div>
                </div>

                <div class="stat-box">
                    <div class="stat-label">Eingelöste Punkte</div>
                    <div class="stat-value">{einloesungen_punkte}</div>
                </div>
            </div>

            <div class="section-title">Aktivste Kunden</div>

            <div class="history-table-wrap">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>Kunden-ID</th>
                            <th>Kunde</th>
                            <th>Buchungen</th>
                        </tr>
                    </thead>
                    <tbody>
                        {kunden_rows}
                    </tbody>
                </table>
            </div>

            <a class="btn btn-dark" href="/chef-dashboard">
                Zurück zum Dashboard
            </a>

        </div>
    </div>
    """

@app.route("/chef-praemien")
def chef_praemien():
    if not ist_chef():
        return redirect("/chef-login")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            name,
            punkte,
            bild,
            farbe,
            aktiv,
            reihenfolge
        FROM praemien
        ORDER BY reihenfolge, id
    """)

    praemien = cur.fetchall()

    cur.close()
    conn.close()

    rows = ""

    for p in praemien:
        status = "✅ Aktiv" if p[5] else "⛔ Inaktiv"

        rows += f"""
        <tr>
            <td>{p[0]}</td>

            <td>
                <img
                    src="/static/images/{p[3]}"
                    alt="{p[1]}"
                    style="
                        width:70px;
                        height:70px;
                        object-fit:cover;
                        border-radius:12px;
                    "
                >
            </td>

            <td>{p[1]}</td>
            <td>{p[2]}</td>

            <td>
                <span style="
                    display:inline-block;
                    width:28px;
                    height:28px;
                    border-radius:8px;
                    background:{p[4]};
                    border:1px solid #777;
                "></span>
            </td>

            <td>{status}</td>
            <td>{p[6]}</td>

            <td>
                <a
                    class="btn btn-dark"
                    href="/chef-praemien/{p[0]}/bearbeiten"
                    style="
                        padding:12px 16px;
                        font-size:18px;
                        margin:0;
                    "
                >
                    ✏️ Bearbeiten
                </a>
            </td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="8">Keine Prämien vorhanden.</td>
        </tr>
        """

    return f"""
    {app_style()}

    <div class="page">
        <div class="card wide-card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Prämien verwalten</div>

            <div class="history-table-wrap">
                <table class="history-table">

                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Bild</th>
                            <th>Prämie</th>
                            <th>Punkte</th>
                            <th>Farbe</th>
                            <th>Status</th>
                            <th>Reihenfolge</th>
                            <th>Aktion</th>
                        </tr>
                    </thead>

                    <tbody>
                        {rows}
                    </tbody>

                </table>
            </div>

            <a class="btn btn-red" href="/chef-praemien/neu">
                ➕ Neue Prämie
            </a>

            <a class="btn btn-dark" href="/chef-einstellungen">
                Zurück zu den Einstellungen
            </a>

        </div>
    </div>
    """

@app.route("/chef-praemien/neu", methods=["GET", "POST"])
def chef_praemie_neu():
    if not ist_chef():
        return redirect("/chef-login")

    meldung = ""

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(MAX(reihenfolge), 0)
        FROM praemien
    """)
    letzte_position = cur.fetchone()[0]
    vorgeschlagene_position = letzte_position + 1

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        punkte_text = request.form.get("punkte", "").strip()
        position_text = request.form.get("position", "").strip()
        aktiv = request.form.get("aktiv") == "on"

        try:
            punkte = int(punkte_text)
        except ValueError:
            punkte = 0

        try:
            position = int(position_text)
        except ValueError:
            position = vorgeschlagene_position

        if not name:
            meldung = "❌ Bitte einen Namen eingeben."

        elif punkte <= 0:
            meldung = "❌ Punkte müssen größer als 0 sein."

        elif position <= 0:
            meldung = "❌ Die Position muss mindestens 1 sein."

        else:
            cur.execute("""
                UPDATE praemien
                SET reihenfolge = reihenfolge + 1
                WHERE reihenfolge >= %s
            """, (position,))

            cur.execute("""
                INSERT INTO praemien
                    (
                        name,
                        punkte,
                        bild,
                        farbe,
                        aktiv,
                        reihenfolge
                    )
                VALUES
                    (%s, %s, %s, %s, %s, %s)
            """, (
                name,
                punkte,
                "standard.png",
                "#ff2b2b",
                aktiv,
                position
            ))

            conn.commit()
            cur.close()
            conn.close()

            return redirect("/chef-praemien")

    cur.close()
    conn.close()

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Neue Prämie</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="hint">
                Bild und Farbe werden später vom Systembetreiber angepasst.
            </div>

            <form method="POST">

                <label class="label">Name der Prämie</label>
                <input
                    type="text"
                    name="name"
                    placeholder="z. B. Baklava"
                    required
                >

                <label class="label">Benötigte Punkte</label>
                <input
                    type="number"
                    name="punkte"
                    placeholder="z. B. 700"
                    min="1"
                    required
                >

                <label class="label">Position</label>
                <input
                    type="number"
                    name="position"
                    value="{vorgeschlagene_position}"
                    min="1"
                    required
                >

                <label
                    style="
                        display:flex;
                        align-items:center;
                        gap:14px;
                        margin:24px 0;
                        font-size:24px;
                        font-weight:800;
                    "
                >
                    <input
                        type="checkbox"
                        name="aktiv"
                        checked
                        style="
                            width:26px;
                            height:26px;
                            margin:0;
                        "
                    >
                    Prämie aktiv
                </label>

                <button class="btn-red" type="submit">
                    ➕ Prämie speichern
                </button>

            </form>

            <a class="btn btn-dark" href="/chef-praemien">
                Abbrechen
            </a>

        </div>
    </div>
    """

@app.route("/chef-praemien/<int:praemie_id>/bearbeiten", methods=["GET", "POST"])
def chef_praemie_bearbeiten(praemie_id):
    if not ist_chef():
        return redirect("/chef-login")

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        punkte_text = request.form.get("punkte", "").strip()
        aktiv = request.form.get("aktiv") == "on"

        if not name:
            cur.close()
            conn.close()
            return "Name darf nicht leer sein.", 400

        try:
            punkte = int(punkte_text)
        except ValueError:
            cur.close()
            conn.close()
            return "Punkte müssen eine ganze Zahl sein.", 400

        if punkte <= 0:
            cur.close()
            conn.close()
            return "Punkte müssen größer als 0 sein.", 400

        cur.execute("""
            UPDATE praemien
            SET
                name = %s,
                punkte = %s,
                aktiv = %s
            WHERE id = %s
        """, (name, punkte, aktiv, praemie_id))

        conn.commit()
        cur.close()
        conn.close()

        return redirect("/chef-praemien")

    cur.execute("""
        SELECT
            id,
            name,
            punkte,
            bild,
            farbe,
            aktiv,
            reihenfolge
        FROM praemien
        WHERE id = %s
    """, (praemie_id,))

    praemie = cur.fetchone()

    cur.close()
    conn.close()

    if not praemie:
        return "Prämie wurde nicht gefunden.", 404

    aktiv_checked = "checked" if praemie[5] else ""

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Prämie bearbeiten</div>

            <div class="info-box">
                <img
                    src="/static/images/{praemie[3]}"
                    alt="{praemie[1]}"
                    style="
                        width:140px;
                        height:140px;
                        object-fit:cover;
                        border-radius:20px;
                        display:block;
                        margin:0 auto 20px auto;
                        border:4px solid {praemie[4]};
                    "
                >

                <div class="hint">
                    Bild, Farbe und Reihenfolge werden vom Systembetreiber verwaltet.
                </div>
            </div>

            <form method="POST">

                <label class="label">Name der Prämie</label>
                <input
                    type="text"
                    name="name"
                    value="{praemie[1]}"
                    required
                >

                <label class="label">Benötigte Punkte</label>
                <input
                    type="number"
                    name="punkte"
                    value="{praemie[2]}"
                    min="1"
                    required
                >

                <label
                    style="
                        display:flex;
                        align-items:center;
                        gap:14px;
                        margin:24px 0;
                        font-size:24px;
                        font-weight:800;
                    "
                >
                    <input
                        type="checkbox"
                        name="aktiv"
                        {aktiv_checked}
                        style="
                            width:26px;
                            height:26px;
                            margin:0;
                        "
                    >
                    Prämie aktiv
                </label>

                <button class="btn-red" type="submit">
                    💾 Änderungen speichern
                </button>
                
                </form>
                
                <form
                    method="POST"
                    action="/chef-praemien/{praemie[0]}/loeschen"
                    onsubmit="return confirm('Soll diese Prämie wirklich endgültig gelöscht werden?');"
                >
                    <button
                        class="btn btn-dark"
                        type="submit"
                        style="
                            background:#8b0000;
                            border:1px solid #b91c1c;
                            margin-top:18px;
                        "
                    >
                        🗑️ Prämie löschen
                    </button>
                </form>
                
                <a class="btn btn-dark" href="/chef-praemien">
                    Abbrechen
                </a>

        </div>
    </div>
    """
@app.route("/chef-praemien/<int:praemie_id>/loeschen", methods=["POST"])
def chef_praemie_loeschen(praemie_id):
    if not ist_chef():
        return redirect("/chef-login")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM praemien
        WHERE id = %s
    """, (praemie_id,))

    conn.commit()
    cur.close()
    conn.close()

    return redirect("/chef-praemien")

@app.route("/chef-punkte-regel", methods=["GET", "POST"])
def chef_punkte_regel():
    if not ist_chef():
        return redirect("/chef-login")

    meldung = ""

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        wert_text = request.form.get("punkte_pro_euro", "").strip()

        try:
            punkte_pro_euro = int(wert_text)
        except ValueError:
            punkte_pro_euro = 0

        if punkte_pro_euro < 1:
            meldung = "❌ Der Wert muss mindestens 1 sein."
        else:
            cur.execute("""
                INSERT INTO einstellungen (schluessel, wert)
                VALUES ('punkte_pro_euro', %s)
                ON CONFLICT (schluessel)
                DO UPDATE SET wert = EXCLUDED.wert
            """, (str(punkte_pro_euro),))

            conn.commit()
            meldung = "✅ Punkteregel wurde gespeichert."

    cur.execute("""
        SELECT wert
        FROM einstellungen
        WHERE schluessel = 'punkte_pro_euro'
    """)

    ergebnis = cur.fetchone()
    punkte_pro_euro = ergebnis[0] if ergebnis else "1"

    cur.close()
    conn.close()

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Punkte-Regel</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="info-box">
                <div class="section-title">Aktuelle Regel</div>

                <div class="value">
                    1 € = {punkte_pro_euro} Punkt
                </div>

                <div class="hint">
                    Nachkommastellen werden nicht berücksichtigt.
                    Beispiel: 12,50 € ergeben bei der Einstellung 1 genau 12 Punkte.
                </div>
            </div>

            <form method="POST">
                <label class="label">Punkte pro vollem Euro</label>

                <input
                    type="number"
                    name="punkte_pro_euro"
                    value="{punkte_pro_euro}"
                    min="1"
                    required
                >

                <button class="btn-red" type="submit">
                    💾 Punkteregel speichern
                </button>
            </form>

            <a class="btn btn-dark" href="/chef-einstellungen">
                Zurück zu den Einstellungen
            </a>

        </div>
    </div>
    """

@app.route("/chef-pin-verwaltung", methods=["GET", "POST"])
def chef_pin_verwaltung():
    if not ist_chef():
        return redirect("/chef-login")

    meldung = ""

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        neue_chef_pin = request.form.get("chef_pin", "").strip()

        if not neue_chef_pin.isdigit() or len(neue_chef_pin) < 4:
            meldung = "❌ Die Chef-PIN muss mindestens 4 Ziffern haben."

        else:
            cur.execute("""
                INSERT INTO einstellungen (schluessel, wert)
                VALUES ('chef_pin', %s)
                ON CONFLICT (schluessel)
                DO UPDATE SET wert = EXCLUDED.wert
            """, (neue_chef_pin,))

            conn.commit()
            meldung = "✅ Chef-PIN wurde gespeichert."

    cur.execute("""
        SELECT wert
        FROM einstellungen
        WHERE schluessel = 'chef_pin'
        LIMIT 1
    """)

    eintrag = cur.fetchone()
    chef_pin = eintrag[0] if eintrag else ""

    cur.close()
    conn.close()

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Chef-PIN verwalten</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <form method="POST">

                <label class="label">Chef-PIN</label>

                <div style="display:flex; gap:12px; align-items:center;">
                    <input
                        id="chefPin"
                        type="password"
                        name="chef_pin"
                        value="{chef_pin}"
                        inputmode="numeric"
                        minlength="4"
                        required
                        style="flex:1;"
                    >

                    <button
                        type="button"
                        onclick="togglePin('chefPin', this)"
                        style="
                            width:auto;
                            padding:18px 22px;
                            font-size:22px;
                            border-radius:16px;
                        "
                    >
                        👁
                    </button>
                </div>

                <button class="btn-red" type="submit">
                    💾 Chef-PIN speichern
                </button>

            </form>

            <div class="hint">
                Hier wird nur die Chef-PIN geändert.
                Persönliche Mitarbeiter-PINs werden unter
                „Mitarbeiter verwalten“ bearbeitet.
            </div>

            <a class="btn btn-dark" href="/chef-einstellungen">
                Zurück zu den Einstellungen
            </a>

            <script>
            function togglePin(inputId, button){{
                const input = document.getElementById(inputId);

                if(input.type === "password"){{
                    input.type = "text";
                    button.textContent = "🙈";
                }}else{{
                    input.type = "password";
                    button.textContent = "👁";
                }}
            }}
            </script>

        </div>
    </div>
    """

@app.route("/chef-mitarbeiter/neu", methods=["GET", "POST"])
def chef_mitarbeiter_neu():
    if not ist_chef():
        return redirect("/chef-login")

    meldung = ""

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pin = request.form.get("pin", "").strip()
        aktiv = request.form.get("aktiv") == "on"

        if not name:
            meldung = "❌ Bitte einen Namen eingeben."

        elif not pin.isdigit() or len(pin) < 4:
            meldung = "❌ Die PIN muss aus mindestens 4 Ziffern bestehen."

        else:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("""
                SELECT id
                FROM mitarbeiter
                WHERE pin = %s
                LIMIT 1
            """, (pin,))

            vorhandener_mitarbeiter = cur.fetchone()

            if vorhandener_mitarbeiter:
                meldung = "❌ Diese PIN wird bereits von einem Mitarbeiter verwendet."
            else:
                cur.execute("""
                    INSERT INTO mitarbeiter
                        (name, pin, aktiv)
                    VALUES
                        (%s, %s, %s)
                """, (name, pin, aktiv))

                conn.commit()
                cur.close()
                conn.close()

                return redirect("/chef-mitarbeiter")

            cur.close()
            conn.close()

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Neuer Mitarbeiter</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <form method="POST">

                <label class="label">Name</label>
                <input
                    type="text"
                    name="name"
                    placeholder="Name des Mitarbeiters"
                    required
                >

                <label class="label">Persönliche PIN</label>
                <input
                    type="password"
                    name="pin"
                    placeholder="Mindestens 4 Ziffern"
                    inputmode="numeric"
                    minlength="4"
                    required
                >

                <label
                    style="
                        display:flex;
                        align-items:center;
                        gap:14px;
                        margin:24px 0;
                        font-size:24px;
                        font-weight:800;
                    "
                >
                    <input
                        type="checkbox"
                        name="aktiv"
                        checked
                        style="
                            width:26px;
                            height:26px;
                            margin:0;
                        "
                    >
                    Mitarbeiter aktiv
                </label>

                <button class="btn-red" type="submit">
                    ➕ Mitarbeiter speichern
                </button>

            </form>

            <a class="btn btn-dark" href="/chef-mitarbeiter">
                Abbrechen
            </a>

        </div>
    </div>
    """

@app.route("/chef-mitarbeiter")
def chef_mitarbeiter():
    if not ist_chef():
        return redirect("/chef-login")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            name,
            pin,
            aktiv,
            erstellt_am
        FROM mitarbeiter
        ORDER BY id
    """)

    mitarbeiter_liste = cur.fetchall()

    cur.close()
    conn.close()

    rows = ""

    for m in mitarbeiter_liste:
        status = "✅ Aktiv" if m[3] else "⛔ Inaktiv"

        rows += f"""
        <tr>
            <td>{m[0]}</td>
            <td>{m[1]}</td>
            <td>••••</td>
            <td>{status}</td>
            <td>{format_datetime(m[4])}</td>
            <td>
                <a
                    class="btn btn-dark"
                    href="/chef-mitarbeiter/{m[0]}/bearbeiten"
                    style="
                        padding:12px 16px;
                        font-size:18px;
                        margin:0;
                    "
                >
                    ✏️ Bearbeiten
                </a>
            </td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="6">Noch keine Mitarbeiter vorhanden.</td>
        </tr>
        """

    return f"""
    {app_style()}

    <div class="page">
        <div class="card wide-card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiter verwalten</div>

            <div class="history-table-wrap">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Name</th>
                            <th>PIN</th>
                            <th>Status</th>
                            <th>Erstellt am</th>
                            <th>Aktion</th>
                        </tr>
                    </thead>

                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>

            <a class="btn btn-red" href="/chef-mitarbeiter/neu">
                ➕ Neuer Mitarbeiter
            </a>

            <a class="btn btn-dark" href="/chef-einstellungen">
                Zurück zu den Einstellungen
            </a>

        </div>
    </div>
    """

@app.route("/chef-mitarbeiter/<int:mitarbeiter_id>/bearbeiten", methods=["GET", "POST"])
def chef_mitarbeiter_bearbeiten(mitarbeiter_id):
    if not ist_chef():
        return redirect("/chef-login")

    meldung = ""

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pin = request.form.get("pin", "").strip()
        aktiv = request.form.get("aktiv") == "on"

        if not name:
            meldung = "❌ Bitte einen Namen eingeben."

        elif not pin.isdigit() or len(pin) < 4:
            meldung = "❌ Die PIN muss aus mindestens 4 Ziffern bestehen."

        else:
            cur.execute("""
                SELECT id
                FROM mitarbeiter
                WHERE pin = %s
                  AND id <> %s
                LIMIT 1
            """, (pin, mitarbeiter_id))

            andere_person = cur.fetchone()

            if andere_person:
                meldung = "❌ Diese PIN wird bereits von einem anderen Mitarbeiter verwendet."
            else:
                cur.execute("""
                    UPDATE mitarbeiter
                    SET
                        name = %s,
                        pin = %s,
                        aktiv = %s
                    WHERE id = %s
                """, (name, pin, aktiv, mitarbeiter_id))

                conn.commit()
                cur.close()
                conn.close()

                return redirect("/chef-mitarbeiter")

    cur.execute("""
        SELECT
            id,
            name,
            pin,
            aktiv,
            erstellt_am,
            letzter_login
        FROM mitarbeiter
        WHERE id = %s
    """, (mitarbeiter_id,))

    mitarbeiter = cur.fetchone()

    cur.close()
    conn.close()

    if not mitarbeiter:
        return "Mitarbeiter wurde nicht gefunden.", 404

    aktiv_checked = "checked" if mitarbeiter[3] else ""

    erstellt_am = (
        format_datetime(mitarbeiter[4])
        if mitarbeiter[4]
        else "Nicht bekannt"
    )
    

    letzter_login = (
        format_datetime(mitarbeiter[5])
        if mitarbeiter[5]
        else "Noch nie angemeldet"
    )

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiter bearbeiten</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="info-box">
                <div class="label">Erstellt am</div>
                <div class="value">{erstellt_am}</div>

                <div class="label">Letzter Login</div>
                <div class="value">{letzter_login}</div>
            </div>

            <form method="POST">

                <label class="label">Name</label>
                <input
                    type="text"
                    name="name"
                    value="{mitarbeiter[1]}"
                    required
                >

                <label class="label">Persönliche PIN</label>

                <div style="display:flex; gap:12px; align-items:center;">
                    <input
                        id="mitarbeiterPin"
                        type="password"
                        name="pin"
                        value="{mitarbeiter[2]}"
                        inputmode="numeric"
                        minlength="4"
                        required
                        style="flex:1;"
                    >

                    <button
                        type="button"
                        onclick="togglePin('mitarbeiterPin', this)"
                        style="
                            width:auto;
                            padding:18px 22px;
                            font-size:22px;
                            border-radius:16px;
                        "
                    >
                        👁
                    </button>
                </div>

                <label
                    style="
                        display:flex;
                        align-items:center;
                        gap:14px;
                        margin:24px 0;
                        font-size:24px;
                        font-weight:800;
                    "
                >
                    <input
                        type="checkbox"
                        name="aktiv"
                        {aktiv_checked}
                        style="
                            width:26px;
                            height:26px;
                            margin:0;
                        "
                    >
                    Mitarbeiter aktiv
                </label>

                <button class="btn-red" type="submit">
                    💾 Änderungen speichern
                </button>
                
                </form>
                
                <a class="btn btn-dark" href="/chef-mitarbeiter">
                    Abbrechen
                </a>

            <script>
            function togglePin(inputId, button){{
                const input = document.getElementById(inputId);

                if(input.type === "password"){{
                    input.type = "text";
                    button.textContent = "🙈";
                }}else{{
                    input.type = "password";
                    button.textContent = "👁";
                }}
            }}
            </script>

        </div>
    </div>
    """

@app.route("/chef-einstellungen")
def chef_einstellungen():
    if not ist_chef():
        return redirect("/chef-login")

    return f"""
    {app_style()}
    <div class="page">
        <div class="card wide-card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Einstellungen</div>
            <button
                type="button"
                class="btn btn-dark"
                onclick="return farbmodusWechseln(event);"
                style="
                    max-width:320px;
                    margin:0 auto 24px auto;
                    padding:14px 20px;
                    font-size:22px;
                "
            >
                🌙 / ☀️ Darstellung wechseln
            </button>
            
            
            <div class="menu-grid">

                <a class="menu-box menu-orange" href="/chef-praemien">
                    🎁 Prämien verwalten
                </a>

                <a class="menu-box menu-green" href="/chef-punkte-regel">
                    🔢 Punkte-Regel
                </a>
               

                <a class="menu-box menu-purple" href="/chef-pin-verwaltung">
                    🔐 PIN-Verwaltung
                </a>
                

                <a class="menu-box menu-blue" href="#">
                    🔔 Push-Nachrichten
                </a>

                <a class="menu-box menu-red" href="/chef-mitarbeiter">
                    👨‍💼 Mitarbeiter verwalten
                </a>
                

                <a class="menu-box menu-gray" href="/chef-logout">
                    🚪 Chef abmelden
                </a>

            </div>

            <a class="btn btn-dark" href="/chef-dashboard">
                Zurück zum Dashboard
            </a>

        </div>
    </div>
    """

@app.route("/chef-nachrichten", methods=["GET", "POST"])
def chef_nachrichten():
    if not ist_chef():
        return redirect("/chef-login")

    meldung = ""
    alte_nachricht = ""

    if request.method == "POST":
        nachricht = request.form.get("nachricht", "").strip()
        alte_nachricht = nachricht

        if not nachricht:
            meldung = "❌ Bitte eine Nachricht eingeben."

        elif len(nachricht) > 500:
            meldung = "❌ Die Nachricht ist zu lang. Maximal 500 Zeichen."

        elif not VAPID_PRIVATE_KEY or not VAPID_SUBJECT:
            meldung = "❌ Die Push-Schlüssel sind auf Render nicht vollständig eingerichtet."

        else:
            conn = get_db_connection()
            cur = conn.cursor()
            
            cur.execute("""
                INSERT INTO push_nachrichten (nachricht)
                VALUES (%s)
                RETURNING id
            """, (nachricht,))
            
            nachricht_id = cur.fetchone()[0]
            
            conn.commit()
            
            cur.close()
            conn.close()
            
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("""
                SELECT
                    ps.id,
                    ps.endpoint,
                    ps.p256dh,
                    ps.auth,
                    k.kunden_id
                FROM push_subscriptions ps
                JOIN kunden k
                    ON k.id = ps.kunde_id
                WHERE k.werbeeinwilligung = TRUE
                ORDER BY ps.id
            """)

            abonnements = cur.fetchall()

            if not abonnements:
                meldung = (
                    "❌ Es gibt noch keine Kunden mit aktivierten "
                    "Benachrichtigungen und Werbeeinwilligung."
                )

                cur.close()
                conn.close()

            else:
                erfolgreich = 0
                fehlgeschlagen = 0
                abgelaufene_ids = []

                

                # Der private PEM-Schlüssel aus Render wird als temporäre
                # Schlüsseldatei für pywebpush bereitgestellt.
                private_key_path = "/tmp/vapid_private.pem"

                private_key_text = (
                    VAPID_PRIVATE_KEY
                    .replace("\\n", "\n")
                    .strip()
                )

                with open(
                    private_key_path,
                    "w",
                    encoding="utf-8"
                ) as private_key_datei:
                    private_key_datei.write(private_key_text + "\n")

                for abonnement in abonnements:
                    subscription_id = abonnement[0]
                    kunden_id = abonnement[4]
                
                    payload = json.dumps({
                        "title": "KEBAB HÖHLE",
                        "body": nachricht,
                        "url": f"/kunde/{kunden_id}?angebot={nachricht_id}"
                    })
                
                    subscription_info = {
                        "endpoint": abonnement[1],
                        "keys": {
                            "p256dh": abonnement[2],
                            "auth": abonnement[3]
                        }
                    }

                    try:
                        webpush(
                            subscription_info=subscription_info,
                            data=payload,
                            vapid_private_key=private_key_path,
                            vapid_claims={
                                "sub": VAPID_SUBJECT
                            },
                            ttl=86400
                        )

                        erfolgreich += 1

                    except WebPushException as fehler:
                        status_code = None

                        if fehler.response is not None:
                            status_code = fehler.response.status_code

                        print(
                            "Push-Versandfehler:",
                            subscription_id,
                            status_code,
                            repr(fehler)
                        )

                        # 404 oder 410 bedeutet meistens:
                        # Das Abonnement existiert nicht mehr.
                        if status_code in (404, 410):
                            abgelaufene_ids.append(subscription_id)
                        else:
                            fehlgeschlagen += 1

                    except Exception as fehler:
                        fehlgeschlagen += 1

                        print(
                            "Allgemeiner Push-Fehler:",
                            subscription_id,
                            repr(fehler)
                        )

                if abgelaufene_ids:
                    cur.execute("""
                        DELETE FROM push_subscriptions
                        WHERE id = ANY(%s)
                    """, (abgelaufene_ids,))

                conn.commit()
                cur.close()
                conn.close()

                meldung = (
                    f"✅ Push-Versand abgeschlossen: "
                    f"{erfolgreich} erfolgreich"
                )

                if fehlgeschlagen:
                    meldung += f", {fehlgeschlagen} fehlgeschlagen"

                if abgelaufene_ids:
                    meldung += (
                        f", {len(abgelaufene_ids)} "
                        f"abgelaufene Anmeldung entfernt"
                    )

                meldung += "."

                if erfolgreich:
                    alte_nachricht = ""

    return f"""
    {app_style()}

    <div class="page">
        <div class="card">

            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Push-Nachrichten</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="hint">
                Hier kann der Chef eine Angebotsnachricht an Kunden senden,
                die Werbung und Handy-Benachrichtigungen erlaubt haben.
            </div>

            <form method="POST">

                <div class="section-title">
                    Angebotsnachricht
                </div>

                <label class="label">
                    Nachricht
                </label>

                <textarea
                    name="nachricht"
                    maxlength="500"
                    placeholder="z. B. Heute Pizza Mexico XL nur 10 € bei deiner Kebab Höhle."
                    required
                >{alte_nachricht}</textarea>

                <button class="btn-red" type="submit">
                    🔔 Push-Nachricht senden
                </button>

            </form>

            <a class="btn btn-dark" href="/chef-dashboard">
                Zum Chef Dashboard
            </a>

            <a class="btn btn-dark" href="/chef-logout">
                Chef abmelden
            </a>

        </div>
    </div>
    """


@app.route("/datenschutz")
def datenschutz():
    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE XXL</div>
            <div class="subtitle">Datenschutzerklärung</div>

            <div class="hint">
                Der Schutz Ihrer personenbezogenen Daten ist uns wichtig.
                Nachfolgend informieren wir Sie darüber, welche personenbezogenen
                Daten bei der Nutzung unseres Bonusprogramms verarbeitet werden.
            </div>

            <h2>1. Verantwortlicher</h2>

            <p>
                Kebab Höhle XXL<br>
                Mustafa Erdogan<br>
                Darmstädter Str. 81<br>
                65474 Bischofsheim<br><br>

                Telefon: 06144 2079485<br>
                E-Mail: mustafaerdugulu@outlook.de
            </p>

            <h2>2. Registrierung und Kundenkonto</h2>

            <p>
                Für die Teilnahme am Bonusprogramm verarbeiten wir bei der
                Registrierung folgende Daten:
            </p>

            <p>
                Vorname<br>
                Nachname<br>
                Geburtsdatum<br>
                Telefonnummer, sofern freiwillig angegeben<br>
                Adresse, sofern freiwillig angegeben<br>
                Passwort in technisch geschützter Form<br>
                automatisch erzeugte Kunden-ID
            </p>

            <p>
                Die Verarbeitung erfolgt zur Einrichtung und Verwaltung des
                Kundenkontos sowie zur Durchführung des Bonusprogramms.
                Rechtsgrundlage ist Art. 6 Abs. 1 lit. b DSGVO.
            </p>

            <h2>3. Bonuspunkte und Prämien</h2>

            <p>
                Im Rahmen des Bonusprogramms werden insbesondere die Kunden-ID,
                der aktuelle Punktestand sowie Gutschriften, Einlösungen und
                damit verbundene Bonusvorgänge gespeichert.
            </p>

            <p>
                Diese Daten werden verarbeitet, um Bonuspunkte und Prämien
                korrekt zu verwalten, Buchungen nachvollziehen und einen
                Missbrauch des Bonusprogramms verhindern zu können.
                Rechtsgrundlage ist Art. 6 Abs. 1 lit. b DSGVO.
            </p>

            <h2>4. Freiwillige Angaben</h2>

            <p>
                Telefonnummer und Adresse sind freiwillige Angaben.
                Eine Registrierung ist auch ohne diese Angaben möglich.
                Die für die Einrichtung des Kundenkontos erforderlichen
                Pflichtangaben müssen jedoch angegeben werden.
            </p>

            <h2>5. Werbeeinwilligung</h2>

            <p>
                Kunden können freiwillig einwilligen, Informationen über
                Angebote und Aktionen von Kebab Höhle XXL zu erhalten.
                Die Teilnahme am Bonusprogramm ist nicht von dieser
                Einwilligung abhängig.
            </p>

            <p>
                Rechtsgrundlage für die Verarbeitung zu Werbezwecken ist
                Art. 6 Abs. 1 lit. a DSGVO. Eine erteilte Einwilligung kann
                jederzeit mit Wirkung für die Zukunft widerrufen werden.
                Die Rechtmäßigkeit der bis zum Widerruf erfolgten Verarbeitung
                bleibt davon unberührt.
            </p>

            <p>
                Der Widerruf kann über die oben genannten Kontaktdaten
                erklärt werden.
            </p>

            <h2>6. Push-Benachrichtigungen</h2>

            <p>
                Sofern Push-Benachrichtigungen freiwillig aktiviert werden,
                können Informationen über Angebote und Aktionen auf dem
                verwendeten Endgerät angezeigt werden.
            </p>

            <p>
                Für die technische Bereitstellung werden die hierfür
                erforderlichen technischen Push-Informationen, insbesondere
                Push-Subscriptions sowie zugehörige technische Kennungen
                und Schlüssel, verarbeitet.
            </p>

            <p>
                Werbliche Push-Benachrichtigungen erfolgen auf Grundlage
                einer Einwilligung gemäß Art. 6 Abs. 1 lit. a DSGVO.
                Push-Benachrichtigungen können über die Einstellungen des
                verwendeten Geräts oder Browsers deaktiviert werden.
            </p>

            <h2>7. Hosting und Datenverarbeitung</h2>

            <p>
                Für die technische Bereitstellung des Bonusprogramms nutzen
                wir Dienste von Render Services, Inc., USA.
                Die Anwendung sowie die zugehörige Datenbank werden derzeit
                in der Render-Region Ohio in den Vereinigten Staaten betrieben.
            </p>

            <p>
                Im Rahmen des Hostings können personenbezogene und technische
                Daten durch den Hosting-Dienstleister verarbeitet werden.
            </p>

            <p>
                Render Services, Inc. gibt an, unter dem EU-US Data Privacy
                Framework zertifiziert zu sein. Soweit erforderlich, sieht
                Render für internationale Datenübermittlungen außerdem die
                von der Europäischen Kommission verabschiedeten
                Standardvertragsklauseln vor.
            </p>

            <h2>8. Technische Daten</h2>

            <p>
                Beim Aufruf und bei der Nutzung der Anwendung können technisch
                notwendige Informationen verarbeitet werden. Dazu können
                insbesondere IP-Adresse, Zeitpunkt des Zugriffs, aufgerufene
                Inhalte sowie Informationen über Browser und Endgerät gehören.
            </p>

            <p>
                Diese Verarbeitung dient der sicheren und zuverlässigen
                Bereitstellung der Anwendung sowie der Fehleranalyse.
                Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO.
                Unser berechtigtes Interesse besteht im sicheren und
                funktionsfähigen Betrieb des Bonusprogramms.
            </p>

            <h2>9. Empfänger personenbezogener Daten</h2>

            <p>
                Personenbezogene Daten werden nur weitergegeben oder durch
                Dienstleister verarbeitet, soweit dies für den Betrieb des
                Bonusprogramms erforderlich ist, eine gesetzliche Verpflichtung
                besteht oder eine entsprechende Einwilligung vorliegt.
            </p>

            <p>
                Zu den Empfängern können insbesondere von uns eingesetzte
                IT- und Hosting-Dienstleister gehören.
            </p>

            <h2>10. Speicherdauer</h2>

            <p>
                Daten des Kundenkontos werden grundsätzlich für die Dauer
                der Teilnahme am Bonusprogramm gespeichert.
            </p>

            <p>
                Wird die Löschung des Kundenkontos verlangt, werden die
                personenbezogenen Daten gelöscht, soweit keine gesetzlichen
                Aufbewahrungspflichten oder sonstigen rechtlichen Gründe
                einer Löschung entgegenstehen.
            </p>

            <p>
                Daten, für die gesetzliche Aufbewahrungspflichten bestehen,
                werden für die jeweilige gesetzliche Aufbewahrungsfrist
                gespeichert und anschließend gelöscht.
            </p>

            <p>
                Nachweise über erteilte oder widerrufene Einwilligungen können
                im erforderlichen Umfang zur Erfüllung gesetzlicher
                Nachweispflichten aufbewahrt werden.
            </p>

            <h2>11. Ihre Rechte</h2>

            <p>
                Ihnen stehen im Rahmen der gesetzlichen Voraussetzungen
                insbesondere folgende Rechte zu:
            </p>

            <p>
                Auskunft über Ihre personenbezogenen Daten
                (Art. 15 DSGVO)<br><br>

                Berichtigung unrichtiger Daten
                (Art. 16 DSGVO)<br><br>

                Löschung Ihrer Daten
                (Art. 17 DSGVO)<br><br>

                Einschränkung der Verarbeitung
                (Art. 18 DSGVO)<br><br>

                Datenübertragbarkeit
                (Art. 20 DSGVO)<br><br>

                Widerspruch gegen bestimmte Verarbeitungen
                (Art. 21 DSGVO)<br><br>

                Widerruf einer erteilten Einwilligung mit Wirkung
                für die Zukunft
            </p>

            <p>
                Zur Ausübung Ihrer Rechte können Sie sich an den oben
                genannten Verantwortlichen wenden.
            </p>

            <h2>12. Beschwerderecht</h2>

            <p>
                Sie haben gemäß Art. 77 DSGVO das Recht, sich bei einer
                Datenschutzaufsichtsbehörde zu beschweren, wenn Sie der
                Ansicht sind, dass die Verarbeitung Ihrer personenbezogenen
                Daten gegen die DSGVO verstößt.
            </p>

            <h2>13. Datensicherheit</h2>

            <p>
                Wir treffen angemessene technische und organisatorische
                Maßnahmen zum Schutz personenbezogener Daten vor Verlust,
                unbefugtem Zugriff, Veränderung oder unbefugter Offenlegung.
                Die Übertragung der Anwendung erfolgt über eine verschlüsselte
                HTTPS-Verbindung.
            </p>

            <h2>14. Änderungen dieser Datenschutzerklärung</h2>

            <p>
                Diese Datenschutzerklärung kann angepasst werden, wenn sich
                Funktionen des Bonusprogramms, eingesetzte Dienstleister
                oder rechtliche Anforderungen ändern.
            </p>

            <p>
                <strong>Stand: August 2026</strong>
            </p>

            <a class="btn btn-red" href="/">Zurück</a>
        </div>
    </div>
    """


@app.route("/impressum")
def impressum():
    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE XXL</div>
            <div class="subtitle">Impressum</div>

            <p>
                <strong>Kebab Höhle XXL</strong><br>
                Inhaber: Mustafa Erdogan<br>
                Darmstädter Str. 81<br>
                65474 Bischofsheim
            </p>

            <p>
                Telefon: 06144 2079485<br>
                E-Mail: mustafaerdugulu@outlook.de
            </p>

            <p>
                Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV:
                Mustafa Erdogan,
                Darmstädter Str. 81,
                65474 Bischofsheim
            </p>

            <a class="btn btn-red" href="/">Zurück</a>
        </div>
    </div>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
