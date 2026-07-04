# Tribal Wars Bot (PyQt5 + QWebEngine)

## Gereksinimler

```text
pip install PyQt5 PyQtWebEngine
```

## Proxy (kullanıcı tercihi)

- Uygulama paketinde **gömülü proxy adresi yoktur**; isterseniz **Ayarlar** sekmesinde **Ağ / Proxy** bölümünden sunucu, port, tür (HTTP / SOCKS5) ve isteğe bağlı kullanıcı adı/şifre girebilirsiniz.
- Tercihler `QSettings` ile **yerel bilgisayarda** saklanır; botu tekrar çalıştırdığınızda **yeniden yazmanız gerekmez** (Ayarlar → Aynı değerler, Kaydet değişiklik yaptığınızda yeterlidir).
- `QTWEBENGINE_CHROMIUM_FLAGS` çoğu ortamda **süreç başlarken** uygulandığından, proxy alanlarını **kaydettikten sonra** oyunun tarayıcıda aynen kullanması için uygulamayı **kapatıp yeniden açmanız** önerilir.
- **Şifre** (varsa) düz metin biçiminde yerel ayar dosyasında/Windows kaydında tutulur; cihaz erişimine dikkat edin.
- **Oyun hizmet şartları** ve proxy sağlayıcınızın koşulları sizin sorumluluğunuzdadır.

## Telegram bildirimleri (isteğe bağlı)

- **BotFather** (Telegram) üzerinden bir bot oluşturup **token** alın; sohbet veya grupta bildirim almak için **chat ID** gerekir (bireysel sohbet için `@userinfobot` gibi botlar, gruplar için yönetici olduktan sonra grup sohbetinde `/getUpdates` yanıtındaki `chat.id` veya `Raw Data` yöntemleri kullanılabilir).
- **Ayarlar** sekmesinde **Telegram** bölümünden etkinleştirip token ve chat ID’yi girin, **Test** ile deneyin.
- **Token** bir parola gibi davranır; ekran görüntülerine ve paylaşımlara dikkat edin. Değerler yine `QSettings` ile yerel olarak saklanır; cihaz paylaşımı riski vardır.
- Bot, **doğrulama ekranı** (hCaptcha, bot koruması vb.) ilk tespit edildiğinde kısa bir uyarı metni gönderir; aynı oturumda tekrar spam yapmamak için yalnızca ilk geçişte çalışır. Bu sırada **temizlik (scav)** otomatik adımları da duraklatılır; doğrulama kalkınca devam eder.

### Teknik not

- **HTTP** tür: Chromium ve Python’daki `urllib` (ikon / harita karosu indirmeleri) aynı **HTTP proxy** yönlendirmesine çekilebilir.
- **SOCKS5** tür: oyun trafiği Chromium üzerinden gider; standart kütüphanedeki `urllib` aynı SOCKS5 proxy’yi her zaman yansıtmayabilir.

### ERR_NO_SUPPORTED_PROXIES görüyorsanız

- **1.0.7+:** Önceki sürümler kullanıcı/şifreyi `--proxy-server` URL’sine koyabiliyordu; bu biçim birçok Chromium sürümünde desteklenmiyor. Yeni mantık: sadece `http://host:port` (veya SOCKS5’te `socks5://…`), kullanıcı adı/şifre **tarayıcı içi proxy kimliği** ile (Qt) veriliyor.
- Genelde ayrıca **yanlış tür** (HTTP / SOCKS) — Ayarlar → **Proxy türü**nü sağlayıcının port açıklamasıyla aynı yapıp uygulamayı yeniden başlatın.

1. Uygulamayı **1.0.7 veya üzeri** `tribal_wars_bot.py` ile açın, ayarları kaydedip kapatıp açın.
2. Hâlâ aynıysa sağlayıcıda bu portun **HTTP mi SOCKS5 mi** olduğunu doğrulayın; çoğu tüketici/ISP hattı **HTTP** türdedir.
3. Yalnızca özel / SOCKS5 senaryolarda, ortamınıza uygun **sağlayıcı dokümanı** veya **yerel HTTP→SOCKS köprüsü** gerekebilir.

## Çalıştırma

```text
python tribal_wars_bot.py
```

Uzak sunucuda (Linux) genelde `xvfb-run` veya açık bir X oturumu gerekir; Windows’ta RDP veya açık masaüstü oturumu yeterlidir (Qt WebEngine görüntü gereksinimi).
