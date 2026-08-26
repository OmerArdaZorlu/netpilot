"""RENK KORLUGU: palet degisikliklerinden SONRA ne durumda?

Sorulan iki ayri soru var ve karistirilmamali:

  1. BENIM DEGISIKLIKLERIM BOZDU MU?  (gerileme sorusu)
     Kontrast icin `--muted` ve dugme rengi degisti. Eski palet ile yeni
     paletin ayni olcute gore karsilastirilmasi.

  2. PALET MUTLAK OLARAK IYI MI?  (mevcut durum sorusu)
     Bunun cevabi kendi uydurdugum bir esikle verilemez. Olcut, CVD icin
     OZELLIKLE TASARLANMIS bir referans palet: Okabe-Ito. "Bizimki
     referansin ne kadar altinda" sorusu anlamli; "10.2 iyi mi kotu mu"
     sorusu degil.

YONTEM — Machado ve ark. (2009) siddet 1.0 matrisleri. Dogrusal RGB
uzerinde dogrudan calisiyorlar; LMS gidis-donusune gerek yok ve yaygin
olarak yayimlanmis degerler. Ilk denememde Vienot matrislerini yanlis
uzaya uygulamistim ve dogrulama yakaladi: protanopide kirmizi/yesil
35.4 cikiyordu, oysa o iki rengin YAKINSAMASI gerekir.

Fark olcusu CIEDE2000.
"""
import itertools
import math

# --------------------------------------------------------------- renk uzayi


def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_lin(h):
    h = h.lstrip("#")
    return [_lin(int(h[i:i + 2], 16)) for i in (0, 2, 4)]


def mat(m, v):
    return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]


# Machado ve ark. 2009, siddet 1.0 (tam dikromat). Dogrusal RGB uzerinde.
CVD = {
    "protanopi": [[0.152286, 1.052583, -0.204868],
                  [0.114503, 0.786281, 0.099216],
                  [-0.003882, -0.048116, 1.051998]],
    "dotanopi": [[0.367322, 0.860646, -0.227968],
                 [0.280085, 0.672501, 0.047413],
                 [-0.011820, 0.042940, 0.968881]],
    "tritanopi": [[1.255528, -0.076749, -0.178779],
                  [-0.078411, 0.930809, 0.147602],
                  [0.004733, 0.691367, 0.303900]],
}


def simule(h, tur):
    lin = hex_lin(h)
    return lin if tur == "normal" else mat(CVD[tur], lin)


def lab(lin):
    M = [[0.4124564, 0.3575761, 0.1804375],
         [0.2126729, 0.7151522, 0.0721750],
         [0.0193339, 0.1191920, 0.9503041]]
    x, y, z = mat(M, [max(0.0, min(1.0, c)) for c in lin])
    xn, yn, zn = 0.95047, 1.0, 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(l1, l2):
    L1, a1, b1 = l1
    L2, a2, b2 = l2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) if Cb > 0 else 0.5
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2)
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp > 0 else 0.0
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * 30 * math.exp(-(((hbp - 275) / 25) ** 2)))) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def fark(h1, h2, tur):
    return ciede2000(lab(simule(h1, tur)), lab(simule(h2, tur)))


def en_yakin(palet, anahtarlar, tur):
    en, cift = 999.0, None
    for a, b in itertools.combinations(anahtarlar, 2):
        d = fark(palet[a], palet[b], tur)
        if d < en:
            en, cift = d, (a, b)
    return en, cift


# --------------------------------------------------------------- paletler

ACIK = {
    "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100",
    "s5": "#e87ba4", "good": "#0ca30c", "warning": "#fab219",
    "serious": "#ec835a", "critical": "#601122", "muted": "#726f69",
}
KOYU = dict(ACIK, **{
    "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#c98500",
    "s5": "#d55181", "muted": "#898781",
    "good": "#5bf968", "critical": "#d03b3b",
})
# Degisiklikten ONCEKI hali — gerileme sorusu icin
ACIK_ESKI = dict(ACIK, muted="#898781", critical="#d03b3b")
KOYU_ESKI = dict(KOYU, muted="#898781", good="#0ca30c")

# CVD icin OZELLIKLE tasarlanmis referans (Okabe & Ito 2008)
OKABE = {"a": "#E69F00", "b": "#56B4E9", "c": "#009E73",
         "d": "#F0E442", "e": "#0072B2"}

KUMELER = {
    "trafik siniflari": ["s1", "s2", "s3", "s4", "s5"],
    "uyari siddeti": ["muted", "good", "warning", "serious", "critical"],
    "siniflandirma katmanlari": ["good", "s1", "s3", "warning", "muted"],
    "doluluk": ["good", "warning", "critical"],
}
TURLER = ("normal", "protanopi", "dotanopi", "tritanopi")
