"""Talep tahmini: gercek talebi BILDIGIMIZ bir kurguda olculuyor.

Simulator gercek aglardaki kisitlanmayi uretmiyor (akislari dogal hizlarinda
uretiyor, hat tavani uygulamiyor). O yuzden kisitlanmayi burada kendimiz
kuruyoruz:

    gercek talep -> [hat tavani, max-min adil pay] -> olculen

Sonra olculeni tahminciye veriyoruz ve gercek talebi geri bulabiliyor mu diye
bakiyoruz. Gercegi biliyoruz cunku onu biz yazdik.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.traffic.demand import DemandEstimator

ok = True
def hata(m):
    global ok
    ok = False
    print("   !! HATA:", m)


def maxmin(talepler, kapasite):
    """Tikanik hatta TCP'nin yaklastigi paylasim."""
    kalan = dict(talepler)
    verilen = {k: 0.0 for k in kalan}
    bos = kapasite
    while bos > 1e-9 and kalan:
        pay = bos / len(kalan)
        doyan = [k for k, v in kalan.items() if v <= pay + 1e-9]
        if not doyan:
            for k in list(kalan):
                verilen[k] += pay
            bos = 0.0
            break
        for k in doyan:
            verilen[k] += kalan[k]
            bos -= kalan[k]
            del kalan[k]
    return verilen


# --------------------------------------------------------------- kurgu
#
# Uc cihaz. Gercek talepleri sabit; hat kapasitesi gun icinde degisiyor
# (gece bos, gunduz dolu) — gercek aglarda oldugu gibi.
GERCEK = {"ws-01": 60.0, "ws-02": 40.0, "srv-yedek": 200.0}
KAPASITE_BOS = 500.0     # gece: kimse kisitlanmiyor
KAPASITE_DOLU = 100.0    # gunduz: uc cihaz 100 Mbps'i paylasiyor

est = DemandEstimator()
SAAT = 3600.0
t = 0.0

print("=" * 76)
print("ASAMA 1 — HAT BOS (gece). Olculen = gercek talep.")
print("=" * 76)
for _ in range(20):
    olculen = maxmin(GERCEK, KAPASITE_BOS)
    for h, v in olculen.items():
        est.observe(h, "up", v, congested=False, ts=t)
    t += 60.0
for h in GERCEK:
    p = est.profile(h, "up")
    print(f"   {h:<12} gercek {GERCEK[h]:6.1f} | ogrenilen tepe {p.peak_mbps:6.1f}")
    if abs(p.peak_mbps - GERCEK[h]) > 0.5:
        hata(f"{h}: bos saatte tepe yanlis ogrenildi")

print()
print("=" * 76)
print("ASAMA 2 — HAT DOLDU (gunduz). Olculen artik gercegi gostermiyor.")
print("=" * 76)
t += 6 * SAAT
olculen = maxmin(GERCEK, KAPASITE_DOLU)
print(f"   kapasite {KAPASITE_DOLU:.0f} Mbps, gercek talep "
      f"{sum(GERCEK.values()):.0f} Mbps\n")
print(f"   {'cihaz':<12}{'gercek':>8}{'olculen':>9}{'ESKI SISTEM':>13}"
      f"{'TAHMIN':>9}{'hata':>8}{'dayanak':>14}")
print("   " + "-" * 76)
eski_hata = yeni_hata = 0.0
for h, gercek in GERCEK.items():
    m = olculen[h]
    e = est.estimate(h, "up", m, congested=True, ts=t,
                     fair_share_mbps=KAPASITE_DOLU / len(GERCEK))
    est.observe(h, "up", m, congested=True, ts=t)
    eski_hata += abs(m - gercek)
    yeni_hata += abs(e.mbps - gercek)
    print(f"   {h:<12}{gercek:>8.1f}{m:>9.1f}{m:>13.1f}"
          f"{e.mbps:>9.1f}{e.mbps - gercek:>+8.1f}{e.basis:>14}")
print("   " + "-" * 76)
print(f"   TOPLAM MUTLAK HATA:  eski sistem {eski_hata:6.1f} Mbps  ->  "
      f"tahminci {yeni_hata:6.1f} Mbps")
if yeni_hata >= eski_hata:
    hata("tahminci eski sistemden iyi degil")

