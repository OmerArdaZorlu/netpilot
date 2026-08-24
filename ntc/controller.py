"""Sistemin orkestrasyonu: toplayıcı, metrik, optimizer ve AI analistini
birbirine bağlayan uzun ömürlü servis."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from .ai.analyst import AIAnalyst, AIReport
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
from .core.models import Alert, OptimizationAction, new_id, now
from .storage.db import Storage
from .traffic.metrics import MetricsEngine
from .traffic.flowopt import FlowOptimizer, FlowPlan, demands_from_signals
from .traffic.optimizer import TrafficOptimizer
from .traffic.topology import Topology
from .traffic.simulator import TrafficSimulator

log = logging.getLogger(__name__)


class Controller:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.simulator = TrafficSimulator()
        self.metrics = MetricsEngine(cfg.link, cfg.collector.window_seconds)
        self.optimizer = TrafficOptimizer(cfg.optimizer, cfg.link)
        # Akış optimize edici — eşik denetçisinden ayrı bir iş yapıyor:
        # eşik "hat doldu" der, bu "trafiği nasıl dağıtalım" hesaplar.
        self.topology = Topology.from_config(cfg.topology_raw)             if cfg.topology_raw else Topology.default(
                cfg.link.downlink_mbps, cfg.link.uplink_mbps)
        self.flow_optimizer = FlowOptimizer(self.topology)
        self.flow_plan: FlowPlan | None = None
        self.storage = Storage(cfg.storage.resolved_path(), cfg.storage.retain_hours)

        self.provider: LLMProvider | None = None
        self.analyst: AIAnalyst | None = None

        self.alerts: deque[Alert] = deque(maxlen=200)
        self.actions: deque[OptimizationAction] = deque(maxlen=200)
        self.reports: deque[AIReport] = deque(maxlen=50)

        self._tasks: list[asyncio.Task] = []
        self._running = False
        self.started_at = 0.0

    # ------------------------------------------------------------------ yaşam

    async def start(self) -> None:
        if self._running:
            return
        await self.storage.open()

        self.provider = await create_provider(self.cfg.ai)
        self.analyst = AIAnalyst(self.cfg.ai, self.provider)

        self._running = True
        self.started_at = now()
        self._tasks = [
            asyncio.create_task(self._collect_loop(), name="collect"),
            asyncio.create_task(self._optimize_loop(), name="optimize"),
            asyncio.create_task(self._ai_loop(), name="ai"),
            asyncio.create_task(self._flow_loop(), name="flow"),
            asyncio.create_task(self._prune_loop(), name="prune"),
        ]
        log.info("Controller başladı (mod=%s, ai=%s/%s)",
                 self.cfg.mode, self.provider.name, self.provider.model)

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self.provider is not None:
            await self.provider.aclose()
        await self.storage.close()
        log.info("Controller durdu")

    # ------------------------------------------------------------------ döngüler

    async def _collect_loop(self) -> None:
        dt = self.cfg.collector.tick_seconds
        while self._running:
            try:
                flows = self.simulator.tick(dt)
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
                result = self.optimizer.evaluate(self.metrics, self.simulator.devices)
                await self._emit_actions(result.actions)
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

    async def run_flow_optimization(self) -> FlowPlan | None:
        """Tek seferlik akış çözümü. CLI ve API buradan çağırıyor."""
        signals = self.metrics.device_signals()
        if not signals:
            return None
        demands = demands_from_signals(signals, self.simulator.devices,
                                       self.topology)
        if not demands:
            return None

        # LP saf CPU işi; olay döngüsünü kilitlememesi için ayrı iş parçacığında.
        plan = await asyncio.to_thread(self.flow_optimizer.solve, demands)
        self.flow_plan = plan
        await self.storage.save_flow_plan(new_id("flw"), now(), plan)

        pulls = plan.pullbacks(self.cfg.flow.min_pullback_mbps)
        if pulls:
            top = pulls[0]
            log.info("Akış planı: %d cihazdan geri çekme, en büyüğü %s "
                     "(%.1f Mbps); darboğaz: %s",
                     len(pulls), top["device"], top["pullback_mbps"],
                     ", ".join(b["edge"] for b in plan.bottlenecks()) or "yok")
        return plan

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
        report = await self.analyst.analyze(
            self.metrics, self.simulator.devices, self.optimizer)
        self.reports.appendleft(report)
        await self.storage.save_report(report)
        await self.bus.publish(TOPIC_AI_REPORT, report)

        if report.actions:
            await self._emit_actions(report.actions)
        # AI bulguları da aynı soğuma defterinden geçer — analist her 30 sn'de bir
        # aynı tıkanmayı yeniden bildirir, akış bundan kirlenmesin.
        await self._emit_alerts(
            self.optimizer.debounce(AIAnalyst.report_alerts(report)))
        return report

    async def ask(self, question: str) -> dict[str, Any]:
        if self.analyst is None:
            return {"error": "AI analisti hazır değil", "answer": ""}
        return await self.analyst.ask(
            question, self.metrics, self.simulator.devices, self.optimizer)

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
            "scenarios": [s.to_dict() for s in self.simulator.scenarios],
            "counts": {
                "alerts": len(self.alerts),
                "actions": len(self.actions),
                "reports": len(self.reports),
            },
        }

    def devices_view(self) -> list[dict[str, Any]]:
        signals = self.metrics.device_signals()
        out = []
        for device in self.simulator.devices.values():
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
