"""AI -> ... -> FLOW zinciri, cozucunun kendi makinesiyle.

AI'in tahsisleri `pinned` olarak 0. tura giriyor; kalan agi LP dolduruyor.
Olculen: hicbir sey kaybediliyor mu, ve akisin ne kadari AI karari.
"""
import asyncio, sys, time
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.analyst import AIAnalyst
from ntc.ai.flowai import pins_for
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.core.models import TrafficClass as TC
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.topology import Edge, Topology, INTERNET

ok = True
def hata(m):
    global ok; ok = False; print("   !! ", m)

def ag(sayacli=False, bozuk=False):
    u = 4.0 if sayacli else 0.0
    h = 0.3 if bozuk else 1.0
    return Topology(edges=[
        Edge("access","core",1000.0,0.2,kind="access"),
        Edge("core","access",1000.0,0.2,kind="access"),
        Edge("core","fiber",40.0,8.0,0.0,kind="wan"),
        Edge("fiber",INTERNET,40.0,8.0,0.0,kind="wan"),
        Edge(INTERNET,"fiber",200.0,8.0,0.0,kind="wan"),
        Edge("fiber","core",200.0,8.0,0.0,kind="wan"),
        Edge("core","yedek",20.0,30.0,u,h,kind="wan"),
        Edge("yedek",INTERNET,20.0,30.0,u,h,kind="wan"),
        Edge(INTERNET,"yedek",100.0,30.0,u,h,kind="wan"),
        Edge("yedek","core",100.0,30.0,u,h,kind="wan"),
    ], default_access="access")

def talepler(t):
    g = t.attach_point("x")
    return [
        Demand("voip",g,TC.REALTIME,40.0,src=INTERNET,direction="down"),
        Demand("ws-01",g,TC.INTERACTIVE,90.0,src=INTERNET,direction="down"),
        Demand("tv",g,TC.STREAMING,120.0,src=INTERNET,direction="down"),
        Demand("yedek",g,TC.BULK,400.0,src=INTERNET,direction="down"),
        Demand("dns",g,TC.BACKGROUND,8.0,src=INTERNET,direction="down"),
        Demand("srv",INTERNET,TC.BULK,80.0,direction="up"),
        Demand("voip",INTERNET,TC.REALTIME,15.0,direction="up"),
    ]

async def main():
    cfg = load_config(); pr = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, pr)
    print(f"model: {pr.model}\n")
    print(f"{'durum':<22}{'LP tek':>9}{'AI+LP':>9}{'kayip':>8}{'AI payi':>9}{'sure':>7}")
    print("-"*64)
    for ad, saat, sy, bz in [("mesai",14,False,False), ("gece",3,False,False),
                             ("sayacli",14,True,False), ("bozuk bacak",14,False,True)]:
        t = ag(sy,bz); d = talepler(t)
        lp = FlowOptimizer(t).solve(d)
        lp_tek = sum(a.granted_mbps for a in lp.allocations)

        t0 = time.time()
        plan, atlanan, _ = await an.propose_flow(t, d, clock_hour=saat)
        pins = pins_for(plan, d) if plan.valid else {}
        hib = FlowOptimizer(t).solve(d, pinned=pins)
        hib_top = sum(a.granted_mbps for a in hib.allocations)
        sure = time.time()-t0

        ai_pay = sum(min(pins.get(a.demand.key, 0.0), a.granted_mbps)
                     for a in hib.allocations)
        kayip = lp_tek - hib_top
        print(f"{ad:<22}{lp_tek:>9.1f}{hib_top:>9.1f}{kayip:>8.1f}"
              f"{(ai_pay/hib_top if hib_top>0 else 0):>8.0%}{sure:>6.0f}s")
        if kayip > lp_tek*0.05:
            hata(f"{ad}: hibrit optimumun %5'inden fazlasini kaybetti")
        for a in hib.allocations:
            if a.granted_mbps > a.demand.mbps + 1e-6:
                hata(f"{ad}: talepten fazla verildi"); break
    print("-"*64)
    print("kayip = LP tek basina - (AI + LP). 0 ise AI hicbir sey kaybettirmedi.")
    print("\nSONUC:", "gecti" if ok else "KALDI")
    await pr.aclose()
asyncio.run(main())
