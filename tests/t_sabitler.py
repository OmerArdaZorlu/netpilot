"""COZUCUNUN SABITLERI RASTGELE MIMARIDE DOGRU MU?

Taban olcegi hatasi (4i) tam bu kategoriydi: simetrik agda dogru, asimetrikte
10 kat sapiyor, ve tek topolojide test edildigi icin gorunmuyordu. Ayni tuzak
kalan sabitlerde de kurulu olabilir. Burada hepsi rastgele mimaride olculuyor.

Olculen sabitler:
  A. path_weight / PATH_PENALTY_BUDGET  - yol tercihi talebi karsilamanin
     onune gecebilir mi? (bir kez gecti: cost_weight yuksekken 80 Mbps atildi)
  B. class_order                        - herhangi bir siralamada oncelik
     gercekten isliyor mu?
  C. FLOOR_PROFILES (4 profil)          - her profil her agda tabanini
     tutturuyor mu?
  D. INFLATION_CAP / PRESSING_RATIO     - tahminci bosta olan cihaza talep
     uyduruyor mu, baskidakini goruyor mu?
  E. belirlenimcilik                    - ayni girdi ayni cikti
  F. temel kisitlar                     - talepten fazla verme, LAN/WAN ayrimi
"""
import itertools
import random
import sys

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.optimize import linprog

from ntc.core.models import TrafficClass as TC
from ntc.traffic.demand import DemandEstimator, INFLATION_CAP_PRESSING
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.flowpolicy import (CLASSES, DEFAULT_POLICY, FLOOR_PROFILES,
                                    FlowPolicy, WEIGHT_LEVELS)
from ntc.traffic.topology import INTERNET, Topology

TOL = 0.5
kusurlar: list[str] = []


def hata(bolum: str, m: str) -> None:
    kusurlar.append(f"{bolum}: {m}")


# --------------------------------------------------------------- hakem

def maxflow(topo, kaynaklar, hedef):
    """Bagimsiz max-flow LP — cozucunun kodunu kullanmiyor."""
    kenarlar = [e for e in topo.edges if e.effective_mbps > 0]
    if not kenarlar:
        return 0.0
    dugumler = topo.nodes
    n_e = len(kenarlar)
    c = np.zeros(n_e)
    for i, e in enumerate(kenarlar):
        if e.dst == hedef:
            c[i] = -1.0
    rows, cols, vals, b_eq = [], [], [], []
    sat = 0
    for n in dugumler:
        if n == hedef or n in kaynaklar:
            continue
        for i, e in enumerate(kenarlar):
            if e.dst == n:
                rows.append(sat); cols.append(i); vals.append(1.0)
            elif e.src == n:
                rows.append(sat); cols.append(i); vals.append(-1.0)
        b_eq.append(0.0); sat += 1
    A_eq = np.zeros((sat, n_e))
    for r, cc, v in zip(rows, cols, vals):
        A_eq[r, cc] = v
    # Hedeften CIKAN ve kaynaga GIREN kenarlar kapali. Bunlar acik kalirsa
    # hedefte korunum uygulanmadigi icin akis `hedef -> komsu -> hedef`
    # dongusune girip sahte kapasite uretiyor (olculdu: 200 Mbps'lik agda
    # 2500 Mbps "tavan" cikti).
    bounds = []
    for e in kenarlar:
        kapali = e.src == hedef or e.dst in kaynaklar
        bounds.append((0.0, 0.0 if kapali else e.effective_mbps))
    res = linprog(c, A_eq=A_eq if sat else None,
                  b_eq=np.array(b_eq) if sat else None,
                  bounds=bounds, method="highs")
    return -res.fun if res.success else 0.0


def politika(**kw):
    ham = {"class_order": list(DEFAULT_POLICY.class_order),
           "floors": {c: 0.0 for c in CLASSES}}
    ham.update(kw)
    p, sorun = FlowPolicy.validate(ham)
    if p is None:
        raise AssertionError(f"test politikasi gecersiz: {sorun}")
    return p


