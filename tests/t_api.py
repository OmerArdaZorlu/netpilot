"""Canli kosu: butun API uclari cevap veriyor mu, sekli dogru mu?

Panel bu uclardan besleniyor. Gorsel denetim yerine gecmez ama "panel
cagirdiginda 500 aliyor mu" sorusunu kapatir.
"""
import json, sys, threading, time, urllib.request
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

UCLAR = [
    ("GET", "/api/status", ["flow"]),
    ("GET", "/api/flow/plan", None),
    ("GET", "/api/classify", ["agreement", "by_basis"]),
    ("GET", "/api/flow/topology", None),
    ("GET", "/api/flow/policy", None),
    ("GET", "/api/flow/demand", None),
    ("GET", "/api/flow/ai", None),
    ("GET", "/api/enforce/state", None),
    ("GET", "/api/enforce/policies", None),
    ("GET", "/api/enforce/preview", None),
    ("POST", "/api/flow/solve", None),
    ("POST", "/api/flow/policy/refresh", None),
    ("GET", "/", None),
]

def cagir(yontem, yol):
    req = urllib.request.Request(f"http://127.0.0.1:8099{yol}", method=yontem)
    if yontem == "POST":
        req.data = b"{}"
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.status, r.read()

def main():
    import uvicorn
    from ntc.api.server import create_app
    from ntc.core.config import load_config
    cfg = load_config()
    app = create_app(cfg)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8099,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True); th.start()
    for _ in range(60):
        try:
            cagir("GET", "/api/status"); break
        except Exception:
            time.sleep(1.0)
    else:
        print("SUNUCU AYAGA KALKMADI"); return 1

    time.sleep(20)   # ilk cozucu + AI turu gecsin
    kusur = 0
    print(f"{'uc':<32}{'durum':>7}{'boyut':>9}  not")
    print("-" * 72)
    for yontem, yol, alanlar in UCLAR:
        try:
            st, govde = cagir(yontem, yol)
            not_ = ""
            if alanlar and yol.startswith("/api"):
                d = json.loads(govde)
                eksik = [a for a in alanlar if a not in d]
                if eksik:
                    not_ = f"EKSIK ALAN: {eksik}"; kusur += 1
            if st != 200:
                not_ = f"HTTP {st}"; kusur += 1
            print(f"{yontem} {yol:<28}{st:>7}{len(govde):>9}  {not_}")
        except Exception as exc:
            kusur += 1
            print(f"{yontem} {yol:<28}{'HATA':>7}{'-':>9}  {exc}")
    # akis gercekten cozuldu mu
    st, g = cagir("GET", "/api/status")
    d = json.loads(g)
    f = d.get("flow") or {}
    print("-" * 72)
    print(f"akis ozeti: cozuldu={f.get('solved')} "
          f"talep {f.get('total_demand_mbps')} Mbps, "
          f"verilen {f.get('total_granted_mbps')} Mbps, "
          f"geri cekme {f.get('pullback_count')}, "
          f"darbogaz {len(f.get('bottlenecks') or [])}")
    st, g = cagir("GET", "/api/classify")
    cl = json.loads(g)
    ag = cl.get("agreement")
    print(f"siniflandirma: {cl.get('total')} akis, uyum "
          + (f"%{ag*100:.1f}" if ag is not None else "olculemez")
          + f", mod={cl.get('mode')}")
    for k, v in (cl.get("by_basis") or {}).items():
        print(f"   {k:<12} pay %{v['share']*100:>5.1f}  isabet "
              + (f"%{v['hit']*100:.1f}" if v['hit'] is not None else "-"))
    if ag is not None and ag < 0.90:
        print("KUSUR: siniflandirma uyumu %90'in altinda"); kusur += 1

    st, g = cagir("GET", "/api/flow/ai")
    ai = json.loads(g)
    print(f"AI akis   : gecerli={ai.get('valid')} pay=%"
          f"{(ai.get('share') or 0)*100:.0f} tahsis={len(ai.get('grants') or [])}")
    if not f.get("total_granted_mbps"):
        print("KUSUR: canli kosuda hic akis cozulmedi"); kusur += 1
    print("-" * 72)
    print("HEPSI GECTI" if kusur == 0 else f"KALDI — {kusur} kusur")
    return 1 if kusur else 0

sys.exit(main())
