import asyncio, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import logging; logging.basicConfig(level=logging.ERROR)
from ntc.core.config import load_config
from ntc.controller import Controller
from ntc.traffic.flowopt import demands_from_signals

async def main():
    cfg = load_config(None)
    c = Controller(cfg)
    await c.start()
    try:
        for sc in ("congestion", "exfil"):      # exfil = yukleme doygunlugu
            try: c.scenario_source.trigger(sc)
            except Exception: pass
        await asyncio.sleep(35)
        sig = c.metrics.device_signals()
        st = c.metrics.link_stats()
        plan = await c.run_flow_optimization()
    finally:
        await c.stop()

    olculen_down = sum(s.down_bps for s in sig.values()) / 1e6
    olculen_up   = sum(s.up_bps for s in sig.values()) / 1e6
    olculen_lan  = sum(s.lan_bps for s in sig.values()) / 1e6
    d = demands_from_signals(sig, c.source.devices, c.topology)
    talep = sum(x.mbps for x in d)

    print("OLCULEN TRAFIK")
    print(f"  indirme (WAN) : {olculen_down:7.1f} Mbps   hat {cfg.link.downlink_mbps:.0f}  "
          f"%{st.down_utilization*100:.0f}")
    print(f"  yukleme (WAN) : {olculen_up:7.1f} Mbps   hat {cfg.link.uplink_mbps:.0f}  "
          f"%{st.up_utilization*100:.0f}")
    print(f"  LAN ici       : {olculen_lan:7.1f} Mbps")
    print(f"  TOPLAM        : {olculen_down + olculen_up + olculen_lan:7.1f} Mbps")
    print()
    print("COZUCUYE GIREN")
    print(f"  talep         : {talep:7.1f} Mbps  ({len(d)} talep)")
    print(f"  gormedigi     : {olculen_down + olculen_up + olculen_lan - talep:7.1f} Mbps")
    print()
    hedefler = {x.dst for x in d}
    print(f"  talep hedefleri: {sorted(hedefler)}")
    yon = {}
    for x in d:
        yon[x.direction] = yon.get(x.direction, 0.0) + x.mbps
    for k, v in sorted(yon.items()):
        print(f"    {k:5}: {v:7.1f} Mbps")
    print(f"  darbogaz       : {[b['edge'] for b in plan.bottlenecks()] or 'yok'}")

asyncio.run(main())
