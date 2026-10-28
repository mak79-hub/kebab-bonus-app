# Startbereitschaft – Kebab-App, 28.10.2026

## Aufbau

Ein gemeinsamer Programmstand, eine getrennte Installation und Datenbank pro
Laden. Das ist bewusst **noch kein gemeinsam genutztes Mehrmandantensystem**.
`SHOP_KEY` bindet eine Datenbank an genau einen Laden. Eine abweichende Kennung
stoppt die Initialisierung, statt vorhandene Kunden einem anderen Laden zuzuordnen.

## Korrigierte Abläufe

- Geldpreise: `menu_catalog.json` bzw. geschützte zentrale Katalogeinstellung;
  Produkt, Variante, Menge, Extras, Beilage und Menü werden serverseitig geprüft.
  Berechnung in Cent; abweichender angezeigter Preis erfordert neue Bestätigung.
- Anmeldung: Kunden bleiben nach erfolgreichem Login/Registrierung bis zu 365
  Tage angemeldet. Stabiler Sitzungsschlüssel aus `SECRET_KEY`, sonst dauerhaft
  in der Datenbank erzeugt. Löschen von Cookies/Website-Daten bleibt ein Logout.
- Personal: eigene serverseitig widerrufbare Sitzung mit 12 Stunden Höchstdauer;
  aktive Rolle und PIN-Version werden bei Zugriffen erneut geprüft. Alte reine
  Rollenflags gelten nicht mehr als Anmeldung. Personal muss nach dem Update
  einmal neu anmelden. Kundenanmeldung gewährt keine Personalrechte.
- PINs: gesalzene Hashwerte, keine Anzeige bestehender PINs; neue PINs mindestens
  sechs Ziffern. Bestehende PINs bleiben bei der Migration verwendbar.
- Chef: optionale TOTP-Zwei-Faktor-Anmeldung unter `/chef-sicherheit`, erst nach
  Bestätigung aktiv, mit acht einmaligen Wiederherstellungscodes. Der Besitzer
  muss den QR-Code selbst mit seiner Authenticator-App verbinden. Ein Foto des
  QR-Codes ist vertraulich. Ein `SECRET_KEY`-Wechsel erfordert eine geplante
  Migration der verschlüsselten TOTP-Daten; niemals unüberlegt rotieren.
- Kundenschutz: Karte und Prämien nur für den angemeldeten Eigentümer; Push wird
  diesem Kunden zugeordnet, nicht einer frei übermittelten Kunden-ID.
- CSRF-Schutz für Formulare/Browser-APIs, Login-Begrenzung, HTML-Maskierung,
  keine Zwischenspeicherung privater Seiten. Druckagent nutzt eigenes Token.
- Stornos werden gespeichert; bezahlte Bestellungen brauchen einen gesonderten
  Erstattungsprozess. Laufende Kartenzahlungen blockieren Storno bis zur Klärung.
- Wiederholte Bestellanfragen mit gleicher Kennung erzeugen dieselbe Bestellung;
  manuelle Punkteaktionen besitzen ebenfalls Wiederholungsschutz.
- Prämien: gemeinsame serverseitige Zeitprüfung und zentraler Punktekatalog;
  offene Prämienbestellungen reservieren Punkte. Einlösung prüft erneut unter
  Kundensperre. Auswertungen berücksichtigen beide Einlösungstypen.
- Punkteregel gilt für manuelle Gutschrift, Bar- und bestätigte Kartenzahlung.
- Modulwahl „Nur Bonus / Nur Bestellen / Beides“ unter `/chef-laden`, wirksam in
  Oberfläche und Server. Gastbestellung benötigt keine Bonusregistrierung.
- Bonus-/Loginportal verwendet dieselben Farben, Schriftfamilie und Komponenten-
  Grundlagen wie die Bestelloberfläche. Prämienprodukte nutzen dieselben Bilder.
- Bestellterminals werden über einmaligen Chef-Code an ein Gerät gebunden.
  Eine frei behauptete Terminal-Station genügt nicht mehr für Kartenzahlungen.

## Bestehende Installation veröffentlichen

