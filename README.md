# kebab-bonus-app

## Neue Bestellbereiche

- `/bestellen` – Kunden-App mit Abholung und Lieferung
- `/mitarbeiter-bestellung` – Bestellaufnahme für Telefon und Theke
- `/bestellterminal/1` – Kundenmonitor 1
- `/bestellterminal/2` – Kundenmonitor 2
- `/theke` – kompakte Bestellübersicht und Direktzahlung

Jede Bestellung erzeugt sofort einen Eintrag in `druckauftraege`. Die Küche wartet
nicht auf den Zahlungserfolg. Bis der Drucker ausgewählt wurde, bleiben die Aufträge
mit Status `BEREIT` gespeichert und sichtbar.

## Spätere Druckereinrichtung

Der vorbereitete `drucker_agent.py` läuft auf einem Rechner im Laden. Für einen
netzwerkfähigen ESC/POS-Drucker werden gesetzt:

```text
KEBAB_APP_URL=https://kebab-bonus-app.onrender.com
PRINT_AGENT_TOKEN=<langes-zufälliges-geheimes-token>
PRINT_MODE=escpos_tcp
PRINTER_HOST=<lokale-IP-des-Druckers>
PRINTER_PORT=9100
```

Dasselbe `PRINT_AGENT_TOKEN` wird in Render gespeichert. Wenn ein anderes
Druckermodell gewählt wird, wird nur die Funktion `drucken()` im lokalen Agenten
angepasst; Bestellseiten, Datenbank und Theke bleiben unverändert.

## SumUp je Station

```text
SUMUP_READER_ID=<Theke>
SUMUP_READER_TERMINAL_1=<Bestellmonitor-1>
SUMUP_READER_TERMINAL_2=<Bestellmonitor-2>
```

Die Kopplung erfolgt als Chef über:

- `/sumup-einrichten?station=THEKE`
- `/sumup-einrichten?station=TERMINAL_1`
- `/sumup-einrichten?station=TERMINAL_2`
