"""MAX_ROWS'un gercek siniri nerede? Tahmin degil olcum.

Sinir uc yerden gelebilir:
  a) baglam penceresi (4096 token) — istem + CIKTI birlikte sigmali
  b) VRAM — uzun istem lm_head ayirmasini sisiriyor (daha once 1.2 GB'ta dustu)
  c) modelin kendisi — uzun listede satir atlamaya baslar

Hangisi once gelirse o sinirdir. Olcelim.
"""
import asyncio, sys, time
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import ntc.ai.flowai as flowai
from ntc.ai.analyst import AIAnalyst
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.core.models import TrafficClass as TC
from ntc.traffic.flowopt import Demand
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

SINIFLAR = [TC.REALTIME, TC.INTERACTIVE, TC.STREAMING, TC.BULK, TC.BACKGROUND]

def talepler(t, n):
    g = t.attach_point("x")
    out = []
    for i in range(n):
        s = SINIFLAR[i % 5]
        yon = "down" if i % 3 else "up"
        out.append(Demand(f"cihaz-{i:02d}",
                          g if yon=="down" else INTERNET, s,
                          10.0 + (i*7 % 90),
                          src=INTERNET if yon=="down" else None,
                          direction=yon))
    return out

async def main():
    cfg = load_config(); pr = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, pr)
    print(f"model: {pr.model} · baglam 4096 token\n")
    print(f"{'satir':>6}{'istem(kar)':>12}{'sure':>7}{'gecerli':>9}"
          f"{'cevaplanan':>12}{'ihlal':>7}{'onarim':>8}")
    print("-"*64)
    for n in (10, 16, 24, 32, 48):
        flowai.MAX_ROWS = n
        t = ag(); d = talepler(t, n)
        rows, kalan = flowai.demand_rows(d, limit=n)
        t0 = time.time()
        try:
            plan, atlanan, bilgi = await an.propose_flow(t, d, clock_hour=14)
            sure = time.time()-t0
            # istem uzunlugunu yeniden kur
            istek = "\n".join(f"- {r['id']}: {r['device']} ({r['direction']}, "
                              f"{r['class']}) istiyor {r['want_mbps']:.1f} Mbps"
                              for r in rows)
            print(f"{n:>6}{len(istek)+1400:>12}{sure:>6.0f}s{str(plan.valid):>9}"
                  f"{len(plan.grants):>12}{len(plan.issues):>7}"
                  f"{plan.repair_ratio:>8.2f}")
        except Exception as e:
            print(f"{n:>6}{'-':>12}{time.time()-t0:>6.0f}s  HATA: "
                  f"{type(e).__name__}: {str(e)[:60]}")
    print("-"*64)
    await pr.aclose()
asyncio.run(main())
