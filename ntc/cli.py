"""Komut satırı arayüzü:  python -m ntc <komut>"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from .ai.provider import create_provider
from .controller import Controller
from .core.config import load_config

console = Console()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ------------------------------------------------------------------- doctor

async def cmd_doctor(args) -> int:
    cfg = load_config(args.config)
    console.print(f"[bold]Yapılandırma[/]  mod={cfg.mode}  "
                  f"hat={cfg.link.downlink_mbps}/{cfg.link.uplink_mbps} Mbps")
    console.print(f"[bold]Veritabanı[/]    {cfg.storage.resolved_path()}")
    console.print(f"[bold]API[/]           http://{cfg.api.host}:{cfg.api.port}")

    provider = await create_provider(cfg.ai)
    health = await provider.health()
    await provider.aclose()

    ok = health.get("ok")
    color = "green" if ok and health["provider"] != "mock" else "yellow"
    console.print(f"[bold]LLM[/]           [{color}]{health['provider']} / "
                  f"{health['model']}[/]")
    if health.get("base_url"):
        console.print(f"                uç: {health['base_url']}")
    if health.get("error"):
        console.print(f"                [yellow]{health['error']}[/]")
    if health.get("available_models"):
        console.print(f"                mevcut: {', '.join(health['available_models'])}")

    if health["provider"] == "mock":
        console.print("\n[yellow]Yerel model bağlı değil.[/] Foundry Local için:")
        console.print("  1) [bold]winget install Microsoft.FoundryLocal[/]")
        console.print("     (MSIX paketi — sideloading kapalıysa kurulum engellenir)")
        console.print(f"  2) [bold]foundry model download {cfg.ai.model}[/]")
        console.print("  3) [bold]python -m ntc doctor[/] ile tekrar kontrol et")
        console.print("\n  Alternatif (Ollama): [bold]ollama pull "
                      f"{cfg.ai.ollama_model}[/] ve config'te provider: ollama")
    return 0


# ------------------------------------------------------------------- analyze

async def _with_controller(cfg, warmup: float, fn):
    controller = Controller(cfg)
    await controller.start()
    try:
        if warmup:
            with console.status(f"Trafik toplanıyor ({warmup:.0f} sn)…"):
                await asyncio.sleep(warmup)
        return await fn(controller)
    finally:
        await controller.stop()


async def cmd_analyze(args) -> int:
    cfg = load_config(args.config)

    async def run(controller: Controller):
        report = await controller.run_analysis()
        if report is None:
            console.print("[red]Analiz üretilemedi[/]")
            return 1
        if args.json:
            console.print_json(json.dumps(report.to_dict(), ensure_ascii=False))
            return 0

        console.print(Panel(report.summary or "—",
                            title=f"Sağlık skoru: {report.health_score}/100",
                            subtitle=f"{report.provider}/{report.model} · "
                                     f"{report.latency_ms:.0f} ms"))
        if report.findings:
            table = Table("Önem", "Bulgu", "Kanıt", title="Bulgular")
            for f in report.findings:
                table.add_row(f["severity"], f["title"], f["evidence"])
            console.print(table)
        if report.recommendations:
            table = Table("Eylem", "Hedef", "Gerekçe", "Güven", title="Öneriler")
            for c in report.recommendations:
                table.add_row(c["action"], c["target"], c["reason"],
                              f"{c['confidence']:.0%}")
            console.print(table)
        return 0

    return await _with_controller(cfg, args.warmup, run)


async def cmd_ask(args) -> int:
    cfg = load_config(args.config)

    async def run(controller: Controller):
        res = await controller.ask(args.question)
        if res.get("error"):
            console.print(f"[red]{res['error']}[/]")
            return 1
        console.print(Panel(res["answer"] or "—",
                            title=args.question,
                            subtitle=f"{res['model']} · {res['latency_ms']:.0f} ms"))
        return 0

    return await _with_controller(cfg, args.warmup, run)


# --------------------------------------------------------------------- watch

def _watch_table(controller: Controller) -> Table:
    status = controller.status()
    link = status["link"]

    table = Table(box=None, expand=True)
    table.add_column("", style="bold", no_wrap=True)
    table.add_column("")

    def util_str(u: float) -> str:
        color = "red" if u >= 0.94 else "yellow" if u >= 0.8 else "green"
        return f"[{color}]{u:.0%}[/]"

    table.add_row("İndirme",
                  f"{link['down_mbps']:.1f} / {link['down_capacity_mbps']} Mbps  "
                  f"{util_str(link['down_utilization'])}")
    table.add_row("Yükleme",
                  f"{link['up_mbps']:.1f} / {link['up_capacity_mbps']} Mbps  "
                  f"{util_str(link['up_utilization'])}")
    table.add_row("Gecikme", f"{link['avg_rtt_ms']:.0f} ms  "
                             f"(retx {link['retransmit_rate']:.1%})")
    table.add_row("Akış / cihaz", f"{link['active_flows']} / {link['active_devices']}")
    table.add_row("Politika", str(status["active_policies"]))
    table.add_row("AI", f"{status['ai']['provider']}/{status['ai']['model']}"
                        + (f"  skor {status['ai']['health_score']}"
                           if status["ai"]["health_score"] is not None else ""))

    talkers = Table("Cihaz", "İndirme", "Yükleme", title="En çok trafik", box=None)
    for row in controller.devices_view()[:6]:
        talkers.add_row(row["hostname"], f"{row['down_mbps']:.1f}",
                        f"{row['up_mbps']:.1f}")

    alerts = Table("Önem", "Uyarı", title="Son uyarılar", box=None)
    for alert in list(controller.alerts)[:5]:
        color = {"critical": "red", "high": "red", "medium": "yellow"}.get(
            alert.severity.value, "white")
        alerts.add_row(f"[{color}]{alert.severity.value}[/]", alert.title)

    outer = Table.grid(expand=True)
    outer.add_row(Panel(table, title="Hat durumu"))
    outer.add_row(Panel(talkers))
    outer.add_row(Panel(alerts))
    return outer


async def cmd_watch(args) -> int:
    cfg = load_config(args.config)
    controller = Controller(cfg)
    await controller.start()
    if args.scenario:
        src = controller.scenario_source
        if src is None:
            console.print(f"[yellow]senaryolar bu kaynakta yok "
                          f"(kaynak: {controller.source.name}) — atlandı[/]")
        else:
            src.trigger(args.scenario, duration=args.duration)
            console.print(f"[cyan]senaryo tetiklendi: {args.scenario}[/]")
    try:
        with Live(_watch_table(controller), console=console,
                  refresh_per_second=2, screen=False) as live:
            while True:
                await asyncio.sleep(1)
                live.update(_watch_table(controller))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await controller.stop()
    return 0


# --------------------------------------------------------------------- serve

def cmd_serve(args) -> int:
    import uvicorn

    from .api.server import create_app

    cfg = load_config(args.config)
    if args.port:
        cfg.api.port = args.port
    if args.host:
        cfg.api.host = args.host

    console.print(f"[bold green]Panel:[/] http://{cfg.api.host}:{cfg.api.port}")
    uvicorn.run(create_app(cfg), host=cfg.api.host, port=cfg.api.port,
                log_level=cfg.logging.level.lower())
    return 0


# ----------------------------------------------------------------------- ana

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ntc", description="Network Traffic Controller")
    parser.add_argument("-c", "--config", help="config.yaml yolu")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="web panelini ve API'yi başlat")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve, is_async=False)

    p = sub.add_parser("watch", help="terminalde canlı ağ durumu")
    p.add_argument("--scenario", help="başlangıçta bir senaryo tetikle")
    p.add_argument("--duration", type=float, default=120.0)
    p.set_defaults(func=cmd_watch, is_async=True)

    p = sub.add_parser("analyze", help="tek seferlik AI analizi")
    p.add_argument("--warmup", type=float, default=15.0,
                   help="analiz öncesi trafik toplama süresi (sn)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_analyze, is_async=True)

    p = sub.add_parser("ask", help="ağa dair serbest soru sor")
    p.add_argument("question")
    p.add_argument("--warmup", type=float, default=15.0)
    p.set_defaults(func=cmd_ask, is_async=True)

    p = sub.add_parser("doctor", help="ortam ve model kurulumunu kontrol et")
    p.set_defaults(func=cmd_doctor, is_async=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    cfg_level = args.log_level or load_config(args.config).logging.level
    _setup_logging(cfg_level)

    if args.is_async:
        try:
            return asyncio.run(args.func(args))
        except KeyboardInterrupt:
            return 130
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
