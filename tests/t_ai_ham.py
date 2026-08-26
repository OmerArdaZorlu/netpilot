"""Kotu aglarda modelin HAM cikisi ne?"""
import asyncio, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from t_ai_random import talepler, AGLAR
from ntc.ai.analyst import AIAnalyst
from ntc.ai.flowai import demand_rows, egress_rows
from ntc.ai.prompts import FLOW_SYSTEM, FLOW_USER
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.traffic.topology import Topology

BAK = [206, 207]

async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    for tohum, s, c, dd, uu, saat in AGLAR:
        if tohum not in BAK: continue
        topo = Topology.generate(seed=tohum, sites=s, egresses=c,
                                 downlink_mbps=dd, uplink_mbps=uu)
        d = talepler(topo, tohum)
        rows, _ = demand_rows(d)
        legs = egress_rows(topo)
        kd = sum(b.get("down_mbps",0) for b in legs)
        ku = sum(b.get("up_mbps",0) for b in legs)
        idn = sum(r["want_mbps"] for r in rows if r["direction"]=="down")
        iup = sum(r["want_mbps"] for r in rows if r["direction"]=="up")
        bacak = "\n".join(f"- {b['name']}: indirme {b.get('down_mbps',0):.0f} Mbps, "
            f"yükleme {b.get('up_mbps',0):.0f} Mbps, {b.get('latency_ms',0):.0f} ms"
            + (", SAYAÇLI" if b.get("metered") else "")
            + ("" if b.get("healthy",True) else ", BOZUK") for b in legs)
        istek = "\n".join(f"- {r['id']}: {r['device']} ({r['direction']}, "
            f"{r['class']}) istiyor {r['want_mbps']:.1f} Mbps" for r in rows)
        istek += "\n\nGeçerli bacak adları: " + ", ".join(b["name"] for b in legs)
        user = FLOW_USER.format(saat=f"{saat:02d}:00 (mesai saati)", bacaklar=bacak,
            kap_down=f"{kd:.0f}", kap_up=f"{ku:.0f}", istekler=istek, oran=AIAnalyst._ration_line("İndirme", idn, kd)
            + "\n" + AIAnalyst._ration_line("Yükleme", iup, ku),
            istek_down=f"{idn:.0f}", istek_up=f"{iup:.0f}")
        ham = await provider.complete(FLOW_SYSTEM, user)
        print("=" * 78)
        print(f"t{tohum}  {s}s/{c}c  kap {kd:.0f}/{ku:.0f}  "
              f"istek {idn:.0f}/{iup:.0f}  ({'IYI' if tohum==202 else 'KOTU'})")
        print("=" * 78)
        print("HAM CIKTI (%d karakter):" % len(ham))
        print(ham[:2500])
        print()
    await provider.aclose()

asyncio.run(main())
