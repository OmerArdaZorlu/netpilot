"""AI + LP hibrit: AI'in cevapladigi kalir, gerisini LP doldurur.

Onceki olcum AI'i YALNIZ basina olctu: 0.22. Tasarimda LP hakem olacakti;
cevaplanmayan talepler ona gidiyordu. Hibritin degeri olculmeden "AI akisi
kuruyor" da "kuramiyor" da yarim kalir.
"""
import asyncio, sys, time
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.analyst import AIAnalyst
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.core.models import TrafficClass as TC
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.topology import Edge, Topology, INTERNET

def ag():
    return Topology(edges=[
        Edge("access","core",1000.0,0.2,kind="access"),
        Edge("core","access",1000.0,0.2,kind="access"),
        Edge("core","fiber",40.0,8.0,0.0,kind="wan"),
        Edge("fiber",INTERNET,40.0,8.0,0.0,kind="wan"),
        Edge(INTERNET,"fiber",200.0,8.0,0.0,kind="wan"),
        Edge("fiber","core",200.0,8.0,0.0,kind="wan"),
        Edge("core","yedek",20.0,30.0,0.0,kind="wan"),
        Edge("yedek",INTERNET,20.0,30.0,0.0,kind="wan"),
        Edge(INTERNET,"yedek",100.0,30.0,0.0,kind="wan"),
        Edge("yedek","core",100.0,30.0,0.0,kind="wan"),
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
    cfg = load_config()
    pr = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, pr)
    print(f"model: {pr.model}\n")
    print(f"{'durum':<16}{'AI tek':>9}{'AI+LP':>9}{'LP tek':>9}{'AI payi':>9}")
    print("-"*54)
    for ad, saat in [("mesai",14),("gece",3),("aksam",21)]:
        t = ag(); d = talepler(t)
        lp = FlowOptimizer(t).solve(d)
        lp_tek = sum(a.granted_mbps for a in lp.allocations)

        plan, atlanan, _ = await an.propose_flow(t, d, clock_hour=saat)
        ai_tek = plan.total_mbps

        # HIBRIT: AI'in verdikleri sabit, kalani LP cozer.
        # AI'in kullandigi kapasiteyi dusup kalan agda atlananlari cozuyoruz.
        kalan_kenarlar = []
        ai_down = sum(g.grant_mbps for g in plan.grants if g.direction=="down")
        ai_up   = sum(g.grant_mbps for g in plan.grants if g.direction=="up")
        for e in t.edges:
            cap = e.capacity_mbps
            if e.kind=="wan":
                if e.src==INTERNET or (e.dst=="core" and e.src!="access"):
                    pay = ai_down * (e.capacity_mbps/max(1e-9,sum(
                        x.capacity_mbps for x in t.edges
                        if x.kind=="wan" and x.src==INTERNET)))
                    cap = max(0.0, e.capacity_mbps - pay)
                elif e.dst==INTERNET or (e.src=="core" and e.dst!="access"):
                    pay = ai_up * (e.capacity_mbps/max(1e-9,sum(
                        x.capacity_mbps for x in t.edges
                        if x.kind=="wan" and x.dst==INTERNET)))
                    cap = max(0.0, e.capacity_mbps - pay)
            kalan_kenarlar.append(Edge(e.src,e.dst,cap,e.latency_ms,
                                       e.cost_per_gb,e.health,e.kind))
        kalan_topo = Topology(edges=kalan_kenarlar,
                              default_access=t.default_access,
                              access_nodes=list(t.access_nodes))
        ek = 0.0
        if atlanan:
            p2 = FlowOptimizer(kalan_topo).solve(atlanan)
            ek = sum(a.granted_mbps for a in p2.allocations)
        hibrit = ai_tek + ek
        pay = ai_tek/hibrit if hibrit>0 else 0.0
        print(f"{ad:<16}{ai_tek:>9.1f}{hibrit:>9.1f}{lp_tek:>9.1f}{pay:>8.0%}")
    print("-"*54)
    print("AI payi = hibritin ne kadarinin AI karariyla geldigi")
    await pr.aclose()

asyncio.run(main())
