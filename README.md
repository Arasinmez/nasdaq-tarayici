# Nasdaq Tarayıcı — sinyal botu

Nasdaq hisselerini tarar, bir giriş kurulumu oluşunca **Telegram'dan haber verir**.
**Emir göndermez, paranla ilgili hiçbir şeye dokunmaz.** Alıp almamak senin kararın.

## Ne yapar?

| Strateji | Zaman dilimi | Ne arar |
|---|---|---|
| Kırılım | Günlük | 20 günlük zirvenin, ortalamanın 1.5 katı hacimle, 50 günlük ortalamanın üstünde kapanışla kırılması |
| Trendde geri çekilme | Günlük | Yükselen trendde (fiyat > 200G, 50G > 200G) RSI'ın 40'ın altından yukarı dönmesi |
| Açılış aralığı kırılımı | 5 dakika | İlk 15 dakikanın tepesinin yüksek hacimle ve VWAP üstünde kırılması (11:30 NY'ye kadar) |

Her sinyalde **fiyat, stop, hedef ve nedenleri** yazar. Sonra o sinyali takip eder:
stop ya da hedef gelince ikinci bir mesaj atar ve sonucu `veri/sonuclar.csv` dosyasına kaydeder.
Böylece botun gerçekte işe yarayıp yaramadığını zamanla görürsün.

Örnek mesaj:

```
🟢 GİRİŞ SİNYALİ — NVDA
Strateji: Açılış aralığı kırılımı (5 dk)

Fiyat: 101.60 $
Stop: 100.50 $ (−1.1%)
Hedef: 103.80 $ (+2.2%)

Neden:
• İlk 15 dakikanın aralığı 100.00–101.00 $; üst sınır kapanışla kırıldı
• Kırılım mumunun hacmi günün ortalamasının 3.9 katı
• Fiyat VWAP'ın (100.93 $) üstünde

Az önce kapanan 5 dakikalık mumda oluştu.
🕒 10:00 New York · 18:00 Türkiye
```

## "7/24" nasıl çalışıyor?

Bot 7/24 açık kalır ama Amerikan borsası 7/24 açık değil. Normal seans New York saatiyle
09:30–16:00. Türkiye saatiyle **Kasım–Mart arası 17:30–00:00**, Mart–Kasım arası 16:30–23:00
(ABD yaz saati yüzünden). Bot bunu kendisi hesaplar, resmî tatilleri de bilir.

- **Seans açıkken:** 5 dakikada bir tüm listeyi tarar.
- **Kapanıştan 15 dk sonra (TR ~00:15):** günlük tarama yapar, yeni sinyalleri ve bir özet gönderir.
- **Gece ve hafta sonu:** bekler. Açılışta "Piyasa açıldı" mesajı atar, böylece botun çalıştığını bilirsin.

## Bilgisayarsız çalıştırma: GitHub Actions (ücretsiz)

Bot GitHub'ın sunucularında, hafta içi seans boyunca 5 dakikada bir kendiliğinden çalışır.
Bilgisayarın açık olması gerekmez. Her şey telefondan ayarlanabilir.

1. **Telegram botunu oluştur:** Telegram'da **@BotFather** → `/newbot` → isim ver → sana
   verdiği **token**'ı kopyala. Sonra yeni botunu aç ve ona bir mesaj yaz ("selam").
2. **Chat id'ni bul:** Telefonun tarayıcısında şu adresi aç (TOKEN yerine kendi token'ın):
   `https://api.telegram.org/botTOKEN/getUpdates`
   Sayfada `"chat":{"id":123456789` gibi bir yer göreceksin. O sayı senin chat id'n.
3. **Depodaki gizli ayarlar:** GitHub'da depo → **Settings → Secrets and variables → Actions →
   New repository secret**. İki tane ekle:
   - `TELEGRAM_BOT_TOKEN` → token
   - `TELEGRAM_CHAT_ID` → chat id
4. **Dene:** Depoda **Actions** sekmesi → "Nasdaq tarayici" → **Run workflow** →
   `telegram-test` seç → çalıştır. Telegram'a "bağlandı" mesajı gelmeli.
5. Bu kadar. Hafta içi seans saatlerinde kendiliğinden çalışır. Sinyal geçmişi depodaki
   `veri/` klasörüne kaydedilir.

**Bilmen gerekenler:**
- **Herkese açık (public) depo** ücretsiz ve sınırsız; 5 dakikada bir tarar. Depon herkese açık
  olursa izleme listen ve sinyal geçmişin görünür. Token'ın görünmez, çünkü "secret" olarak saklanır.
- **Gizli (private) depo** ücretsiz planda ayda 2.000 dakikayla sınırlı. Bu yüzden
  `.github/workflows/tarayici.yml` içindeki `*/5`'i `*/10` yap (10 dakikada bir).
- GitHub zamanlanmış işleri yoğun saatlerde birkaç dakika geç başlatabilir. Gün içi kırılım
  15 dakikadan eskiyse bot geç kalmış sayar ve mesaj atmaz (`taze_dakika` ayarı). Günlük
  sinyaller bundan etkilenmez.
- Yahoo bazen bulut sunucularından gelen istekleri sınırlayabilir. Sık sık "veri alınamadı"
  görürsen API anahtarlı bir veri kaynağına geçmek gerekir.
- Bir şey bozulursa GitHub sana e-posta atar. Actions sekmesindeki kayıtlardan ne olduğunu görebilirsin.

## Alternatif: kendi bilgisayarında çalıştırma

1. **Python 3.10 veya üstü** kur: python.org → Downloads. Windows'ta kurulumda
   **"Add Python to PATH"** kutusunu işaretle.
