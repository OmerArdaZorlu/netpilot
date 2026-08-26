import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.provider import extract_json

ok = True
def case(name, raw, want_key, want_val):
    global ok
    try:
        got = extract_json(raw)
        good = got.get(want_key) == want_val
    except Exception as exc:
        got, good = f"ISTISNA {exc}", False
    if not good:
        ok = False
    print(f"  {'OK  ' if good else 'FAIL'} {name}")
    if not good:
        print(f"        -> {got}")

# Uretimde kirilan gercek vaka
case("bozuk fence + gercek json blogu",
     '```ple bir JSON formatinda sonucun:\n\n```json\n{"summary": "ok", "health_score": 70}\n```',
     "summary", "ok")

case("duz json", '{"summary": "ok"}', "summary", "ok")
case("json fence", '```json\n{"summary": "ok"}\n```', "summary", "ok")
case("adsiz fence", '```\n{"summary": "ok"}\n```', "summary", "ok")
case("onsozlu", 'Iste analiz:\n{"summary": "ok"}', "summary", "ok")
case("sonsozlu", '{"summary": "ok"}\nUmarim yardimci olur.', "summary", "ok")
case("ic ice nesne", '{"a": {"b": 1}, "summary": "ok"}', "summary", "ok")
case("dizede suslu parantez", '{"summary": "ok", "reason": "a { b } c"}', "summary", "ok")
case("dizede kacisli tirnak", '{"summary": "ok", "reason": "dedi \\"merhaba\\""}', "summary", "ok")
case("bos fence + json", '```\n\n```\n{"summary": "ok"}', "summary", "ok")
case("iki fence, ilki bozuk",
     '```\nbu json degil\n```\n```json\n{"summary": "ok"}\n```', "summary", "ok")

# Bulunamamasi gerekenler
# NOT: 'yarim' vakasi bu listeden CIKARILDI ve bu bilincli bir sozlesme
# degisikligi. Kesilmis JSON artik reddedilmiyor, kurtariliyor
# (`_salvage_truncated`). Sebep uretimde olculdu: model baglami 4096 token
# ve uzun bir yanit ortada kesilince analizin TAMAMI cope gidiyordu —
# gecerli olan ozet ve ilk bulgular dahil. Kurtarma vakalari t_kurtarma.py'de.
for name, raw in [("bos", ""), ("json yok", "hicbir sey yok")]:
    try:
        extract_json(raw)
        print(f"  FAIL {name}: hata bekleniyordu")
        ok = False
    except ValueError:
        print(f"  OK   {name}: dogru sekilde reddedildi")

print("\nSONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
