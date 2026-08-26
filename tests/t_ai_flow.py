"""AI AKISI KENDISI KURUYOR — ne kadar iyi kuruyor?

Onceki tasarimda AI hedefi kuruyordu, sayiyi LP hesapliyordu. Burada sayiyi
AI veriyor; LP karar verici degil HAKEM: ayni talebi cozup optimumu veriyor,
AI'in plani ona karsi olculuyor.

Olculen dort sey:
  vs_optimum   : AI'in gecirdigi toplam / LP'nin gecirdigi toplam
  repair_ratio : dogrulayicinin ne kadarini yeniden yazdigi (0 = hic)
  issues       : kural ihlali sayisi
  sinif karari : gorusme ve DNS'e ne yaptigi
"""
import asyncio
import sys
import time

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.ai.analyst import AIAnalyst
from ntc.ai.flowai import score
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.core.models import TrafficClass as TC
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.topology import Edge, Topology, INTERNET


def ag(sayacli=False):
    ucret = 4.0 if sayacli else 0.0
    return Topology(edges=[
        Edge("access", "core", 1000.0, 0.2, kind="access"),
        Edge("core", "access", 1000.0, 0.2, kind="access"),
        Edge("core", "fiber", 40.0, 8.0, 0.0, kind="wan"),
        Edge("fiber", INTERNET, 40.0, 8.0, 0.0, kind="wan"),
        Edge(INTERNET, "fiber", 200.0, 8.0, 0.0, kind="wan"),
        Edge("fiber", "core", 200.0, 8.0, 0.0, kind="wan"),
        Edge("core", "yedek", 20.0, 30.0, ucret, kind="wan"),
        Edge("yedek", INTERNET, 20.0, 30.0, ucret, kind="wan"),
        Edge(INTERNET, "yedek", 100.0, 30.0, ucret, kind="wan"),
        Edge("yedek", "core", 100.0, 30.0, ucret, kind="wan"),
    ], default_access="access")


def talepler(topo):
    g = topo.attach_point("x")
    return [
        Demand("voip",   g, TC.REALTIME,     40.0, src=INTERNET, direction="down"),
        Demand("ws-01",  g, TC.INTERACTIVE,  90.0, src=INTERNET, direction="down"),
        Demand("tv",     g, TC.STREAMING,   120.0, src=INTERNET, direction="down"),
        Demand("yedek",  g, TC.BULK,        400.0, src=INTERNET, direction="down"),
        Demand("dns",    g, TC.BACKGROUND,    8.0, src=INTERNET, direction="down"),
        Demand("srv",    INTERNET, TC.BULK,  80.0, direction="up"),
        Demand("voip",   INTERNET, TC.REALTIME, 15.0, direction="up"),
    ]


DURUMLAR = [
    ("A) MESAI SAATI, sayacsiz", 14, False),
    ("B) GECE 03:00, sayacsiz",   3, False),
    ("C) MESAI, SAYACLI yedek",  14, True),
]


async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    analyst = AIAnalyst(cfg.ai, provider)
    print(f"saglayici: {provider.name} / {provider.model}\n")

    ozet = []
    for ad, saat, sayacli in DURUMLAR:
        topo = ag(sayacli)
        d = talepler(topo)
        kap_d, kap_u = topo.wan_capacity()

        # HAKEM: ayni talep, LP ile
        lp = FlowOptimizer(topo).solve(d)
        lp_toplam = sum(a.granted_mbps for a in lp.allocations)
        lp_sinif = {}
        for a in lp.allocations:
            lp_sinif[a.demand.traffic_class.value] = \
                lp_sinif.get(a.demand.traffic_class.value, 0.0) + a.granted_mbps

        print("=" * 78)
        print(ad)
        print("=" * 78)
        print(f"  kapasite: indirme {kap_d:.0f} / yukleme {kap_u:.0f} Mbps · "
              f"talep {sum(x.mbps for x in d):.0f} Mbps")

        t0 = time.time()
        plan, atlanan, bilgi = await analyst.propose_flow(topo, d, clock_hour=saat)
        sure = time.time() - t0

        s = score(plan, lp_toplam, sum(x.mbps for x in d))
        print(f"  sure {sure:.1f} sn · gecerli={plan.valid} · "
              f"yanitlanmayan={len(atlanan)}")
        if plan.situation:
            print(f"  durum   : {plan.situation}")
        if plan.rationale:
            print(f"  gerekce : {plan.rationale}")

        if not plan.valid:
            print("  -> AI kullanilabilir bir akis uretemedi")
            for i in plan.issues[:4]:
                print("     ihlal:", i)
            ozet.append((ad, 0.0, 1.0, len(plan.issues)))
            print()
            continue

        print(f"\n  {'talep':<22}{'AI verdi':>10}{'LP verdi':>10}")
        ai_sinif = {}
        for g in plan.grants:
            ai_sinif[g.traffic_class] = ai_sinif.get(g.traffic_class, 0.0) + g.grant_mbps
        for k in ("realtime", "interactive", "streaming", "bulk", "background"):
            if k in ai_sinif or k in lp_sinif:
                print(f"  {k:<22}{ai_sinif.get(k,0):>10.1f}{lp_sinif.get(k,0):>10.1f}")
        print(f"  {'TOPLAM':<22}{s['ai_total_mbps']:>10.1f}{s['lp_total_mbps']:>10.1f}")
        print(f"\n  vs_optimum   : {s['vs_optimum']:.3f}   "
              f"(1.000 = LP kadar iyi)")
        print(f"  repair_ratio : {s['repair_ratio']:.3f}   "
              f"(0.000 = dogrulayici hic mudahale etmedi)")
        print(f"  ihlal sayisi : {s['issues']}")
        for i in plan.issues[:5]:
            print("     -", i)
        bacaklar = {}
        for g in plan.grants:
            if g.egress:
                bacaklar[g.egress] = bacaklar.get(g.egress, 0) + 1
        print(f"  bacak secimi : {bacaklar or 'hic secilmedi'}")
        ozet.append((ad, s["vs_optimum"], s["repair_ratio"], s["issues"]))
        print()

    print("=" * 78)
    print(f"{'durum':<30}{'vs_optimum':>12}{'onarim':>9}{'ihlal':>7}")
    print("-" * 78)
    for ad, v, r, i in ozet:
        print(f"{ad:<30}{v:>12.3f}{r:>9.3f}{i:>7}")
    print("=" * 78)
    await provider.aclose()


asyncio.run(main())
