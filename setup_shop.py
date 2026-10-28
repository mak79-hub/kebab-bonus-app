"""Repeatable provisioning without cloning code or sharing another shop's DB.

Usage: SHOP_KEY=my-shop DATABASE_URL=... CHEF_PIN=... python setup_shop.py
       --profile profile.json --menu menu.json
Secrets come only from the configured environment, never CLI args/output/files.
"""
import argparse
import json
import re
import os
from pathlib import Path
from shop import DEFAULT, cents, save_setting, transaction


def validate_menu(menu):
    if not isinstance(menu,dict) or not all(isinstance(menu.get(k),list) for k in ('categories','products','upgrades','sauces','without','doener_extras')):
        raise ValueError('Speisekarte benötigt Kategorien, Produkte, Menüs und Optionslisten.')
    if not 1<=len(menu['products'])<=500 or not 1<=len(menu['categories'])<=50:
        raise ValueError('Bitte 1–500 Produkte und 1–50 Kategorien verwenden.')
    category_ids=set();product_ids=set()
    def text(value,limit=300):
        if not isinstance(value,str) or len(value)>limit or any(c in value for c in '<>"\'\\&') or any(ord(c)<32 for c in value):
            raise ValueError('Produkttexte müssen Klartext ohne HTML oder Anführungszeichen sein.')
    def image(value):
        if not isinstance(value,str) or len(value)>2000 or any(c in value for c in '<>"\'\\') or any(ord(c)<32 for c in value) or (value and not (value.startswith('/static/') or value.startswith('https://'))):
            raise ValueError('Produktbilder benötigen einen sicheren /static/-Pfad oder eine HTTPS-Adresse.')
    for category in menu['categories']:
        cid=category.get('id','')
        if not re.fullmatch(r'[a-z0-9-]{1,50}',cid) or cid in category_ids:raise ValueError('Kategorie-ID ist ungültig oder doppelt.')
        category_ids.add(cid);text(category.get('name'),100);text(category.get('emoji',''),16);image(category.get('img',''))
    for p in menu['products']:
        if not isinstance(p,dict) or isinstance(p.get('id'),bool) or not isinstance(p.get('id'),int) or not 1<=p['id']<=1000000 or p['id'] in product_ids or p.get('cat') not in category_ids:
            raise ValueError('Produkt-ID oder Kategorie ist ungültig oder doppelt.')
        product_ids.add(p['id']);text(p.get('name').replace('&','und'),100);text(p.get('desc','').replace('&','und'),1000)
        image(p.get('img',''));text(p.get('emoji',''),16)
        if not isinstance(p.get('variants'),list) or not 1<=len(p['variants'])<=10:raise ValueError('Produktvarianten fehlen.')
        names=set()
        for v in p['variants']:
            text(v.get('name'),80)
            if v['name'] in names or not 1<=cents(v.get('price'))<=100000:raise ValueError('Variante oder Preis ist ungültig.')
            names.add(v['name'])
        if not isinstance(p.get('extras'),list) or len(p['extras'])>20:raise ValueError('Ungültige Extras.')
        for extra in p['extras']:
            text(extra.get('name'),80)
            for key in ('price','priceClassic','priceXXL'):
                if key in extra and not 0<=cents(extra[key])<=100000:raise ValueError('Ungültiger Extrapreis.')
            if 'price' not in extra and not all(k in extra for k in ('priceClassic','priceXXL')):raise ValueError('Extrapreis fehlt.')
    for group in ('sauces','without'):
        if len(menu[group])>20:raise ValueError('Zu viele Optionen.')
        for value in menu[group]:text(value,80)
    for group in ('upgrades','doener_extras'):
        if len(menu[group])>20:raise ValueError('Zu viele Zusatzoptionen.')
        for value in menu[group]:
            text(value.get('name'),80)
            text(value.get('desc','').replace('&','und'),1000)
            if not 0<=cents(value.get('price'))<=100000:raise ValueError('Ungültiger Zusatzpreis.')
    return menu


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',required=True,type=Path)
    parser.add_argument('--menu',required=True,type=Path)
    args=parser.parse_args()
    if not os.environ.get('SHOP_KEY') or os.environ['SHOP_KEY']=='kebab-hoehle-xxl':
        raise SystemExit('Für einen neuen Laden zuerst eine eigene SHOP_KEY und eine eigene DATABASE_URL konfigurieren. Die Mustafa-Installation wird hier nicht überschrieben.')
    profile={**DEFAULT,**json.loads(args.profile.read_text())}
    if profile.get('modules') not in ('bonus','ordering','both') or not all(isinstance(profile.get(k),str) and profile[k].strip() for k in ('name','owner','address','postcode','city','email')):
        raise SystemExit('Vollständiges Betreiberprofil und gültige Modulwahl erforderlich.')
    for day,pair in profile['hours'].items():
        from datetime import time
        if day not in {str(n) for n in range(7)} or (pair and (len(pair)!=2 or time.fromisoformat(pair[0])>=time.fromisoformat(pair[1]))):raise SystemExit('Ungültige Bestellzeiten.')
    if any(not isinstance(city,str) or not city or not 0<=cents(value)<=250000 for city,value in profile['delivery'].items()):raise SystemExit('Ungültige Lieferorte.')
    menu=validate_menu(json.loads(args.menu.read_text()))
    import app
    from psycopg2.extras import Json
    with transaction(app.get_db_connection) as cur:
        save_setting(cur,'shop_config',json.dumps(profile,ensure_ascii=False))
        save_setting(cur,'menu_catalog',json.dumps(menu,ensure_ascii=False))
        for product in menu['products']:
            variants={v['name']:cents(v['price']) for v in product['variants']}
            cur.execute('INSERT INTO bonus_katalog(produkt_id,varianten) VALUES (%s,%s) ON CONFLICT DO NOTHING',(product['id'],Json(variants)))
    print('Profil, Module und Speisekarte eingerichtet. Keine Kunden/Punkte/Bestellungen gelöscht. Geräte und Betreiberangaben vor Freigabe prüfen.')


if __name__=='__main__':main()
