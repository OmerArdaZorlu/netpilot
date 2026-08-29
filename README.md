# netpilot

Ağ trafiğini ölçen, **hangi trafiğin hangi hattan ne kadar akacağını doğrusal
programla hesaplayan** ve bu kararı cihaz komutuna çeviren bir kontrol
çekirdeği. Trafik ya simüle edilir ya da gerçek arayüzden yakalanır. Yerel bir
LLM kararı operatöre açıklıyor ve çözücünün hedefini kuruyor — sayıları
hesaplayan matematik, açıklayan model.

> **Eşik denetimi optimizasyon değildir.** "Doluluk %80'i geçti → uyar" bir
> termostattır; sistemde o da var ama ayrı bir iş yapıyor. Optimizasyon,
> ölçülen talepleri ağın kapasitesine karşı çözüp cihaz başına sayı
> üretmektir. **Eşik motoru durumu tespit eder, sayıyı çözücü verir.**

> **Paket adı `ntc`, repo adı `netpilot`.** Kasıtlı: içeride onlarca `from ntc…`
> import var, yeniden adlandırmak gereksiz kırılganlık.

**Çalışan ve ölçülmüş olanlar:**

- **Trafik toplama ve metrikler** — WAN ve LAN ayrı ölçülür
- **Trafik sınıflandırma** — DPI'sız, katmanlı: süreç adı → tek-sınıflı port →
  hedef IP → akış şekli → varsayılan
- **Talep tahmini** — doygun hatta ölçülen hız zaten tavandır; gerçek talep
  hattın boş olduğu anlardan öğrenilir
- **Eşik tabanlı kural motoru** — tıkanma tespiti ve uyarılar
- **Akış optimize edici** — çok mallı akış problemini doğrusal programla
  çözer: hangi trafik hangi çıkıştan, kimden ne kadar geri çekilmeli
- **Topoloji modeli + üreteci** — tohumlu, rastgele ama gerçekçi ağlar; sistem
  elle yazılmış tek bir topolojiye bağlı değil
- **İnfaz katmanı** — planı cihazdan bağımsız politikalara, onları `tc` /
  `New-NetQosPolicy` komutlarına çevirir; farkı uzlaştırır (**gölge modda**)
- **AI politika katmanı** — duruma göre çözücünün hedefini kurar; ürettiği her
  alan doğrulanır, geçersizse reddedilir
- **Yerel LLM analisti** — Foundry Local / Ollama / kural tabanlı yedek
- **Canlı yakalama modu** (`mode: live`) — gerçek trafik akışlara çevrilir;
  hacim paket yakalamadan, süreç kimliği bağlantı tablosundan gelip 5'li
  üzerinden birleşir
- **Panel + API** — 31 HTTP ucu, WebSocket, canlı dashboard
- **Simülasyon ortamı** — 6 tetiklenebilir senaryo
- **Test paketi** — 47 dosya; `python tests/kos.py` → 32/32

> **İki ayrı "canlı" var, karıştırmayın:**
> `mode: live` → **trafiği gerçekten yakalar** (çalışıyor).
> `enforce.mode: canli` → **komutu gerçekten çalıştırır** (bilerek bağlı değil).
> İnfaz gölge modda kalıyor çünkü üzerinde doğrulama yapabileceğimiz gerçek bir
> cihaz yok; sınanmamış çalıştırma kodu "hazır" görünür. Komut *metni* teste
> karşı doğrulandı, komutun cihazdaki *davranışı* doğrulanmadı.

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

### Testler

```bash
python tests/kos.py            # çevrimdışı testler
python tests/kos.py --hepsi    # yerel model / ayakta sunucu isteyenler dahil
python tests/kos.py -k flow    # adı eşleşenler
```

Her test ayrı süreçte koşar ve UTF-8 zorlanır — Windows konsolu cp1252 olduğu
için Türkçe çıktı testleri bir kez **kodda hata yokken** başarısız göstermişti.

---

## Mimari

