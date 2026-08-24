# netpilot

Yerel bir LLM ile çalışan ağ yönetim çekirdeği. Trafiği toplar, ölçer, tıkanmayı
tespit eder, gerekçeli QoS politikaları üretir; AI analisti bu tabloyu okuyup
özet, bulgu ve öneri çıkarır.

> **Paket adı `ntc`, repo adı `netpilot`.** Kasıtlı: içeride onlarca `from ntc…`
> import var, yeniden adlandırmak gereksiz kırılganlık.

**Bu sürümde:**
- Trafik toplama, metrikler (WAN ve LAN ayrı ölçülür)
- Eşik tabanlı kural motoru — tıkanma tespiti, QoS politika taslakları
- **Akış optimize edici** — çok mallı akış problemini doğrusal programla
  çözer: hangi trafik hangi çıkıştan, kimden ne kadar geri çekilmeli
- Yerel LLM analisti (Foundry Local / Ollama / kural tabanlı yedek)
- Canlı panel, simülasyon ortamı, 6 tetiklenebilir senaryo

**Sonraki:** infaz katmanı (üretilen planı gerçekten uygulamak) → akıllı
firewall → honeypot/deception → endpoint agent'ları.

> ⚠️ Üretilen politikalar şu an **uygulanmıyor**. Sistem ölçüyor, hesaplıyor ve
> öneriyor; ağa dokunan bir modül henüz yok.

---

## Hızlı başlangıç

```bash
pip install -r requirements.txt

python -m ntc doctor      # ortam ve model kontrolü
python -m ntc serve       # panel: http://127.0.0.1:8080
```

### Yerel modeli bağlama

Sağlayıcı zinciri: **Foundry Local → Ollama → mock**. Hiçbiri hazır değilse
sistem kural tabanlı yedekle çalışmaya devam eder, durmaz.

**Foundry Local (tercih edilen — Microsoft yığını):**

```powershell
winget install Microsoft.FoundryLocal
foundry model download phi-4-mini
python -m ntc doctor        # LLM  foundry / phi-4-mini
```

Foundry Local modeli ONNX Runtime üzerinde koşturur ve donanıma uygun varyantı
(CPU / GPU / NPU) kendi seçer. Servis ucu dinamik porttadır; sağlayıcı bunu
`foundry server status -o json` çıktısındaki `webUrls` alanından keşfeder.
Sabitlemek istersen `config.yaml` içindeki `ai.base_url` alanına yaz.

> **Not:** Foundry Local bir MSIX paketidir. Makinede sideloading kapalıysa
> (`HKLM\SOFTWARE\Policies\Microsoft\Windows\Appx\AllowAllTrustedApps = 0`)
> kurulum `0x80073cff` ile başarısız olur. Değeri yönetici olarak `1` yapmak
> yeterlidir — **eğer** onu yazan bir yönetim politikası yoksa. Cihaz Intune /
> MDM kaydındaysa politika her senkronda geri yazılır; sahibini
> `HKLM\SOFTWARE\Microsoft\PolicyManager\current\device\ApplicationManagement`
> altındaki `AllowAllTrustedApps_WinningProvider` değerinden görebilirsin.
> O durumda ya kayıt kaldırılmalı ya da Ollama yedeğiyle devam edilmeli.

**Ollama (geliştirme yedeği):**

```powershell
ollama pull phi4-mini
```

Model adlandırması iki çalıştırıcıda farklı (`phi-4-mini` ↔ `phi4-mini`), bu
yüzden config'te ayrı alanlar var: `ai.model` ve `ai.ollama_model`.

### Diğer komutlar

```bash
python -m ntc watch --scenario congestion   # terminalde canlı tablo
python -m ntc analyze                       # tek seferlik AI analizi
python -m ntc analyze --json                # makine okunur çıktı
python -m ntc ask "en çok bandı kim yiyor?"
```

---

## Mimari

```
              ┌──────────────┐
              │  Simulator   │  sentetik ama gerçekçi akışlar
              │  (→ Live)    │  faz 2'de scapy ile canlı yakalama
              └──────┬───────┘
                     │ Flow[]
              ┌──────▼───────┐
              │   Metrics    │  kayan pencere: doluluk, sınıf/cihaz dağılımı
              └──────┬───────┘
     ┌──────────────┼──────────────┐
┌────▼─────┐  ┌─────▼──────┐  ┌────▼──────┐
│Optimizer │  │  FlowOpt   │  │AI Analyst │  yerel LLM
│(eşikler) │  │ (LP çözücü)│  │ (bağlam)  │  Foundry Local
└────┬─────┘  └─────┬──────┘  └────┬──────┘
     └──────────────┼──────────────┘
              ┌──────▼───────┐
              │  Controller  │  olay yolu + kalıcılık
              └──────┬───────┘
              ┌──────▼───────┐
              │  API + Panel │  FastAPI, WebSocket, canlı dashboard
              └──────────────┘
```

### Tasarım kararları

**Kararların iskeleti kuraldan, bağlamı AI'dan gelir.** Uygulanabilir her
politika `traffic/optimizer.py` içindeki ölçülebilir eşiklerden çıkar. Model
çökse, yavaşlasa veya saçmalasa da sistem doğru çalışmaya devam eder. AI katmanı
üstüne özet, bulgu ve öneri ekler.

**Eşik denetimi ile optimizasyon ayrı işlerdir.** `optimizer.py` bir
termostattır: doluluk eşiği aşınca politika taslağı üretir. `flowopt.py` ise
gerçek bir optimizasyon yapar — talepleri topolojinin kenarlarına dağıtan çok
mallı akış problemini doğrusal programla çözer ve "hangi trafik hangi çıkıştan,
kimden ne kadar geri çekilmeli" sorusunu cevaplar. Sınıf öncelikleri katı
uygulanır, ama her sınıfın kapasiteden bir asgari garantisi vardır — yoksa en
düşük öncelikli sınıf (DNS, keepalive) tamamen aç kalıyordu.

