import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.analyst import AIAnalyst

VALID = {"cam-entrance", "cam-parking", "ws-dev-02", "ws-finance-01",
         "srv-backup-01", "realtime", "interactive", "streaming", "bulk",
         "background", "link"}

# Hepsi gercek olcumden alindi (12 kosu, iki varyant)
cases = [
    ("streaming",                       ["streaming"]),
    ("cam-entrance",                    ["cam-entrance"]),
    ("link",                            ["link"]),
    ("cam-entrance, cam-parking",       ["cam-entrance", "cam-parking"]),
    ("ws-dev-02, ws-finance-01",        ["ws-dev-02", "ws-finance-01"]),
    ("lan_mbps",                        []),
    ("active_flows",                    []),
    ("trust=0.95 (srv-backup-01)",      []),
    ("",                                []),
    ("  streaming  ",                   ["streaming"]),
    ("cam-entrance, uydurma-cihaz",     []),   # biri gecersizse tumu duser
]

ok = True
for raw, want in cases:
    got = AIAnalyst._resolve_targets(raw, VALID)
    flag = "OK  " if got == want else "FAIL"
    if got != want:
        ok = False
    print(f"{flag} {raw!r:34} -> {got}")

# Uctan uca: gecersizler dusuyor, virgullu olan aciliyor
recs = [
    {"action": "deprioritize", "target": "streaming", "reason": "r", "confidence": 0.9},
    {"action": "rate_limit", "target": "cam-entrance, cam-parking", "reason": "r", "confidence": 0.8},
    {"action": "advise", "target": "lan_mbps", "reason": "r", "confidence": 0.5},
    {"action": "uydurma_eylem", "target": "link", "reason": "r", "confidence": 2.0},
]
out = AIAnalyst._clean_recommendations(recs, VALID)
print("\nuctan uca:")
for r in out:
    print("  ", r)
# AI onerileri artik HER ZAMAN "advise". Uygulanabilir aksiyonun sayisini
# akis cozucusu veriyor; modelin oraya ikinci bir sayi yazmasi, kaldirdigimiz
# celiskiyi geri getirirdi.
beklenen = [("advise", "streaming"), ("advise", "cam-entrance"),
            ("advise", "cam-parking"), ("advise", "link")]
alinan = [(r["action"], r["target"]) for r in out]
if alinan != beklenen:
    ok = False
    print("  FAIL beklenen:", beklenen)
if any(r["action"] != "advise" for r in out):
    ok = False
    print("  FAIL: uygulanabilir aksiyon uretilmis")
if out and out[-1]["confidence"] != 1.0:
    ok = False
    print("  FAIL guven kirpilmadi:", out[-1]["confidence"])

print("\nSONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
