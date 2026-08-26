"""Iki ayri soru, ayri ayri cevaplaniyor."""
import itertools, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from t_cvd import (ACIK, ACIK_ESKI, KOYU, KOYU_ESKI, KUMELER, OKABE, TURLER,
                   en_yakin, fark)

print("=" * 76)
print("SORU 1 — KONTRAST DUZELTMELERIM RENK KORLUGUNU BOZDU MU?")
print("(degisen tek renk `--muted`: #898781 -> #726f69, yalniz ACIK temada)")
print("=" * 76)
print(f"{'tema':<6}{'tur':<11}{'kume':<26}{'eski':>8}{'yeni':>8}{'fark':>8}")
print("-" * 76)
gerileme = []
for tema, yeni, eski in (("acik", ACIK, ACIK_ESKI), ("koyu", KOYU, KOYU_ESKI)):
    for tur in TURLER:
        for ad, k in KUMELER.items():
            if "muted" not in k:
                continue
            e, _ = en_yakin(eski, k, tur)
            y, _ = en_yakin(yeni, k, tur)
            d = y - e
            im = "" if d >= -0.05 else "  <- GERILEME"
            if d < -0.05:
                gerileme.append((tema, tur, ad, e, y))
            print(f"{tema:<6}{tur:<11}{ad:<26}{e:8.1f}{y:8.1f}{d:+8.1f}{im}")
print("-" * 76)
print("GERILEME YOK — degisiklik hicbir kumede ayirt edilebilirligi dusurmedi"
      if not gerileme else f"GERILEME: {len(gerileme)} kume")

print()
print("=" * 76)
print("SORU 2 — MUTLAK DURUM: referansa gore neredeyiz?")
print("Olcut Okabe-Ito (CVD icin OZELLIKLE tasarlanmis 5 renk).")
print("Kendi uydurdugum bir esik degil: 'referans ne yapiyorsa o iyidir'.")
print("=" * 76)
print(f"{'tur':<12}{'Okabe-Ito':>11}{'biz-acik':>11}{'biz-koyu':>11}   yorum")
print("-" * 76)
SINIF = KUMELER["trafik siniflari"]
for tur in TURLER:
    o, _ = en_yakin(OKABE, list(OKABE), tur)
    a, ca = en_yakin(ACIK, SINIF, tur)
    k, ck = en_yakin(KOYU, SINIF, tur)
    kotu = min(a, k)
    yorum = ("referans kadar iyi" if kotu >= o
             else f"referansin %{(1-kotu/o)*100:.0f} altinda")
    print(f"{tur:<12}{o:11.1f}{a:11.1f}{k:11.1f}   {yorum}")

print()
print("En zayif ciftler (trafik siniflari):")
for tema, palet in (("acik", ACIK), ("koyu", KOYU)):
    for tur in TURLER[1:]:
        d, c = en_yakin(palet, SINIF, tur)
        print(f"  {tema}/{tur:<11}{c[0]}/{c[1]:<4}{d:6.1f}")

print()
print("=" * 76)
print("DIGER KUMELER — referansin en kotusu (protan/dotan) esik alinirsa")
print("=" * 76)
o_min = min(en_yakin(OKABE, list(OKABE), t)[0] for t in ("protanopi", "dotanopi"))
print(f"referans esigi (Okabe-Ito protan/dotan en kotusu): {o_min:.1f}")
print("-" * 76)
alt = []
for tema, palet in (("acik", ACIK), ("koyu", KOYU)):
    for tur in TURLER:
        for ad, k in KUMELER.items():
            d, c = en_yakin(palet, k, tur)
            if d < o_min:
                alt.append((tema, tur, ad, c, d))
for tema, tur, ad, c, d in alt:
    print(f"  {tema}/{tur:<11}{ad:<26}{c[0]}/{c[1]:<10}{d:6.1f}")
print(f"\nreferans esiginin altinda kalan: {len(alt)}")
