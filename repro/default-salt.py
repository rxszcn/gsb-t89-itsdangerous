# Probe itsdangerous 2.2.0: "the default salt" and salt application rules are
# implemented twice with different values (signer.py vs serializer.py), and
# TimedSerializer re-routes SignatureExpired around the 'never fails' contract.
# Run from repo root: PYTHONPATH=src .venv/bin/python <this file>
import json

from freezegun import freeze_time

import itsdangerous
from itsdangerous import Signer

print("exports:", [n for n in dir(itsdangerous) if not n.startswith("_")])

k = b"shared-secret"
print("--- A: two layers, two different 'default salt' literals ---")
s = Signer(k)
ser = itsdangerous.Serializer(k)
print("Signer default salt     :", s.salt)
print("Serializer default salt :", ser.salt)
print("Signer.default_key_derivation:", Signer.default_key_derivation)
print(
    "salt ignored under default derivation?",
    Signer(k, salt=b"totally-different").sign(b"x") == Signer(k).sign(b"x"),
)

print("--- B: with key_derivation=hmac the two defaults collide ---")
serh = itsdangerous.Serializer(k, signer_kwargs={"key_derivation": "hmac"})
tok = serh.dumps({"u": 1})
payload_b, _, sig_b = tok.rpartition(".")
print("serializer token        :", tok)
s_default = Signer(k, key_derivation="hmac")
s_ser_salt = Signer(k, key_derivation="hmac", salt=ser.salt)
print("verify w/ Signer-default salt :", s_default.verify_signature(payload_b.encode(), sig_b.encode()))
print("verify w/ Serializer-default salt:", s_ser_salt.verify_signature(payload_b.encode(), sig_b.encode()))

print("--- C: expired Timestamp token through loads vs loads_unsafe ---")
try:
    TimedSerializer = itsdangerous.TimedSerializer
except AttributeError:
    class TimedSerializer(itsdangerous.serializer.Serializer):
        default_signer = itsdangerous.TimestampSigner

tts = TimedSerializer(k)
with freeze_time("2020-01-01 00:00:00"):
    ttok = tts.dumps({"u": 42})
print("timed token:", ttok)
with freeze_time("2020-01-01 00:10:00"):
    try:
        tts.loads(ttok, max_age=60)
        print("loads: OK (unexpected)")
    except Exception as e:
        print("loads(max_age=60) ->", type(e).__name__, ":", e)
    print("loads_unsafe(max_age=60) ->", tts.loads_unsafe(ttok, max_age=60))
    print("loads no max_age ->", tts.loads(ttok))
