import copy
import json
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import pytest
from shop import CATALOG,DEFAULT,cents
from test_readiness import sql,post,login,order,token


@pytest.mark.parametrize('city,minimum',DEFAULT['delivery'].items())
def test_delivery_minimum_uses_authoritative_total(env,city,minimum):
    c=env['app'].test_client()
    data=order(bestellart='LIEFERUNG',kundenname='Synthetic Only',telefon='000000',lieferort=city,lieferadresse='Teststraße 1, '+city)
    first=post(c,'/api/bestellung',json=data)
    assert first.status_code==400 and 'Es fehlen noch' in first.json['fehler']
    data['items'][0].update(qty=4,total=50)
    assert post(c,'/api/bestellung',json=data).status_code==200


def test_missing_delivery_fields_and_unknown_city_rejected(env):
    c=env['app'].test_client()
    assert post(c,'/api/bestellung',json=order(bestellart='LIEFERUNG')).status_code==400
    assert post(c,'/api/bestellung',json=order(bestellart='LIEFERUNG',kundenname='Synthetic',telefon='000',lieferort='Unknown',lieferadresse='Test')).status_code==400


def test_central_price_edit_changes_browser_and_server(env):
    c=env['app'].test_client();login(c,env,'chef')
    form={'value_'+str(p['id'])+'_'+str(i):str(v['price']) for p in CATALOG['products'] for i,v in enumerate(p['variants'])}
    form['value_1_0']='13.25'
    assert post(c,'/chef-speisekarte',data=form).status_code==200
    data=order();assert post(c,'/api/bestellung',json=data).status_code==400
    data['items'][0]['total']=13.25
    assert post(c,'/api/bestellung',json=data).status_code==200
    assert '13.25' in c.get('/bestellen').text


def test_bonus_catalog_edit_is_shared_by_display_and_redemption(env):
    c=env['app'].test_client();login(c,env,'chef')
    page=c.get('/chef-praemien');soup=BeautifulSoup(page.text,'html.parser')
    form={field['name']:field.get('value','on') for field in soup.select('input[name]') if field.get('type')!='checkbox' or field.has_attr('checked')}
    form['value_1_0']='777'
    assert post(c,'/chef-praemien',data=form).status_code==302
    sql(env,"INSERT INTO punkte_bewegungen(kunde_id,typ,punkte) VALUES (%s,'GUTSCHRIFT',10000)",(env['customer_id'],))
    login(c,env,'customer');data=order();data['items'][0].pop('total')
    r=post(c,'/api/praemien-bestellung',json=data)
    assert r.status_code==200 and r.json['punkte']==777
    assert '7.77' in c.get('/kunde/KH-TEST-1/praemien').text


def test_modern_reward_redemption_is_counted_in_reports(env):
    sql(env,"INSERT INTO punkte_bewegungen(kunde_id,typ,punkte) VALUES (%s,'EINLOESUNG_BESTELLUNG',-777)",(env['customer_id'],))
    c=env['app'].test_client();login(c,env,'chef')
    for path in ('/chef-dashboard','/chef-einloesungen','/chef-statistiken'):
        r=c.get(path);assert r.status_code==200 and '777' in r.text


def test_module_configuration_persists_with_staff_permissions(env):
    c=env['app'].test_client();login(c,env,'chef')
    form={key:str(DEFAULT[key]) for key in ('name','city','owner','address','postcode','phone','email','logo')}
    form.update(modules='ordering',delivery='Teststadt=35.50')
    for day,pair in DEFAULT['hours'].items():
        form['open_'+day]=pair[0] if pair else '';form['close_'+day]=pair[1] if pair else ''
    assert post(c,'/chef-laden',data=form).status_code==200
    assert c.get('/login').status_code==404 and c.get('/bestellen').status_code==200
    assert 'Teststadt' in c.get('/bestellen').text


def test_paid_orders_do_not_credit_points_when_bonus_module_disabled(env):
    c=env['app'].test_client();login(c,env,'customer')
    shop=copy.deepcopy(DEFAULT);shop['modules']='ordering'
    sql(env,"INSERT INTO einstellungen VALUES ('shop_config',%s)",(json.dumps(shop),))
    r=post(c,'/api/bestellung',json=order());login(c,env,'staff')
    paid=post(c,'/api/bestellung/'+r.json['token']+'/bezahlt')
    assert paid.status_code==200 and paid.json['punkte']==0
    assert sql(env,'SELECT count(*) FROM punkte_bewegungen',fetch=True)[0][0]==0


def test_server_logout_requires_post_and_revokes_session(env):
    c=env['app'].test_client();login(c,env,'chef')
    assert c.get('/chef-logout').status_code==200
    assert c.get('/chef-dashboard').status_code==200
    assert post(c,'/chef-logout').status_code==302
    assert c.get('/chef-dashboard').location=='/chef-login'


