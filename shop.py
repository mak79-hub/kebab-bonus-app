"""Shared shop configuration and authoritative money/points calculations."""
import copy
import json
import os
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from flask import g, has_request_context

CATALOG = json.loads(Path(__file__).with_name('menu_catalog.json').read_text(encoding='utf-8'))
PRODUCTS = {p['id']: p for p in CATALOG['products']}
DEFAULT = {
    'name': 'Kebap Höhle XXL', 'city': 'Bischofsheim',
    'owner': 'Mustafa Erdogan', 'address': 'Darmstädter Str. 81', 'postcode': '65474',
    'phone': '06144 2079485', 'email': 'mustafaerdugulu@outlook.de',
    'logo': '/static/bilder/logo.png', 'modules': 'both',
    'hours': {'0':['11:00','21:30'],'1':None,'2':['11:00','21:30'],'3':['11:00','21:30'],
              '4':['11:00','21:30'],'5':['12:00','21:30'],'6':['12:00','21:30']},
    'delivery': {'Bischofsheim':25,'Ginsheim':25,'Gustavsburg':25,'Bauschheim':35,
                 'Mainz-Kostheim':35,'Rüsselsheim':45,'Mainz-Kastel':45,'Nauheim':45,'Trebur':45,'Astheim':45},
}


@contextmanager
def transaction(connect):
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def setting(cur, key, default=''):
    cur.execute('SELECT wert FROM einstellungen WHERE schluessel=%s', (key,))
    row = cur.fetchone()
    return row[0] if row else default


def save_setting(cur, key, value):
    cur.execute('INSERT INTO einstellungen(schluessel,wert) VALUES (%s,%s) ON CONFLICT(schluessel) DO UPDATE SET wert=EXCLUDED.wert', (key, str(value)))


def config(connect):
    if has_request_context() and hasattr(g, 'shop_config'):
        return g.shop_config
    with transaction(connect) as cur:
        raw = setting(cur, 'shop_config', '{}')
        menu_configured = bool(setting(cur,'menu_catalog',''))
    result = copy.deepcopy(DEFAULT)
    result.update(json.loads(raw))
    # New installations must explicitly supply their own identity (no silent clone).
    if os.environ.get('SHOP_KEY', 'kebab-hoehle-xxl') != 'kebab-hoehle-xxl' and raw == '{}':
        result.update(name='Neuer Laden', city='', owner='', address='', postcode='', phone='', email='', logo='', delivery={}, modules='bonus')
    result['setup_complete'] = os.environ.get('SHOP_KEY','kebab-hoehle-xxl')=='kebab-hoehle-xxl' or (raw!='{}' and menu_configured)
    if has_request_context():
        g.shop_config = result
    return result


def enabled(cfg, module):
    return cfg['modules'] in (module, 'both')


def cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('.01')):
            raise ValueError('Ungültiger Geldbetrag.')
        return int(amount * 100)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Ungültiger Geldbetrag.') from None


def integer(value, minimum=1, maximum=20):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError('Die gewählte Menge oder Kennung ist ungültig.')
    return value


def selections(value, allowed, maximum):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(v,str) or v not in allowed for v in value) or len(set(value)) != len(value):
        raise ValueError('Eine Auswahl ist ungültig oder doppelt vorhanden.')
    return value


def catalog(connect, rewards=False):
    if has_request_context():
        cached = getattr(g, 'reward_catalog' if rewards else 'menu_catalog', None)
        if cached is not None:
            return cached
    with transaction(connect) as cur:
        result = json.loads(setting(cur,'menu_catalog',json.dumps(CATALOG)))
        if rewards:
            from praemien_katalog import PRAEMIEN_KATALOG, PRAEMIEN_EXTRAS, PRAEMIEN_MENUES
            reference = json.loads(Path(__file__).with_name('reward_catalog.json').read_text(encoding='utf-8'))
            source = {p['id']:p for p in result['products']}
            cur.execute('SELECT produkt_id,varianten,aktiv FROM bonus_katalog ORDER BY produkt_id')
            rows = cur.fetchall()
            products = []
            for product_id, variants, active in rows:
                if not active or product_id not in source:
                    continue
                product = copy.deepcopy(source[product_id])
                product['variants'] = [{'name':v['name'],'price':variants[v['name']]/100} for v in product['variants'] if v['name'] in variants]
                if not product['variants']:
                    continue
                for extra in product['extras']:
                    if extra['name']=='Extra Ei':
                        extra.update(priceClassic=1,priceXXL=2)
                    elif extra['name'] in PRAEMIEN_EXTRAS:
                        extra['price'] = PRAEMIEN_EXTRAS[extra['name']]/100
                products.append(product)
            result['products'] = products
            result['doener_extras'] = [{'name':n,'price':PRAEMIEN_EXTRAS[n]/100} for n in ('Fleisch','Gegrilltes Gemüse')]
            result['upgrades'] = [{'name':u['name'],'desc':u['desc'],'price':PRAEMIEN_MENUES[u['name']]/100} for u in reference['upgrades']]
    if has_request_context():
        setattr(g,'reward_catalog' if rewards else 'menu_catalog',result)
    return result