```
              ┌──────────────┐
              │  FlowSource  │  mode: simulation -> sentetik akışlar
              │              │  mode: live       -> yakalama ⋈ bağlantı tablosu
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

**Ölçülen hız, doygun hatta talebin kendisi değildir.** Bir cihaz 200 Mbps
isterken hat doluysa 33 Mbps ölçülür — ve o sayıyı "talep" saymak sistemi tam
da tıkanma anında körleştirir: "33 istedi, 33 verdim, memnun". `demand.py`
hat **boşken** ölçülen değerleri cihaz başına tepe olarak saklıyor ve hat
dolduğunda oradan okuyor. Ölçüldü (gerçeği bildiğimiz kurguda): toplam mutlak
hata 200.0 → **0.0 Mbps**, görülen eksik 0 → **200 Mbps**.

Tepeyi körlemesine kullanmıyoruz: düşük ölçümün iki zıt sebebi olabilir
(cihaz kısıtlanıyor / cihaz boşta) ve ayırt eden sinyal **adil payına
dayanmış mı** olduğu. Sinyal yoksa şişirme yapılmıyor — ayıramadığımızda
hayali talep üretmek başkasının payını çalmak demek.

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

**Akışı AI kurar, LP hakemlik eder.** Model her isteğe kaç Mbps verileceğine
ve hangi bacaktan akacağına kendisi karar veriyor; çıktısı üç kısıttan
geçiriliyor (talebi aşma, kapasiteyi aşma, var olmayan cihaz/bacak) ve
çözücünün ilk turuna giriyor. LP karar verici değil: modelin kararını ağa
oturtuyor, yanıtsız kalanı dolduruyor, kapasiteyi aştırmıyor.

```
topoloji + talepler ──► AI ──► doğrulayıcı ──► LP (hakem) ──► akış
```

Ölçüldü: model **tek başına** optimumun %22'sini geçiriyor; hibritte kayıp
**0.0 Mbps** ve akışın %54–89'u modelin kararı. Satırlara kimlik verip
kimlikle cevap istemek payı %16–24'ten %54–89'a çıkardı — model cihaz adını
yeniden yazarken `lan`'ı `down`, bacak adını `indirme` yazıyordu.

**AI çözücünün hedefini de kurar — sayısını değil.** Çözücü verilen hedefe göre
matematiksel olarak en iyi cevabı bulur, ama *hedefin kendisi* bir olgu değil
karar: "realtime her zaman bulk'u yener" gece 03:00'te yanlış, "gecikme
paradan baskın" sayaçlı hat devredeyken yanlış. Bu kararlar `flowopt.py`
içinde sabit tablolardı; artık `flowpolicy.py` içinde ve duruma göre AI
kuruyor:

```
durum (ölçüm + saat + hat durumu) ──► AI ──► FlowPolicy ──► LP ──► akış
                                             (hedef)              (sayılar)