1. CI muss für den konkreten Commit erfolgreich sein; Tests betreffen nur die
   temporäre PostgreSQL-Testdatenbank. Echte Zahlungen/Push/Druck sind dort gesperrt.
2. Live-Dienst und Datenbankzuordnung gegenprüfen. Keine fremden App-/DB-Dienste
   im Arbeitsbereich ändern. Render-Umgebungsvariablen bleiben erhalten.
3. Geprüften Entwicklungszweig zusammenführen; der bestehende Render-Autodeploy
   übernimmt. Nicht zusätzlich einen zweiten Deploy anstoßen.
4. Additive Migration läuft unter einer Transaktionssperre, einschließlich der
   bisher in Produktion knappen PIN-/Einstellungs-Spalten. Kunden, Punkte und
   Bestellungen werden nicht gelöscht. Fehler führen zum Rollback der Migration.
5. Neuen konkreten Deploy bis `live` beobachten; `/health`, öffentliche Seiten,
   Zugriffsschutz und Logs prüfen. „Build gestartet“ allein ist kein Erfolg.
6. PINs sind nach der Migration Hashwerte. Ein blindes Zurückrollen auf alten
   Code stellt deshalb keine kompatible Personal-Anmeldung wieder her. Bei
   Problemen korrigierend vorwärts deployen oder eine geplante Datenbank-
   Wiederherstellung durchführen. Render-PITR ist keine getestete Rücksicherung.

## Einen weiteren Laden einrichten

1. Neue eigene PostgreSQL-Datenbank und eigener Render-Dienst, gleicher GitHub-
   Programmstand. Keine gemeinsame `DATABASE_URL` zwischen unterschiedlichen Läden.
2. Eigene `SHOP_KEY`, `DATABASE_URL`, `CHEF_PIN` und möglichst feste `SECRET_KEY`
   über sichere Render-Konfiguration setzen; keine echten Werte ins Repository.
3. Eigenes Betreiberprofil und eigene Speisekarte als JSON vorbereiten. Struktur
   des Menükatalogs entspricht `menu_catalog.json`. Keine fremden Preise/Logos/
   Betreiberangaben ungeprüft übernehmen.
4. `python setup_shop.py --profile /sicherer/pfad/profile.json --menu /sicherer/pfad/menu.json`
   aus der Umgebung der neuen Installation ausführen. Das Werkzeug verlangt eine
   eigene Shop-Kennung und überschreibt nicht die Mustafa-Installation.
5. Bonuspreise, Module, Zeiten und Lieferorte im Chefbereich prüfen. Rechtstexte
   benötigen eine Betreiberprüfung; die Software liefert keine Rechtsberatung.
6. Eigene Zahlungs-/Reader-, Druck- und Push-Zugänge konfigurieren und abnehmen.
   Eine nicht fertig konfigurierte neue Installation nimmt keine Kunden an.

## Noch erforderliche Geräte-/Betriebsabnahme

- Authenticator des tatsächlichen Chefs einrichten und Wiederherstellungscodes
  sicher verwahren, falls Zwei-Faktor-Schutz gewünscht ist.
- Anmeldung/PWA auf Mehmets tatsächlich betroffenem Handy prüfen, einschließlich
  Neustart und Rücknavigation. Ein automatisierter Test ersetzt dieses Gerät nicht.
- Richtigen Küchenbon physisch ausgeben; Verbindungsausfall, Papierfehler,
  Wiederanlauf und einen bewusst bestätigten Wiederholungsdruck prüfen. Keine
  Zusage „genau einmal gedruckt“ bei unbekanntem Zustand eines echten Druckers.
- SumUp am richtigen Gerät: Erfolg, Abbruch, Zeitüberschreitung und Zahlungs-
  abgleich mit realem Händlerkonto. Die automatisierten Tests simulieren SumUp.
- Push auf den vorgesehenen Handys prüfen, ohne Werbung an echte Kunden zu testen.
- Wiederherstellung einer Sicherung in einer separaten Datenbank erproben und
  Kapazität mit realistischen Last-/Parallelitätstests bestimmen.

Ohne diese Abnahmen ist „100 % fehlerfrei“ oder eine uneingeschränkte betriebliche
Freigabe nicht belegt. CI-Ergebnisse und Browserbilder stehen am zugehörigen PR.