def paid_positions(items, menu=None):
    menu = menu or CATALOG
    products = {p['id']:p for p in menu['products']}
    if not isinstance(items,list) or not 1 <= len(items) <= 50:
        raise ValueError('Der Warenkorb ist leer oder zu groß.')
    positions = []
    total_cents = 0
    for item in items:
        if not isinstance(item,dict):
            raise ValueError('Eine Bestellposition ist ungültig.')
        product_client = item.get('product') or {}
        variant_client = item.get('variant') or {}
        if not isinstance(product_client,dict) or not isinstance(variant_client,dict):
            raise ValueError('Produkt oder Variante ist ungültig.')
        product_id = integer(item.get('productId',product_client.get('id')), 1, 1000000)
        product = products.get(product_id)
        if not product:
            raise ValueError('Das Produkt ist nicht in der Speisekarte.')
        variant = next((v for v in product['variants'] if v['name'] == variant_client.get('name')), None)
        if variant is None:
            raise ValueError('Die Produktvariante ist ungültig.')
        qty = integer(item.get('qty',1))
        unit = cents(variant['price'])
        is_doener = product['cat'] in {'koefte','doener-sandwich','wrap-doener','doener-teller','lahmacun'} or (product['cat']=='boxenstopp' and any(n in product['name'].lower() for n in ('steak','beef','chicken')))
        allowed_extras = menu['doener_extras'] if is_doener else product['extras']
        extras_in = item.get('extras') or []
        if not isinstance(extras_in,list) or len(extras_in)>20:
            raise ValueError('Extras sind ungültig.')
        extras, seen = [], set()
        for requested in extras_in:
            if not isinstance(requested,dict) or not isinstance(requested.get('name'),str):
                raise ValueError('Ein Extra ist ungültig.')
            name = requested['name']
            extra = next((e for e in allowed_extras if e['name']==name),None)
            if not extra or name in seen:
                raise ValueError('Ein Extra ist nicht erlaubt oder doppelt vorhanden.')
            seen.add(name)
            amount = cents(extra['price'] if 'price' in extra else extra['priceXXL'] if variant['name']=='XXL' else extra['priceClassic'])
            unit += amount
            extras.append({'name': name, 'price': amount/100})
        upgrade_in = item.get('upgrade')
        upgrade = None
        if upgrade_in is not None:
            if not isinstance(upgrade_in,dict) or product['cat'] in {'getraenke','menus'}:
                raise ValueError('Für dieses Produkt ist kein Menü-Zusatz erlaubt.')
            upgrade = next((copy.deepcopy(u) for u in menu['upgrades'] if u['name']==upgrade_in.get('name')),None)
            if upgrade is None:
                raise ValueError('Das gewählte Menü ist ungültig.')
            unit += cents(upgrade['price'])
        sauces = selections(item.get('sauces'), menu['sauces'] if is_doener else [], 2)
        without = selections(item.get('without'), menu['without'] if is_doener else [], 7)
        side = item.get('side')
        if product_id in {22,23,24,101}:
            if side not in ('Pommes','Reis'):
                raise ValueError('Bitte Pommes oder Reis als Beilage auswählen.')
        elif side is not None:
            raise ValueError('Für dieses Produkt gibt es keine freie Beilagenauswahl.')
        note = item.get('note') or ''
        if not isinstance(note,str) or len(note)>300 or any(ord(c)<32 and c not in '\n\t' for c in note):
            raise ValueError('Der Küchenhinweis ist ungültig.')
        line = unit*qty
        if 'total' in item and cents(item['total']) != line:
            raise ValueError('Der angezeigte Preis stimmt nicht mit der Speisekarte überein. Bitte Seite neu laden und Auswahl erneut bestätigen.')
        if line>100000:
            raise ValueError('Eine Position darf höchstens 1.000 € kosten.')
        total_cents += line
        positions.append({'productId':product_id,'product':product,'variant':variant,'qty':qty,
                          'extras':extras,'upgrade':upgrade,'sauces':sauces,'without':without,
                          'side':side,'note':note,'unit_cents':unit,'total_cents':line,'total':line/100})
    if not 0 < total_cents <= 250000:
        raise ValueError('Die Bestellung darf höchstens 2.500 € kosten.')
    return positions,total_cents


def purchase_points(cur, amount):
    try:
        rate = Decimal(setting(cur,'punkte_pro_euro','1'))
        value = Decimal(str(amount))
        if not rate.is_finite() or not 1<=rate<=1000 or not value.is_finite() or not 0<=value<=2500:
            raise ValueError('Punkteregel oder Einkaufsbetrag ist ungültig.')
        return int((value*rate).to_integral_value(rounding=ROUND_DOWN))
    except InvalidOperation:
        raise ValueError('Punkteregel oder Einkaufsbetrag ist ungültig.') from None
