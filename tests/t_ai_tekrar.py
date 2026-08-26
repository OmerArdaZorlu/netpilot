"""Ayni ag ikinci kez sorulunca ayni mi cokuyor?

Ayirt edilen: ariza AGIN OZELLIGI mi (yapisal, duzeltilebilir) yoksa
MODELIN DEGISKENLIGI mi (ayni girdi, farkli cikti).
"""
import asyncio, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from t_ai_random import talepler, AGLAR
from ntc.ai.analyst import AIAnalyst
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.traffic.topology import Topology

TUR = 3

async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    analyst = AIAnalyst(cfg.ai, provider)
    print(f"{'ag':<16}" + "".join(f"{'tur'+str(i+1):>10}" for i in range(TUR)) + "   yorum")
    print("-" * 62)
    for tohum, s, c, dd, uu, saat in AGLAR:
        topo = Topology.generate(seed=tohum, sites=s, egresses=c,
                                 downlink_mbps=dd, uplink_mbps=uu)
        d = talepler(topo, tohum)
        sonuc = []
        for _ in range(TUR):
            try:
                plan, _a, _b = await analyst.propose_flow(topo, d, clock_hour=saat)
                # anlamli tahsis = 0'dan buyuk
                sonuc.append(sum(1 for g in plan.grants if g.grant_mbps > 0.01)
                             if plan.valid else -1)
            except Exception:
                sonuc.append(-1)
        iyi = [x for x in sonuc if x >= 8]
        if len(iyi) == TUR:   y = "kararli iyi"
        elif not iyi:         y = "KARARLI KOTU (yapisal)"
        else:                 y = f"DEGISKEN ({len(iyi)}/{TUR} iyi)"
        print(f"{s}s/{c}c t{tohum}{'':<3}"
              + "".join(f"{('gecersiz' if x < 0 else str(x)):>10}" for x in sonuc)
              + f"   {y}")
    await provider.aclose()

asyncio.run(main())
