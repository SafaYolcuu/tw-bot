================================================================================
  Tribal Wars Bot — Kurulum ve Sistem Gereksinimleri
================================================================================

SISTEM GEREKSINIMLERI
---------------------
  • Windows 10 veya 11, 64-bit (x64). 32-bit desteklenmez.
  • Visual C++ Redistributable 2015–2022 x64 (ZORUNLU):
      https://aka.ms/vs/17/release/vc_redist.x64.exe
  • Disk: en az ~400 MB bos alan (ZIP + _internal)
  • RAM: en az 4 GB (onerilen 8 GB) — gomulu Chromium tarayici
  • Internet: oyun sunucusu + lisans / guncelleme erisimi

KURULUM (ADIM ADIM)
-------------------
  1) ZIP dosyasini bir klasore TAM cikartin
     (Ornek: C:\Games\TWBot  veya  D:\TribalWarsBot)
  2) Klasorde sunlar yan yana olmali:
       TribalWarsBot.exe
       _internal\          ← bu klasor eksikse bot ACILMAZ
       baslat.bat
       gerekler-kontrol.bat
       README_KURULUM.txt
  3) VC++ x64 paketini kurun (yukaridaki link).
  4) Once gerekler-kontrol.bat calistirin; OK ise baslat.bat ile acin.
  5) Ilk acilista lisans / giris adimlarini tamamlayin.

YAPMAYIN
--------
  • ZIP icinden dogrudan calistirmayin
  • Temp / Downloads icinden calistirmayin
  • OneDrive / Google Drive senkron klasorunde tutmayin (DLL kilitlenebilir)
  • Program Files altina koymaya zorlamayin (yazma izni sorunlari)

SIK HATALAR
-----------
  Hic acilmiyor / aninda kapaniyor
    → VC++ x64 kurun; ZIP’i yeniden cikarin; antivirus karantinasini kontrol edin
    → boot.log ve crash.log dosyalarini (exe yaninda) destek ekibine gonderin

  "Qt / DLL yuklenemedi"
    → _internal klasoru eksik veya bozulmus; ZIP’i tekrar cikarin
    → OneDrive yerine yerel disk yolu kullanin

  Aciliyor ama tarayici beyaz / cokuyor
    → baslat.bat ile acin
    → GPU surucusunu guncelleyin; Windows Defender istisnasi ekleyin

  Lisans / guncelleme SSL hatasi
    → Antivirus HTTPS taramasini gecici kapatin veya istisna ekleyin

  Windows 7 / 8
    → Desteklenmez; Windows 10 veya 11 64-bit gerekir

DESTEK ICIN GONDERIN
--------------------
  1) Windows surumu (Ayarlar → Sistem → Hakkinda): 10/11 ve 64-bit mi?
  2) ZIP tam cikarildi mi? exe yaninda _internal var mi?
  3) VC++ x64 kuruldu mu?
  4) Bot klasor yolu (OneDrive mi?)
  5) Antivirus / Defender engeli var mi?
  6) boot.log ve crash.log (varsa)
  7) Hata: hic acilmiyor mu, tarayici mi bos?

Guncelleme: Yardim → Guncellemeleri kontrol et  veya  guncelle.bat
================================================================================