**AI önerileri otomatik uygulanmaz.** LLM'den gelen aksiyonlar `source="ai"`,
`applied=False` olarak üretilir ve operatörün onayını bekler. Halüsinasyon ağa
dokunamaz.

**WAN ve LAN ayrı ölçülür.** Kameranın NVR'a akıttığı veya yedeğin LAN dosya
sunucusuna yazdığı trafik anahtarda kalır — internet hattının doluluğuna
sayılmaz. Karıştırmak, LAN'a yedek yazan bir sunucuyu "hattı tıkıyor" diye
yanlışlıkla sınırlandırmaya yol açar.

**Her akış diske yazılmaz.** Saniyede onlarca akış üretiliyor; hepsini saklamak
günde milyonlarca satır demek. Diske giden: metrik zaman serisi, uyarılar,
aksiyonlar, AI raporları ve *dikkat çekici* akışlar (senaryo etiketli, LAN içi
tarama, bilinmeyen uygulama).

**Uyarılar soğuma süresiyle bastırılır.** Kural motoru saniyeler arayla çalışır;
süregelen bir tıkanma aksi halde akışı doldurup gerçek yeni olayları görünmez
kılar.

---

## Simülasyon senaryoları

Panelden tek tıkla veya API ile tetiklenir:

| Senaryo | Ne yapar |
|---|---|
| `congestion` | Tüm ağın yükünü ~2.6× artırır, RTT ve retransmit şişer |
| `bandwidth_hog` | Tek cihaz büyük bir indirmeyle hattı doldurur |
| `port_scan` | LAN içinde çok sayıda porta SYN taraması |
| `exfil` | Alışılmadık hedefe büyük dışarı yükleme |
| `beacon` | C2 tarzı düzenli aralıklı küçük çağrılar |
| `quiet` | Sessiz saat — yükü %15'e düşürür |

```bash
curl -X POST http://127.0.0.1:8080/api/sim/scenario \
  -H "Content-Type: application/json" \
  -d '{"name":"bandwidth_hog","device":"tv-lobby","duration":90,"params":{"mbps":180}}'
```

---

## API

| Uç nokta | Açıklama |
|---|---|
| `GET /api/status` | Genel durum, hat özeti, AI sağlığı |
| `GET /api/devices` | Cihaz listesi + davranış sinyalleri + aktif politikalar |
| `GET /api/metrics/live` | Anlık pencere metrikleri, sınıf payları, en çok trafik |
| `GET /api/metrics/history?seconds=` | Zaman serisi |
| `GET /api/flows/notable` | Kaydedilmiş dikkat çekici akışlar |
| `GET /api/alerts` · `GET /api/actions` | Uyarılar / politikalar |
| `POST /api/actions/{id}/apply` · `/revert` | Politika onayı / kaldırma |
| `GET /api/ai/report` · `POST /api/ai/analyze` | AI analizi |
| `POST /api/ai/ask` | Serbest metin soru-cevap |
| `GET /api/ai/snapshot` | Modele giden ham bağlam (şeffaflık/hata ayıklama) |
| `GET /api/flow/plan` | Son akış çözümü: tahsisler, geri çekmeler, darboğazlar |
| `POST /api/flow/solve` | Beklemeden yeniden çöz |
| `GET /api/flow/topology` | Topoloji grafiği (düğümler + kenarlar) |
| `POST /api/sim/scenario` · `DELETE /api/sim/scenarios` | Senaryo tetikle / temizle |
| `WS /ws` | Canlı metrik / uyarı / aksiyon / rapor akışı |

---

## Yapılandırma

`config.yaml` — her alanın koddaki bir varsayılanı var. Ortam değişkeniyle de
ezilebilir:

```bash
NTC_AI__MODEL=llama3.2  NTC_API__PORT=9000  python -m ntc serve
```

---

## Yol haritası

- [x] **Faz 1 — Trafik izleme + kural motoru**
- [x] **Akış optimizasyonu** — topoloji modeli + çok mallı akış çözücüsü
- [ ] **İnfaz katmanı** — üretilen planı gerçekten uygulamak
      (`Set-NetQosPolicy`, önce gölge modda)
- [ ] **Faz 2 — Canlı mod:** scapy ile gerçek arayüz yakalama; `LiveSource`
      simülatörün yerine aynı arayüzden geçer
- [ ] **Faz 3 — Akıllı firewall:** kural motoru + LLM'in trafik bağlamına bakıp
      dinamik kural üretmesi; kurallar önce "gölge modda" değerlendirilir
- [ ] **Faz 4 — Honeypot + deception:** sahte servisler, tarama yapanları
      yakalama, API davranışını yoklayanlara tutarlı sahte HTTP yanıtları
- [ ] **Faz 5 — Endpoint agent'ları:** cihazlara dağıtılan ajanlar, süreç ve
      bağlantı telemetrisi, merkezi komuta

---

## Kapsam ve sorumluluk

Bu araç, **yönetim yetkisine sahip olduğun** ağlar için tasarlandı. Faz 2 ile
gerçek trafik yakalama ve firewall kuralı yazma devreye girdiğinde, çalıştırdığın
ağın sahibi ya da yetkilendirilmiş yöneticisi olduğundan emin ol. Varsayılan mod
`simulation`'dır ve hiçbir gerçek arayüze dokunmaz.
