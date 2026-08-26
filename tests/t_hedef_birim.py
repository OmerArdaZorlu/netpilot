"""Hedef cozumleme: neyi kurtarmali, neyi DUSURMELI.

Ikinci liste birincisi kadar onemli. Cozumlemeyi gevsetmek, modelin
soylemedigi bir seyi soylemis gibi gostermeye acilir.
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.ai.analyst import AIAnalyst

GECERLI = {"ws-dev-02", "ws-finance-01", "cam-entrance", "cam-parking",
           "srv-app-01", "srv-backup-01", "guest-wifi-a", "phone-omer",
           "lt-sales-07", "tv-lobby",
           "realtime", "interactive", "streaming", "bulk", "background", "link"}

KURTARILMALI = [
    # (girdi, beklenen sonuc)
    ("ws-dev-02",                        ["ws-dev-02"]),
    ("Link",                             ["link"]),
    ("LINK",                             ["link"]),
    ("Cam-entrance ve cam-parking",      ["cam-entrance", "cam-parking"]),
    ("ws-dev-02 ve ws-finance-01",       ["ws-dev-02", "ws-finance-01"]),
    ("ws-dev-02, ws-finance-01",         ["ws-dev-02", "ws-finance-01"]),
    ("cam-entrance & cam-parking",       ["cam-entrance", "cam-parking"]),
    ("Interactive cihazlar",             ["interactive"]),
    ("Link cihazları",                   ["link"]),
    ("Link cihazı",                      ["link"]),
    ("Guest Wi-Fi a",                    ["guest-wifi-a"]),
    ("Cam cihazları",                    ["cam-entrance", "cam-parking"]),
    ("srv",                              ["srv-app-01", "srv-backup-01"]),
    ("Cam cihazları ve ws-dev-02",       ["cam-entrance", "cam-parking", "ws-dev-02"]),
    ("İnteraktif Trafik",                ["interactive"]),
    ("Interactive ve streaming sınıfları", ["interactive", "streaming"]),
    ("Yayın",                            ["streaming"]),
    ("Gerçek zamanlı",                   ["realtime"]),
    ("Arka plan trafiği",                ["background"]),
    ("Hat",                              ["link"]),
    ("Link cihazı, özellikle ws-dev-02 ve ws-finance-01",
                                         ["link", "ws-dev-02", "ws-finance-01"]),
]

DUSMELI = [
    "Güvenilirlik",            # soyut kavram, hedef degil
    "lan_mbps",                # metrik adi
    "trust=0.95 (srv-backup-01)",   # kanit dizesi
    "",                        # bos
    "tüm cihazlar",            # belirsiz: hangi cihazlar?
    "yüksek kullanımlı cihaz", # belirsiz
    "ws-dev-99",               # var olmayan cihaz
    "ws-dev-02 ve ws-dev-99",  # biri var biri yok -> tamami dusmeli
    "ab",                      # cok kisa, grup genislemesi tetiklememeli
    "Lateral flows",           # metrik adi
    "özellikle",               # yalniz baglac, hedef yok
    "özellikle ws-dev-99",     # baglac + var olmayan cihaz
]

ok = True
print(f"{'girdi':<36}{'sonuc':<40}{'':>4}")
print("-" * 82)
for girdi, beklenen in KURTARILMALI:
    got = AIAnalyst._resolve_targets(girdi, GECERLI)
    d = got == beklenen
    ok &= d
    print(f"{'OK  ' if d else 'FAIL'} {girdi:<32}{str(got):<40}"
          + ("" if d else f"  beklenen {beklenen}"))
print()
print("DUSMESI GEREKENLER")
print("-" * 82)
for girdi in DUSMELI:
    got = AIAnalyst._resolve_targets(girdi, GECERLI)
    d = got == []
    ok &= d
    print(f"{'OK  ' if d else 'FAIL'} {girdi!r:<34}"
          + ("dustu" if d else f"KURTARILDI (yanlis): {got}"))
print()
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