def zorlu_ag(tohum, site, cikis, d, u, sayacli=True, bozuk=True):
    """Rastgele ag + sayacli ve bozuk bacak zorlamasi.

    Uretecin kendisi bacaklarin bir kismini sayacli yapiyor ama sagligi hep
    1.0 birakiyor. Yol tercihi sabitleri asil burada sinaniyor: pahali ve
    bozuk bacak varken cozucu trafigi atmadan tasiyabiliyor mu.
    """
    from dataclasses import replace
    t = Topology.generate(seed=tohum, sites=site, egresses=cikis,
                          downlink_mbps=d, uplink_mbps=u)
    if cikis < 2:
        return t
    e = []
    for x in t.edges:
        if x.kind == "wan" and "cikis-2" in (x.src, x.dst):
            e.append(replace(x, cost_per_gb=5.0 if sayacli else 0.0))
        elif x.kind == "wan" and "cikis-3" in (x.src, x.dst):
            e.append(replace(x, health=0.45 if bozuk else 1.0))
        else:
            e.append(x)
    return Topology(edges=e, default_access=t.default_access,
                    access_nodes=t.access_nodes)


AGLAR = [(501, 1, 2, 200., 20.), (502, 2, 3, 300., 40.), (503, 3, 4, 500., 50.),
         (504, 1, 5, 150., 15.), (505, 4, 2, 800., 80.), (506, 2, 4, 1000., 100.),
         (507, 5, 3, 90., 9.),   (508, 3, 2, 400., 40.)]


# ================================================================ A
print("=" * 78)
print("A. Yol tercihi talebi karsilamanin onune geciyor mu?")
print("   (27 agirlik birlesimi x 8 rastgele ag, hepsinde sayacli+bozuk bacak)")
print("=" * 78)

seviyeler = ["dusuk", "normal", "yuksek"]
en_kotu = (1.0, "")
sayac = 0
for tohum, s, c, d, u in AGLAR:
    t = zorlu_ag(tohum, s, c, d, u)
    giris = t.attach_point("pc")
    tavan = maxflow(t, {INTERNET}, giris)
    talep = [Demand(f"pc{i}", giris, TC.BULK, d * 3 / 4, src=INTERNET,
                    direction="down") for i in range(4)]
    for lat, cost, hea in itertools.product(seviyeler, repeat=3):
        p = politika(latency_weight=lat, cost_weight=cost, health_weight=hea)
        plan = FlowOptimizer(t, p).solve(talep)
        gecen = sum(a.granted_mbps for a in plan.allocations)
        oran = gecen / tavan if tavan > 0 else 1.0
        sayac += 1
        if oran < en_kotu[0]:
            en_kotu = (oran, f"t{tohum} {lat}/{cost}/{hea} "
                             f"{gecen:.1f}/{tavan:.1f}")
        if oran < 1.0 - 1e-3:
            hata("A", f"t{tohum} agirlik {lat}/{cost}/{hea}: "
                      f"{gecen:.1f} < tavan {tavan:.1f} (%{oran * 100:.1f})")
print(f"   {sayac} birlesim denendi")
print(f"   en kotu oran: {en_kotu[0]:.4f}" +
      (f"  ({en_kotu[1]})" if en_kotu[1] else ""))
print("   sonuc:", "GECTI — hicbir agirlik trafik attirmadi"
      if en_kotu[0] >= 1.0 - 1e-3 else "KALDI")

# ================================================================ B
print()
print("=" * 78)
print("B. Sinif sirasi: herhangi bir siralamada oncelik isliyor mu?")
print("   (20 rastgele permutasyon x 8 ag, taban kapali ki saf sira olculsun)")
print("=" * 78)

rnd = random.Random(7)
permler = [list(CLASSES)]
while len(permler) < 20:
    p = list(CLASSES); rnd.shuffle(p)
    if p not in permler:
        permler.append(p)

ihlal = 0
kontrol = 0
for tohum, s, c, d, u in AGLAR:
    t = zorlu_ag(tohum, s, c, d, u)
    giris = t.attach_point("pc")
    talep = [Demand(f"pc-{k}", giris, TC(k), d * 0.6, src=INTERNET,
                    direction="down") for k in CLASSES]
    for perm in permler:
        pol = politika(class_order=perm)
        plan = FlowOptimizer(t, pol).solve(talep)
        oranlar = {a.demand.traffic_class.value:
                   a.granted_mbps / a.demand.mbps for a in plan.allocations}
        for i in range(len(perm) - 1):
            ust, alt = perm[i], perm[i + 1]
            kontrol += 1
            if oranlar.get(ust, 0.0) + 1e-6 < oranlar.get(alt, 0.0):
                ihlal += 1
                if ihlal <= 3:
                    hata("B", f"t{tohum} sira {' > '.join(perm)}: "
                              f"{ust} %{oranlar[ust]*100:.0f} < "
                              f"{alt} %{oranlar[alt]*100:.0f}")
