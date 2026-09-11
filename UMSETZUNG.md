# Erweiterung Bestellsystem – Stand September 2026

## Fertig vorbereitet

1. Bestehende Kunden-Speisekarte bleibt die gemeinsame Grundlage.
2. Abholung und Lieferung sind auswählbar.
3. Mitarbeiter können über `/mitarbeiter-bestellung` Bestellungen aufnehmen.
4. Zwei getrennte Kundenmonitore existieren unter `/bestellterminal/1` und
   `/bestellterminal/2`.
5. Jeder Monitor besitzt eine eigene SumUp-Reader-Zuordnung.
6. Alle Bestellungen werden sofort gespeichert und an die Druckwarteschlange
   übergeben – vor der Zahlung.
7. Varianten, Menügröße, Beilage, bis zu zwei Soßen, abgewählte Zutaten, Extras,
   Käse und Hinweise werden an Theke und Druckauftrag übertragen.
8. In der Theke stehen Barzahlung und SumUp ohne vorgeschaltete Bestätigungsdialoge
   direkt zur Verfügung.
9. Gleichzeitige Bestellungen erhalten durch eine Datenbanksperre sicher
   unterschiedliche Tagesnummern.

## Lieferung – erster betriebsfähiger Umfang

- Name, Telefon, vollständige Adresse, gewünschte Zeit und Hinweis
- Barzahlung beim Fahrer
- Kartenzahlung beim Fahrer
- Zahlung bei Abholung

Onlinezahlung von zu Hause ist bewusst noch nicht als scheinbar fertige Funktion
eingebaut. Dafür wird ein separater SumUp-Online-Checkout benötigt; die vorhandene
Cloud-API steuert die physischen Solo-Geräte im Laden.

## Noch nicht aktivierbar

- Echter Küchendruck: Bestellungen stehen vollständig als `BEREIT` in der
  Druckwarteschlange. Sobald das Druckermodell bekannt ist, wird der passende
  Adapter aktiviert.
- Bestellmonitor-Zahlungen: In Render müssen nach dem Kauf und Koppeln der Geräte
  `SUMUP_READER_TERMINAL_1` und `SUMUP_READER_TERMINAL_2` gesetzt werden.

## Sichere GitHub-Reihenfolge

1. `app.py`
2. `templates/bestellen.html`
3. `templates/theke.html`
4. `drucker_agent.py`
5. `README.md`
6. `UMSETZUNG.md`

Erst danach Render deployen. Beim ersten Start erweitert `init_db()` die bestehende
PostgreSQL-Datenbank ohne vorhandene Bestellungen oder Kundendaten zu löschen.
