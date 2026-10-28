import copy
import json
import secrets
import uuid
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import psycopg2
import pytest
import pyotp
from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash,check_password_hash
from shop import CATALOG,paid_positions,cents


def sql(env,query,params=(),fetch=False):
    with psycopg2.connect(env['uri']) as conn:
        with conn.cursor() as cur:
            cur.execute(query,params)
            return cur.fetchall() if fetch else None


def token(c):
    c.get('/')
    with c.session_transaction() as s:return s['_csrf']


def post(c,path,data=None,json=None):
    headers={'X-CSRF-Token':token(c)}
    return c.post(path,data=data,json=json,headers=headers)


def login(c,env,role):
    if role=='customer':return post(c,'/login',data={'kennung':'KH-TEST-1','passwort':env['customer_password']})
    return post(c,'/chef-login' if role=='chef' else '/mitarbeiter-login',data={'pin':env[role+'_pin']})


def order(**updates):
    result={'anfrage_id':uuid.uuid4().hex,'items':[{'product':{'id':1},'variant':{'name':'Classic'},'qty':1,'total':12.5,'extras':[],'upgrade':None,'sauces':[],'without':[],'side':None}],
            'bestellquelle':'KUNDEN_APP','bestellart':'ABHOLUNG','station':'','zahlungsart':'BAR'}
    result.update(updates);return result


def test_registration_logs_in_and_persists_365_days(env):
    c=env['app'].test_client();r=post(c,'/register',data={'vorname':'New','nachname':'Synthetic','geburtsdatum':'2000-01-01','passwort':'test-only-strong-password','passwort2':'test-only-strong-password','datenschutz':'on'})
    assert r.status_code==302
    assert c.get(r.location).status_code==200
    with c.session_transaction() as s:assert s['kunde_id'].startswith('KH-') and s.permanent
    days=(c.get_cookie('session').expires-datetime.now(timezone.utc)).total_seconds()/86400
    assert 364.99<days<365.01
    assert '/kunde/' in c.get('/login').location
    c.get('/bestellen')
    with c.session_transaction() as s:assert s.get('kunde_id')


def test_duplicate_registration_does_not_disclose_card(env):
    c=env['app'].test_client();r=post(c,'/register',data={'vorname':'Synthetic','nachname':'Customer','geburtsdatum':'2000-01-01','passwort':'different-password','passwort2':'different-password','datenschutz':'on'})
    assert r.status_code==409 and 'KH-TEST-1' not in r.text
    with c.session_transaction() as s:assert 'kunde_id' not in s


@pytest.mark.parametrize('path',['/kunde/KH-TEST-1','/kunde/KH-TEST-1/praemien','/kunde/KH-TEST-1/praemien-alt'])
def test_customer_ownership_enforced(env,path):
    c=env['app'].test_client();assert c.get(path).location=='/login'
    assert login(c,env,'customer').status_code==302
    assert c.get('/kunde/KH-OTHER').location=='/login'
    assert c.get('/kunde/KH-TEST-1').status_code==200


@pytest.mark.parametrize('role',['anonymous','customer','staff'])
@pytest.mark.parametrize('path',['/chef-dashboard','/chef-kunden','/chef-gutschriften','/chef-einloesungen','/chef-statistiken','/chef-einstellungen','/chef-laden','/chef-speisekarte','/chef-mitarbeiter','/chef-praemien','/chef-pin-verwaltung','/chef-sicherheit','/chef-terminals'])
def test_no_wrong_role_can_enter_chef(env,role,path):
    c=env['app'].test_client()
    if role!='anonymous':login(c,env,role)
    assert c.get(path).status_code==302 and c.get(path).location=='/chef-login'


@pytest.mark.parametrize('path',['/mitarbeiter','/scanner','/mitarbeiter/KH-TEST-1','/mitarbeiter-bestellung','/theke'])
def test_customer_cannot_enter_staff(env,path):
    c=env['app'].test_client();login(c,env,'customer')
    assert c.get(path).status_code==302
    with c.session_transaction() as s:assert not s.get('chef_angemeldet') and not s.get('mitarbeiter_angemeldet')


