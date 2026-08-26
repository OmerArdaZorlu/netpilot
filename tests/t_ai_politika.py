"""AI HEDEF SECIMI rastgele mimaride dogru dugmeyi ceviriyor mu?

Onceki 2/4 sayisi tek elle yazilmis agdan geliyordu. Burada her durum
farkli bir rastgele mimaride kuruluyor ve dogru cevap onceden yazili.
"""
import asyncio, sys
from dataclasses import replace
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.ai.analyst import AIAnalyst
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.core.models import LinkStats
from ntc.traffic.topology import Topology, INTERNET
from ntc.traffic.flowpolicy import WEIGHT_LEVELS


class SahteMetrik:
    """decide_policy yalniz link_stats() cagiriyor."""
    def __init__(self, st): self._st = st
    def link_stats(self): return self._st


def stat(du, uu, rtt, retx, paylar):
    return LinkStats(window_seconds=60.0, down_bps=0, up_bps=0,
                     down_utilization=du, up_utilization=uu, lan_bps=0,
                     flow_count=40, device_count=12, avg_rtt_ms=rtt,
                     retransmit_rate=retx,
                     per_class_bps={k: v * 1e6 for k, v in paylar.items()})


def sayacli_yap(t):
    """En az bir cikisi sayacli yap, otekileri ucretsiz."""
    e = []
    ilk = True
    for x in t.edges:
        if x.kind == "wan" and ("cikis-1" in (x.src, x.dst)):
            e.append(replace(x, cost_per_gb=4.0)); ilk = False
        elif x.kind == "wan":
            e.append(replace(x, cost_per_gb=0.0))
        else:
            e.append(x)
    return Topology(edges=e, default_access=t.default_access,
                    access_nodes=t.access_nodes)


def bozuk_yap(t):
    e = [replace(x, health=0.55) if (x.kind == "wan" and "cikis-1" in (x.src, x.dst))
         else (replace(x, health=1.0) if x.kind == "wan" else x) for x in t.edges]
    return Topology(edges=e, default_access=t.default_access,
                    access_nodes=t.access_nodes)


def ucretsiz_saglam(t):
    e = [replace(x, cost_per_gb=0.0, health=1.0) if x.kind == "wan" else x
         for x in t.edges]
    return Topology(edges=e, default_access=t.default_access,
                    access_nodes=t.access_nodes)


# (ad, tohum, site, cikis, d, u, saat, donusum, stat, kontrol, beklenen)
def _seviye(alan, deger):
    """Sayiyi geri kategoriye cevirir; FlowPolicy dogrulamada sayiya ceviriyor."""
    for ad, v in WEIGHT_LEVELS[alan].items():
        if abs(v - deger) < 1e-6:
            return ad
    return f"{deger}"

def k_para(p):
    s = _seviye("cost_weight", p.cost_weight)
    return s == "yuksek", f"cost_weight={s} ({p.cost_weight})"
def k_saglik(p):
    s = _seviye("health_weight", p.health_weight)
    return s == "yuksek", f"health_weight={s} ({p.health_weight})"
def k_gece(p):
    o = p.class_order
    return o.index("bulk") < o.index("streaming"), " > ".join(o)
def k_mesai(p):
    o = p.class_order
    return o.index("realtime") < o.index("bulk") and o.index("interactive") < o.index("bulk"), " > ".join(o)
def k_gecikme(p):
    s = _seviye("latency_weight", p.latency_weight)
    return s == "yuksek", f"latency_weight={s} ({p.latency_weight})"

DURUMLAR = [
 ("sayacli hat",     401,2,3,300.,30.,14, sayacli_yap,
  stat(.31,.18,45,.004,{"bulk":120,"streaming":80,"interactive":40}), k_para,   "cost_weight=yuksek"),
 ("sayacli hat 2",   402,1,2,600.,60.,14, sayacli_yap,
  stat(.24,.12,38,.006,{"streaming":200,"bulk":150,"interactive":60}), k_para,  "cost_weight=yuksek"),
 ("bozuk bacak",     403,3,4,500.,50.,14, bozuk_yap,
  stat(.70,.30,180,.055,{"interactive":90,"streaming":70,"bulk":50}), k_saglik, "health_weight=yuksek"),
 ("bozuk bacak 2",   404,2,2,200.,20.,14, bozuk_yap,
  stat(.65,.28,210,.071,{"realtime":40,"interactive":60,"bulk":40}), k_saglik,  "health_weight=yuksek"),
 ("gece yedekleme",  405,4,3,800.,80.,3,  ucretsiz_saglam,
  stat(.35,.60,22,.002,{"bulk":300,"background":20}), k_gece,      "bulk > streaming"),
 ("gece yedekleme 2",406,1,1,150.,15.,2,  ucretsiz_saglam,
  stat(.28,.75,18,.001,{"bulk":90,"background":8}), k_gece,        "bulk > streaming"),
 ("mesai tikanik",   407,5,4,1000.,100.,11, ucretsiz_saglam,
  stat(.95,.62,95,.018,{"streaming":400,"interactive":250,"bulk":200,"realtime":60}), k_mesai, "realtime+interactive > bulk"),
 ("mesai tikanik 2", 408,2,5,250.,25.,15, ucretsiz_saglam,
  stat(.97,.71,110,.022,{"bulk":150,"streaming":60,"interactive":30,"realtime":10}), k_mesai, "realtime+interactive > bulk"),
 ("gecikme yuksek",  409,3,2,400.,40.,14, ucretsiz_saglam,
  stat(.60,.35,240,.008,{"realtime":50,"interactive":120}), k_gecikme, "latency_weight=yuksek"),
 ("gecikme yuksek 2",410,1,4,120.,12.,10, ucretsiz_saglam,
  stat(.55,.30,310,.006,{"realtime":30,"interactive":50}), k_gecikme, "latency_weight=yuksek"),
]


async def main():
    cfg = load_config(); provider = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, provider)
    print(f"{'durum':<20}{'ag':<9}{'sonuc':<12}{'dogru':>7}   modelin verdigi")
    print("-" * 92)
    d = 0
    for ad, tohum, s, c, dd, uu, saat, don, st, kontrol, bek in DURUMLAR:
        t = don(Topology.generate(seed=tohum, sites=s, egresses=c,
                                  downlink_mbps=dd, uplink_mbps=uu))
        pol, sorun, sonuc = await an.decide_policy(SahteMetrik(st), t,
                                                   clock_hour=saat)
        ok, gorunen = kontrol(pol)
        if sonuc != "kabul":
            ok = False
        d += 1 if ok else 0
        print(f"{ad:<20}{s}s/{c}c{'':<3}{sonuc:<12}{'EVET' if ok else 'hayir':>7}"
              f"   {gorunen}   (bek: {bek})")
    print("-" * 92)
    print(f"DOGRU: {d}/{len(DURUMLAR)}")
    await provider.aclose()

asyncio.run(main())