```

**Modelden sayı istenmiyor, kelime isteniyor.** Ölçüldü (4 durum, gerçek
phi-4-mini): sayı istendiğinde ağırlıklara 4/4 hiç dokunmadı, tabanları
%14.6'da eşitledi, üç sınıfı sıfırladı. Kategorik seçeneğe (`"dusuk" |
"normal" | "yuksek"`, profil adı) çevrilince aynı dört durumda 1/4 → 2/4'e
çıktı ve ürettiği sayılar makul hale geldi. Model niyeti anlıyor, sayıya
çeviremiyor — bu yüzden sayıyı kod koyuyor.

**Modelin ürettiği her alan doğrulanıyor.** Sıralama beş sınıfın permütasyonu
olmak zorunda, profil adı listede olmalı, ağırlık seviyesi tanınmalı, taban
toplamı %60'ı aşamaz. Geçersiz çıktı sessizce kabul edilmiyor: gerekçesiyle
reddedilip **mevcut hedef korunuyor.** (Ölçümde model `"gedemek"` diye bir
profil uydurdu; kapı tuttu, ağa hiçbir şey gitmedi.) Varsayılana dönmek yerine
mevcudu korumak bilinçli — hedefi tam da modelin güvenilmediği anda
sıfırlamak ağı sallardı.

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
| `GET /api/classify` | Sınıflandırma denetimi: uyum oranı, katman başına pay ve isabet |
| `GET /api/alerts` · `GET /api/actions` | Uyarılar / politikalar |
| `POST /api/actions/{id}/apply` · `/revert` | Politika onayı / kaldırma |
| `GET /api/ai/health` · `GET /api/ai/report` · `GET /api/ai/reports` | AI sağlığı ve raporlar |
| `POST /api/ai/analyze` | Beklemeden analiz ettir |
| `POST /api/ai/ask` | Serbest metin soru-cevap |
| `GET /api/ai/snapshot` | Modele giden ham bağlam (şeffaflık/hata ayıklama) |
| `GET /api/flow/plan` | Son akış çözümü: tahsisler, geri çekmeler, darboğazlar |
| `POST /api/flow/solve` | Beklemeden yeniden çöz |
| `GET /api/flow/history` | Geçmiş akış planları (toplamlar, geri çekmeler, darboğazlar) |
| `GET /api/flow/topology` | Topoloji grafiği (düğümler + kenarlar) |
| `GET /api/flow/ai` | Modelin son akış önerisi + akışın ne kadarı onun kararı |
| `GET /api/flow/demand` | Talep profilleri: hangi cihaz boş saatte ne çekiyor |
| `GET /api/flow/policy` | Çözücünün şu anki hedefi: kim koydu, neden, son turda ne oldu |
| `POST /api/flow/policy/refresh` | Hedefi beklemeden yeniden kur |
| `GET /api/enforce/state` | İnfaz durumu: sürücü, mod, kurulu kurallar, son uzlaştırma |
| `GET /api/enforce/policies` | Son plandan çıkan **istenen** politika kümesi |
| `GET /api/enforce/preview` | Kuru çalıştırma: hepsi onaylı olsa hangi komutlar çıkardı |
| `GET /api/sim/scenarios` · `POST /api/sim/scenario` · `DELETE /api/sim/scenarios` | Senaryo listele / tetikle / temizle. Senaryo desteklemeyen kaynakta (canlı mod) tetikleme **409** döner |
| `WS /ws` | Canlı metrik / uyarı / aksiyon / rapor akışı |

---

## Yapılandırma

`config.yaml` — her alanın koddaki bir varsayılanı var. Ortam değişkeniyle de
ezilebilir:

```bash
NTC_AI__MODEL=llama3.2  NTC_API__PORT=9000  python -m ntc serve
```

---

## Ölçümler

Buradaki her satır çalıştırılmış bir ölçüme dayanıyor; tahmin yok.

| Ne | Sonuç |
|---|---|
| **Optimizasyon kazancı** (220 Mbps talep, 100 Mbps'lik bacaklar) | tek hat 100 Mbps (%45) → iki hat 200 Mbps (%91) = **x2.00** |
| Aynı ölçüm, 12 rastgele mimaride | ortalama **x1.34**, en yüksek x1.63, tek çıkışlı ağlarda dürüstçe x1.00 |
| **AI akış tahsisi**, 20 rastgele mimari | 19'unda tam cevap, LP'ye göre kayıp **0.0 Mbps**, akışın %54–89'u modelin kararı |
| **Sınıflandırma doğruluğu**, 8 tohum / 15.070 akış | IP tablosu yokken **%97.4**, eksik tabloyla **%98.3** |
| **Talep tahmini** (gerçeği bilinen kurgu) | toplam mutlak hata 200.0 → **0.0 Mbps** |
| **İnfaz** (tıkanma senaryosu, linux sürücüsü) | onaysızken **0** kısan kural; onaydan sonra 1 kural / 2 komut; ikinci turda **0 komut** (fark yok) |
| **Canlı yakalama** (gerçek ağ, yönetici hakkı olmadan) | 111 akış / 577 paket; süreç adı çözülme soğuk başlangıçta %70, sürekli yoklamada **%95.5** |
| **Panel** | 16/16 kart dolu, **0 JS hatası**; erişilebilirlik ve renk körlüğü denetiminden geçti |
| **Testler** | çevrimdışı **32/32** (193 sn) + yerel model gerektiren **15/15** |

---

## Neyin doğrulanmadığı

Bu bölüm bilerek var: doğrulanmamış bir şeyi "çalışıyor" diye saymak, projenin
kendi kuralına aykırı.

- 🔴 **Canlı modda topoloji hâlâ kurgu.** Paketler gerçek, ama çözücünün
  üzerinde çalıştığı grafik `config.yaml`'daki üretilmiş ağ. Gerçek topoloji ve
  kapasite yazılmadan canlı moddaki darboğaz çıktısı okunmamalı.
- 🔴 **Canlı mod tek makineden bakıyor.** Ağdaki öteki cihazların trafiği
  görünmüyor; bunun için yansıtma (SPAN) portu ya da cihaz başına ajan gerekir.
- 🔴 **İnfaz gerçek cihazda denenmedi.** Üretilen komutların *metni* test
  edildi, cihazdaki davranışı edilmedi.
- ⚠️ **`rtt` ve yeniden gönderim canlıda ölçülmüyor** (paket sayaçlarından
  çıkarılamaz, uydurulmuyor) → hat *kalitesi* kuralları canlı modda kör; hat
  *doluluğu* kuralları çalışıyor.
- ⚠️ **Canlı sınıflandırmanın isabeti ölçülmedi** — karşılaştırılacak doğru
  etiket yok. Yukarıdaki %97–98 simülasyona karşı ölçüldü.
- ⚠️ **Windows'ta yol kararı uygulanamıyor:** RRAS politika tabanlı yönlendirme
  yapmıyor. Uygulama-farkında yönlendirme saf Windows'ta vaat edilemez.
- ⚠️ **Domain gerektiren hiçbir şey test edilmedi** (AD kimlik, NPS/802.1X, GPO
  ile dağıtım) — geliştirme makinesi Windows 11 Home.

---

## Yol haritası

- [x] **Faz 1 — Trafik izleme + kural motoru**
- [x] **Akış optimizasyonu** — topoloji modeli + çok mallı akış çözücüsü
- [x] **İnfaz katmanı** — politika nesneleri + `tc`/`New-NetQosPolicy`
      sürücüleri + fark uzlaştırıcı. Gölge modda; canlı çalıştırma gerçek
      cihaz üzerinde doğrulanana kadar bağlanmayacak.
- [x] **Akış kaynağı soyutlaması** — `FlowSource` protokolü; `mode` ayarı
      kaynağı gerçekten seçiyor, bilinmeyen değerde açılışta hata veriyor.
- [x] **Faz 2 — Canlı yakalama:** `LiveSource` gerçek trafiği akışlara
      çeviriyor. Hacim paket yakalamadan (Npcap/scapy), süreç kimliği bağlantı
      tablosundan (`psutil`) geliyor ve 5'li üzerinden birleşiyor — Windows'ta
      ikisini tek kaynaktan almak mümkün değil (Sysmon bağlantı olayında bayt
      alanı yok, yakalamada süreç yok).

**Sıradaki üç iş — bunlar bitmeden canlı moddaki çıktılar yorumlanmamalı:**

- [ ] **Gerçek topoloji ve kapasite** — canlı modda çözücü hâlâ üretilmiş bir
      grafik üzerinde çalışıyor
- [ ] **Çok cihazlı görünürlük** — yansıtma portu ya da cihaz başına ajan
- [ ] **İnfazın gerçek cihazda doğrulanması** — sanal makinede Linux router

**Sonraki fazlar:**

- [ ] **Faz 3 — Akıllı firewall:** kural motoru + LLM'in trafik bağlamına bakıp
      dinamik kural üretmesi; kurallar önce gölge modda değerlendirilir
- [ ] **Faz 4 — Honeypot + deception:** sahte servisler, tarama yapanları
      yakalama, API davranışını yoklayanlara tutarlı sahte HTTP yanıtları
- [ ] **Faz 5 — Endpoint agent'ları:** cihazlara dağıtılan ajanlar, süreç ve
      bağlantı telemetrisi, merkezi komuta

---

## Kapsam ve sorumluluk

Bu araç, **yönetim yetkisine sahip olduğun** ağlar için tasarlandı. `mode: live`
gerçek paketleri yakalar — çalıştırdığın ağın sahibi ya da yetkilendirilmiş
yöneticisi olduğundan emin ol. Varsayılan mod `simulation`'dır ve hiçbir gerçek
arayüze dokunmaz; infaz da varsayılan olarak gölge modda, yani hiçbir komut
çalıştırılmaz.
