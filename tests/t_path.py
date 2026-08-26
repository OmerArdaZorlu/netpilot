"""Yol atama: yapiskan mi, oranlari tutturuyor mu."""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Edge, Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer, PathAssigner

ok = True
def chk(ad, kosul, detay=""):
    global ok
    if not kosul: ok = False
    print(f"  {'OK  ' if kosul else 'FAIL'} {ad} {detay}")

topo = Topology(edges=[
    Edge("sw-access", "sw-core", 1000),
    Edge("sw-core", "a", 60, latency_ms=8, kind="wan"),
    Edge("a", INTERNET, 60, latency_ms=8, kind="wan"),
    Edge("sw-core", "b", 40, latency_ms=30, kind="wan"),
    Edge("b", INTERNET, 40, latency_ms=30, kind="wan"),
], default_access="sw-access")
plan = FlowOptimizer(topo).solve([Demand("pc", INTERNET, TC.BULK, 100.0)])
pa = PathAssigner(plan)

akislar = [f"10.0.0.5:{p}->93.184.216.34:443/tcp" for p in range(2000, 6000)]
atama = {k: pa.assign("pc", "bulk", "up", k) for k in akislar}

print("\n1. Her akisa bir cikis atandi")
chk("bos atama yok", all(atama.values()))

print("\n2. Dagilim planin oranini tutturuyor (a=60, b=40)")
from collections import Counter
c = Counter(atama.values())
toplam = sum(c.values())
for dst in sorted(c):
    pay = c[dst] / toplam * 100
    print(f"   {dst}: %{pay:.1f}")
chk("a ~ %60", abs(c["a"]/toplam - 0.60) < 0.03, f"({c['a']/toplam*100:.1f})")
chk("b ~ %40", abs(c["b"]/toplam - 0.40) < 0.03, f"({c['b']/toplam*100:.1f})")

print("\n3. Yapiskan: ayni akis hep ayni yolda")
tekrar = {k: pa.assign("pc", "bulk", "up", k) for k in akislar}
chk("tum atamalar ayni", tekrar == atama)

print("\n4. Farkli akis farkli yola dusebiliyor")
chk("iki cikis da kullanildi", len(c) == 2)

print("\n5. Tek yollu planda atama yapilmaz")
tek = Topology.default(200, 20)
pa2 = PathAssigner(FlowOptimizer(tek).solve([Demand("pc", INTERNET, TC.BULK, 5.0)]))
chk("bos donuyor", pa2.assign("pc", "bulk", "up", akislar[0]) == "")

print("\nSONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
