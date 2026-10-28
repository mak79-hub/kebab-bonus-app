"""Transactional order safety, persistent cancellations and unified reward costs."""
import hashlib
import json
import re
import secrets
from flask import jsonify, request, session, redirect
from shop import catalog, paid_positions, transaction, purchase_points, enabled
from access import digest


def order_metadata(data, kind):
    key=data.get('anfrage_id')
    if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}',key):
        raise ValueError('Bitte die Bestellseite neu laden. Die eindeutige Anfragekennung fehlt.')
    owner=session.setdefault('_order_owner',secrets.token_urlsafe(24))
    request_id=digest(kind+'|'+owner+'|'+key)
    try:
        body_hash=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    except (ValueError,TypeError):
        raise ValueError('Ungültige Bestelldaten.') from None
    return request_id,body_hash


def repeated_order(cur,meta):
    cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(meta[0],))
    cur.execute('SELECT id,tagesnummer,token,punkte_betrag,anfrage_hash,status FROM bestellungen WHERE anfrage_id=%s', (meta[0],))
    row=cur.fetchone()
    if not row:
        return None
    if not secrets.compare_digest(row[4] or '',meta[1]):
        return jsonify(ok=False,fehler='Diese Anfragekennung wurde bereits für eine andere Bestellung verwendet. Bitte den Warenkorb erneut öffnen.'),409
    return jsonify(ok=True,bestellung_id=row[0],bestellnummer=row[1],token=row[2],qr='ORDER:'+row[2],punkte=row[3],status=row[5],wiederholt=True)


