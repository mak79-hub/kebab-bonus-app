import os
import base64
from io import BytesIO

import psycopg2
import qrcode
from flask import Flask, request, render_template, url_for, redirect

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


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


def mitarbeiter_style():
    return """
    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family: Arial, Helvetica, sans-serif;
            background: #111;
            color: #fff;
        }

        .page {
            min-height: 100vh;
            padding: 24px;
            display: flex;
            justify-content: center;
            align-items: flex-start;
        }

        .card {
            width: 100%;
            max-width: 520px;
            background: #1b1b1b;
            border: 1px solid #333;
            border-radius: 22px;
            padding: 24px;
            box-shadow: 0 20px 50px rgba(0,0,0,0.45);
        }

        .logo {
            text-align: center;
            font-size: 28px;
            font-weight: 900;
            color: #ff2b2b;
            margin-bottom: 6px;
        }

        .subtitle {
            text-align: center;
            color: #aaa;
            margin-bottom: 24px;
        }

        .info-box {
            background: #252525;
            border-radius: 18px;
            padding: 18px;
            margin-bottom: 18px;
            border: 1px solid #333;
        }

        .label {
            color: #999;
            font-size: 14px;
            margin-bottom: 4px;
        }

        .value {
            font-size: 24px;
            font-weight: 800;
            margin-bottom: 14px;
        }

        .points {
            background: linear-gradient(135deg, #b30000, #ff2b2b);
            border-radius: 18px;
            padding: 18px;
            text-align: center;
            margin-bottom: 22px;
        }

        .points .number {
            font-size: 44px;
            font-weight: 900;
        }

        .points .text {
            font-size: 16px;
            color: #ffe2e2;
        }

        input {
            width: 100%;
            padding: 16px;
            border-radius: 14px;
            border: 1px solid #444;
            background: #111;
            color: #fff;
            font-size: 20px;
            margin-top: 8px;
            margin-bottom: 16px;
            outline: none;
        }

        input:focus {
            border-color: #ff2b2b;
        }

        button, .btn {
            width: 100%;
            border: none;
            border-radius: 14px;
            padding: 16px;
            font-size: 18px;
            font-weight: 800;
            cursor: pointer;
            text-decoration: none;
            display: block;
            text-align: center;
        }

        .btn-red {
            background: #ff2b2b;
            color: white;
        }

        .btn-dark {
            background: #2b2b2b;
            color: white;
            border: 1px solid #444;
            margin-top: 12px;
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
            padding: 14px;
            border-radius: 14px;
            margin-bottom: 18px;
            background: #252525;
            border: 1px solid #444;
            text-align: center;
            font-weight: 700;
        }

        .section-title {
            font-size: 20px;
            font-weight: 900;
            margin-bottom: 10px;
        }

        .divider {
            height: 1px;
            background: #333;
            margin: 24px 0;
        }

        a {
            color: white;
        }

        .small-link {
            display: block;
            text-align: center;
            color: #aaa;
            margin-top: 18px;
            text-decoration: none;
        }

        .danger-note {
            color: #ffb3b3;
            background: #351818;
            border: 1px solid #6b2222;
            padding: 12px;
            border-radius: 14px;
            margin-bottom: 16px;
            font-size: 14px;
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
                    vorname,
                    nachname,
                    geburtsdatum,
                    telefon,
                    adresse,
                    werbeeinwilligung,
                    werbeeinwilligung_am
                )
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (
                vorname,
                nachname,
                geburtsdatum,
                telefon,
                adresse,
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
            INSERT INTO kunden_laeden (
                kunde_id,
                laden_id,
                punkte,
                letzter_besuch
            )
            VALUES (%s, 1, 0, CURRENT_TIMESTAMP)
            ON CONFLICT (kunde_id, laden_id) DO NOTHING
        """, (kunde_db_id,))

        conn.commit()
        cur.close()
        conn.close()

        qr_data = url_for("kunde", kunden_id=kunden_id, _external=True)
        qr_code = make_qr_code(qr_data)

        return f"""
        <h1>Registrierung erfolgreich</h1>
        <h2>Willkommen {vorname} {nachname}</h2>
        <p>Deine Kunden-ID:</p>
        <h2>{kunden_id}</h2>
        <p>Bitte speichere diesen QR-Code. Er wird beim Sammeln und Einlösen von Punkten benötigt.</p>
        <img src="data:image/png;base64,{qr_code}" width="250">
        <br><br>
        <a href="/">Zurück zur Registrierung</a>
        """

    return render_template("register.html")