print(f"   {len(permler)} permutasyon x {len(AGLAR)} ag = {kontrol} ikili kontrol")
print(f"   ihlal: {ihlal}")
print("   sonuc:", "GECTI" if ihlal == 0 else "KALDI")

# ================================================================ C
print()
print("=" * 78)
print("C. Taban profilleri: 4 profil x 8 ag, her sinif tabanini aliyor mu?")
print("=" * 78)

c_ihlal = 0
c_kontrol = 0
for ad, profil in FLOOR_PROFILES.items():
    for tohum, s, c, d, u in AGLAR:
        t = zorlu_ag(tohum, s, c, d, u)
        giris = t.attach_point("pc")
        pol, sorun = FlowPolicy.validate({
            "class_order": list(DEFAULT_POLICY.class_order),
            "floor_profile": ad})
        assert pol is not None, sorun
        # Her sinif kapasitenin yarisini istiyor -> hepsi tabanindan fazla
        talep = ([Demand(f"d-{k}", giris, TC(k), d * 0.5, src=INTERNET,
                         direction="down") for k in CLASSES]
                 + [Demand(f"u-{k}", INTERNET, TC(k), u * 0.5,
                           direction="up") for k in CLASSES])
        opt = FlowOptimizer(t, pol)
        plan = opt.solve(talep)
        verilen = {(a.demand.device): a.granted_mbps for a in plan.allocations}
        # Taban NOMINAL degil ETKIN kapasiteye gore olculmeli: bozuk bacak
        # `effective_mbps`'i dusuruyor ve var olmayan kapasiteden garanti
        # verilemez. Ilk olcumde nominal kullanip 59 sahte ihlal urettim;
        # oranlarin hepsi tam 0.781 cikinca (bozuk bacagin payi) anlasildi.
        for k in CLASSES:
            for on, kap in (("d", opt._capacity_for("down")),
                            ("u", opt._capacity_for("up"))):
                bek = pol.floor_of(k) * kap
                got = verilen.get(f"{on}-{k}", 0.0)
                c_kontrol += 1
                if got + TOL < bek:
                    c_ihlal += 1
                    if c_ihlal <= 4:
                        hata("C", f"{ad}/t{tohum} {on}/{k}: {got:.2f} < "
                                  f"taban {bek:.2f}")
print(f"   {c_kontrol} taban kontrolu ({len(FLOOR_PROFILES)} profil x "
      f"{len(AGLAR)} ag x 5 sinif x 2 yon)")
print(f"   ihlal: {c_ihlal}")
print("   sonuc:", "GECTI" if c_ihlal == 0 else "KALDI")

# ================================================================ D
print()
print("=" * 78)
print("D. Talep tahmini sabitleri: bostakine talep uyduruyor mu?")
print("=" * 78)

d_ihlal = 0
senaryolar = [
    # (ad, tepe, olculen, adil pay, tikanik, beklenen)
    ("bosta cihaz, tikanik hat",      60.0,  5.0, 50.0, True,  "olculen"),
    ("baskida cihaz, tikanik hat",    60.0, 48.0, 50.0, True,  "tepe"),
    ("tam sinirda (%90)",             60.0, 45.0, 50.0, True,  "tepe"),
    ("sinirin hemen altinda (%89)",   60.0, 44.5, 50.0, True,  "olculen"),
    ("adil pay bilinmiyor",           60.0, 20.0, None, True,  "olculen"),
    ("hat bos",                       60.0, 30.0, 50.0, False, "olculen"),
    ("gecmis yok",                     0.0, 12.0, 50.0, True,  "olculen"),
]
for ad, tepe, olculen, pay, tikanik, bekle in senaryolar:
    est = DemandEstimator()
    if tepe > 0:
        est.observe("pc", "down", tepe, congested=False, ts=0.0)
    e = est.estimate("pc", "down", olculen, congested=tikanik, ts=3600.0,
                     fair_share_mbps=pay)
    dogru = (abs(e.mbps - olculen) < 0.01 if bekle == "olculen"
             else abs(e.mbps - tepe) < 0.01)
    if not dogru:
        d_ihlal += 1
        hata("D", f"{ad}: {e.mbps:.1f} Mbps ({e.basis}), beklenen {bekle} "
                  f"(olculen {olculen}, tepe {tepe})")
    print(f"   {'OK  ' if dogru else 'FAIL'} {ad:<32} -> {e.mbps:>6.1f} Mbps "
          f"({e.basis})")

