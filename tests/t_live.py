"""CANLI KAYNAK: hacim ⋈ kimlik birlesimi dogru mu?

Yakalama yetki ve gercek trafik istiyor; bu test **paketleri kendisi kurup**
besliyor, yani yonetici olmadan ve agdan bagimsiz kosuyor. Olculen sey
yakalama surucusu degil, bizim mantigimiz: yon karari, anahtarlama,
sahiplik cozumu, Flow uretimi.

Gercek yakalama ayri dogrulandi (bkz. PROJECT_STATUS 4w) ve burada
tekrarlanamaz: agda o anda ne aktigi tekrarlanabilir bir olcum degil.
"""
import asyncio, socket, sys, time
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import logging; logging.basicConfig(level=logging.CRITICAL)

from ntc.core.config import LiveConfig
from ntc.core.models import Direction, TrafficClass
from ntc.traffic.capture import PacketVolumeFeed, VolumeRecord
from ntc.traffic.live import ConnectionOwners, LiveSource

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether

ok = True
YEREL = "10.0.0.5"
UZAK = "93.184.216.34"
LAN = "10.0.0.9"


def kontrol(baslik, kosul, not_=""):
    global ok
    if not kosul:
        ok = False
    print(f"  {'OK  ' if kosul else 'FAIL'} {baslik}" + (f"  ({not_})" if not_ else ""))


