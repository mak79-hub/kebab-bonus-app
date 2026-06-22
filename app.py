import os
import base64
import urllib.parse
import urllib.request
from io import BytesIO
from datetime import timedelta

import psycopg2
import qrcode
from flask import Flask, request, render_template, url_for, redirect, session

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "kebab-hoehe-test-secret-key")
app.permanent_session_lifetime = timedelta(hours=12)

DATABASE_URL = os.environ.get("DATABASE_URL")
MITARBEITER_PIN = os.environ.get("MITARBEITER_PIN", "1234")
CHEF_PIN = os.environ.get("CHEF_PIN", "9999")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def get_db_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)

    return psycopg2.connect(
        host="localhost",
        database="kebab_assistent",
        user="postgres",
        password="Auto2026!"
    )


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


def app_style():
    return """
    <style>
        * { box-sizing: border-box; }

        body {
            margin: 0;
            font-family: Arial, Helvetica, sans-serif;
            background: #080808;
            color: #fff;
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
            background: #171717;
            border: 1px solid #333;
            border-radius: 28px;
            padding: 34px;
            box-shadow: 0 20px 55px rgba(0,0,0,0.55);
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
            color: #bbb;
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
            color: #bbb;
            font-size: 28px;
            margin-bottom: 32px;
        }

        .info-box {
            background: #252525;
            border-radius: 24px;
            padding: 30px;
            margin-bottom: 30px;
            border: 1px solid #3a3a3a;
        }

        .label {
            color: #aaa;
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
            background: #252525;
            border: 1px solid #444;
            border-radius: 20px;
            padding: 22px;
            color: #ddd;
            font-size: 24px;
            line-height: 1.45;
            margin-bottom: 26px;
        }

        input, textarea {
            width: 100%;
            padding: 28px;
            border-radius: 20px;
            border: 1px solid #444;
            background: #0f0f0f;
            color: #fff;
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
            background: #252525;
            border: 1px solid #444;
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
            background: #252525;
            border: 1px solid #444;
            border-radius: 22px;
            padding: 24px;
            text-align: center;
        }

        .stat-label {
            color: #bbb;
            font-size: 20px;
            font-weight: 800;
            margin-bottom: 10px;
        }

        .stat-value {
            font-size: 46px;
            font-weight: 900;
            color: #fff;
        }

        .history-table-wrap {
            width: 100%;
            overflow-x: auto;
            background: #222;
            border: 1px solid #444;
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
            border-bottom: 1px solid #444;
            text-align: left;
            font-size: 18px;
        }

        .history-table th {
            background: #111;
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
        }
    </style>
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


@app.route("/", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        vorname = request.form.get("vorname", "").strip()
        nachname = request.form.get("nachname", "").strip()
        geburtsdatum = request.form.get("geburtsdatum")
        telefon = request.form.get("telefon", "").strip()
        adresse = request.form.get("adresse", "").strip()
        angebote = request.form.get("angebote") == "on"

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
                    werbeeinwilligung, werbeeinwilligung_am
                )
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (vorname, nachname, geburtsdatum, telefon, adresse, angebote))

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

        if pin == MITARBEITER_PIN:
            session.permanent = True
            session["mitarbeiter_angemeldet"] = True
            return redirect(next_url)
        else:
            meldung = "❌ Falscher PIN."

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiter Login</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <form method="POST">
                <div class="section-title">Mitarbeiter-PIN eingeben</div>
                <label class="label">PIN</label>
                <input type="password" name="pin" placeholder="PIN eingeben" required>
                <input type="hidden" name="next" value="{next_url}">
                <button class="btn-red" type="submit">Einloggen</button>
            </form>

            <a class="small-link" href="/">Zur Registrierung</a>
        </div>
    </div>
    """


@app.route("/mitarbeiter-logout")
def mitarbeiter_logout():
    session.pop("mitarbeiter_angemeldet", None)
    return redirect("/mitarbeiter-login")


