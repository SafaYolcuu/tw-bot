# Tribal Wars Bot — manuel lisans satışı

## VDS (Windows)

Detay: [license-server/MANUAL-VDS.md](license-server/MANUAL-VDS.md)

```powershell
cd license-server
.\vds\install.ps1
.\vds\open-firewall.ps1      # Yönetici
.\vds\install-service.ps1    # Yönetici
```

Panel: `http://VDS_IP:8080/admin`

## Müşteri botu

1. `license_config.example.json` → `license_config.json` kopyala
2. `license_api_base` = `http://VDS_IP:8080`
3. EXE ile aynı klasöre koy

## Key üret

- Panel: **Yeni lisans**
- VDS: `license-server\vds\key-uret-remote.ps1`
- Yerel: `key-uret.bat` (sunucu ayaktayken)

Mesaj şablonu: [license-server/SATIS-SABLON.txt](license-server/SATIS-SABLON.txt)
