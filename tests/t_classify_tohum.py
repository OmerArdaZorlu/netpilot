"""SINIFLANDIRICI DOGRULUGU — 8 simulasyon tohumunda, dort kosulda.

DURUSTLUK NOTU: `classify.IP_RANGES` ile `catalog.APP_ENDPOINTS` ayni gercegi
anlatan iki tablo. Ikisi de bizim yazdigimiz icin "IP acikken %100" bir sey
kanitlamaz — kendi tablomuzu kendi tablomuzla dogrulamis oluruz. O yuzden asil
sayi A kosulu (IP yok) ve D kosulu (tablo eksik).

  A  IP YOK           port + sekil. Bir netflow toplayicisinin gordugu kadari.
                      Tablonun kendini dogrulamasi imkansiz. GERCEK TABAN.
  B  IP TAM           tablo eksiksiz. Ust sinir; gercek dunyada ulasilmaz.
  C  IP TAM + SUREC   Sysmon da devrede. Tavan.
  D  IP EKSIK         bloklarin yalniz ucte biri biliniyor. Gercek dunyaya en
                      yakin kosul: blok listeleri her zaman eksik ve eskir.
"""
import random
import sys

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

import ntc.traffic.classify as C
from ntc.traffic.classify import classify, signals_from_flow
from ntc.traffic.simulator import TrafficSimulator

SUREC = {
    "teams-call": "Teams.exe", "voip-sip": "softphone.exe",
    "https-web": "chrome.exe", "ssh": "ssh.exe", "rdp": "mstsc.exe",
    "game-udp": "steam.exe",
    # Tarayici tabanli: Sysmon konak sureci yazar, uygulamayi degil.
    "netflix": "chrome.exe", "youtube": "msedge.exe",
    "rtsp-camera": "camera-agent.exe",
    "windows-update": "usoclient.exe", "smb-backup": "backup-agent.exe",
    "cloud-sync": "OneDrive.exe",
    "dns": "svchost.exe", "ntp": "w32time.exe",
    "mqtt-telemetry": "mosquitto.exe", "os-telemetry": "svchost.exe",
}

TAM = list(C.IP_RANGES)
# Eksik tablo: uctebiri. Hangi ucu oldugunu tohum belirliyor ki
# "sansli ucu sectim" olmasin.
def eksik_tablo(tohum):
    r = random.Random(tohum)
    n = max(1, len(TAM) // 3)
    return r.sample(TAM, n)


def kur(bloklar):
    C.IP_RANGES = list(bloklar)
    C._NETS = [(__import__("ipaddress").ip_network(c), a) for c, a in bloklar]


def olc(akislar, *, ip, surec):
    d = 0
    for f in akislar:
        s = signals_from_flow(f, process=SUREC.get(f.app, "") if surec else "")
        if not ip:
            s.dst_ip = ""
        d += classify(s).traffic_class == f.traffic_class
    return d / len(akislar)


print(f"{'tohum':>7}{'akis':>7}{'A IP yok':>11}{'B IP tam':>11}"
      f"{'C +surec':>11}{'D IP eksik':>12}")
print("-" * 60)
top = [0.0] * 4
N = 0
for tohum in (11, 23, 37, 41, 59, 71, 83, 97):
    sim = TrafficSimulator(seed=tohum)
    ak = []
    for _ in range(120):
        ak.extend(sim.tick(1.0))
    kur(TAM)
    a = olc(ak, ip=False, surec=False)
    b = olc(ak, ip=True, surec=False)
    c = olc(ak, ip=True, surec=True)
    kur(eksik_tablo(tohum))
    d = olc(ak, ip=True, surec=False)
    kur(TAM)
    for i, v in enumerate((a, b, c, d)):
        top[i] += v * len(ak)
    N += len(ak)
    print(f"{tohum:>7}{len(ak):>7}{a*100:>10.1f}%{b*100:>10.1f}%"
          f"{c*100:>10.1f}%{d*100:>11.1f}%")
print("-" * 60)
print(f"{'AGIRLIKLI':>14}{N:>7}" + "".join(
    f"{top[i]/N*100:>10.1f}%" for i in range(3)) + f"{top[3]/N*100:>11.1f}%")
print()
print("Okuma: A gercek tabandir — hicbir tablo kendini dogrulamiyor.")
print("       D gercek dunyaya en yakin: blok listesi her zaman eksiktir.")
print("       B ve C ust sinir; ikisi de bizim yazdigimiz tabloya dayaniyor.")
