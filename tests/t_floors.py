"""Tabanlar her topolojide dogru olcekleniyor mu?

Bulunan hata: butun tabanlar `_egress_capacity()` ile olcekleniyordu ve o
yalniz dst==internet kenarlarini topluyor — yani YALNIZ YUKLEME. Indirme
kenarlari internet->wan yonunde oldugu icin hic sayilmiyordu.

Simetrik agda (100/100) dogru calisiyordu, o yuzden fark edilmemisti.
Asimetrik agda — yani gercek internet hatlarinin tamaminda — indirme
tabanlari kat kat kucuk cikiyordu.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.flowpolicy import DEFAULT_POLICY

ok = True
def check(n, got, want, tol=0.51):
    global ok
    g = abs(got - want) <= tol
    if not g: ok = False
    print(f"  {'OK  ' if g else 'FAIL'} {n}: {got:.2f} (beklenen {want:.2f})")

def check_true(n, cond, detay=""):
    global ok
    if not cond: ok = False
    print(f"  {'OK  ' if cond else 'FAIL'} {n} {detay}")


AGLAR = [("300/40", 300.0, 40.0), ("200/20", 200.0, 20.0),
         ("1000/100", 1000.0, 100.0), ("100/100", 100.0, 100.0),
         ("50/50", 50.0, 50.0)]

print("=" * 70)
print("1. Taban olcegi YONE gore, tek bir havuza gore degil")
print("=" * 70)
for ad, d, u in AGLAR:
    t = Topology.generate(seed=3, sites=1, egresses=1,
                          downlink_mbps=d, uplink_mbps=u)
    o = FlowOptimizer(t)
    check(f"{ad} indirme olcegi", o._capacity_for("down"), d)
    check(f"{ad} yukleme olcegi", o._capacity_for("up"), u)

print()
print("=" * 70)
print("2. En dusuk oncelikli sinif her agda tabanini aliyor")
print("=" * 70)
print("   (background = DNS/keepalive; ac kalmasi agi calismaz hale getirir)")
for ad, d, u in AGLAR:
    t = Topology.generate(seed=3, sites=1, egresses=1,
                          downlink_mbps=d, uplink_mbps=u)
    giris = t.attach_point("pc")
    plan = FlowOptimizer(t, DEFAULT_POLICY).solve([
        Demand("yedek", giris, TC.BULK, d * 4, src=INTERNET, direction="down"),
        Demand("dns", giris, TC.BACKGROUND, d * 0.1, src=INTERNET,
               direction="down"),
    ])
    dns = next(a for a in plan.allocations if a.demand.device == "dns")
    beklenen = DEFAULT_POLICY.floor_of("background") * d       # %2 x indirme
    check(f"{ad} dns tabani", dns.granted_mbps, beklenen)

print()
print("=" * 70)
print("3. LAN talebi internet kapasitesine gore olceklenmiyor")
print("=" * 70)
t = Topology.generate(seed=3, sites=1, egresses=1,
                      downlink_mbps=200.0, uplink_mbps=20.0)
o = FlowOptimizer(t)
lan = o._capacity_for("lan", "nvr")
print(f"   NVR'a giren kapasite: {lan:.0f} Mbps  "
      f"(internet cikisi {o._capacity_for('up'):.0f} Mbps)")
check_true("LAN olcegi internet cikisindan bagimsiz", lan > 100,
           f"({lan:.0f} Mbps)")

print()
print("=" * 70)
print("4. Iki yon ayni anda: biri otekinin tabanini yemiyor")
print("=" * 70)
t = Topology.generate(seed=3, sites=1, egresses=1,
                      downlink_mbps=200.0, uplink_mbps=20.0)
giris = t.attach_point("pc")
plan = FlowOptimizer(t, DEFAULT_POLICY).solve([
    Demand("a", giris, TC.BULK, 500.0, src=INTERNET, direction="down"),
    Demand("b", giris, TC.BACKGROUND, 20.0, src=INTERNET, direction="down"),
    Demand("c", INTERNET, TC.BULK, 100.0, direction="up"),
    Demand("d", INTERNET, TC.BACKGROUND, 5.0, direction="up"),
])
g = {a.demand.device: a.granted_mbps for a in plan.allocations}
print(f"   indirme: bulk {g['a']:.1f} · background {g['b']:.1f}  (kapasite 200)")
print(f"   yukleme: bulk {g['c']:.1f} · background {g['d']:.1f}  (kapasite 20)")
check("indirme background tabani", g["b"], 0.02 * 200)
check("yukleme background tabani", g["d"], 0.02 * 20)
check("indirme toplam", g["a"] + g["b"], 200.0)
check("yukleme toplam", g["c"] + g["d"], 20.0)

print()
print("=" * 70)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