2. Bu klasörü bilgisayarına çıkar, içinde bir terminal aç (Windows: klasörde adres
   çubuğuna `cmd` yazıp Enter).
3. Gerekli paketleri kur:
   ```
   pip install -r requirements.txt
   ```

### Telegram kurulumu (bilgisayarda)

1. Telegram'da **@BotFather**'ı aç, `/newbot` yaz, bota bir isim ver.
   Sana `123456:ABC...` gibi bir **token** verecek.
2. Yeni botunu Telegram'da aç ve ona herhangi bir mesaj yaz (örneğin "selam").
3. Token'ı kaydet:
   - **Windows:** `setx TELEGRAM_BOT_TOKEN "123456:ABC..."` sonra terminali kapatıp yeniden aç.
   - **Mac:** `echo 'export TELEGRAM_BOT_TOKEN="123456:ABC..."' >> ~/.zshrc` sonra terminali yeniden aç.
4. Chat id'ni bul:
   ```
   python calistir.py --chat-id-bul
   ```
   Çıkan numarayı aynı şekilde kaydet: `setx TELEGRAM_CHAT_ID "123456789"` (Mac'te `export ...`).
5. Dene:
   ```
   python calistir.py --telegram-test
   ```
   Telegram'a "Nasdaq tarayıcı bağlandı" mesajı gelmeli.

> Token'ı kimseyle paylaşma; token'ı bilen biri senin botun adına mesaj atabilir.

### Çalıştırma (bilgisayarda)

```
python calistir.py --kuru --simdi    # önce dene: hemen tarar, sadece ekrana yazar
python calistir.py --simdi           # hemen tarar, Telegram'a da yollar, çıkar
python calistir.py                   # 7/24 mod (durdurmak için Ctrl+C)
```

7/24 mod için bilgisayarın açık kalmalı ve **uyku moduna geçmemeli**
(Windows güç ayarlarında uykuyu "Hiçbir zaman" yap). Bilgisayarın kapalıyken de çalışsın
istersen aynı klasörü sürekli açık duran bir makineye veya ucuz bir sunucuya (VPS) kurabilirsin.
Kurulum adımları aynı.

## Önce şunu yap: geriye dönük test

GitHub'da: **Actions → Nasdaq tarayici → Run workflow → `geriye-test`**. Bittiğinde işin
kaydını aç, "Botu calistir" adımında sonuç tablosu yazar. Bilgisayarda:

```
python geriye_test.py              # listedeki hisseler, son 5 yıl
python geriye_test.py --yil 10
```

Her strateji, **"her gün rastgele giriş"** kıyasıyla birlikte raporlanır. Bir strateji bu
kıyası belirgin şekilde geçmiyorsa sinyal sana bir avantaj sağlamıyor demektir. Son yıllarda
Nasdaq genel olarak yükseldiği için rastgele alım bile kârlı görünebilir.

## Ayarlar — `config.yaml`

İzleme listesi, tarama sıklığı, strateji eşikleri, stop mesafesi (ATR katı) ve hedef (R katı)
burada. Not Defteri ile açıp değiştir, botu yeniden başlat.

## Dosyalar

```
calistir.py            botu başlatır
geriye_test.py         geçmiş veride test
config.yaml            ayarlar
tarayici/
  market_hours.py      seans saatleri, tatiller, yaz saati
  indicators.py        SMA, EMA, RSI, ATR, VWAP
  signals.py           stratejiler (canlı ve test aynı kodu kullanır)
  data.py              Yahoo Finance verisi (yfinance)
  notifier.py          Telegram ve mesaj biçimleri
  scanner.py           7/24 döngü, sinyal takibi (stop / hedef / süre)
  storage.py           tekrar mesaj koruması, CSV kayıtları
  backtest.py          geçmiş test motoru
.github/workflows/     GitHub Actions zamanlaması (bulutta çalıştırma)
tests/                 48 otomatik test:  python -m pytest
veri/                  (çalışınca oluşur) bot.log, sinyaller.csv, sonuclar.csv, durum.json
```

## Sınırlar — nerede yanılıyor olabilirim?

- **Bu stratejilerin para kazandırdığı kanıtlanmış değil.** Bunlar herkesin bildiği klasik
  teknik kurulumlar. Önce `geriye_test.py`, sonra birkaç hafta `sonuclar.csv`. Gerçek parayla
  denemeden önce ikisine de bak.
- **Ücretsiz veri resmî değil.** yfinance, Yahoo'nun herkese açık verisini kullanır. Bazen
  gecikebilir, kısa süre bozulabilir ya da sınırlanabilir. Bot hatada çökmez, bir sonraki
  turda tekrar dener. Ciddi gün içi kullanım için ücretli, gerçek zamanlı bir veri kaynağı gerekir.
- **Gecikme:** Sinyal mum kapanınca gelir. Sen Midas'ta emri girene kadar fiyat kaçmış olabilir.
  Mesajdaki fiyattan çok uzaklaştıysa kovalamamak genelde daha güvenli.
- **Günlük sinyaller gece gelir** (TR ~00:15). Ertesi gün açılışta fiyat stop'un altına ya da
  hedefin üstüne açıldıysa kurulum geçersizdir.
- **Stop ve hedef sadece öneri.** Bot senin yerine stop koymaz; uygulamada kendin girmelisin.
- **Geriye test olduğundan iyi görünür:** Liste bugün başarılı olan şirketlerden oluşuyor
  (hayatta kalma yanılgısı). Komisyon, vergi ve kur farkı da dahil değil.
- **Kısa mesai günleri** (Şükran Günü ertesi, 24 Aralık, 3 Temmuz) kurala göre hesaplanıyor,
  resmî takvim değil.

*Yatırım tavsiyesi değildir.*
