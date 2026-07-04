# Tribal Wars Bot — sürüm dağıtımı

Arkadaşlar bot içinden güncelleme alır; GitHub adresi kullanıcıya gösterilmez.

## Her yeni sürümde (siz)

1. `tribal_wars_bot.py` içinde `APP_VERSION` artırın (ör. `1.2.1`).
2. `build_exe.bat` ile exe üretin, zipleyin: `TribalWarsBot-1.2.1.zip`
   - Zip kökünde: `TribalWarsBot.exe`, `_internal/` (varsa), `guncelle.bat`, `arascript.js`, `map-coord-picker.js`
3. GitHub **tw-bot** reposunda **Release** oluşturun: etiket `v1.2.1`, zip dosyasını ekleyin.
4. `docs/bot-update.json` güncelleyin:
   - `version`: `1.2.1`
   - `download_url`: Release zip’inin doğrudan indirme linki
   - `changelog_tr`: Türkçe değişiklik listesi
5. `main` dalına push edin → GitHub Pages birkaç dakikada güncellenir.

## GitHub Pages (bir kez)

Repo **Settings → Pages**:

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**

Manifest adresi: `https://safayolcuu.github.io/tw-bot/bot-update.json`

## İlk kurulum (arkadaşlar)

Bir kez `build_exe` zip’ini WhatsApp vb. ile gönderin. Sonraki sürümler bot açılınca veya **Yardım → Güncellemeleri kontrol et** ile gelir.

## Zip yapısı

`guncelle.bat` exe ile aynı klasörde olmalı. Bot indirilen zip’i `guncelleme\package\` altına açar; `guncelle.bat` dosyaları kurulum klasörüne kopyalar ve `tw_config.json` yedekten geri yükler.
