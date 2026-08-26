"""Kesilmis JSON kurtarma: neyi kurtarmali, neyi UYDURMAMALI."""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.provider import extract_json

# Uretimde gorulen gercek kesilme
GERCEK = ('{\n  "summary": "Ag sagligi genel olarak iyi, ancak belirli '
          'cihazlar ve trafik siniflari yuksek kullanim ve dusuk guvenilirlik '
          'gosteriyor, bu da potansiyel baglanti sorunlarina veya hizmet '
          'kalitesinde ')

VAKALAR = [
    ("tam nesne (kurtarma devreye girmemeli)",
     '{"summary":"tamam","health_score":80}',
     lambda d: d["summary"] == "tamam" and d["health_score"] == 80),
    ("uretimde gorulen kesilme",
     GERCEK,
     lambda d: d.get("summary", "").startswith("Ag sagligi")),
    ("dizi ortasinda kesilme",
     '{"summary":"x","findings":[{"title":"a","detail":"b"},{"title":"c"',
     lambda d: d["summary"] == "x" and len(d.get("findings", [])) >= 1
               and d["findings"][0]["title"] == "a"),
    ("sayi ortasinda kesilme",
     '{"summary":"x","health_score":8',
     lambda d: d["summary"] == "x"),
    ("ic ice nesne kesilmesi",
     '{"a":{"b":{"c":"d","e":"f',
     lambda d: d.get("a", {}).get("b", {}).get("c") == "d"),
    ("fence icinde kesilme",
     '```json\n{"summary":"y","findings":[{"title":"z"',
     lambda d: d["summary"] == "y"),
    ("fazla virgul: dizi sonu",
     '{"a":[1,2,],"b":3}',
     lambda d: d["a"] == [1, 2] and d["b"] == 3),
    ("fazla virgul: nesne sonu",
     '{"a":1,"b":2,}',
     lambda d: d["a"] == 1 and d["b"] == 2),
    ("fazla virgul + kesilme (uretimde gorulen)",
     '{"summary":"ok","findings":[{"t":"a"},{"t":"b"},],"recs":[{"x":1,"c":',
     lambda d: d["summary"] == "ok" and len(d["findings"]) == 2),
    ("dize icindeki virgul KORUNMALI",
     '{"r":"a, b, c","n":1}',
     lambda d: d["r"] == "a, b, c"),
    ("kacisli tirnak + virgul",
     # JSON'da kacisli tirnak TEK ters boludur; iki tane yazmak kacisli
     # ters bolu demek ve dizeyi orada kapatir. Fixture'in kendisi bozuktu.
     '{"a":"tirnak ' + chr(92) + '" icinde, virgul","b":[1,2,],}',
     lambda d: d["a"].endswith("icinde, virgul") and d["b"] == [1, 2]),
    ("kacisli tirnak iceren kesilme",
     '{"summary":"o \\"dedi\\" ve','', ),
]

ok = True
for vaka in VAKALAR:
    ad, metin = vaka[0], vaka[1]
    kontrol = vaka[2] if len(vaka) > 2 and callable(vaka[2]) else None
    try:
        d = extract_json(metin)
        gecti = (kontrol(d) if kontrol else isinstance(d, dict))
        print(f"{'OK  ' if gecti else 'FAIL'} {ad:<42} -> {str(d)[:60]}")
    except Exception as e:
        gecti = False
        print(f"FAIL {ad:<42} -> istisna: {e}")
    ok &= gecti

print()
print("UYDURMAMASI GEREKENLER")
print("-" * 70)
for ad, metin in [("bos", ""), ("json yok", "merhaba nasilsin"),
                  ("yalniz acilis", "{")]:
    try:
        d = extract_json(metin)
        # `{` tek basina bos nesneye acilabilir; bos donmesi kabul,
        # uydurulmus alan donmesi degil.
        gecti = isinstance(d, dict) and not d
        print(f"{'OK  ' if gecti else 'FAIL'} {ad:<20} -> {d!r}")
    except Exception as e:
        gecti = True
        print(f"OK   {ad:<20} -> reddedildi ({type(e).__name__})")
    ok &= gecti

# Ust duzey dizi MESRU: akis yolu (`flowai._normalize`) o bicimi kullaniyor.
# Kural, cagiranin sozluk beklerken kontrol etmesi.
d = extract_json("[1,2,3]")
liste_ok = isinstance(d, list) and d == [1, 2, 3]
print(f"{'OK  ' if liste_ok else 'FAIL'} ust duzey dizi olduğu gibi doner -> {d!r}")
ok &= liste_ok

from ntc.ai.analyst import AIAnalyst
import inspect
kaynak = inspect.getsource(AIAnalyst.analyze)
korumali = "isinstance(data, dict)" in kaynak
print(f"{'OK  ' if korumali else 'FAIL'} analyze() sozluk olmayani eliyor")
ok &= korumali

print()
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
