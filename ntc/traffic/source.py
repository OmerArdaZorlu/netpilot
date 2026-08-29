"""Akış kaynağı — sistemin trafiği nereden gördüğü.

Faz 1 boyunca tek kaynak simülatördü ve `controller._collect_loop` onu
**doğrudan** çağırıyordu (`self.simulator.tick(dt)`). `cfg.mode`
("simulation" | "live") ise hiçbir davranışı değiştirmiyordu: yalnız
`cli.py` ile `/api/status` ekranına basılıyordu. Yani canlı mod bir ayar
gibi görünen ama karşılığı olmayan bir **vaatti** — `NTC_MODE=live` ile
çalıştırılan sistem sessizce simülasyon üretmeye devam ediyordu.

Bu modül o boşluğu kapatıyor. Toplayıcı artık somut bir sınıfa değil
`FlowSource` arayüzüne bağlı; kaynağı `build_source()` seçiyor ve
tanımadığı bir mod için **sessizce simülasyona düşmüyor**, gerekçeli hata
veriyor.

Arayüzün dar tutulmasının sebebi: kaynağın tek işi akış üretmek. Metrik,
sınıflandırma, çözücü — hiçbiri kaynağı tanımıyor, hepsi `Flow` listesi
alıyor. Canlı kaynak geldiğinde değişmesi gereken tek yer burası olacak.

**Senaryolar kaynağa ait bir yetenek, arayüzün parçası değil.** "Tıkanma
senaryosunu tetikle" yalnız üretilmiş trafikte anlamlı; canlı yakalamada
karşılığı yok ve olmamalı (gerçek ağa sahte trafik basmak demek olurdu).
Bu yüzden `supports_scenarios` bayrağı var: API ucu bakıp yeteneği olmayan
kaynakta gerekçeli hata döndürüyor. Arayüze koyup canlı kaynakta boş
geçseydik, düğmeye basan operatör "tetikledim" sanacaktı.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..core.models import Device, Flow

if TYPE_CHECKING:  # yalnız tip denetimi için — çalışma anında döngü olmasın
    from ..core.config import Config


# Kabul edilen mod adları. `mode` kullanıcı yazısı; küçük harfe indirilip
# burada aranıyor. Türkçe karşılıklar da kabul ediliyor çünkü panel ve CLI
# Türkçe ve kullanıcının `mod: simulasyon` yazması beklenebilir.
SIMULATION_MODES = frozenset({"simulation", "sim", "simulasyon", "simülasyon"})
LIVE_MODES = frozenset({"live", "canli", "canlı"})


@runtime_checkable
class FlowSource(Protocol):
    """Akış üreten her kaynağın uyması gereken sözleşme."""

    #: Kaynağın adı — loglarda ve `/api/status` içinde görünür.
    name: str
    #: Bilinen cihazlar. Simülatörde baştan kurulu, canlı kaynakta keşifle
    #: dolar. Optimize edici, çözücü ve panel bu sözlüğü okuyor.
    devices: dict[str, Device]
    #: Senaryo tetikleme yeteneği var mı (bkz. modül açıklaması).
    supports_scenarios: bool
    #: Kaynak akışın `traffic_class` alanını **doğru** dolduruyor mu.
    #: Simülatör dolduruyor (akışı kendisi ürettiği için bilir), canlı kaynak
    #: dolduramaz — kabloda öyle bir alan yok. Kontrolcü buna bakıp
    #: sınıflandırıcıyı doğru moda alıyor: etiketlemeyen kaynakta gölge modda
    #: kalmak, bütün trafiği varsayılan sınıfta bırakmak demek olurdu ve
    #: önceliklendirme sessizce anlamsızlaşırdı.
    labels_traffic_class: bool

    def tick(self, dt: float = 1.0) -> list[Flow]:
        """Son `dt` saniyeye ait akışları döndürür."""
        ...

    async def start(self) -> None:
        """Kaynağı ayağa kaldırır (abonelik, süreç, dosya kolu)."""
        ...

    async def aclose(self) -> None:
        """Kaynağı kapatır. Kapanışta her zaman çağrılır."""
        ...


class UnsupportedMode(RuntimeError):
    """`mode` tanınmıyor ya da o kaynağın kodu henüz yok."""


def _bos_live():
    """`live:` bloğu yazılmamışsa varsayılan ayarlar."""
    from ..core.config import LiveConfig
    return LiveConfig()


def build_source(cfg: "Config") -> FlowSource:
    """`cfg.mode`'a göre akış kaynağını kurar.

    Bilinmeyen modda **hata veriyor**, varsayılana düşmüyor: yazım hatası
    yüzünden simülasyonda kalan bir kurulum, gerçek ağı izlediğini sanan
    bir operatör demektir.
    """
    mode = (cfg.mode or "").strip().lower()

    if mode in SIMULATION_MODES:
        from .simulator import TrafficSimulator
        return TrafficSimulator()

    if mode in LIVE_MODES:
        from .live import LiveSource
        return LiveSource(getattr(cfg, "live", None) or _bos_live())

    raise UnsupportedMode(
        f"Bilinmeyen mode: {cfg.mode!r}. Geçerli değerler: "
        f"{', '.join(sorted(SIMULATION_MODES | LIVE_MODES))}"
    )
