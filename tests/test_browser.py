"""Real-browser acceptance on synthetic localhost data; never a live checkout."""
import os
import pathlib
import threading
import shutil
import pytest
from werkzeug.serving import make_server
from test_readiness import sql

pytestmark=pytest.mark.skipif(os.environ.get('RUN_BROWSER_TESTS')!='1',reason='Browser acceptance runs in CI')


def test_mobile_bonus_and_guest_order_browser(env):
    from playwright.sync_api import sync_playwright,expect
    server=make_server('127.0.0.1',0,env['app'],threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    output=pathlib.Path('test-results');output.mkdir(exist_ok=True)
    errors=[]
    try:
        with sync_playwright() as p:
            executable=shutil.which('google-chrome') or shutil.which('chromium')
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':executable} if executable else {}))
            page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(origin+'/',wait_until='networkidle')
            expect(page.get_by_text('Worauf hast du Lust?')).to_be_visible()
            expect(page.get_by_role('link',name='Jetzt bestellen',exact=False)).to_be_visible()
            page.screenshot(path=str(output/'01-start-mobile.png'),full_page=True)
            page.goto(origin+'/login')
            page.locator('#kennung').fill('KH-TEST-1');page.locator('#passwort').fill(env['customer_password'])
            page.get_by_role('button',name='Sicher anmelden').click()
            expect(page.get_by_text('Hallo, Synthetic.')).to_be_visible()
            page.screenshot(path=str(output/'02-bonus-mobile.png'),full_page=True)
            # The back navigation must retain the customer session.
            page.get_by_role('link',name='Speisekarte öffnen & bestellen').click()
            expect(page.locator('.category-tile')).to_have_count(19)
            page.locator('button[onclick="bestellseiteZurueck()"]').click()
            expect(page.get_by_text('Hallo, Synthetic.')).to_be_visible()
            # Separate anonymous customer browser, no compulsory registration.
            guest=browser.new_page(viewport={'width':390,'height':844})
            guest.on('pageerror',lambda error:errors.append(str(error)))
            guest.goto(origin+'/bestellen',wait_until='networkidle')
            guest.locator('.category-tile').filter(has_text='Döner Sandwich').click()
            guest.locator('.product-card').filter(has=guest.locator('.product-name',has_text='Döner Sandwich Steak')).first.click()
            guest.locator('.variant-btn').filter(has_text='Classic').click()
            guest.locator('#addToCartBtn').click()
            guest.get_by_role('button',name='Zum Warenkorb').click()
            expect(guest.locator('#cartTotal')).to_have_text('12,50€')
            guest.wait_for_function("Math.abs(document.querySelector('#cartSidebar').getBoundingClientRect().x)<1")
            guest.screenshot(path=str(output/'03-order-mobile.png'),full_page=True)
            guest.locator('#checkoutButton').click()
            expect(guest.locator('#successModal')).to_have_class('success-overlay show')
            expect(guest.locator('#orderNumber')).to_have_text('#100')
            assert sql(env,'SELECT COUNT(*) FROM bestellungen',fetch=True)[0][0]==1
            # Screenshots of the shared premium product presentation.
            sql(env,"INSERT INTO punkte_bewegungen(kunde_id,typ,punkte) VALUES (%s,'GUTSCHRIFT',2000)",(env['customer_id'],))
            page.goto(origin+'/kunde/KH-TEST-1/praemien',wait_until='networkidle')
            expect(page.locator('.category-tile')).to_have_count(19)
            page.screenshot(path=str(output/'04-rewards-mobile.png'),full_page=True)
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)
