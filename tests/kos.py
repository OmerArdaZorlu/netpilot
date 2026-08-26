"""Test koşucusu — `python tests/kos.py`.

Testler bu depoya 2026-08-26'da alındı. O güne kadar geçici bir klasörde
(`%LOCALAPPDATA%\\Temp\\claude\\...`) duruyorlardı: "9 test paketi GECTI"
iddiasının dayanağı silinebilir bir temp klasörüydü. Artık sürüm kontrolünde.

**Her test ayrı bir süreçte koşuyor.** Sebebi teknik: bazıları modül düzeyinde
durum kuruyor (tohumlu rastgelelik, katalog kopyaları, olay döngüsü) ve aynı
yorumlayıcıda arka arkaya koşturmak birbirlerinin sonucunu etkiliyordu.
Ayrıca biri çökerse geri kalanı koşmaya devam ediyor.

**UTF-8 zorlanıyor.** Testler Türkçe yazıyor, Windows konsolu cp1252; bu
`UnicodeEncodeError` ile testleri **başarısız gösteriyordu**, oysa kodda hata
yoktu. Bir kez yanlış teşhise yol açtı, o yüzden burada zorunlu.

Kullanım:
    python tests/kos.py              # çevrimdışı testler (varsayılan)
    python tests/kos.py --hepsi      # servis isteyenler dahil
    python tests/kos.py --servis     # yalnız servis isteyenler
    python tests/kos.py -k flow      # adı eşleşenler
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
KOK = TESTS.parent

# Koşucunun **kendi** çıktısı da Türkçe ve ilk sürümünde tam olarak
# çocuklarını koruduğu hataya düştü: "test koşuluyor" satırındaki 'ş'
# cp1252'de kodlanamayıp koşucuyu tek satırda düşürdü. Çocuk süreçlere
# ortam değişkeni geçirmek yeterli değil; bu süreç kendi akışını da
# çevirmek zorunda.
for _akis in (sys.stdout, sys.stderr):
    try:
        _akis.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):   # yönlendirilmiş/sarılmış akış
        pass

# Yerel model isteyen testler: gerçek bir LLM sağlayıcısına (Foundry Local /
# Ollama) çıkarım yaptırıyorlar. Ölçüldü (2026-08-26): tek turlu olanlar
# 14-33 sn, çok turlu olanlar 75 sn'yi aşıyor. Varsayılan koşuya girmiyorlar —
# model yokken hepsi kırmızı yanar ve gerçek gerilemeleri gizlerdi.
#
# Buradaki ayrım "ağ kullanıyor mu" değil **"modele soruyor mu"**. `t_api`,
# `t_bosluk`, `t_snap` de ağ/kontrolcü ayağa kaldırıyor ve 30-60 sn sürüyor,
# ama model olmadan da sonuç veriyorlar; onlar varsayılanda kalıyor.
SERVIS_GEREKTIREN = {
    "t_ai_diag", "t_ai_flow", "t_ai_flow2", "t_ai_ham", "t_ai_hibrit",
    "t_ai_policy", "t_ai_politika", "t_ai_random", "t_ai_tekrar", "t_ai_yeni",
    "t_hedef", "t_json_hata", "t_kesilme", "t_rows", "t_zincir",
}

# Tek koşuda beklenenden uzun sürenler için tavan (saniye). Çok turlu AI
# testleri 75 sn'de kesiliyordu, o yüzden geniş.
ZAMAN_ASIMI = 420


def testleri_bul(desen: str | None) -> list[Path]:
    hepsi = sorted(TESTS.glob("t_*.py"))
    if desen:
        hepsi = [t for t in hepsi if desen.lower() in t.stem.lower()]
    return hepsi


def kos(test: Path) -> tuple[int, float, str]:
    ortam = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t0 = time.time()
    try:
        p = subprocess.run(
            [sys.executable, str(test)], cwd=str(KOK), env=ortam,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=ZAMAN_ASIMI)
        kod = p.returncode
        cikti = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        kod = -9
        cikti = f"ZAMAN ASIMI ({ZAMAN_ASIMI} sn)\n" + (e.stdout or "")
    return kod, time.time() - t0, cikti


def main() -> int:
    ap = argparse.ArgumentParser(description="netpilot test koşucusu")
    ap.add_argument("--hepsi", action="store_true",
                    help="servis isteyen testleri de koştur")
    ap.add_argument("--servis", action="store_true",
                    help="yalnız servis isteyen testleri koştur")
    ap.add_argument("-k", dest="desen", default=None,
                    help="adı bu metni içeren testleri koştur")
    ap.add_argument("-v", dest="ayrintili", action="store_true",
                    help="geçen testlerin çıktısını da yaz")
    args = ap.parse_args()

    testler = testleri_bul(args.desen)
    if args.servis:
        testler = [t for t in testler if t.stem in SERVIS_GEREKTIREN]
    elif not args.hepsi:
        testler = [t for t in testler if t.stem not in SERVIS_GEREKTIREN]

    if not testler:
        print("Eşleşen test yok.")
        return 1

    print(f"{len(testler)} test koşuluyor  (kök: {KOK})\n")
    kalanlar: list[tuple[str, str]] = []
    gecen = 0
    t0 = time.time()

    for test in testler:
        kod, sure, cikti = kos(test)
        isaret = "GECTI" if kod == 0 else "KALDI"
        print(f"  {isaret:<5} {test.stem:<22} {sure:6.1f}s")
        sys.stdout.flush()
        if kod == 0:
            gecen += 1
            if args.ayrintili:
                print(cikti)
        else:
            kalanlar.append((test.stem, cikti))

    print(f"\n{'=' * 60}")
    print(f"{gecen}/{len(testler)} gecti   ({time.time() - t0:.0f} sn)")

    for ad, cikti in kalanlar:
        print(f"\n{'-' * 60}\n{ad} ciktisi (son 25 satir):\n")
        print("\n".join(cikti.strip().splitlines()[-25:]))

    return 0 if not kalanlar else 1


if __name__ == "__main__":
    sys.exit(main())
