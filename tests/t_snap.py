import asyncio, json, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import logging; logging.basicConfig(level=logging.ERROR)
from ntc.core.config import load_config
from ntc.controller import Controller

async def main():
    cfg = load_config(None)
    c = Controller(cfg)
    await c.start()
    try:
        for sc in ("congestion", "bandwidth_hog", "port_scan", "exfil", "beacon"):
            try: c.scenario_source.trigger(sc)
            except Exception: pass
        await asyncio.sleep(30)
        snap = c.analyst.build_snapshot(c.metrics, c.source.devices, c.optimizer)
    finally:
        await c.stop()

    from ntc.ai.analyst import _snapshot_json
    raw = _snapshot_json(snap)
    print(f"snapshot        : {len(raw)} karakter  (tavan {cfg.ai.max_snapshot_chars})")
    print(f"cihaz satiri    : {len(snap.get('devices', []))}")
    print(f"atlanan cihaz   : {snap.get('devices_omitted', 0)}")
    print(f"tavana uyuyor mu: {'EVET' if len(raw) <= cfg.ai.max_snapshot_chars else 'HAYIR'}")

    # Sert tavan sinamasi: cok kucuk butce
    small = c.analyst._trim_snapshot(
        json.loads(json.dumps(snap)), 600)
    print(f"\n600 karakterlik butce -> {len(_snapshot_json(small))} karakter, "
          f"{len(small.get('devices', []))} cihaz, {small.get('devices_omitted', 0)} atlandi")

    # Yuvarlama kontrolu: uzun ondalik kaldi mi?
    import re
    long_floats = re.findall(r"\d+\.\d{5,}", raw)
    print(f"5+ ondalikli sayi: {len(long_floats)}  {long_floats[:3]}")

asyncio.run(main())