@app.route("/mitarbeiter", methods=["GET", "POST"])
def mitarbeiter():
    if not ist_mitarbeiter():
        return redirect("/mitarbeiter-login?next=/mitarbeiter")

    if request.method == "POST":
        kunden_id = request.form.get("kunden_id", "").strip().upper()
        return redirect(f"/mitarbeiter/{kunden_id}")

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiterbereich</div>

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
            cur.execute("""
                INSERT INTO punkte_bewegungen (kunde_id, typ, punkte)
                VALUES (%s, %s, %s)
            """, (kunde_db_id, "GUTSCHRIFT", punkte))

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
            cur.execute("""
                INSERT INTO punkte_bewegungen (kunde_id, typ, punkte)
                VALUES (%s, %s, %s)
            """, (kunde_db_id, "EINLOESUNG", -punkte_einloesen))

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

        if pin == CHEF_PIN:
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

            <a class="btn btn-red" href="/chef-nachrichten">Telegram-Angebot senden</a>
            <a class="btn btn-dark" href="/chef-logout">Chef abmelden</a>
            <a class="small-link" href="/">Zur Registrierung</a>
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
        elif len(nachricht) > 1000:
            meldung = "❌ Die Nachricht ist zu lang. Maximal 1000 Zeichen."
        else:
            text = f"🔊‼Kebab Höhle Angebot:\n\n{nachricht}"
            ok, info = send_telegram_message(text)

            if ok:
                meldung = "✅ " + info
                alte_nachricht = ""
            else:
                meldung = "❌ " + info

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Chef Nachrichten</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="hint">
                Hier kann der Chef eine Angebotsnachricht schreiben und als Telegram senden.
                Die Mitarbeiterseite bleibt unverändert.
            </div>

            <form method="POST">
                <div class="section-title">Angebotsnachricht</div>
                <label class="label">Nachricht</label>
                <textarea name="nachricht" placeholder="z.B. Nächste Woche Pizza Mexico XL nur 10 € bei deiner Kebab Höhle." required>{alte_nachricht}</textarea>
                <button class="btn-red" type="submit">Telegram senden</button>
            </form>

            <a class="btn btn-dark" href="/chef-dashboard">Zum Chef Dashboard</a>
            <a class="btn btn-dark" href="/chef-logout">Chef abmelden</a>
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
                Wir verarbeiten personenbezogene Daten ausschließlich zur Durchführung
                unseres Bonusprogramms und zur Verwaltung von Kundenkonten.
            </div>

            <h2>Verantwortlicher</h2>

            <p>
                Kebab Höhle XXL<br>
                Mustafa Erdogan<br>
                Darmstädter Str. 81<br>
                65474 Bischofsheim<br><br>

                Telefon: 06144 2079485<br>
                E-Mail: mustafaerdugulu@outlook.de
            </p>

            <h2>Gespeicherte Daten</h2>

            <p>
                Vorname<br>
                Nachname<br>
                Geburtsdatum<br>
                Telefonnummer<br>
                Adresse<br>
                Kunden-ID<br>
                Punktestand<br>
                Punktebewegungen
            </p>

            <h2>Zweck der Speicherung</h2>

            <p>
                Teilnahme am Bonusprogramm,
                Verwaltung von Punkten,
                Verhinderung von Doppelregistrierungen,
                Kundenservice sowie zukünftige Bestell- und Lieferfunktionen.
            </p>

            <h2>Werbeeinwilligung</h2>

            <p>
                Kunden können freiwillig zustimmen,
                Informationen zu Angeboten und Aktionen zu erhalten.
                Diese Einwilligung kann jederzeit widerrufen werden.
            </p>

            <h2>Ihre Rechte</h2>

            <p>
                Auskunft,
                Berichtigung,
                Löschung,
                Einschränkung der Verarbeitung
                sowie Widerruf erteilter Einwilligungen.
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
