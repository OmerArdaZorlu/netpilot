"""Yerel LLM sağlayıcı katmanı.

Tek arayüz, iki uygulama:
  * OllamaProvider — gerçek yerel model (phi4-mini, llama3.2, qwen2.5 ...)
  * MockProvider   — model kurulu değilken sistemin çalışmaya devam etmesi için

Sistemin hiçbir yeri Ollama'yı doğrudan tanımaz; hep bu arayüzden geçer.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..core.config import AIConfig

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"
    model: str = "-"

    @abstractmethod
    async def complete(self, system: str, prompt: str,
                       json_mode: bool = False) -> str:
        ...

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        ...

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        """JSON bekleyen çağrılar için — model konuşkanlık yaparsa kurtarır."""
        raw = await self.complete(system, prompt, json_mode=True)
        return extract_json(raw)

    async def aclose(self) -> None:
        return None


def _scan_object(text: str) -> dict[str, Any] | None:
    """Metindeki ilk dengeli JSON nesnesini söker; yoksa None.

    Dize içindeki süslü parantezleri saymamak için kaçış ve tırnak durumunu
    izliyor — aksi halde `{"reason": "a { b"}` gibi bir yanıt dengesiz görünür.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:idx + 1])
                except json.JSONDecodeError:
                    return None
    return None


_ARITMETIK = re.compile(r"(:[ ]*)(-?[0-9]+(?:[.][0-9]+)?)[ ]*([*/])[ ]*(-?[0-9]+(?:[.][0-9]+)?)")


def _collapse_arithmetic(text: str) -> str:
    """`"grant_mbps": 229.7 * 0.83` gibi ifadeleri sonuca indirir.

    **Neden var:** modele "herkese kabaca %83'ünü ver" dendiğinde phi-4-mini
    niyeti doğru anlıyor ama çarpmayı yapamıyor ve JSON'un içine **ifadeyi**
    yazıyor. Sonuç geçersiz JSON: iyi bir cevap tamamen kayboluyordu
    (ölçüldü: 10 rastgele ağın 1'i yalnız bu yüzden düşüyordu).

    Yalnız **değer konumundaki** (iki nokta üst üsteden hemen sonra gelen)
    sayı-işlem-sayı üçlüsü işleniyor. Dize değerleri tırnakla başladığı için
    desene takılmıyor; modelin gerekçe cümlesindeki `*` işaretine
    dokunulmuyor.
    """
    def bir(m: "re.Match[str]") -> str:
        onek, a, op, b = m.group(1), float(m.group(2)), m.group(3), float(m.group(4))
        if op == "/" and b == 0:
            return m.group(0)
        return f"{onek}{round(a * b if op == '*' else a / b, 3)}"

    for _ in range(4):          # a * b * c gibi zincirler için birkaç tur
        yeni = _ARITMETIK.sub(bir, text)
        if yeni == text:
            break
        text = yeni
    return text


def _strip_trailing_commas(text: str) -> str:
    """`,` + kapanış parantezi ikilisini temizler — dize dışındakileri.

    **Neden var:** phi-4-mini düzenli olarak son elemandan sonra virgül
    bırakıyor (`},
  ],`). JSON bunu kabul etmez ve hata mesajı yanıltıcı
    oluyor: *"Expecting property name enclosed in double quotes"* — panelde
    görülen tam bu. Kesilme kurtarması da işe yaramıyordu, çünkü sorun
    metnin sonunda değil ortasında.

    Dize içindeki virgüllere dokunulmuyor; gerekçe cümlesindeki bir virgül
    silinirse metin bozulur.
    """
    out: list[str] = []
    icerde = False
    kacis = False
    for ch in text:
        if icerde:
            out.append(ch)
            if kacis:
                kacis = False
            elif ch == chr(92):
                kacis = True
            elif ch == '"':
                icerde = False
            continue
        if ch == '"':
            icerde = True
            out.append(ch)
            continue
        if ch in "}]":
            # Geriye doğru boşlukları atla; virgül varsa at.
            i = len(out) - 1
            while i >= 0 and out[i].isspace():
                i -= 1
            if i >= 0 and out[i] == ",":
                del out[i]
        out.append(ch)
    return "".join(out)


