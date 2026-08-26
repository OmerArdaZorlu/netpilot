"""SINIFLANDIRICI NE KADAR DOGRU?

Olcum simulatorun KENDI etiketine karsi: simulator akisi uretirken hangi
uygulamadan urettigini biliyor, siniflandirici bilmiyor. `app` ve
`traffic_class` alanlari siniflandiriciya HIC verilmiyor — verilseydi
kendi cevabimizi kopya cekmis olurduk.

Uc kosul olculuyor:
  A. YALNIZ AG GORUNUMU   port + sekil. Bir netflow toplayicisinin gordugu
     kadari. Taban bu.
  B. + HEDEF IP           gercek dunyada Netflix CDN'i taniniyor. Simulator
     hedef IP'yi uygulamadan bagimsiz sectigi icin burada OLCULEMEZ; bunun
     yerine gercekci bir IP atamasi kurgulanip ayrica olculuyor.
  C. + SUREC ADI          Sysmon Event 3. Tarayici tabanli akislar konak
     surece (chrome.exe) dustugu icin bu bile %100 vermiyor — asil soru o.
"""
import collections
import random
import sys

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.core.models import TrafficClass as TC
from ntc.traffic.catalog import APPS
from ntc.traffic.classify import (BASIS_DEFAULT, BASIS_IP, BASIS_PORT,
                                  BASIS_PROCESS, BASIS_SHAPE, PORT_AMBIGUOUS,
                                  classify, signals_from_flow)
from ntc.traffic.simulator import TrafficSimulator

# Gercek dunyada bir akisi hangi surec acar. Tarayici tabanli olanlar
# BILEREK konak surece dusuyor: Netflix'i tarayicidan izleyen kullanici
# icin Sysmon `chrome.exe` yazar, `netflix.exe` degil.
SUREC = {
    "teams-call": "Teams.exe", "voip-sip": "softphone.exe",
    "https-web": "chrome.exe", "ssh": "ssh.exe", "rdp": "mstsc.exe",
    "game-udp": "steam.exe",
    "netflix": "chrome.exe", "youtube": "msedge.exe",
    "rtsp-camera": "camera-agent.exe",
    "windows-update": "usoclient.exe", "smb-backup": "backup-agent.exe",
    "cloud-sync": "OneDrive.exe",
    "dns": "svchost.exe", "ntp": "w32time.exe",
    "mqtt-telemetry": "mosquitto.exe", "os-telemetry": "svchost.exe",
}

# Gercekci hedef IP: her uygulama kendi servis blogunda. Simulator bunu
# yapmiyor (ortak havuzdan rastgele seciyor) — IP katmanini olcmek icin
# burada kurguluyoruz.
IP = {
    "netflix": "23.246.2.11", "youtube": "142.250.187.14",
    "windows-update": "13.107.42.14",
}


def akislari_topla(n_tur=140):
    sim = TrafficSimulator(seed=11)
    hepsi = []
    for _ in range(n_tur):
        hepsi.extend(sim.tick(1.0))
    return hepsi


def olc(akislar, ad, *, surec=False, gercek_ip=False):
    dogru = 0
    per_sinif = collections.defaultdict(lambda: [0, 0])   # [dogru, toplam]
    per_katman = collections.defaultdict(lambda: [0, 0])
    karisiklik = collections.Counter()
    for f in akislar:
        s = signals_from_flow(f, process=SUREC.get(f.app, "") if surec else "")
        # Simulatorun hedef IP havuzu uygulamadan BAGIMSIZ. Yani bir
        # https-web akisi tesadufen Google blogunda cikabiliyor ve
        # siniflandirici onu hakli olarak youtube sayiyor. Bu olcumu
        # kirletir: IP katmaninin katkisini degil, simulatorun rastgeleligini
        # olcmus oluruz. O yuzden IP yalnizca blogunu bildigimiz uygulamalara
        # veriliyor, otekilerde bos.
        s.dst_ip = IP.get(f.app, "") if gercek_ip else ""
        c = classify(s)
        d = c.traffic_class == f.traffic_class
        dogru += d
        per_sinif[f.traffic_class.value][1] += 1
        per_sinif[f.traffic_class.value][0] += d
        per_katman[c.basis][1] += 1
        per_katman[c.basis][0] += d
        if not d:
            karisiklik[(f.app, f.traffic_class.value,
                        c.traffic_class.value, c.basis)] += 1
    n = len(akislar)
    print(f"\n{'=' * 76}\n{ad}\n{'=' * 76}")
    print(f"  genel dogruluk: {dogru}/{n} = %{dogru / n * 100:.1f}")
    print(f"\n  {'sinif':<14}{'dogru':>8}{'toplam':>8}{'oran':>8}")
    for k in ("realtime", "interactive", "streaming", "bulk", "background"):
        if k in per_sinif:
            d, t = per_sinif[k]
            print(f"  {k:<14}{d:>8}{t:>8}{d / t * 100:>7.1f}%")
    print(f"\n  {'katman':<14}{'dogru':>8}{'kullanim':>10}{'isabet':>8}")
    for k in (BASIS_PROCESS, BASIS_IP, BASIS_PORT, BASIS_SHAPE, BASIS_DEFAULT):
        if k in per_katman:
            d, t = per_katman[k]
            print(f"  {k:<14}{d:>8}{t:>10}{d / t * 100:>7.1f}%")
    if karisiklik:
        print(f"\n  en sik karistirilanlar:")
        for (app, ger, tah, kat), c in karisiklik.most_common(6):
            print(f"    {app:<16} {ger:<12} -> {tah:<12} ({kat}) x{c}")
    return dogru / n


print("=" * 76)
print("Belirsiz portlar (katalogdan turetildi, elle yazilmadi)")
print("=" * 76)
for (proto, port), adlar in sorted(PORT_AMBIGUOUS.items()):
    siniflar = sorted({APPS[a].traffic_class.value for a in adlar})
    print(f"  {proto}/{port}: {len(adlar)} uygulama, {len(siniflar)} sinif")
    print(f"    {', '.join(adlar)}")
    print(f"    siniflar: {', '.join(siniflar)}")

akislar = akislari_topla()
print(f"\n{len(akislar)} akis uretildi "
      f"({len({f.app for f in akislar})} farkli uygulama)")

a = olc(akislar, "A. YALNIZ AG GORUNUMU (port + sekil)")
b = olc(akislar, "B. + HEDEF IP (gercekci blok atamasi)", gercek_ip=True)
c = olc(akislar, "C. + SUREC ADI (Sysmon Event 3)", surec=True, gercek_ip=True)

print(f"\n{'=' * 76}")
print(f"{'kosul':<44}{'dogruluk':>10}")
print("-" * 76)
print(f"{'A. yalniz ag gorunumu (port + sekil)':<44}{a * 100:>9.1f}%")
print(f"{'B. + hedef IP bloklari':<44}{b * 100:>9.1f}%")
print(f"{'C. + surec adi (Sysmon)':<44}{c * 100:>9.1f}%")
print("=" * 76)