def test_terminal_pairing_and_revoke(env):
    chef=env['app'].test_client();login(chef,env,'chef')
    r=post(chef,'/chef-terminals',data={'station':'TERMINAL_1','action':'pair'})
    text=BeautifulSoup(r.text,'html.parser').select_one('.notice').text
    code=text.split(': ')[1].split('.')[0]
    device=env['app'].test_client();r=post(device,'/terminal-aktivieren',data={'code':code})
    assert r.status_code==302 and device.get('/bestellterminal/1').status_code==200
    assert device.get('/bestellterminal/2').location=='/terminal-aktivieren'
    assert post(chef,'/chef-terminals',data={'station':'TERMINAL_1','action':'revoke'}).status_code==200
    assert device.get('/bestellterminal/1').location=='/terminal-aktivieren'


@pytest.mark.parametrize('money',[
    None,{'value':1250,'currency':'USD','minor_unit':2},
    {'value':12,'currency':'EUR','minor_unit':0},
    {'value':1,'currency':'EUR','minor_unit':2},
    {'value':True,'currency':'EUR','minor_unit':2}])
def test_sumup_cannot_mark_paid_with_unverified_money(env,monkeypatch,money):
    c=env['app'].test_client();r=post(c,'/api/bestellung',json=order());t=r.json['token']
    sql(env,"UPDATE bestellungen SET zahlungsart='SUMUP',sumup_checkout_id='synthetic-checkout',sumup_status='PENDING',sumup_reader_id='synthetic-reader'")
    login(c,env,'staff');monkeypatch.setattr(env['module'],'sumup_anfrage',lambda *a,**k:{'data':{'status':'successful','checkout_id':'synthetic-checkout','total_amount':money}})
    assert c.get('/api/bestellung/'+t+'/sumup/status').status_code==502
    assert sql(env,'SELECT status FROM bestellungen',fetch=True)[0][0]=='WARTET_AUF_ZAHLUNG'


def test_sumup_correct_confirmed_payment_and_rule(env,monkeypatch):
    c=env['app'].test_client();login(c,env,'customer');r=post(c,'/api/bestellung',json=order());t=r.json['token']
    sql(env,"UPDATE bestellungen SET zahlungsart='SUMUP',sumup_checkout_id='synthetic-checkout',sumup_status='PENDING',sumup_reader_id='synthetic-reader'")
    sql(env,"UPDATE einstellungen SET wert='2' WHERE schluessel='punkte_pro_euro'")
    login(c,env,'staff');monkeypatch.setattr(env['module'],'sumup_anfrage',lambda *a,**k:{'data':{'status':'successful','checkout_id':'synthetic-checkout','total_amount':{'currency':'EUR','minor_unit':2,'value':1250}}})
    r=c.get('/api/bestellung/'+t+'/sumup/status');assert r.status_code==200 and r.json['punkte']==25
    r=c.get('/api/bestellung/'+t+'/sumup/status');assert r.status_code==200
    assert sql(env,'SELECT SUM(punkte) FROM punkte_bewegungen',fetch=True)[0][0]==25


@pytest.mark.skipif(os.environ.get('TEST_PGLITE')=='1',reason='Real simultaneous transactions require native PostgreSQL; covered in CI')
def test_concurrent_same_request_creates_one_order(env):
    c=env['app'].test_client();csrf=token(c);cookie=c.get_cookie('session').value;data=order()
    def run(_):
        clone=env['app'].test_client();clone.set_cookie('session',cookie)
        r=clone.post('/api/bestellung',json=copy.deepcopy(data),headers={'X-CSRF-Token':csrf})
        return r.status_code,r.json
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
    assert all(r[0]==200 for r in results)
    assert len({r[1]['token'] for r in results})==1
    assert sql(env,'SELECT COUNT(*) FROM bestellungen',fetch=True)[0][0]==1


