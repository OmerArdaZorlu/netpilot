"""AI analizi ne siklikta kesiliyor ve sebebi ne?

Canli kosuda goruldu: 'JSON tamamlanmamis'. Model baglami 4096 token
(istem + cikti birlikte). Istem buyuduce ciktiya yer kalmiyor olabilir.
Olculen: istem uzunlugu <-> basarisizlik iliskisi.
"""
import asyncio, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.analyst import AIAnalyst
from ntc.ai.prompts import ANALYST_SYSTEM, ANALYST_USER
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.traffic.metrics import MetricsEngine
from ntc.traffic.optimizer import TrafficOptimizer
from ntc.traffic.simulator import TrafficSimulator

TUR = 12

async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, provider)
    sim = TrafficSimulator(seed=31)
    met = MetricsEngine(cfg.link, cfg.collector.window_seconds)
    opt = TrafficOptimizer(cfg.optimizer, cfg.link)
    for _ in range(90):
        met.add(sim.tick(1.0))

    print(f"{'tur':>4}{'istem':>8}{'cikti':>8}{'sure':>7}  durum")
    print("-" * 56)
    hata = 0
    for i in range(TUR):
        for _ in range(15):
            met.add(sim.tick(1.0))
        snap = an.build_snapshot(met, sim.devices, opt)
        from ntc.ai.analyst import _snapshot_json
        f = an._facts(None, None)
        user = ANALYST_USER.format(
            snapshot=_snapshot_json(snap), alerts=f["alerts_text"],
            flow=f["flow_text"],
            targets=", ".join(sorted(an._valid_targets(sim.devices))))
        istem = len(ANALYST_SYSTEM) + len(user)
        r = await an.analyze(met, sim.devices, opt)
        ok = not r.error
        hata += 0 if ok else 1
        cikti = len(r.summary or "") + sum(
            len(str(x)) for x in (r.findings or []) + (r.recommendations or []))
        print(f"{i+1:>4}{istem:>8}{cikti:>8}{r.latency_ms/1000:>6.0f}s  "
              + ("ok" if ok else f"HATA: {(r.error or '')[:44]}"))
    print("-" * 56)
    print(f"basarisiz: {hata}/{TUR}  (%{hata/TUR*100:.0f})")
    await provider.aclose()

asyncio.run(main())
