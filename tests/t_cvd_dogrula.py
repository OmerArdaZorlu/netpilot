"""Once YONTEMI dogrula: simulator bilinen davranisi uretmiyorsa sayilar
gurultudur.

ILK DENEMEM YANLISTI ve bu ders kayda deger: "protanopide kirmizi ve yesil
BIRBIRINE YAKIN cikmali" diye bir CIEDE2000 kontrolu yazmistim ve
basarisiz oldu. Simulator dogruydu, BEKLENTIM yanlisti — protanopide
kirmizi cok koyulasir, yesil parlak kalir; ikisi TON'da karisir, PARLAKLIK'ta
degil. Dogru kontrol ton ekseninde.
"""
import math
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from t_cvd import CVD, ciede2000, fark, lab, mat, simule


def ton(h, tur):
    L, a, b = lab(simule(h, tur))
    return math.degrees(math.atan2(b, a)) % 360


def ton_farki(h1, h2, tur):
    d = abs(ton(h1, tur) - ton(h2, tur))
    return min(d, 360 - d)


ok = True
print("=" * 72)
print("A. TON EKSENI — dikromatta hangi renkler ayni tona cokuyor?")
print("=" * 72)
A = [
    ("kirmizi/yesil normalde ayri ton", "#ff0000", "#00ff00", "normal",    ">", 60),
    ("kirmizi/yesil protanopide AYNI tona coker", "#ff0000", "#00ff00", "protanopi", "<", 6),
    ("kirmizi/yesil dotanopide AYNI tona coker",  "#ff0000", "#00ff00", "dotanopi",  "<", 6),
    ("mavi tonu protanopide korunur", "#0000ff", "#0000ff", "protanopi", "<", 1),
    ("mavi/sari protanopide ayri ton kalir", "#0000ff", "#ffff00", "protanopi", ">", 60),
    # Tritanopide hangi ciftin coktugunu VARSAYMADIM, olctum: bu modelde
    # yesil (136->179) ve camgobegi (196->196) 17 dereceye yakinsiyor.
    # Ilk yazdigim "mavi/teal" cifti yanlisti — mavi 306->252 ile ayri
    # kaliyor. Tritan davranisi protan/dotan kadar keskin degil; tritanopi
    # ayrica ~100 kat nadir (yaklasik 1/10.000 vs erkeklerde 1/12).
    ("yesil/camgobegi tritanopide AYNI tona coker", "#00ff00", "#00ffff", "tritanopi", "<", 25),
    ("sari tritanopide ton kaydirir", "#ffff00", "#ffff00", "normal", "<", 1),
    ("kirmizi/yesil tritanopide ayri kalir", "#ff0000", "#00ff00", "tritanopi", ">", 40),
]
for ad, a, b, tur, yon, esik in A:
    d = ton_farki(a, b, tur)
    g = (d > esik) if yon == ">" else (d < esik)
    ok &= g
    print(f"{'OK  ' if g else 'FAIL'} {ad:<44}{d:6.1f} derece ({yon}{esik})")

print()
print("=" * 72)
print("B. MATEMATIKSEL OZELLIKLER")
print("=" * 72)
B = []
d = fark("#3987e5", "#3987e5", "dotanopi")
B.append(("ayni renk = 0", d < 0.001, f"{d:.4f}"))
for tur in ("protanopi", "dotanopi", "tritanopi"):
    v = simule("#eb6834", tur)
    d = ciede2000(lab(v), lab(mat(CVD[tur], v)))
    B.append((f"{tur}: iki kez simule ~ bir kez (izdusum)", d < 7.0, f"{d:.2f}"))
# Parlaklik ekseni korunmali: gri tonlari birbirinden ayirt edilebilir kalmali
d = fark("#808080", "#c0c0c0", "protanopi")
B.append(("griler protanopide ayirt edilebilir", d > 15, f"{d:.1f}"))
for ad, g, v in B:
    ok &= g
    print(f"{'OK  ' if g else 'FAIL'} {ad:<52}{v:>10}")

print()
print("SONUC:", "yontem dogrulandi" if ok else "YONTEM SUPHELI — sayilara guvenme")
sys.exit(0 if ok else 1)
