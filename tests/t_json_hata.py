"""AI analizi hangi JSON hatalarindan dusuyor ve ne siklikta?

Panelde goruldu: 'Expecting property name enclosed in double quotes'.
Bu kesilme DEGIL — belgenin ortasinda bozukluk. Kurtarma katmani bunu
kapsamiyordu. Once siklik ve tur dagilimi.
"""
import asyncio, collections, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.analyst import AIAnalyst
from ntc.ai.prompts import ANALYST_SYSTEM, ANALYST_USER
from ntc.ai.provider import create_provider, extract_json
from ntc.core.config import load_config
from ntc.traffic.metrics import MetricsEngine
from ntc.traffic.optimizer import TrafficOptimizer
from ntc.traffic.simulator import TrafficSimulator

TUR = 25

async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, provider)
    sim = TrafficSimulator(seed=53)
    met = MetricsEngine(cfg.link, cfg.collector.window_seconds)
    opt = TrafficOptimizer(cfg.optimizer, cfg.link)
    for _ in range(90):
        met.add(sim.tick(1.0))

    from ntc.ai.analyst import _snapshot_json
    turler = collections.Counter()
    hamlar = []
    for i in range(TUR):
        for _ in range(12):
            met.add(sim.tick(1.0))
        snap = an.build_snapshot(met, sim.devices, opt)
        f = an._facts(None, None)
        user = ANALYST_USER.format(
            snapshot=_snapshot_json(snap), alerts=f["alerts_text"],
            flow=f["flow_text"],
            targets=", ".join(sorted(an._valid_targets(sim.devices))))
        ham = await provider.complete(ANALYST_SYSTEM, user, json_mode=True)
        try:
            d = extract_json(ham)
            turler["ok" if isinstance(d, dict) else "sozluk-degil"] += 1
        except Exception as e:
            msg = str(e)
            if "tamamlanmam" in msg or "Unterminated" in msg or "Expecting value" in msg:
                t = "kesilme"
            elif "property name" in msg:
                t = "tirnaksiz-anahtar"
            elif "delimiter" in msg:
                t = "eksik-ayirici"
            else:
                t = msg[:40]
            turler[t] += 1
            hamlar.append((t, ham))
        print(f"  {i+1}/{TUR}", end="\r", flush=True)

    print(" " * 20)
    print("=" * 60)
    for k, v in turler.most_common():
        print(f"  {k:<26}{v:>4}  (%{v/TUR*100:.0f})")
    print("=" * 60)
    for t, ham in hamlar[:3]:
        print(f"\n--- {t} ---")
        print(ham[:900])
    await provider.aclose()

asyncio.run(main())
