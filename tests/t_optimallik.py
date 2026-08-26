"""COZUCU GERCEKTEN OPTIMAL MI?

"Duzgun optimize ediyor" laf; olculebilir hale getirelim. Dort sart:

1. TAVANI BULUYOR MU
   Politika notrken (tek sinif, taban yok) cozucunun gecirdigi toplam,
   agin teorik max-flow'una esit olmali. Az ise cozucu kotudur.

2. BOSA KAPASITE BIRAKIYOR MU
   Bir talep karsilanmamisken, o talebin hedefine giden bir yolda bos
   kapasite kalmissa bu KESIN hata. En agir kusur bu olurdu.

3. TALEPTEN FAZLA VERIYOR MU
   Hicbir talep istediginden fazla almamali.

4. ONCELIK ISLIYOR MU
   Ust sinifin karsilanma orani alt siniftan dusuk olmamali (taban
   garantileri disinda).

Hepsi 15 rastgele agda olculuyor.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

import random

import numpy as np
from scipy.optimize import linprog

from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer, EPS
from ntc.traffic.flowpolicy import DEFAULT_POLICY, FlowPolicy

ok = True
def hata(m):
    global ok
    ok = False
    print("   !! HATA:", m)


def maxflow(topo, kaynaklar, hedef):
    """Teorik tavan: kaynaklardan hedefe akabilecek en buyuk toplam.

    Ayri ve bagimsiz bir LP — cozucunun kendi kodunu kullanmiyor ki
    "kendi kendini dogrulama" tuzagina dusmeyelim.
    """
    kenarlar = [e for e in topo.edges if e.effective_mbps > 0]
    if not kenarlar:
        return 0.0
    dugumler = topo.nodes
    di = {n: i for i, n in enumerate(dugumler)}
    n_e, n_n = len(kenarlar), len(dugumler)

    # degiskenler: her kenarda akis
    c = np.zeros(n_e)
    for i, e in enumerate(kenarlar):
        if e.dst == hedef:
            c[i] = -1.0          # hedefe varan akisi maksimize et

    # korunum: kaynak ve hedef disindaki her dugumde giren = cikan
    rows, vals, cols = [], [], []
    b_eq = []
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
    bounds = [(0.0, e.effective_mbps) for e in kenarlar]
    res = linprog(c, A_eq=A_eq if sat else None,
                  b_eq=np.array(b_eq) if sat else None,
                  bounds=bounds, method="highs")
    return -res.fun if res.success else 0.0


def notr_politika():
    """Tek sinif gibi davranan hedef: taban yok, sira farketmiyor."""
    p, _ = FlowPolicy.validate({
        "class_order": list(DEFAULT_POLICY.class_order),
        "floors": {c: 0.0 for c in DEFAULT_POLICY.floors},
    })
    return p


print("=" * 78)
print(f"{'ag':>3} {'dugum':>5} {'cikis':>5} | {'teorik tavan':>13} {'cozucu':>9} "
      f"{'oran':>6} | {'bos kapasite':>12} {'asim':>5}")
print("=" * 78)

oranlar = []
for tohum in range(1, 16):
    rnd = random.Random(tohum * 17)
    topo = Topology.generate(seed=tohum, sites=rnd.randint(1, 4),
                             egresses=rnd.randint(1, 3),
                             downlink_mbps=rnd.choice([200.0, 400.0]),
                             uplink_mbps=rnd.choice([40.0, 80.0]))

    # Kapasiteyi asan yukleme talebi: tavana dayansin.
    hostlar = [f"pc-{i}" for i in range(6)]
    talep = [Demand(h, INTERNET, TC.BULK, 200.0, direction="up")
             for h in hostlar]
    kaynaklar = {topo.attach_point(h) for h in hostlar}

    tavan = maxflow(topo, kaynaklar, INTERNET)
    plan = FlowOptimizer(topo, notr_politika()).solve(talep)
    gecen = sum(a.granted_mbps for a in plan.allocations)

    # --- 2. bosa kapasite: cikis kenarlarinda kalan
    bos = 0.0
    for e in topo.edges:
        if e.kind == "wan" and e.dst == INTERNET:
            kul = plan.edge_load_mbps.get((e.src, e.dst), 0.0)
            bos += max(0.0, e.effective_mbps - kul)
    karsilanmayan = sum(a.shortfall_mbps for a in plan.allocations)

    # --- 3. talepten fazla
    asim = sum(1 for a in plan.allocations
               if a.granted_mbps > a.demand.mbps + 1e-6)

    oran = gecen / tavan if tavan > 0 else float("nan")
    oranlar.append(oran)

    if tavan > 0 and gecen < tavan - 0.5:
        hata(f"ag {tohum}: tavanin altinda ({gecen:.1f} < {tavan:.1f})")
    if karsilanmayan > 0.5 and bos > 0.5:
        hata(f"ag {tohum}: {karsilanmayan:.1f} Mbps karsilanmamis ama "
             f"{bos:.1f} Mbps cikis kapasitesi bos")
    if asim:
        hata(f"ag {tohum}: {asim} talep istediginden fazla aldi")

    print(f"{tohum:>3} {len(topo.nodes):>5} "
          f"{len([e for e in topo.edges if e.kind=='wan' and e.dst==INTERNET]):>5} | "
          f"{tavan:>13.1f} {gecen:>9.1f} {oran:>5.2f}x | "
          f"{bos:>12.2f} {asim:>5}")

print("=" * 78)
gecerli = [o for o in oranlar if o == o]
print(f"teorik tavana ulasma: ortalama {sum(gecerli)/len(gecerli):.4f}x  "
      f"(en dusuk {min(gecerli):.4f}x)")

# ------------------------------------------------------------- 4. oncelik
print()
print("=" * 78)
print("ONCELIK: ust sinif once mi doyuyor?")
print("=" * 78)
topo = Topology.generate(seed=5, sites=2, egresses=2,
                         downlink_mbps=300.0, uplink_mbps=60.0)
talep = []
for sinif in [TC.REALTIME, TC.INTERACTIVE, TC.STREAMING, TC.BULK, TC.BACKGROUND]:
    talep.append(Demand(f"pc-{sinif.value}", INTERNET, sinif, 40.0,
                        direction="up"))
plan = FlowOptimizer(topo, DEFAULT_POLICY).solve(talep)
kapasite = sum(e.effective_mbps for e in topo.edges
               if e.kind == "wan" and e.dst == INTERNET)
print(f"  kapasite {kapasite:.0f} Mbps, talep {sum(d.mbps for d in talep):.0f} Mbps")
print(f"  {'sinif':<14}{'talep':>8}{'verilen':>9}{'oran':>8}")
onceki_oran = None
for a in sorted(plan.allocations,
                key=lambda x: DEFAULT_POLICY.priority_of(x.demand.traffic_class.value)):
    print(f"  {a.demand.traffic_class.value:<14}{a.demand.mbps:>8.1f}"
          f"{a.granted_mbps:>9.1f}{a.satisfaction:>8.2f}")
    if onceki_oran is not None and a.satisfaction > onceki_oran + 0.02:
        hata(f"{a.demand.traffic_class.value} ustundekinden daha cok doydu")
    onceki_oran = a.satisfaction

# ------------------------------------------------------------ 5. kararlilik
print()
print("=" * 78)
print("KARARLILIK: ayni girdi -> ayni cikti mi?")
print("=" * 78)
imzalar = set()
for _ in range(5):
    p = FlowOptimizer(topo, DEFAULT_POLICY).solve(talep)
    imzalar.add(tuple(round(a.granted_mbps, 6) for a in
                      sorted(p.allocations, key=lambda x: x.demand.key)))
print(f"  5 kosuda {len(imzalar)} farkli sonuc")
if len(imzalar) != 1:
    hata("ayni girdi farkli cikti veriyor")

print()
print("=" * 78)
print("SONUC:", "COZUCU OPTIMAL" if ok else "KUSUR VAR")
print("=" * 78)
sys.exit(0 if ok else 1)