def _salvage_truncated(text: str) -> dict[str, Any] | None:
    """Yarıda kesilmiş bir JSON nesnesinden kurtarılabileni çıkarır.

    **Neden var:** model bağlamı 4096 token ve istem ~1500 token. Model
    olağandışı uzun bir yanıt yazdığında (ölçüldü: 6200 karakter) çıktı
    ortasında kesiliyor ve `extract_json` hiçbir şey döndüremiyordu — yani
    **tamamen geçerli olan özet ve ilk bulgular da çöpe gidiyordu.** Ölçülen
    sıklık 14 analizde 1; nadir ama kaybedilen şey analizin tamamı.

    Yaptığı tek şey: açık kalan dizeyi ve parantezleri kapatmak, sonra
    ayrıştırmak. **Hiçbir değer uydurmuyor** — yarım kalan son alan
    ayrıştırılamazsa atılıyor. Kurtarılan nesne eksik olabilir; çağıran
    taraf zaten eksik alanı tolere ediyor (`data.get(...)`).
    """
    start = text.find("{")
    if start == -1:
        return None
    govde = text[start:]

    # Dize içinde miyiz, hangi parantezler açık?
    yigin: list[str] = []
    icerde = False
    kacis = False
    son_guvenli = 0          # dize dışında kapanan son elemanın sonu
    for i, ch in enumerate(govde):
        if icerde:
            if kacis:
                kacis = False
            elif ch == "\\":
                kacis = True
            elif ch == '"':
                icerde = False
        elif ch == '"':
            icerde = True
        elif ch in "{[":
            yigin.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if yigin:
                yigin.pop()
            son_guvenli = i + 1
        elif ch == "," and not yigin[1:]:
            son_guvenli = i + 1

    if not yigin and not icerde:
        return None          # kesik değil; asıl ayrıştırıcı zaten denedi

    adaylar = []
    # 1) Olduğu gibi kapat.
    adaylar.append(govde + ('"' if icerde else "") + "".join(reversed(yigin)))
    # 2) Yarım kalan son alanı at, oradan kapat. Yarım bir alan geçerli
    #    ayrıştırılsa bile yanlış veri taşır; atmak uydurmaktan iyidir.
    if son_guvenli:
        kirp = govde[:son_guvenli].rstrip().rstrip(",")
        y2: list[str] = []
        ic2 = False
        kc2 = False
        for ch in kirp:
            if ic2:
                if kc2:
                    kc2 = False
                elif ch == "\\":
                    kc2 = True
                elif ch == '"':
                    ic2 = False
            elif ch == '"':
                ic2 = True
            elif ch in "{[":
                y2.append("}" if ch == "{" else "]")
            elif ch in "}]" and y2:
                y2.pop()
        adaylar.append(kirp + "".join(reversed(y2)))

    for aday in adaylar:
        try:
            parsed = json.loads(aday)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return None


def extract_json(raw: str) -> Any:
    """Metnin içinden ilk geçerli JSON değerini söker.

    **Sözlük dönmesi garanti DEĞİL.** Model üst düzeyde bir dizi
    döndürebiliyor ve `flowai._normalize` bunu bilerek kullanıyor
    (tahsisleri liste olarak veren şema varyantı). Çağıran taraf sözlük
    bekliyorsa kendisi kontrol etmek zorunda — imza sözlük derken
    `analyze()` doğrudan `data.get()` çağırıyordu ve liste gelen bir yanıt
    yakalanmayan `AttributeError` ile analiz döngüsünü düşürürdü.

    Küçük modeller sık sık ```json bloğu veya "İşte analiz:" gibi bir önsöz
    ekler; bunları temizlemeden parse etmek üretimde en sık kırılan yer.
    """
    text = raw.strip()
    if not text:
        raise ValueError("model boş yanıt döndürdü")
    text = _collapse_arithmetic(text)
    # Fazla virgül ayrıştırmayı belgenin ORTASINDA düşürüyor; kesilme
    # kurtarması oraya yetişemiyor. Ölçüldü: 25 analizin 1'i bu yüzden
    # tamamen kayboluyordu.
    text = _strip_trailing_commas(text)

    # Kod bloklarını sırayla dene. Tek blok yakalayıp `text`'in üstüne yazmak
    # üretimde kırıldı: model bozuk bir blok açıp (```ple ...) ardından doğru
    # ```json bloğunu verdiğinde, ilk eşleşme araya giren düzyazıyı yakalıyor
    # ve gerçek JSON tamamen kayboluyordu. Adaylar denenir, hiçbiri tutmazsa
    # **ham metne** dönülür — arama alanı asla daraltılmaz.
    for block in re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        candidate = block.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = _scan_object(candidate)
        if isinstance(parsed, dict):
            return parsed

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    found = _scan_object(text)
    if found is not None:
        return found

    kurtarilan = _salvage_truncated(text)
    if kurtarilan is not None:
        log.warning("JSON yarıda kesilmişti; %d alan kurtarıldı",
                    len(kurtarilan))
        return kurtarilan

    start = text.find("{")
    if start == -1:
        raise ValueError(f"yanıtta JSON bulunamadı: {raw[:200]!r}")

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:idx + 1])
    raise ValueError(f"JSON tamamlanmamış: {raw[:200]!r}")


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg
        self.model = cfg.ollama_model or cfg.model
        self._client = httpx.AsyncClient(
            base_url=(cfg.base_url or cfg.ollama_base_url).rstrip("/"),
            timeout=httpx.Timeout(cfg.timeout_seconds, connect=5.0),
        )

    async def complete(self, system: str, prompt: str,
                       json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {
                "temperature": self.cfg.temperature,
                "num_ctx": 8192,
            },
        }
        if json_mode:
            payload["format"] = "json"

        try:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama çağrısı başarısız: {exc}") from exc

        data = resp.json()
        return (data.get("message") or {}).get("content", "")

    async def health(self) -> dict[str, Any]:
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"provider": self.name, "ok": False, "model": self.model,
                    "error": str(exc)}

        models = [m.get("name", "") for m in resp.json().get("models", [])]
        # "phi4-mini" istendiğinde "phi4-mini:latest" de kabul edilmeli
        present = any(m == self.model or m.startswith(f"{self.model}:")
                      for m in models)
        return {
            "provider": self.name,
            "ok": present,
            "model": self.model,
            "available_models": models,
            "error": None if present else
                     f"model '{self.model}' indirilmemiş — `ollama pull {self.model}`",
        }

    async def aclose(self) -> None:
        await self._client.aclose()


