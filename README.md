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
- **Topoloji üreteci** — tohumlu, rastgele ama gerçekçi ağlar; sistem elle
  yazılmış tek bir topolojiye bağlı değil
- **İnfaz katmanı** — planı cihazdan bağımsız politikalara, onları `tc` /
  `New-NetQosPolicy` komutlarına çevirir; farkı uzlaştırır
- Yerel LLM analisti (Foundry Local / Ollama / kural tabanlı yedek)
- Canlı panel, simülasyon ortamı, 6 tetiklenebilir senaryo

**Sonraki:** canlı yakalama (scapy) → akıllı firewall → honeypot/deception →
endpoint agent'ları.

> ⚠️ İnfaz **gölge modda**. Komutlar üretilir ve panelde gösterilir, hiçbiri
> çalıştırılmaz. Canlı mod bilerek bağlanmadı: üzerinde doğrulama
> yapabileceğimiz gerçek bir cihaz yok ve sınanmamış çalıştırma kodu "hazır"
> görünür. Komut *metni* teste karşı doğrulandı, komutun cihazdaki *davranışı*
> doğrulanmadı.

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
     │              │              │
     │        ┌─────▼──────┐       │  politika -> sürücü -> uzlaştırma
     │        │  Enforce   │       │  (gölge modda: komut üretir,
     │        │ (politika) │       │   çalıştırmaz)
     │        └─────┬──────┘       │
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

**Optimizasyonun değeri çıkış sayısından geliyor.** Tek çıkışlı bir ağda
çözücünün yapabileceği tek şey paylaştırmak: birinden alıp ötekine vermek,
toplam sabit kalır. Birden çok çıkış olunca boşta duran bacak kullanılıyor ve
kazanç gerçek oluyor. Ölçüldü (220 Mbps talep, 100 Mbps'lik bacaklar):

```
tek hat  ->  100 Mbps geçiyor  (%45)   srv-yedek 4 Mbps'e düşüyor ki ws'ler geçsin
iki hat  ->  200 Mbps geçiyor  (%91)   = x2.00, kimse kısılmadan
```

12 rastgele mimaride (9–17 düğüm, 1–5 site, 1–4 çıkış) ortalama **x1.34**
kazanç ölçüldü; en yükseği x1.63. Tek çıkışlı olanlar dürüstçe x1.00 —
orada kazanılacak bir şey yok.

**Topoloji elle yazılmak zorunda değil.** `Topology.generate(seed, sites,
egresses)` tohumlu, gerçekçi bir ağ üretiyor: uçlarda LAN'lar, aralarında
dağıtım router'ları, çekirdekte kesişim, birden çok WAN çıkışı. Elle yazılmış
tek bir topolojide çalışmak hiçbir şey kanıtlamıyor — sistemin işi *ne bulursa
onda* çalışmak. Çözücü, çevirici ve infaz katmanlarının hiçbiri düğüm adı
varsaymıyor.

**Hat kapasitesinin tek kaynağı topolojidir.** Panel doluluğu `link:`
ayarından, çözücünün darboğazı topolojiden hesaplanıyordu; ikisi elle
tutulduğu için bir kez ayrıştılar ve panel "%92 dolu" derken çözücü "darboğaz
yok" dedi. Artık `link:` topolojiden türetiliyor — kuralı yorumla korumak
yerine yapıyla koruyoruz.

**Eşik denetimi ile optimizasyon ayrı işlerdir.** `optimizer.py` bir
termostattır: doluluk eşiği aşınca politika taslağı üretir. `flowopt.py` ise
gerçek bir optimizasyon yapar — talepleri topolojinin kenarlarına dağıtan çok
mallı akış problemini doğrusal programla çözer ve "hangi trafik hangi çıkıştan,
kimden ne kadar geri çekilmeli" sorusunu cevaplar. Sınıf öncelikleri katı
uygulanır, ama her sınıfın kapasiteden bir asgari garantisi vardır — yoksa en
düşük öncelikli sınıf (DNS, keepalive) tamamen aç kalıyordu.

**Kapsam başına ayrı sürücü.** Kısıt ağın iki farklı yerinde uygulanıyor ve
oralar aynı dili konuşmuyor: çekirdekteki router `tc` + `ip rule`, uçtaki
Windows domain `New-NetQosPolicy`. Her kural `scope` alanını taşıyor ve
uzlaştırıcı onu doğru sürücüye yolluyor (`enforce.core_driver` /
`enforce.edge_driver`). Tek sürücü seçseydik ya uçtaki damgalar ya
çekirdekteki indirme kısıtları sessizce düşerdi.

**Kısan her kural operatör onayı bekler, damgalar beklemez.** Hız tavanı ve
yol ataması birinin bandını daraltır; yanlışsa zararı vardır, o yüzden
`applied` bayrağı olmadan cihaza inmez. DSCP damgası kimseyi kısmaz, yalnız
trafiği tanıtır — en kötü ihtimalle yol üstündeki cihaz onu yok sayar. Aynı
kapıdan geçirmek, zararsız olanı da gereksiz yere bekletirdi.

**İnfaz sil-kur değil, fark uygular.** Çözücü 15 saniyede bir yeni plan
üretiyor; her turda tüm kuralları silip yeniden kursaydık (a) sil ile kur
arasındaki boşlukta tıkanma anında vana tamamen açılırdı, (b) değişmeyen
kural için de cihaza komut giderdi. Her kuralın iki kimliği var: `key`
(kimlik) ve `fingerprint` (kimlik + değer). Fark ikisinin karşılaştırmasından
çıkıyor ve tavan 0.1 Mbps'e yuvarlanıyor, yoksa ölçüm gürültüsü her turda
"değişti" derdi.

**Yapılamayan sessizce atlanmaz.** Windows QoS indirmeyi kısamaz (veri sana
ulaştıysa dar boğazı çoktan geçmiştir) ve yol seçemez (Windows yalnız hedefe
göre yönlendirir). Sürücü bunlara yaklaşık bir komut üretmek yerine gerekçesi
yazılı bir "atlandı" kaydı bırakıyor; kural aktif sayılmıyor ve bir sonraki
turda yeniden deneniyor.

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
| `GET /api/enforce/state` | İnfaz durumu: sürücü, mod, kurulu kurallar, son uzlaştırma |
| `GET /api/enforce/policies` | Son plandan çıkan **istenen** politika kümesi |
| `GET /api/enforce/preview` | Kuru çalıştırma: hepsi onaylı olsa hangi komutlar çıkardı |
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
- [x] **İnfaz katmanı** — politika nesneleri + `tc`/`New-NetQosPolicy`
      sürücüleri + fark uzlaştırıcı. Gölge modda; canlı çalıştırma gerçek
      cihaz üzerinde doğrulanana kadar bağlanmayacak.
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
