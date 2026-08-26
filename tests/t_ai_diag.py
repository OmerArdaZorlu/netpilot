"""Uc kotu agda model tam olarak ne dedi?"""
import asyncio, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from t_ai_random import talepler, AGLAR
from ntc.ai.analyst import AIAnalyst
from ntc.ai.flowai import demand_rows, egress_rows
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.traffic.topology import Topology

KOTU = [201, 203, 205]

async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    analyst = AIAnalyst(cfg.ai, provider)
    for tohum, s, c, dd, uu, saat in AGLAR:
        if tohum not in KOTU:
            continue
        topo = Topology.generate(seed=tohum, sites=s, egresses=c,
                                 downlink_mbps=dd, uplink_mbps=uu)
        d = talepler(topo, tohum)
        rows = demand_rows(d)
        legs = egress_rows(topo)
        print("=" * 78)
        print(f"t{tohum}  {s} site / {c} cikis  {dd:.0f}/{uu:.0f}  saat {saat}")
        print("=" * 78)
        print("modele giden satirlar:")
        for r in rows:
            print("   ", r)
        print("modele giden bacaklar:")
        for l in legs:
            print("   ", l)
        plan, atlanan, bilgi = await analyst.propose_flow(topo, d, clock_hour=saat)
        print(f"\ngecerli={plan.valid}  tahsis={len(plan.grants)}  "
              f"atlanan={len(atlanan)}  onarim={plan.repair_ratio:.2f}")
        print("durum   :", plan.situation)
        print("gerekce :", plan.rationale)
        print("ihlaller:")
        for i in plan.issues:
            print("   -", i)
        print("tahsisler:")
        for g in plan.grants:
            print(f"    {g.device:<12}{g.direction:<6}{g.traffic_class:<14}"
                  f"{g.grant_mbps:>8.1f}  {g.egress}")
        print()
    await provider.aclose()

asyncio.run(main())
