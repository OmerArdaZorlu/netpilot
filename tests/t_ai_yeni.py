"""Duzeltmeler bu 10 tohuma uyduruldu mu? Hic gorulmemis 10 agda olc."""
import asyncio, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import t_ai_random as T
from ntc.ai.analyst import AIAnalyst
from ntc.ai.flowai import pins_for
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.traffic.flowopt import FlowOptimizer
from ntc.traffic.topology import Topology

YENI = [(301,2,2,250.,25.,14),(302,1,3,600.,60.,3),(303,4,5,120.,12.,14),
        (304,3,1,700.,70.,21),(305,5,2,90.,9.,14),(306,2,4,1500.,150.,14),
        (307,1,5,350.,35.,3),(308,4,3,60.,6.,14),(309,3,3,900.,90.,14),
        (310,5,4,180.,18.,21)]

async def main():
    cfg = load_config(); provider = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, provider)
    print(f"{'ag':<16}{'kap d/u':>13}{'gecerli':>9}{'>0 tahsis':>11}"
          f"{'ihlal':>7}{'pay':>7}{'kayip':>8}")
    print("-" * 72)
    iyi = tam = 0; paylar = []; kayiplar = []
    for tohum,s,c,dd,uu,saat in YENI:
        topo = Topology.generate(seed=tohum, sites=s, egresses=c,
                                 downlink_mbps=dd, uplink_mbps=uu)
        d = T.talepler(topo, tohum)
        lp = FlowOptimizer(topo).solve(d)
        lp_top = sum(a.granted_mbps for a in lp.allocations)
        plan,_a,_b = await an.propose_flow(topo, d, clock_hour=saat)
        pins = pins_for(plan, d) if plan.valid else {}
        hib = FlowOptimizer(topo).solve(d, pinned=pins)
        hib_top = sum(a.granted_mbps for a in hib.allocations)
        ai = sum(min(pins.get(a.demand.key,0.), a.granted_mbps) for a in hib.allocations)
        pay = ai/hib_top if hib_top>0 else 0.
        kayip = lp_top - hib_top
        n = sum(1 for g in plan.grants if g.grant_mbps > 0.01)
        if plan.valid: iyi += 1
        if n >= 8: tam += 1
        paylar.append(pay); kayiplar.append(kayip)
        print(f"{s}s/{c}c t{tohum}{'':<3}{dd:.0f}/{uu:.0f}".ljust(16+13)
              + f"{str(plan.valid):>9}{n:>11}{len(plan.issues):>7}"
                f"{pay*100:>6.0f}%{kayip:>8.1f}")
    print("-" * 72)
    print(f"gecerli plan      : {iyi}/10")
    print(f"8+ tahsis veren   : {tam}/10")
    print(f"ortalama AI payi  : %{sum(paylar)/len(paylar)*100:.0f}")
    print(f"toplam kayip      : {sum(kayiplar):.1f} Mbps  "
          f"(en kotu {max(kayiplar):.1f})")
    await provider.aclose()

asyncio.run(main())