def install_order_safety(app,legacy,access,connect,cfg):
    legacy['order_metadata']=order_metadata
    legacy['repeated_order']=repeated_order
    original_manual_credit=app.view_functions['mitarbeiter_kunde']
    original_manual_redeem=app.view_functions['punkte_einloesen']

    def reward_positions(items):
        positions,total=paid_positions(items,catalog(connect,True))
        return [{
            'produkt_id':p['productId'],'produkt_name':p['product']['name'],'variante':p['variant']['name'],
            'menge':p['qty'],'einzelpunkte':p['unit_cents'],'gesamtpunkte':p['total_cents'],
            'optionen':{'extras':[{'name':e['name'],'punkte':round(e['price']*100)} for e in p['extras']],
                        'upgrade':{'name':p['upgrade']['name'],'desc':p['upgrade']['desc'],'punkte':round(p['upgrade']['price']*100)} if p['upgrade'] else None,
                        'sauces':p['sauces'],'without':p['without'],'side':p['side']}
        } for p in positions],total
    legacy['praemien_positionen_pruefen']=reward_positions

    def reward_order():
        customer=legacy['angemeldeter_kunde']()
        if not customer: return jsonify(ok=False,fehler='Bitte als Kunde anmelden.'),401
        data=request.get_json(silent=True)
        if not isinstance(data,dict): return jsonify(ok=False,fehler='Ungültige Bestelldaten.'),400
        try:
            _positions,total=reward_positions(data.get('items'))
            meta=order_metadata(data,'reward')
        except ValueError as exc:
            return jsonify(ok=False,fehler=str(exc)),400
        # Reserve capacity and insert under the SAME customer transaction/lock.
        with transaction(connect) as cur:
            cur.execute('SELECT id FROM kunden WHERE id=%s FOR UPDATE',(customer[0],))
            repeated=repeated_order(cur,meta)
            if repeated is not None: return repeated
            cur.execute('SELECT COALESCE(SUM(punkte),0) FROM punkte_bewegungen WHERE kunde_id=%s',(customer[0],));balance=cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(punkte_betrag),0) FROM bestellungen WHERE kunde_id=%s AND zahlungsart='BONUSPUNKTE' AND status='WARTET_AUF_ZAHLUNG' AND NOT punkte_eingeloest",(customer[0],));reserved=cur.fetchone()[0]
            if total>balance-reserved:
                return jsonify(ok=False,fehler='Nicht genügend freie Punkte. Offene Prämienbestellungen reservieren bereits Punkte.',punktestand=max(0,balance-reserved)),409
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('bestellungen_tagesnummer_' || CURRENT_DATE::text))")
            cur.execute('SELECT COALESCE(MAX(tagesnummer),99)+1 FROM bestellungen WHERE bestelldatum=CURRENT_DATE');number=cur.fetchone()[0]
            token=secrets.token_urlsafe(24)
            cur.execute("INSERT INTO bestellungen(token,tagesnummer,kunde_id,gesamtbetrag,zahlungsart,punkte_betrag,anfrage_id,anfrage_hash) VALUES (%s,%s,%s,0,'BONUSPUNKTE',%s,%s,%s) RETURNING id",(token,number,customer[0],total,meta[0],meta[1]));order_id=cur.fetchone()[0]
            from psycopg2.extras import Json
            for p in _positions:
                options={**p['optionen'],'einzelpunkte':p['einzelpunkte'],'gesamtpunkte':p['gesamtpunkte']}
                cur.execute('INSERT INTO bestellpositionen(bestellung_id,produkt_id,produkt_name,variante,menge,einzelpreis,gesamtpreis,extras) VALUES (%s,%s,%s,%s,%s,0,0,%s)',(order_id,p['produkt_id'],p['produkt_name'],p['variante'],p['menge'],Json(options)))
            legacy['druckauftrag_anlegen'](cur,order_id,{
                'shop_name':cfg()['name'],'bestellnummer':number,'quelle':'PRAEMIEN_APP','bestellart':'ABHOLUNG','zahlungsart':'BONUSPUNKTE',
                'kunde':{'name':customer[2]+' '+customer[3]},
                'positionen':[{'menge':p['menge'],'produkt':p['produkt_name'],'variante':p['variante'],
                               'beilage':p['optionen']['side'],'menue':p['optionen']['upgrade'],
                               'saucen':p['optionen']['sauces'],'ohne':p['optionen']['without'],'extras':p['optionen']['extras']} for p in _positions]})
        return jsonify(ok=True,bestellung_id=order_id,bestellnummer=number,token=token,qr='ORDER:'+token,punkte=total)
    app.view_functions['api_praemien_bestellung']=reward_order

    def manual_action(kunden_id,redeem=False):
        original=original_manual_redeem if redeem else original_manual_credit
        if request.method=='GET': return original(kunden_id)
        if not access.role('mitarbeiter'): return jsonify(ok=False,fehler='Mitarbeiter-Anmeldung erforderlich.'),401
        action=request.form.get('_action_id','')
        if not re.fullmatch(r'[a-f0-9]{32}',action): return 'Bitte die Kundenseite neu laden.',400
        reference=digest(session['personal_token']+'|'+action)
        try:
            with transaction(connect) as cur:
                cur.execute('SELECT id FROM kunden WHERE kunden_id=%s FOR UPDATE',(kunden_id.upper(),));customer=cur.fetchone()
                if not customer: return 'Kunde nicht gefunden.',404
                cur.execute('SELECT 1 FROM punkte_bewegungen WHERE referenz=%s',(reference,))
                if cur.fetchone(): return redirect('/mitarbeiter/'+kunden_id)
                if redeem:
                    raw=request.form.get('punkte_einloesen','')
                    if not raw.isdigit() or not 1<=int(raw)<=1000000: raise ValueError('Bitte gültige Punkte eingeben.')
                    points=int(raw)
                    cur.execute("SELECT COALESCE((SELECT SUM(punkte) FROM punkte_bewegungen WHERE kunde_id=%s),0)-COALESCE((SELECT SUM(punkte_betrag) FROM bestellungen WHERE kunde_id=%s AND zahlungsart='BONUSPUNKTE' AND status='WARTET_AUF_ZAHLUNG' AND NOT punkte_eingeloest),0)",(customer[0],customer[0]))
                    if points>cur.fetchone()[0]: raise ValueError('Nicht genügend freie Punkte. Bitte offene Prämienbestellungen beachten.')
                else:
                    points=purchase_points(cur,request.form.get('betrag','').replace(',','.'))
                    if points<=0: raise ValueError('Bitte einen gültigen Einkaufsbetrag eingeben.')
                cur.execute('INSERT INTO punkte_bewegungen(kunde_id,typ,punkte,mitarbeiter_id,referenz) VALUES (%s,%s,%s,%s,%s)',
                            (customer[0],'EINLOESUNG' if redeem else 'GUTSCHRIFT',-points if redeem else points,session['mitarbeiter_id'],reference))
                access.audit(cur,'MANUAL_REDEMPTION' if redeem else 'MANUAL_CREDIT',customer[0])
        except ValueError as exc:
            from markupsafe import escape
            return legacy['auto_back_to_scanner_page']('Bitte prüfen',str(escape(str(exc)))),400
        return legacy['auto_back_to_scanner_page']('Punkte eingelöst' if redeem else 'Punkte gutgeschrieben',str(points)+' Punkte wurden erfolgreich '+('eingelöst.' if redeem else 'gutgeschrieben.'))
    app.view_functions['mitarbeiter_kunde']=lambda kunden_id:manual_action(kunden_id)
    app.view_functions['punkte_einloesen']=lambda kunden_id:manual_action(kunden_id,True)

    @app.route('/api/bestellung/<token>/stornieren',methods=['POST'])
    def cancel_order(token):
        if not access.staff(): return jsonify(ok=False,fehler='Personal-Anmeldung erforderlich.'),401
        with transaction(connect) as cur:
            cur.execute('SELECT id,status,sumup_status FROM bestellungen WHERE token=%s FOR UPDATE',(token,));row=cur.fetchone()
            if not row: return jsonify(ok=False,fehler='Bestellung nicht gefunden.'),404
            if row[1]=='STORNIERT': return jsonify(ok=True,status='STORNIERT',bereits_storniert=True)
            if row[1]=='BEZAHLT': return jsonify(ok=False,fehler='Bezahlte Bestellungen benötigen einen getrennten Erstattungsablauf.'),409
            if row[2] in ('STARTED','PENDING'):
                return jsonify(ok=False,fehler='Zuerst laufende Kartenzahlung abbrechen und Status prüfen.'),409
            cur.execute("UPDATE bestellungen SET status='STORNIERT',druckstatus=CASE WHEN druckstatus='GEDRUCKT' THEN druckstatus ELSE 'STORNIERT' END WHERE id=%s",(row[0],))
            cur.execute("UPDATE druckauftraege SET status='STORNIERT' WHERE bestellung_id=%s AND status IN ('BEREIT','FEHLER')",(row[0],))
            access.audit(cur,'ORDER_CANCELLED',row[0])
        return jsonify(ok=True,status='STORNIERT')

    @app.route('/api/druckauftraege/<int:auftrag_id>/wiederholen',methods=['POST'])
    def retry_print(auftrag_id):
        if not access.staff(): return jsonify(ok=False,fehler='Personal-Anmeldung erforderlich.'),401
        with transaction(connect) as cur:
            cur.execute("UPDATE druckauftraege d SET status='BEREIT',fehler=NULL FROM bestellungen b WHERE d.id=%s AND d.bestellung_id=b.id AND d.status='FEHLER' AND b.status<>'STORNIERT' RETURNING d.bestellung_id",(auftrag_id,));row=cur.fetchone()
            if not row:return jsonify(ok=False,fehler='Nur fehlgeschlagene, nicht stornierte Aufträge können wiederholt werden.'),409
            cur.execute("UPDATE bestellungen SET druckstatus='BEREIT' WHERE id=%s",(row[0],));access.audit(cur,'PRINT_RETRIED',auftrag_id)
        return jsonify(ok=True)
