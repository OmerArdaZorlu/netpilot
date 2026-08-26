"""GERCEK MODEL: duruma gore hedefi kurabiliyor mu?

Dort farkli durum veriliyor, her birinde beklenen davranis onceden yazili.
Model sayi hesaplamiyor — siralama ve agirlik uretiyor. Sorulan soru:
"bu kadarini becerebiliyor mu?"

Sonuc ne cikarsa yazilacak. Beceremiyorsa da yazilacak.
"""
import asyncio
import sys
import time

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.ai.analyst import AIAnalyst
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.core.models import Alert, LinkStats, Severity, new_id, now
from ntc.traffic.flowpolicy import DEFAULT_POLICY
from ntc.traffic.topology import Edge, Topology, INTERNET


class SahteMetrics:
    """Sabit olcum dondurur — modelin girdisini tam kontrol edebilmek icin."""
    def __init__(self, st):
        self._st = st
    def link_stats(self):
        return self._st


def stats(down, up, rtt, retr, siniflar):
    return LinkStats(
        window_seconds=60.0, down_bps=0.0, up_bps=0.0,
        down_utilization=down, up_utilization=up, lan_bps=0.0,
        flow_count=0, device_count=8,
        avg_rtt_ms=rtt, retransmit_rate=retr,
        per_class_bps={k: v * 1e6 for k, v in siniflar.items()},
        per_device_bps={},
    )


def uyari(baslik):
    return Alert(id=new_id("alr"), ts=now(), severity=Severity.MEDIUM,
                 source="optimizer", title=baslik, detail="", meta={})


def ag(sayacli=False, bozuk=False):
    ucret = 5.0 if sayacli else 0.0
    saglik = 0.25 if bozuk else 1.0
    return Topology(edges=[
        Edge("access", "core", 1000.0, 0.2, kind="access"),
        Edge("core", "access", 1000.0, 0.2, kind="access"),
        Edge("core", "fiber", 20.0, 8.0, 0.0, saglik, kind="wan"),
        Edge("fiber", INTERNET, 20.0, 8.0, 0.0, saglik, kind="wan"),
        Edge(INTERNET, "fiber", 200.0, 8.0, 0.0, saglik, kind="wan"),
        Edge("fiber", "core", 200.0, 8.0, 0.0, saglik, kind="wan"),
        Edge("core", "yedek", 20.0, 30.0, ucret, kind="wan"),
        Edge("yedek", INTERNET, 20.0, 30.0, ucret, kind="wan"),
        Edge(INTERNET, "yedek", 100.0, 30.0, ucret, kind="wan"),
        Edge("yedek", "core", 100.0, 30.0, ucret, kind="wan"),
    ], default_access="access")


DURUMLAR = [
    dict(
        ad="A) MESAI SAATI, sakin",
        saat=11,
        st=stats(0.45, 0.30, 35, 0.004,
                 {"interactive": 40, "streaming": 25, "background": 5,
                  "realtime": 15, "bulk": 15}),
        topo=ag(),
        uyarilar=[],
        beklenen="realtime/interactive ustlerde kalmali",
        kontrol=lambda p: p.priority_of("realtime") <= 1
                          and p.priority_of("bulk") >= 3
                          and p.floor_of("realtime") > p.floor_of("bulk"),
    ),
    dict(
        ad="B) GECE 03:00, yedekleme penceresi",
        saat=3,
        st=stats(0.88, 0.91, 60, 0.010,
                 {"bulk": 82, "background": 12, "interactive": 4,
                  "streaming": 2}),
        topo=ag(),
        uyarilar=[uyari("Yükleme hattında tıkanma")],
        beklenen="bulk yukari cikmali (ofis bos, yedekleme bitmeli)",
        kontrol=lambda p: p.priority_of("bulk") < DEFAULT_POLICY.class_order.index("bulk")
                          or p.floor_of("bulk") > DEFAULT_POLICY.floor_of("bulk"),
    ),
    dict(
        ad="C) SAYACLI HAT devrede, tikanma yok",
        saat=14,
        st=stats(0.35, 0.25, 30, 0.003,
                 {"interactive": 45, "streaming": 30, "bulk": 15,
                  "realtime": 8, "background": 2}),
        topo=ag(sayacli=True),
        uyarilar=[],
        beklenen="cost_weight yukselmeli (para onemli, tikanma yok)",
        kontrol=lambda p: p.cost_weight > DEFAULT_POLICY.cost_weight,
    ),
    dict(
        ad="D) HAT BOZULDU + kalite dustu",
        saat=15,
        st=stats(0.93, 0.80, 210, 0.075,
                 {"streaming": 50, "bulk": 30, "interactive": 12,
                  "realtime": 6, "background": 2}),
        topo=ag(bozuk=True),
        uyarilar=[uyari("İndirme hattı doygun"), uyari("Hat kalitesi bozuldu")],
        beklenen="health veya latency agirligi yukselmeli",
        kontrol=lambda p: ((p.health_weight > DEFAULT_POLICY.health_weight
                            or p.latency_weight > DEFAULT_POLICY.latency_weight)
                           and p.priority_of("realtime") <= 1),
    ),
]


async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    analyst = AIAnalyst(cfg.ai, provider)
    print(f"saglayici: {provider.name} / {provider.model}\n")

    gecen = 0
    for d in DURUMLAR:
        print("=" * 74)
        print(d["ad"])
        print("=" * 74)
        print("  beklenen:", d["beklenen"])
        t0 = time.time()
        pol, sorunlar, sonuc = await analyst.decide_policy(
            SahteMetrics(d["st"]), d["topo"], d["uyarilar"],
            current=DEFAULT_POLICY, clock_hour=d["saat"])
        sure = time.time() - t0

        print(f"  sonuc   : {sonuc}  ({sure:.1f} sn)")
        if sorunlar:
            for x in sorunlar:
                print("     uyari:", x)
        if sonuc != "kabul":
            print("  -> model kullanilabilir bir hedef uretmedi")
            print()
            continue

        print("  durum   :", pol.situation or "-")
        print("  gerekce :", pol.rationale or "-")
        print("  sira    :", " > ".join(pol.class_order))
        print("  tabanlar:", {k: round(v, 3) for k, v in pol.floors.items()},
              f"(toplam %{sum(pol.floors.values())*100:.0f})")
        print(f"  agirlik : gecikme {pol.latency_weight:g} / "
              f"para {pol.cost_weight:g} / saglik {pol.health_weight:g}")
        fark = pol.diff(DEFAULT_POLICY)
        print("  fark    :", fark or "varsayilandan farksiz")

        try:
            uygun = bool(d["kontrol"](pol))
        except Exception as exc:
            uygun = False
            print("     kontrol hatasi:", exc)
        print(f"  BEKLENTIYE UYDU: {'EVET' if uygun else 'HAYIR'}")
        gecen += 1 if uygun else 0
        print()

    print("=" * 74)
    print(f"OZET: {gecen}/{len(DURUMLAR)} durumda beklenen yonde hedef kurdu")
    print("=" * 74)
    await provider.aclose()


asyncio.run(main())
