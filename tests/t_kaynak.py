"""AKIS KAYNAGI SEAM'I: `mode` gercekten kaynagi seciyor mu?

Bu testin varlik sebebi olculdu: `cfg.mode` uzun sure bir ayar gibi gorunup
hicbir davranisi degistirmiyordu (yalniz ekrana basiliyordu), yani
`NTC_MODE=live` yazan kurulum sessizce simulasyon uretiyordu. Seam kurulunca
sorunun tekrar sessizce acilmamasi icin dort sey burada kilitleniyor:

  1. Tanidik modlar dogru kaynagi kuruyor.
  2. Tanimadigi mod SESSIZCE varsayilana dusmuyor, hata veriyor.
  3. Senaryo yetenegi olmayan kaynak API'de gerekcelendiriliyor
     (basilan dugme "tetikledim" yalanini soylemiyor).
  4. Toplayici somut simulatore degil arayuze bagli: sahte bir kaynak
     takildiginda sistem onun akislarini isliyor.

4. madde onemli cunku Faz 2'de canli kaynak tam olarak boyle takilacak.
"""
import asyncio, json, sys, threading, time, urllib.request
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import logging; logging.basicConfig(level=logging.ERROR)   # toplayici istisnalari gorunsun

from ntc.core.config import load_config
from ntc.core.models import Direction, Flow, TrafficClass, new_id, now
from ntc.controller import Controller
from ntc.traffic.source import FlowSource, UnsupportedMode, build_source

ok = True


def kontrol(baslik, kosul, not_=""):
    global ok
    if not kosul:
        ok = False
    print(f"  {'OK  ' if kosul else 'FAIL'} {baslik}" + (f"  ({not_})" if not_ else ""))


