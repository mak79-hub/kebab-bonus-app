"""Lokaler Küchen-Druckagent.

Läuft später auf einem kleinen Rechner im Laden. Bis der Drucker feststeht,
bleibt PRINT_MODE=disabled. Netzwerkfähige ESC/POS-Drucker können ohne weitere
Bibliothek über Port 9100 angesprochen werden.
"""
import json
import os
import socket
import time
import urllib.error
import urllib.request


APP_URL = os.environ.get("KEBAB_APP_URL", "").rstrip("/")
TOKEN = os.environ.get("PRINT_AGENT_TOKEN", "")
PRINT_MODE = os.environ.get("PRINT_MODE", "disabled").lower()
PRINTER_HOST = os.environ.get("PRINTER_HOST", "")
PRINTER_PORT = int(os.environ.get("PRINTER_PORT", "9100"))


def api(path, method="GET", data=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(
        APP_URL + path,
        data=body,
        method=method,
        headers={"X-Print-Agent-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def namen(liste):
    return ", ".join(str(x.get("name") if isinstance(x, dict) else x) for x in (liste or []))


def beleg(payload):
    def sauber(value):
        if isinstance(value, str):
            return "".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in value)
        if isinstance(value, dict):
            return {k: sauber(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sauber(v) for v in value]
        return value
    payload = sauber(payload)
    kunde = payload.get("kunde") or {}
    lines = [
        payload.get("shop_name") or "KEBAP HOEHLE XXL",
        "BESTELLUNG #" + str(payload.get("bestellnummer", "")),
        f"{payload.get('bestellart', '')} | {payload.get('quelle', '')}",
        "=" * 42,
    ]
    for item in payload.get("positionen") or []:
        lines.append(f"{item.get('menge', 1)}x {item.get('produkt', '')} {item.get('variante', '')}".strip())
        if item.get("menue"):
            menu = item["menue"]
            lines.append("  MENUE: " + str(menu.get("name") if isinstance(menu, dict) else menu))
        if item.get("beilage"):
            lines.append("  BEILAGE: " + str(item["beilage"]))
        if item.get("saucen"):
            lines.append("  SOSSEN: " + namen(item["saucen"]))
        if item.get("ohne"):
            lines.append("  OHNE: " + namen(item["ohne"]))
        if item.get("extras"):
            lines.append("  EXTRAS: " + namen(item["extras"]))
        if item.get("hinweis"):
            lines.append("  HINWEIS: " + str(item["hinweis"]))
        lines.append("-" * 42)
    if kunde.get("name"):
        lines.append("KUNDE: " + kunde["name"])
    if kunde.get("telefon"):
        lines.append("TEL: " + kunde["telefon"])
    if kunde.get("adresse"):
        lines.append("ADRESSE: " + kunde["adresse"])
    if kunde.get("zeit"):
        lines.append("ZEIT: " + kunde["zeit"])
    if kunde.get("hinweis"):
        lines.append("HINWEIS: " + kunde["hinweis"])
    lines.extend(["", "", ""])
    return "\n".join(lines)


def drucken(text):
    if PRINT_MODE != "escpos_tcp":
        raise RuntimeError("Noch kein Drucker eingerichtet (PRINT_MODE=disabled).")
    if not PRINTER_HOST:
        raise RuntimeError("PRINTER_HOST fehlt.")
    daten = b"\x1b\x40" + text.encode("cp858", errors="replace") + b"\n\n\x1d\x56\x00"
    with socket.create_connection((PRINTER_HOST, PRINTER_PORT), timeout=10) as verbindung:
        verbindung.sendall(daten)


def run():
    if not APP_URL or not TOKEN:
        raise SystemExit("KEBAB_APP_URL und PRINT_AGENT_TOKEN muessen gesetzt sein.")
    while True:
        try:
            result = api("/api/druckauftraege")
            for auftrag in result.get("auftraege") or []:
                try:
                    drucken(beleg(auftrag["payload"]))
                    api(f"/api/druckauftraege/{auftrag['id']}/status", "POST", {"status": "GEDRUCKT"})
                except Exception as error:
                    if PRINT_MODE != "disabled":
                        api(f"/api/druckauftraege/{auftrag['id']}/status", "POST", {
                            "status": "FEHLER", "fehler": str(error)
                        })
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            print("Druckdienst nicht erreichbar:", error)
        time.sleep(2)


if __name__ == "__main__":
    run()

