"""Elle renk secmek yerine KISITLARI saglayan aday ara.

Kisitlar (hepsi ayni anda):
  1. good/critical dotanopi farki ARTMALI (asil hedef)
  2. Icinde bulundugu HICBIR kumede HICBIR CVD turunde en dusuk fark
     mevcut degerin altina inmemeli (gerileme yasak)
  3. Metin disi kontrast >= 3.0 (o temanin yuzeyi ve zemini)
"""
import itertools, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from t_cvd import ACIK, KOYU, KUMELER, TURLER, en_yakin, fark

def lum(h):
    h = h.lstrip("#")
    def sc(c):
        c /= 255.0
        return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4
    r,g,b = (int(h[i:i+2],16) for i in (0,2,4))
    return 0.2126*sc(r)+0.7152*sc(g)+0.0722*sc(b)

def kont(h, bg):
    a,b = lum(h), lum(bg)
    if a < b: a,b = b,a
    return (a+0.05)/(b+0.05)

def taban(palet):
    return {(ad,tur): en_yakin(palet, k, tur)[0]
            for ad,k in KUMELER.items() for tur in TURLER}

import colorsys

# TON KISITI. Arama sayiyi optimize ederken anlami bozuyordu: en iyi aday
# `#600055` cikti — mor bir "kritik" rengi. Kirmizi tehlike demek; metrik
# iyilesse de tasarim kotulesir. Renk kimligi korunmali.
TON = {"critical": (345, 15), "good": (95, 155)}   # HSV derece araligi

def ton_uygun(anahtar, h):
    x = h.lstrip("#")
    r, g, b = (int(x[i:i+2], 16)/255 for i in (0, 2, 4))
    hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
    if ss < 0.35 or vv < 0.15:      # gri veya cok koyu: kimlik tasimaz
        return False
    d = hh * 360
    lo, hi = TON[anahtar]
    return (d >= lo or d <= hi) if lo > hi else (lo <= d <= hi)


def dene(palet, anahtar, deger, zeminler, taban_degerler):
    if not ton_uygun(anahtar, deger):
        return None
    yeni = dict(palet); yeni[anahtar] = deger
    for bg in zeminler:
        if kont(deger, bg) < 3.0:
            return None
    for ad,k in KUMELER.items():
        if anahtar not in k: continue
        for tur in TURLER:
            if en_yakin(yeni, k, tur)[0] < taban_degerler[(ad,tur)] - 0.05:
                return None
    return fark(yeni["good"], yeni["critical"], "dotanopi")

def izgara(r0,r1,g0,g1,b0,b1,adim=17):
    for r in range(r0,r1+1,adim):
        for g in range(g0,g1+1,adim):
            for b in range(b0,b1+1,adim):
                yield f"#{r:02x}{g:02x}{b:02x}"

for tema, palet, zeminler in (("ACIK", ACIK, ("#fcfcfb","#f7f7f5")),
                              ("KOYU", KOYU, ("#1a1a19","#0d0d0d"))):
    tb = taban(palet)
    simdi = fark(palet["good"], palet["critical"], "dotanopi")
    print("=" * 68)
    print(f"{tema} tema — su anki good/critical dotanopi farki: {simdi:.1f}")
    print("=" * 68)
    for anahtar, kutu in (("critical", (0x60,0xff,0x00,0x60,0x00,0x60)),
                          ("good", (0x00,0x60,0x60,0xff,0x00,0x60))):
        en, iyi = simdi, None
        for h in izgara(*kutu):
            d = dene(palet, anahtar, h, zeminler, tb)
            if d and d > en:
                en, iyi = d, h
        if iyi:
            print(f"  {anahtar:<9}{palet[anahtar]} -> {iyi}   dotanopi {simdi:.1f} -> {en:.1f}"
                  f"   kontrast {kont(iyi,zeminler[0]):.2f}/{kont(iyi,zeminler[1]):.2f}")
        else:
            print(f"  {anahtar:<9}kisitlari saglayan aday YOK")
    print()