def baslik(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


# --------------------------------------------------------------- sahte kaynak

class SahteKaynak:
    """Simulator OLMAYAN bir kaynak. Senaryo yetenegi yok."""

    name = "sahte"
    supports_scenarios = False

    def __init__(self):
        self.devices = {}
        self.tick_sayisi = 0

    def tick(self, dt=1.0):
        self.tick_sayisi += 1
        return [Flow(
            id=new_id("flw"), ts=now(), device_id="dev-sahte",
            src_ip="10.10.0.99", dst_ip="1.1.1.1", src_port=40000, dst_port=443,
            proto="tcp", app="https-web", traffic_class=TrafficClass.INTERACTIVE,
            direction=Direction.INBOUND, bytes_down=1_000_000, bytes_up=100_000,
            packets=900, duration=dt, rtt_ms=20.0, retransmits=0,
        )]

    async def start(self):
        return None

    async def aclose(self):
        return None


# --------------------------------------------------------------- 1. mod secimi

baslik("1. mode -> kaynak")
cfg = load_config()

for mod, beklenen in [("simulation", "simulation"), ("sim", "simulation"),
                      ("simulasyon", "simulation"), ("SIMULATION", "simulation")]:
    cfg.mode = mod
    try:
        s = build_source(cfg)
        kontrol(f"mode={mod!r} -> {s.name}", s.name == beklenen)
    except UnsupportedMode as e:
        kontrol(f"mode={mod!r} kaynak kurdu", False, str(e)[:50])

# Canli mod: kodu yazilana kadar HATA vermeli. Sessizce simulatore dusmek
# "gercek agi izliyorum" sanan bir operator demek.
for mod in ("live", "canli", "canlı"):
    cfg.mode = mod
    try:
        s = build_source(cfg)
        kontrol(f"mode={mod!r} sessizce {s.name} kurdu", False,
                "canli mod uygulanmadan kaynak donmemeli")
    except UnsupportedMode as e:
        kontrol(f"mode={mod!r} -> gerekceli hata", "Faz 2" in str(e))

# Yazim hatasi da sessiz kalmamali.
for mod in ("sacma", "", "simulaton"):
    cfg.mode = mod
    try:
        build_source(cfg)
        kontrol(f"mode={mod!r} hata vermedi", False)
    except UnsupportedMode as e:
        kontrol(f"mode={mod!r} -> hata + gecerli degerler",
                "simulation" in str(e) and "live" in str(e))

# ------------------------------------------------------------ 2. protokol uyumu

baslik("2. protokol uyumu")
cfg.mode = "simulation"
sim = build_source(cfg)
kontrol("simulator FlowSource", isinstance(sim, FlowSource))
kontrol("sahte kaynak FlowSource", isinstance(SahteKaynak(), FlowSource))
kontrol("simulator senaryo yetenegi var", sim.supports_scenarios is True)
kontrol("simulator cihazlari kurulu", len(sim.devices) > 0, f"{len(sim.devices)} cihaz")
kontrol("tick akis uretiyor", len(sim.tick(1.0)) > 0)

# ------------------------------------------------------- 3. toplayici arayuze bagli

baslik("3. toplayici sahte kaynagi isliyor mu")


async def toplayici_denemesi():
    cfg2 = load_config()
    cfg2.mode = "simulation"
    c = Controller(cfg2)
    sahte = SahteKaynak()
    c.source = sahte                     # canli kaynak Faz 2'de boyle takilacak
    await c.start()
    try:
        await asyncio.sleep(3.0)
        return sahte.tick_sayisi, c.metrics.link_stats(), c.status()
    finally:
        await c.stop()


tick_sayisi, stats, durum = asyncio.run(toplayici_denemesi())
kontrol("sahte kaynagin tick'i cagrildi", tick_sayisi > 0, f"{tick_sayisi} tur")
kontrol("akislari metrige girdi", stats.flow_count > 0, f"{stats.flow_count} akis")
kontrol("status kaynagi bildiriyor", durum.get("source") == "sahte", durum.get("source"))
kontrol("senaryo yetenegi kapali gorunuyor",
        durum.get("scenarios_supported") is False)
kontrol("senaryo listesi bos", durum.get("scenarios") == [])

# ------------------------------------------------------------------ 4. API uclari

baslik("4. senaryo uclari")
PORT = 8098


def cagir(yontem, yol, govde=None, port=PORT):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{yol}", method=yontem)
    if govde is not None:
        req.data = json.dumps(govde).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def sunucu_ile(kaynak=None, port=PORT):
    """Sunucuyu ayaga kaldirir; `kaynak` verilirse onu takar.

    Uclar kontrolcuyu kapanistan (closure) okuyor, o yuzden nesneyi
    degistirmiyoruz; `app.state.controller` ayni nesne oldugu icin onun
    `source` alanini degistirmek yetiyor.
    """
    import uvicorn
    from ntc.api.server import create_app
    cfg3 = load_config()
    cfg3.mode = "simulation"
    app = create_app(cfg3)
    if kaynak is not None:
        app.state.controller.source = kaynak
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(60):
        try:
            cagir("GET", "/api/status", port=port)
            break
        except Exception:
            time.sleep(0.5)
    return srv, th


srv, th = sunucu_ile()
try:
    st, d = cagir("GET", "/api/sim/scenarios")
    kontrol("GET /api/sim/scenarios 200", st == 200, f"HTTP {st}")
    kontrol("supported=true", d.get("supported") is True)
    kontrol("senaryo listesi dolu", len(d.get("available") or []) > 0,
            f"{len(d.get('available') or [])} senaryo")

    st, d = cagir("POST", "/api/sim/scenario", {"name": "congestion", "duration": 5})
    kontrol("POST /api/sim/scenario 200", st == 200, f"HTTP {st}")

    st, d = cagir("GET", "/api/sim/scenarios")
    kontrol("tetiklenen senaryo aktifte gorunuyor",
            any(s.get("name") == "congestion" for s in (d.get("active") or [])))

    st, d = cagir("DELETE", "/api/sim/scenarios")
    kontrol("DELETE /api/sim/scenarios 200", st == 200 and d.get("cleared", 0) >= 1,
            f"HTTP {st} cleared={d.get('cleared')}")

    st, d = cagir("GET", "/api/status")
    kontrol("status.mode ile status.source ayri alanlar",
            d.get("mode") == "simulation" and d.get("source") == "simulation")
finally:
    srv.should_exit = True
    th.join(timeout=20)

# ------------------------------------------- 5. yetenegi olmayan kaynakta uclar

baslik("5. senaryo yetenegi olmayan kaynak")
PORT2 = 8097
srv2, th2 = sunucu_ile(kaynak=SahteKaynak(), port=PORT2)
try:
    st, d = cagir("GET", "/api/sim/scenarios", port=PORT2)
    # Listeleme hata VERMEMELI: panel bu ucu her acilista cagiriyor, orada
    # 409 gurultu olurdu. Yetenek yoklugu `supported` alanindan okunuyor.
    kontrol("GET /api/sim/scenarios yine 200", st == 200, f"HTTP {st}")
    kontrol("supported=false", d.get("supported") is False)
    kontrol("available bos", d.get("available") == [])

    # Tetikleme ise SESSIZ GECMEMELI: dugmeye basan operator tetikledigini
    # sanmamali.
    st, d = cagir("POST", "/api/sim/scenario", {"name": "congestion"}, port=PORT2)
    kontrol("POST /api/sim/scenario -> 409", st == 409, f"HTTP {st}")
    kontrol("409 kaynak adini gerekce olarak veriyor",
            "sahte" in json.dumps(d, ensure_ascii=False), json.dumps(d)[:60])

    st, d = cagir("DELETE", "/api/sim/scenarios", port=PORT2)
    kontrol("DELETE /api/sim/scenarios -> 409", st == 409, f"HTTP {st}")

    st, d = cagir("GET", "/api/status", port=PORT2)
    kontrol("status: mode=simulation ama source=sahte",
            d.get("mode") == "simulation" and d.get("source") == "sahte",
            f"mode={d.get('mode')} source={d.get('source')}")
finally:
    srv2.should_exit = True
    th2.join(timeout=20)

print("\n" + "=" * 68)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
