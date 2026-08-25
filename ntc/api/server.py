"""HTTP + WebSocket arayüzü."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from ..controller import Controller
from ..core.bus import TOPIC_ACTION, TOPIC_AI_REPORT, TOPIC_ALERT, TOPIC_METRICS
from ..core.config import Config, load_config

log = logging.getLogger(__name__)
DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


class Hub:
    """Bağlı WebSocket istemcilerine olay dağıtır."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, kind: str, payload: Any) -> None:
        if not self.clients:
            return
        message = {"kind": kind, "payload": payload}
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(ws)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    controller = Controller(cfg)
    hub = Hub()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        _wire_bus(controller, hub)
        await controller.start()
        try:
            yield
        finally:
            await controller.stop()

    app = FastAPI(title="Network Traffic Controller", version="0.1.0",
                  lifespan=lifespan)
    app.state.controller = controller
    app.state.hub = hub

    # ------------------------------------------------------------------ arayüz

    @app.get("/", include_in_schema=False)
    async def dashboard():
        if not DASHBOARD.exists():
            return JSONResponse({"error": "dashboard bulunamadı"}, status_code=404)
        return FileResponse(DASHBOARD)

    # -------------------------------------------------------------------- okuma

    @app.get("/api/status")
    async def status():
        return controller.status()

    @app.get("/api/devices")
    async def devices():
        return controller.devices_view()

    @app.get("/api/metrics/live")
    async def metrics_live():
        stats = controller.metrics.link_stats()
        return {
            "link": stats.to_dict(),
            "class_shares": controller.metrics.class_shares(),
            "top_talkers": [
                {"device_id": d, "bps": b,
                 "hostname": _hostname(controller, d)}
                for d, b in controller.metrics.top_talkers()
            ],
        }

    @app.get("/api/metrics/history")
    async def metrics_history(seconds: float = Query(600, ge=10, le=86400)):
        return await controller.storage.samples_since(seconds)

    @app.get("/api/flows/notable")
    async def notable_flows(limit: int = Query(100, ge=1, le=1000),
                            device_id: str | None = None):
        return await controller.storage.recent_notable_flows(limit, device_id)

    @app.get("/api/classify")
    async def classify_report():
        """Sınıflandırıcının canlı denetimi.

        `agreement` yalnız gerçek etiketin bilindiği yerde (simülasyon)
        anlamlı; canlı yakalamada `None` döner çünkü karşılaştırılacak bir
        doğru yoktur. `by_basis` hangi katmanın ne kadar taşıdığını
        gösteriyor — bu olmadan "sınıflandırma çalışıyor" ölçülemez bir
        iddia olurdu.
        """
        return controller.classifier.report()

    @app.get("/api/flow/plan")
    async def flow_plan():
        """Son akış çözümü: kime ne kadar, hangi kenardan, kimden ne kadar geri."""
        if controller.flow_plan is None:
            return {"solved": False,
                    "note": "henüz çözüm yok — ilk döngü bekleniyor"}
        return controller.flow_plan.to_dict()

    @app.post("/api/flow/solve")
    async def flow_solve():
        """Beklemeden yeniden çöz. Panelden 'şimdi hesapla' için."""
        plan = await controller.run_flow_optimization()
        if plan is None:
            return {"solved": False, "note": "ölçülecek trafik yok"}
        return plan.to_dict()

    @app.get("/api/flow/history")
    async def flow_history(limit: int = Query(50, ge=1, le=500)):
        """Geçmiş akış kararları — "dün kimden ne kadar kısıldı" için."""
        return await controller.storage.recent_flow_plans(limit)

    @app.get("/api/flow/topology")
    async def flow_topology():
        return controller.topology.to_dict()

    @app.get("/api/flow/ai")
    async def flow_ai():
        """Modelin son akış önerisi ve akışın ne kadarının onun kararı olduğu.

        `share` alanı iddiayı ölçülebilir tutuyor: 0.22 ise geçen trafiğin
        %22'sini model belirledi, gerisini LP doldurdu. `repair_ratio` ise
        doğrulayıcının ne kadarını yeniden yazdığını söylüyor — yüksekse
        karar modelin değil doğrulayıcının demektir.
        """
        if controller.ai_flow is None:
            return {"enabled": controller.cfg.ai.flow_enabled,
                    "note": "henüz öneri yok"}
        return {"enabled": controller.cfg.ai.flow_enabled,
                "share": round(controller.ai_flow_share, 4),
                **controller.ai_flow.to_dict()}

    @app.get("/api/flow/demand")
    async def flow_demand():
        """Talep profilleri: hangi cihazın boş saatte ne kadar çektiği.

        Çözücüye giden "talep" sayısının nereden geldiğini gösteriyor.
        Doygun hatta ölçülen hız zaten tavandır; bu tablo o tavanın
        arkasındaki gerçek isteği taşıyor.
        """
        return controller.demand_estimator.to_dict()

    @app.get("/api/flow/policy")
    async def flow_policy():
        """Çözücünün şu anki **hedefi** — kim koydu, neden, neyi değiştirdi.

        `note` alanı modelin son turda ne yaptığını söylüyor:
        kabul / reddedildi / korundu. Reddedilen çıktının gerekçesi
        `issues` içinde duruyor — modelin saçmaladığını gizlemiyoruz.
        """
        return {
            **controller.flow_policy.to_dict(),
            "describe": controller.flow_policy.describe(),
            "note": controller.policy_note,
            "issues": controller.policy_issues,
            "enabled": controller.cfg.ai.policy_enabled,
        }

    @app.post("/api/flow/policy/refresh")
    async def flow_policy_refresh():
        """Beklemeden hedefi yeniden kur."""
        pol = await controller.refresh_policy()
        return {**pol.to_dict(), "note": controller.policy_note,
                "issues": controller.policy_issues}

    @app.get("/api/enforce/state")
    async def enforce_state():
        """İnfaz katmanının durumu: hangi sürücü, hangi mod, hangi kurallar."""
        if controller.enforcer is None:
            return {"enabled": False,
                    "note": "infaz kapalı (config: enforce.enabled)"}
        return {"enabled": True,
                "require_approval": controller.cfg.enforce.require_approval,
                **controller.enforcer.to_dict()}

    @app.get("/api/enforce/policies")
    async def enforce_policies():
        """Son plandan çıkan **istenen** politika kümesi.

        Uzlaştırıcının kurduğuyla aynı olmak zorunda değil: onaysız veya
        sürücünün desteklemediği kurallar burada görünür ama kurulmaz.
        `/api/enforce/state` içindeki `last.skipped` neden kurulmadığını
        gerekçesiyle söylüyor.
        """
        return controller.policies.to_dict()

    @app.get("/api/enforce/preview")
    async def enforce_preview():
        """Kuru çalıştırma: şu an onaylanmış olsa hangi komutlar çıkardı.

        Gerçek uzlaştırıcının durumunu bozmuyor — ayrı bir Enforcer örneği
        üzerinde hesaplanıyor. Aksi halde "önizleme" tuşuna basmak, kuralları
        kurulmuş sayıp bir sonraki gerçek turda "değişmedi" dedirtirdi.
        """
        if controller.enforcer is None:
            return {"enabled": False}
        from ..enforce import Enforcer
        gecici = Enforcer(dict(controller.enforcer.drivers), mode="golge")
        sonuc = gecici.reconcile(controller.policies)
        return sonuc.to_dict()

    @app.get("/api/alerts")
    async def alerts(limit: int = Query(50, ge=1, le=500), persisted: bool = False):
        if persisted:
            return await controller.storage.recent_alerts(limit)
        return [a.to_dict() for a in list(controller.alerts)[:limit]]

    @app.get("/api/actions")
    async def actions(limit: int = Query(50, ge=1, le=500)):
        return {
            "active": [a.to_dict() for a in controller.optimizer.active.values()],
            "recent": [a.to_dict() for a in list(controller.actions)[:limit]],
        }

    # ------------------------------------------------------------------ komutlar

    @app.post("/api/actions/{action_id}/apply")
    async def apply_action(action_id: str):
        action = controller.optimizer.apply(action_id)
        if action is None:
            raise HTTPException(404, "aksiyon bulunamadı veya artık aktif değil")
        await controller.storage.save_actions([action])
        return action.to_dict()

    @app.post("/api/actions/{action_id}/revert")
    async def revert_action(action_id: str):
        action = controller.optimizer.revert(action_id)
        if action is None:
            raise HTTPException(404, "aksiyon bulunamadı")
        await controller.storage.save_actions([action])
        return action.to_dict()

    # ------------------------------------------------------------------------ AI

    @app.get("/api/ai/health")
    async def ai_health():
        if controller.provider is None:
            raise HTTPException(503, "sağlayıcı hazır değil")
        return await controller.provider.health()

    @app.get("/api/ai/report")
    async def ai_report():
        if not controller.reports:
            return JSONResponse({"detail": "henüz analiz üretilmedi"}, status_code=204)
        return controller.reports[0].to_dict()

    @app.get("/api/ai/reports")
    async def ai_reports(limit: int = Query(20, ge=1, le=100)):
        return await controller.storage.recent_reports(limit)

    @app.post("/api/ai/analyze")
    async def ai_analyze():
        report = await controller.run_analysis()
        if report is None:
            raise HTTPException(503, "analist hazır değil")
        return report.to_dict()

    @app.post("/api/ai/ask")
    async def ai_ask(question: str = Body(..., embed=True)):
        if not question.strip():
            raise HTTPException(400, "soru boş olamaz")
        return await controller.ask(question.strip())

    @app.get("/api/ai/snapshot")
    async def ai_snapshot():
        """Modele giden ham bağlam — hata ayıklama ve şeffaflık için."""
        if controller.analyst is None:
            raise HTTPException(503, "analist hazır değil")
        return controller.analyst.build_snapshot(
            controller.metrics, controller.simulator.devices, controller.optimizer)

    # ------------------------------------------------------------- simülasyon

    @app.post("/api/sim/scenario")
    async def trigger_scenario(payload: dict = Body(...)):
        name = payload.get("name")
        if not name:
            raise HTTPException(400, "name zorunlu")
        try:
            scenario = controller.simulator.trigger(
                name,
                device_id=payload.get("device"),
                duration=float(payload.get("duration", 60)),
                **(payload.get("params") or {}),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return scenario.to_dict()

    @app.get("/api/sim/scenarios")
    async def list_scenarios():
        from ..traffic.simulator import SCENARIOS
        return {
            "available": list(SCENARIOS),
            "active": [s.to_dict() for s in controller.simulator.scenarios],
        }

    @app.delete("/api/sim/scenarios")
    async def clear_scenarios():
        return {"cleared": controller.simulator.clear_scenarios()}

    # ------------------------------------------------------------------- canlı

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await hub.join(ws)
        try:
            await ws.send_json({"kind": "status", "payload": controller.status()})
            while True:
                # İstemci ping'leri dışında bir şey beklemiyoruz.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            # Sessizce yutmak, panelin neden veri almadığını görünmez
            # kılıyordu. Bağlantı yine kapanıyor ama sebebi log'a düşüyor.
            log.exception("WebSocket oturumu hatayla kapandı")
        finally:
            hub.leave(ws)

    return app


def _hostname(controller: Controller, device_id: str) -> str:
    device = controller.simulator.devices.get(device_id)
    return device.hostname if device else device_id


def _wire_bus(controller: Controller, hub: Hub) -> None:
    async def on_metrics(_topic: str, stats) -> None:
        await hub.broadcast("metrics", stats.to_dict())

    async def on_alert(_topic: str, alert) -> None:
        await hub.broadcast("alert", alert.to_dict())

    async def on_action(_topic: str, action) -> None:
        await hub.broadcast("action", action.to_dict())

    async def on_report(_topic: str, report) -> None:
        await hub.broadcast("ai_report", report.to_dict())

    controller.bus.subscribe(TOPIC_METRICS, on_metrics)
    controller.bus.subscribe(TOPIC_ALERT, on_alert)
    controller.bus.subscribe(TOPIC_ACTION, on_action)
    controller.bus.subscribe(TOPIC_AI_REPORT, on_report)


def run() -> None:
    import uvicorn

    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(create_app(cfg), host=cfg.api.host, port=cfg.api.port,
                log_level=cfg.logging.level.lower())


if __name__ == "__main__":
    run()