def test_legacy_flags_do_not_grant_access(env):
    c=env['app'].test_client()
    with c.session_transaction() as s:s['chef_angemeldet']=True;s['mitarbeiter_angemeldet']=True;s['mitarbeiter_id']=env['staff_id']
    assert c.get('/chef-dashboard').location=='/chef-login'
    assert c.get('/mitarbeiter').status_code==302


def test_staff_revoked_when_deactivated(env):
    c=env['app'].test_client();assert login(c,env,'staff').status_code==302
    assert c.get('/theke').status_code==200
    sql(env,'UPDATE mitarbeiter SET aktiv=FALSE WHERE id=%s',(env['staff_id'],))
    assert c.get('/theke').status_code==302


def test_staff_pin_change_revokes_existing_cookie(env):
    c=env['app'].test_client();login(c,env,'staff');assert c.get('/mitarbeiter').status_code==200
    sql(env,'UPDATE mitarbeiter SET pin=%s WHERE id=%s',(generate_password_hash('only-test-new-pin'),env['staff_id']))
    assert c.get('/mitarbeiter').status_code==302


def test_staff_session_expires_without_changing_customer_365_days(env):
    c=env['app'].test_client();login(c,env,'staff')
    sql(env,"UPDATE personal_sitzungen SET ablauf_am=NOW()-INTERVAL '1 second'")
    assert c.get('/theke').status_code==302
    assert env['app'].permanent_session_lifetime==timedelta(days=365)


def test_pin_hashes_and_no_pin_on_management_screen(env):
    c=env['app'].test_client();login(c,env,'chef')
    text=c.get('/chef-mitarbeiter').text+c.get('/chef-pin-verwaltung').text
    assert env['staff_pin'] not in text and env['chef_pin'] not in text
    assert sql(env,'SELECT pin FROM mitarbeiter',fetch=True)[0][0].startswith(('scrypt:','pbkdf2:'))


def test_csrf_is_required_even_for_chef(env):
    c=env['app'].test_client();login(c,env,'chef')
    assert c.post('/chef-punkte-regel',data={'punkte_pro_euro':'3'}).status_code==403
    assert post(c,'/chef-punkte-regel',data={'punkte_pro_euro':'3'}).status_code==200


def test_csrf_order_and_origin_response_properties(env):
    c=env['app'].test_client();r=c.get('/bestellen')
    assert r.headers['Cache-Control']=='no-store, private'
    assert r.headers['X-Frame-Options']=='DENY'
    assert c.post('/api/bestellung',json=order()).status_code==403


def test_login_rate_limit(env):
    c=env['app'].test_client()
    for _ in range(15):assert post(c,'/chef-login',data={'pin':'bad-test-only'}).status_code==401
    assert post(c,'/chef-login',data={'pin':env['chef_pin']}).status_code==429


def test_staff_external_next_is_rejected(env):
    c=env['app'].test_client()
    r=post(c,'/mitarbeiter-login',data={'pin':env['staff_pin'],'next':'https://example.invalid'})
    assert r.status_code==302 and r.location=='/theke'


def test_no_role_accumulation(env):
    c=env['app'].test_client();login(c,env,'chef');login(c,env,'customer')
    with c.session_transaction() as s:assert s.get('kunde_id') and not s.get('personal_token')
    assert c.get('/chef-dashboard').location=='/chef-login'


def test_stable_session_key_survives_repeat_migration(env):
    before=env['app'].secret_key;env['module'].init_db()
    assert sql(env,"SELECT wert FROM einstellungen WHERE schluessel='_session_secret'",fetch=True)[0][0]==before


def test_wrong_shop_cannot_reuse_database(env,monkeypatch):
    monkeypatch.setenv('SHOP_KEY','different-synthetic-shop')
    with pytest.raises(RuntimeError,match='anderen Laden'):env['module'].init_db()


def test_one_cent_attack_rejected(env):
    c=env['app'].test_client();data=order();data['items'][0]['total']=.01
    r=post(c,'/api/bestellung',json=data)
    assert r.status_code==400 and sql(env,'SELECT count(*) FROM bestellungen',fetch=True)[0][0]==0


