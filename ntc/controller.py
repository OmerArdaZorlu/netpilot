"""Sistemin orkestrasyonu: toplayıcı, metrik, optimizer ve AI analistini
birbirine bağlayan uzun ömürlü servis."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from .ai.analyst import AIAnalyst, AIReport
from .ai.flowai import pins_for
from .ai.provider import LLMProvider, create_provider
from .core.bus import (
    TOPIC_ACTION,
    TOPIC_AI_REPORT,
    TOPIC_ALERT,
    TOPIC_FLOW_BATCH,
    TOPIC_METRICS,
    EventBus,
)
from .core.config import Config
from .core.models import (
    Alert,
    Direction,
    OptimizationAction,
    new_id,
    now,
)
from .storage.db import Storage
from .traffic.metrics import MetricsEngine
from .traffic.flowopt import (
    FlowOptimizer,
    FlowPlan,
    PathAssigner,
    actions_from_plan,
    demands_from_signals,
)
from .enforce import (
    Enforcer,
    PolicySet,
    Reconciliation,
    approved_keys,
    build_driver,
    policies_from_plan,
)
from .traffic.classify import ClassifyAudit
from .traffic.demand import DemandEstimator
from .traffic.flowpolicy import DEFAULT_POLICY, FlowPolicy
from .traffic.optimizer import TrafficOptimizer
from .traffic.topology import Topology
from .traffic.source import FlowSource, build_source

log = logging.getLogger(__name__)


class Controller:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bus = EventBus()
        # Akış kaynağı. Somut sınıf değil arayüz: `cfg.mode` bir ayar gibi
        # görünüp hiçbir şeyi değiştirmiyordu, artık kaynağı o seçiyor.
        # Bilinmeyen modda `build_source` hata veriyor — sessizce simülasyona
        # düşmek, gerçek ağı izlediğini sanan bir operatör demek olurdu.
        self.source: FlowSource = build_source(cfg)
        # Akış optimize edici — eşik denetçisinden ayrı bir iş yapıyor:
        # eşik "hat doldu" der, bu "trafiği nasıl dağıtalım" hesaplar.
        self.topology = (
            Topology.from_config(cfg.topology_raw) if cfg.topology_raw
            else Topology.default(cfg.link.downlink_mbps, cfg.link.uplink_mbps))

        # **Hat kapasitesinin tek kaynağı topoloji.** Panel doluluğu `link:`
        # ayarından, çözücünün darboğazı topolojiden hesaplanıyor. İkisi elle
        # tutulduğunda bir kez ayrıştılar: panel "%92 dolu" derken çözücü
        # "darboğaz yok" dedi, çünkü varsayılan topoloji yapılandırmada
        # olmayan kapasite uyduruyordu. Türetince ayrışması imkânsız hale
        # geliyor — kuralı yorumla korumaktansa yapıyla korumak daha güvenli.
        _d, _u = self.topology.wan_capacity()
        if _d > 0 and _u > 0:
            if (abs(_d - cfg.link.downlink_mbps) > 0.5
                    or abs(_u - cfg.link.uplink_mbps) > 0.5):
                log.info("Hat kapasitesi topolojiden alındı: %.0f/%.0f Mbps "
                         "(yapılandırmada %.0f/%.0f yazıyordu)",
                         _d, _u, cfg.link.downlink_mbps, cfg.link.uplink_mbps)
            cfg.link.downlink_mbps = _d
            cfg.link.uplink_mbps = _u

        # Bunlar `cfg.link` referansını topoloji türetmesinden **sonra** alıyor.
        self.metrics = MetricsEngine(cfg.link, cfg.collector.window_seconds)
        self.optimizer = TrafficOptimizer(cfg.optimizer, cfg.link)
        # Çözücünün **hedefi**. Sabit bir tablo değil: AI duruma bakıp
        # kuruyor (gece yedekleme penceresi, sayaçlı hat devrede, bozuk
        # bacak...). Model yoksa veya saçmalarsa varsayılanda kalıyor.
        self.flow_policy: FlowPolicy = DEFAULT_POLICY
        self.policy_note: str = "başlangıç"
        self.policy_issues: list[str] = []
        # Modelin son akış önerisi ve akışın ne kadarının onun kararı olduğu.
        self.ai_flow: Any = None
        self.ai_flow_share: float = 0.0
        self.flow_optimizer = FlowOptimizer(self.topology, self.flow_policy)
        # Talep tahmini — doygun hatta ölçülen hız zaten tavandır ve onu
        # "talep" saymak sistemi tam da tıkanma anında körleştiriyordu.
        self.demand_estimator = DemandEstimator()
        # Trafik sınıflandırma. Varsayılan gölge: karar veriyor ama
        # yazmıyor; uyum oranı `/api/classify` ucunda ölçülüyor.
        self.classifier = ClassifyAudit(self.cfg.classify)
        self.flow_plan: FlowPlan | None = None
        # Akışları planın oranlarına göre çıkışlara dağıtır.
        self.path_assigner = PathAssigner()
        # İnfaz katmanı — planı politikaya, politikayı cihaz komutuna çevirir.
        # Varsayılan gölge modu: komut üretir, çalıştırmaz.
        self.enforcer = self._build_enforcer()
        self.policies = PolicySet()
        self.storage = Storage(cfg.storage.resolved_path(), cfg.storage.retain_hours)

        self.provider: LLMProvider | None = None
        self.analyst: AIAnalyst | None = None

        self.alerts: deque[Alert] = deque(maxlen=200)
        self.actions: deque[OptimizationAction] = deque(maxlen=200)
        self.reports: deque[AIReport] = deque(maxlen=50)

        self._tasks: list[asyncio.Task] = []
        self._running = False
        self.started_at = 0.0

    def _build_enforcer(self) -> Enforcer | None:
        """Yapılandırmadaki sürücüyü kurar.

        Sürücü kurulamıyorsa (yanlış ad, eksik parametre) denetleyici
        **çalışmaya devam ediyor** — infaz olmadan sistem hâlâ ölçüyor,
        hesaplıyor ve öneriyor. İnfazın açılamaması ölçümü de durdursaydı,
        elde hiçbir şey kalmazdı.
        """
        cfg = self.cfg.enforce
        if not cfg.enabled:
            return None

        def kur(ad: str):
            kwargs: dict = {}
            if ad == "linux":
                kwargs = {"wan_if": cfg.wan_if, "lan_if": cfg.lan_if,
                          "table_by_egress": dict(cfg.tables or {})}
            return build_driver(ad, **kwargs)

        try:
            # Kapsam başına sürücü: çekirdek router ile uçtaki Windows
            # domain aynı anda sürülüyor. İkisi de boşsa tek `driver`
            # tüm kapsamlara bakıyor (tek cihazlı kurulum, demo).
            if cfg.core_driver or cfg.edge_driver:
                surucular = {}
                if cfg.core_driver:
                    surucular["core"] = kur(cfg.core_driver)
                if cfg.edge_driver:
                    surucular["edge"] = kur(cfg.edge_driver)
                hedef = surucular
            else:
                hedef = kur(cfg.driver)
            # Canlı mod bilerek desteklenmiyor: çalıştırıcı ancak üzerinde
            # doğrulama yapılabilecek bir cihaz varken yazılmalı.
            return Enforcer(hedef, mode="golge")
        except Exception:
            log.exception("İnfaz sürücüsü kurulamadı (core=%s edge=%s tek=%s); "
                          "infaz kapalı",
                          cfg.core_driver, cfg.edge_driver, cfg.driver)
            return None

    # -------------------------------------------------------------- senaryo

    @property
    def scenario_source(self) -> Any:
        """Senaryo tetikleyebilen kaynak; yeteneği yoksa `None`.

        Senaryolar arayüzün parçası değil çünkü canlı yakalamada karşılığı
        yok — "tıkanma senaryosu tetikle" gerçek ağa sahte trafik basmak
        olurdu. Yeteneği olmayan kaynakta API ucu gerekçeli hata veriyor;
        boş geçseydi düğmeye basan operatör tetiklediğini sanacaktı.
        """
        return self.source if getattr(self.source, "supports_scenarios", False) else None

    @property
    def scenarios(self) -> list[Any]:
        src = self.scenario_source
        return list(src.scenarios) if src is not None else []

    # ------------------------------------------------------------------ yaşam

    async def start(self) -> None:
        if self._running:
            return
        await self.storage.open()
        await self.source.start()

        self.provider = await create_provider(self.cfg.ai)
        self.analyst = AIAnalyst(self.cfg.ai, self.provider)

        self._running = True
        self.started_at = now()
        self._tasks = [
            asyncio.create_task(self._collect_loop(), name="collect"),
            asyncio.create_task(self._optimize_loop(), name="optimize"),
            asyncio.create_task(self._ai_loop(), name="ai"),
            asyncio.create_task(self._flow_loop(), name="flow"),
            asyncio.create_task(self._policy_loop(), name="policy"),
            asyncio.create_task(self._prune_loop(), name="prune"),
        ]
        log.info("Controller başladı (mod=%s, kaynak=%s, ai=%s/%s)",
                 self.cfg.mode, self.source.name,
                 self.provider.name, self.provider.model)

    async def stop(self) -> None:
        self._running = False
        # Kurduğumuz kısıtları bırakmadan çıkmıyoruz. Sahipsiz kalan bir
        # hız tavanı en kötü arıza biçimi: sebebi görünmez, kimse kaldırmaz.
        if self.enforcer is not None and self.enforcer.active:
            try:
                geri = self.enforcer.rollback()
                log.info("İnfaz geri alındı: %s", geri.summary())
            except Exception:
                log.exception("İnfaz geri alınamadı")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.source.aclose()
        if self.provider is not None:
            await self.provider.aclose()
        await self.storage.close()
        log.info("Controller durdu")

    # ------------------------------------------------------------------ döngüler

    async def _collect_loop(self) -> None:
        dt = self.cfg.collector.tick_seconds
        while self._running:
            try:
                flows = self.source.tick(dt)
                self.classifier.process(flows)
                self._stamp_paths(flows)
                self.metrics.add(flows)
                stats = self.metrics.link_stats()
                self.metrics.sample_history(stats)

                await self.storage.save_notable_flows(flows)
                await self.storage.save_sample(stats)
                await self.bus.publish(TOPIC_FLOW_BATCH, flows)
                await self.bus.publish(TOPIC_METRICS, stats)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Toplayıcı döngüsünde hata")
            await asyncio.sleep(dt)

    async def _optimize_loop(self) -> None:
        interval = self.cfg.optimizer.interval_seconds
        while self._running:
            await asyncio.sleep(interval)
            if not self.cfg.optimizer.enabled:
                continue
            try:
                # Eşik motoru yalnız uyarır; "ne kadar" kararını akış
                # çözücüsü veriyor (bkz. optimizer.evaluate yorumu).
                result = self.optimizer.evaluate(self.metrics, self.source.devices)
                await self._emit_alerts(result.alerts)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Optimizer döngüsünde hata")

    async def _ai_loop(self) -> None:
        interval = self.cfg.ai.analysis_interval_seconds
        # İlk analiz için pencerenin bir miktar dolmasını bekle.
        await asyncio.sleep(min(interval, self.cfg.collector.window_seconds / 4))
        while self._running:
            try:
                await self.run_analysis()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("AI döngüsünde hata")
            await asyncio.sleep(interval)

    async def _flow_loop(self) -> None:
        """Ölçülen talepleri topolojiye oturtup en iyi dağıtımı hesaplar.

        Eşik döngüsünden ayrı ve daha seyrek çalışıyor. Sonucu bir *hedef
        durum*: kim ne kadar alabilir, hangi kenardan, kimden ne kadar geri
        çekilmeli. Uygulayan yok — 5. mimari ilke, önce gölge modda.
        """
        interval = self.cfg.flow.interval_seconds
        await asyncio.sleep(min(interval, self.cfg.collector.window_seconds / 4))
        while self._running:
            if self.cfg.flow.enabled:
                try:
                    await self.run_flow_optimization()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Akış optimizasyon döngüsünde hata")
            await asyncio.sleep(interval)

    async def _policy_loop(self) -> None:
        """Çözücünün hedefini duruma göre yeniden kurar.

        Akış döngüsünden **seyrek** koşuyor ve bu bilinçli: hedefi her 15
        saniyede değiştirmek, çözümü sürekli oynatıp ağı sallar. Hedef yavaş
        değişen bir şey — gün içindeki faz, hat durumu, olay hali.
        """
        interval = self.cfg.ai.policy_interval_seconds
        await asyncio.sleep(min(interval, self.cfg.collector.window_seconds))
        while self._running:
            if self.cfg.ai.policy_enabled:
                try:
                    await self.refresh_policy()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Politika döngüsünde hata")
            await asyncio.sleep(interval)

    async def refresh_policy(self) -> FlowPolicy:
        """Tek seferlik hedef güncellemesi. API ve CLI buradan çağırıyor."""
        if self.analyst is None:
            return self.flow_policy
        onceki = self.flow_policy
        politika, sorunlar, sonuc = await self.analyst.decide_policy(
            self.metrics, self.topology, list(self.alerts),
            current=onceki)
        self.policy_note = sonuc
        self.policy_issues = sorunlar
        if sonuc == "kabul":
            fark = politika.diff(onceki)
            self.flow_policy = politika
            self.flow_optimizer.policy = politika
            if fark:
                log.info("Hedef değişti (%s): %s", politika.situation or "-",
                         "; ".join(fark))
        elif sorunlar:
            # Reddedilen politikayı sessizce yutmuyoruz: modelin ne saçmaladığı
            # panelde görünmeli, yoksa "AI çalışıyor" yanılsaması kalır.
            log.warning("Hedef güncellenmedi (%s): %s", sonuc,
                        "; ".join(sorunlar))
        return self.flow_policy

    async def run_flow_optimization(self) -> FlowPlan | None:
        """Tek seferlik akış çözümü. CLI ve API buradan çağırıyor."""
        signals = self.metrics.device_signals()
        if not signals:
            return None
        # Aktif hız tavanlarımız tahminciye en sağlam kanıt: ölçüm tavana
        # yapışmışsa cihaz bastırılıyordur ve talebi tavanın üstündedir.
        tavanlar: dict[tuple[str, str], float] = {}
        for a in self.optimizer.active.values():
            if a.kind.value != "rate_limit":
                continue
            h = (a.params or {}).get("hostname")
            y = (a.params or {}).get("direction")
            c = (a.params or {}).get("cap_mbps")
            if h and y and c:
                tavanlar[(h, y)] = float(c)

        demands = demands_from_signals(
            signals, self.source.devices, self.topology,
            estimator=self.demand_estimator,
            link_stats=self.metrics.link_stats(),
            congestion_threshold=self.cfg.link.congestion_threshold,
            active_caps=tavanlar)
        if not demands:
            return None

        # --- AI akışı kendisi kuruyor ---
        #
        # Zincirin başında model duruyor: tahsisleri `pinned` olarak çözücünün
        # 0. turuna giriyor, LP kalanı dolduruyor. Model çökerse, geçersiz
        # üretirse ya da kapalıysa `pins` boş kalır ve çözücü eskisi gibi
        # tek başına çalışır — kayıp yok, yalnız karar payı sıfırlanır.
        pins: dict[str, float] = {}
        self.ai_flow = None
        if self.cfg.ai.flow_enabled and self.analyst is not None:
            try:
                ai_plan, _atlanan, _ = await self.analyst.propose_flow(
                    self.topology, demands)
                self.ai_flow = ai_plan
                if ai_plan.valid:
                    pins = pins_for(ai_plan, demands)
            except Exception:
                log.exception("AI akış önerisi alınamadı; LP tek başına çözecek")

        # LP saf CPU işi; olay döngüsünü kilitlememesi için ayrı iş parçacığında.
        plan = await asyncio.to_thread(self.flow_optimizer.solve, demands,
                                       self.flow_policy, pins)
        if pins:
            ai_pay = sum(min(pins.get(a.demand.key, 0.0), a.granted_mbps)
                         for a in plan.allocations)
            toplam = sum(a.granted_mbps for a in plan.allocations)
            self.ai_flow_share = ai_pay / toplam if toplam > 0 else 0.0
        else:
            self.ai_flow_share = 0.0
        self.flow_plan = plan
        self.path_assigner.update(plan)
        await self.storage.save_flow_plan(new_id("flw"), now(), plan)

        # Çözücünün kararlarını aksiyona çevirip politika defterine al.
        actions = actions_from_plan(plan, self.source.devices,
                                    self.cfg.flow.min_pullback_mbps)
        recorded = self.optimizer.adopt(actions)
        if recorded:
            await self._emit_actions(recorded)

        await self._enforce(plan)

        pulls = plan.pullbacks(self.cfg.flow.min_pullback_mbps)
        if pulls:
            top = pulls[0]
            log.info("Akış planı: %d cihazdan geri çekme, en büyüğü %s "
                     "(%.1f Mbps); darboğaz: %s",
                     len(pulls), top["device"], top["pullback_mbps"],
                     ", ".join(b["edge"] for b in plan.bottlenecks()) or "yok")
        return plan

    async def _enforce(self, plan: FlowPlan) -> Reconciliation | None:
        """Planı politikaya çevirip infaz katmanına uzlaştırır.

        **Aksiyon defterinden sonra çağrılıyor, önce değil.** Onay kapısı
        aksiyonların `applied` bayrağına bakıyor; defter güncellenmeden
        çağırsaydık operatörün bu turda onayladığı bir aksiyon bir tur
        gecikmeyle uygulanırdı.
        """
        if self.enforcer is None:
            return None
        self.policies = policies_from_plan(
            plan, self.source.devices, self.cfg.flow.min_pullback_mbps)
        onay = None
        if self.cfg.enforce.require_approval:
            onay = approved_keys(list(self.optimizer.active.values()),
                                 self.policies)
        try:
            return await asyncio.to_thread(
                self.enforcer.reconcile, self.policies, onay)
        except Exception:
            log.exception("İnfaz uzlaştırması başarısız")
            return None

    def _stamp_paths(self, flows: list) -> None:
        """Yeni akışlara çıkış düğümü damgalar.

        Yön akışın kendi yönünden geliyor; hangi cihaz, hangi sınıf olduğu da
        akışta var. Atama akış anahtarının hash'i ile yapıldığı için aynı akış
        her tick'te aynı çıkışa düşüyor — yol ortada değişmiyor.
        """
        if not self.flow_plan:
            return
        for f in flows:
            direction = "lan" if f.direction is Direction.LATERAL else (
                "down" if f.bytes_down >= f.bytes_up else "up")
            device = self.source.devices.get(f.device_id)
            host = getattr(device, "hostname", None) or f.device_id
            key = f"{f.src_ip}:{f.src_port}->{f.dst_ip}:{f.dst_port}/{f.proto}"
            f.egress = self.path_assigner.assign(
                host, f.traffic_class.value, direction, key)

    async def _prune_loop(self) -> None:
        while self._running:
            await asyncio.sleep(600)
            try:
                removed = await self.storage.prune()
                if removed:
                    log.info("Eski kayıtlar temizlendi: %d satır", removed)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Temizlik döngüsünde hata")

    # ------------------------------------------------------------------ eylemler

    async def run_analysis(self) -> AIReport | None:
        if self.analyst is None:
            return None
        # Analist artık tespit etmiyor, açıklıyor: kural motorunun uyarıları
        # ve çözücünün kararı ona olgu olarak veriliyor.
        report = await self.analyst.analyze(
            self.metrics, self.source.devices, self.optimizer,
            alerts=list(self.alerts)[:10], flow_plan=self.flow_plan)
        self.reports.appendleft(report)
        await self.storage.save_report(report)
        await self.bus.publish(TOPIC_AI_REPORT, report)

        if report.actions:
            await self._emit_actions(report.actions)
        # ⚠️ AI bulguları artık uyarıya çevrilmiyor. Analist yeni sorun
        # tespit etmiyor; açıkladığı uyarılar zaten kural motoru tarafından
        # yayınlanmış oluyor. Tekrar yayınlamak aynı olayı iki kez göstermek
        # olurdu.
        return report

    async def ask(self, question: str) -> dict[str, Any]:
        if self.analyst is None:
            return {"error": "AI analisti hazır değil", "answer": ""}
        return await self.analyst.ask(
            question, self.metrics, self.source.devices, self.optimizer)

    async def _emit_actions(self, actions: list[OptimizationAction]) -> None:
        if not actions:
            return
        for action in actions:
            self.actions.appendleft(action)
        await self.storage.save_actions(actions)
        for action in actions:
            await self.bus.publish(TOPIC_ACTION, action)

    async def _emit_alerts(self, alerts: list[Alert]) -> None:
        if not alerts:
            return
        for alert in alerts:
            self.alerts.appendleft(alert)
            log.info("[%s] %s — %s", alert.severity.value.upper(),
                     alert.title, alert.detail)
        await self.storage.save_alerts(alerts)
        for alert in alerts:
            await self.bus.publish(TOPIC_ALERT, alert)

    # ------------------------------------------------------------------ durum

    def status(self) -> dict[str, Any]:
        stats = self.metrics.link_stats()
        last = self.reports[0] if self.reports else None
        return {
            "running": self._running,
            "mode": self.cfg.mode,
            # Kaynağın adı ayrı duruyor: `mode` istenen, bu **olan**.
            "source": self.source.name,
            "scenarios_supported": self.scenario_source is not None,
            "uptime_seconds": round(now() - self.started_at, 1) if self.started_at else 0,
            "ai": {
                "provider": self.provider.name if self.provider else None,
                "model": self.provider.model if self.provider else None,
                "last_analysis_ts": last.ts if last else None,
                "health_score": last.health_score if last else None,
            },
            "flow": {
                "enabled": self.cfg.flow.enabled,
                "solved": self.flow_plan is not None,
                # Özet; tam plan /api/flow ucunda. Panel her saniye status
                # çekiyor, kenar kenar döküm oraya sığmaz.
                "total_demand_mbps": round(sum(
                    a.demand.mbps for a in self.flow_plan.allocations), 2)
                    if self.flow_plan else None,
                "total_granted_mbps": round(sum(
                    a.granted_mbps for a in self.flow_plan.allocations), 2)
                    if self.flow_plan else None,
                "pullback_count": len(self.flow_plan.pullbacks(
                    self.cfg.flow.min_pullback_mbps)) if self.flow_plan else 0,
                "bottlenecks": [b["edge"] for b in self.flow_plan.bottlenecks()]
                    if self.flow_plan else [],
            },
            "link": {
                "down_mbps": round(stats.down_bps / 1e6, 2),
                "up_mbps": round(stats.up_bps / 1e6, 2),
                "lan_mbps": round(stats.lan_bps / 1e6, 2),
                "down_capacity_mbps": self.cfg.link.downlink_mbps,
                "up_capacity_mbps": self.cfg.link.uplink_mbps,
                "down_utilization": round(stats.down_utilization, 4),
                "up_utilization": round(stats.up_utilization, 4),
                "avg_rtt_ms": stats.avg_rtt_ms,
                "retransmit_rate": stats.retransmit_rate,
                "active_flows": stats.flow_count,
                "active_devices": stats.device_count,
            },
            "totals": self.metrics.totals,
            "active_policies": len(self.optimizer.active),
            "scenarios": [s.to_dict() for s in self.scenarios],
            "counts": {
                "alerts": len(self.alerts),
                "actions": len(self.actions),
                "reports": len(self.reports),
            },
        }

    def devices_view(self) -> list[dict[str, Any]]:
        signals = self.metrics.device_signals()
        out = []
        for device in self.source.devices.values():
            sig = signals.get(device.id)
            row = device.to_dict()
            row["signals"] = sig.to_dict() if sig else None
            row["down_mbps"] = round(sig.down_bps / 1e6, 2) if sig else 0.0
            row["up_mbps"] = round(sig.up_bps / 1e6, 2) if sig else 0.0
            row["lan_mbps"] = round(sig.lan_bps / 1e6, 2) if sig else 0.0
            row["policies"] = [
                a.to_dict() for a in self.optimizer.active.values()
                if a.target == device.id
            ]
            out.append(row)
        out.sort(key=lambda r: r["down_mbps"] + r["up_mbps"], reverse=True)
        return out