@app.route("/kunde/<kunden_id>")
def kunde(kunden_id):
    kunden_id = kunden_id.strip().upper()

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
        return "<h1>Kunde nicht gefunden</h1>"

    punktestand = get_punktestand(kunde[0])

    return f"""
    {mitarbeiter_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Kundenkarte</div>

            <div class="info-box">
                <div class="label">Kunden-ID</div>
                <div class="value">{kunde[1]}</div>

                <div class="label">Name</div>
                <div class="value">{kunde[2]} {kunde[3]}</div>
            </div>

            <div class="points">
                <div class="number">{punktestand}</div>
                <div class="text">aktuelle Punkte</div>
            </div>

            <a class="btn btn-red" href="/mitarbeiter/{kunde[1]}">Mitarbeiterbereich öffnen</a>
            <a class="small-link" href="/">Zur Registrierung</a>
        </div>
    </div>
    """


@app.route("/mitarbeiter", methods=["GET", "POST"])
def mitarbeiter():
    if request.method == "POST":
        kunden_id = request.form.get("kunden_id", "").strip().upper()
        return redirect(f"/mitarbeiter/{kunden_id}")

    return f"""
    {mitarbeiter_style()}
    <div class="page">
        <div class="card">
            <div class="logo">KEBAB HÖHLE</div>
            <div class="subtitle">Mitarbeiterbereich</div>

            <form method="POST">
                <div class="section-title">Kunden suchen</div>
                <label class="label">Kunden-ID eingeben</label>
                <input type="text" name="kunden_id" placeholder="KH-30001" required>
                <button class="btn-red" type="submit">Kunden öffnen</button>
            </form>

            <a class="small-link" href="/">Zur Registrierung</a>
        </div>
    </div>
    """


@app.route("/mitarbeiter/<kunden_id>", methods=["GET", "POST"])
def mitarbeiter_kunde(kunden_id):
    kunden_id = kunden_id.strip().upper()
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
        {mitarbeiter_style()}
        <div class="page">
            <div class="card">
                <div class="logo">KEBAB HÖHLE</div>
                <div class="subtitle">Kunde nicht gefunden</div>

                <div class="message">❌ Diese Kunden-ID wurde nicht gefunden.</div>

                <form action="/mitarbeiter" method="POST">
                    <label class="label">Kunden-ID nochmal eingeben</label>
                    <input type="text" name="kunden_id" placeholder="KH-30001" required>
                    <button class="btn-red" type="submit">Kunden suchen</button>
                </form>
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
                INSERT INTO punkte_bewegungen (
                    kunde_id,
                    typ,
                    punkte
                )
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
    {mitarbeiter_style()}
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
                <div class="text">aktuelle Punkte</div>
            </div>

            <form method="POST">
                <div class="section-title">Punkte gutschreiben</div>
                <label class="label">Einkaufsbetrag in Euro</label>
                <input type="number" step="0.01" name="betrag" placeholder="z.B. 12.50" required>
                <button class="btn-green" type="submit">Punkte gutschreiben</button>
            </form>

            <div class="divider"></div>

            <a class="btn btn-orange" href="/mitarbeiter/{kunde[1]}/einloesen">Punkte einlösen</a>
            <a class="btn btn-dark" href="/mitarbeiter">Anderen Kunden suchen</a>
        </div>
    </div>
    """


@app.route("/mitarbeiter/<kunden_id>/einloesen", methods=["GET", "POST"])
def punkte_einloesen(kunden_id):
    kunden_id = kunden_id.strip().upper()
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
        {mitarbeiter_style()}
        <div class="page">
            <div class="card">
                <div class="logo">KEBAB HÖHLE</div>
                <div class="subtitle">Kunde nicht gefunden</div>
                <div class="message">❌ Diese Kunden-ID wurde nicht gefunden.</div>
                <a class="btn btn-red" href="/mitarbeiter">Zurück</a>
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
                INSERT INTO punkte_bewegungen (
                    kunde_id,
                    typ,
                    punkte
                )
                VALUES (%s, %s, %s)
            """, (kunde_db_id, "EINLOESUNG", -punkte_einloesen))

            conn.commit()
            meldung = f"✅ {punkte_einloesen} Punkte erfolgreich eingelöst."
            punktestand = get_punktestand(kunde_db_id)

    cur.close()
    conn.close()

    return f"""
    {mitarbeiter_style()}
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
                <div class="text">verfügbare Punkte</div>
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