# sisirme tavani
est = DemandEstimator()
est.observe("pc", "down", 900.0, congested=False, ts=0.0)
e = est.estimate("pc", "down", 5.0, congested=True, ts=100.0,
                 fair_share_mbps=5.0)
tavan = 5.0 * INFLATION_CAP_PRESSING
if e.mbps > tavan + 0.01:
    d_ihlal += 1
    hata("D", f"sisirme tavani asildi: {e.mbps:.1f} > {tavan:.1f}")
print(f"   {'OK  ' if e.mbps <= tavan + 0.01 else 'FAIL'} "
      f"{'sisirme tavani (x' + str(INFLATION_CAP_PRESSING) + ')':<32} -> "
      f"{e.mbps:>6.1f} Mbps (tavan {tavan:.0f})")
print("   sonuc:", "GECTI" if d_ihlal == 0 else "KALDI")

# ================================================================ E
print()
print("=" * 78)
print("E. Belirlenimcilik: ayni girdi 5 kez -> ayni cikti mi?")
print("=" * 78)
e_ihlal = 0
for tohum, s, c, d, u in AGLAR:
    t = zorlu_ag(tohum, s, c, d, u)
    giris = t.attach_point("pc")
    talep = [Demand(f"pc{i}", giris, TC(k), d * 0.4, src=INTERNET,
                    direction="down")
             for i, k in enumerate(CLASSES)]
    imzalar = set()
    for _ in range(5):
        plan = FlowOptimizer(t, DEFAULT_POLICY).solve(talep)
        imzalar.add(tuple(round(a.granted_mbps, 6) for a in plan.allocations))
    if len(imzalar) != 1:
        e_ihlal += 1
        hata("E", f"t{tohum}: 5 kosuda {len(imzalar)} farkli sonuc")
print(f"   {len(AGLAR)} ag x 5 kosu")
print(f"   ihlal: {e_ihlal}")
print("   sonuc:", "GECTI" if e_ihlal == 0 else "KALDI")

# ================================================================ F
print()
print("=" * 78)
print("F. Temel kisitlar: talepten fazla verme, LAN/WAN ayrimi")
print("=" * 78)
f_ihlal = 0
f_kontrol = 0
for tohum, s, c, d, u in AGLAR:
    t = zorlu_ag(tohum, s, c, d, u)
    giris = t.attach_point("pc")
    talep = [
        Demand("a", giris, TC.BULK, d * 2, src=INTERNET, direction="down"),
        Demand("b", giris, TC.REALTIME, 3.0, src=INTERNET, direction="down"),
        Demand("c", INTERNET, TC.BULK, u * 2, direction="up"),
        Demand("kam", "nvr", TC.STREAMING, 400.0, src=giris, direction="lan"),
    ]
    plan = FlowOptimizer(t, DEFAULT_POLICY).solve(talep)
    g = {a.demand.device: a.granted_mbps for a in plan.allocations}
    for a in plan.allocations:
        f_kontrol += 1
        if a.granted_mbps > a.demand.mbps + 1e-6:
            f_ihlal += 1
            hata("F", f"t{tohum} {a.demand.device}: talepten fazla "
                      f"{a.granted_mbps:.2f} > {a.demand.mbps:.2f}")
    # LAN trafigi internet kapasitesini yemiyor
    wan_down = g.get("a", 0.0) + g.get("b", 0.0)
    if wan_down > d + TOL:
        f_ihlal += 1
        hata("F", f"t{tohum}: WAN indirme {wan_down:.1f} > kapasite {d}")
    if g.get("kam", 0.0) <= d * 0.5 and g.get("kam", 0.0) < 100:
        f_ihlal += 1
        hata("F", f"t{tohum}: LAN talebi internet kapasitesine gore "
                  f"kisilmis ({g.get('kam', 0.0):.1f} Mbps)")
print(f"   {f_kontrol} tahsis kontrolu + {len(AGLAR) * 2} kapasite kontrolu")
print(f"   ihlal: {f_ihlal}")
print("   sonuc:", "GECTI" if f_ihlal == 0 else "KALDI")

# ================================================================ ozet
print()
print("=" * 78)
if kusurlar:
    print(f"KALDI — {len(kusurlar)} kusur")
    for k in kusurlar[:25]:
        print("  -", k)
    if len(kusurlar) > 25:
        print(f"  ... ve {len(kusurlar) - 25} tane daha")
    sys.exit(1)
print("HEPSI GECTI")
