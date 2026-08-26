"""Politika kolu gercekten cozumu degistiriyor mu?

AI'i isin icine sokmadan once bunu kanitlamak gerekiyor: hedef degisince
cikan akis da degisiyor mu, yoksa kol bosa mi doniyor.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Edge, Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.flowpolicy import DEFAULT_POLICY, FlowPolicy

ok = True
def check(n, got, want):
    global ok
    g = got == want
    if not g: ok = False
    print(f"  {'OK  ' if g else 'FAIL'} {n}: {got!r} (beklenen {want!r})")

def check_true(n, cond, detay=""):
    global ok
    if not cond: ok = False
    print(f"  {'OK  ' if cond else 'FAIL'} {n} {detay}")


AG = [
    Edge("access", "core", 1000.0, 0.2, kind="access"),
    Edge("core", "access", 1000.0, 0.2, kind="access"),
    Edge("core", "fiber", 100.0, 8.0, 0.0, kind="wan"),
    Edge("fiber", INTERNET, 100.0, 8.0, 0.0, kind="wan"),
    Edge("core", "lte", 100.0, 40.0, 5.0, kind="wan"),     # yavas + sayacli
    Edge("lte", INTERNET, 100.0, 40.0, 5.0, kind="wan"),
]
TOPO = Topology(edges=AG, default_access="access")

TALEP = [
    Demand("voip",  INTERNET, TC.REALTIME,    30.0, direction="up"),
    Demand("ws-01", INTERNET, TC.INTERACTIVE, 60.0, direction="up"),
    Demand("yedek", INTERNET, TC.BULK,       150.0, direction="up"),
]


def coz(politika):
    p = FlowOptimizer(TOPO, politika).solve(TALEP)
    return {a.demand.device: round(a.granted_mbps, 1) for a in p.allocations}


# ------------------------------------------------ 1. gunduz (varsayilan)
print("=" * 70)
print("1. GUNDUZ — varsayilan hedef (realtime > interactive > ... > bulk)")
print("=" * 70)
gunduz = coz(DEFAULT_POLICY)
print("  ", gunduz)
check("voip tam", gunduz["voip"], 30.0)
check_true("yedek kisitli", gunduz["yedek"] < 150.0,
           f"({gunduz['yedek']} Mbps)")

# ---------------------------------------- 2. gece yedekleme penceresi
print()
print("=" * 70)
print("2. GECE 03:00 — yedekleme penceresi, hedef degisti")
print("=" * 70)
gece, sorunlar = FlowPolicy.validate({
    "class_order": ["bulk", "realtime", "interactive", "streaming", "background"],
    "floors": {"bulk": 0.40, "realtime": 0.10, "interactive": 0.05,
               "streaming": 0.02, "background": 0.02},
    "situation": "gece yedekleme penceresi",
    "rationale": "Ofis bos, yedeklemenin sabaha bitmesi gerekiyor.",
})
check_true("politika gecerli", gece is not None)
print("   uyarilar:", sorunlar or "yok")
g2 = coz(gece)
print("  ", g2)
check_true("yedek daha cok aldi", g2["yedek"] > gunduz["yedek"],
           f"({gunduz['yedek']} -> {g2['yedek']} Mbps)")
check_true("voip yine de aliyor (taban korudu)", g2["voip"] > 0,
           f"({g2['voip']} Mbps)")
print("   fark:", gece.diff(DEFAULT_POLICY))

# ------------------------------------------- 3. sayacli hat: para agir bassin
print()
print("=" * 70)
print("3. SAYACLI HAT — para agirligi artirildi")
print("=" * 70)
def lte_kullanimi(politika):
    p = FlowOptimizer(TOPO, politika).solve(TALEP)
    t = 0.0
    for a in p.allocations:
        for (s, d), v in a.edge_usage.items():
            if d == INTERNET and s == "lte":
                t += v
    return round(t, 1)

ucuz = lte_kullanimi(DEFAULT_POLICY)
pahali, _ = FlowPolicy.validate({
    "class_order": list(DEFAULT_POLICY.class_order),
    "floors": dict(DEFAULT_POLICY.floors),
    "cost_weight": 100.0, "latency_weight": 0.1,
    "path_weight": 1e-2,
    "situation": "sayacli hat pahali",
})
kisik = lte_kullanimi(pahali)
print(f"   LTE kullanimi: varsayilan {ucuz} Mbps -> para agir {kisik} Mbps")
check_true("para agirligi LTE'yi kisti", kisik <= ucuz,
           f"({ucuz} -> {kisik})")

# ------------------------------------------------------- 4. dogrulama kapisi
print()
print("=" * 70)
print("4. DOGRULAMA — modelin sacmasi iceri girmiyor")
print("=" * 70)
kotu = [
    ("eksik sinif", {"class_order": ["realtime", "bulk"], "floors": {}}),
    ("uydurma sinif", {"class_order": ["realtime", "interactive", "streaming",
                                       "bulk", "kritik"], "floors": {}}),
    ("liste degil", {"class_order": "realtime once", "floors": {}}),
    ("sozluk degil", "realtime once gelsin"),
    ("sayi degil", {"class_order": list(DEFAULT_POLICY.class_order),
                    "floors": {"realtime": "cok"}}),
]
for ad, ham in kotu:
    pol, sor = FlowPolicy.validate(ham)
    check_true(f"reddedildi: {ad}", pol is None, f"-> {sor[0] if sor else ''}")

print()
print("  --- sayisal tasmalar kirpiliyor ve YAZILIYOR ---")
tasma, sor = FlowPolicy.validate({
    "class_order": list(DEFAULT_POLICY.class_order),
    "floors": {c: 0.9 for c in DEFAULT_POLICY.floors},
    "path_weight": 5.0,
})
check_true("politika kabul edildi", tasma is not None)
check_true("taban toplami tavanda", abs(sum(tasma.floors.values()) - 0.60) < 0.01,
           f"(%{sum(tasma.floors.values())*100:.0f})")
check_true("path_weight araliga cekildi", tasma.path_weight <= 1e-2,
           f"({tasma.path_weight:g})")
for x in sor:
    print("     uyari:", x)

# ------------------------------------------- 5. taban tavani neden var
print()
print("=" * 70)
print("5. TAVAN OLMASAYDI: siralama anlamsizlasirdi")
print("=" * 70)
print(f"   MAX_FLOOR_TOTAL = %{0.60*100:.0f}")
print("   -> herkes garantisini alsaydi artik kalmaz, oncelik sirasi bosa donerdi")
check_true("taban toplami her zaman tavan alti",
           sum(tasma.floors.values()) <= 0.6001)

print()
print("=" * 70)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
