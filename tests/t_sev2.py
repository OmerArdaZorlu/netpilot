"""Model aciklamasi, kaynagindaki dereceyi geri aliyor mu."""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.analyst import AIAnalyst

# Gercek kosudan alinan basliklar
facts = [
    {"title": "Yükleme hattında tıkanma", "severity": "medium", "evidence": "up_utilization=0.96"},
    {"title": "İndirme hattı doygun", "severity": "high", "evidence": "down_utilization=1.01"},
    {"title": "Doymuş bağlantı: sw-core->wan", "severity": "high", "evidence": "20/20 Mbps"},
]

cases = [
    # (modelin yazdigi baslik, beklenen derece)
    ("Yükleme hattındaki tıkanma", "medium"),      # ek farki — eski hali kaldi
    ("İndirme hattının doygunluğu", "high"),
    ("Doymuş bağlantı: sw-core->wan", "high"),
    ("Kediler neden miyavlar", "info"),            # alakasiz -> info kalmali
    ("Kritik güvenlik açığı", "info"),             # uydurma -> yukselemez
]

ok = True
for baslik, beklenen in cases:
    f = [{"title": baslik, "severity": "info", "evidence": ""}]
    got = AIAnalyst._attach_severity(f, facts)[0]["severity"]
    good = got == beklenen
    if not good: ok = False
    print(f"  {'OK  ' if good else 'FAIL'} {baslik:34} -> {got:7} (beklenen {beklenen})")

print("\nSONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
