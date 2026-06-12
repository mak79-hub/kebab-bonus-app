import os
import base64
from io import BytesIO

import psycopg2
import qrcode
from flask import Flask, request, render_template, url_for

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
    <h1>Kundenkarte</h1>
    <p>Kunden-ID:</p>
    <h2>{kunde[1]}</h2>
    <p>Name:</p>
    <h2>{kunde[2]} {kunde[3]}</h2>
    <p>Aktueller Punktestand:</p>
    <h2>{punktestand} Punkte</h2>
    <a href="/">Zurück</a>
    """

@app.route("/mitarbeiter", methods=["GET", "POST"])
def mitarbeiter():
    if request.method == "POST":
        kunden_id = request.form.get("kunden_id", "").strip().upper()
        return f"""
        <script>
            window.location.href = "/mitarbeiter/{kunden_id}";
        </script>
        """

    return """
    <h1>Mitarbeiterbereich</h1>
    <form method="POST">
        Kunden-ID eingeben:<br>
        <input type="text" name="kunden_id" placeholder="KH-30001" required><br><br>
        <button type="submit">Kunden suchen</button>
    </form>
    <br>
    <a href="/">Zurück</a>
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
        return """
        <h1>Kunde nicht gefunden</h1>
        <form action="/mitarbeiter" method="POST">
            Kunden-ID nochmal eingeben:<br>
            <input type="text" name="kunden_id" placeholder="KH-30001" required><br><br>
            <button type="submit">Kunden suchen</button>
        </form>
        """

    kunde_db_id = kunde[0]

    if request.method == "POST":
        aktion = request.form.get("aktion")

        if aktion == "gutschrift":
            betrag_text = request.form.get("betrag", "0").replace(",", ".")
            betrag = float(betrag_text)
            punkte = int(betrag)

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

        if aktion == "einloesung":
            punkte_text = request.form.get("punkte_einloesen", "0")
            punkte_einloesen = int(punkte_text)

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

    cur.close()
    conn.close()

    punktestand = get_punktestand(kunde_db_id)

    return f"""
    <h1>Mitarbeiterbereich</h1>
    <p>{meldung}</p>

    <p>Kunden-ID:</p>
    <h2>{kunde[1]}</h2>

    <p>Name:</p>
    <h2>{kunde[2]} {kunde[3]}</h2>

    <p>Aktueller Punktestand:</p>
    <h2>{punktestand} Punkte</h2>

    <hr>

    <h3>Punkte gutschreiben</h3>
    <form method="POST">
        Einkaufsbetrag in Euro:<br>
        <input type="number" step="0.01" name="betrag" required><br><br>
        <input type="hidden" name="aktion" value="gutschrift">
        <button type="submit">Punkte gutschreiben</button>
    </form>

    <hr>

    <h3>Punkte einlösen</h3>
    <form method="POST">
        Punkte einlösen:<br>
        <input type="number" name="punkte_einloesen" required><br><br>
        <input type="hidden" name="aktion" value="einloesung">
        <button type="submit">Punkte einlösen</button>
    </form>

    <br>
    <a href="/mitarbeiter">Anderen Kunden suchen</a>
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
