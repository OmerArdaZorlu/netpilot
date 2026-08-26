"""Akış optimize edicinin dogrulamasi.

Her senaryonun cevabi elle hesaplanabilir; cozucunun ciktisi ona karsi
olculuyor. Amac "calisiyor gorunuyor" degil, "dogru sayiyi buluyor".
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Edge, Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer

ok = True


def check(name, got, want, tol=0.05):
    global ok
    good = abs(got - want) <= tol
    if not good:
        ok = False
    print(f"  {'OK  ' if good else 'FAIL'} {name}: {got:.2f} (beklenen {want:.2f})")


def line(title):
    print(f"\n{'='*68}\n{title}\n{'='*68}")


# --------------------------------------------------------------- 1. darbogaz
line("1. Tek dar bogaz — talep kapasiteyi asiyor")
# sw -> wan (100 Mbps) -> internet. Iki cihaz, her biri 80 Mbps istiyor.
topo = Topology(edges=[
    Edge("sw-access", "sw-core", 1000), Edge("sw-core", "wan", 100, kind="wan"),
    Edge("wan", INTERNET, 100, kind="wan"),
], default_access="sw-access")
opt = FlowOptimizer(topo)
plan = opt.solve([
    Demand("pc-a", INTERNET, TC.BULK, 80.0),
    Demand("pc-b", INTERNET, TC.BULK, 80.0),
])
g = {a.demand.device: a.granted_mbps for a in plan.allocations}
# 100 Mbps'i esit paylasmali: 50 / 50
check("pc-a", g["pc-a"], 50.0)
check("pc-b", g["pc-b"], 50.0)
check("toplam", sum(g.values()), 100.0)
pb = {r["device"]: r["pullback_mbps"] for r in plan.pullbacks()}
check("pc-a geri cekme", pb.get("pc-a", 0), 30.0)
print("  darbogaz:", [b["edge"] for b in plan.bottlenecks()])

# ---------------------------------------------------------------- 2. oncelik
line("2. Oncelik — realtime once doyar")
topo2 = Topology(edges=[
    Edge("sw-access", "sw-core", 1000), Edge("sw-core", "wan", 100, kind="wan"),
    Edge("wan", INTERNET, 100, kind="wan"),
], default_access="sw-access")
plan2 = FlowOptimizer(topo2).solve([
    Demand("voip", INTERNET, TC.REALTIME, 30.0),
    Demand("yedek", INTERNET, TC.BULK, 200.0),
])
g2 = {a.demand.device: a.granted_mbps for a in plan2.allocations}
check("voip (realtime, tam)", g2["voip"], 30.0)
check("yedek (bulk, kalan)", g2["yedek"], 70.0)

# ------------------------------------------------------------ 3. coklu kenar
line("3. Coklu kenar — trafik iki hatta bolunur")
topo3 = Topology(edges=[
    Edge("sw-access", "sw-core", 1000),
    Edge("sw-core", "wan-a", 60, latency_ms=8, kind="wan"),
    Edge("wan-a", INTERNET, 60, latency_ms=8, kind="wan"),
    Edge("sw-core", "wan-b", 60, latency_ms=40, kind="wan"),
    Edge("wan-b", INTERNET, 60, latency_ms=40, kind="wan"),
], default_access="sw-access")
plan3 = FlowOptimizer(topo3).solve([Demand("pc", INTERNET, TC.BULK, 100.0)])
a3 = plan3.allocations[0]
check("toplam verilen", a3.granted_mbps, 100.0)
ua = a3.edge_usage.get(("sw-core", "wan-a"), 0.0)
ub = a3.edge_usage.get(("sw-core", "wan-b"), 0.0)
check("hizli hat doldu", ua, 60.0)
check("yavas hat kalani aldi", ub, 40.0)
print("  -> tek hat 60 Mbps ama 100 Mbps akiyor: yol bolunmus")

# ------------------------------------------------------------- 4. maliyet
line("4. Maliyet — sayacli hat en son kullanilir")
topo4 = Topology(edges=[
    Edge("sw-access", "sw-core", 1000),
    Edge("sw-core", "fiber", 50, latency_ms=8, kind="wan"),
    Edge("fiber", INTERNET, 50, latency_ms=8, kind="wan"),
    Edge("sw-core", "lte", 50, latency_ms=45, cost_per_gb=4.0, kind="wan"),
    Edge("lte", INTERNET, 50, latency_ms=45, cost_per_gb=4.0, kind="wan"),
], default_access="sw-access")
plan4 = FlowOptimizer(topo4).solve([Demand("pc", INTERNET, TC.BULK, 40.0)])
a4 = plan4.allocations[0]
check("fiber tercih edildi", a4.edge_usage.get(("sw-core", "fiber"), 0.0), 40.0)
check("lte'ye dokunulmadi", a4.edge_usage.get(("sw-core", "lte"), 0.0), 0.0)

# ------------------------------------------------------------- 5. LAN/WAN
line("5. LAN trafigi WAN'i tuketmez")
topo5 = Topology(edges=[
    Edge("sw-access", "sw-core", 1000),
    Edge("sw-core", "nvr", 500),
    Edge("sw-core", "wan", 100, kind="wan"),
    Edge("wan", INTERNET, 100, kind="wan"),
], default_access="sw-access")
plan5 = FlowOptimizer(topo5).solve([
    Demand("kamera", "nvr", TC.STREAMING, 300.0),      # LAN ici
    Demand("pc", INTERNET, TC.BULK, 100.0),            # WAN
])
g5 = {a.demand.device: a.granted_mbps for a in plan5.allocations}
check("kamera (LAN, tam)", g5["kamera"], 300.0)
check("pc (WAN, tam)", g5["pc"], 100.0)
print("  -> 300 Mbps LAN akarken 100 Mbps WAN hala tam: ayri olculuyor")

# --------------------------------------------------------------- 6. saglik
line("6. Hat bozulunca trafik digerine kayar")
topo6 = Topology(edges=[
    Edge("sw-access", "sw-core", 1000),
    Edge("sw-core", "wan-a", 100, kind="wan", health=0.2),   # %80 bozuk
    Edge("wan-a", INTERNET, 100, kind="wan", health=0.2),
    Edge("sw-core", "wan-b", 100, kind="wan"),
    Edge("wan-b", INTERNET, 100, kind="wan"),
], default_access="sw-access")
plan6 = FlowOptimizer(topo6).solve([Demand("pc", INTERNET, TC.BULK, 100.0)])
a6 = plan6.allocations[0]
check("saglam hat yuklendi", a6.edge_usage.get(("sw-core", "wan-b"), 0.0), 100.0)
check("bozuk hat bos", a6.edge_usage.get(("sw-core", "wan-a"), 0.0), 0.0)

# ------------------------------------------------------------ 7. ulasilamaz
line("7. Ulasilamaz hedef cokmeye yol acmaz")
plan7 = FlowOptimizer(topo5).solve([
    Demand("pc", "olmayan-dugum", TC.BULK, 10.0),
    Demand("pc2", INTERNET, TC.BULK, 10.0),
])
g7 = {a.demand.device: a.granted_mbps for a in plan7.allocations}
check("ulasilamaz talep sifir", g7["pc"], 0.0)
check("gecerli talep etkilenmedi", g7["pc2"], 10.0)
print("  not:", plan7.note)

print("\n" + "="*68)


# -------------------------------------------------------- 8. asgari garanti
line("8. En dusuk oncelik ac kalmiyor")
topo8 = Topology(edges=[
    Edge("sw-access", "sw-core", 1000), Edge("sw-core", "wan", 100, kind="wan"),
    Edge("wan", INTERNET, 100, kind="wan"),
], default_access="sw-access")
plan8 = FlowOptimizer(topo8).solve([
    Demand("voip",  INTERNET, TC.REALTIME,    50.0),
    Demand("web",   INTERNET, TC.INTERACTIVE, 80.0),
    Demand("dns",   INTERNET, TC.BACKGROUND,   2.0),
])
g8 = {a.demand.device: a.granted_mbps for a in plan8.allocations}
check("dns tam karsilandi", g8["dns"], 2.0)
check("voip tam", g8["voip"], 50.0)
check("web kalani aldi", g8["web"], 48.0)
check("toplam kapasite kadar", sum(g8.values()), 100.0)
print("  -> background en dusuk oncelikte ama 0 degil")

# ------------------------------------------- 9. yanlis etiket onceligi calmaz
line("9. Yanlis etiketlenmis dev akis onceligi calamaz")
plan9 = FlowOptimizer(topo8).solve([
    Demand("voip",    INTERNET, TC.REALTIME,    50.0),
    Demand("torrent", INTERNET, TC.BACKGROUND, 500.0),
])
g9 = {a.demand.device: a.granted_mbps for a in plan9.allocations}
check("voip yine tam", g9["voip"], 50.0)
print(f"  torrent aldi: {g9['torrent']:.1f} Mbps (tabani 4, gerisi artiktan)")

# ---------------------------------------- 10. asimetrik hat: iki yon ayri
line("10. Indirme ve yukleme ayri kapasiteler")
from ntc.traffic.topology import Topology as T
topo10 = T.default(200, 20)
plan10 = FlowOptimizer(topo10).solve([
    Demand("pc", topo10.attach_point("pc"), TC.BULK, 150.0,
           src=INTERNET, direction="down"),
    Demand("pc", INTERNET, TC.BULK, 15.0, direction="up"),
])
g10 = {a.demand.direction: a.granted_mbps for a in plan10.allocations}
check("indirme tam (150/200)", g10["down"], 150.0)
check("yukleme tam (15/20)", g10["up"], 15.0)

# ------------------------------- 11. yukleme doygunlugu yakalaniyor (eski hata)
line("11. Yukleme doygunlugu goruluyor")
plan11 = FlowOptimizer(topo10).solve([
    Demand("pc", topo10.attach_point("pc"), TC.BULK, 50.0,
           src=INTERNET, direction="down"),
    Demand("pc", INTERNET, TC.BULK, 60.0, direction="up"),
])
g11 = {a.demand.direction: a.granted_mbps for a in plan11.allocations}
check("indirme etkilenmedi", g11["down"], 50.0)
check("yukleme hatta sigdi", g11["up"], 20.0)
pb11 = plan11.pullbacks()
check("geri cekme miktari", pb11[0]["pullback_mbps"], 40.0)
print(f"  geri cekme yonu: {pb11[0]['direction']}")
check("darbogaz sayisi", len(plan11.bottlenecks()), 2)

# ------------------------------------- 12. LAN trafigi WAN'i tuketmiyor
line("12. LAN trafigi WAN hattini tuketmiyor")
plan12 = FlowOptimizer(topo10).solve([
    Demand("kamera", "nvr", TC.STREAMING, 400.0, direction="lan"),
    Demand("pc", INTERNET, TC.BULK, 18.0, direction="up"),
])
g12 = {a.demand.device: a.granted_mbps for a in plan12.allocations}
check("LAN tam gecti (400 Mbps)", g12["kamera"], 400.0)
check("WAN yuklemesi etkilenmedi", g12["pc"], 18.0)
print("  -> 400 Mbps LAN akarken 20 Mbps'lik WAN hatti hala serbest")

print("\n" + "=" * 68)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
