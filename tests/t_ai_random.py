"""AI akis uretimi RASTGELE mimaride calisiyor mu?

t_ai_flow.py tek elle yazilmis agda olcmustu (access-core-fiber/yedek).
Butun AI sayilarim (%38 pay, 0 kayip) o tek agdan geliyordu. Burada ayni
sey N rastgele mimaride olculuyor: model 4 cikisli bir agda bacak
secebiliyor mu, yoksa 2 cikista ezberledigi sey mi isliyordu.
"""
import asyncio, random, sys, time
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.ai.analyst import AIAnalyst
from ntc.ai.flowai import pins_for
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.core.models import TrafficClass as TC
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.traffic.topology import Topology, INTERNET

SINIFLAR = [TC.REALTIME, TC.INTERACTIVE, TC.STREAMING, TC.BULK, TC.BACKGROUND]

AGLAR = [  # (tohum, site, cikis, indirme, yukleme, saat)
    (201, 1, 1, 200.0, 20.0, 14), (202, 1, 2, 200.0, 20.0, 14),
    (203, 2, 3, 300.0, 40.0, 14), (204, 3, 2, 300.0, 40.0,  3),
    (205, 3, 4, 500.0, 50.0, 14), (206, 4, 4, 1000.0, 100.0, 14),
    (207, 2, 5, 150.0, 15.0, 21), (208, 5, 3, 800.0, 80.0, 14),
    (209, 1, 4, 100.0, 10.0,  3), (210, 4, 1, 400.0, 40.0, 14),
]


def talepler(topo, tohum, n=12):
    """Kapasitenin ~1.8 katini isteyen karisik talep kumesi."""
    rnd = random.Random(tohum * 31)
    kap_d, kap_u = topo.wan_capacity()
    d = []
    for i in range(n):
        ad = f"cihaz-{i:02d}"
        g = topo.attach_point(ad)
        sinif = rnd.choice(SINIFLAR)
        if rnd.random() < 0.72:
            d.append(Demand(ad, g, sinif, kap_d * 1.8 / n * rnd.uniform(0.4, 1.6),
                            src=INTERNET, direction="down"))
        else:
            d.append(Demand(ad, INTERNET, sinif,
                            kap_u * 1.8 / n * rnd.uniform(0.4, 1.6), direction="up"))
    # LAN talebi de ekle: modele hic gitmemeli
    d.append(Demand("kamera", "nvr", TC.STREAMING, 60.0,
                    src=topo.attach_point("kamera"), direction="lan"))
    return d


async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    analyst = AIAnalyst(cfg.ai, provider)
    print(f"saglayici: {provider.name} / {provider.model}\n")
    print(f"{'ag':<14}{'kap d/u':>13}{'talep':>8}{'gecerli':>9}"
          f"{'cevap':>7}{'ihlal':>7}{'pay':>7}{'kayip':>8}{'sure':>7}")
    print("-" * 84)

    satirlar = []
    for tohum, s, c, dd, uu, saat in AGLAR:
        topo = Topology.generate(seed=tohum, sites=s, egresses=c,
                                 downlink_mbps=dd, uplink_mbps=uu)
        d = talepler(topo, tohum)
        kap_d, kap_u = topo.wan_capacity()
        lp = FlowOptimizer(topo).solve(d)
        lp_top = sum(a.granted_mbps for a in lp.allocations)

        t0 = time.time()
        try:
            plan, atlanan, _ = await analyst.propose_flow(topo, d, clock_hour=saat)
        except Exception as exc:
            print(f"{s}s/{c}c(t{tohum}){'':<2} HATA: {exc}")
            satirlar.append((f"{s}s/{c}c", False, 0, 0, 0.0, 0.0, 0.0, []))
            continue
        sure = time.time() - t0

        pins = pins_for(plan, d) if plan.valid else {}
        hib = FlowOptimizer(topo).solve(d, pinned=pins)
        hib_top = sum(a.granted_mbps for a in hib.allocations)
        ai_pay = sum(min(pins.get(a.demand.key, 0.0), a.granted_mbps)
                     for a in hib.allocations)
        pay = ai_pay / hib_top if hib_top > 0 else 0.0
        kayip = lp_top - hib_top

        # LAN modele gitti mi?
        lan_gitti = any(g.direction == "lan" or g.device == "kamera"
                        for g in plan.grants)
        bacaklar = {g.egress for g in plan.grants if g.egress}
        gecerli_bacaklar = {f"cikis-{i}" for i in range(1, c + 1)}
        uydurma = bacaklar - gecerli_bacaklar

        print(f"{s}s/{c}c(t{tohum}){'':<2}{kap_d:.0f}/{kap_u:.0f}".ljust(14 + 13)
              + f"{len(d):>8}{str(plan.valid):>9}{len(plan.grants):>7}"
                f"{len(plan.issues):>7}{pay * 100:>6.0f}%{kayip:>8.1f}{sure:>6.0f}s")
        satirlar.append((f"{s}s/{c}c", plan.valid, len(plan.grants),
                         len(plan.issues), pay, kayip, sure,
                         (["LAN modele gitti"] if lan_gitti else [])
                         + ([f"uydurma bacak: {uydurma}"] if uydurma else [])
                         + ([f"bacak secmedi"] if plan.valid and not bacaklar else [])))

    print("-" * 84)
    ge = [x for x in satirlar if x[1]]
    print(f"gecerli plan       : {len(ge)}/{len(satirlar)}")
    if ge:
        print(f"ortalama AI payi   : %{sum(x[4] for x in ge) / len(ge) * 100:.0f}"
              f"  (en dusuk %{min(x[4] for x in ge)*100:.0f}, "
              f"en yuksek %{max(x[4] for x in ge)*100:.0f})")
    print(f"toplam kayip       : {sum(x[5] for x in satirlar):.1f} Mbps"
          f"  (en kotu {max((x[5] for x in satirlar), default=0):.1f})")
    print(f"toplam ihlal       : {sum(x[3] for x in satirlar)}")
    kusur = [(x[0], x[7]) for x in satirlar if x[7]]
    if kusur:
        print("\nkusurlar:")
        for ad, k in kusur:
            print(f"  {ad}: {'; '.join(k)}")
    await provider.aclose()

if __name__ == "__main__":
    asyncio.run(main())
