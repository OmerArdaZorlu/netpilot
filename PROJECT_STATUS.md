# netpilot — Proje Durumu

> **Bu dosya oturumlar arası hafızadır.** Yeni bir oturuma başlarken önce burayı
> oku, sonra devam et. Bir iş bitince veya bir karar değişince burayı güncelle.

**Son güncelleme:** 2026-08-26
**Repo:** https://github.com/OmerArdaZorlu/netpilot
**Paket adı:** `ntc` (repo adı `netpilot` ile kasıtlı olarak farklı — içeride
onlarca `from ntc...` import var, değiştirmek gereksiz kırılganlık)

---

## 1. Nerede duruyoruz

**Faz 1 tamamlandı ve çalışıyor:** trafik izleme + optimizasyon çekirdeği.

```
FlowSource → Metrics → (Optimizer ‖ AI Analyst) → Controller → API + Panel
(kaynak: simulator | live)
```

| Modül | Dosya | Durum |
|---|---|---|
| Yapılandırma | `ntc/core/config.py` | ✅ YAML + `NTC_` ortam değişkeni ezmesi |
| Ortak tipler | `ntc/core/models.py` | ✅ Flow, Device, Alert, Action, LinkStats |
| Olay yolu | `ntc/core/bus.py` | ✅ async pub/sub |
| Uygulama/cihaz katalogu | `ntc/traffic/catalog.py` | ✅ 16 uygulama, 10 cihaz profili |
| Akış kaynağı | `ntc/traffic/source.py` | ✅ `FlowSource` protokolü; `mode` kaynağı gerçekten seçiyor, bilinmeyende hata veriyor |
| Canlı kaynak | `ntc/traffic/capture.py` + `live.py` | ✅ Faz 2: yakalama ⋈ bağlantı tablosu; gerçek ağda ölçüldü (süreç çözülme %70). Yansıtma portu ve yönetici hakkı doğrulanmadı |
| Trafik üreteci | `ntc/traffic/simulator.py` | ✅ 6 senaryo tetiklenebilir |
| Metrikler | `ntc/traffic/metrics.py` | ✅ kayan pencere, WAN/LAN ayrı |
| Optimizasyon motoru | `ntc/traffic/optimizer.py` | ✅ 5 kural, politika defteri, uyarı soğutma |
| LLM sağlayıcı | `ntc/ai/provider.py` | ✅ zincir: foundry → ollama → mock |
| Foundry Local | `ntc/ai/foundry.py` | ✅ 2026-08-24'te gerçek serviste doğrulandı (3 kusur çıktı, düzeltildi) |
| AI analisti | `ntc/ai/analyst.py` | ✅ snapshot, analiz, soru-cevap, normalizasyon, hedef + akış üretimi |
| AI akış üretimi | `ntc/ai/flowai.py` | ✅ modelin tahsisi; **20 rastgele mimaride 19'u tam cevap**, kayıp 0.0 Mbps |
| Kalıcılık | `ntc/storage/db.py` | ✅ SQLite, 5 tablo, 24 saat saklama |
| Akış topolojisi | `ntc/traffic/topology.py` | ✅ yönlü kapasiteli grafik + tohumlu rastgele ağ üreteci; `link:` buradan türetiliyor |
| Akış çözücüsü | `ntc/traffic/flowopt.py` | ✅ çok mallı akış LP'si; hedefi artık sabit değil, `flowpolicy` üzerinden geliyor |
| Trafik sınıflandırma | `ntc/traffic/classify.py` | ✅ katmanlı, gölge modda bağlı; **%97.4 (IP'siz) / %98.3 (eksik IP)**, 15.070 akış |
| Talep tahmini | `ntc/traffic/demand.py` | ✅ boş saat tepesi + baskı ayrımı; ölçüm hatası 200 → 0 Mbps |
| Akış politikası | `ntc/traffic/flowpolicy.py` | ✅ çözücünün hedefi + doğrulama kapısı; **10 rastgele mimaride 9/10** |
| İnfaz — politika | `ntc/enforce/policy.py` | ✅ cihazdan bağımsız kural nesneleri + onay köprüsü |
| İnfaz — sürücüler | `ntc/enforce/drivers.py` | ✅ anlat / linux (`tc`) / windows (QoS); **komut metni** doğrulandı, cihaz davranışı doğrulanmadı |
| İnfaz — uzlaştırıcı | `ntc/enforce/engine.py` | ✅ fark uygulama, gölge modu, kapanışta geri alma |
| Orkestrasyon | `ntc/controller.py` | ✅ 5 async döngü |
| API + WebSocket | `ntc/api/server.py` | ✅ |
| Panel | `ntc/dashboard/index.html` | ✅ görsel + erişilebilirlik + renk körlüğü denetiminden geçti (12 kusur düzeltildi); 2026-08-26'da gerçek `serve` üzerinde uçtan uca çalıştırıldı |
| CLI | `ntc/cli.py` | ✅ serve / watch / analyze / ask / doctor |
| Testler | `tests/` (47 dosya) | ✅ depoya alındı; `python tests/kos.py` → 32/32 |

### Çalıştırma

```powershell
pip install -r requirements.txt
python -m ntc doctor      # ortam + model kontrolü
python -m ntc serve       # panel: http://127.0.0.1:8080
```

### Doğrulanmış davranışlar

- `port_scan` senaryosu → 48 port / 71 IP tespiti, HIGH uyarı
- `bandwidth_hog` → hedef cihaza `rate_limit`, hatta `rebalance`
- `exfil` → yükleme hattı doygunluğu yakalanıyor
- Uyarı tekrarı 90 sn soğuma ile bastırılıyor
- Sağlayıcı zinciri: hiçbiri kurulu değilken gerekçeli şekilde mock'a düşüyor
- Renk paleti CVD doğrulayıcısından açık+koyu temada geçti
- Panel canlı (2026-08-26): `/` ve `/api/status` 200, 0 JS hatası, 16/16 kart
  dolu; tıkanma senaryosu doluluğu %27 → %55/%72'ye, talebi 135 → 552 Mbps'e
  taşıdı, 4 darboğaz çıktı ve senaryo bitince söndü
- Foundry Local uçtan uca (2026-08-24): `doctor` → sağlayıcı bağlanıyor,
  `analyze` → gerçek `/v1/chat/completions` yanıtı. Soğuk yol (model bellekte
  değilken) 43 sn, sıcak koşu 25-27 sn. Tembel yükleme ve çıkarım kilidi
  bellekten çıkarma testiyle doğrulandı.
- İnfaz uçtan uca (2026-08-24, linux sürücüsü, tıkanma senaryosu):
  onaysızken 0 kısan kural → operatör bir aksiyonu onaylayınca 1 kural /
  2 `tc` komutu → ikinci turda 0 komut (fark yok) → onay geri alınınca
  1 kaldırma → kapanışta 0 kural kaldı.
- İnfaz birim testleri: `t_enforce.py` 16 senaryo, hepsi geçti.
- **Optimizasyonun kazancı ölçüldü (2026-08-24):** 220 Mbps talep,
  100 Mbps'lik bacaklar → tek hat 100 Mbps (%45), iki hat 200 Mbps (%91),
  **x2.00**. 12 rastgele mimaride (9–17 düğüm, 1–5 site, 1–4 çıkış)
  ortalama **x1.34**, en yüksek x1.63. Tek çıkışlı ağlarda x1.00 — orada
  kazanılacak bir şey yok, çözücü yalnız paylaştırabilir.
  (`t_random_topo.py`, 12 tohum, hepsi geçti.)

---

## 2. Kilitlenmiş kararlar

Bunlar tartışıldı ve karara bağlandı; yeniden açmadan önce sebebini oku.

| Karar | Gerekçe |
|---|---|
| **Python + FastAPI + asyncio** | Paket analizi ve ML ekosistemi en zengin |
| **Simülasyon ortamı** (`mode: simulation`) | Gerçek arayüze dokunmadan geliştirme |
| **AI runtime: Foundry Local** (Phi) | Microsoft yığını; Ollama geliştirme yedeği olarak kalır |
| **Kimlik: Active Directory temelli** | Şu anki `Device.trust` uydurma bir sayı; AD gerçek veri getirir |
| **Firewall: Windows Defender Firewall w/ Advanced Security** | Yerleşik, `NetSecurity` modülü makinede mevcut |
| **QoS: Policy-based QoS** (`Set-NetQosPolicy`) | Mevcut optimizer çıktısı doğrudan buna çevrilebilir |
| **Erişim kontrolü: NPS + 802.1X** | "Cihazı karantinaya al" gerçek aksiyona dönüşür |
| **Aldatma: AD honeytoken** | SPN'li sahte servis hesabı; kerberoast denemesi Event 4769 |
| **Barındırma: IIS** | FastAPI paneli IIS arkasında; açılışta ayakta, sertifika IIS'ten |
| **Repo adı `netpilot`, paket `ntc`** | Kısa, çakışmasız isim; import'lar bozulmasın |

### Mimari ilkeler — bunları bozma

1. **Kararların iskeleti kuraldan çıkar, bağlamı AI'dan gelir.** Uygulanabilir
   her politika `optimizer.py` içindeki ölçülebilir eşiklerden doğar. Model
   çökse de sistem doğru çalışmaya devam eder.
2. **AI önerileri otomatik uygulanmaz.** `source="ai"`, `applied=False` ile
   üretilir, operatör onayı bekler. Halüsinasyon ağa dokunamaz.
3. **WAN ve LAN ayrı ölçülür.** Kamera→NVR ve yedek→dosya sunucusu trafiği
   internet hattı doluluğuna sayılmaz. (Bu bir kez hataya yol açtı: LAN'a yedek
   yazan sunucu "hattı tıkıyor" diye yanlışlıkla sınırlandırılıyordu.)
4. **Her akış diske yazılmaz.** Sadece dikkat çekici olanlar (senaryo etiketli,
   LAN içi tarama, bilinmeyen uygulama) + metrik zaman serisi.
5. **Yeni infaz modülleri önce gölge modda.** Firewall kuralları devre dışı /
   sadece loglayan halde üretilir. Yanlış bir kuralın DC erişimini kesmesi,
   çözdüğü problemden pahalıdır.
6. **AI kritik yolda durabilir mi? Ölçüt sıklık + kimin başlattığı.**
   *(2026-08-22'de düzeltildi; önceki "AI hiçbir zaman yolda olmaz" kaydı fazla
   kaba bir genellemeydi.)* Model senkron durabilir eğer: olay seyrek (dk'da <1),
   **insan başlatmış** (duraklama zaten bekleniyor), zaman aşımı varsayılanı
   tanımlı, ve açılış yolunda gerekmiyor. Ölçüt "güvenlik açısından kritik mi"
   **değil**.
   - ✅ Sığar: cihaz takılması, firewall kuralı *oluşturma*, politika taslağı
   - ❌ Sığmaz: paket yönlendirme, dosya erişimi, süreç başlatma
     (sn'de binlerce, otomatik)

---

## 3. Engeller

### ✅ Foundry Local kurulamıyordu — çözüldü (2026-08-24)

*(Önceki kayıt yanlış teşhis içeriyordu: "registry'yi elle düzelt" yazıyordu,
ama o değeri yazan bir yönetim politikasıydı ve elle değişiklik geri alınırdı.)*

**Gerçek sebep:** cihaz Akdeniz Üniversitesi tenant'ına **Intune/MDM ile
kayıtlıydı** (`WorkplaceJoined: YES`). `AllowAllTrustedApps = 0` değeri elle
yazılmış değil, MDM'in uyguladığı bir CSP politikasıydı — sahibi şurada
görünüyor:

```
HKLM\SOFTWARE\Microsoft\PolicyManager\current\device\ApplicationManagement
  AllowAllTrustedApps_WinningProvider = ECEF570E-...   ("MS DM Server")
```

**Teşhis refleksi:** sideloading engelini görünce önce `Policies\...\Appx`
değerine değil, `PolicyManager\...\ApplicationManagement` altındaki
`_WinningProvider` alanına bak. Orada bir sağlayıcı varsa registry'yi elle
değiştirmek işe yaramaz; senkronda geri yazılır.

**Yapılan:** kullanıcı okul hesabının cihaz kaydını kaldırdı (Ayarlar → İş veya
okul hesabına erişim → Bağlantıyı kes). Sonrasında `WorkplaceJoined: NO`,
politika kalktı, `winget install Microsoft.FoundryLocal` geçti.

> eduroam etkilenmedi: EAP-TTLS üzerinden **SecureW2** ile bağlanıyor, Intune
> sertifikalarını kullanmıyor. Önceden kontrol edildi.

### ⚠️ Foundry sürüm kırılganlığı

CLI 0.10.3'te komut grubu `service` değil **`server`**. Kod `service status`
ayrıştırıyordu ve hiç çalışmamıştı. Sürüm yükseltmelerinde bu yüzey yeniden
kırılabilir — `ntc/ai/foundry.py` içindeki keşif JSON ayrıştırmasına ve metin
regex yedeğine dayanıyor, ikisi de `_parse_status_url` testinde kayıtlı.

### ✅ Foundry CUDA bellek hatası — çözüldü (2026-08-24)

Panel testinde AI analizi düşüyordu:

```
HTTP 500 — onnxruntime::BFCArena::AllocateRawInternal
Failed to allocate memory for requested buffer of size 17461248
```

**Sebep donanım yetersizliği değildi.** Ölçüm sırasında CUDA ve OpenVINO
varyantlarını defalarca yükleyip boşaltmıştım; Foundry daemon'ı bu belleği
geri vermiyor. `nvidia-smi`: **7903 MiB / 8188 MiB kullanımda, 55 MiB boş.**

**Çözüm:** `foundry server stop` → VRAM anında 0 MiB'a düştü → `server start`.
Sonrasında yük altında 4 analiz turu: **3 × 200, 0 × 500.** Gecikme 15.7 sn,
gerçek özet üretildi.

**Teşhis refleksi:** Foundry 500 + `AllocateRawInternal` görürsen önce
`nvidia-smi` ile boş VRAM'e bak; doluysa daemon'ı yeniden başlat. Model
yükleme/boşaltma döngüsü yapan her test seansından sonra bu birikiyor.

**⚠️ Kalan risk — pay dar.** Model yüklüyken GPU yine 7903 MiB'da duruyor
(ONNX Runtime arena'yı baştan büyük alıyor). Başka bir GPU tüketicisi
(oyun, tarayıcı, Epic Games Launcher — testte açıktı) devreye girerse hata
tekrar edebilir. Kritik bir kurulumda ya GPU'yu ayırmak ya OpenVINO
varyantına dönmek gerekir.

### ✅ `extract_json` yıkıcı fence ayrıştırması — düzeltildi (2026-08-24)

Aynı testte ikinci bir hata çıktı, bu sefer bizim kodumuzda:

```
AI analizi başarısız: yanıtta JSON bulunamadı:
'```ple bir JSON formatında sonucun:

```json
{"summary": ...'
```

Model bozuk bir kod bloğu açıp (` ```ple `) ardından doğru ` ```json `
bloğunu vermişti. `extract_json` ilk ` ``` ` çiftini yakalayıp **`text`'in
üstüne yazıyordu**; gerçek JSON'u içeren kısım kayboluyor, süslü parantez
taraması da artık yanlış metinde çalışıyordu.

**Düzeltme:** bloklar sırayla aday olarak deneniyor, hiçbiri tutmazsa **ham
metne** dönülüyor — arama alanı asla daraltılmıyor. Parantez tarayıcısı
`_scan_object` olarak ayrıldı ve iki yerde de kullanılıyor.
`scratchpad/t_json.py`: 14 vaka, üretimde kırılan tam senaryo dahil.

### 🔴 Windows Server lab yok

Kullanıcının makinesi **Windows 11 Home Single Language**, `WORKGROUP`'ta:
- Hyper-V yok, `gpedit.msc` yok, domain'e katılım yok

**Bu yüzden test edilemeyen modüller:** AD kimlik, honeytoken, NPS/802.1X,
GPO ile QoS dağıtımı, RRAS.

Kullanıcı "şimdilik lab yok" dedi. Lab kurulursa seçenekler: VirtualBox +
Server 2025 Evaluation (ücretsiz, 180 gün) / Azure VM / Win11 Pro yükseltme.

**Kural: doğrulayamadığımız modülü "bitti" diye işaretlemiyoruz.**

---

## 4. Sıradaki işler

### Şimdi yapılabilir (bu makinede doğrulanabilir)

0. **İstem düzeltmesi** ← *önerilen başlangıç, artık gerçek modelle ölçülebilir*
   Foundry çalıştığı için `prompts.py` ilk kez gerçek phi-4-mini'ye karşı
   sınanabiliyor ve şu an başarısız (bkz. Teknik borç). Bunu düzeltmeden
   eklenecek her AI özelliği aynı zemine oturur.

1. **Sysmon telemetrisi**
   Event ID 3 (ağ bağlantısı + süreç), 22 (DNS), 1 (süreç oluşumu) okuyup
   simülatörün yerine gerçek akış koymak. Kullanıcının "cihazlara agent"
   maddesinin büyük kısmını karşılıyor ve diğer her modülü gerçek veriyle
   besliyor. Domain gerektirmiyor.

2. **Defender Firewall entegrasyonu, gölge modda**
   `NetSecurity` modülü mevcut (v2.0.0.0). Kurallar devre dışı/log-only üretilir.

3. **Paketleme: IIS + Windows Service + Event Log + PowerShell modülü**
   IIS Home sürümünde özellik olarak eklenebilir. Event Log'a kendi uyarılarımızı
   yazmak, mevcut izleme araçlarının bizi görmesini sağlar.
   PowerShell modülü: `Get-NtcStatus`, `Get-NtcDevice`, `Invoke-NtcAnalysis`.

### Yazıldı ve doğrulandı

4. **✅ Akış optimizasyonu — çok mallı akış çözücüsü** *(2026-08-24)*

   *Kullanıcının "optimizasyon"dan kastı buydu ve uzun süre yanlış anlaşıldı:
   Faz 1'deki `optimizer.py` bir **eşik denetçisi** (doluluk %80'i geçti → not
   yaz), optimizasyon değil. Asıl istenen, ölçülen taleplerin ağ üzerinde en
   iyi nasıl dağıtılacağını **hesaplamak**.*

   | Dosya | Ne yapıyor |
   |---|---|
   | `ntc/traffic/topology.py` | Yönlü kapasiteli grafik. Kenar başına kapasite / gecikme / maliyet / sağlık. İndirme ve yükleme ayrı kenar (asimetri gerçek) |
   | `ntc/traffic/flowopt.py` | Çok mallı akış problemini doğrusal program olarak kurup `scipy` HiGHS ile çözer |

   **Politika: önce öncelik, sonra adalet.** Sınıflar sırayla çözülüyor
   (realtime → … → background), yüksek öncelikli çözüm sonrakiler için kısıt
   olarak sabitleniyor. Her sınıf içinde iki aşama: (1) en kötü durumdaki
   akışın karşılanma oranını maksimize et — tek akışın aç kalmasını engeller,
   (2) o oran taban iken toplamı maksimize et.

   **Çıktı:** akış başına verilen hız + kenar kullanımı, cihaz başına
   "şu kadarını geri çek" listesi, doymuş kenarlar (darboğaz).

   **Doğrulama:** 9 senaryo, cevapları elle hesaplanabilir —
   `scratchpad/t_flowopt.py`. Darboğaz paylaşımı, sınıf önceliği, çok kenara
   bölme, sayaçlı hattan kaçınma, LAN/WAN ayrımı, bozuk hattan kaçınma,
   ulaşılamaz hedef, asgari garanti, yanlış etiketlenmiş dev akış.
   Gerçek simüle trafikte de koşturuldu: 425 Mbps talep /
   350 Mbps kapasite → üç WAN çıkışı da doldu, realtime-interactive-streaming
   %100, bulk %47, background %0.

   **✅ Asgari garanti eklendi.** Katı öncelik en alt sınıfı aç bırakıyordu
   (`background` %0). Çözüm iki turlu: 1. turda her sınıf yalnız **tabanı**
   kadar talep edebiliyor, 2. turda kalan kapasite katı önceliğe göre
   dağıtılıyor.

   *İlk denemem yanlıştı ve ölçümle yakalandı:* taban **talebin** oranıydı,
   talep büyüyünce tabanlar da büyüyor ve üst sınıfların tabanları kapasiteyi
   bitiriyordu — 1245 Mbps talep / 350 kapasite ile `background` yine %0
   çıktı. Taban artık **kapasitenin** dilimi (`CLASS_FLOOR_SHARE`, toplam
   %38) ve taban turu küçükten büyüğe işleniyor: büyük bir sınıfın tabanı,
   küçük ama hayati bir sınıfın tabanını yiyemiyor.

   Aynı baskı altında sonuç: realtime %100, interactive %76, streaming %6,
   bulk %5, **background %100**.

   **⚠️ Tam sözlüksel max-min adalet değil.** Sınıf içinde tek turlu
   yaklaşım; ikinci aşama birinci aşamada eşitlenmiş akışlardan bazılarını
   daha fazla besleyebilir. Gerçek adalet turlu darboğaz sabitlemesi ister.

   **✅ Sisteme bağlandı.** `controller.py`'a beşinci döngü olarak
   (`_flow_loop`, 15 sn) eklendi. LP saf CPU işi olduğu için
   `asyncio.to_thread` ile ayrı iş parçacığında koşuyor — olay döngüsünü
   kilitlemiyor.

   | Uç | Ne döner |
   |---|---|
   | `GET /api/status` → `flow` | özet: talep, verilen, geri çekme sayısı, darboğazlar |
   | `GET /api/flow/plan` | son çözümün tamamı |
   | `POST /api/flow/solve` | beklemeden yeniden çöz |
   | `GET /api/flow/topology` | grafik (18 kenar, 8 düğüm) |

   Ayarlar `config.yaml` → `flow:` (enabled / interval_seconds /
   min_pullback_mbps) ve isteğe bağlı `topology:` bloğu; blok yoksa
   `link:` kapasitelerinden varsayılan topoloji kuruluyor.

   **✅ Panele bağlandı** (2026-08-24). `index.html` içinde "Akış planı"
   kartı: çıkış diyagramı (SVG, kapasite ↔ bant yüksekliği, yük ↔ bağlantı
   kalınlığı, doluluk ↔ renk), sınıf başına karşılanma çubukları, geri çekme
   listesi, doymuş kenar rozetleri, "Şimdi hesapla" düğmesi. Mevcut tasarım
   sistemi kullanıldı (`.meter`, `.classrow`, `.feed`, `utilColor`).

   **⚠️ Varsayılan topoloji hatası — düzeltildi.** İlk sürüm varsayılan olarak
   üç WAN çıkışı kuruyordu (fiber 200 + yedek 100 + LTE 50 = 350 Mbps), oysa
   `link:` tek bir 200 Mbps hat tanımlıyor. Sonuç: gerçek hat %92 doluyken
   panel "darboğaz yok" diyordu — **panel ile ölçüm birbirini yalanlıyordu.**
   Varsayılan artık `link:` ile birebir tek çıkış; çoklu çıkış `topology:`
   bloğuyla açıkça tanımlanıyor (`config.yaml`'da örnek yorum olarak var).

   **Kural: varsayılan topoloji yapılandırmadaki kapasiteyi aşmaz.**

   Doğrulama (tıkanma senaryosu, headless Edge ile ekran görüntüsü):
   ölçüm 210.1 / 200 Mbps → çözücü tam 200 veriyor, 2 darboğaz, 10.1 Mbps'i
   üç cihazdan geri çekiyor. Sıkışma yalnız `bulk` sınıfına yansıyor
   (%90), gerçek zamanlı ve arka plan %100.

   **Hâlâ eksik:** `Flow`'a yol alanı eklenmedi, uygulayan infaz katmanı yok.

   **⚠️ Windows kısıtı yerinde duruyor:** RRAS politika tabanlı yönlendirme
   yapamıyor. Çözücünün ürettiği yol kararı saf Windows'ta doğrudan
   uygulanamaz — (a) hedefe göre statik rota + arayüz metriği, ya da
   (b) DSCP ile işaretleyip yol seçimini gerçek router/SD-WAN'a bırakmak.
   **Uygulama-farkında yönlendirmeyi saf Windows'ta vaat etme.**

4b. **✅ İnfaz katmanı — gölge modda yazıldı ve doğrulandı** *(2026-08-24)*

   Çözücünün *hedef durumunu* cihaz komutuna indiren üç katman. Ayrı
   durmalarının sebebi aynı kararın iki dünyada tamamen farklı görünmesi:
   `RateLimit(cam-entrance, down, 45)` Linux'ta bir `tc class`, Windows'ta
   **imkânsız**.

   | Dosya | Ne yapıyor |
   |---|---|
   | `ntc/enforce/policy.py` | **Ne** — cihazdan bağımsız kural nesneleri: `RateLimit`, `PathPin`, `Mark`. `policies_from_plan()` planı bunlara çeviriyor; `approved_keys()` operatör onayını kural anahtarına bağlıyor |
   | `ntc/enforce/drivers.py` | **Nasıl** — `DescribeDriver` (Türkçe anlatır, cihaz gerektirmez), `LinuxTcDriver` (`tc` + `ip rule` + `iptables mangle`), `WindowsQosDriver` (`New-NetQosPolicy`) |
   | `ntc/enforce/engine.py` | **Ne zaman** — `Enforcer.reconcile()`: istenen ile bilinen durumun farkını uygular |

   **Kapsam başına ayrı sürücü.** Ağın iki yerine iki farklı dille yazıyoruz:
   çekirdekteki router `tc` konuşuyor, uçtaki Windows domain
   `New-NetQosPolicy`. `Enforcer` kuralı `scope` alanına göre doğru sürücüye
   yolluyor (`enforce.core_driver` / `enforce.edge_driver`). Tek sürücüyle
   çalışsaydık ya uçtaki damgalar ya çekirdekteki indirme kısıtları sessizce
   düşerdi. Bir kapsamın sürücüsü tanımsızsa kural gerekçesiyle "atlandı"
   oluyor, kurulmuş sayılmıyor. *(Ölçüldü: çekirdek kuralı `tc`, uç kuralı
   `New-NetQosPolicy` üretti; uç sürücüsü yokken 1 atlandı, 0 aktif.)*

   **Kapsam (`scope`) fizikten geliyor, tercihten değil.** `edge` = uç
   makine / erişim anahtarı: yüklemeyi kısabilir, DSCP vurabilir. `core` =
   router kesişimi: indirme kısıtı ve yol seçimi ancak burada anlamlı.
   `scope` "en erken nerede yapılabilir" demek, "yalnız orada" değil —
   router yüklemenin de yukarısında durduğu için `edge` kuralını da
   uygulayabiliyor. *(Bir gün "tutarlılık için" uzlaştırıcıya kapsam filtresi
   eklenirse router'ın yükleme kısıtları sessizce düşer.)*

   **Neden sil-kur değil fark:** çözücü 15 sn'de bir yeni plan üretiyor. Her
   turda hepsini silip kursaydık (a) sil ile kur arasındaki boşlukta, tam
   tıkanma anında vana tamamen açılırdı, (b) değişmeyen kural için de cihaza
   komut giderdi. Her kuralın iki kimliği var: `key` (kimlik) ve
   `fingerprint` (kimlik + değer). Tavan 0.1 Mbps'e yuvarlanıyor — yoksa
   ölçüm gürültüsü her turda "değişti" dedirtirdi. *(Ölçüldü: 30.04 Mbps
   değişimi yok sayıldı.)*

   **Onay kapısı.** Kısan her kural (`rate`, `path`) `applied` bayrağı
   olmadan kurulmuyor; damgalar (`mark`) kurulmadan geçiyor çünkü kimseyi
   kısmıyorlar. Onay geri alınınca kural da kalkıyor — ayrı bir "geri al"
   yoluna gerek yok. *(Ölçüldü: onaysız kısan kural sayısı 0; onaydan sonra
   1 kural + 2 komut; onay geri alınınca 1 kaldırma.)*

   **Yapılamayan sessizce atlanmıyor.** Windows QoS indirmeyi kısamaz ve yol
   seçemez; sürücü yaklaşık komut üretmek yerine gerekçeli `UnsupportedRule`
   fırlatıyor. Atlanan kural **aktif sayılmıyor**, bir sonraki turda yeniden
   deneniyor — "kuruldu" diye yazsaydık sonsuza kadar uygulanmamış kalırdı.

   **443 belirsizliği tahminle doldurulmuyor.** Katalogda beş uygulama 443'te
   ve dördü farklı sınıfta. "443 → interactive" damgası yazmak, tıkanmayı
   yaratan Windows Update'e en yüksek etkileşimli önceliği vermek olurdu.
   `class_selectors()` yalnız **tek sınıfa ait** portları üretiyor; belirsiz
   olanlar ayrı listede duruyor ve uygulama-yolu eşleşmesi isteyen, operatörün
   tamamlayacağı kural olarak işaretleniyor.

   **⛔ Canlı mod bilerek bağlanmadı.** `Enforcer(mode="canli")` bir `runner`
   şart koşuyor ve hiçbir yerde verilmiyor. Üzerinde doğrulama yapabileceğimiz
   cihaz yokken yazılan `subprocess.run` kodu "infaz hazır" sanılırdı. **Komut
   metni** teste karşı doğrulandı; **komutun cihazdaki davranışı** doğrulanmadı.
   Bu ayrımı bulanıklaştırma.

   Kapanışta `rollback()` çağrılıyor — sahipsiz kalan bir hız tavanı en kötü
   arıza biçimi: sebebi görünmez, kimse kaldırmaz.

   **⚠️ Gerçek router işletim sistemi sürücüsü yok.** `LinuxTcDriver` bir
   Linux/VyOS router'a uyuyor; Cisco IOS, MikroTik RouterOS veya switch CLI
   için sürücü yazılmadı. Hangi cihaz olduğu netleşince o sürücü eklenmeli —
   politika ve uzlaştırma katmanları değişmeden kalır, çeviri tablosu
   `drivers.py`'ye bir sınıf olarak girer.

   Doğrulama: `t_enforce.py` 16 senaryo + `t_enforce_scope.py` 9 senaryo,
   hepsi geçti. Uçtan uca çalıştırma
   (linux sürücüsü, tıkanma senaryosu) 5 adımda doğrulandı. API:
   `/api/enforce/state`, `/api/enforce/policies`, `/api/enforce/preview`.
   Panelde "İnfaz" kartı: kurulu kurallar, atlananlar gerekçesiyle, üretilen
   komutlar.

4g. **✅ Topoloji üreteci — sistem elle yazılmış ağa bağlı değil** *(2026-08-24)*

   **Neden:** `Topology.default()` tek hat modelliyordu (daha önce uydurma
   kapasite koyup paneli yalanladığı için öyle kilitlenmişti), ama çoklu çıkış
   hiçbir zaman yapılandırmaya yazılmadı. Sonuç: demo tek hatta koşuyordu ve
   optimize edici hiçbir şeyi hızlandıramıyordu — yalnız paylaştırıyordu.
   Kullanıcı bunu haklı olarak "işe yaramıyor" diye gördü.

   `Topology.generate(seed, sites, egresses, downlink_mbps, uplink_mbps)`
   tohumlu, gerçekçi bir ağ üretiyor:

   ```
   cihazlar ─► access-1 ─► dist-1 ─┐
                                   ├─► core ─┬─► cikis-1 ─► internet
   cihazlar ─► access-2 ─► dist-2 ─┘         └─► cikis-2 ─► internet
   ```

   Aynı tohum aynı ağı veriyor (tekrarlanabilirlik), farklı tohum farklı
   şekil (genellik). `config.yaml` içinde `topology.generate:` bloğu varsayılan;
   gerçek ağ `edges:` ile birebir yazılabiliyor, o yol da korundu.

   **Cihazlar tek anahtara toplanmıyor.** `attach_point()` cihaz adını kararlı
   hash ile erişim düğümlerine dağıtıyor — envanter tablosu tutmadan. Hepsini
   tek anahtara bağlamak, gerçekte var olmayan bir darboğaz uydurmak olurdu.

   **`link:` artık topolojiden türetiliyor** (`wan_capacity()`). Panel doluluğu
   ile çözücünün darboğazının ayrışması bir kez oldu (panel "%92 dolu",
   çözücü "darboğaz yok"); artık kuralı yorumla değil **yapıyla** koruyoruz —
   ayrışması imkânsız.

   Doğrulama: `t_random_topo.py` 12 rastgele mimaride tüm zinciri
   (çözücü → aksiyon → politika → infaz) koşturuyor; hiçbir katman düğüm adı
   varsaymıyor, hiçbiri düşmedi, hiçbir kural atlanmadı.

4h. **✅ AI politika katmanı — modelin sisteme dokunduğu tek yol** *(2026-08-24)*

   **Kullanıcının baştan beri istediği buydu ve ben yanlış kurmuştum.** LP'yi
   "beyin", AI'ı "anlatıcı" sanmıştım. Doğrusu: LP verilen hedefe göre optimal
   çözüyor, ama *hedef* bir olgu değil karar — ve o karar `flowopt.py` içinde
   sabit tablolardaydı (realtime hep bulk'u yener, tabanlar hep aynı, gecikme
   hep paradan baskın). Sabit tablo sabit bir gün ve sabit bir ağ varsayıyordu.

   ```
   durum (ölçüm + saat + hat) ──► AI ──► FlowPolicy ──► LP ──► akış
                                         (hedef)              (sayılar)
   ```

   Dört sabit dışarı çıktı: sınıf sırası, taban payları, yol tercihi ağırlığı,
   gecikme/para/sağlık karışımı.

   **Ölçüm — modelden sayı istemek çalışmıyor.** 4 durum, gerçek phi-4-mini:

   | | sayı isteyen istem | kategorik istem |
   |---|---|---|
   | doğru hedef | **1/4** | **2/4** |
   | ağırlıklara dokundu | 0/4 (hepsinde varsayılanı geri yazdı) | 2/4 |
   | taban kalitesi | %14.6'da eşitledi, 3 sınıfı sıfırladı | profil adı, makul |

   "Sayaçlı hat dikkate alınmalı" diye yazdığı halde para ağırlığını 10'da
   bıraktı — niyeti anlıyor, sayıya çeviremiyor. Aynı kusur %17.5'i "critical"
   demesinde ve bir payı %122 raporlamasında da vardı. Çözüm: sayıyı ondan
   hiç istememek. Model `"yuksek"` diyor, sayıyı `WEIGHT_LEVELS` koyuyor;
   taban için profil adı seçiyor, sayıları `FLOOR_PROFILES` tutuyor.

   **Doğrulama kapısı çalışıyor.** Ölçümde model `"gedemek"` diye bir profil
   uydurdu → reddedildi, mevcut hedef korundu, ağa hiçbir şey gitmedi.
   Reddedilen çıktı gizlenmiyor: `policy_issues` ve `/api/flow/policy`
   üzerinden görünür — yoksa "AI çalışıyor" yanılsaması kalırdı.

   **Reddedilince varsayılana DÖNMÜYORUZ, mevcudu koruyoruz.** Varsayılan daha
   güvenli görünür ama değil: hedefi tam da modelin güvenilmediği anda
   sıfırlamak ağı sallar.

   Canlı doğrulama: AI "akşam, ofis boş, yeniden gönderim yüksek" okudu,
   `bulk > streaming > interactive > realtime` sırası + `yedekleme-penceresi`
   profili + gecikme/sağlık yüksek kurdu; çözücü bulk'a +137.6 Mbps verdi.
   *(Uyarı: iki çözüm arasında talep de büyüdüğü için toplamdaki değişim
   yalnız politikaya atfedilemez; sınıf dağılımındaki kayma atfedilebilir.)*

   ⚠️ **Kalan kusur:** sayaçlı hat durumunda model hâlâ parayı değil gecikmeyi
   yükseltiyor (2/4'ün kaçan yarısı). İstemde "SAYAÇLI" büyük harfle yazılı
   ama görmüyor. Denenecek: daha büyük model, ya da sayaçlı hat varlığını
   ayrı bir alan olarak sormak.

4i. **🔴→✅ Taban ölçeği topolojiye göre bozuktu** *(2026-08-25)*

   Kullanıcı sordu: *"o varsayılanın her farklı topolojide doğru verdiğine
   emin misin?"* — emin değildim ve haklı çıktı.

   `_floors()` bütün tabanları `_egress_capacity()` ile ölçekliyordu; o da
   yalnız `dst == internet` kenarlarını topluyor, **yani sadece yükleme**.
   İndirme kenarları `internet → wan` yönünde olduğu için filtreye hiç
   takılmıyordu.

   | hat | eski background tabanı | doğrusu | kat |
   |---|---|---|---|
   | 200/20 (ev) | 0.4 Mbps | 4.0 Mbps | 10.0x |
   | 300/40 (varsayılan) | 0.8 Mbps | 6.0 Mbps | 7.5x |
   | 1000/100 (kurumsal) | 2.0 Mbps | 20.0 Mbps | 10.0x |
   | **100/100 (simetrik)** | **2.0 Mbps** | **2.0 Mbps** | **1.0x** |

   **Simetrik ağda doğru çalışıyordu, o yüzden fark edilmemişti.** Test
   topolojilerinin çoğu simetrikti; asimetri gerçek internet hatlarının
   tanımlayıcı özelliği ve hata tam orada ortaya çıkıyordu.

   En kötü tarafı: bozulan şey, "en düşük öncelikli sınıf asla aç kalmasın"
   diye özellikle konmuş olan taban. DNS/keepalive sınıfı pratikte açtı.

   LAN talepleri daha da kötüydü — hedefleri internet bile değilken internet
   çıkış kapasitesine göre ölçekleniyorlardı.

   Düzeltme: `_capacity_for(direction, lan_dst)` — taban ölçeği yöne göre
   (indirme / yükleme / LAN hedefi). `_floors()` yön başına ayrı bütçe
   kuruyor. `t_floors.py` beş farklı hat oranında doğruluyor.

4j. **✅ AI akışı DOĞRUDAN kuruyor — `AI → ... → flow`** *(2026-08-25)*

   Kullanıcı defalarca söyledi, ben defalarca yanlış kurdum: *"aradaki yol
   önemsiz ama AI → ... → flow, böyle OLACAK."* Politika katmanı (4h) AI'ı
   bir ayar düğmesi yapıyordu; akışı LP kuruyordu. Bu o değildi.

   `ntc/ai/flowai.py` — model **tahsisi kendisi veriyor**: hangi isteğe kaç
   Mbps, hangi bacaktan. Çıktı `validate()` ile üç kısıttan geçiyor (talebi
   aşma, kapasiteyi aşma, var olmayan cihaz/bacak) ve `pins_for()` ile
   çözücünün **0. turuna** giriyor. LP artık karar verici değil hakem:
   modelin kararını ağa oturtuyor, kalanı dolduruyor.

   **Ölçüm — AI tek başına yetmiyor, hibrit kayıpsız:**

   | | AI tek | AI + LP | LP tek |
   |---|---|---|---|
   | geçen toplam | ~%22 | **%100** | %100 |

   AI tek başına optimumun beşte birini geçiriyordu (taleplerin çoğunu
   yanıtsız bırakıyor). Hibrit hiçbir şey kaybetmiyor.

   **Kimlikle sorma iki kat fark yarattı.** İlk sürümde model cihaz adını,
   yönü ve sınıfı yeniden yazıyordu ve ölçülebilir biçimde bozuyordu:
   `lan`'ı `down` yazıyor, bacak alanına `indirme` koyuyordu. Satırlara kısa
   kimlik (`r1`, `r2`) verip kimlikle cevap istemek:

   | | eski (ad yazdırma) | yeni (kimlik) |
   |---|---|---|
   | akışın AI payı | %16–24 | **%54–89** |
   | kayıp | 0.0 | 0.0 |

   Yazdırmadığı şeyi yanlış yazamıyor. **LAN talepleri modele hiç
   gitmiyor** — hedefleri internet değil, seçecek bacak yok, ve model yönü
   `down` sanıp satırı bozuyordu.

   **3. tur eklendi: artan kapasite çöpe atılmıyor.** AI'ın sabitlediği
   talepler artık turuna girmiyordu (kararı o verdi) ama kimsenin
   istemediği kapasite boşta kalıyordu — ölçüldü: sayaçlı senaryoda 45 Mbps,
   bozuk bacak senaryosunda 31 Mbps. Artık AI'ın kararı **taban** olarak
   korunuyor, üstü artıktan besleniyor. Kimseden bir şey alınmıyor.

   Canlı doğrulama (tıkanma senaryosu): AI geçerli, 10 tahsis, **0 ihlal,
   0 onarım**, bacakları kendi seçti (cikis-1: 6, cikis-2: 4), akışın %38'i
   AI kararı, LP'ye göre kayıp 0.0 Mbps.

   ⚠️ **Pay `MAX_ROWS` ile sınırlı.** Canlıda 54 talebin 10'u modele gidiyor,
   gerisi LP'ye — %38'lik payın tavanı bu. Model daha uzun listeyi
   kaldırabilirse pay yükselir.

   API: `/api/flow/ai` — `share` alanı iddiayı ölçülebilir tutuyor.

4k. **Model tavanı ölçüldü — phi-4-mini'de kalındı** *(2026-08-25)*

   `MAX_ROWS` sınırının donanımdan mı modelden mi geldiği soruldu. Süpürüldü:

   | satır | istem (karakter) | süre | geçerli | cevapladığı |
   |---|---|---|---|---|
   | 10 | 1896 | 6 sn | evet | 10 |
   | 16 | 2200 | 5 sn | evet | 10 |
   | 24 | 2606 | 3 sn | evet | 5 |
   | 32 | 3015 | 38 sn | **hayır** | 0 (JSON bozuk) |
   | 48 | 3828 | 3 sn | evet | 5 |

   **Donanım değil.** VRAM hatası yok, bağlam taşması yok (48 satır ≈ 1300
   token, sınır 4096), gecikme düz. Model uzun yapılı çıktı üretemiyor.

   **Bir üst basamağın önü VRAM ile kapalı:** phi-4 (14B) CUDA sürümü
   **8.4 GB**, kart **8188 MiB (8.0 GB)** — kıl payı sığmıyor. CPU sürümü
   10.2 GB ve 16.9 GB sistem belleğine sığar ama çok yavaş olur; kullanıcı
   denemeye gerek görmedi. phi-4-mini-reasoning (3.1 GB) **belleğe sığdı**,
   davranıştan düştü: 9249 karakter İngilizce akıl yürütme, `</think>` hiç
   kapanmıyor; 12288 token bütçesiyle 15 dakikada bitmedi.

   **Karar: phi-4-mini kalıyor.** Denenmemiş kaldıraç: modele **10'ar 10'ar**
   birkaç kez sormak (her turda kalan kapasiteyi bildirerek). Payı %38'in
   üzerine çıkarabilir, daha büyük model gerektirmez.

4l. **🔴→✅ AI katmanları tek elle yazılmış ağda sınanmıştı** *(2026-08-25)*

   Kullanıcı sordu: *"sen tek düz bir topolojide mi test ediyon hâlâ?"* —
   çözücü için hayır, **AI için evet**, ve bu tam da baştan beri istenenin
   tersiydi.

   | katman | eskiden nerede ölçülmüştü |
   |---|---|
   | çözücü optimalliği | 15 rastgele ağ ✅ |
   | tüm zincir | 12 rastgele ağ ✅ |
   | **AI akış üretimi** | tek elle yazılmış ağ ❌ |
   | **AI hedef seçimi** | tek elle yazılmış ağ ❌ |
   | **taban ölçeği** | 5 hız oranı, hepsi `sites=1, egresses=1` ❌ |

   Yani %38 pay, 0 kayıp, 2/4 doğruluk — hepsi tek bir ağdan geliyordu.

   #### Ölçüm 1: taban ölçeği şekle karşı — geçti

   12 farklı şekil (1–5 site, 1–5 çıkış, 50/50'den 2000/200'e): yön başına
   ölçek ve `background` tabanı **12/12** doğru. `t_floors_shape.py`.
   Taban düzeltmesi (4i) şekilden bağımsız çalışıyor.

   #### Ölçüm 2: AI akış üretimi 10 rastgele ağda — 4/10 ile başladı

   İlk tablo aldatıcıydı: *tahsis sayısı* saymıştım, oysa modelin verdiği
   tahsislerin çoğu **0.0 Mbps**'ti. Sıfırdan büyük tahsisleri sayınca
   gerçek sayı **4/10**. Tekrarlı koşu (3 tur) arızanın **rastgele değil
   yapısal** olduğunu gösterdi: aynı ağ hep aynı çöküyor.

   Ham çıktıya bakınca iki ayrı sebep çıktı:

   **(a) Şema sapması — bizim hatamız.** Model bazı ağlarda üst düzeyde bir
   **liste** döndürüyor, her elemanın içine kendi `allocations` alanını
   koyuyor. İçerik doğruydu — 10 satır, makul sayılar, bacaklar seçilmiş —
   ve `validate()` `raw.get("allocations")` bulamadığı için **tümünü
   reddediyordu.** Modelin iyi cevabını biz çöpe atıyorduk.
   → `flowai._normalize()` üç biçimi tek biçime indiriyor.

   **(b) Tıkanmada sıfırlama — modelin aritmetiği.** Talep kapasiteyi aşınca
   model *"toplam sınırı aşıyor"* deyip herkese **0** yazıyor. Payı
   bölüştürmek yerine reddediyor — tam da sistemin var olma sebebi olan
   durumda kesip atıyor.
   → İstem: "ASLA 0 YAZMA", ve yüzdeyi **biz** hesaplayıp hazır cümle olarak
   veriyoruz ("herkese kabaca %55'ini ver, sonra önceliğe göre ayarla").

   **(c) Düzeltmenin yan etkisi — ölçümde yakalandı.** "%83'ünü ver" deyince
   model çarpmayı yapamayıp JSON'un içine **ifadeyi** yazdı:
   `"grant_mbps": 229.7 * 0.83`. Niyet doğru, aritmetik yok; JSON geçersiz.
   → `provider._collapse_arithmetic()` yalnız **değer konumundaki**
   sayı-işlem-sayı üçlüsünü sonuca indiriyor. Dize değerleri tırnakla
   başladığı için desene takılmıyor.

   | | düzeltme öncesi | sonrası |
   |---|---|---|
   | 8+ anlamlı tahsis veren ağ | 4/10 | **9/10** |
   | ortalama AI payı | %49 | **%66** |
   | kayıp | 0.0 | **0.0** |

   **Uydurma kontrolü:** düzeltmeler bu 10 tohuma uyduruldu mu diye **hiç
   görülmemiş 10 ağda** yeniden ölçüldü (2–5 site, 1–5 çıkış, 60/6'dan
   1500/150'ye): **10/10 geçerli, 10/10 tam cevap, ortalama pay %71,
   toplam kayıp 0.0 Mbps.**

   ⚠️ Kalan: bir ağda (t207) model 10 satırın 5'ini yanıtlıyor ve duruyor.
   Bilinen erken-durma kusuru; kalan 5 talep LP'ye gidiyor, kayıp yok.

   #### Ölçüm 3: AI hedef seçimi 10 rastgele ağda — 5/10 → 9/10

   Her durum farklı bir rastgele mimaride, doğru cevap önceden yazılı:
   sayaçlı hat ×2, bozuk bacak ×2, gece yedekleme ×2, mesai tıkanması ×2,
   yüksek gecikme ×2.

   *İlk turda iki hatayı kendi testimde yaptım ve düzelttim:*
   `FlowPolicy` doğrulamadan sonra kategoriyi **sayıya** çeviriyor, ben
   metinle karşılaştırmıştım; ve sayaçlı senaryoları %88 doluluk ile
   kurmuştum, oysa istemimizin kendi kuralı "sayaçlı hat varsa **ve tıkanma
   yoksa** parayı öne al" diyor — model kurala uymuştu, test yanlıştı.

   Düzeltilmiş ölçüm **5/10**. İki arıza kümesi: sayaçlı 0/2 (bilinen borç,
   rastgele ağda da doğrulandı) ve **mesai tıkanması 0/2** — model iş
   saatinde tıkanıkken `streaming`/`bulk`'u realtime'ın önüne alıyordu, bu
   yeni ve daha zararlı.

   **Düzeltme — koşulu modele çıkarttırmıyoruz, hazır cümle veriyoruz.**
   `_situation()` artık durum özetinin başına bayrak satırları koyuyor:

   ```
   - TIKANMA VAR — hat dolu, kimi kısacağına karar vermelisin.
   - SAYAÇLI BACAK VAR (cikis-1) ve tıkanma yok → cost_weight "yuksek" olmalı.
   - MESAİ SAATİNDE TIKANMA → realtime ve interactive, bulk'un ÖNÜNDE olmalı.
   ```

   Bacak listesinde "SAYAÇLI" zaten yazılıydı ve model görmüyordu. Okuduğunu
   uyguluyor, çıkarım yapmıyor. **5/10 → 9/10.**

   Kalan tek arıza: bir ağda model sıralamadan `realtime`'ı düşürdü →
   doğrulama kapısı **reddetti**, mevcut hedef korundu, ağa hiçbir şey
   gitmedi. Yanlış cevap ama zararsız — kapının çalıştığının kanıtı.

   #### Genel ders

   Her üç düzeltme de aynı biçimde: **modelden çıkarım ya da aritmetik
   istediğimiz her yerde düşüyor, hazır cümle verdiğimiz her yerde
   çalışıyor.** Aynı kalıp daha önce sayı→kategori geçişinde (4h) ve
   ad→kimlik geçişinde (4j) de işe yaramıştı. Yeni bir AI özelliği eklerken
   önce şunu sor: *model burada hesap mı yapmak zorunda?* Cevap evetse
   hesabı Python'a al.

   Gerileme: `t_flowopt`, `t_enforce`, `t_enforce_scope`, `t_floors`,
   `t_floors_shape`, `t_demand`, `t_random_topo`, `t_json` — hepsi geçti.

4m. **✅ Ölçülmemiş ne varsa ölçüldü** *(2026-08-25)*

   4l'den sonra açıkta kalan tek şey "hiç ölçülmemiş sabitler"di. Hepsi
   rastgele mimaride sınandı — `t_sabitler.py`, `t_zincir.py`, `t_api.py`.

   | ne | kapsam | sonuç |
   |---|---|---|
   | Ağırlıklar (gecikme/para/sağlık) | 27 birleşim × 8 ağ = **216** | en kötü oran **1.0000** — hiçbir ağırlık trafik attırmadı |
   | Sınıf sırası | 20 permütasyon × 8 ağ = **640 ikili** | 0 ihlal |
   | Taban profilleri | 4 profil × 8 ağ × 5 sınıf × 2 yön = **320** | 0 ihlal |
   | Talep tahmini sabitleri | 8 senaryo + şişirme tavanı | 0 ihlal |
   | Belirlenimcilik | 8 ağ × 5 koşu | tek sonuç |
   | Talepten fazla verme / LAN-WAN | 8 ağ | 0 ihlal |
   | Tam zincir (AI dahil) | 8 ağ | kayıp 0.0, sapma ≤%2.2 |
   | API uçları, canlı | 12 uç | hepsi 200 |

   **Ölçüm hataları kendi tarafımda çıktı, üç kez.** Sabitlerin hepsi
   doğruydu; yanlış olan testlerdi ve her biri bulunması gereken bir şey
   öğretti:

   - **Max-flow hakemi sahte kapasite üretiyordu.** Hedef düğümde korunum
     uygulanmadığı için akış `access-1 → dist-1 → access-1` döngüsüne
     giriyor ve 200 Mbps'lik ağda "2500 Mbps tavan" çıkıyordu. Hedeften
     çıkan ve kaynağa giren kenarlar kapatılmalı. *(Aynı kusur
     `t_optimallik.py`'de de olabilir — orada yön ters olduğu için
     tetiklenmiyor, ama hakem kodu ortak değil.)*
   - **Tabanı nominal kapasiteyle karşılaştırdım.** Bozuk bacak
     `effective_mbps`'i düşürüyor; var olmayan kapasiteden garanti
     verilemez. 59 sahte ihlal üretti, oranların hepsi tam 0.781 çıkınca
     (bozuk bacağın payı) anlaşıldı. Taban **etkin** kapasiteye göre
     ölçülür.
   - **`PathAssigner` tek çıkışlı ağda boş dönüyor** ve bu doğru davranış:
     seçilecek yol yok, `update()` bu akışları bilerek tabloya almıyor.

   **Yol atayıcı ayrıca ölçüldü:** 2000 akış anahtarı ile ampirik dağılım
   planın kenar kullanımına uyuyor (en büyük sapma **%2.2**, tolerans %5),
   ve aynı akış anahtarı hep aynı çıkışa düşüyor (yapışkanlık).

   ⚠️ **Canlı koşuda görülen yeni kusur:** danışma yolunda model hedef
   alanına birleşik ad yazıyor — `'Cam-entrance ve cam-parking'` — ve
   normalizasyon bunları düşürüyor. Akış yolunu etkilemiyor (orada kimlikle
   cevap veriyor), yalnız öneri listesini zayıflatıyor. Kaydedildi, açık.

4n. **✅ Panel görsel denetimi — 7 kusur bulundu** *(2026-08-25)*

   Panel bugüne kadar "çalışıyor ama görsel olarak denetlenmedi" diye
   duruyordu. Denetim göz kararıyla değil **ölçerek** yapıldı: panele bir
   ölçüm betiği enjekte eden geçici bir yol (`/denetim`) açıldı, headless
   Edge ile 390 / 768 / 1024 / 1440 / 1920 px genişliklerde ve iki temada
   koşturuldu. Ölçülen: yatay taşma, viewport dışına çıkan öğeler, kesilen
   metin, dokunma hedefi boyutu, çökmüş çubuk dolgusu, boş ama yer kaplayan
   kartlar.

   | # | kusur | ölçüm |
   |---|---|---|
   | 1 | **Sayfa yana kayıyordu** | 390px'de **360px**, 768'de 114px, 1024'te 16px yatay taşma |
   | 2 | **Bütün çubuk dolguları görünmezdi** | `.bar` genişliği doluydu, **yüksekliği 0** |
   | 3 | Boş kartlar dev yer kaplıyordu | "Uyarılar" kartı 40 karakterle **632px** |
   | 4 | Dokunma hedefleri küçüktü | 10 düğmenin hepsi **30px** yükseklikte |
   | 5 | Grafik ekseni çakışıyordu | "Mbps" başlığı en üst eksen değerinin üstünde |
   | 6 | Dar ekranda kenar boşluğu fazlaydı | 390px viewport'ta 48px yatay dolgu |
   | 7 | Sınıflandırma kartı yoktu | yeni katmanın panelde karşılığı yoktu |

   **1 — yatay taşma.** `.flow-grid` ızgara öğelerinde `min-width: 0` yoktu.
   Izgara öğesinin varsayılan `min-width: auto` değeri, içeriğin doğal en
   küçük genişliğinin altına inmesini engelliyor; `.cmds` `white-space: pre`
   olduğu için o doğal genişlik **en uzun komut satırı** kadar. Sütun
   küçülmeyince tüm sayfa kayıyordu. `.cmds` üzerindeki `overflow-x: auto`
   yetmiyor — kısıtlanması gereken kap, öğenin kendisi.

   **2 — en sinsi olanı.** `.classrow .bar` bir `<span>`, yani satır içi
   eleman, ve satır içi elemanda `height: 100%` hiçbir şey yapmaz. Kutu
   sıfır yüksekliğe çöküyordu. Panelde bu sınıfı kullanan **bütün** çubuklar
   bomboştu — "Trafik sınıfı dağılımı" dahil — ve kimse fark etmemişti,
   çünkü **boş bir çubuk "değer düşük" diye okunuyor.** Sessiz yalan:
   gerçekte 12.1 Mbps olan etkileşimli trafik, bakan kişiye sıfır
   görünüyordu. Yalnız gözle bakmak bunu yakalamazdı; denetim betiğine
   "genişliği var ama yüksekliği yok" kontrolü eklendi ki geri gelemesin.

   **Sonuç:** üç genişlikte de **0 yatay taşma, 0 taşan öğe, 0 kesilen
   metin, 0 küçük hedef, 0 çökmüş dolgu, 0 boş dev kart.**

   Panele **Trafik sınıflandırma kartı** eklendi. Kart genel bir doğruluk
   yüzdesi göstermekle yetinmiyor, **katman başına pay ve isabet** veriyor:
   trafiğin çoğu tek sınıflı portlardan gelir ve orada isabet zaten %100'dür,
   asıl soru belirsiz olanda ne olduğudur. Şekil katmanının payı büyük ve
   isabeti düşükse yüksek bir genel yüzde hiçbir şey ifade etmiyor demektir.

   ⚠️ **Denetlenmeyen:** renk kontrast oranları (WCAG) ölçülmedi; klavye ile
   gezinme ve odak görünürlüğü denenmedi; ekran okuyucu denenmedi.

4o. **✅ Açık kusurlar kapatıldı** *(2026-08-25)*

   #### AI önerilerinin %38'i hedef yüzünden düşüyordu

   Canlı koşuda `'Cam-entrance ve cam-parking'` uyarısı görülmüştü. Ölçüldü:
   8 analiz turu, 26 öneri, **10'u (%38) tümden düşüyordu.** Düşenlere bakınca
   çoğunun anlamı belliydi, yalnız yazımı tutmuyordu:

   | model ne yazdı | sorun |
   |---|---|
   | `Cam-entrance ve cam-parking` | `ve` ayırıcı olarak tanınmıyor |
   | `Link` | yalnız büyük harf |
   | `Cam cihazları` | grup ifadesi (`cam-*`) |
   | `Interactive cihazlar` | geçerli sınıf + gürültü eki |
   | `Guest Wi-Fi a` | boşluk/tire farkı |
   | `İnteraktif Trafik` | model sınıf adını **Türkçeye çevirmiş** |
   | `özellikle ws-dev-02` | baştaki bağlaç |

   `_resolve_targets` yeniden yazıldı: ayırıcıya göre böl → baştaki bağlacı
   at → kanonik biçimde eşle (yalnız harf+rakam) → gürültü ekini at →
   Türkçe sınıf adını çevir → grup ön ekini genişlet.

   **%38 → %5** (44 öneri). Kalan ikisi gerçekten hedef değil
   (`Güvenilirlik`, `güvenilirlik sınıfı` — metrik adları) ve **doğru
   düşüyor.** Çözümlemeyi daha da gevşetmek, modelin söylemediği bir şeyi
   söylemiş gibi göstermek olurdu.

   *Bir tuzak ölçümde yakalandı:* `ve` için `` sözcük sınırı yetmiyor —
   tire de sınır sayılıyor ve `srv-ve-01` gibi meşru bir ad `['srv-','-01']`
   diye ikiye bölünüyordu. Boşluk şart koşuldu.

   Birim testi iki yönlü: **14 kurtarılmalı + 12 düşmeli**
   (`t_hedef_birim.py`). İkinci liste birincisi kadar önemli.

   #### Kesilen JSON analizin tamamını çöpe atıyordu

   Aynı ölçümde görüldü: `AI analizi başarısız: JSON tamamlanmamış`. Model
   bağlamı 4096 token, istem ~1500; model olağandışı uzun bir yanıt yazınca
   (ölçüldü: 6200 karakter) çıktı ortada kesiliyor ve **tamamen geçerli olan
   özet ve ilk bulgular da kayboluyordu.** Sıklık 14 analizde 1 — nadir ama
   kaybedilen şey analizin tamamı.

   `provider._salvage_truncated()`: açık kalan dizeyi ve parantezleri kapatır,
   ayrıştırır. **Hiçbir değer uydurmuyor** — yarım kalan son alan
   ayrıştırılamazsa atılıyor. 7 kurtarma + 4 ret vakası (`t_kurtarma.py`).

   ⚠️ **Bilinçli sözleşme değişikliği:** `t_json.py`'deki "yarım JSON
   reddedilmeli" vakası kaldırıldı. O test eski sözleşmeyi kodluyordu ve
   düzeltilen kusur tam olarak oydu.

   #### İki sessiz kusur daha

   - **`_policy_text` ölü ve kırıktı.** Sınıf içinde `self` almadan ve
     `@staticmethod` olmadan tanımlanmış; çağrılsa `TypeError` atardı.
     Hiçbir yerden çağrılmıyordu — istem artık `policies` alanı almıyor.
     Silindi.
   - **`extract_json` sözlük dönmeyi garanti etmiyor** ama imzası öyle
     diyordu. Model üst düzeyde dizi yazabiliyor (akış yolu bunu bilerek
     kullanıyor) ve `analyze()` doğrudan `data.get()` çağırıyordu — dizi
     gelen bir yanıt **yakalanmayan `AttributeError`** ile analiz döngüsünü
     düşürürdü. İmza düzeltildi, `analyze()`'a tip kapısı kondu.

4p. **✅ Panel erişilebilirlik denetimi — 5 kusur daha** *(2026-08-25)*

   4n'de "denetlenmedi" diye bırakılan taraf: kontrast, klavye, odak.
   Ölçüm yine enjekte edilen betikle — her metnin **görünen** rengi
   (saydam katmanlar altındakiyle karıştırılarak) arka planına karşı WCAG
   oranı hesaplandı, iki temada.

   | # | kusur | ölçüm |
   |---|---|---|
   | 8 | **Açık temada 72 metin kontrast eşiğinin altında** | `--muted` yüzeyde 3.50 / zeminde 3.35, eşik 4.5 |
   | 9 | Birincil düğme okunmuyordu | beyaz metin `--s1` üzerinde 3.64 (koyu) / 4.42 (açık) |
   | 10 | 9 tablo başlığında `scope` yok | ekran okuyucu hücreyi başlığıyla eşleştiremiyor |
   | 11 | **Yıkıcı komutlar mavi görünüyordu** | `var(--s1, #e5484d)` — yedek kırmızı hiç uygulanmıyordu |
   | 12 | Var olmayan tokenlar | `--bg2`, `--line` hiçbir yerde tanımlı değil |

   **8 — tek renk, 72 ihlal.** `--muted: #898781` iki temada da aynıydı;
   koyu temada 4.85 ile geçiyor, açık temada 3.50 ile kalıyordu. Açık tema
   için `#726f69` (yüzey 4.88, zemin 4.67); sıcak ton korundu.

   **9 — düğme kendi tokenini aldı.** `--s1` aynı zamanda grafikteki
   `realtime` serisinin rengi; onu koyulaştırmak grafiği bozardı.
   `--accent-solid: #2367b8` — beyaz metin 5.67, zemine karşı açıkta 5.29,
   koyuda 3.43 (bileşen eşiği 3.0). Tek değer iki temada da geçiyor.

   **11 — sessiz ama önemli.** `color: var(--s1, #e5484d)` yazılmıştı: yedek
   kırmızı ama `--s1` TANIMLI ve mavi, yani yedek hiç uygulanmıyordu.
   Kaldırılacak kuralın komutu uyarı rengiyle değil sıradan mavi ile
   çiziliyordu. Silme satırının kırmızı olması süs değil — operatörün "bu
   kuralı kaldırıyor" diye ayırt ettiği tek işaret.

   **Sonuç: iki temada da 0 kontrast ihlali**, en düşük oran 4.85 (koyu) /
   4.88 (açık). Odak göstergesi eksik 0, erişilebilir isim eksik 0, başlık
   seviyesi atlaması 0, `th scope` eksik 0, SVG etiketi eksik 0,
   `lang="tr"` doğru.

   ⚠️ **Hâlâ denetlenmeyen:** gerçek ekran okuyucu (NVDA/JAWS) ile kullanım,
   ve renk körlüğü simülasyonu *bu değişikliklerden sonra* tekrarlanmadı
   (palet daha önce CVD doğrulayıcısından geçmişti; kontrast ayrı bir ölçüt).

4r. **✅ Fazla virgül JSON'u ortadan düşürüyordu** *(2026-08-25)*

   Panelde canlı görüldü: `model hatası: Expecting property name enclosed in
   double quotes`. Ölçüldü — 25 analiz turu, **1'i (%4)** bu yüzden tamamen
   kayboluyordu. Ham çıktıya bakınca sebep açıktı:

   ```
       },
     ],          <- son elemandan sonra virgül
   ```

   Hata mesajı yanıltıcı: JSON `,` sonrası bir özellik adı bekliyor ve `]`
   bulunca "tırnaksız anahtar" diye şikâyet ediyor. `_salvage_truncated` de
   yetişemiyordu, çünkü bozukluk metnin sonunda değil **ortasında**.

   `provider._strip_trailing_commas()`: dize dışındaki `,` + kapanış
   ikilisini temizler. Dize içindeki virgüllere dokunmuyor — gerekçe
   cümlesindeki bir virgülü silmek metni bozardı. Kaçışlı tırnak
   (`"tirnak \" icinde, virgul"`) doğru izleniyor.

   `t_kurtarma.py` 12 kurtarma + 5 ret vakası.

4s. **✅ Renk körlüğü — kontrast düzeltmelerinden sonra yeniden ölçüldü** *(2026-08-26)*

   4p'de açık bırakılmıştı: kontrast için `--muted` ve düğme rengi değişti,
   ama renk körlüğü **ayrı bir ölçüt** ve birini düzeltmek ötekini bozabilir.

   **Yöntemi önce doğruladım ve iyi ki: iki kez benim beklentim yanlıştı.**

   - İlk simülatörüm Viénot matrislerini **yanlış uzaya** uyguluyordu.
     Doğrulama yakaladı: protanopide kırmızı/yeşil 35.4 çıkıyordu, oysa
     yakınsamaları gerekir. Machado ve ark. (2009) şiddet 1.0 matrislerine
     geçildi (doğrusal RGB üzerinde çalışıyorlar).
   - Sonra da *kontrolüm* yanlıştı: "protanopide kırmızı/yeşil ΔE yakın
     olmalı" diye yazmıştım. Değil — protanopide kırmızı **çok koyulaşır**,
     yeşil parlak kalır; ikisi **tonda** karışır, parlaklıkta değil. Doğru
     kontrol ton ekseninde: 96° → **1.4°** (protan), 0.7° (dotan).
   - Tritanopide hangi çiftin çöktüğünü de varsaymadım, ölçtüm: bu modelde
     yeşil/camgöbeği 17°'ye yakınsıyor; mavi ayrı kalıyor.

   **Eşiği de uydurmadım.** "ΔE 10 iyi mi kötü mü" sorusunun cevabı yok;
   ölçüt CVD için **özellikle tasarlanmış** Okabe-Ito paleti oldu.

   #### Soru 1 — kontrast düzeltmelerim bozdu mu?

   **Hayır.** `--muted` değişiminin CVD etkisi bütün kümelerde tam **+0.0**.

   #### Soru 2 — palet mutlak olarak nerede?

   | | Okabe-Ito | biz-açık | biz-koyu |
   |---|---|---|---|
   | normal | 21.7 | 24.2 | 21.1 |
   | protanopi | 15.2 | 14.7 | 11.4 |
   | **dotanopi** | 11.6 | 9.6 | **3.5** |
   | tritanopi | 12.1 | 8.2 | 5.5 |

   Trafik sınıfı renkleri (`--s1..--s5`) referansın altında ve bu **önceden
   beri öyle** — benim değişikliklerimden gelmiyor.

   #### Belirleyici bulgu: bilgi yalnız renkte değil

   Renk taşıyan her öğenin yanında metin var mı diye ölçüldü:
   **yalnız renkle anlatılan öğe sayısı 0.** Sınıf çubuklarında sınıf adı,
   doluluk çubuklarında yüzde ve "rahat/yüksek/kritik" sözcüğü, uyarılarda
   `label` + ikon (•/▲/■) var. Yani zayıf palet bilgi kaybı değil, hız kaybı.

   #### Yine de düzeltilen: yeşil/kırmızı

   `--good` / `--critical` dotanopide **5.4** ile ayırt edilemezdi ve bu çift
   hat doluluğunu gösteriyor — bir bakışta taranan sinyal.

   Değerler elle seçilmedi; kısıtları sağlayan aday **arandı**: ton kırmızı/
   yeşil kalacak, hiçbir kümede hiçbir CVD türünde gerileme olmayacak, metin
   dışı kontrast ≥ 3.0.

   | tema | değişen | dotanopi |
   |---|---|---|
   | açık | `--critical` #d03b3b → **#601122** | 5.4 → **33.2** |
   | koyu | `--good` #0ca30c → **#5bf968** | 5.4 → **25.9** |

   İki temada ters yönde: koyu zeminde kontrast için her renk açık olmak
   zorunda, orada `critical`'i koyulaştırmak kontrastı 3.62 → 2.35'e
   düşürürdü.

   *Arama bir tuzağı da gösterdi:* ton kısıtı koymadan en iyi aday
   `#601122` değil **`#600055`** çıkıyordu — mor bir "kritik" rengi. Metrik
   daha iyi, tasarım daha kötü. Sayıyı amaç edinmenin sınırı burası.

   *Elle seçtiğim ilk dark aday (`#1cc41c`) protanopide 9.9 → 1.2 gerileme
   yapıyordu ve ölçüm yakaladı.* Aramanın gerileme kısıtı olmasaydı
   uygulanmış olurdu.

   **Sonuç:** gerileme yok, `good`/`critical` çifti düzeldi, kontrast iki
   temada 0 ihlal (en düşük 4.85/4.88), yerleşim üç genişlikte 0 taşma.

   ⚠️ **Açık kalan:** `--s1..--s5` beşlisi dotanopide referansın %70 altında.
   Düzeltmek beş rengin birden yeniden tasarımı demek ve her biri kontrast +
   CVD + kimlik kısıtlarını aynı anda sağlamalı. Metin yedeği olduğu için
   acil değil; yapılırsa aynı arama yöntemiyle yapılmalı.

4t. **✅ Panel gerçek üründe uçtan uca çalıştırıldı** *(2026-08-26)*

   Şimdiye kadarki panel denetimleri (4n, 4p, 4s) **enjekte edilmiş kopyalar**
   üzerinde koşuyordu (`/denetim`, `/a11y`). Bu, ölçtüğümüz şeyin kullanıcının
   açtığı sayfa olduğunu kanıtlamıyordu. Bu tur `python -m ntc serve` ile
   gerçek ürün açıldı.

   | ne | sonuç |
   |---|---|
   | `GET /` (panel) | 200 |
   | `GET /api/status` | 200 |
   | JS hatası (`uncaught`, `SyntaxError`, `ReferenceError`, `TypeError`) | **0** |
   | Boş kart | **0/16** |

   Ekran görüntüsünde her kart doluyordu: kutucuklar, zaman serisi grafiği,
   doluluk ölçerleri, sınıf dağılımı, 10 satırlık cihaz tablosu, AI analisti
   (3 bulgu + 6 cihaz bazlı öneri — 4m'de açık kalan hedef çözümleme kusurunun
   düzeldiği görünüyor), akış planı + çıkış diyagramı, infaz kartı,
   sınıflandırma kartı, senaryo düğmeleri.

   **Etkileşim canlı test edildi.** `POST /api/sim/scenario`
   (`{"name":"congestion","duration":90}`; listeleme ayrı uçta:
   `GET /api/sim/scenarios`):

   ```
   t+ 25s  indirme %27  yükleme %29   talep  135 Mbps   darboğaz 0
   t+ 40s  indirme %51  yükleme %61   talep  291 Mbps   darboğaz 0
   t+ 55s  indirme %54  yükleme %72   talep  537 Mbps   darboğaz 4
   t+100s  indirme %36  yükleme %49   talep  552 Mbps   darboğaz 4  (sönüyor)
   ```

   **"Darboğaz var ama toplam doluluk %72" çelişki değil.** İnceledim: doymuş
   kenarlar `core→cikis-2` ve `cikis-2→internet`, ikisi de 18.0/18.0 Mbps
   (doluluk 1.0). Çözücü bir bacağı sonuna kadar doldurup kalanı ötekine
   taşırıyor; iki bacak (22 + 18) olduğu için toplam %72'de kalıyor. Doğru
   davranış — ileride "darboğaz varken neden %100 değil" diye yeniden
   sorulmasın diye kaydedildi.

   Sınıflandırma kartı canlıda: %100 uyum, katman dağılımı
   Port %59 / Hedef IP %21 / Varsayılan %20.

4u. **✅ Testler depoya alındı, koşucu yazıldı** *(2026-08-26)*

   **Bulgu:** "9 test paketi GECTI" satırının dayanağı **silinebilir bir temp
   klasörüydü.** 46 test dosyası
   `%LOCALAPPDATA%\Temp\claude\...\ef0f7898-.../scratchpad` altında duruyordu;
   git'te izleri yoktu, Windows temp temizliği hepsini götürebilirdi. Faz 2
   tam da bu testlerin koruduğu yüzeyi (toplayıcı, sınıflandırma, talep)
   değiştireceği için önce koruma ağı sabitlendi.

   | ne | sonuç |
   |---|---|
   | `tests/` altına alınan dosya | 46 → **45** (biri silindi, aşağıda) + 1 yeni = **46** |
   | Sabit yol temizliği | 45 dosyada 51 `sys.path.insert(0, r"c:\...")` satırı depo-göreli hale geldi |
   | Koşucu | `tests/kos.py` — her test ayrı süreçte, UTF-8 zorunlu |
   | Varsayılan koşu | **31/31 geçti, 186 sn** |
   | Model gerektiren koşu | **15/15 geçti, 1397 sn** |

   **`t_sev` silindi — eski sözleşmeyi kodluyordu.** Çağırdığı
   `AIAnalyst._calibrate_severity` artık yok: AI'ın rolü "dereceyi model
   verir"den "dereceyi kural motoru verir"e çevrilince (Teknik borç #4)
   yerini `_attach_severity` aldı ve testini `t_sev2` devraldı. Kırık test
   değil, **kapanmış bir tasarımın kalıntısıydı**; onarmak eski davranışı
   geri çağırmak olurdu. *(Aynı gerekçeyle `t_json.py`'den bir vaka
   çıkarılmıştı — bkz. 4o.)*

   **Testler ikiye ayrıldı ve ayrım "ağ kullanıyor mu" değil, "modele soruyor
   mu".** 15 test gerçek bir LLM sağlayıcısına çıkarım yaptırıyor (14-33 sn,
   çok turlular 75 sn'yi aşıyor) ve model yokken hepsi kırmızı yanardı —
   gerçek gerilemeleri gizlerdi. Varsayılan koşuya girmiyorlar
   (`--servis` / `--hepsi` ile çağrılıyorlar). `t_api`, `t_bosluk`, `t_snap`
   de 30-60 sn sürüyor ve kontrolcü ayağa kaldırıyor ama **model olmadan da
   sonuç veriyorlar**; onlar varsayılanda kaldı.

   **UTF-8 bir kez yanlış teşhise yol açtı, koşucu bunu yapısal olarak
   çözüyor.** Testler Türkçe yazıyor, Windows konsolu cp1252: ilk koşuda
   16 testin 9'u "başarısız" göründü, sebebi `UnicodeEncodeError`'dı, kodda
   hata yoktu. İronik ayrıntı: koşucunun **kendisi** ilk sürümünde tam bu
   hataya düştü ("test koşuluyor" satırındaki `ş`) — çocuk süreçlere ortam
   değişkeni geçirmek yetmiyor, süreç kendi çıktı akışını da çevirmek zorunda.

4v. **✅ Akış kaynağı soyutlaması — `mode` artık gerçek** *(2026-08-26)*

   **Bulgu:** `cfg.mode` (`simulation` | `live`) **hiçbir davranışı
   değiştirmiyordu.** Tek kullanımı `cli.py` ile `/api/status` ekranıydı;
   `_collect_loop` doğrudan `self.simulator.tick(dt)` çağırıyordu. Yani
   `NTC_MODE=live` ile çalıştırılan sistem sessizce simülasyon üretmeye
   devam ediyordu — ayar gibi görünen bir vaat.

   | dosya | ne yapıyor |
   |---|---|
   | `ntc/traffic/source.py` | `FlowSource` protokolü (`name`, `devices`, `supports_scenarios`, `tick`, `start`, `aclose`) + `build_source(cfg)` |
   | `ntc/traffic/simulator.py` | sözleşmeyi karşılıyor: `name="simulation"`, senaryo yeteneği, boş yaşam döngüsü |
   | `ntc/controller.py` | `self.simulator` → `self.source`; kaynak `build_source` ile seçiliyor, `start`/`stop` kaynağın yaşam döngüsünü çağırıyor |

   **Bilinmeyen modda hata veriyor, varsayılana düşmüyor.** Yazım hatası
   yüzünden simülasyonda kalan bir kurulum, gerçek ağı izlediğini sanan bir
   operatör demektir. `mode: live` şu an gerekçeli `UnsupportedMode`
   fırlatıyor: *"Faz 2 — Sysmon/ETW telemetrisi henüz uygulanmadı."*
   Türkçe karşılıklar (`simulasyon`, `canli`) da tanınıyor.

   **Senaryolar arayüzün parçası DEĞİL, kaynağın yeteneği.** "Tıkanma
   senaryosu tetikle" yalnız üretilmiş trafikte anlamlı; canlı yakalamada
   karşılığı gerçek ağa sahte trafik basmak olurdu. `supports_scenarios`
   bayrağı var: `POST /api/sim/scenario` yeteneği olmayan kaynakta gerekçeli
   **409** dönüyor, `GET /api/sim/scenarios` ise boş liste + `supported:
   false` (panel bu ucu her açılışta çağırıyor, orada hata gürültü olurdu).
   Arayüze koyup canlı kaynakta boş geçseydik, düğmeye basan operatör
   tetiklediğini sanacaktı.

   `/api/status` artık `mode` (**istenen**) ile `source` (**olan**) alanlarını
   ayrı veriyor.

   **Doğrulama:** `mode` sweep'i (`live`/`canli` → gerekçeli hata,
   `simulasyon` → simülatör, `sacma` → geçerli değerler listesiyle hata),
   protokol uyumu (`isinstance(src, FlowSource)`), 12 akışlık `tick`,
   ve **30/30 test** — `t_api` (16 uç canlı) dahil.

   **Değiştirdiğim uçları hiçbir test kapsamıyordu.** `t_api` 16 ucu geziyor
   ama senaryo uçları listesinde yoktu — yani tam da dokunduğum yüzey açıktaydı.
   `t_kaynak.py` yazıldı, **21 kontrol**: mod eşlemesi (4 geçerli yazım),
   canlı modun gerekçeli hatası, üç yazım hatası varyantı, protokol uyumu,
   sahte kaynak takılınca toplayıcının onun akışlarını işlemesi, senaryo
   uçlarının iki kaynakta da doğru davranması (200 ↔ 409).

   *Testi yazarken iki kez kendi hatama düştüm ve ikisi de aynı şeyi
   gösterdi:* sahte kaynağın ürettiği `Flow` önce yanlış alan adıyla
   (`protocol`, doğrusu `proto`), sonra var olmayan `Direction.DOWN` ile
   kuruluyordu. İkisinde de toplayıcı istisnayı yakalayıp logluyor ve
   **sessizce 0 akış** işliyordu — test log seviyesi `CRITICAL` olduğu için
   sebep görünmüyordu. Testin log seviyesi `ERROR`'a çekildi. Faz 2'de canlı
   kaynak da bu döngüye takılacak; oradaki bir alan uyuşmazlığı aynı şekilde
   sessiz kalırdı.

   **Hâlâ eksik:** canlı kaynağın kendisi. Bu iş yalnız **yeri** açtı.

4w. **✅ FAZ 2 — Canlı kaynak: gerçek trafik `Flow`'a dönüyor** *(2026-08-26)*

   Simülatörün yerine geçen ilk gerçek kaynak. `mode: live` artık çalışan bir
   şey.

   **Tasarımı belirleyen kısıt: Windows'ta kimlik ile hacim aynı kaynaktan
   gelmiyor.**

   | besleme | verdiği | vermediği |
   |---|---|---|
   | paket yakalama (Npcap/scapy) | 5'li başına **bayt/paket** | süreç |
   | bağlantı tablosu (`psutil`) | 5'li başına **süreç/PID** | bayt |
   | *(Sysmon Event 3)* | süreç, olay tabanlı | **bayt alanı yok** |

   Yani "Sysmon kurunca canlı mod gelir" yanlış bir beklenti olurdu: Sysmon
   bağlantı olayında hacim yok. Doğru şekil **iki beslemenin 5'li üzerinden
   birleştirilmesi** ve Sysmon ileride kimlik beslemesinin *yerine* geçer,
   birleştirmenin şekli değişmez.

   | dosya | ne yapıyor |
   |---|---|
   | `ntc/traffic/capture.py` | hacim beslemesi: scapy `AsyncSniffer`, 5'li başına sayaç. Paket **saklanmıyor** (`store=False`), yalnız tamsayı artıyor |
   | `ntc/traffic/live.py` | `ConnectionOwners` (kimlik) + `LiveSource` (birleştirme, yön kararı, cihaz türetme) |
   | `ntc/core/config.py` | `live:` bloğu (arayüz, filtre, yoklama sıklığı, TTL) |

   **Ölçüm (gerçek ağ, bu makine, yönetici DEĞİL):** 75 akış / 275 KiB,
   736 paket, süreç adı çözülme **%70**. Gerçek süreçler çıktı:
   `claude.exe`, `opera.exe`, `msedge.exe`, `steamwebhelper.exe`,
   `Code.exe`, `ms-teams.exe`.

   #### Dört kusur ölçümle bulundu, üçü sessizdi

   **1. Yakalama "çalışıyor" görünüp 0 paket üretiyordu — yanlış arayüz.**
   scapy'nin varsayılan arayüzü bu makinede bir TAP adaptörüydü;
   `running` True, 10 saniyede sıfır paket. Doğru arayüzde (Wi-Fi) yönetici
   olmadan 33 paket geldi, yani sebep yetki değildi.
   → `guess_interface()`: kurulu **dış bağlantıların yerel adreslerine**
   bakıp o adresi taşıyan arayüzü seçiyor. "Arayüz ayakta mı" yetmiyor —
   VPN/VirtualBox adaptörleri de ayakta ve IP'li. *Trafiğin aktığı yeri akan
   trafik söylüyor.*

   **2. Sessizlik artık bir durum alanı.** "Ayakta ama hiç paket yok" dışarıdan
   sağlıklı görünüyordu: panel sıfır trafikle dolar, kimse sebebini aramaz.
   `PacketVolumeFeed.sessiz` + tek seferlik uyarı logu + `/api/status`
   içinde `capture_silent`.

   **3. Katman importu geç kalıyordu ve BÜTÜN paketler düşüyordu.** scapy
   katmanları (`IP`, `TCP`, `UDP`) ilk paket geldiğinde import ediliyordu;
   scapy paketi **çözümlerken** o sınıflar yüklü olmadığı için bağlantı
   katmanını tanıyamıyor ("Unable to guess datalink type") ve her paketi ham
   `Packet` olarak kuruyordu. `IP in pkt` hiçbir zaman tutmuyor: yakalama
   **45 paket teslim etmesine rağmen sayaçlar 0** kalıyordu. Çözümleme geriye
   dönük düzeltilemiyor. → Katmanlar yakalamadan **önce** yükleniyor
   (`_katmanlar()`).

   *Bu kusur teşhis edilirken iki kez yanlış yöne sapıldı ve ikisi de
   ölçümle elendi: BPF filtresi (`ip` 104 paket, `ip or ip6` 26 paket —
   ikisi de çalışıyor) ve karışık mod (`promisc=False` 55 paket — o da
   çalışıyor).*

   **4. Süreç çözülme oranı %52'de takılıydı — sebebi `psutil`'in UDP'yi
   uzak uçsuz vermesi.** Ölçüm: 71 UDP kaydının **71'inde** `raddr` boş;
   ayrıca 60 soket joker adrese (`0.0.0.0`/`::`) bağlı ama paket somut IP
   taşıyor. Yalnız 5'li anahtarla çalışırken bütün QUIC/DNS trafiği sahipsiz
   kalıyordu.
   → Kimlik tablosu **üç seviyede** indeksleniyor: tam 5'li → yerel uç →
   yalnız port. Aramada da bu sırayla deneniyor; zayıf eşleşme yalnız
   güçlüsü yokken kullanılıyor.
   **%52 → %59 → %70.**

   #### Bilerek yapılmayanlar

   - **`rtt_ms` ve `retransmits` 0 kalıyor.** Paket sayaçlarından ikisi de
     çıkarılamaz (RTT için ACK eşlemesi, yeniden gönderim için sıra numarası
     takibi gerekir). Uydurmak yerine sıfır: **hat kalitesi kuralları canlı
     modda kör**, hat doluluğu kuralları etkilenmiyor. Açık borç.
   - **Sınıf etiketi üretilmiyor** (`labels_traffic_class = False`).
     Kontrolcü bunu görüp sınıflandırıcıyı **canlı moda alıyor** ve gerekçeyi
     logluyor: gölge modda kalsaydı bütün trafik varsayılan sınıfta kalır ve
     önceliklendirme sessizce anlamsızlaşırdı.
   - **`Flow.process` alanı eklendi.** `ClassifyAudit` zaten `flow.process`
     okuyordu ama `Flow`'da öyle bir alan yoktu — yani sınıflandırıcının **en
     sağlam katmanı canlı modda hiç ateşlenmeyecekti.** Sessiz bir uyumsuzluk;
     bulunduğu için kapandı.
   - **Cihaz envanteri gözlemden doğuyor**, kimliği uydurulmuyor: `kind`
     UNKNOWN, `trust` sabit. AD entegrasyonuna kadar bilinmiyor.

   #### Kalan kayıp öğretici

   Çözülemeyen akışların çoğu `tcp/80` — yani **ölçüm sırasında benim
   ürettiğim** kısa ömürlü isteklerin kendisi: yoklama arasında açılıp
   kapanıyorlar. Sysmon Event 3 olay tabanlı olduğu için tam bu boşluğu
   kapatır. **Sysmon'un gerçek katkısı "süreç adı" değil, yoklamanın
   kaçırdığı kısa ömürlü bağlantılar.**

   **Testler:** `t_live.py` — paketleri kendisi kurup besliyor, yani yönetici
   ve ağ gerektirmiyor (29 kontrol: yön/anahtarlama, iki yönün tek kovada
   birleşmesi, sessizlik tespiti, üç anahtar seviyesi, TTL, `Flow` üretimi,
   sınıflandırıcı modunun otomatik geçişi). Gerçek yakalama ayrıca elle
   ölçüldü (yukarıdaki sayılar); ağda o an ne aktığı tekrarlanabilir bir
   ölçüm olmadığı için teste konmadı.

   #### Uçtan uca: kontrolcü + canlı kaynak (gerçek trafik)

   ```
   kaynak: live   sınıflandırıcı: canli (otomatik geçti, gerekçe loglandı)
   111 akış · 577 paket · süreç çözülme %95.5 · yakalama sessiz değil
   sınıf dağılımı: interactive 0.04 / background 0.01 / bulk / streaming
   ```

   **Süreç çözülme %70 → %95.5** ve sebebi öğretici: kontrolcü saniyede bir
   yokluyor ve TTL biriktiği için kısa ömürlü bağlantılar da yakalanıyor.
   Tek seferlik ölçümdeki %70, yoklama geçmişi olmayan **soğuk başlangıcın**
   oranıydı.

   **Katman dağılımı simülasyondakinin tersi çıktı:** canlıda **şekil %89**,
   port %3.6, süreç %4.5, IP %0.9. Simülasyonda port başı çekiyordu
   (%59). Sebep: gerçek trafiğin çoğu küçük hacimli keepalive/telemetri ve
   şekil katmanı onları "hacim çok küçük → background" diye önce yakalıyor.
   ⚠️ Bu **isabet değil dağılım**; canlıda karşılaştırılacak doğru etiket
   olmadığı için sınıflandırma doğruluğu hâlâ ölçülmemiş durumda.

   ⚠️ **Doğrulanmamış:** yansıtma (SPAN) portundan çok cihazlı yakalama,
   yönetici hakkıyla davranış, yüksek hızda (>100 Mbps) paket düşme oranı,
   canlı sınıflandırmanın **isabeti**.

### Tasarımı konuşuldu, kodu yazılmadı

4c. **Cache kaydı boşluğu**

   **⚠️ Kayıt boşluğu:** kullanıcı "ağın girişinde ortak bir cache, AI ona
   göre önceliklendirir" diye bir tasarım hatırlıyor; bunun kaydı hiçbir
   yerde yok (kod, bu dosya, README, git geçmişi arandı). Kuyruk mu içerik
   önbelleği mi olduğu netleşmedi. **Netleşince buraya yaz.**

4d. **Talep tahmini — AI'ın rolü netleşti (2026-08-24)**

   Kullanıcıya soruldu: AI *tahmin* mi yapacak yoksa *mevcut durumu* mu
   yorumlayacak? Cevap: **mevcut durumu yorumlamak.** Yani AI zincirin
   içinde değil yanında duruyor; sayıyı çözücü veriyor, AI operatöre
   anlatıyor. Bu karar `prompts.py`'deki mevcut tasarımla uyumlu, değişiklik
   gerektirmiyor.

   Eğilim tahmini (*"20 dk sonra hat dolacak"*) yine de açık bir borç ama
   **modelin işi değil**: `db.py` 24 saatlik zaman serisini tutuyor ve
   kullanılmıyor; eğilim oradan regresyonla çıkar, AI çıkanı yorumlar.

4e. **✅ Talep tahmini — doygun hatta ölçülen hız zaten tavan** *(2026-08-25)*

   `demands_from_signals()` ölçülen hızı talep sayıyordu ve bu sistemi tam da
   tıkanma anında körleştiriyordu: 300 Mbps gerçek talep / 100 Mbps kapasite
   durumunda herkes 33 Mbps ölçülüyor, çözücü "33 istedi 33 verdim" deyip
   geri çekme listesini **boş** bırakıyordu.

   `ntc/traffic/demand.py` — hat **boşken** ölçülen değer gerçek taleptir;
   o anlar cihaz+yön başına tepe olarak saklanıyor, hat dolduğunda oradan
   okunuyor.

   **Ölçüm (gerçeği bildiğimiz kurgu, `t_demand.py`):**

   | | eski sistem | tahminci |
   |---|---|---|
   | toplam mutlak hata | 200.0 Mbps | **0.0 Mbps** |
   | gördüğü eksik | 0.0 Mbps (kör) | **200.0 Mbps** |

   **Kritik ayrım: cihaz payına dayanmış mı?** Tıkanık hatta düşük ölçümün
   iki zıt sebebi var — cihaz kısıtlanıyordur (talebi yüksek) ya da boştadır
   (talebi ölçülen kadar). Ayırt eden: adil payının %90'ını kullanıyor mu.
   Bu ayrım olmadan yedeklemesi bitmiş bir sunucu, eski tepesiyle
   başkalarının payını çalıyordu (ölçümde yakalandı: 5 Mbps çeken cihaza
   50 Mbps talep uydurdu).

   **İki tasarım hatası ölçümde yakalandı ve düzeltildi:**
   - *Tepenin değerini yaşıyla küçültmek.* 60 Mbps çektiği gözlenen cihaz
     6 saat sonra 45 Mbps ister sayılıyordu; 45 hiçbir zaman gözlenmedi.
     Tepe bir olgudur, olgu yaşlanmaz — *ilgisi* yaşlanır. Değer olduğu gibi
     duruyor, yaşlanma `confidence` tarafında; pencere dolunca tamamen düşüyor.
   - *`peak_ts = 0.0` sentinel'i.* Sıfır geçerli bir zaman damgası; tepe
     sessizce düşüyordu. `has_peak` bayrağıyla ayrıldı.

   **Sinyal yoksa şişirmiyoruz.** Adil pay bilgisi verilmezse tahmin =
   ölçüm. İlk sürümde tersi vardı ("tıkanık hatta ölçüm zaten baskı
   altındadır") ve boştaki cihaza hayali talep uyduruyordu. Ayıramadığımızda
   şişirmek, başkasının payını çalmak demek.

   ⚠️ **Simülasyonda bu katman etkisiz** ve bu beklenen: simülatör hat
   tavanını uygulamıyor, akışları doğal hızlarında üretiyor. Yani sim'de
   ölçülen zaten talep. Katmanın değeri kısıtlanmayı elle kurduğumuz
   `t_demand.py` üzerinde kanıtlandı; gerçek ağda asıl orada işe yarayacak.
   Canlı koşuda doğrulanan: eşik altında devreye girmiyor (%64 doluluk →
   şişirme yok), eşik üstünde giriyor (%139 doluluk → 2 satır geri çekme).

   API: `/api/flow/demand` — hangi cihazın boş saatte ne çektiği.

4f. **✅ Trafik sınıflandırma — DPI'sız katmanlı** *(2026-08-25)*

   `ntc/traffic/classify.py`. Sistemin bütün öncelik mantığı
   `traffic_class`'a dayanıyor; simülasyonda o etiket hazır geliyor, gerçek
   ağda gelmiyor. Bu katman olmadan geri kalan her şey doğru cevabı yanlış
   soruya veriyor.

   **Katman sırası — ölçümle düzeltildi:**

   ```
   1. süreç adı (Sysmon Event 3)
   2. TEK SINIFLI PORT          ← ilk sürümde 3. sıradaydı, yanlıştı
   3. hedef IP bloğu            ← yalnız BELİRSİZ portta
   4. akış şekli
   5. varsayılan: interactive
   ```

   *İlk sürümde IP portun önündeydi ve ölçüm bunu yakaladı:* DNS `udp/53`'ten
   gidiyor ama çözücü bir bulut adresinde durduğu için IP katmanı onu
   `https-web` sayıyordu. **Genel doğruluk %72.4 → %64.2, yani IP katmanı
   eklemek sistemi kötüleştiriyordu.** Sebep basit: `udp/53`'e giden akış
   DNS'tir, hedefin kim olduğundan bağımsız. IP'nin yeri portun cevap
   veremediği yer.

   **Belirsiz port tablosu elle yazılmıyor,** katalogdan türetiliyor.
   `tcp/443` = 6 uygulama, 4 sınıf (`https-web`, `netflix`, `youtube`,
   `windows-update`, `cloud-sync`, `os-telemetry`). Elle yazılsa katalogla
   sessizce ayrışır ve ayrışma tam da yanlış sınıflandırma demek olurdu.

   **Tarayıcılar bilerek eşlenmiyor.** `chrome.exe → https-web` yazmak,
   tarayıcıdan izlenen Netflix'i `interactive` yapardı — bir video akışına
   en yüksek etkileşimli önceliği vermek. Tarayıcı `svchost` gibi konak
   süreç; ne yaptığını IP ve şekil söyler. IP katmanının varlık sebebi bu.

   **"Tek yönlü akış → streaming" kuralı SÜPÜRÜLDÜ ve kaldırıldı.**
   Sezgisel olarak doğru görünüyordu (video indirir, yüklemez) ama her
   eşikte zarar veriyordu — `https-web` de tek yönlü olabiliyor:

   | yukarı/aşağı eşiği | IP yokken | IP varken |
   |---|---|---|
   | 0.00 (kural kapalı) | **%98.3** | **%99.8** |
   | 0.01 | %98.0 | %99.2 |
   | 0.02 | %97.0 | %97.6 |
   | 0.05 | %92.9 | %92.9 |

   `SHAPE_STREAM_DOWN_BPS` de süpürüldü: 6 Mbps optimumda çıktı — tam
   olarak katalogdaki `https-web` tavanı.

   **Simülatörün bir kusuru bu iş sırasında bulundu ve düzeltildi.** Hedef
   IP'yi **uygulamadan bağımsız** ortak bir havuzdan seçiyordu — yani
   Netflix CDN'ine giden DNS akışı üretiyordu. Gerçek ağda bu olmaz ve IP
   katmanını ölçülemez kılıyordu (isabet %15.6). `catalog.APP_ENDPOINTS`
   ile uygulama başına gerçekçi blok verildi.

   **Doğruluk — 8 simülasyon tohumu, 15.070 akış:**

   | koşul | doğruluk | ne demek |
   |---|---|---|
   | **A. IP yok (port + şekil)** | **%97.4** | **gerçek taban** — hiçbir tablo kendini doğrulamıyor |
   | **D. IP tablosu eksik (1/3)** | **%98.3** | **gerçek dünyaya en yakın** — blok listeleri hep eksiktir |
   | B. IP tam | %100.0 | üst sınır, kendi tablomuza dayanıyor |
   | C. IP tam + süreç | %100.0 | tavan |

   ⚠️ **B ve C bir şey kanıtlamaz.** `classify.IP_RANGES` ile
   `catalog.APP_ENDPOINTS` aynı gerçeği anlatan iki tablo ve ikisini de biz
   yazdık; "IP açıkken %100" kendi tablomuzu kendi tablomuzla doğrulamaktır.
   **Anlamlı sayılar A ve D.**

   ⚠️ **Gerçek ağ bu sayının altında kalır.** Ölçüm simülatöre karşı ve
   simülatörün uygulamaları tam olarak katalogdakiler. Gerçek trafikte
   katalogda olmayan uygulamalar var; onlar varsayılana (`interactive`)
   düşer.

   **Sisteme gölge modda bağlandı** (`ClassifyAudit`, `classify.mode`
   varsayılan `golge`). Simülasyonda etiket zaten doğru; onu %97'lik bir
   tahminle ezmek düpedüz gerileme olurdu. Gölgede karar veriliyor,
   karşılaştırılıyor, akışa dokunulmuyor. `mode: canli` sınıfı gerçekten
   yazar — canlı yakalamada tek kaynak o.

   API: `/api/classify` — uyum oranı, **katman başına pay ve isabet**,
   belirsiz portlar, uyuşmazlık örnekleri. Katman dökümü olmadan
   "sınıflandırma çalışıyor" ölçülemez bir iddia olurdu.

### Lab gerektiriyor

5. AD tabanlı kimlik (`Device.trust` → OU/grup üyeliği), DHCP kiralarından
   cihaz envanteri
6. AD honeytoken + Event 4769 izleme
7. NPS + 802.1X karantina VLAN
8. GPO ile QoS/firewall dağıtımı
9. RRAS

### Henüz hiç ele alınmadı

10. Honeypot / deception: sahte SMB, RDP, WinRM, LDAP servisleri; API yoklayanlara
    tutarlı sahte HTTP yanıtları; DNS sinkhole
11. Canlı yakalama modu (`mode: live`) — ETW providers
    (`Microsoft-Windows-TCPIP`, `DNS-Client`) veya scapy

### Faz 7 — Endpoint bütünlüğü ve zararlı yazılım triyajı

*(2026-08-21'de kararlaştırıldı. Fikrin kaynağı: Linux'taki IMA/EVM'nin
Windows karşılığını kurup AI ile yorumlatmak.)*

| Linux | Windows karşılığı |
|---|---|
| IMA-appraisal | **WDAC** — çekirdek kod bütünlüğü politikası, **audit modu var** |
| IMA measurement log | **Sysmon** Event 1/7/6 — SHA256 + imza durumu |
| fanotify | MiniFilter sürücüsü — **kapsam dışı** (EV sertifika + MS onayı gerekir) |
| AIDE/Tripwire | Sysmon Event 11 + hash temel çizgisi |
| — | **AMSI** — script içeriğini çalışma anında, çözülmüş halde verir |

**Yapılacaklar sırası:**
1. Sysmon hash + imza telemetrisi (Faz 2'nin doğal uzantısı)
2. WDAC audit modu + CodeIntegrity olayları (3076 audit / 3077 enforced)
3. Hash temel çizgisi + TrustedInstaller korelasyonu
4. **AMSI sağlayıcısı → script içeriği → AI yorumu** ← asıl değer burada
5. Defender verdiktlerinin tüketilmesi (`Get-MpThreatDetection`)

**AI'ın buradaki tek gerçek katkısı AMSI.** Karıştırılmış PowerShell'e bakıp
niyeti okuyabiliyor — imza tabanlı tespitin yapısal olarak yapamadığı şey.

**AI'a verilmeyecekler (sezgiye ters, o yüzden yazılı):**
- **Hash'e bakıp karar vermek.** SHA256 içerik hakkında sıfır bilgi taşır; model
  yine de emin bir cevap uydurur. Hash → itibar sorgusu veritabanı işidir.
- **Binary içeriği okutmak.** Ham PE baytları pahalı ve faydasız. Anlamlı olan
  üst veri: imza, yayıncı, yol, süreç soyağacı.
- **Gerçek zamanlı engelleme.** Dosya erişimi mikrosaniye, model saniye.
- **Defender'ın yerini almak.** Bilinen zararlıda o bizden kat kat iyi; tüketiriz.

**⚠️ Hacim problemi — bu modülün batacağı yer.** Bir iş istasyonu saatte on
binlerce dosya olayı üretir; yerel modelin bütçesi saatte birkaç düzine çağrı.
Araya ~1000:1 deterministik eleme şart:

```
Ham olaylar (~10.000/saat)
  ↓ Katman 1 (deterministik): imzalı mı? bilinen yayıncı mı? temelde var mı?
  ↓ (~200/saat)
  ↓ Katman 2 (ucuz zenginleştirme): yerel izin listesi, Defender verdikti, soyağacı
  ↓ (~20/saat)
  ↓ Katman 3: AI yorumu
```

Elemeyi yapan katman **kesinlikle model olmamalı** — eleme maliyeti elenenden
pahalıya gelir.

**⚠️ Temel çizgi gürültüsü.** Windows Update binlerce meşru dosya değiştirir.
Ayırt edici sinyal: değişikliği yapan `TrustedInstaller` soyundan mı geliyor?
Deterministik kural, modele sorulacak şey değil.

**Yerel modelin avantajı:** script içeriği makineden hiç çıkmıyor. Buluta
gönderilemeyecek veriyi analiz edebiliyoruz — Foundry Local seçiminin karşılığı.

### Faz 8 — Cihaz / çevre birimi kontrolü

*(2026-08-22'de kararlaştırıldı. Not: buradaki "endpoint" **çevre birimi**
anlamında — USB, Bluetooth — iş istasyonu anlamındaki endpoint'ten farklı.)*

**İlke: politika derinliği riskle orantılı olmalı, uniform değil.** Her sınıfa
aynı derinlikte bakmak düşük riskli olanlarda boşa gecikme demek.

**Kapı merdiveni — hangi kapıda durdurduğun cihaz sınıfına göre değişir:**

| Kapı | Nerede durdurur | Lisans |
|---|---|---|
| 0. UEFI/BIOS port kapatma | Fiziksel; en erken ama kör, çalışma anında yönetilemez | — |
| 1. **Kernel DMA Protection** | IOMMU — Thunderbolt/PCIe belleğe hiç dokunamadan durur | Ücretsiz |
| 2. **Device Installation Restrictions** | PnP, sürücü bağlanmadan. Aygıt Yöneticisi'nde **Code 48** | Ücretsiz |
| 3. Device Control / RSAC | Sürücü yüklendikten sonra R/W/E filtresi | Defender for Endpoint |
| 4. BitLocker To Go politikası | Şifresiz depolama salt okunur bağlanır | Ücretsiz |

**Varsayılanımız Kapı 2.** Ücretsiz, çalışma anında yönetilebilir, cihaz hiçbir
sürücü kodu çalıştıramadan durur. Registry:
`HKLM\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions`
(domain'de GPO, domain dışında doğrudan registry).

**⚠️ Kapı 2 "sıfır maruziyet" değil — üç sınırı var:**
1. **Descriptor zaten okundu.** Politika değerlendirilmeden hub sürücüsü VID/PID
   ve sınıf dizelerini okumuş oluyor. Maruziyet sıfır değil, descriptor
   ayrıştırmasıyla sınırlı.
2. **Donanım kimliği cihazın kendi beyanı.** Kötü niyetli aygıt kendini izin
   listesindeki klavye olarak tanıtabilir. Kapı kazara/amatör riske karşı çok
   güçlü, **kararlı saldırgana karşı aşılabilir** → arkasına davranış
   doğrulaması şart.
3. **Bileşik cihazlar.** Tek aygıt hem depolama hem HID arayüzü sunabilir.
   Sınıf bazlı kural depolamayı engelleyip HID'i geçirebilir — BadUSB'nin
   klasik yolu. Kurallar **arayüz bazında** kapsanmalı, cihaz bazında değil.

**DMA yeteneği olanlarda Kapı 2 tek başına yetmez** — Thunderbolt aygıtı sürücü
yüklenmesinden bağımsız belleğe erişir, Kapı 1 açık olmak zorunda.

**Bizim rolümüz:** yeni kapı icat etmiyoruz (kendi filtre sürücümüz = çekirdek
sürücüsü + EV sertifika + MS attestation; kapsam dışı ve yerleşikten kötü).
Üç iş yapıyoruz: (a) Kapı 2 politikasını üretip yönetmek, (b) `Kernel-PnP`
(400/410/442) ve `DriverFrameworks-UserMode` olaylarını izlemek, (c) triyaj.

**❓ Test edilmeli:** Bu politikaların Windows 11 **Home**'da `gpedit` olmadan,
doğrudan registry ile çalışıp çalışmadığı. PnP yöneticisi değerleri registry'den
okuduğu için işlemesi beklenir ama doğrulanmadı.

| Sınıf | Ne yapabilir | Kontrol derinliği | Gecikme bütçesi |
|---|---|---|---|
| Thunderbolt / PCIe | RAM'i doğrudan okur (DMA) | En sıkı: Kernel DMA Protection, kayıtsızsa blok | Katı |
| USB depolama | Zararlı taşır, sızdırır | Tam RWE + BitLocker To Go | ms, deterministik |
| USB HID | **Tuş vuruşu enjekte eder** | İzin listesi + davranış anomalisi | ms + sonradan analiz |
| USB ağ adaptörü | Sahte DHCP/DNS | Varsayılan blok + istisna listesi | ms |
| WPD (telefon/kamera) | Sızdırma kanalı | Okuma/yazma kontrolü | ms |
| Yazıcı | Baskıyla sızdırma | Yazma kontrolü + kayıt | Gevşek |
| Bluetooth | Menzil kısa, servis sınırlı; HID riski sürüyor | Servis GUID izin listesi | Gevşek |
| Ses / kamera | Mahremiyet | Kayıt ve izin | Gevşek |

**Gözden kaçan iki tehdit — ayrı senaryo olarak ele al:**
- **BadUSB / HID enjeksiyonu.** Kendini klavye tanıtan aygıt saniyeler içinde
  komut yazar; klavyeler genelde izin listesinde olduğu için politika geçirir.
  Tespit sınıf kuralıyla değil **davranışla**: depolamayla birlikte beliren
  "klavye", insan üstü tuş hızı.
- **USB ethernet adaptörü.** Takıldığı anda varsayılan ağ geçidini ele geçirip
  DNS'i yönlendirebilir. Zaten ağ trafiğine bakıyoruz — yakalayacak en doğru
  yerdeyiz. Varsayılan **blok** + istisna listesi.

**AI'ın yeri: bağlantı yolunda — evet, senkron.** *(2026-08-22'de düzeltildi;
önceki "AI yolda olmaz" kaydı fazla kaba bir genellemeydi.)*

Doğru ölçüt "güvenlik açısından kritik mi" değil, **olayın sıklığı ve kimin
başlattığı.** AI senkron durabilir, eğer: olay seyrek (dk'da <1), insan
başlatmış, zaman aşımı varsayılanı tanımlı, açılış yolunda gerekmiyor.

| Yol | Sıklık | Başlatan | AI sığar mı |
|---|---|---|---|
| **Cihaz takılması** | günde birkaç | insan | ✅ evet |
| Firewall kuralı *oluşturma* | seyrek | insan | ✅ evet |
| Paket yönlendirme / dosya erişimi / süreç başlatma | sn'de binlerce | otomatik | ❌ |

Windows zaten sürücü bağlayıp birim bağlamak için 1-3 sn harcıyor; oraya 2 sn
eklemek kullanıcı için görünmez.

**Uygulama (çekirdek sürücüsü gerekmiyor, kullanıcı alanında):**
```
1. Registry politikası: sınıf VARSAYILAN REDDET
2. Cihaz takılır → sürücü bağlanmaz, ölü durur (Code 48)
3. Servis Kernel-PnP 400/410 olayını görür
4. Deterministik kontrol → geçerse AI triyajı (~2 sn bütçe)
5. Onaylanırsa donanım kimliği izin listesine eklenir
6. pnputil /scan-devices → yeniden numaralandırma, cihaz canlanır
```

**Şart 1 — Açılış yolu deterministik kalmalı.** Makine açılırken servisimiz
çalışmıyor, Windows PnP çalışıyor. O anda takılı cihazların kararı registry
politikasından gelmek zorunda. **OS politikası taban katman olarak her zaman
duruyor**, AI üstüne binen ikinci kat.

**Şart 2 — Zaman aşımı varsayılanı sınıfa göre:**

| Sınıf | AI bütçesi | Zaman aşımında |
|---|---|---|
| Depolama, ağ adaptörü, WPD | 2-3 sn | **Reddet** |
| Yazıcı, ses, kamera | 2 sn | İzin ver + kaydet |
| HID | **beklemez** | İzin ver, davranışla doğrula |
| Thunderbolt/PCIe | beklemez | Kernel DMA Protection karar verir |

**⚠️ HID istisnası ve ironisi:** Klavye anında çalışmak zorunda — oturum açma,
UAC, BitLocker parolası. AI kararına bağlarsan model yavaşladığında kilitlenme
olur. Ama **BadUSB tam da HID sınıfında**: gecikmeye en az tahammülü olan sınıf
en tehlikelisi. Orada strateji ters: **önce izin ver, sonra davranışla doğrula**
(tuş zamanlaması, yazılan içerik).

**⚠️ HID'in RWE yüzeyi yok — Kapı 3 yapısal olarak uygulanamıyor.**
Read/write/execute bir *depolama* kavramı; klavyede dosya sistemi olmadığı için
kısıtlanacak erişim türü de yok. Depolamada "oku evet, yaz hayır" ince ayarı
mümkün, HID'de ara ton yok. Bu HID'i güvenli yapmıyor — tehdidi veriye erişmek
değil **yazmak**. Kalan iki kaldıraç:
- Bilinen klavyeler için donanım kimliği izin listesi (BadUSB o kimliği taklit
  edebileceği için tek başına zayıf)
- **Asıl kontrol: davranış** — tuş zamanlaması, aygıtın belirme bağlamı
  (depolamayla birlikte mi geldi), yazılan içerik

Yani HID'de politika katmanı **kayıt ve farkındalık** sağlıyor, engelleme
sağlamıyor. Engelleme davranış katmanının işi → **Yol B (küçük ML modelleri)
HID için opsiyonel değil, zorunlu.**

**⚠️ Operasyonel tuzak — "policy ekledim ama etkilemedi":** Device Installation
Restrictions varsayılan olarak **sadece kurulum anında** çalışır. Politikayı
sonradan eklediğinde hâlihazırda takılı ve sürücüsü yüklü cihaz çalışmaya devam
eder. Retroactive uygulamak için ayrı bayraklar var
(`DenyDeviceIDsRetroactive` / `DenyDeviceClassesRetroactive`). Ayrıca aynı
VID/PID daha önce kurulmuşsa sürücü düğümü sistemde kalır ve yeni kurulum
sayılmaz. Uygularken bu bayrakları bilinçli ele al, yoksa politika sessizce
etkisiz görünür.

**⚠️ Lisans gerçeği:** Tam RWE granülaritesi (*Removable Storage Access
Control*) Defender for Endpoint özelliği, lisans ister. Altındaki *Device
Installation Restrictions* politika ile ücretsiz ama sadece blok/izin —
"oku evet, yaz hayır" inceliği yok. **Lisanssız senaryoda RWE vaat etme.**

#### 8a. İki eksenli politika modeli

Politika tek bir kimlikten değil, **iki eksenin kesişiminden** doğuyor:

| Eksen | Soru | Kimlik kaynağı |
|---|---|---|
| **Makine** | Hangi bilgisayarda? | Envanterden **MAC**, sistem içinde AD bilgisayar nesnesi |
| **Çevre birimi** | Hangi aygıt? | Sınıfa göre değişir (aşağıdaki tablo) |

Kural formu: *"şu makinede, şu aygıta, şu süreyle, şu seviyede izin."*

**Sınıf başına kullanılabilir kimlik** — içe aktarma formatı sınıfın sağladığı
anahtarı kabul etmeli, tek bir alan dayatmamalı:

| Sınıf | Kimlik | Birim ayırt eder mi |
|---|---|---|
| **Bluetooth** (fare, klavye, kulaklık) | **BD_ADDR** (48-bit, MAC formatı) | ✅ evet |
| **USB ağ adaptörü** | VID/PID + **MAC** | ✅ evet |
| USB depolama | VID/PID + seri | Seri opsiyonel; ucuz aygıtlarda yok/tekrarlı |
| Kablolu USB HID | VID/PID (seri genelde yok) | ❌ sadece model düzeyi |
| USB yazıcı | VID/PID + seri | Çoğunda var |
| Thunderbolt/PCIe | UUID / donanım kimliği | ✅ genelde var |

> **Not:** Kablolu USB aygıtlarda MAC yoktur — MAC bir ağ arayüzü (IEEE 802)
> kimliğidir, USB farede ağ arayüzü yok. Bluetooth aygıtlarda ise BD_ADDR var
> ve MAC formatında, oradan envanter eşleşmesi doğrudan çalışır.

**⚠️ MAC'i birleştirme anahtarı olarak kullanırken:** Wi-Fi MAC rastgeleleştirme
(Windows gizlilik özelliği) envanter kaydıyla tutmaz; dock istasyonları birden
fazla dizüstüyü aynı Ethernet MAC'iyle gösterebilir; çok NIC'li makinelerde
hangi arayüzün kayıtlı olduğu belirsizdir. MAC'i **içe aktarma anahtarı** olarak
kullan, sistem içinde makineyi AD bilgisayar nesnesiyle takip et.

#### 8b. Helpdesk delegasyonu

| Rol | Yapabildiği | Yapamadığı |
|---|---|---|
| **Helpdesk** | Belirli makine/kullanıcı için cihaz onayı, **süre sınırlı** | Global sınıf muafiyeti, şema değişikliği |
| **Güvenlik** | Global politika, sınıf kuralları, kalıcı izin | — |
| **Denetçi** | Salt okuma, tüm kayıt | Değişiklik |

**Süre sınırlı istisna kilit desen:** "bu aygıta bu makinede 8 saat izin ver."
Helpdesk'in günlük işi zaten bu. Kalıcı izinler güvenlik onayından geçer.
Zorunlu kayıt: **kim onayladı, ne zaman, hangi gerekçeyle.** Rol ataması AD
grubundan gelir.

Envanterden içe aktarma **makine eksenini** doldurur. Seri numarası olmayan
kayıt "model düzeyi izin" olarak açıkça işaretlenmeli — helpdesk ne kadar geniş
bir kapı açtığını görsün.

#### 8c. HID güvenlik alt sistemi

HID'de RWE yüzeyi ve dolayısıyla politika kaldıracı yok (bkz. yukarısı). Ona
ayrı bir sistem kuruyoruz.

**Çözüm: cihazı engelleme, oturumu kilitle.** Kilitlenme paradoksunun çıkışı bu —
klavyeyi engellersen kullanıcı kendini kurtaramaz, ama aygıtın *işe yarayacağı
bağlamı* kaldırabilirsin:

```
Envanterde olmayan HID belirdi
  → cihaza izin ver (kilitlenme yok)
  → OTURUMU ANINDA KİLİTLE
  → uyarı üret
```

Enjekte edilen tuş vuruşları kilit ekranına gider. Meşru kullanıcı parolasını
girip devam eder, 5 saniye kaybeder; saldırganın penceresi kapanır.

**Tepki makine tipine göre değişmeli:**
- Sabit iş istasyonu → oturumu kilitle (klavye nadiren değişir, yanlış pozitif düşük)
- Dizüstü / hot-desk → sadece uyar (sürekli farklı klavye/dock takılıyor)
- Sunucu / kritik makine → kilitle + anlık bildirim

**Üç girdi — hiçbiri tek başına yeterli değil:**

| Girdi | Ne söyler | Tek başına |
|---|---|---|
| Envanter eşleşmesi | Bu model bizim mi, bu makineye zimmetli mi | Zayıf (model düzeyi) |
| Bağlam | Depolamayla birlikte mi geldi, saat, ekran kilitli miydi | Orta |
| Davranış | Tuş zamanlaması insan mı, ne yazıldı | Güçlü |

Envanter eşleşmesi **kesin kapı değil, güven ön bilgisi.** Kurumun hiç satın
almadığı bir modelin belirmesi yine de güçlü sinyal.

Üç zayıf sinyali birleştirip yargıya varmak **AI'ın gerçekten iyi olduğu iş** —
her kombinasyon için kural yazmak kombinatoryal olarak imkânsız.

#### 8d. AI'ın sınıf başına rolü (HID dışı)

HID'de olmayan şey burada var: **RWE granülaritesi.** Yani AI'ın kararı
ikili değil, **seviye** — izin / salt okunur / ret.

Takılma anında AI'ın eline geçenler: üretici ve ürün dizeleri, seri (var mı,
formatı makul mü), **sunduğu arayüzler** (bileşik mi, beyanla tutarlı mı),
makine + kullanıcı + AD grubu + saat, makine ve filo geçmişi, envanter eşleşmesi.

| Sınıf | AI'ın işi |
|---|---|
| **Depolama** | Seri formatı makul mü (sahtelerde boş / "123456789" / tekrarlı), beyan-arayüz tutarlılığı (disk diyen aygıt HID de sunuyorsa güçlü şüphe), bağlam (03:00'te finans makinesine bilinmeyen 512 GB). Çıktı çoğu şüpheli durumda **"bağla ama salt okunur"** — sızmayı keser, kullanıcıyı tam engellemez |
| **USB ağ adaptörü** | "Kullanıcı dock'a taktı" ile "sahte adaptör sokuldu" ayrımı — saf bağlam yargısı |
| **WPD** (telefon/kamera) | Kullanıcının kayıtlı kendi cihazı mı, kurumsal mı kişisel mi. Hassas OU'larda tipik çıktı salt okunur/ret |
| **Bluetooth** | **Servis profili tutarsızlığı** — kulaklık diye tanıtılan aygıt HID veya seri port profili istiyorsa beyan ile talep uyuşmuyor |
| Yazıcı / ses / kamera | Düşük risk, kayıt yeterli. Anomali sadece bağlamda (sunucuya yazıcı takılması) |
| Thunderbolt/PCIe | Kernel DMA Protection karar verir; AI sadece sonradan anlatır |

**🎯 Projenin gerçek üstünlüğü — ağ + cihaz telemetrisi aynı sistemde.**
Sahte ağ adaptörü bağlandıktan sonra ağ modülümüz anında bakabiliyor: varsayılan
ağ geçidi değişti mi, DNS başka yere mi gidiyor. Saf cihaz kontrol ürünü DNS
değişimini görmez, saf ağ ürünü takılma olayını görmez. **İkisini birleştirmek
bizim yapabildiğimiz şey** — bunu ürün anlatısında öne çıkar.

**Çapraz rol 1 — filo geneli örüntü.** Tek makineye bakan hiçbir kural bunu
yakalayamaz: *aynı bilinmeyen VID/PID iki saatte altı makinede belirdi.* Bu
kampanya, tesadüf değil. Merkezi kontrolcü olduğumuz için görebiliyoruz — ve bu,
model düzeyi izin listesinin zayıflığını telafi eden şey.

**Çapraz rol 2 — politika taslağı.** Öğrenme dönemi sonrası: "son 30 günde şu 40
model görüldü, şu 3'ü sıra dışı, işte önerilen izin listesi." Helpdesk sıfırdan
liste yazmıyor.

**⛔ AI'ın sınırı:** Kalıcı politika değiştiremez. Kararı o takılma olayına özel —
izin verir, kaydeder, gerekçesini yazar. Kalıcı izin listesine ekleme **insan
onayı** ister. Böylece yanlış bir karar tek olayla sınırlı kalır, filoya yayılmaz.

---

## 5. AI'ın rol haritası

Değişmeyen çerçeve: **AI yorumlar, açıklar, önerir; infaz eden deterministik
katmandır.** Faz 8'de görüldüğü gibi bu, AI'ın *asla* yolda duramayacağı
anlamına gelmiyor — ölçüt yukarıdaki 6. ilke.

### Faz faz

**Faz 2 — Sysmon / canlı telemetri.** Snapshot'a süreç bilgisi giriyor: hangi
bağlantıyı hangi program açtı. Yeni AI işi: **süreç–hedef makullüğü.**
"`svchost.exe` 45.61.136.12'ye 5 sn'de bir bağlanıyor" — model tipik Windows
süreçlerinin ne yaptığını bilir. Kural yazmanın zor, modelin doğal olarak iyi
olduğu alan.
⚠️ Sınır: model **senin ağının normalini bilmez.** Anormallik tespitini geçmiş
verinin istatistiği yapar, model sapmanın *ne anlama geldiğini* açıklar. Tersi
olursa her yazılım kurulumunda alarm çalar.

**Faz 3 — Akıllı firewall.** Üç katman:
1. Model kural **önerir** — ham `netsh`/PowerShell yazmaz, şablonlu yapı doldurur
   (kaynak, hedef, port, yön, eylem). Ham komut üretimi en hızlı ayağa sıkma yolu.
2. Deterministik doğrulayıcı **patlama yarıçapını** hesaplar: DC erişimini keser
   mi, yönetim portlarını kapatır mı, akan kritik trafiği durdurur mu. Geçmeyen
   öneri operatöre **gösterilmez bile**.
3. Gölge mod → gözlem → onay.

Modelin asıl katkısı kural değil **gerekçe**: operatörün onay verirken okuyacağı
"bu neyi engeller, neyi engellemez" metni.

**Faz 4 — Çoklu kenar yönlendirme. AI gerçek zamanlı karar VERMEZ.** Üç sebep:
her çıkarım saniyeler sürüyor ama karar milisaniye ölçeğinde; modelde
**histerezis yok** (aynı duruma iki kez farklı cevap → yalpalama); maliyet
hatası pahalı (sayaçlı LTE'ye yayın trafiği dökmek faturaya yansır).
Doğru rol: **geriye dönük ayar** ("geçen hafta hangi sınıf hangi kenarda daha
iyi çalıştı, eşikler doğru mu") ve **olay anlatımı** ("14:00'te B kenarına
geçildi çünkü A'da kayıp %4'e çıkmıştı; 40 sn sürdü, 12 akış etkilendi").

**Faz 5 — Honeypot / aldatma. Modelin gerçekten benzersiz katkı yaptığı yer.**
"API davranışını yoklayanlara sahte HTTP döndürme" statik honeypot ile
yapılamaz — saldırgan üç istekte kalıbı anlar. İki gerçek zorluk:
- **Tutarlılık.** Sahte API kendiyle çelişmemeli (ilk istekte `user_id: 4471`
  dönüp ikincide tanımaması tuzağı ele verir) → üretilen sahte dünyanın durumu
  saklanmalı.
- **Gecikme.** 3 sn'de dönen API şüpheli. Çözüm: modeli **istek anında değil
  önceden** kullan — sahte veri kümesini baştan üret, statik servis et; canlı
  üretimi sadece beklenmedik uç noktalar için sakla.

Ters yön: saldırganın etkileşim dökümünü modele verip **"bu kişi ne arıyordu"**
özetini çıkarmak.

**Faz 6 — Endpoint agent'ları.** Cihazlar arası **korelasyon**: "aynı yeni süreç
üç makinede aynı saatte belirdi" — tek cihaza bakınca görünmeyen örüntüler.

**Faz 7 — Bütünlük/zararlı triyajı.** Bkz. Faz 7 bölümü: tek gerçek katkı AMSI
script yorumlama; huni mimarisi zorunlu.

**Faz 8 — Cihaz kontrolü.** Bkz. 8c ve 8d.

### Fazlardan bağımsız iki büyük iş

**1. Geçmişe soru sorma.** Şu anki soru-cevap sadece *anlık* snapshot'ı görüyor;
"geçen hafta en çok hangi cihaz politika ihlali yaptı" sorulamıyor. Modele
**salt okunur sorgu araçları** vermek gerekiyor (SQLite üzerinde). Model artık
pasif yorumcu değil, veriyi kendi çekiyor. Yetki hâlâ sadece okuma.

**2. Olay sonrası rapor.** Dağınık olay kayıtlarını okunabilir zaman çizelgesine
çevirmek. İnsanın en çok vakit harcadığı, modelin en iyi olduğu iş.

### ⛔ AI'a asla verilmeyecekler

| İş | Neden |
|---|---|
| Firewall kuralını onaysız uygulamak | Tek yanlış kural ağı böler; geri alma penceresi dar |
| Gerçek zamanlı yol seçimi | Gecikme + histerezis yokluğu → yalpalama |
| Cihazı ağdan atmak / karantinaya almak | Yanlış pozitifin bedeli üretimi durdurmak |
| Ham komut üretmek (`netsh`, PowerShell) | Enjeksiyon ve syntax hatası riski; şablon kullan |
| Kalıcı politika değişikliği | Yanlış karar tek olayla sınırlı kalmalı, filoya yayılmamalı |
| Hash'e bakıp zararlı kararı vermek | SHA256 içerik hakkında sıfır bilgi taşır; model yine de uydurur |

### Gecikme bütçesi — mimarinin asıl sebebi

*(2026-08-24'te bu makinede ölçüldü. Önceki kayıt "saniye mertebesinde cevap
veriyor" diyordu — doğru ama fazla kaba; asıl belirleyici **üretilen token
sayısı**, ve o Faz 8'in senkron AI kararını doğrudan etkiliyor.)*

Ölçülen (Foundry Local 0.10.3, phi-4-mini, RTX 4060):

| Ölçüm | Değer |
|---|---|
| Token üretimi — CUDA varyantı | **~60 tok/sn** |
| Token üretimi — OpenVINO varyantı (Intel iGPU) | ~9.5 tok/sn |
| Kısa istem → kısa yanıt (119 token) | **2-3 sn** |
| Tam ağ analizi (uzun snapshot → uzun JSON) | **25-27 sn** |
| Modeli belleğe alma (CUDA / OpenVINO) | 10-13 sn / 65 sn, tek seferlik |

**Çıkarım — bütçeyi belirleyen şey model değil, çıktı uzunluğu.** Aynı model
aynı donanımda 2 sn de sürüyor 26 sn de; farkı yaratan kaç token ürettiği.

- **30 sn'lik periyodik analiz:** 26 sn ile sığıyor ama **payı dar**. Çıkarım
  kilidi çakışmayı engelliyor, yine de istem büyürse aralık artırılmalı.
- **Faz 8'in "cihaz takılmasında 2-3 sn AI bütçesi" varsayımı ayakta** —
  *ama koşullu:* triyaj çıktısı kısa tutulmalı (karar + tek cümle gerekçe,
  ~100 token). Analiz istemi gibi uzun JSON istenirse bütçe 10 kat aşılır.
  **Bu bir tasarım kısıtı, ayarlanacak bir sayı değil.**
- **Akış başına / paket başına karar:** hâlâ tamamen imkânsız.

⚠️ **Varyant tuzağı:** `phi-4-mini` takma adı varsayılan olarak OpenVINO
varyantına çözülüyor. NVIDIA GPU'lu makinede bu 6 kat yavaş demek ve sessizce
oluyor — hata vermiyor, sadece yavaşlıyor. `config.yaml` içinde varyant açıkça
sabitlendi (`Phi-4-mini-instruct-cuda-gpu`). Başka makinede ilk bakılacak yer.

AI/kural ayrımı estetik tercih değil, bu bütçenin doğal sonucu.

### Faz 9 — Küçük ML modelleri (hızlı yol)

*(2026-08-22'de "Yol B" olarak tartışıldı; Faz 8'in HID tarafı buna bağımlı
olduğu için **opsiyonel değil**.)*

Önemli ayrım: **hızlı yola sığmayan LLM'ler, genel olarak makine öğrenmesi
değil.** Küçük bir sınıflandırıcı mikrosaniyelerde karar verir ve kritik yola
rahatça girer.

```
Hızlı yol:  kurallar + küçük ML modelleri   (μs–ms)  → tespit ve engelleme
Yavaş yol:  yerel LLM                        (saniye) → yorum, korelasyon, anlatı
```

Adaylar:
- **Tuş vuruşu zamanlaması sınıflandırıcısı** → BadUSB. İnsan yazımının zamanlama
  dağılımı makineden belirgin farklı; klasik ve başarılı bir problem.
  **Faz 8/8c bunsuz çalışmıyor** — politika HID'de engelleme sağlamıyor.
- **Akış özellikleri sınıflandırıcısı** → beacon tespiti (paket aralık
  düzenliliği, boyut varyansı)
- **Cihaz descriptor anomali skoru** → beyan edilen sınıf ile gerçek arayüzlerin
  tutarsızlığı

**Eğitim verisi:** akış tarafında elverişli konumdayız — simülatör zaten etiketli
veri üretebiliyor (`beacon`, `exfil`, `port_scan` senaryoları). Tuş zamanlaması
için ayrı veri toplamak gerekir.

**Değerlendirilmedi:** Yol A (düşük riskli, geri alınabilir kararlarda modele
gerçek yetki — hangi uyarı öne çıkarılacak, politika taslağının otomatik
uygulanıp gözden geçirmeye bırakılması). Masada duruyor.

---

## 6. Teknik borç

*(2026-08-24'te sistematik tarama yapıldı: AST taraması + tüm uçların
çalıştırılması + yük altında log incelemesi. Aşağıdakiler kapatıldı.)*

### ✅ Kapatılanlar

| Borç | Ne yapıldı |
|---|---|
| README `netpilot` olarak güncellenmedi | Başlık, kapsam, mimari şeması, API tablosu, yol haritası güncellendi |
| `config.py` `_build()` iç içe dataclass yolu ölü kod | `get_type_hints` ile çözüldü; iç içe dataclass artık **gerçekten** kuruluyor (testle kanıtlandı) |
| Panel görsel olarak denetlenmedi | Edge headless ile denetlendi, bir okunabilirlik hatası bulunup düzeltildi |
| Foundry sağlayıcısı test edilmedi | Gerçek serviste doğrulandı, 4 kusur çıktı ve düzeltildi |
| `extract_json` yıkıcı fence ayrıştırması | Adaylar sırayla deneniyor, ham metne dönülüyor (14 vaka) |
| `optimizer._check_downlink` kategori hatası | Hat doluluğunu `hog_share_threshold` ile kıyaslıyordu — kaldırıldı |
| `server.py` WebSocket `except Exception: pass` | `log.exception` ile değiştirildi |
| Kullanılmayan importlar | `asyncio`, `TrafficClass`, `time`, `DeviceKind` silindi |
| İstem şişmesi → ONNX bellek hatası | Yuvarlama + sıfır alan atlama + `max_snapshot_chars` sert tavanı; istem 5055 → 3216 karakter |

### ✅ Akış çözücüsü kapsam boşluğu — kapatıldı (2026-08-24)

Sistematik denetimde çıktı: çözücü ağın **yalnızca üçte birini** görüyordu.

```
ölçülen: indirme 182 + yükleme 58 + LAN 331 = 572 Mbps
çözücüye giren: 182 Mbps          görmediği: 390 Mbps
darboğaz raporu: "yok"            gerçek: yükleme hattı %292 dolu
```

Üç ayrı kusur:

1. **`demands_from_signals` yalnız `down_bps` okuyordu.** Yükleme hiç
   modellenmiyordu — üstelik yükleme **daha dar** kaynak (20 vs 200 Mbps) ve
   doygunluk önce orada oluyor.
2. **`lan_targets` hiç doldurulmuyordu.** Topolojideki `nvr` / `srv-file`
   düğümlerine hiçbir talep ulaşmıyordu; o kenarlar ölüydü. Kullanıcının
   "iç ağı optimize et" dediği trafik tam olarak buydu.
3. **Topolojideki yönler fiziksel olarak ters yazılmıştı.** İndirme kapasitesi
   `cihaz → internet` kenarlarına konmuştu. Tek yön modellendiği sürece fark
   etmiyordu; yükleme eklenince yükleme, indirme kapasitesini tüketmeye
   başlayacaktı.

**Yapılan:** `Demand`'a `src` ve `direction` alanları eklendi; talep üretici
üç yönü de (indirme / yükleme / LAN) üretiyor; LAN hedefi cihaz türünden
türetiliyor (kamera→NVR, sunucu→dosya sunucusu); topoloji yönleri
düzeltildi; `pullbacks()` artık yönü de bildiriyor — indirmeyi kısmakla
yüklemeyi kısmak farklı aksiyonlar ve farklı yerde uygulanıyor.

Doğrulama: kapsam %32 → **%100** (555/555 Mbps, 57 talep), darboğaz raporu
yükleme hattını doğru buluyor. Test 10-12 eklendi (asimetrik hat, yükleme
doygunluğu, LAN'ın WAN'ı tüketmemesi).

### ✅ Akış planı artık kalıcı

`flow_plans` tablosu + `GET /api/flow/history`. Saklanan: toplamlar, geri
çekmeler, darboğazlar — "geçen hafta hangi cihaz en çok kısıldı" sorusunu
cevaplayan alanlar. Tam kenar dökümü saklanmıyor (pahalı ve sorulmuyor).

### ✅ Dört açık borç kapatıldı (2026-08-24)

**1. Eşik motoru ile çözücünün çelişkisi.** İkisi de aynı cihaz için hız
kararı üretiyordu, farklı sayılarla: `optimizer.py` "ws-dev-02'yi 70 Mbps'e
sınırla" derken çözücü "8.8 Mbps verilebilir" diyordu. Biri eşikten uydurma,
diğeri hesaplanmış; operatör hangisine bakacağını bilmiyordu.

> **İş bölümü artık net: eşik motoru durumu tespit eder ve uyarır, sayıyı
> çözücü verir.** `optimizer.evaluate()` yalnız uyarı döndürüyor;
> `flowopt.actions_from_plan()` aksiyonları üretiyor ve `optimizer.adopt()`
> ile aynı politika defterinden geçiyor (TTL, tekrar bastırma, uygula/kaldır
> tek yerde kalsın).

Yeni `ActionKind.REROUTE` eklendi — çözücü zaten yol bölmesi hesaplıyordu,
karşılığı yoktu.

⚠️ İlk sürümde `reroute` tespiti yanlıştı: yol üzerindeki **ardışık durakları**
(sw-core → wan → internet) paralel çıkış sanıp tek yollu topolojide bile
"bölündü" diyordu. Doğrusu: gerçek bölünme = tek bir düğümden birden çok
kenara akış çıkması.

**2. `Flow`'a yol alanı.** `Flow.egress` eklendi, `PathAssigner` ile
dolduruluyor.

> **Akış başına atama, paket başına değil.** Tek akışın paketlerini farklı
> gecikmeli iki yola serpiştirmek TCP'yi yavaşlatıyor: sırasız gelen paket
> kayıp sanılıyor, tıkanma penceresi çöküyor. Atama `blake2b` hash'i ile
> deterministik — aynı akış hep aynı çıkışa düşüyor, yani yapışkanlık bedava
> geliyor ve akış ortasında yol değişmiyor. Dağılım planın oranını tutuyor
> (ölçüldü: 60/40 planına karşı %60.2/%39.8).

**3. `class_mix` yön ayrımı.** `class_mix_down` / `_up` / `_lan` eklendi.
Tek karışım kullanmak iki yönde de yanlıştı: ölçümde genel karışım %50 toplu
%50 yayın derken indirme gerçekte %90 yayın, yükleme %90 toplu çıktı.

**4. AI'ın rolü — tespit değil açıklama.** İstem yeniden yazıldı: modele
snapshot + kural motorunun uyarıları + çözücünün kararı veriliyor, ondan
**açıklama** isteniyor.

| Ne | Önce | Sonra |
|---|---|---|
| Önem derecesi | model veriyordu (`0.175 doluluk → critical`) | koddan, kaynağındaki olgudan |
| Öneri türü | `rate_limit` vb. sayılı aksiyon | her zaman `advise`, sayısız |
| AI aksiyonu | politika defterine giriyordu | 0 — çözücü tek kaynak |
| AI uyarısı | ayrı uyarı üretiyordu | üretmiyor (aynı olay iki kez görünürdü) |

Derece eşleştirmesi kök bazlı: alt dize karşılaştırması Türkçe eklerde
tutmuyordu ("hattında" ≠ "hattındaki"). Kelimelerin ilk 5 harfi alınıyor.

Gerçek modelle doğrulandı: 0 AI aksiyonu, uydurma derece yok, uyarı kaynağı
yalnız `optimizer`, özet tutarlı.

### 🔴 Açık kalanlar

*(2026-08-26'da elden geçirildi. Önceki liste **bayattı**: üç maddesinin üçü de
kapanmış işleri açık gösteriyordu — infaz katmanı 4b'de, talep tahmini 4e'de,
sınıflandırma 4f'de yazıldı. Bu dosya oturumlar arası hafıza olduğu için bayat
bir borç listesi yapılmış işi yeniden yaptırır; bu yüzden madde kapanınca
**burası da güncellenmeli.**)*

**Faz 2 geldi, ama kapsamı sınırlı:**

- **Canlı kaynak tek makineden bakıyor.** `LiveSource` bu makinenin
  trafiğini görüyor; ağın tamamını görmek için yansıtma (SPAN) portu ya da
  cihaz başına ajan gerekiyor — ikisi de doğrulanmadı.
- **Süreç çözülme %70.** Kalan kayıp yoklama arasında doğup ölen kısa
  ömürlü bağlantılar; Sysmon Event 3 (olay tabanlı) tam bu boşluğu kapatır.
- **`rtt_ms` / `retransmits` canlıda 0.** Hat *kalitesi* kuralları canlı
  modda kör; hat *doluluğu* kuralları çalışıyor.

**Yazıldı ama gerçek donanımda/veride doğrulanmadı:**

- **İnfaz katmanı gerçek cihazda denenmedi.** Üç katman yazıldı ve gölge modda
  ölçüldü (4b), ama doğrulanan şey **üretilen komut metni**; `tc` / QoS
  politikasının cihazdaki davranışı değil. Gerçek router yok — VirtualBox
  kurulu, Linux router sanal makinede sınanabilir.
- **Talep tahmini simülasyonda etkisiz** (4e). Simülatör hat tavanını
  uygulamadığı için sim'de ölçüm zaten talep. Katman `t_demand.py` üzerinde
  kanıtlandı; asıl değeri gerçek ağda ortaya çıkacak.
- **Sınıflandırma gerçek trafikte ölçülmedi** (4f). Doğruluk simülatöre karşı
  %97-98; gerçek ağda katalogda olmayan uygulamalar varsayılana düşecek.
  Canlı modda sınıflandırıcı artık **yazar** durumda (4w) ama canlıda
  karşılaştırılacak doğru etiket olmadığı için isabeti ölçülemiyor — ancak
  elle etiketlenmiş bir örneklemle ölçülebilir.

**Küçük, bloklamayan borçlar:**

- **AI payı `MAX_ROWS`=10 ile tavanlı** (4j/4k): canlıda 54 talebin 10'u modele
  gidiyor, akışın %38'i. Denenmemiş kaldıraç: modele 10'ar 10'ar birkaç turda
  sormak.
- **Sayaçlı hatta model parayı değil gecikmeyi optimize ediyor** (4h).
- **`--s1..--s5` beşlisi dotanopide referansın %70 altında** (4s). Metin yedeği
  olduğu için acil değil.
- **Gerçek ekran okuyucu (NVDA/JAWS) ile denetim yapılmadı** (4p).
- **Windows'ta yol kararı uygulanamıyor** (RRAS politika tabanlı yönlendirme
  yok). Mimari kısıt, borç değil — ama "uygulama-farkında yönlendirme" vaadi
  saf Windows'ta verilemez.

### Doğrulama durumu

*(2026-08-26: testler `tests/` altına alındı — artık bu tablo elle sayılan
bir iddia değil, `python tests/kos.py` ile tekrarlanabilir bir ölçüm.)*

```
python tests/kos.py            32/32 GECTI    240 sn
python tests/kos.py --servis   15/15 GECTI   1397 sn  (yerel model gerektirir)

derleme · panel JS · AST taramasi                      temiz
16 API ucu + WebSocket, yuk altinda                    200
AI cagrilari yuk altinda                               0 x 500
```

Varsayılan koşudakiler (32): `t_actions t_api t_bosluk t_classify
t_classify_sweep t_classify_tohum t_cvd t_cvd_ara t_cvd_dogrula t_cvd_olc
t_demand t_enforce t_enforce_scope t_floors t_floors_shape t_flowopt
t_hedef_birim t_json t_kaynak t_kazanc t_kurtarma t_live t_mix t_optimallik
t_parse t_path t_policy t_random_topo t_sabitler t_sev2 t_snap t_targets`

Model gerektirenler (15): `t_ai_diag t_ai_flow t_ai_flow2 t_ai_ham
t_ai_hibrit t_ai_policy t_ai_politika t_ai_random t_ai_tekrar t_ai_yeni
t_hedef t_json_hata t_kesilme t_rows t_zincir`

## 7. Çalışma anlaşmaları

- **Dil:** kod yorumları, panel ve CLI çıktısı **Türkçe**. GitHub description
  ve topics **İngilizce** (keşfedilebilirlik için).
- **Git:** commit ve push'u **kullanıcı kendisi yapıyor**. Aksi söylenmedikçe
  ben git komutu çalıştırmıyorum (salt okuma kontroller hariç).
- **Dal:** `main`.
- **Doğrulama:** test edilmemiş kodu "bitti" diye raporlama. Neyin
  doğrulandığını, neyin doğrulanmadığını açıkça yaz.
- **OneDrive uyarısı:** depo `OneDrive\Desktop` altında. OneDrive `.git`
  klasörünü de senkronluyor; ileride garip git hataları çıkarsa ilk şüpheli bu.
  Kalıcı çözüm projeyi OneDrive dışına taşımak.
