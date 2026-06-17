import os
import base64
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

        input {
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
        }

        input:focus { border-color: #ff2b2b; }

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
            border: 1px solid #444;
            border-radius: 24px;
            overflow: hidden;
            margin-bottom: 22px;
        }

        #reader video {
            width: 100% !important;
            border-radius: 24px;
        }

        #reader select {
            font-size: 18px !important;
            padding: 10px !important;
            height: auto !important;
            min-height: 42px !important;
            border-radius: 10px !important;
            margin-top: 10px !important;
            margin-bottom: 10px !important;
        }

        #reader button {
            font-size: 22px !important;
            padding: 16px !important;
            border-radius: 14px !important;
            font-weight: 900 !important;
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

            input {
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
                max-width: 100% !important;
            }

            #reader select {
                font-size: 20px !important;
                padding: 12px !important;
                min-height: 48px !important;
            }

            #reader button {
                font-size: 24px !important;
                padding: 18px !important;
            }
        }
    </style>
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

    kunde = cur.fetchone()

    cur.close()
    conn.close()

    if not kunde:
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

    punktestand = get_punktestand(kunde[0])
    qr_data = url_for("kunde", kunden_id=kunde[1], _external=True)
    qr_code = make_qr_code(qr_data)

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Bonusprogramm</div>

            <div class="info-box">
                <div class="label">Kunden-ID</div>
                <div class="value">{kunde[1]}</div>

                <div class="label">Name</div>
                <div class="value">{kunde[2]} {kunde[3]}</div>
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

            <div id="reader" style="width:100%; max-width:720px; margin:0 auto 26px auto; overflow:hidden; border-radius:24px;"></div>
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
                qrbox: {{ width: 340, height: 340 }},
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

    kunde = cur.fetchone()

    if not kunde:
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

    kunde_db_id = kunde[0]

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
            meldung = f"✅ {punkte} Punkte erfolgreich gutgeschrieben."
        else:
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
                <div class="value">{kunde[1]}</div>

                <div class="label">Name</div>
                <div class="value">{kunde[2]} {kunde[3]}</div>
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

            <a class="btn btn-orange" href="/mitarbeiter/{kunde[1]}/einloesen">Punkte einlösen</a>
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

    kunde = cur.fetchone()

    if not kunde:
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

    kunde_db_id = kunde[0]
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
            meldung = f"✅ {punkte_einloesen} Punkte erfolgreich eingelöst."
            punktestand = get_punktestand(kunde_db_id)

    cur.close()
    conn.close()

    return f"""
    {app_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Punkte einlösen</div>

            {"<div class='message'>" + meldung + "</div>" if meldung else ""}

            <div class="info-box">
                <div class="label">Kunden-ID</div>
                <div class="value">{kunde[1]}</div>

                <div class="label">Name</div>
                <div class="value">{kunde[2]} {kunde[3]}</div>
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

            <a class="btn btn-dark" href="/mitarbeiter/{kunde[1]}">Zurück zur Kundenseite</a>
            <a class="btn btn-red" href="/scanner">Nächsten Kunden scannen</a>
            <a class="small-link" href="/mitarbeiter-logout">Abmelden</a>
        </div>
    </div>
    """


@app.route("/datenschutz")
def datenschutz():
    return """
    <h1>Datenschutzhinweise</h1>
    <p>Diese Seite ist aktuell nur eine Testversion.</p>
    <p>
    Vor dem Live-Betrieb wird hier eine vollständige DSGVO-konforme
    Datenschutzerklärung eingebunden.
    </p>
    <a href="/">Zurück</a>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
