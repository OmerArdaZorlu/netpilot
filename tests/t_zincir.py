"""TUM ZINCIR, AI DAHIL, RASTGELE MIMARIDE.

t_random_topo.py zinciri AI'siz kosturuyordu (cozucu -> aksiyon -> politika
-> infaz). Burada AI da devrede: modelin tahsisi cozucuye pin olarak giriyor
ve ciktinin ta cihaz komutuna kadar saglam kaldigi olculuyor.

Kontroller:
  1. AI plani -> pin -> cozucu: kayipsiz mi (LP tek basina kadar geciriyor mu)
  2. Yol atayici: her akis bir cikisa dusuyor mu, dagilim plana uyuyor mu,
     ayni akis hep ayni cikisa mi dusuyor (yapiskanlik)
  3. Politika uretimi: hicbir kural olmayan cihaz/bacak adi tasimiyor mu
  4. Infaz: kapsam basina dogru surucuye gidiyor mu, onaysiz kisan kural
     kuruluyor mu, ikinci turda fark uretiyor mu
  5. Kapanis: rollback sonrasi aktif kural sifir mi
"""
import asyncio
import sys

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

import t_ai_random as T
from ntc.ai.analyst import AIAnalyst
from ntc.ai.flowai import pins_for
from ntc.ai.provider import create_provider
from ntc.core.config import load_config
from ntc.enforce.drivers import LinuxTcDriver, WindowsQosDriver
from ntc.enforce.engine import Enforcer
from ntc.enforce.policy import SCOPE_CORE, SCOPE_EDGE, policies_from_plan
from ntc.traffic.flowopt import FlowOptimizer, PathAssigner, actions_from_plan
from ntc.traffic.topology import INTERNET, Topology

AGLAR = [(601, 1, 2, 200., 20.), (602, 3, 3, 400., 40.), (603, 2, 4, 700., 70.),
         (604, 4, 1, 120., 12.), (605, 5, 5, 1200., 120.), (606, 1, 3, 60., 6.),
         (607, 3, 2, 900., 90.), (608, 2, 5, 250., 25.)]

kusurlar: list[str] = []


def hata(m):
    kusurlar.append(m)


