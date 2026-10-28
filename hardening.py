"""Production guardrails and unified portal around the existing order workflows.

One deployment/database per shop. Customer sessions never grant staff privileges.
All mutations use CSRF; print agents use a separate scoped token instead.
"""
import copy
import json
import os
import re
import secrets
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from flask import abort, g, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import pyotp
from access import Access, safe_next, digest
from shop import (CATALOG, catalog, config, enabled, cents, paid_positions,
                  purchase_points, transaction, setting, save_setting)

BONUS_ENDPOINTS = {'register','login','kunde','kunde_praemien','kunde_praemien_alt','api_praemien_bestellung',
    'mitarbeiter','scanner','mitarbeiter_kunde','punkte_einloesen','chef_kunden','chef_gutschriften',
    'chef_einloesungen','chef_statistiken','chef_praemien','chef_praemie_neu','chef_praemie_bearbeiten',
    'chef_praemie_loeschen','chef_punkte_regel','chef_nachrichten','push_public_key','push_subscribe','customer_settings'}
ORDER_ENDPOINTS = {'bestellen','mitarbeiter_bestellung','bestellterminal','api_bestellung','chef_menu','chef_terminals','terminal_activation'}


def install(app, legacy):
    connect = legacy['get_db_connection']
    access = Access(app, connect)
    app.extensions['access'] = access
    with transaction(connect) as cur:
        app.secret_key = os.environ.get('SECRET_KEY') or setting(cur,'_session_secret')
    if not app.secret_key:
        raise RuntimeError('Ein stabiler Sitzungsschlüssel ist erforderlich.')
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE','true').lower()!='false',
                      MAX_CONTENT_LENGTH=2*1024*1024,
                      PERMANENT_SESSION_LIFETIME=timedelta(days=365))
    # Render terminates TLS. Do not trust client-controlled forwarded identities.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=0, x_for=0)
    legacy['ist_chef'] = lambda: access.role('chef')
    legacy['ist_mitarbeiter'] = lambda: access.role('mitarbeiter')
    legacy['paid_positions'] = lambda items: paid_positions(items, catalog(connect))
    legacy['purchase_points'] = lambda cur,amount: purchase_points(cur,amount) if enabled(config(connect),'bonus') else 0

    def cfg():
        return config(connect)

    def page(mode, title, **data):
        shop = cfg()
        defaults = {'shop':shop,'mode':mode,'title':title,'bonus':enabled(shop,'bonus'),
                    'ordering':enabled(shop,'ordering'),'back':'/','area':'','wide':False}
        defaults.update(data)
        return render_template('portal.html',**defaults)

    def error(message,status=400):
        if request.path.startswith('/api/') or request.path.startswith('/push-'):
            return jsonify(ok=False,success=False,fehler=message,message=message),status
        return page('info','Bitte prüfen',message=message),status

    def bestellstatus(zeitpunkt=None):
        tz = ZoneInfo('Europe/Berlin')
        now = zeitpunkt.astimezone(tz) if zeitpunkt else datetime.now(tz)
        hours = cfg()['hours']
        for offset in range(8):
            day = now.date()+timedelta(days=offset)
            pair = hours.get(str(day.weekday()))
            if not pair:
                continue
            start = datetime.combine(day,time.fromisoformat(pair[0]),tzinfo=tz)
            end = datetime.combine(day,time.fromisoformat(pair[1]),tzinfo=tz)
            if offset==0 and start<=now<end:
                return {'offen':True,'ruhetag':False,'naechste_oeffnung':None,'server_zeit':now.isoformat()}
            if start>now:
                return {'offen':False,'ruhetag':not bool(hours.get(str(now.weekday()))),
                        'naechste_oeffnung':start.isoformat(),'server_zeit':now.isoformat()}
        return {'offen':False,'ruhetag':True,'naechste_oeffnung':None,'server_zeit':now.isoformat()}

    legacy['aktueller_bestellstatus'] = bestellstatus
    # Mapping delegates lookups per request, without mutating process globals.
    class DeliveryMap(dict):
        def __contains__(self,key): return key in cfg()['delivery']
        def __getitem__(self,key): return cfg()['delivery'][key]
    legacy['LIEFERMINDESTWERTE'] = DeliveryMap()

    @app.context_processor
    def shop_context():
        context={'shop':cfg()}
        if request.endpoint in {'bestellen','mitarbeiter_bestellung','bestellterminal'}:
            context['menu_catalog']=catalog(connect)
            context['staff_back_url']='/chef-dashboard' if access.role('chef') else '/mitarbeiter'
        elif request.endpoint=='kunde_praemien':
            context['reward_catalog']=catalog(connect,True)
        return context

    @app.before_request
    def guard():
        if request.endpoint=='static' or request.path=='/service-worker.js':
            return None
        if request.endpoint is None:
            return None
        endpoint = request.endpoint
        if endpoint=='health':
            return None
        shop = cfg()
        if not shop['setup_complete'] and not endpoint.startswith('chef_') and endpoint not in {'health','shop_manifest'}:
            return error('Dieser Laden wird noch eingerichtet. Bestellungen und Registrierungen sind noch nicht freigegeben.',503)
        if endpoint in BONUS_ENDPOINTS and not enabled(shop,'bonus'):
            return error('Das Bonusprogramm ist für diesen Laden nicht aktiviert.',404)
        if endpoint in ORDER_ENDPOINTS and not enabled(shop,'ordering'):
            return error('Das Bestellsystem ist für diesen Laden nicht aktiviert.',404)
        # Central role boundary includes every current and future chef endpoint.
        if endpoint.startswith('chef_') and endpoint not in {'chef_login','chef_logout'} and not access.role('chef'):
            return redirect('/chef-login') if request.method=='GET' else error('Chef-Anmeldung erforderlich.',401)
        if endpoint in {'mitarbeiter','scanner','mitarbeiter_kunde','punkte_einloesen','mitarbeiter_bestellung','theke'}:
            identity = access.staff()
            if not identity or (endpoint in {'mitarbeiter','scanner','mitarbeiter_kunde','punkte_einloesen'} and identity['role']!='mitarbeiter'):
                return redirect(url_for('mitarbeiter_login',next=request.path)) if request.method=='GET' else error('Mitarbeiter-Anmeldung erforderlich.',401)
        if endpoint in {'kunde','kunde_praemien','kunde_praemien_alt'}:
            customer_code = (request.view_args or {}).get('kunden_id','').strip().upper()
            if session.get('kunde_id') != customer_code:
                return redirect('/login')
        if endpoint in {'push_subscribe','customer_settings'} and not session.get('kunde_id'):
            return error('Bitte zuerst als Kunde anmelden.',401)
        if endpoint in {'api_sumup_start','api_sumup_status'} and not access.staff():
            if not valid_terminal():
                return error('Autorisierter Personal- oder Terminalzugang erforderlich.',401)
            with transaction(connect) as cur:
                cur.execute('SELECT station FROM bestellungen WHERE token=%s', (request.view_args['token'],))
                row = cur.fetchone()
                if not row or row[0] != session.get('terminal_station'):
                    return error('Diese Bestellung gehört nicht zu diesem Terminal.',403)
        if endpoint=='bestellterminal':
            if not valid_terminal() or session.get('terminal_station')!='TERMINAL_'+str(request.view_args['nummer']):
                return redirect('/terminal-aktivieren')
        if request.method in {'POST','PUT','PATCH','DELETE'}:
            agent = endpoint=='api_druckauftrag_status' and legacy['druck_agent_erlaubt']()
            token = request.headers.get('X-CSRF-Token') or request.form.get('_csrf','')
            expected = session.get('_csrf','')
            if not agent and (not expected or not token or not secrets.compare_digest(str(token),str(expected))):
                return error('Sicherheitsprüfung fehlgeschlagen. Bitte Seite neu laden und erneut versuchen.',403)
        if endpoint=='api_bestellung':
            data = request.get_json(silent=True)
            if not isinstance(data,dict):
                return error('Ungültige Bestelldaten.')
            source = data.get('bestellquelle') or 'KUNDEN_APP'
            station = data.get('station') or ''
            if not isinstance(source,str) or not isinstance(station,str):
                return error('Bestellquelle oder Station ist ungültig.')
            if source=='MITARBEITER':
                if not access.staff() or station!='THEKE':
                    return error('Autorisierter Thekenzugang erforderlich.',401)
            elif source.startswith('BESTELLMONITOR_'):
                if not valid_terminal() or source!='BESTELLMONITOR_'+session['terminal_station'][-1] or station!=session['terminal_station'] or data.get('bestellart')!='VOR_ORT':
                    return error('Dieses Bestellterminal ist nicht autorisiert.',401)
            elif source!='KUNDEN_APP' or station or data.get('bestellart')=='VOR_ORT' or data.get('zahlungsart')=='SUMUP':
                return error('Bestellquelle, Station oder Zahlungsart ist nicht erlaubt.')
        if endpoint=='api_praemien_bestellung':
            status = legacy['aktueller_bestellstatus']()
            if not status['offen']:
                return jsonify(ok=False,fehler='Heute ist Ruhetag.' if status['ruhetag'] else 'Bestellungen sind zurzeit nicht verfügbar.',**status),403
        session.setdefault('_csrf',secrets.token_urlsafe(32))
        session.setdefault('_order_owner',secrets.token_urlsafe(24))

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Permissions-Policy']='camera=(self), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy']="object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.is_secure:
            response.headers['Strict-Transport-Security']='max-age=31536000'
        if response.status_code>=500 and response.is_json:
            response.set_data(json.dumps({'ok':False,'fehler':'Ein interner Fehler ist aufgetreten. Bitte erneut versuchen oder den Laden kontaktieren.'}))
        if response.mimetype in ('text/html','application/json'):
            response.headers['Cache-Control']='no-store, private'
            response.headers['Vary']='Cookie'
        if response.mimetype!='text/html' or response.status_code>=500:
            return response
        token = session.setdefault('_csrf',secrets.token_urlsafe(32))
        soup = BeautifulSoup(response.get_data(as_text=True),'html.parser')
        meta = soup.new_tag('meta',attrs={'name':'csrf-token','content':token})
        script = soup.new_tag('script',src='/static/security.js')
        link = soup.new_tag('link',rel='stylesheet',href='/static/portal.css')
        if soup.head:
            soup.head.insert(0,script); soup.head.insert(0,meta); soup.head.append(link)
        else:
            soup.insert(0,script);soup.insert(0,meta);soup.append(link)
        for form in soup.find_all('form'):
            if form.get('method','get').lower()=='post':
                form.insert(0,soup.new_tag('input',type='hidden',attrs={'name':'_csrf','value':token}))
                form.insert(0,soup.new_tag('input',type='hidden',attrs={'name':'_action_id','value':secrets.token_hex(16)}))
        # Replace legacy display-only branding, never script/data content.
        shop=cfg()
        identities={'Kebap Höhle XXL':shop['name'],'Kebab Höhle XXL':shop['name'],
                    'KEBAB HÖHLE XXL':shop['name'],'KEBAB HÖHLE':shop['name'],
                    'DÖNI BONUS':shop['name']+' Bonus','DÖNİ BONUS':shop['name']+' Bonus',
                    'Mustafa Erdogan':shop['owner'],'Darmstädter Str. 81':shop['address'],
                    '65474 Bischofsheim':shop['postcode']+' '+shop['city'],
                    '06144 2079485':shop['phone'],'mustafaerdugulu@outlook.de':shop['email']}
        for node in list(soup.find_all(string=True)):
            if node.parent and node.parent.name not in {'script','style'}:
                text=str(node)
                for old,new in identities.items():
                    if old != new: text=text.replace(old,new)
                if text!=str(node):node.replace_with(text)
        # Remove unavailable navigation, rather than merely failing after a click.
        for anchor in soup.find_all('a',href=True):
            path = urlsplit(anchor['href']).path
            try:
                target,_ = app.url_map.bind('').match(path,method='GET')
            except Exception:
                continue
            if (target in BONUS_ENDPOINTS and not enabled(cfg(),'bonus')) or (target in ORDER_ENDPOINTS and not enabled(cfg(),'ordering')):
                anchor.decompose()
        response.set_data(str(soup))
        return response

    @app.errorhandler(500)
    def internal_error(_error):
        return error('Ein interner Fehler ist aufgetreten. Bitte den Laden kontaktieren.',500)

    def home():
        return page('home','Willkommen',customer_code=session.get('kunde_id'))

    def customer_login():
        if request.method=='GET' and session.get('kunde_id'):
            return redirect(url_for('kunde',kunden_id=session['kunde_id']))
        message, status, identifier = '',200,''
        if request.method=='POST':
            identifier=request.form.get('kennung','').strip()[:120]
            password=request.form.get('passwort','')
            if not access.rate_allowed('customer_ip') or not access.rate_allowed('customer',identifier):
                message,status='Zu viele Anmeldeversuche. Bitte in 15 Minuten erneut versuchen.',429
            else:
                with transaction(connect) as cur:
                    cur.execute("SELECT kunden_id,passwort FROM kunden WHERE kunden_id=%s OR telefon=%s OR LOWER(vorname || ' ' || nachname)=LOWER(%s) ORDER BY id LIMIT 2",(identifier.upper(),identifier,identifier))
                    rows=cur.fetchall()
                # An ambiguous name/phone must not select an arbitrary account.
                if len(rows)==1 and rows[0][1] and len(password)<=256 and check_password_hash(rows[0][1],password):
                    access.end();session.clear();session.permanent=True;session['kunde_id']=rows[0][0]
                    return redirect(url_for('kunde',kunden_id=rows[0][0]))
                message='Kundenangaben oder Passwort sind falsch. Bitte bei gleichem Namen die Kunden-ID verwenden.'
                status=401
        return page('login','Willkommen zurück.',identifier=identifier,message=message),status

    def register():
        if request.method=='GET':
            return page('register','Bonuskarte erstellen')
        if not access.rate_allowed('registration'):
            return page('register','Bonuskarte erstellen',message='Zu viele Versuche. Bitte später erneut versuchen.'),429
        values={key:request.form.get(key,'').strip() for key in ('vorname','nachname','geburtsdatum','telefon','adresse')}
        password=request.form.get('passwort','')
        try:
            birthday=date.fromisoformat(values['geburtsdatum'])
            if not date(1900,1,1)<=birthday<=date.today(): raise ValueError()
            if not all(1<=len(values[k])<=80 for k in ('vorname','nachname')) or len(values['telefon'])>40 or len(values['adresse'])>300:
                raise ValueError()
            if not 8<=len(password)<=256 or password!=request.form.get('passwort2'):
                return page('register','Bonuskarte erstellen',message='Bitte zwei gleiche Passwörter mit mindestens 8 Zeichen eingeben.'),400
            if request.form.get('datenschutz')!='on':
                return page('register','Bonuskarte erstellen',message='Bitte die Datenschutzhinweise bestätigen.'),400
        except ValueError:
            return page('register','Bonuskarte erstellen',message='Bitte Namen, Kontaktdaten und Geburtsdatum prüfen.'),400
        with transaction(connect) as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(values['vorname'].casefold()+'|'+values['nachname'].casefold()+'|'+values['geburtsdatum'],))
            cur.execute('SELECT 1 FROM kunden WHERE LOWER(vorname)=LOWER(%s) AND LOWER(nachname)=LOWER(%s) AND geburtsdatum=%s',(values['vorname'],values['nachname'],birthday))
            if cur.fetchone():
                return page('register','Bonuskarte erstellen',message='Eine Registrierung mit diesen Angaben ist nicht möglich. Bitte anmelden oder an der Theke nachfragen.'),409
            code='KH-'+secrets.token_hex(5).upper()
            cur.execute('INSERT INTO kunden(kunden_id,vorname,nachname,geburtsdatum,telefon,adresse,passwort,werbeeinwilligung,werbeeinwilligung_am) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP) RETURNING id',
                        (code,values['vorname'],values['nachname'],birthday,values['telefon'],values['adresse'],generate_password_hash(password),request.form.get('angebote')=='on'))
            customer_id=cur.fetchone()[0]
            cur.execute('INSERT INTO kunden_laeden(kunde_id,laden_id,punkte) VALUES (%s,1,0) ON CONFLICT DO NOTHING',(customer_id,))
        access.end();session.clear();session.permanent=True;session['kunde_id']=code
        return redirect(url_for('kunde',kunden_id=code))

    def customer_card(kunden_id):
        with transaction(connect) as cur:
            cur.execute('SELECT id,kunden_id,vorname,nachname,werbeeinwilligung FROM kunden WHERE kunden_id=%s',(kunden_id.strip().upper(),))
            row=cur.fetchone()
            if not row:
                session.pop('kunde_id',None)
                return redirect('/login')
            cur.execute('SELECT titel,nachricht FROM push_nachrichten WHERE aktiv=TRUE ORDER BY erstellt_am DESC,id DESC LIMIT 1')
            offer=cur.fetchone()
        customer={'id':row[0],'code':row[1],'vorname':row[2],'nachname':row[3],'consent':row[4]}
        return page('card','Deine Bonuskarte',customer=customer,balance=legacy['get_punktestand'](row[0]),
                    qr=legacy['make_qr_code'](url_for('kunde',kunden_id=row[1],_external=True)),offer=offer)

    @app.route('/kunde/einstellungen',methods=['POST'])
    def customer_settings():
        with transaction(connect) as cur:
            cur.execute('UPDATE kunden SET werbeeinwilligung=%s,werbeeinwilligung_am=CURRENT_TIMESTAMP WHERE kunden_id=%s', (request.form.get('angebote')=='on',session['kunde_id']))
        return redirect(url_for('kunde',kunden_id=session['kunde_id']))

    @app.route('/logout',methods=['GET','POST'])
    def customer_logout():
        if request.method=='POST':
            access.end();session.clear();return redirect('/')
        return page('logout','Abmelden')

    def staff_login(role):
        if request.method=='GET' and access.role(role):
            return redirect('/chef-dashboard' if role=='chef' else '/theke')
        message,status='',200
        next_url=safe_next(request.values.get('next','/theke'))
        if request.method=='POST':
            pin=request.form.get('pin','')
            if not access.rate_allowed(role):
                message,status='Zu viele Anmeldeversuche. Bitte in 15 Minuten erneut versuchen.',429
            else:
                employee=access.employee_for_pin(pin) if role=='mitarbeiter' else None
                valid=bool(employee) if role=='mitarbeiter' else access.verify_chef_pin(pin) and access.verify_second_factor(request.form.get('otp',''))
                if valid:
                    access.begin(role,employee)
                    if employee:
                        with transaction(connect) as cur:
                            cur.execute('UPDATE mitarbeiter SET letzter_login=CURRENT_TIMESTAMP WHERE id=%s',(employee[0],))
                    return redirect('/chef-dashboard' if role=='chef' else next_url)
                message,status='Anmeldung fehlgeschlagen. Bitte Zugangsdaten prüfen.',401
        with transaction(connect) as cur:
            second=bool(setting(cur,'chef_totp_secret')) if role=='chef' else False
        return page('chef_login' if role=='chef' else 'staff_login','Chef-Anmeldung' if role=='chef' else 'Mitarbeiter-Anmeldung',message=message,second_factor=second,next_url=next_url),status

    def staff_logout(role):
        if request.method=='POST':
            access.end();return redirect('/chef-login' if role=='chef' else '/mitarbeiter-login')
        return page('logout','Personal abmelden')

    def push_subscribe():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('keys'),dict):
            return error('Ungültige Push-Daten.')
        endpoint=data.get('endpoint',''); keys=data['keys']
        host=urlsplit(endpoint).hostname if isinstance(endpoint,str) else None
        # Persist only known browser push service endpoints; prevents arbitrary SSRF targets.
        allowed=host and (host=='fcm.googleapis.com' or host=='updates.push.services.mozilla.com' or host.endswith('.push.services.mozilla.com') or host.endswith('.notify.windows.com') or host.endswith('.push.apple.com'))
        if not allowed or not endpoint.startswith('https://') or len(endpoint)>3000 or any(not isinstance(keys.get(k),str) or not 1<=len(keys[k])<=300 for k in ('p256dh','auth')):
            return error('Ungültiger Push-Dienst oder Schlüssel.')
        with transaction(connect) as cur:
            cur.execute('SELECT id FROM kunden WHERE kunden_id=%s',(session['kunde_id'],))
            row=cur.fetchone()
            if not row: return error('Kunde nicht gefunden.',401)
            cur.execute('INSERT INTO push_subscriptions(kunde_id,endpoint,p256dh,auth) VALUES (%s,%s,%s,%s) ON CONFLICT(kunde_id) DO UPDATE SET endpoint=EXCLUDED.endpoint,p256dh=EXCLUDED.p256dh,auth=EXCLUDED.auth,erstellt_am=CURRENT_TIMESTAMP', (row[0],endpoint,keys['p256dh'],keys['auth']))
        return jsonify(success=True,message='Push-Benachrichtigungen wurden aktiviert.')

    @app.route('/chef-laden',methods=['GET','POST'])
    def chef_shop():
        message,success='',False
        if request.method=='POST':
            updated=copy.deepcopy(cfg())
            try:
                for key in ('name','city','owner','address','postcode','phone','email','logo'):
                    value=request.form.get(key,'').strip()
                    if len(value)>200 or any(ord(c)<32 for c in value): raise ValueError('Bitte gültige Profildaten eintragen.')
                    if key in {'name','city','owner','address','postcode','email'} and not value: raise ValueError('Bitte alle Pflichtfelder ausfüllen.')
                    updated[key]=value
                if updated['logo'] and not (updated['logo'].startswith('/static/') or updated['logo'].startswith('https://')): raise ValueError('Logo muss ein /static/-Pfad oder eine HTTPS-Adresse sein.')
                updated['modules']=request.form.get('modules','')
                if updated['modules'] not in ('bonus','ordering','both'): raise ValueError('Bitte ein gültiges Paket wählen.')
                updated['hours']={}
                for day in range(7):
                    start,end=(request.form.get(prefix+'_'+str(day),'') for prefix in ('open','close'))
                    if not start and not end: updated['hours'][str(day)]=None; continue
                    if not re.fullmatch(r'\d{2}:\d{2}',start) or not re.fullmatch(r'\d{2}:\d{2}',end) or time.fromisoformat(start)>=time.fromisoformat(end): raise ValueError('Bitte gültige Öffnungs- und Schlusszeiten am selben Tag eintragen.')
                    updated['hours'][str(day)]=[start,end]
                updated['delivery']={}
                lines=request.form.get('delivery','').splitlines()
                if len(lines)>100: raise ValueError('Höchstens 100 Lieferorte sind möglich.')
                for line in lines:
                    if not line.strip(): continue
                    city,value=line.rsplit('=',1);city=city.strip()
                    amount=cents(value.strip().replace(',','.'))
                    if not city or len(city)>80 or city in updated['delivery'] or not 0<=amount<=250000: raise ValueError('Lieferort oder Mindestwert ist ungültig.')
                    updated['delivery'][city]=amount/100
                with transaction(connect) as cur:
                    save_setting(cur,'shop_config',json.dumps(updated,ensure_ascii=False))
                    access.audit(cur,'SHOP_CONFIG_CHANGED')
                g.pop('shop_config',None);message,success='Ladeneinstellungen wurden gespeichert.',True
            except (ValueError,TypeError):
                message='Bitte Profildaten, Paket, Zeiten und Lieferorte im Format Ort=25.00 prüfen.'
        return page('shop_settings','Laden & Module',area='Chef',back='/chef-einstellungen',wide=True,message=message,success=success)

    @app.route('/chef-speisekarte',methods=['GET','POST'])
    def chef_menu():
        menu=copy.deepcopy(catalog(connect));message,success='',False
        if request.method=='POST':
            try:
                for product in menu['products']:
                    for index,variant in enumerate(product['variants']):
                        amount=cents(request.form.get('value_'+str(product['id'])+'_'+str(index),'').replace(',','.'))
                        if not 0<amount<=100000: raise ValueError()
                        variant['price']=amount/100
                with transaction(connect) as cur:
                    save_setting(cur,'menu_catalog',json.dumps(menu,ensure_ascii=False));access.audit(cur,'MENU_PRICES_CHANGED')
                g.pop('menu_catalog',None);message,success='Alle Preise wurden zentral gespeichert.',True
            except ValueError:
                message='Bitte gültige Preise zwischen 0,01 und 1.000 Euro eintragen.'
        return page('catalog','Speisekarte & Preise',area='Chef',back='/chef-einstellungen',wide=True,products=menu['products'],message=message,success=success)

    def rewards_admin():
        message,success='',False
        reward_menu=catalog(connect,True)
        with transaction(connect) as cur:
            cur.execute('SELECT produkt_id,varianten,aktiv FROM bonus_katalog ORDER BY produkt_id')
            rows=cur.fetchall()
        menu_products={p['id']:p for p in catalog(connect)['products']}
        from praemien_katalog import PRAEMIEN_KATALOG
        products=[{'id':pid,'name':menu_products[pid]['name'],
                   'active':active,'variants':[{'name':v['name'],'points':variants[v['name']]} for v in menu_products[pid]['variants'] if v['name'] in variants]}
                  for pid,variants,active in rows if pid in menu_products]
        if request.method=='POST':
            try:
                updates=[]
                for p in products:
                    variants={v['name']:int(request.form.get('value_'+str(p['id'])+'_'+str(index),'')) for index,v in enumerate(p['variants'])}
                    if any(not 1<=n<=100000 for n in variants.values()): raise ValueError()
                    updates.append((json.dumps(variants),request.form.get('active_'+str(p['id']))=='on',p['id']))
                with transaction(connect) as cur:
                    for row in updates: cur.execute('UPDATE bonus_katalog SET varianten=%s::jsonb,aktiv=%s WHERE produkt_id=%s',row)
                    access.audit(cur,'REWARD_CATALOG_CHANGED')
                return redirect('/chef-praemien?gespeichert=1')
            except ValueError:
                message='Bitte ganze Punktewerte von 1 bis 100.000 eingeben.'
        if request.args.get('gespeichert'): message,success='Prämienpunkte wurden gespeichert.',True
        return page('rewards','Prämien & Punkte',area='Chef',back='/chef-einstellungen',wide=True,products=products,message=message,success=success)

    def staff_admin(mitarbeiter_id=None,new=False):
        message=''
        employee={'id':mitarbeiter_id,'name':'','active':True}
        if mitarbeiter_id:
            with transaction(connect) as cur:
                cur.execute('SELECT id,name,aktiv FROM mitarbeiter WHERE id=%s',(mitarbeiter_id,));row=cur.fetchone()
            if not row: abort(404)
            employee={'id':row[0],'name':row[1],'active':row[2]}
        if request.method=='POST':
            name=request.form.get('name','').strip();pin=request.form.get('pin','');active=request.form.get('aktiv')=='on'
            if not 1<=len(name)<=80 or ((pin or new) and not re.fullmatch(r'\d{6,30}',pin)):
                message='Bitte einen Namen und eine neue PIN mit mindestens 6 Ziffern eingeben.'
            else:
                with transaction(connect) as cur:
                    # Prevent races when creating/changing duplicate PINs.
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext('personal_pin_changes'))")
                    if pin and access.pin_used(cur,pin,mitarbeiter_id):
                        message='Diese PIN wird bereits verwendet. Bitte eine andere wählen.'
                    else:
                        if new:
                            cur.execute('INSERT INTO mitarbeiter(name,pin,aktiv) VALUES (%s,%s,%s) RETURNING id',(name,generate_password_hash(pin),active));mitarbeiter_id=cur.fetchone()[0]
                        elif pin:
                            cur.execute('UPDATE mitarbeiter SET name=%s,pin=%s,aktiv=%s WHERE id=%s',(name,generate_password_hash(pin),active,mitarbeiter_id))
                        else:
                            cur.execute('UPDATE mitarbeiter SET name=%s,aktiv=%s WHERE id=%s',(name,active,mitarbeiter_id))
                        if pin or not active:
                            cur.execute('UPDATE personal_sitzungen SET widerrufen=TRUE WHERE mitarbeiter_id=%s',(mitarbeiter_id,))
                        access.audit(cur,'EMPLOYEE_CHANGED',mitarbeiter_id)
                if not message: return redirect('/chef-mitarbeiter')
        return page('staff_edit','Mitarbeiter hinzufügen' if new else 'Mitarbeiter bearbeiten',employee=employee,message=message,area='Chef',back='/chef-mitarbeiter')

    def staff_list():
        with transaction(connect) as cur:
            cur.execute('SELECT id,name,aktiv FROM mitarbeiter ORDER BY name,id');rows=cur.fetchall()
        return page('staff_list','Dein Team',employees=rows,area='Chef',back='/chef-einstellungen',wide=True)

    def chef_pin():
        message,success='',False
        if request.method=='POST':
            pin=request.form.get('chef_pin','')
            if not access.rate_allowed('chef_pin_change') or not access.verify_chef_pin(request.form.get('current_pin','')):
                message='Bisherige Chef-PIN ist falsch oder zu viele Versuche.'
            elif not re.fullmatch(r'\d{6,30}',pin) or pin!=request.form.get('repeat_pin'):
                message='Bitte zwei gleiche neue PINs mit mindestens 6 Ziffern eingeben.'
            else:
                with transaction(connect) as cur:
                    save_setting(cur,'chef_pin',generate_password_hash(pin));access.audit(cur,'CHEF_PIN_CHANGED')
                    cur.execute("UPDATE personal_sitzungen SET widerrufen=TRUE WHERE rolle='chef'")
                access.begin('chef');message,success='Chef-PIN geändert. Andere Chef-Sitzungen wurden abgemeldet.',True
        return page('chef_pin','Chef-Zugang sichern',area='Chef',back='/chef-einstellungen',message=message,success=success)

    @app.route('/chef-sicherheit',methods=['GET','POST'])
    def chef_security():
        message='';recovery=[];secret='';qr=''
        with transaction(connect) as cur: second=bool(setting(cur,'chef_totp_secret'))
        if request.method=='POST':
            if not access.rate_allowed('chef_2fa_change') or not access.verify_chef_pin(request.form.get('current_pin','')):
                message='Chef-PIN ist falsch oder zu viele Versuche.'
            else:
                action=request.form.get('action')
                if action=='start' and not second:
                    secret=pyotp.random_base32();session['pending_totp']=access.cipher().encrypt(secret.encode()).decode();session['pending_totp_at']=datetime.now().timestamp()
                elif action=='confirm' and not second:
                    pending=session.get('pending_totp','')
                    if not pending or datetime.now().timestamp()-session.get('pending_totp_at',0)>600:
                        message='Einrichtung abgelaufen. Bitte neu beginnen.'
                    else:
                        secret=access.cipher().decrypt(pending.encode()).decode()
                        if pyotp.TOTP(secret).verify(request.form.get('otp','').strip(),valid_window=0):
                            recovery=[secrets.token_hex(6) for _ in range(8)]
                            with transaction(connect) as cur:
                                save_setting(cur,'chef_totp_secret',pending)
                                save_setting(cur,'chef_totp_last_step',int(datetime.now().timestamp())//30)
                                save_setting(cur,'chef_recovery_codes',json.dumps([generate_password_hash(c) for c in recovery]))
                                access.audit(cur,'CHEF_2FA_ENABLED')
                            session.pop('pending_totp',None);access.begin('chef');second=True
                        else: message='Authenticator-Code ist nicht korrekt.'
                elif action=='disable' and second:
                    if access.verify_second_factor(request.form.get('otp','')):
                        with transaction(connect) as cur:
                            for key in ('chef_totp_secret','chef_recovery_codes','chef_totp_last_step'): cur.execute('DELETE FROM einstellungen WHERE schluessel=%s',(key,))
                            access.audit(cur,'CHEF_2FA_DISABLED')
                        access.begin('chef');second=False
                    else: message='Zweiter Faktor ist nicht korrekt.'
        if not second and session.get('pending_totp'):
            secret=access.cipher().decrypt(session['pending_totp'].encode()).decode()
            qr=legacy['make_qr_code'](pyotp.TOTP(secret).provisioning_uri(name='Chef',issuer_name=cfg()['name']))
        return page('two_factor','Zwei-Faktor-Anmeldung',area='Chef',back='/chef-pin-verwaltung',message=message,
                    second_factor=second,recovery_codes=recovery,secret=secret,provisioning_qr=qr)

    def valid_terminal():
        token=session.get('terminal_token')
        if not token: return False
        with transaction(connect) as cur:
            cur.execute('SELECT station FROM terminal_zugaenge WHERE token_hash=%s AND aktiv=TRUE AND ablauf_am>NOW()',(digest(token),))
            row=cur.fetchone()
        return bool(row and row[0]==session.get('terminal_station'))

    @app.route('/chef-terminals',methods=['GET','POST'])
    def chef_terminals():
        code='';message=''
        if request.method=='POST':
            station=request.form.get('station')
            if station not in ('TERMINAL_1','TERMINAL_2'): return error('Ungültige Station.')
            with transaction(connect) as cur:
                if request.form.get('action')=='revoke':
                    cur.execute('UPDATE terminal_zugaenge SET aktiv=FALSE WHERE station=%s',(station,));message='Terminalzugänge wurden gesperrt.'
                else:
                    code=secrets.token_hex(6).upper()
                    cur.execute("INSERT INTO terminal_aktivierungen(code_hash,station,ablauf_am) VALUES (%s,%s,NOW()+INTERVAL '10 minutes')",(digest(code),station))
                    message='Aktivierungscode für '+station+': '+code+'. Auf dem Bestellgerät /terminal-aktivieren öffnen. Gültig: 10 Minuten, einmalig.'
                access.audit(cur,'TERMINAL_ACCESS_CHANGED',station)
        return render_template('terminals.html',shop=cfg(),message=message)

    @app.route('/terminal-aktivieren',methods=['GET','POST'])
    def terminal_activation():
        message=''
        if request.method=='POST':
            if not access.rate_allowed('terminal_pairing'): return error('Zu viele Versuche.',429)
            code=request.form.get('code','').strip().upper().replace(' ','')
            with transaction(connect) as cur:
                cur.execute('DELETE FROM terminal_aktivierungen WHERE code_hash=%s AND ablauf_am>NOW() RETURNING station',(digest(code),));row=cur.fetchone()
                if row:
                    raw=secrets.token_urlsafe(40)
                    cur.execute("INSERT INTO terminal_zugaenge(token_hash,station,ablauf_am) VALUES (%s,%s,NOW()+INTERVAL '365 days')",(digest(raw),row[0]))
                    session['terminal_token']=raw;session['terminal_station']=row[0];session.permanent=True
                    return redirect('/bestellterminal/'+row[0][-1])
            message='Aktivierungscode ist ungültig oder abgelaufen.'
        return render_template('terminals.html',shop=cfg(),activation=True,message=message)

    @app.route('/health')
    def health():
        with transaction(connect) as cur: cur.execute('SELECT 1')
        return jsonify(ok=True,version='readiness-v2')

    @app.route('/static/manifest.webmanifest')
    def shop_manifest():
        shop=cfg()
        response=jsonify(name=shop['name']+' Bonus',short_name=shop['name'],start_url='/',scope='/',
                         display='standalone',background_color='#0d0d0d',theme_color='#0d0d0d',
                         icons=[{'src':'/static/icons/icon-192.png','sizes':'192x192','type':'image/png'},
                                {'src':'/static/icons/icon-512.png','sizes':'512x512','type':'image/png'}])
        response.mimetype='application/manifest+json'
        response.headers['Cache-Control']='no-store'
        return response

    # Replace legacy implementations, preserving all established URLs.
    replacements={'startseite':home,'login':customer_login,'register':register,'kunde':customer_card,
                  'push_subscribe':push_subscribe,'mitarbeiter_login':lambda:staff_login('mitarbeiter'),
                  'chef_login':lambda:staff_login('chef'),'mitarbeiter_logout':lambda:staff_logout('mitarbeiter'),
                  'chef_logout':lambda:staff_logout('chef'),'chef_pin_verwaltung':chef_pin,
                  'chef_mitarbeiter':staff_list,'chef_mitarbeiter_neu':lambda:staff_admin(new=True),
                  'chef_mitarbeiter_bearbeiten':lambda mitarbeiter_id:staff_admin(mitarbeiter_id),
                  'chef_praemien':rewards_admin,'kunde_praemien_alt':lambda kunden_id:redirect(url_for('kunde_praemien',kunden_id=kunden_id)),
                  'chef_praemie_neu':lambda:redirect('/chef-praemien'),
                  'chef_praemie_bearbeiten':lambda praemie_id:redirect('/chef-praemien'),
                  'chef_praemie_loeschen':lambda praemie_id:redirect('/chef-praemien')}
    for endpoint,view in replacements.items():
        app.view_functions[endpoint]=view
    # These established routes now render POST forms as well.
    for rule in list(app.url_map.iter_rules()):
        if rule.endpoint in {'chef_praemien','chef_logout','mitarbeiter_logout'}:
            rule.methods.add('POST')
    from order_safety import install_order_safety
    install_order_safety(app,legacy,access,connect,cfg)