def baslik(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


def paket(src, dst, sport=1234, dport=443, yuk=100, udp=False):
    kat = UDP(sport=sport, dport=dport) if udp else TCP(sport=sport, dport=dport)
    return Ether() / IP(src=src, dst=dst) / kat / (b"x" * yuk)


# ------------------------------------------------------------------ 1. hacim

baslik("1. hacim beslemesi: yon ve anahtarlama")
feed = PacketVolumeFeed(iface="yok", yerel_adresler={YEREL})

feed.feed(paket(YEREL, UZAK, 5000, 443, yuk=100))    # yukari
feed.feed(paket(UZAK, YEREL, 443, 5000, yuk=400))    # asagi
feed.feed(paket(YEREL, YEREL, 1, 2))                 # makine ici -> sayilmaz
feed.feed(paket("8.8.8.8", "1.1.1.1", 1, 2))         # iki uc yabanci

kayitlar = feed.drain()
kontrol("iki yon TEK kovada birlesti", len(kayitlar) == 1, f"{len(kayitlar)} kova")
if kayitlar:
    k = kayitlar[0]
    kontrol("asagi bayt dogru", k.bytes_down > k.bytes_up,
            f"down={k.bytes_down} up={k.bytes_up}")
    kontrol("yerel uc dogru secildi", k.local_ip == YEREL and k.local_port == 5000)
    kontrol("uzak uc dogru secildi", k.remote_ip == UZAK and k.remote_port == 443)
    kontrol("paket sayisi 2", k.packets == 2, str(k.packets))
kontrol("makine ici paket sayilmadi (yabanci degil)", feed.yabanci_paket == 1,
        f"yabanci={feed.yabanci_paket}")
kontrol("drain sayaci sifirladi", feed.drain() == [])

# Ayni baglantinin iki yonu ayri kovaya dusseydi cihaz basina hiz iki katina
# cikardi; yukaridaki "TEK kova" kontrolu tam olarak bunu koruyor.

baslik("1b. yakalama sessizligi fark ediliyor mu")
sessiz = PacketVolumeFeed(iface="yok", yerel_adresler={YEREL})
kontrol("baslamadan sessiz degil", sessiz.sessiz is False)
sessiz._sniffer = type("S", (), {"running": True})()
sessiz._basladi_ts = time.time() - (PacketVolumeFeed.SESSIZLIK_ESIGI + 1)
kontrol("ayakta + 0 paket + esik gecti -> sessiz", sessiz.sessiz is True)
sessiz.feed(paket(YEREL, UZAK))
kontrol("paket gelince sessizlik biter", sessiz.sessiz is False)

# ------------------------------------------------------------------ 2. kimlik

baslik("2. kimlik beslemesi: uc anahtar seviyesi")


class SahteBaglanti:
    def __init__(self, lip, lport, rip=None, rport=0, pid=1, udp=False):
        self.laddr = type("A", (), {"ip": lip, "port": lport})()
        self.raddr = type("A", (), {"ip": rip, "port": rport})() if rip else None
        self.pid = pid
        self.type = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM


def sahte_psutil(baglantilar, adlar):
    import types
    mod = types.SimpleNamespace()
    mod.net_connections = lambda kind="inet": baglantilar
    mod.Process = lambda pid: types.SimpleNamespace(name=lambda: adlar.get(pid, ""))
    return mod


def owners_ile(baglantilar, adlar, ttl=120.0):
    import sys as _s
    gercek = _s.modules.get("psutil")
    _s.modules["psutil"] = sahte_psutil(baglantilar, adlar)
    try:
        o = ConnectionOwners(ttl=ttl)
        o.refresh(ts=1000.0)
        return o
    finally:
        # Her durumda geri al. Ilk cagrida psutil henuz import edilmemis
        # olabiliyor (`gercek is None`) ve o zaman sahte modul sys.modules'ta
        # KALIYORDU: sonraki gercek psutil kullanicisi (local_addresses)
        # AttributeError ile dusuyordu.
        if gercek is None:
            _s.modules.pop("psutil", None)
        else:
            _s.modules["psutil"] = gercek


o = owners_ile(
    [SahteBaglanti(YEREL, 5000, UZAK, 443, pid=10),        # tam 5'li
     SahteBaglanti(YEREL, 6000, pid=20, udp=True),         # UDP: uzak uc YOK
     SahteBaglanti("0.0.0.0", 7000, pid=30, udp=True)],    # joker yerel IP
    {10: "opera.exe", 20: "chrome.exe", 30: "svchost.exe"})

kontrol("5'li eslesmesi", o.lookup("tcp", YEREL, 5000, UZAK, 443)[1] == "opera.exe")
kontrol("UDP yerel-uc yedegi", o.lookup("udp", YEREL, 6000, UZAK, 53)[1] == "chrome.exe")
kontrol("joker adres icin port yedegi",
        o.lookup("udp", YEREL, 7000, UZAK, 123)[1] == "svchost.exe")
kontrol("bilinmeyen port cozulmuyor", o.lookup("tcp", YEREL, 9999, UZAK, 80)[1] == "")
kontrol("hit_rate olculuyor", o.hit_rate is not None and 0 < o.hit_rate < 1,
        f"{o.hit_rate}")

# TTL: eski kayit dusuyor mu
o2 = owners_ile([SahteBaglanti(YEREL, 5000, UZAK, 443, pid=10)], {10: "a.exe"}, ttl=10.0)
o2._buda(1000.0 + 11.0)
kontrol("TTL gecince kayit dusuyor", o2.lookup("tcp", YEREL, 5000, UZAK, 443)[1] == "")

# ------------------------------------------------------------------ 3. birlesim

baslik("3. LiveSource: hacim + kimlik -> Flow")


class SahteHacim:
    """PacketVolumeFeed yerine gecen sahte — testin agdan bagimsiz kalmasi icin."""

    iface = "test"
    running = True
    sessiz = False
    toplam_paket = 4
    yabanci_paket = 0
    secilen_arayuz_tahmini = False
    SESSIZLIK_ESIGI = 15.0

    def __init__(self, kayitlar):
        self._kayitlar = kayitlar

    def start(self): pass
    def aclose(self): pass

    def drain(self):
        k, self._kayitlar = self._kayitlar, []
        return k


kaynak = LiveSource(LiveConfig())
kaynak._yerel = {YEREL}
kaynak.volume = SahteHacim([
    VolumeRecord("tcp", YEREL, 5000, UZAK, 443, bytes_down=4000, bytes_up=500, packets=9),
    VolumeRecord("tcp", YEREL, 5001, LAN, 445, bytes_down=100, bytes_up=9000, packets=7),
    VolumeRecord("udp", YEREL, 6000, UZAK, 53, bytes_down=0, bytes_up=0, packets=0),
])
kaynak.owners = owners_ile(
    [SahteBaglanti(YEREL, 5000, UZAK, 443, pid=10)], {10: "opera.exe"})
kaynak._son_yoklama = time.time() + 3600     # tick icinde yeniden yoklamasin

akislar = kaynak.tick(2.0)
kontrol("bos kayit atlandi", len(akislar) == 2, f"{len(akislar)} akis")
wan = [f for f in akislar if f.direction is not Direction.LATERAL]
lan = [f for f in akislar if f.direction is Direction.LATERAL]
kontrol("ozel hedef LATERAL sayildi", len(lan) == 1)
kontrol("dis hedef WAN sayildi", len(wan) == 1)
if wan:
    f = wan[0]
    kontrol("indirme agirlikli akis INBOUND", f.direction is Direction.INBOUND)
    kontrol("surec adi Flow'a gecti", f.process == "opera.exe", f.process)
    kontrol("bayt/paket tasindi", f.bytes_down == 4000 and f.packets == 9)
    kontrol("sure tick'ten geldi", f.duration == 2.0)
    kontrol("rtt/yeniden gonderim uydurulmadi", f.rtt_ms == 0.0 and f.retransmits == 0)
    kontrol("app bos birakildi (siniflandirici dolduracak)", f.app == "")
kontrol("cihaz gozlemden dogdu", len(kaynak.devices) == 1, f"{len(kaynak.devices)}")
kontrol("kaynak sinif etiketi uretmedigini bildiriyor",
        kaynak.labels_traffic_class is False)
kontrol("senaryo yetenegi yok", kaynak.supports_scenarios is False)

# ---------------------------------------------- 4. siniflandirici canliya aliniyor

baslik("4. etiketlemeyen kaynakta siniflandirici modu")
from ntc.core.config import load_config
from ntc.controller import Controller


class EtiketsizKaynak:
    name = "etiketsiz"
    supports_scenarios = False
    labels_traffic_class = False

    def __init__(self): self.devices = {}
    def tick(self, dt=1.0): return []
    async def start(self): pass
    async def aclose(self): pass


cfg = load_config()
cfg.mode = "simulation"
cfg.classify.mode = "golge"
c = Controller(cfg)
kontrol("etiketleyen kaynakta golge modda kaliyor", c.classifier.mode == "golge")

# Kural `Controller.__init__` icinde isliyor, o yuzden kaynagi build_source
# seviyesinde degistiriyoruz.
import ntc.controller as _ctrl
_eski = _ctrl.build_source
_ctrl.build_source = lambda cfg: EtiketsizKaynak()
try:
    c3cfg = load_config(); c3cfg.mode = "simulation"; c3cfg.classify.mode = "golge"
    c3 = Controller(c3cfg)
    kontrol("etiketsiz kaynakta siniflandirici canliya aliniyor",
            c3.classifier.mode == "canli", c3.classifier.mode)
    kontrol("status kaynagi bildiriyor", c3.status()["source"] == "etiketsiz")
finally:
    _ctrl.build_source = _eski

# Canli modda sinif GERCEKTEN yaziliyor mu: akis kasitli yanlis sinifla
# geliyor, siniflandiricinin karari onun uzerine yazmali.
from ntc.traffic.classify import ClassifyAudit, classify, signals_from_flow


class Ayar:
    enabled = True
    mode = "canli"
    use_process = True
    sample_size = 10


import dataclasses
# Fikstürün hacmi bilerek buyutuluyor: siniflandiricinin "hacim cok kucuk ->
# background" kurali dogru calisiyor ve kucuk bir akisla sinandiginda
# "ustune yazdi mi" sorusu olculemez hale geliyordu (beklenen de background
# cikiyor, fark gorunmuyor).
hedef = dataclasses.replace(akislar[0], bytes_down=6_000_000, bytes_up=200_000)
hedef.traffic_class = TrafficClass.BACKGROUND          # kasitli yanlis
beklenen = classify(signals_from_flow(hedef, process=hedef.process)).traffic_class
ClassifyAudit(Ayar()).process([hedef])
kontrol("canli modda sinif ustune yaziliyor", hedef.traffic_class == beklenen,
        f"{hedef.traffic_class.value} (beklenen {beklenen.value})")
kontrol("yazilan sinif kasitli yanlisla ayni degil",
        beklenen is not TrafficClass.BACKGROUND, beklenen.value)

print("\n" + "=" * 68)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