def test_authoritative_price_is_stored_and_client_name_ignored(env):
    c=env['app'].test_client();data=order();data['items'][0]['product']['name']='Client fake product';data['items'][0]['variant']['price']=.01
    r=post(c,'/api/bestellung',json=data);assert r.status_code==200 and r.json['ok']
    row=sql(env,'SELECT produkt_name,einzelpreis,gesamtpreis FROM bestellpositionen',fetch=True)[0]
    assert row[0]=='Döner Sandwich Steak' and float(row[1])==12.5 and float(row[2])==12.5


@pytest.mark.parametrize('change',[
    {'product':{'id':999999}}, {'variant':{'name':'FAKE'}},{'qty':0},{'qty':21},{'qty':True},{'qty':1.5},
    {'extras':[{'name':'FAKE','price':0}]},{'extras':[{'name':'Steak'},{'name':'Steak'}]},
    {'upgrade':{'name':'FREE MENU'}},{'sauces':['Knoblauch','Chili','Cocktail']},{'sauces':['Knoblauch','Knoblauch']},
    {'without':['FAKE']},{'side':'FAKE'},{'total':float('nan')},{'total':-1},{'total':0},
    {'product':'invalid'},{'variant':[]},{'note':'a'*301},{'note':'\u001bprinter-injection'}])
def test_invalid_positions_are_rejected(env,change):
    c=env['app'].test_client();data=order();data['items'][0].update(change)
    assert post(c,'/api/bestellung',json=data).status_code==400
    assert sql(env,'SELECT count(*) FROM bestellungen',fetch=True)[0][0]==0


@pytest.mark.parametrize('product',CATALOG['products'],ids=lambda p:str(p['id']))
def test_all_102_products_and_variants_can_be_priced(product):
    for variant in product['variants']:
        item={'product':{'id':product['id']},'variant':{'name':variant['name']},'qty':1}
        if product['id'] in {22,23,24,101}:item['side']='Pommes'
        positions,total=paid_positions([item])
        assert total==cents(variant['price']) and positions[0]['product']['name']==product['name']


def test_price_extras_pide_xxl_menu_and_quantity():
    p=next(p for p in CATALOG['products'] if p['cat']=='pide')
    item={'product':{'id':p['id']},'variant':{'name':'XXL'},'qty':2,'extras':[{'name':'Extra Ei','price':0}],'upgrade':{'name':'Menü XL','price':0}}
    _,amount=paid_positions([item]);base=next(v['price'] for v in p['variants'] if v['name']=='XXL')
    assert amount==2*(cents(base)+300+550)


def test_order_retry_is_idempotent(env):
    c=env['app'].test_client();data=order();a=post(c,'/api/bestellung',json=data);b=post(c,'/api/bestellung',json=data)
    assert a.status_code==b.status_code==200 and a.json['token']==b.json['token']
    assert sql(env,'SELECT count(*) FROM bestellungen',fetch=True)[0][0]==1
    assert sql(env,'SELECT count(*) FROM druckauftraege',fetch=True)[0][0]==1
    data['kundenname']='Changed body'
    assert post(c,'/api/bestellung',json=data).status_code==409


def test_persistent_cancel_and_no_payment_after_cancel(env):
    c=env['app'].test_client();r=post(c,'/api/bestellung',json=order());order_token=r.json['token']
    login(c,env,'staff')
    assert post(c,'/api/bestellung/'+order_token+'/stornieren').status_code==200
    assert post(c,'/api/bestellung/'+order_token+'/bezahlt').status_code==409
    assert sql(env,'SELECT status FROM bestellungen',fetch=True)[0][0]=='STORNIERT'
    assert sql(env,'SELECT status FROM druckauftraege',fetch=True)[0][0]=='STORNIERT'
    assert any(o['status']=='STORNIERT' for o in c.get('/api/theke/bestellungen').json['bestellungen'])


