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
from .core.models import Alert, OptimizationAction, now
from .storage.db import Storage
from .traffic.metrics import MetricsEngine
from .traffic.optimizer import TrafficOptimizer
from .traffic.simulator import TrafficSimulator

log = logging.getLogger(__name__)


class Controller:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.simulator = TrafficSimulator()
        self.metrics = MetricsEngine(cfg.link, cfg.collector.window_seconds)
        self.optimizer = TrafficOptimizer(cfg.optimizer, cfg.link)
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
