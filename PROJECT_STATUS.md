# netpilot — Proje Durumu

> **Bu dosya oturumlar arası hafızadır.** Yeni bir oturuma başlarken önce burayı
> oku, sonra devam et. Bir iş bitince veya bir karar değişince burayı güncelle.

**Son güncelleme:** 2026-08-24
**Repo:** https://github.com/OmerArdaZorlu/netpilot
**Paket adı:** `ntc` (repo adı `netpilot` ile kasıtlı olarak farklı — içeride
onlarca `from ntc...` import var, değiştirmek gereksiz kırılganlık)

---

## 1. Nerede duruyoruz

**Faz 1 tamamlandı ve çalışıyor:** trafik izleme + optimizasyon çekirdeği.

```
Simulator → Metrics → (Optimizer ‖ AI Analyst) → Controller → API + Panel
```

| Modül | Dosya | Durum |
|---|---|---|
| Yapılandırma | `ntc/core/config.py` | ✅ YAML + `NTC_` ortam değişkeni ezmesi |
| Ortak tipler | `ntc/core/models.py` | ✅ Flow, Device, Alert, Action, LinkStats |
| Olay yolu | `ntc/core/bus.py` | ✅ async pub/sub |
| Uygulama/cihaz katalogu | `ntc/traffic/catalog.py` | ✅ 16 uygulama, 10 cihaz profili |
| Trafik üreteci | `ntc/traffic/simulator.py` | ✅ 6 senaryo tetiklenebilir |
| Metrikler | `ntc/traffic/metrics.py` | ✅ kayan pencere, WAN/LAN ayrı |
| Optimizasyon motoru | `ntc/traffic/optimizer.py` | ✅ 5 kural, politika defteri, uyarı soğutma |
| LLM sağlayıcı | `ntc/ai/provider.py` | ✅ zincir: foundry → ollama → mock |
| Foundry Local | `ntc/ai/foundry.py` | ✅ 2026-08-24'te gerçek serviste doğrulandı (3 kusur çıktı, düzeltildi) |
| AI analisti | `ntc/ai/analyst.py` | ✅ snapshot, analiz, soru-cevap, normalizasyon |
| Kalıcılık | `ntc/storage/db.py` | ✅ SQLite, 5 tablo, 24 saat saklama |
| Orkestrasyon | `ntc/controller.py` | ✅ 4 async döngü |
| API + WebSocket | `ntc/api/server.py` | ✅ |
| Panel | `ntc/dashboard/index.html` | ⚠️ çalışıyor, **görsel olarak denetlenmedi** |
| CLI | `ntc/cli.py` | ✅ serve / watch / analyze / ask / doctor |

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
- Foundry Local uçtan uca (2026-08-24): `doctor` → sağlayıcı bağlanıyor,
  `analyze` → gerçek `/v1/chat/completions` yanıtı. Soğuk yol (model bellekte
  değilken) 43 sn, sıcak koşu 25-27 sn. Tembel yükleme ve çıkarım kilidi
  bellekten çıkarma testiyle doğrulandı.

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

### Tasarımı konuşuldu, kodu yazılmadı

4b. **İnfaz katmanı — çözücünün kararını uygulayan taraf**

   Optimize edici bir *hedef durum* üretiyor; onu uygulayan hiçbir şey yok.
   `applied` alanı yalnızca bir boolean, tüketen kod yok. Kilitli karar QoS
   için `Set-NetQosPolicy`.

   **⚠️ Kayıt boşluğu:** kullanıcı "ağın girişinde ortak bir cache, AI ona
   göre önceliklendirir" diye bir tasarım hatırlıyor; bunun kaydı hiçbir
   yerde yok (kod, bu dosya, README, git geçmişi arandı). Kuyruk mu içerik
   önbelleği mi olduğu netleşmedi. **Netleşince buraya yaz.**

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

- **İnfaz katmanı yok.** Plan hâlâ bir *hedef durum*. Mimari kararlaştırıldı:
  kenarda (Windows domain) QoS politikası + DSCP damgası, çekirdekte router.
  Router'lar da kontrolümüzde varsayılıyor. Elimizde fiziksel cihaz yok;
  VirtualBox kurulu olduğu için Linux router sanal makinede test edilebilir.
- **Talep = ölçülen, istenen değil.** Doygun hatta ölçüm körleşiyor. Çözümü
  konuşuldu: cihaz başına *profil* (boş saatteki hız, transfer toplam boyutu),
  anlık ölçüm değil. `db.py` 24 saatlik seriyi zaten tutuyor, kullanılmıyor.
- **Sınıflandırma gerçek veride yok.** Simülatör sınıfı kendisi söylüyor.
  Kademeli tasarım konuşuldu: süreç adı (Sysmon) → hedef IP → port → akış
  şekli → emin değilsen "etkileşimli". Simülatörün etiketleriyle doğruluğu
  ölçülebilir.

### Doğrulama durumu

```
9 test paketi                                          GECTI
  t_parse   Foundry uc kesfi (7 vaka)
  t_json    JSON cikarma (14 vaka)
  t_targets hedef dogrulama + advise zorlamasi
  t_sev2    derece eslestirmesi (Turkce ek dahil)
  t_flowopt akis cozucusu (12 senaryo)
  t_mix     yon basina sinif karisimi
  t_actions plandan aksiyon uretimi
  t_path    yol atama: yapiskanlik + oran
  t_snap    snapshot butcesi ve budama

derleme · panel JS · AST taramasi                      temiz
16 API ucu + WebSocket, yuk altinda                    200
AI cagrilari yuk altinda                               0 x 500
```

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