@pytest.mark.skipif(os.environ.get('TEST_PGLITE')=='1',reason='Real simultaneous transactions require native PostgreSQL; covered in CI')
def test_concurrent_reward_reservations_cannot_overdraw(env):
    c=env['app'].test_client();login(c,env,'customer');csrf=token(c);cookie=c.get_cookie('session').value
    sql(env,"INSERT INTO punkte_bewegungen(kunde_id,typ,punkte) VALUES (%s,'GUTSCHRIFT',1500)",(env['customer_id'],))
    def run(_):
        clone=env['app'].test_client();clone.set_cookie('session',cookie);data=order();data['items'][0].pop('total')
        return clone.post('/api/praemien-bestellung',json=data,headers={'X-CSRF-Token':csrf}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sorted(results)==[200,409]
    assert sql(env,'SELECT COUNT(*) FROM bestellungen',fetch=True)[0][0]==1


def test_fresh_schema_complete_and_repeatable(env):
    env['module'].init_db();env['module'].init_db()
    tables={row[0] for row in sql(env,"SELECT tablename FROM pg_tables WHERE schemaname='public'",fetch=True)}
    assert {'kunden','mitarbeiter','einstellungen','bestellungen','druckauftraege','personal_sitzungen','bonus_katalog','terminal_zugaenge'}<=tables
    assert sql(env,'SELECT COUNT(*) FROM kunden',fetch=True)[0][0]==1


def test_provisioning_menu_validator_rejects_unsafe_imports():
    from setup_shop import validate_menu
    assert validate_menu(copy.deepcopy(CATALOG))
    bad=copy.deepcopy(CATALOG);bad['products'][0]['name']='<script>bad</script>'
    with pytest.raises(ValueError):validate_menu(bad)


@pytest.mark.parametrize('path,role',[('/bestellen',None),('/kunde/KH-TEST-1/praemien','customer'),('/theke','staff'),('/scanner','staff'),('/chef-einstellungen','chef')])
def test_rendered_javascript_is_valid(env,path,role):
    import shutil,subprocess
    if not shutil.which('node'):pytest.skip('Node syntax checks run in CI')
    c=env['app'].test_client()
    if role:login(c,env,role)
    response=c.get(path);assert response.status_code==200
    for script in BeautifulSoup(response.text,'html.parser').find_all('script'):
        if script.get('src') or not script.text.strip():continue
        result=subprocess.run(['node','--check'],input=script.text,text=True,capture_output=True,timeout=10)
        assert result.returncode==0,result.stderr


def test_chef_back_navigation_does_not_redirect_to_employee_login(env):
    c=env['app'].test_client();login(c,env,'chef')
    response=c.get('/mitarbeiter-bestellung')
    assert response.status_code==200 and 'window.location.href = "/chef-dashboard"' in response.text


def test_printer_payload_cannot_inject_escpos_controls():
    from drucker_agent import beleg
    receipt=beleg({'kunde':{'name':'Synthetic\x1b\x40Name'},'positionen':[{'produkt':'Test\x1d\x56Product'}]})
    assert '\x1b' not in receipt and '\x1d' not in receipt


def test_upgrade_existing_production_column_limits_preserves_data(env):
    # Real production schema has pin VARCHAR(20) and settings VARCHAR(255).
    # Simulate that structure with ONLY synthetic local test data.
    from werkzeug.security import check_password_hash
    legacy_pin='438729'
    sql(env,'DELETE FROM mitarbeiter')
    sql(env,'ALTER TABLE mitarbeiter ALTER COLUMN pin TYPE VARCHAR(20)')
    sql(env,'ALTER TABLE einstellungen ALTER COLUMN wert TYPE VARCHAR(255)')
    sql(env,'INSERT INTO mitarbeiter(name,pin,aktiv) VALUES (%s,%s,TRUE)',('Legacy Synthetic',legacy_pin))
    env['module'].init_db()
    hashed=sql(env,'SELECT pin FROM mitarbeiter',fetch=True)[0][0]
    assert len(hashed)>20 and check_password_hash(hashed,legacy_pin)
    sql(env,"INSERT INTO einstellungen VALUES ('long-synthetic-config',%s)",('x'*1000,))
    assert sql(env,'SELECT COUNT(*) FROM kunden',fetch=True)[0][0]==1


def test_customer_can_release_only_own_pending_reward_reservation(env):
    c=env['app'].test_client();login(c,env,'customer')
    sql(env,"INSERT INTO punkte_bewegungen(kunde_id,typ,punkte) VALUES (%s,'GUTSCHRIFT',1500)",(env['customer_id'],))
    data=order();data['items'][0].pop('total')
    r=post(c,'/api/praemien-bestellung',json=data);assert r.status_code==200;t=r.json['token']
    card=c.get('/kunde/KH-TEST-1').text;assert '1000 Punkte reserviert' in card and '500 frei verfügbar' in card
    assert 'Number(500)' in c.get('/kunde/KH-TEST-1/praemien').text
    stranger=env['app'].test_client()
    assert post(stranger,'/kunde/praemien/'+t+'/stornieren').status_code==401
    assert post(c,'/kunde/praemien/'+t+'/stornieren').status_code==302
    assert '1000 Punkte reserviert' not in c.get('/kunde/KH-TEST-1').text
    assert sql(env,'SELECT SUM(punkte) FROM punkte_bewegungen',fetch=True)[0][0]==1500