print()
print("=" * 76)
print("ASAMA 3 — GERI CEKME LISTESI: eski sistem kor, tahminci goruyor")
print("=" * 76)
print(f"   {'cihaz':<12}{'eski: eksik':>14}{'tahmin: eksik':>16}")
eski_toplam = yeni_toplam = 0.0
for h, gercek in GERCEK.items():
    m = olculen[h]
    e = est.estimate(h, "up", m, congested=True, ts=t,
                     fair_share_mbps=KAPASITE_DOLU / len(GERCEK))
    eski = max(0.0, m - m)                 # olculen talep = olculen verilen
    yeni = max(0.0, e.mbps - m)
    eski_toplam += eski; yeni_toplam += yeni
    print(f"   {h:<12}{eski:>14.1f}{yeni:>16.1f}")
print(f"   {'TOPLAM':<12}{eski_toplam:>14.1f}{yeni_toplam:>16.1f}")
if eski_toplam > 0.01:
    hata("eski sistem eksik gormemeliydi (olculen=verilen)")
if yeni_toplam < 50:
    hata("tahminci gercek eksigi gormedi")

print()
print("=" * 76)
print("ASAMA 4 — GUVENLIK FRENLERI")
print("=" * 76)

# 4a. sisme tavani
e = est.estimate("srv-yedek", "up", 5.0, congested=True, ts=t,
                 fair_share_mbps=33.3)
print(f"   BOSTA: 5 Mbps cekiyor, payi 33.3 (tepesi 200) -> tahmin {e.mbps:.1f}, "
      f"dayanak '{e.basis}'")
if e.mbps > 5.0 + 0.01:
    hata("bostaki cihaza hayali talep uydurdu")
else:
    print("     OK  paya dayanmiyor -> tepe kullanilmadi, sisme yok")

e2 = est.estimate("srv-yedek", "up", 33.3, congested=True, ts=t,
                  fair_share_mbps=33.3)
print(f"   DAYANMIS: 33.3 cekiyor, payi 33.3 (tepesi 200) -> tahmin {e2.mbps:.1f}, "
      f"dayanak '{e2.basis}'")
if e2.mbps < 190:
    hata("paya dayanan cihazin tepesi kullanilmadi")
else:
    print("     OK  paya dayaniyor -> gercek talep (200) geri bulundu")

# 4b. yaslanma
t_eski = t + 25 * SAAT
e = est.estimate("ws-01", "up", 10.0, congested=True, ts=t_eski,
                 fair_share_mbps=10.0)
print(f"   25 saat sonra (tepe 60) -> tahmin {e.mbps:.1f} Mbps, "
      f"dayanak '{e.basis}'")
if e.mbps > 10.0 + 0.01:
    hata("yaslanmis tepe hala kullaniliyor")
else:
    print("     OK  eskimis tepe dustu")

# 4c. hic verisi olmayan cihaz
e = est.estimate("yeni-cihaz", "up", 30.0, congested=True, ts=t,
                 fair_share_mbps=30.0)
print(f"   hic gozlenmemis cihaz -> tahmin {e.mbps:.1f}, dayanak '{e.basis}', "
      f"guven {e.confidence}")
if e.mbps != 30.0:
    hata("verisi olmayan cihaz icin sayi uydurdu")
if e.confidence > 0.3:
    hata("verisi olmayan cihaza yuksek guven verdi")

# 4d. bizim koydugumuz tavana yapisma
e = est.estimate("ws-02", "up", 20.0, congested=True, ts=t,
                 fair_share_mbps=40.0, capped_at_mbps=20.0)
print(f"   20 Mbps tavana yapismis -> tahmin {e.mbps:.1f}, dayanak '{e.basis}'")
if e.mbps <= 20.0:
    hata("tavana yapisan cihazin talebi tavanin ustunde olmaliydi")

# 4f. pay bilgisi HIC yoksa sisirme olmamali
e = est.estimate("srv-yedek", "up", 5.0, congested=True, ts=t)
print(f"   pay bilgisi yok, 5 Mbps (tepesi 200) -> tahmin {e.mbps:.1f}, "
      f"dayanak '{e.basis}'")
if e.mbps > 5.0 + 0.01:
    hata("pay bilgisi yokken sisirme yapti")
else:
    print("     OK  sinyal yoksa olculende kaliyor")

# 4e. hat bosken tahmin = olculen (sisirme yok)
e = est.estimate("srv-yedek", "up", 12.0, congested=False, ts=t)
print(f"   hat bos, 12 Mbps cekiyor -> tahmin {e.mbps:.1f}, dayanak '{e.basis}'")
if e.mbps != 12.0:
    hata("hat bosken sisirme yapti")

print()
print("=" * 76)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