async def main():
    cfg = load_config()
    provider = await create_provider(cfg.ai)
    an = AIAnalyst(cfg.ai, provider)
    print(f"saglayici: {provider.name} / {provider.model}\n")
    print(f"{'ag':<14}{'kayip':>8}{'akis':>7}{'atanan':>8}{'yapis':>7}{'sapma':>7}"
          f"{'kural':>7}{'komut':>7}{'onaysiz':>9}{'2.tur':>7}{'kapanis':>9}")
    print("-" * 84)

    for tohum, s, c, d, u in AGLAR:
        topo = Topology.generate(seed=tohum, sites=s, egresses=c,
                                 downlink_mbps=d, uplink_mbps=u)
        talep = T.talepler(topo, tohum)
        ad = f"{s}s/{c}c t{tohum}"

        # --- 1. AI -> pin -> cozucu
        lp = FlowOptimizer(topo).solve(talep)
        lp_top = sum(a.granted_mbps for a in lp.allocations)
        plan_ai, _atl, _b = await an.propose_flow(topo, talep, clock_hour=14)
        pins = pins_for(plan_ai, talep) if plan_ai.valid else {}
        plan = FlowOptimizer(topo).solve(talep, pinned=pins)
        top = sum(a.granted_mbps for a in plan.allocations)
        kayip = lp_top - top
        if kayip > 0.5:
            hata(f"{ad}: AI pinleri {kayip:.1f} Mbps kaybettiriyor")

        # --- 2. yol atayici
        pa = PathAssigner(plan)
        akis = atanan = 0
        yapiskan = True
        cikislar = {f"cikis-{i}" for i in range(1, c + 1)}
        for a in plan.allocations:
            if a.granted_mbps <= 0.01 or a.demand.direction == "lan":
                continue
            for k in range(6):
                akis += 1
                anahtar = f"{a.demand.device}:{k}:443"
                dst = pa.assign(a.demand.device, a.demand.traffic_class.value,
                                a.demand.direction, anahtar)
                if dst:
                    atanan += 1
                    if dst not in cikislar:
                        hata(f"{ad}: yol atayici olmayan cikis verdi: {dst!r}")
                    # yapiskanlik: ayni anahtar tekrar sorulunca ayni cevap
                    if pa.assign(a.demand.device,
                                 a.demand.traffic_class.value,
                                 a.demand.direction, anahtar) != dst:
                        yapiskan = False
        # Tek cikisli agda secilecek yol yok; `update()` bu akislari bilerek
        # tabloya almiyor. Bos donmesi dogru davranis, kusur degil.
        if c >= 2 and akis and atanan == 0:
            hata(f"{ad}: cok cikisli agda hicbir akisa cikis atanmadi")
        if not yapiskan:
            hata(f"{ad}: yol atamasi yapiskan degil — akis ortasinda yol degisir")

        # Dagilim plani takip ediyor mu: 2000 akis anahtari at, ampirik
        # oranlar planin kenar kullanimina uymali (+-%5).
        sapma = 0.0
        for a in plan.allocations:
            if a.granted_mbps <= 0.01 or a.demand.direction == "lan":
                continue
            dallar = {}
            for (src, dst), v in a.edge_usage.items():
                if v > 1e-9:
                    dallar.setdefault(src, {})[dst] = v
            _, cik = max(dallar.items(), key=lambda kv: len(kv[1]),
                         default=(None, {}))
            if len(cik) < 2:
                continue
            tp = sum(cik.values())
            bek = {k: v / tp for k, v in cik.items()}
            sayim = {}
            N = 2000
            for i in range(N):
                dst = pa.assign(a.demand.device, a.demand.traffic_class.value,
                                a.demand.direction, f"{a.demand.device}:f{i}:443")
                sayim[dst] = sayim.get(dst, 0) + 1
            for k, oran in bek.items():
                fark = abs(sayim.get(k, 0) / N - oran)
                sapma = max(sapma, fark)
                if fark > 0.05:
                    hata(f"{ad}: yol dagilimi plandan sapiyor — {k} "
                         f"beklenen %{oran*100:.1f}, olculen "
                         f"%{sayim.get(k,0)/N*100:.1f}")

        # --- 3. politika uretimi
        cihazlar = {a.demand.device: None for a in plan.allocations}
        pset = policies_from_plan(plan, cihazlar)
        kurallar = list(pset.rules)
        gecerli_dugum = set(topo.nodes)
        for r in kurallar:
            if r.scope not in (SCOPE_EDGE, SCOPE_CORE):
                hata(f"{ad}: taninmayan kapsam {r.scope!r}")
            hedef = getattr(r, "egress", "")
            if hedef and hedef not in gecerli_dugum:
                hata(f"{ad}: kural olmayan dugume isaret ediyor: {hedef!r}")

        # --- 4. infaz, kapsam basina ayri surucu
        enf = Enforcer({SCOPE_CORE: LinuxTcDriver(),
                        SCOPE_EDGE: WindowsQosDriver()})
        onaysiz = enf.reconcile(pset, approved=set())
        kisan = [r for r in kurallar if r.kind in ("rate", "path")]
        aktif_kisan = sum(1 for k in getattr(onaysiz, "added", [])
                          if getattr(k, "kind", "") in ("rate", "path"))
        if aktif_kisan:
            hata(f"{ad}: onaysiz {aktif_kisan} kisan kural kuruldu")

        onayli = {r.key for r in kurallar}
        r1 = enf.reconcile(pset, approved=onayli)
        komut = len(getattr(r1, "commands", []))
        r2 = enf.reconcile(pset, approved=onayli)
        ikinci = len(getattr(r2, "commands", []))
        if ikinci:
            hata(f"{ad}: degismeyen plan 2. turda {ikinci} komut uretti")

        # --- 5. kapanis
        geri = enf.rollback()
        kalan = len(enf._state)
        if kalan:
            hata(f"{ad}: kapanistan sonra {kalan} kural aktif kaldi")

        print(f"{ad:<14}{kayip:>8.1f}{akis:>7}{atanan:>8}"
              f"{'evet' if yapiskan else 'HAYIR':>7}{sapma*100:>6.1f}%"
              f"{len(kurallar):>7}"
              f"{komut:>7}{aktif_kisan:>9}{ikinci:>7}{kalan:>9}")

    print("-" * 84)
    if kusurlar:
        print(f"KALDI — {len(kusurlar)} kusur")
        for k in kusurlar[:20]:
            print("  -", k)
    else:
        print(f"HEPSI GECTI — {len(AGLAR)} rastgele mimaride tam zincir")
    await provider.aclose()
    return 1 if kusurlar else 0


sys.exit(asyncio.run(main()))
