import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.foundry import _parse_status_url as p

cases = [
    # gerçek çıktı — servis hazırlanıyor
    ('{"running":false,"state":"initializing"}', None),
    # gerçek çıktı — servis hazır
    ('{"running":true,"state":"ready","pid":2828,'
     '"webUrls":["http://127.0.0.1:58082"],'
     '"startedAt":"2026-08-23T08:35:13.606139+00:00",'
     '"uptime":"14s","logFile":""}', "http://127.0.0.1:58082"),
    ("", None),
    # -o json desteklenmezse metin yedeği
    ("● success: Server ready (http://127.0.0.1:58082)", "http://127.0.0.1:58082"),
    # koşuyor ama URL yok
    ('{"running":true,"webUrls":[]}', None),
    # alan adı değişmiş sürüm — JSON içinde arama
    ('{"running":true,"endpoints":["http://127.0.0.1:1234"]}',
     "http://127.0.0.1:1234"),
    # sondaki eğik çizgi kırpılmalı
    ('{"running":true,"webUrls":["http://127.0.0.1:58082/"]}',
     "http://127.0.0.1:58082"),
]

ok = True
for raw, want in cases:
    got = p(raw)
    if got != want:
        ok = False
        print("FAIL", repr(raw[:55]), "->", got, "beklenen", want)
    else:
        print("OK  ", repr(raw[:55]), "->", got)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
