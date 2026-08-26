"""Taban olcegi SEKLE gore de dogru mu?

t_floors.py duzeltmeyi HIZ ORANINA karsi dogrulamisti (300/40, 200/20, ...)
ama hepsi sites=1, egresses=1 — yani tek sira. Coklu cikisli ve cok siteli
agda taban olceginin dogru ciktigi hic olculmedi. Burasi o bosluk.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.flowpolicy import DEFAULT_POLICY

SEKILLER = [
    (1, 1, 200.0, 20.0), (1, 2, 200.0, 20.0), (1, 4, 200.0, 20.0),
    (3, 1, 300.0, 40.0), (3, 2, 300.0, 40.0), (3, 4, 300.0, 40.0),
    (5, 3, 1000.0, 100.0), (2, 5, 500.0, 500.0), (4, 2, 100.0, 10.0),
    (1, 3, 50.0, 50.0), (5, 5, 2000.0, 200.0), (2, 1, 80.0, 8.0),
]

kalan = []
print(f"{'site/cikis':<12}{'hat':<14}{'olcek d/u':<20}{'dns d':<14}{'dns u':<14}")
print("-" * 76)

for i, (s, c, d, u) in enumerate(SEKILLER):
    t = Topology.generate(seed=100 + i, sites=s, egresses=c,
                          downlink_mbps=d, uplink_mbps=u)
    o = FlowOptimizer(t)
    ol_d, ol_u = o._capacity_for("down"), o._capacity_for("up")

    giris = t.attach_point("pc")
    plan = FlowOptimizer(t, DEFAULT_POLICY).solve([
        Demand("yedek", giris, TC.BULK, d * 4, src=INTERNET, direction="down"),
        Demand("dns", giris, TC.BACKGROUND, d * 0.2, src=INTERNET, direction="down"),
        Demand("up-yedek", INTERNET, TC.BULK, u * 4, direction="up"),
        Demand("up-dns", INTERNET, TC.BACKGROUND, u * 0.2, direction="up"),
    ])
    g = {a.demand.device: a.granted_mbps for a in plan.allocations}
    pay = DEFAULT_POLICY.floor_of("background")
    bek_d, bek_u = pay * d, pay * u

    hata = []
    if abs(ol_d - d) > 0.51: hata.append(f"indirme olcegi {ol_d:.1f}!={d}")
    if abs(ol_u - u) > 0.51: hata.append(f"yukleme olcegi {ol_u:.1f}!={u}")
    if abs(g["dns"] - bek_d) > 0.51: hata.append(f"dns-down {g['dns']:.1f}!={bek_d:.1f}")
    if abs(g["up-dns"] - bek_u) > 0.51: hata.append(f"dns-up {g['up-dns']:.1f}!={bek_u:.1f}")
    # kapasite tam kullanilmali (talep kapasitenin cok ustunde)
    if abs(g["yedek"] + g["dns"] - d) > 1.0: hata.append(f"indirme toplam {g['yedek']+g['dns']:.1f}!={d}")
    if abs(g["up-yedek"] + g["up-dns"] - u) > 1.0: hata.append(f"yukleme toplam {g['up-yedek']+g['up-dns']:.1f}!={u}")

    im = "OK  " if not hata else "FAIL"
    print(f"{im} {s}s/{c}c{'':<4}{d:.0f}/{u:.0f}{'':<7}"
          f"{ol_d:.1f}/{ol_u:.1f}{'':<7}"
          f"{g['dns']:.2f} (bek {bek_d:.2f}){'':<1}"
          f"{g['up-dns']:.2f} (bek {bek_u:.2f})")
    if hata:
        kalan.append((f"{s}s/{c}c {d:.0f}/{u:.0f}", hata))

print("-" * 76)
if kalan:
    print(f"KALDI: {len(kalan)}/{len(SEKILLER)}")
    for ad, h in kalan:
        print(f"  {ad}: {'; '.join(h)}")
    sys.exit(1)
print(f"GECTI: {len(SEKILLER)}/{len(SEKILLER)} sekil")
