"""Personal sessions are server-revocable and never inferred from customer IDs."""
import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from flask import g, request, session
from werkzeug.security import check_password_hash, generate_password_hash
import pyotp
from shop import transaction, setting, save_setting


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def safe_next(value, default='/theke'):
    # Never reflect a URL into a Location header or HTML without validation.
    if not isinstance(value,str) or not value.startswith('/') or value.startswith('//') or any(c in value for c in ('\\','\r','\n','\t','#')):
        return default
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.path.startswith(('/chef-login','/mitarbeiter-login')):
        return default
    return value


class Access:
    def __init__(self, app, connect):
        self.app, self.connect = app, connect

    def audit(self, cur, event, object_id=None):
        cur.execute('INSERT INTO sicherheitsprotokoll(ereignis,rolle,objekt_id) VALUES (%s,%s,%s)',
                    (event, session.get('personal_rolle'), str(object_id) if object_id is not None else None))

    def version(self, cur, role, employee=None):
        if role == 'chef':
            credential = setting(cur,'chef_pin') + '|' + setting(cur,'chef_totp_secret')
        elif employee:
            credential = employee[1]
        else:
            return ''
        return digest(credential)

    def staff(self):
        if hasattr(g,'staff_identity'):
            return g.staff_identity
        g.staff_identity = None
        raw = session.get('personal_token')
        role = session.get('personal_rolle')
        if not raw or role not in ('chef','mitarbeiter'):
            return None
        with transaction(self.connect) as cur:
            cur.execute('SELECT rolle,mitarbeiter_id,credential_version FROM personal_sitzungen WHERE token_hash=%s AND NOT widerrufen AND ablauf_am>NOW()', (digest(raw),))
            row = cur.fetchone()
            if not row or row[0] != role:
                return None
            employee, name = None, 'Chef'
            if role == 'mitarbeiter':
                cur.execute('SELECT id,pin,name FROM mitarbeiter WHERE id=%s AND aktiv=TRUE', (row[1],))
                employee = cur.fetchone()
                if not employee:
                    return None
                name = employee[2]
            if not secrets.compare_digest(row[2], self.version(cur, role, employee)):
                return None
            g.staff_identity = {'role':role,'id':row[1],'name':name}
        return g.staff_identity

    def role(self, role):
        identity = self.staff()
        return bool(identity and identity['role'] == role)

    def begin(self, role, employee=None):
        self.end()
        session.clear()
        raw = secrets.token_urlsafe(40)
        with transaction(self.connect) as cur:
            cur.execute('INSERT INTO personal_sitzungen(token_hash,rolle,mitarbeiter_id,credential_version,ablauf_am) VALUES (%s,%s,%s,%s,%s)',
                        (digest(raw),role,employee[0] if employee else None,self.version(cur,role,employee),datetime.now(timezone.utc)+timedelta(hours=12)))
            self.audit(cur,'PERSONAL_LOGIN',employee[0] if employee else 'chef')
        session.permanent = True
        session.update(personal_token=raw, personal_rolle=role)
        if role == 'mitarbeiter':
            session.update(mitarbeiter_angemeldet=True,mitarbeiter_id=employee[0],mitarbeiter_name=employee[2])
        else:
            session['chef_angemeldet'] = True
        g.pop('staff_identity',None)

    def end(self):
        raw = session.get('personal_token')
        if raw:
            with transaction(self.connect) as cur:
                cur.execute('UPDATE personal_sitzungen SET widerrufen=TRUE WHERE token_hash=%s', (digest(raw),))
        for key in ('personal_token','personal_rolle','mitarbeiter_angemeldet','mitarbeiter_id','mitarbeiter_name','chef_angemeldet'):
            session.pop(key,None)
        g.pop('staff_identity',None)

    def rate_allowed(self, role, identifier=''):
        # Reserve an attempt atomically BEFORE the expensive password hash check.
        # Do not store raw IPs or customer identifiers in the attempt table.
        bucket = digest(role+'|'+str(request.remote_addr)+'|'+identifier.casefold()[:100])
        with transaction(self.connect) as cur:
            cur.execute("""INSERT INTO login_versuche(bucket,versuche) VALUES (%s,1)
                ON CONFLICT(bucket) DO UPDATE SET
                versuche=CASE WHEN login_versuche.fenster_am<NOW()-INTERVAL '15 minutes' THEN 1 ELSE login_versuche.versuche+1 END,
                fenster_am=CASE WHEN login_versuche.fenster_am<NOW()-INTERVAL '15 minutes' THEN NOW() ELSE login_versuche.fenster_am END
                RETURNING versuche""", (bucket,))
            allowed = cur.fetchone()[0] <= (200 if role=='customer_ip' else 15)
        return allowed

    def cipher(self):
        raw = self.app.secret_key
        if isinstance(raw,str):
            raw = raw.encode()
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw+b'chef-totp-v1').digest()))

    def verify_second_factor(self, code):
        code = str(code).strip().replace(' ','')
        with transaction(self.connect) as cur:
            cur.execute("SELECT wert FROM einstellungen WHERE schluessel='chef_totp_secret' FOR UPDATE")
            row = cur.fetchone()
            if not row or not row[0]:
                return True
            try:
                secret = self.cipher().decrypt(row[0].encode()).decode()
            except Exception:
                self.app.logger.error('Chef second-factor decryption failed; check stable SECRET_KEY configuration')
                return False
            step = int(time.time())//30
            last = int(setting(cur,'chef_totp_last_step','-1'))
            totp = pyotp.TOTP(secret)
            for candidate in (step-1, step, step+1):
                if candidate>last and secrets.compare_digest(totp.at(candidate*30),code):
                    save_setting(cur,'chef_totp_last_step',candidate)
                    return True
            codes = json.loads(setting(cur,'chef_recovery_codes','[]'))
            for index, hashed in enumerate(codes):
                if check_password_hash(hashed,code):
                    codes.pop(index)
                    save_setting(cur,'chef_recovery_codes',json.dumps(codes))
                    self.audit(cur,'CHEF_RECOVERY_USED')
                    return True
            return False

    def verify_chef_pin(self,pin):
        with transaction(self.connect) as cur:
            hashed = setting(cur,'chef_pin')
        return bool(hashed and pin and check_password_hash(hashed,pin))

    def employee_for_pin(self,pin):
        if not pin or len(pin)>128:
            return None
        with transaction(self.connect) as cur:
            cur.execute('SELECT id,pin,name FROM mitarbeiter WHERE aktiv=TRUE ORDER BY id')
            rows = cur.fetchall()
        matches = [r for r in rows if check_password_hash(r[1],pin)]
        return matches[0] if len(matches)==1 else None

    def pin_used(self,cur,pin,exclude=None):
        cur.execute('SELECT id,pin FROM mitarbeiter WHERE id IS DISTINCT FROM %s', (exclude,))
        return any(check_password_hash(row[1],pin) for row in cur.fetchall())