class MockProvider(LLMProvider):
    """Model yokken deterministik, işe yarar bir çıktı üretir.

    Amaç sahte zeka değil: boru hattının (snapshot -> analiz -> rapor -> UI)
    model olmadan da uçtan uca test edilebilmesi.
    """

    name = "mock"
    model = "rule-based-stub"

    async def complete(self, system: str, prompt: str,
                       json_mode: bool = False) -> str:
        return json.dumps(self._analyze(prompt), ensure_ascii=False)

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True, "model": self.model,
                "error": None,
                "note": "Yerel model bulunamadı; kural tabanlı yedek kullanılıyor."}

    def _analyze(self, prompt: str) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        try:
            snapshot = extract_json(prompt)
        except ValueError:
            pass

        link = snapshot.get("link", {})
        down = float(link.get("down_utilization", 0) or 0)
        up = float(link.get("up_utilization", 0) or 0)
        rtt = float(link.get("avg_rtt_ms", 0) or 0)

        findings: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []

        if down >= 0.8:
            findings.append({
                "title": "İndirme hattı tıkanmış",
                "severity": "high" if down >= 0.94 else "medium",
                "evidence": f"down_utilization={down:.2f}",
            })
            recommendations.append({
                "action": "defer",
                "target": "bulk",
                "reason": "Toplu transferleri yoğunluk dışına kaydır.",
                "confidence": 0.8,
            })
        if up >= 0.8:
            findings.append({
                "title": "Yükleme hattı dar boğaz",
                "severity": "medium",
                "evidence": f"up_utilization={up:.2f}",
            })
        if rtt > 120:
            findings.append({
                "title": "Gecikme yükseldi",
                "severity": "medium",
                "evidence": f"avg_rtt_ms={rtt:.0f}",
            })
            recommendations.append({
                "action": "prioritize",
                "target": "realtime",
                "reason": "Gecikmeye duyarlı trafiği koru.",
                "confidence": 0.75,
            })

        if not findings:
            summary = (f"Ağ sağlıklı. İndirme %{down * 100:.0f}, yükleme "
                       f"%{up * 100:.0f} doluluk, RTT {rtt:.0f} ms.")
        else:
            summary = (f"{len(findings)} bulgu var; indirme %{down * 100:.0f}, "
                       f"yükleme %{up * 100:.0f} doluluk, RTT {rtt:.0f} ms.")

        return {
            "summary": summary,
            "health_score": max(0, min(100, int(100 - down * 45 - up * 25
                                               - min(rtt, 300) / 10))),
            "findings": findings,
            "recommendations": recommendations,
        }


async def _build(kind: str, cfg: AIConfig) -> LLMProvider:
    if kind == "foundry":
        # Döngüsel içe aktarmayı önlemek için burada import ediliyor.
        from .foundry import FoundryLocalProvider
        return FoundryLocalProvider(cfg)
    if kind == "ollama":
        return OllamaProvider(cfg)
    return MockProvider()


async def create_provider(cfg: AIConfig) -> LLMProvider:
    """İstenen sağlayıcıyı kurar; `auto` sırayla Foundry → Ollama → mock dener.

    Sağlayıcı seçimi hiçbir zaman sistemi durdurmaz: hiçbiri hazır değilse mock
    devreye girer ve kural motoru çalışmaya devam eder.
    """
    choice = (cfg.provider or "auto").lower()

    if choice == "mock":
        return MockProvider()

    if choice in ("foundry", "ollama"):
        provider = await _build(choice, cfg)
        try:
            status = await provider.health()
        except LLMUnavailable as exc:
            status = {"ok": False, "error": str(exc)}
        if not status.get("ok"):
            log.warning("%s sağlıklı değil ama provider=%s zorlandı: %s",
                        choice, choice, status.get("error"))
        return provider

    for kind in ("foundry", "ollama"):
        provider = await _build(kind, cfg)
        try:
            status = await provider.health()
        except LLMUnavailable as exc:
            status = {"ok": False, "error": str(exc)}
        if status.get("ok"):
            log.info("Yerel model hazır: %s / %s", kind, provider.model)
            return provider
        log.info("%s kullanılamıyor: %s", kind, status.get("error"))
        await provider.aclose()

    log.warning("Hiçbir yerel model hazır değil — mock sağlayıcıya düşülüyor.")
    return MockProvider()