def test_cash_points_respect_rule_and_are_credited_once(env):
    sql(env,"UPDATE einstellungen SET wert='3' WHERE schluessel='punkte_pro_euro'")
    c=env['app'].test_client();login(c,env,'customer');r=post(c,'/api/bestellung',json=order());t=r.json['token'];login(c,env,'staff')
    first=post(c,'/api/bestellung/'+t+'/bezahlt');second=post(c,'/api/bestellung/'+t+'/bezahlt')
    assert first.json['punkte']==37 and second.json['bereits_bezahlt']
    assert sql(env,'SELECT SUM(punkte) FROM punkte_bewegungen',fetch=True)[0][0]==37


def test_manual_credit_and_redeem_are_persistent_and_repeat_safe(env):
    sql(env,"UPDATE einstellungen SET wert='2' WHERE schluessel='punkte_pro_euro'")
    c=env['app'].test_client();login(c,env,'staff')
    action=secrets.token_hex(16)
    data={'betrag':'12,50','_action_id':action}
    assert post(c,'/mitarbeiter/KH-TEST-1',data=data).status_code==200
    assert post(c,'/mitarbeiter/KH-TEST-1',data=data).status_code==302
    assert sql(env,'SELECT SUM(punkte) FROM punkte_bewegungen',fetch=True)[0][0]==25
    data={'punkte_einloesen':'20','_action_id':secrets.token_hex(16)}
    assert post(c,'/mitarbeiter/KH-TEST-1/einloesen',data=data).status_code==200
    assert post(c,'/mitarbeiter/KH-TEST-1/einloesen',data=data).status_code==302
    assert sql(env,'SELECT SUM(punkte) FROM punkte_bewegungen',fetch=True)[0][0]==5


@pytest.mark.parametrize('day,start',[(26,11),(28,11),(29,11),(30,11),(31,12)])
def test_hours_boundaries(env,day,start):
    tz=ZoneInfo('Europe/Berlin')
    with env['app'].test_request_context('/'):
        f=env['status'];assert not f(datetime(2026,10,day,start-1,59,tzinfo=tz))['offen']
        assert f(datetime(2026,10,day,start,0,tzinfo=tz))['offen']
        assert not f(datetime(2026,10,day,21,30,tzinfo=tz))['offen']


def test_tuesday_and_sunday(env):
    with env['app'].test_request_context('/'):
        f=env['status'];tz=ZoneInfo('Europe/Berlin')
        assert f(datetime(2026,10,27,14,tzinfo=tz))['ruhetag']
        assert not f(datetime(2026,11,1,11,59,tzinfo=tz))['offen']
        assert f(datetime(2026,11,1,12,tzinfo=tz))['offen']


def test_reward_order_reservations_prevent_overcommit(env):
    sql(env,"INSERT INTO punkte_bewegungen(kunde_id,typ,punkte) VALUES (%s,'GUTSCHRIFT',1100)",(env['customer_id'],))
    c=env['app'].test_client();login(c,env,'customer');data=order();data['items'][0].pop('total')
    a=post(c,'/api/praemien-bestellung',json=data)
    assert a.status_code==200 and a.json['punkte']==1000
    retry=post(c,'/api/praemien-bestellung',json=data);assert retry.status_code==200 and retry.json['token']==a.json['token']
    data['anfrage_id']=uuid.uuid4().hex
    assert post(c,'/api/praemien-bestellung',json=data).status_code==409


def test_reward_hours_gate_is_not_bypassed(env,monkeypatch):
    from shop import DEFAULT
    shop=copy.deepcopy(DEFAULT);shop['hours']={str(d):None for d in range(7)}
    sql(env,"INSERT INTO einstellungen VALUES ('shop_config',%s)",(json.dumps(shop),))
    monkeypatch.setattr(env['module'],'aktueller_bestellstatus',env['status'])
    c=env['app'].test_client();login(c,env,'customer')
    assert post(c,'/api/praemien-bestellung',json=order()).status_code==403


