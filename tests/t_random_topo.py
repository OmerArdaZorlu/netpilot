"""Rastgele mimarilerde tum zincirin calistigini olcer.

Sorulan soru: "elle yazilmis bir topolojide degil, NE BULURSA ONDA calisiyor mu?"

Her tohum icin ayni talep iki agda cozuluyor:
  A) tek bacak  — yalnizca en buyuk cikis (optimize edicinin yapabilecegi tek
                  sey paylastirmak; toplam sabit)
  B) tum bacaklar — bosta duran bacak da kullaniliyor

Sonra ayni plan aksiyon -> politika -> infaz zincirinden gecirilip hicbir
katmanin dugum adi varsaymadigi dogrulaniyor.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

import random

from ntc.core.models import Device, DeviceKind, TrafficClass as TC
from ntc.traffic.topology import Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer, actions_from_plan
from ntc.enforce import (DescribeDriver, Enforcer, LinuxTcDriver, WindowsQosDriver,
                         policies_from_plan)

ok = True
def fail(msg):
    global ok
    ok = False
    print("   FAIL:", msg)


HOSTS = ["ws-01", "ws-02", "lt-03", "srv-yedek", "tv-lobi", "cam-giris",
         "telefon", "misafir"]
DEVICES = {f"dev-{h}": Device(id=f"dev-{h}", ip=f"10.20.0.{i+10}",
                              mac="00:00:00:00:00:00", hostname=h,
                              kind=DeviceKind.WORKSTATION)
           for i, h in enumerate(HOSTS)}


def talepler(topo, rnd):
    """Topolojiden bagimsiz talep: indirme, yukleme ve LAN."""
    d = []
    for h in HOSTS:
        giris = topo.attach_point(h)
        sinif = rnd.choice([TC.REALTIME, TC.INTERACTIVE, TC.STREAMING,
                            TC.BULK, TC.BACKGROUND])
        d.append(Demand(h, giris, sinif, rnd.uniform(10, 90),
                        src=INTERNET, direction="down"))
        d.append(Demand(h, INTERNET, sinif, rnd.uniform(2, 25), direction="up"))
    d.append(Demand("cam-giris", "nvr", TC.STREAMING, 300.0, direction="lan"))
    return d


def tek_bacak(topo):
    """Ayni ag ama yalnizca en buyuk cikis acik — tek hatli esdeger."""
    cikislar = {}
    for e in topo.edges:
        if e.kind == "wan" and e.src == INTERNET:
            cikislar[e.dst] = e.capacity_mbps
    if not cikislar:
        return None
    tut = max(cikislar, key=cikislar.get)
    kenarlar = [e for e in topo.edges
                if e.kind != "wan" or tut in (e.src, e.dst)]
    return Topology(edges=kenarlar, default_access=topo.default_access,
                    access_nodes=list(topo.access_nodes))


print("=" * 78)
print(f"{'tohum':>5} {'site':>4} {'cikis':>5} {'dugum':>5} | "
      f"{'tek bacak':>10} {'tum bacak':>10} {'kazanc':>7} | "
      f"{'aksiyon':>7} {'politika':>8} {'komut':>5}")
print("=" * 78)

kazanclar = []
for tohum in range(1, 13):
    rnd = random.Random(tohum * 31)
    sites = rnd.randint(1, 5)
    egresses = rnd.randint(1, 4)
    topo = Topology.generate(seed=tohum, sites=sites, egresses=egresses,
                             downlink_mbps=rnd.choice([200.0, 300.0, 500.0]),
                             uplink_mbps=rnd.choice([30.0, 40.0, 80.0]))
    d = talepler(topo, random.Random(tohum))

    try:
        plan = FlowOptimizer(topo).solve(d)
    except Exception as exc:
        fail(f"tohum {tohum}: cozucu coktu -> {exc}")
        continue

    tekt = tek_bacak(topo)
    plan1 = FlowOptimizer(tekt).solve(d) if tekt else None
    v_cok = sum(a.granted_mbps for a in plan.allocations)
    v_tek = sum(a.granted_mbps for a in plan1.allocations) if plan1 else 0.0
    kazanc = v_cok / v_tek if v_tek > 0 else float("nan")

    # --- zincirin geri kalani: aksiyon -> politika -> infaz
    try:
        acts = actions_from_plan(plan, DEVICES)
        pols = policies_from_plan(plan, DEVICES)
        tablolar = {e.dst: f"nt{i}" for i, e in enumerate(topo.edges)
                    if e.kind == "wan" and e.src == INTERNET}
        enf = Enforcer({"core": LinuxTcDriver(table_by_egress=tablolar),
                        "edge": WindowsQosDriver()})
        rec = enf.reconcile(pols)
    except Exception as exc:
        fail(f"tohum {tohum}: zincir coktu -> {exc}")
        continue

    # --- degismez kurallar
    if v_cok < v_tek - 0.5:
        fail(f"tohum {tohum}: coklu bacak tek bacaktan az verdi")
    if egresses > 1 and kazanc < 1.0:
        fail(f"tohum {tohum}: coklu cikista kazanc yok")
    kapasite = topo.wan_capacity()
    if kapasite[0] <= 0 or kapasite[1] <= 0:
        fail(f"tohum {tohum}: WAN kapasitesi sifir")
    for a in plan.allocations:
        if a.granted_mbps > a.demand.mbps + 0.01:
            fail(f"tohum {tohum}: talepten fazla verildi")
            break
    if rec.skipped:
        for s in rec.skipped:
            fail(f"tohum {tohum}: kural atlandi -> {s.reason}")

    kazanclar.append(kazanc)
    print(f"{tohum:>5} {sites:>4} {egresses:>5} {len(topo.nodes):>5} | "
          f"{v_tek:>10.1f} {v_cok:>10.1f} {kazanc:>6.2f}x | "
          f"{len(acts):>7} {len(pols):>8} {len(rec.commands):>5}")

print("=" * 78)
gecerli = [k for k in kazanclar if k == k]
if gecerli:
    print(f"ortalama kazanc: x{sum(gecerli)/len(gecerli):.2f}  "
          f"(en dusuk x{min(gecerli):.2f}, en yuksek x{max(gecerli):.2f})")

# --- elle yazilmis topoloji de hala calisiyor mu (geriye uyum)
print("\nElle yazilmis topoloji (geriye uyum):")
el = Topology.from_config({
    "default_access": "sw-access",
    "edges": [
        {"src": "sw-access", "dst": "core", "capacity_mbps": 1000},
        {"src": "core", "dst": "sw-access", "capacity_mbps": 1000},
        {"src": "core", "dst": "w1", "capacity_mbps": 20, "kind": "wan"},
        {"src": "w1", "dst": "internet", "capacity_mbps": 20, "kind": "wan"},
        {"src": "internet", "dst": "w1", "capacity_mbps": 100, "kind": "wan"},
        {"src": "w1", "dst": "core", "capacity_mbps": 100, "kind": "wan"},
    ]})
p = FlowOptimizer(el).solve(talepler(el, random.Random(1)))
print(f"   dugum={len(el.nodes)} WAN={el.wan_capacity()} "
      f"gecen={sum(a.granted_mbps for a in p.allocations):.1f} Mbps")
if el.wan_capacity() != (100.0, 20.0):
    fail("elle yazilmis topolojinin kapasitesi yanlis okundu")

print("\n" + "=" * 78)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
