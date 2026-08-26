"""AI onerilerinin kac tanesi hedef yuzunden dusuyor?

Canli kosuda goruldu: model hedef alanina 'Cam-entrance ve cam-parking'
yaziyor ve oneri tumden dusuyor. Once ORANI olcelim, sonra duzeltelim.
"""
import asyncio, collections, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.ai.analyst import AIAnalyst
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.traffic.metrics import MetricsEngine
from ntc.traffic.optimizer import TrafficOptimizer
from ntc.traffic.simulator import TrafficSimulator

TUR = 14

async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, provider)
    sim = TrafficSimulator(seed=17)
    met = MetricsEngine(cfg.link, cfg.collector.window_seconds)
    opt = TrafficOptimizer(cfg.optimizer, cfg.link)

    ham_hedefler = []
    orijinal = AIAnalyst._resolve_targets.__func__

    @classmethod
    def izle(cls, raw, valid):
        r = orijinal(cls, raw, valid)
        ham_hedefler.append((raw, bool(r), len(r)))
        return r
    AIAnalyst._resolve_targets = izle

    for _ in range(90):
        met.add(sim.tick(1.0))
    for i in range(TUR):
        for _ in range(20):
            met.add(sim.tick(1.0))
        await an.analyze(met, sim.devices, opt)
        print(f"  tur {i+1}/{TUR} bitti", flush=True)

    n = len(ham_hedefler)
    dusen = [h for h, ok, _ in ham_hedefler if not ok]
    print()
    print("=" * 70)
    print(f"toplam oneri hedefi : {n}")
    print(f"cozulen             : {n - len(dusen)}  (%{(n-len(dusen))/max(n,1)*100:.0f})")
    print(f"DUSEN               : {len(dusen)}  (%{len(dusen)/max(n,1)*100:.0f})")
    print("=" * 70)
    if dusen:
        print("dusen hedefler (en sik):")
        for h, c in collections.Counter(dusen).most_common(15):
            print(f"  x{c:<3} {h!r}")
    await provider.aclose()

asyncio.run(main())