@pytest.mark.parametrize('modules,paid_status,bonus_status',[('bonus',404,200),('ordering',200,404),('both',200,200)])
def test_modules_enforced_at_ui_and_backend(env,modules,paid_status,bonus_status):
    from shop import DEFAULT
    shop=copy.deepcopy(DEFAULT);shop['modules']=modules
    sql(env,"INSERT INTO einstellungen VALUES ('shop_config',%s)",(json.dumps(shop),))
    c=env['app'].test_client();assert c.get('/bestellen').status_code==paid_status
    assert c.get('/login').status_code==bonus_status
    home=c.get('/').text
    assert ('Jetzt bestellen' in home)==(modules!='bonus')
    assert ('Meine Bonuskarte' in home)==(modules!='ordering')
    if modules=='bonus':assert post(c,'/api/bestellung',json=order()).status_code==404
    if modules=='ordering':assert post(c,'/api/praemien-bestellung',json=order()).status_code==404


def test_guest_terminal_spoofing_is_blocked(env):
    c=env['app'].test_client();assert c.get('/bestellterminal/1').location=='/terminal-aktivieren'
    data=order(bestellquelle='BESTELLMONITOR_1',station='TERMINAL_1',bestellart='VOR_ORT',zahlungsart='SUMUP')
    assert post(c,'/api/bestellung',json=data).status_code==401
    assert post(c,'/api/bestellung/unknown/sumup/start').status_code==401


def test_chef_totp_setup_and_login(env,monkeypatch):
    c=env['app'].test_client();login(c,env,'chef');access=env['app'].extensions['access']
    r=post(c,'/chef-sicherheit',data={'action':'start','current_pin':env['chef_pin']});assert r.status_code==200
    with c.session_transaction() as s:secret=access.cipher().decrypt(s['pending_totp'].encode()).decode()
    r=post(c,'/chef-sicherheit',data={'action':'confirm','current_pin':env['chef_pin'],'otp':pyotp.TOTP(secret).now()})
    assert r.status_code==200 and 'Wiederherstellungscodes' in r.text
    fresh=env['app'].test_client()
    assert post(fresh,'/chef-login',data={'pin':env['chef_pin']}).status_code==401
    # The activation step itself consumed the current OTP; a recovery code is
    # used to test a second device without sleeping or reusing a code.
    recovery=BeautifulSoup(r.text,'html.parser').select_one('.code-list code').text
    assert post(fresh,'/chef-login',data={'pin':env['chef_pin'],'otp':recovery}).status_code==302
    another=env['app'].test_client()
    assert post(another,'/chef-login',data={'pin':env['chef_pin'],'otp':recovery}).status_code==401


def test_untrusted_names_are_escaped(env):
    sql(env,'UPDATE kunden SET vorname=%s WHERE id=%s',('<img src=x onerror=alert(1)>',env['customer_id']))
    c=env['app'].test_client();login(c,env,'customer');r=c.get('/kunde/KH-TEST-1')
    assert '<img src=x onerror=' not in r.text and '&lt;img' in r.text
    login(c,env,'chef');r=c.get('/chef-kunden')
    assert '<img src=x onerror=' not in r.text


def test_foreign_push_customer_id_is_not_trusted(env):
    c=env['app'].test_client();login(c,env,'customer')
    r=post(c,'/push-subscribe',json={'kunde_id':999999,'endpoint':'https://fcm.googleapis.com/fcm/send/synthetic-only','keys':{'p256dh':'synthetic','auth':'synthetic'}})
    assert r.status_code==200
    assert sql(env,'SELECT kunde_id FROM push_subscriptions',fetch=True)[0][0]==env['customer_id']


def test_push_arbitrary_server_url_is_rejected(env):
    c=env['app'].test_client();login(c,env,'customer')
    assert post(c,'/push-subscribe',json={'endpoint':'https://127.0.0.1/private','keys':{'p256dh':'x','auth':'x'}}).status_code==400


@pytest.mark.parametrize('path',['/chef-dashboard','/chef-kunden','/chef-gutschriften','/chef-einloesungen','/chef-statistiken','/chef-einstellungen','/chef-laden','/chef-speisekarte','/chef-mitarbeiter','/chef-mitarbeiter/neu','/chef-praemien','/chef-pin-verwaltung','/chef-sicherheit','/chef-terminals','/chef-punkte-regel'])
def test_chef_pages_render_for_authorized_chef(env,path):
    c=env['app'].test_client();login(c,env,'chef');r=c.get(path)
    assert r.status_code==200

