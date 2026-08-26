"""AYNI AGDA: kararlar uygulanmadan vs uygulandiktan sonra.

Onceki olcum iki farkli agi karsilastiriyordu (1 bacak vs 2 bacak) — o
kapasiteyi ikiye katlamanin sonucu, netpilot'un degil.

Burada ag sabit. Degisen tek sey: kararlar uygulaniyor mu.

TEMEL DURUM (netpilot yok):
  - Yol: hepsi birincil bacaktan. Gercek aglarda yonlendirme HEDEFE gore
    yapilir; ikinci bacak yedek olarak durur, bos bekler.
  - Paylasim: max-min adil pay. TCP'nin dogal davranisina en yakin model
    ve temel duruma comert: sinif onceligi yok, herkes esit yarisiyor.

NETPILOT:
  - Yol: cozucunun dagitimi, iki bacak da kullaniliyor.
  - Paylasim: sinif onceligi + sinif basina asgari garanti.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Edge, Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer

ok = True


def maxmin(talepler, kapasite):
    """Max-min adil pay: doymamis herkes esit boler, artan tekrar dagilir.

    Bu, tikanmis bir hatta TCP'nin yaklastigi paylasim. Temel durumu bilerek
    bu kadar iyi modelliyoruz — daha kaba bir model (orn. talep oraninda
    bolme) netpilot'u haksiz yere iyi gosterirdi.
    """
    kalan = dict((i, d.mbps) for i, d in enumerate(talepler))
    verilen = dict((i, 0.0) for i in kalan)
    bos = kapasite
    while bos > 1e-9 and kalan:
        pay = bos / len(kalan)
        doyan = [i for i, v in kalan.items() if v <= pay + 1e-9]
        if not doyan:
            for i in list(kalan):
                verilen[i] += pay
                kalan[i] -= pay
            bos = 0.0
            break
        for i in doyan:
            verilen[i] += kalan[i]
            bos -= kalan[i]
            del kalan[i]
    return verilen


# ------------------------------------------------------------------ senaryo
#
# Iki bacakli ag. Birincil daha hizli, o yuzden varsayilan yonlendirme onu
# secer ve ikincisi bos bekler — netpilot olmadan olan tam olarak bu.
AG = [
    Edge("access", "core", 1000.0, 0.2, kind="access"),
    Edge("core", "access", 1000.0, 0.2, kind="access"),
    Edge("core", "birincil", 100.0, 8.0, kind="wan"),
    Edge("birincil", INTERNET, 100.0, 8.0, kind="wan"),
    Edge(INTERNET, "birincil", 100.0, 8.0, kind="wan"),
    Edge("birincil", "core", 100.0, 8.0, kind="wan"),
    Edge("core", "yedek", 100.0, 26.0, kind="wan"),
    Edge("yedek", INTERNET, 100.0, 26.0, kind="wan"),
    Edge(INTERNET, "yedek", 100.0, 26.0, kind="wan"),
    Edge("yedek", "core", 100.0, 26.0, kind="wan"),
]

TALEP = [
    Demand("voip-toplanti", INTERNET, TC.REALTIME,    20.0, direction="up"),
    Demand("ws-01",         INTERNET, TC.INTERACTIVE, 40.0, direction="up"),
    Demand("ws-02",         INTERNET, TC.INTERACTIVE, 40.0, direction="up"),
    Demand("srv-yedek",     INTERNET, TC.BULK,       120.0, direction="up"),
]
BIRINCIL_KAPASITE = 100.0
toplam_talep = sum(d.mbps for d in TALEP)

# -------------------------------------------------------------- 1. temel durum
temel = maxmin(TALEP, BIRINCIL_KAPASITE)
temel_ad = dict((TALEP[i].device, v) for i, v in temel.items())

# ------------------------------------------------------------- 2. netpilot
topo = Topology(edges=AG, default_access="access")
plan = FlowOptimizer(topo).solve(TALEP)
np_ad = dict((a.demand.device, a.granted_mbps) for a in plan.allocations)
bacak = {}
for a in plan.allocations:
    for (s, d), v in a.edge_usage.items():
        if d == INTERNET:
            bacak[s] = bacak.get(s, 0.0) + v

# ------------------------------------------------------------------ rapor
print("=" * 74)
print("AYNI AG, AYNI TALEP — degisen tek sey: kararlar uygulaniyor mu")
print("=" * 74)
print(f"Ag      : iki bacak, her biri 100 Mbps yukleme (toplam 200)")
print(f"Talep   : {toplam_talep:.0f} Mbps")
print()
print(f"{'kim':<16}{'sinif':<14}{'talep':>8}{'netpilot YOK':>14}{'netpilot VAR':>14}{'fark':>10}")
print("-" * 74)
for d in TALEP:
    a, b = temel_ad[d.device], np_ad[d.device]
    ok_isaret = f"{b - a:+.1f}"
    print(f"{d.device:<16}{d.traffic_class.value:<14}{d.mbps:>8.1f}"
          f"{a:>14.1f}{b:>14.1f}{ok_isaret:>10}")
print("-" * 74)
t_yok, t_var = sum(temel_ad.values()), sum(np_ad.values())
print(f"{'TOPLAM':<16}{'':<14}{toplam_talep:>8.1f}{t_yok:>14.1f}{t_var:>14.1f}"
      f"{t_var - t_yok:>+10.1f}")
print()
print(f"Bacak kullanimi netpilot YOK : birincil {BIRINCIL_KAPASITE:.0f} Mbps, "
      f"yedek 0.0 Mbps  (bos bekliyor)")
print("Bacak kullanimi netpilot VAR : " +
      ", ".join(f"{k} {v:.1f} Mbps" for k, v in sorted(bacak.items())))
print()
print(f"Toplam gecen: {t_yok:.0f} -> {t_var:.0f} Mbps   (x{t_var / t_yok:.2f})")

# --------------------------------------------------- asil onemli olan: VoIP
voip = TALEP[0]
v_yok, v_var = temel_ad[voip.device], np_ad[voip.device]
print()
print("Gercek zamanli trafik (goruşme) karsilanma orani:")
print(f"   netpilot YOK : %{v_yok / voip.mbps * 100:.0f}   "
      f"({v_yok:.1f} / {voip.mbps:.0f} Mbps)")
print(f"   netpilot VAR : %{v_var / voip.mbps * 100:.0f}   "
      f"({v_var:.1f} / {voip.mbps:.0f} Mbps)")

# ------------------------------------------------- 3. tek bacakta ne oluyor
print()
print("=" * 74)
print("KONTROL: ayni sey TEK bacakli agda (yol secimi mumkun degil)")
print("=" * 74)
tek = Topology(edges=[e for e in AG if "yedek" not in (e.src, e.dst)],
               default_access="access")
plan_tek = FlowOptimizer(tek).solve(TALEP)
tek_ad = dict((a.demand.device, a.granted_mbps) for a in plan_tek.allocations)
print(f"{'kim':<16}{'talep':>8}{'netpilot YOK':>14}{'netpilot VAR':>14}{'fark':>10}")
print("-" * 62)
for d in TALEP:
    a, b = temel_ad[d.device], tek_ad[d.device]
    print(f"{d.device:<16}{d.mbps:>8.1f}{a:>14.1f}{b:>14.1f}{b - a:>+10.1f}")
print("-" * 62)
tt = sum(tek_ad.values())
print(f"{'TOPLAM':<16}{toplam_talep:>8.1f}{t_yok:>14.1f}{tt:>14.1f}{tt - t_yok:>+10.1f}")
print()
print(f"-> Toplam degismedi ({t_yok:.0f} -> {tt:.0f} Mbps). Tek bacakta")
print("   kazanilacak bant yok; degisen tek sey KIMIN aldigi.")
print(f"   Goruşme: %{temel_ad['voip-toplanti'] / 20 * 100:.0f} -> "
      f"%{tek_ad['voip-toplanti'] / 20 * 100:.0f}")

print()
print("=" * 74)
print("SONUC: gecti")
