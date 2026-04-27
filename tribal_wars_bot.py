"""
Tribal Wars Bot — Kabile Savaşları Otomasyon Aracı
PyQt5 + QWebEngineView (Chromium tabanlı gömülü tarayıcı)

Gereksinimler:
    pip install PyQt5 PyQtWebEngine
"""

import os
import sys


def _tw_add_pyqt_dll_paths():
    """Windows: Qt DLL'leri bulunamadiginda ImportError azaltir (os.add_dll_directory)."""
    if sys.platform != "win32":
        return
    try:
        import os
        from pathlib import Path

        candidates = []
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base = Path(sys._MEIPASS)
            candidates.extend(
                [
                    base / "PyQt5" / "Qt5" / "bin",
                    base / "PyQtWebEngine" / "Qt5" / "bin",
                ]
            )
        else:
            import site

            roots = list(site.getsitepackages())
            u = site.getusersitepackages()
            if u:
                roots.append(u)
            for root in roots:
                rp = Path(root)
                candidates.append(rp / "PyQt5" / "Qt5" / "bin")
                candidates.append(rp / "PyQtWebEngine" / "Qt5" / "bin")

        seen = []
        for p in candidates:
            try:
                p = p.resolve()
            except OSError:
                continue
            if not p.is_dir():
                continue
            s = str(p)
            if s in seen:
                continue
            seen.append(s)
            try:
                os.add_dll_directory(s)
            except (OSError, AttributeError):
                pass
        if seen:
            os.environ["PATH"] = os.pathsep.join(seen) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


_tw_add_pyqt_dll_paths()

import re
import json
import math
import random
import ssl
import time
import datetime
import urllib.error
import urllib.request
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, urlencode
from PyQt5.QtCore import Qt, QUrl, QTimer, QTime, QDate, QSize, pyqtSignal, QObject, QSettings, pyqtSlot
from PyQt5.QtGui import QFont, QColor, QBrush, QPainter, QPen, QPixmap, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QSpinBox, QTreeWidget, QTreeWidgetItem, QTextEdit, QSplitter,
    QListWidget, QListWidgetItem,
    QFrame, QGroupBox, QGridLayout, QHeaderView, QStatusBar, QScrollArea,
    QSizePolicy, QFormLayout, QMessageBox, QTableWidget, QTableWidgetItem,
    QTimeEdit, QDateEdit, QAbstractItemView, QDoubleSpinBox, QSlider,
    QDialog, QDialogButtonBox, QRadioButton, QButtonGroup,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel

# ─────────────────────────────────────────────
#  SABİTLER
# ─────────────────────────────────────────────

# EXE'nin guncel oldugunu dogrulamak icin her onemli degisiklikte artirin.
APP_VERSION = "1.1.0"

SERVERS = [
    ("klanlar.org", "https://www.klanlar.org"),
    ("tribalwars.works", "https://www.tribalwars.works"),
    ("tribalwars.net", "https://www.tribalwars.net"),
    ("tribalwars.com.tr", "https://www.tribalwars.com.tr"),
    ("tribalwars.co.uk", "https://www.tribalwars.co.uk"),
    ("tribalwars.de", "https://www.die-staemme.de"),
]

# Planlayıcı: Chrome bookmarklet ile aynı kaynak (script src — eval/CSP uyumu).
TW_PLANNER_SCRIPT_URL = "https://safayolcuu.github.io/klanlar/arascript.js"

# QSettings: org/app — build’e gömülü proxy yok, kullanıcı tercihleri diske gider.
QSETTINGS_ORG = "TribalWarsBot"
QSETTINGS_APP = "TWB"

# Kalici ayar dosyasi: exe'nin yaninda tw_config.json.
# Yeni exe dagitilsa bile bu dosya silinmez; arkadaslar sadece exe'yi gunceller.
def _tw_config_path() -> Path:
    """Exe'nin (veya script'in) bulundugu klasorde tw_config.json dondur."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return base / "tw_config.json"


def _tw_load_config() -> dict:
    """tw_config.json'i oku; dosya yoksa veya bozuksa bos dict."""
    p = _tw_config_path()
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _tw_save_config(data: dict) -> None:
    """tw_config.json'a yaz (mevcut anahtarlari koru, sadece verilen anahtarlari guncelle)."""
    p = _tw_config_path()
    existing = _tw_load_config()
    existing.update(data)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def tw_apply_saved_proxy_environment() -> None:
    """
    Kayıtlı proxy tercihini uygular. QApplication sonrası, QWebEngine kullanılmadan önce çağrılmalı
    (Chromium `QTWEBENGINE_CHROMIUM_FLAGS` çoğunlukla süreç başlarken okur).

    Proxy kapalı: QTWEBENGINE_CHROMIUM_FLAGS dokunulmaz (dıştan export ettiyseniz aynen kalır).
    Chromium: kullanıcı/şifre **URL içine gömülmez** (ERR_NO_SUPPORTED_PROXIES); StealthWebPage
    `proxyAuthenticationRequired` ile verilir. urllib: HTTP türde ProxyHandler’da hâlâ user:pass URL’de.
    """
    s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
    if not s.value("network/proxy_enabled", False, type=bool):
        urllib.request.install_opener(urllib.request.build_opener())
        return

    host = (s.value("network/proxy_host", "") or "").strip()
    try:
        port = int(s.value("network/proxy_port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    ptype = (s.value("network/proxy_type", "http") or "http").strip().lower()
    if ptype not in ("http", "https", "socks5"):
        ptype = "http"
    user = (s.value("network/proxy_user", "") or "").strip()
    password = (s.value("network/proxy_password", "") or "").strip()

    if not host or not (1 <= port <= 65535):
        urllib.request.install_opener(urllib.request.build_opener())
        return

    if user or password:
        uq, pq = quote(user, safe=""), quote(password, safe="")
        auth = f"{uq}:{pq}@"
    else:
        auth = ""

    # Chromium: yalnız host:port (+ şema), kimlik yok (Qt sayfa sinyalinde)
    if ptype == "socks5":
        server = f"socks5://{host}:{port}"
    else:
        server = f"http://{host}:{port}"

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"--proxy-server={server}"

    if ptype in ("http", "https"):
        proxy_url = f"http://{auth}{host}:{port}"
        ph = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        urllib.request.install_opener(urllib.request.build_opener(ph))
    else:
        urllib.request.install_opener(urllib.request.build_opener())


def _tw_normalize_telegram_chat_id(raw: str) -> str:
    s = (raw or "").strip().replace(" ", "")
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    return s


def _tw_telegram_build_opener(insecure_skip_verify: bool = None):
    """Kurumsal ağ/SSL tarama (self‑signed) için `insecure_skip_verify` veya QSettings
    `notify/telegram_insecure_ssl`. Aksi: doğrulanmış sertifika, varsa `certifi` mağazası.
    Oyun global proxy’si yok: ProxyHandler boş; Telegram ayrı."""
    if insecure_skip_verify is None:
        cfg = _tw_load_config()
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        insecure_skip_verify = cfg.get(
            "telegram_insecure_ssl",
            s.value("notify/telegram_insecure_ssl", False, type=bool)
        )
    if insecure_skip_verify:
        ctx = ssl._create_unverified_context()
    else:
        try:
            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )


def tw_telegram_api_send_message(
    token: str, chat_id: str, text: str, insecure_override: bool = None
) -> tuple:
    """sendMessage. Dönüş: (başarılı: bool, hata_metni: str) — hata yoksa ikinci dize boş.
    `insecure_override` True/False: test penceresinden; None: QSettings (Kaydet’le kalıcı)."""
    body = (text or "").strip()
    token = (token or "").strip()
    chat = _tw_normalize_telegram_chat_id(chat_id)
    if not token or not chat or not body:
        return (False, "Token, Chat ID veya metin boş.")
    data = urlencode({"chat_id": chat, "text": body[:4090]}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    # Ayarlardaki global HTTP proxy: Telegram bu istekte tünel kullanmıyor (önceki 403 tünel).
    _tg = _tw_telegram_build_opener(insecure_skip_verify=insecure_override)
    try:
        with _tg.open(req, timeout=30) as resp:
            raw = resp.read(8192).decode("utf-8", "replace")
        j = json.loads(raw)
        if not j.get("ok"):
            desc = (j.get("description") or str(j))[:500]
            return (False, f"API: {desc}")
        return (True, "")
    except urllib.error.HTTPError as e:
        try:
            hbody = e.read().decode("utf-8", "replace")
            hj = json.loads(hbody)
            desc = (hj.get("description") or hbody)[:500]
            return (False, f"API ({e.code}): {desc}")
        except Exception:
            return (False, f"HTTP {e.code} {e.reason or ''}")
    except urllib.error.URLError as e:
        r = e.reason
        r = r if (r and str(r).strip()) else str(e)
        return (False, str(r)[:500])
    except Exception as e:
        return (False, str(e)[:500])


def _tw_telegram_msgbox_on_top(parent, is_warning, title, text) -> None:
    """Küçük pencere bazen oyun/ tarayıcının altında kaldığından üstte göster."""
    m = QMessageBox(parent)
    m.setIcon(QMessageBox.Warning if is_warning else QMessageBox.Information)
    m.setWindowTitle(title)
    m.setText(text)
    m.setWindowModality(Qt.ApplicationModal)
    m.setWindowFlags(m.windowFlags() | Qt.WindowStaysOnTopHint)
    m.exec_()


def tw_telegram_send_message_threaded(bot, text: str) -> None:
    """tw_config.json veya QSettings: notify/telegram_*. Açıksa sendMessage."""
    def work():
        err = None
        try:
            cfg = _tw_load_config()
            s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
            enabled = cfg.get("telegram_enabled", s.value("notify/telegram_enabled", False, type=bool))
            if not enabled:
                return
            token = (cfg.get("telegram_bot_token") or s.value("notify/telegram_bot_token", "") or "").strip()
            chat = (cfg.get("telegram_chat_id") or s.value("notify/telegram_chat_id", "") or "").strip()
            if not token or not chat or not (text and str(text).strip()):
                return
            ok, emsg = tw_telegram_api_send_message(token, chat, str(text))
            if not ok:
                err = emsg
        except Exception as e:
            err = str(e)[:500]
        if err and bot is not None:
            try:
                bot._telegram_send_error.emit(err)
            except (RuntimeError, AttributeError):
                pass

    threading.Thread(target=work, daemon=True).start()


VILLAGE_TYPES = [
    ("yours", "#e8e832", "Senin"),
    ("enemy", "#cc2222", "Düşman"),
    ("ally", "#4488cc", "Müttefik"),
    ("other", "#FF6600", "Diğer"),
    ("abandoned", "#888888", "Terk Edilmiş"),
    ("nap", "#9944aa", "NAP"),
    ("tribe", "#1a3a8a", "Kabile"),
]

TROOP_TYPES = [
    ("Mızrakçı", 1200, 50),
    ("Kılıççı", 800, 30),
    ("Baltacı", 450, 20),
    ("Okçu", 600, 0),
    ("Hafif Süvari", 300, 10),
    ("Ağır Süvari", 150, 5),
    ("Koçbaşı", 20, 2),
    ("Mancınık", 10, 0),
    ("Şövalye", 50, 3),
]


# ─────────────────────────────────────────────
#  BİRİM İKON YÖNETİCİSİ
# ─────────────────────────────────────────────

class _IconSignals(QObject):
    """İkon indirme iş parçacığından ana iş parçacığına sinyal gönderir.
    QPixmap thread'den oluşturulamaz — ham byte'ları taşıyoruz."""
    icon_ready = pyqtSignal(str, bytes)   # (unit_key, png_bytes)


class TroopIconManager:
    """
    Tribal Wars sunucusunun image_base URL'sinden birim ikonlarını
    arka planda indirir, önbelleğe alır ve isteyen widget'lara uygular.

    Kullanım:
        manager = TroopIconManager()
        manager.set_image_base("https://dsde.innogamescdn.com/asset/abc123/")
        manager.apply_to_label(label, "spear")
        manager.apply_to_checkbox(cb, "sword")
    """

    # Tribal Wars CDN'deki gerçek dosya adları
    # image_base zaten "…/graphic/" ile bitiyor olabilir;
    # _build_url() her iki durumu da ele alır.
    UNIT_KEYS = [
        "spear", "sword", "axe", "archer",
        "spy", "light", "marcher", "heavy",
        "ram", "catapult", "knight", "snob",
    ]

    def __init__(self):
        self._image_base: str = ""
        self._cache: dict[str, "QPixmap"] = {}     # key → 16×16 QPixmap
        self._subscribers: dict[str, list] = {}    # key → [(widget, mode), ...]
        self._log_fn = None                        # opsiyonel: _add_log(cat, type, msg)
        self._signals = _IconSignals()
        self._signals.icon_ready.connect(self._on_icon_ready)

    # ── Genel API ─────────────────────────────

    def set_image_base(self, image_base: str, log_fn=None):
        """
        Oyundan alınan image_base URL'sini ayarlar ve tüm eksik
        ikonları arka planda indirmeye başlar.
        """
        if log_fn:
            self._log_fn = log_fn
        if not image_base or image_base == self._image_base:
            return
        self._image_base = image_base.rstrip("/") + "/"
        for key in self.UNIT_KEYS:
            if key not in self._cache:
                self._download_async(key)

    def apply_to_label(self, label: "QLabel", unit_key: str):
        """QLabel'e ikon ata; henüz indirilmediyse abone listesine ekle."""
        self._register(unit_key, label, "label")
        if unit_key in self._cache:
            label.setPixmap(self._cache[unit_key])

    def apply_to_checkbox(self, checkbox: "QCheckBox", unit_key: str):
        """QCheckBox'a ikon ata; henüz indirilmediyse abone listesine ekle."""
        self._register(unit_key, checkbox, "checkbox")
        if unit_key in self._cache:
            self._set_checkbox_icon(checkbox, self._cache[unit_key])

    def get_icon(self, unit_key: str) -> "QIcon | None":
        """Önbellekteki ikonu QIcon olarak döndürür, yoksa None."""
        px = self._cache.get(unit_key)
        return QIcon(px) if px else None

    # ── Dahili yardımcılar ────────────────────

    def _register(self, key: str, widget, mode: str):
        self._subscribers.setdefault(key, []).append((widget, mode))

    def _build_url(self, unit_key: str) -> str:
        """
        Tribal Wars'ın farklı sunucularında image_base farklı biçimlerde gelebilir:
          • "https://cdn.innogamescdn.com/asset/XYZ/"          (base, graphic/ yok)
          • "https://cdn.innogamescdn.com/asset/XYZ/graphic/"  (graphic/ zaten var)
        Gerçek ikon yolu her iki durumda da:
          …/graphic/unit/unit_KEY.png
        """
        base = self._image_base
        if "graphic/" in base:
            # base zaten graphic/ içeriyor → unit/unit_KEY.png ekle
            return base + "unit/unit_" + unit_key + ".png"
        else:
            return base + "graphic/unit/unit_" + unit_key + ".png"

    def _download_async(self, unit_key: str):
        """PNG'yi arka plan iş parçacığında indir; ham byte'ları sinyal ile gönder.
        QPixmap yalnızca ana thread'de oluşturulabilir."""
        def _worker():
            url = self._build_url(unit_key)
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read()
                if raw:
                    self._signals.icon_ready.emit(unit_key, raw)
            except Exception as e:
                if self._log_fn:
                    self._log_fn("İKON", "warn", f"İndirilemedi [{unit_key}]: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_icon_ready(self, unit_key: str, raw: bytes):
        """Ana iş parçacığında çalışır — QPixmap burada oluşturuluyor."""
        px = QPixmap()
        if not px.loadFromData(raw):
            if self._log_fn:
                self._log_fn("İKON", "warn", f"QPixmap yüklenemedi [{unit_key}]")
            return
        px = px.scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._cache[unit_key] = px

        if self._log_fn:
            self._log_fn("İKON", "success", f"✅ {unit_key} ikonu yüklendi")

        for widget, mode in self._subscribers.get(unit_key, []):
            try:
                if mode == "label":
                    widget.setPixmap(px)
                elif mode == "checkbox":
                    self._set_checkbox_icon(widget, px)
            except RuntimeError:
                pass   # Widget silinmiş

    @staticmethod
    def _set_checkbox_icon(checkbox, pixmap: "QPixmap"):
        checkbox.setIcon(QIcon(pixmap))
        checkbox.setIconSize(pixmap.size())


# Uygulama genelinde tek örnek
troop_icon_mgr = TroopIconManager()


def generate_villages(count=90):
    villages = []
    for i in range(count):
        vt = random.choice(VILLAGE_TYPES)
        villages.append({
            "id": i,
            "x": 500 + random.randint(0, 29),
            "y": 445 + random.randint(0, 24),
            "type": vt[0],
            "color": vt[1],
            "type_label": vt[2],
            "name": f"Köy {i + 1}",
            "player": "-" if vt[0] == "abandoned" else f"Oyuncu{random.randint(1, 50)}",
            "points": random.randint(500, 10000),
        })
    return villages


# ─────────────────────────────────────────────
#  ANTİ-DETECTİON TARAYICI
# ─────────────────────────────────────────────

class StealthWebPage(QWebEnginePage):
    """Bot tespitini önleyen özel sayfa sınıfı."""

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        # Proxy kimliği: --proxy-server’da user:pass kullanmak Chromium’da ERR_NO_SUPPORTED_PROXIES
        # üretebiliyor; QAuthenticator ile (Qt 5.8+) verilir.
        self.proxyAuthenticationRequired.connect(self._on_proxy_authentication_required)

    def javaScriptConsoleMessage(self, level, message, line, source):
        pass

    def _on_proxy_authentication_required(self, _request_url, authenticator, _proxy_host):
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        if not s.value("network/proxy_enabled", False, type=bool):
            return
        u = (s.value("network/proxy_user", "") or "").strip()
        pw = (s.value("network/proxy_password", "") or "").strip()
        try:
            authenticator.setUser(u)
            authenticator.setPassword(pw)
        except (RuntimeError, TypeError, AttributeError):
            pass


class StealthBrowser(QWebEngineView):
    """Anti-detection özellikleri olan gömülü Chromium tarayıcı."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # Özel profil oluştur
        self.profile = QWebEngineProfile("tribal_bot", self)
        self._configure_profile()

        # Stealth sayfa
        self.stealth_page = StealthWebPage(self.profile, self)
        self.setPage(self.stealth_page)

        # Anti-detection JS enjeksiyonu
        self.stealth_page.loadFinished.connect(self._inject_stealth_js)

    def _configure_profile(self):
        """Profili normal bir tarayıcı gibi yapılandır."""
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        self.profile.setHttpUserAgent(ua)
        self.profile.setHttpAcceptLanguage("tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.AllowPersistentCookies)

        settings = self.profile.settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)

        # HTTP disk cache — tekrar ziyaret edilen kaynaklar (css/js/img) ağdan çekilmesin
        try:
            self.profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
            self.profile.setHttpCacheMaximumSize(50 * 1024 * 1024)  # 50 MB
        except Exception:
            pass

    def _inject_stealth_js(self, ok):
        """Sayfa yüklendikten sonra anti-detection JavaScript enjekte et."""
        if not ok:
            return

        stealth_js = """
        (function() {
            // navigator.webdriver flag'ini kaldır
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Chrome runtime simülasyonu
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };

            // Permissions API maskeleme
            if (navigator.permissions) {
                const originalQuery = navigator.permissions.query;
                navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({state: Notification.permission}) :
                        originalQuery(parameters)
                );
            }

            // Plugin dizisi
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // Dil ayarları
            Object.defineProperty(navigator, 'languages', {
                get: () => ['tr-TR', 'tr', 'en-US', 'en']
            });

            // WebGL vendor maskeleme
            const getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(p) {
                if (p === 37445) return 'Intel Inc.';
                if (p === 37446) return 'Intel Iris OpenGL Engine';
                return getParam.call(this, p);
            };
        })();
        """
        self.page().runJavaScript(stealth_js)

    def navigate(self, url):
        self.setUrl(QUrl(url))


class TwPlannerBridge(QObject):
    """Planlayıcıdaki simulator place linki → Ordu Gönder kuyruğu (QWebChannel)."""

    def __init__(self, bot):
        super().__init__(bot)
        self._bot = bot

    @pyqtSlot(str, str)
    def enqueueSimulatorCommand(self, href: str, date_time_json: str):
        if hasattr(self._bot, "_tw_planner_enqueue_from_href"):
            self._bot._tw_planner_enqueue_from_href(href, date_time_json)


# ─────────────────────────────────────────────
#  STYLESHEET
# ─────────────────────────────────────────────

STYLESHEET = """
QMainWindow {
    background-color: #f0f0f0;
}
#topPanel {
    background-color: #e8e8e8;
    border-bottom: 1px solid #bbbbbb;
    padding: 6px;
}
QTabWidget::pane {
    border: 1px solid #aaaaaa;
    background: #f5f5f5;
    padding: 0px;
}
QTabBar::tab {
    background: #dddddd;
    border: 1px solid #aaaaaa;
    border-bottom: none;
    padding: 6px 16px;
    margin-right: 2px;
    font-size: 11px;
}
QTabBar::tab:selected {
    background: #f5f5f5;
    border-top: 2px solid #2d5a9e;
    font-weight: bold;
}
QTabBar::tab:hover {
    background: #e8e8e8;
}
QPushButton {
    padding: 4px 14px;
    border: 1px solid #999999;
    border-radius: 3px;
    background: qlineargradient(y1:0, y2:1, stop:0 #f8f8f8, stop:1 #dddddd);
    font-size: 11px;
}
QPushButton:hover {
    background: qlineargradient(y1:0, y2:1, stop:0 #ffffff, stop:1 #e8e8e8);
}
QPushButton#startBtn {
    background: qlineargradient(y1:0, y2:1, stop:0 #5b8fd4, stop:1 #2d5a9e);
    color: white; border: 1px solid #2d5a9e; font-weight: bold;
}
QPushButton#stopBtn {
    background: qlineargradient(y1:0, y2:1, stop:0 #dd6666, stop:1 #aa3333);
    color: white; border: 1px solid #993333; font-weight: bold;
}
QLineEdit, QSpinBox {
    padding: 3px 6px; border: 1px solid #999999;
    border-radius: 2px; background: white; font-size: 11px;
}
QLineEdit:focus { border: 1px solid #2d5a9e; }
QComboBox {
    padding: 3px 6px; border: 1px solid #999999;
    border-radius: 2px; background: white; font-size: 11px;
}
QGroupBox {
    font-weight: bold; font-size: 11px; color: #2d5a9e;
    border: 1px solid #cccccc; border-radius: 4px;
    margin-top: 8px; padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
}
QTreeWidget {
    border: 1px solid #aaaaaa; background: white;
    font-size: 11px; alternate-background-color: #f6f6f6;
}
QTreeWidget::item:selected { background: #2d5a9e; color: white; }
QHeaderView::section {
    background: #e0e0e0; border: 1px solid #aaaaaa;
    padding: 4px; font-weight: bold; font-size: 10px;
}
QTextEdit#logText {
    background-color: #1e1e1e; color: #cccccc;
    font-family: Consolas, monospace; font-size: 10px;
    border: 1px solid #333333; padding: 6px;
}
QStatusBar {
    background: #e0e0e0; border-top: 1px solid #aaaaaa;
    font-size: 10px; color: #555555;
}
QStatusBar QLabel {
    color: #555555;
}
QScrollArea#settingsTabScroll, QWidget#settingsTabViewport, QWidget#settingsTabScrollInner {
    background-color: #f5f5f5;
    border: none;
}
QLabel#settingsProxyHelp {
    color: #666666;
    font-size: 10px;
}
"""


STYLESHEET_DARK = """
QMainWindow {
    background-color: #2b2b2b;
}
#topPanel {
    background-color: #383838;
    border-bottom: 1px solid #555555;
    padding: 6px;
}
QTabWidget::pane {
    border: 1px solid #555555;
    background: #323232;
    padding: 0px;
}
QTabBar::tab {
    background: #454545;
    border: 1px solid #555555;
    border-bottom: none;
    padding: 6px 16px;
    margin-right: 2px;
    font-size: 11px;
    color: #e8e8e8;
}
QTabBar::tab:selected {
    background: #323232;
    border-top: 2px solid #5b8fd4;
    font-weight: bold;
    color: #f0f0f0;
}
QTabBar::tab:hover {
    background: #505050;
}
QPushButton {
    padding: 4px 14px;
    border: 1px solid #666666;
    border-radius: 3px;
    background: qlineargradient(y1:0, y2:1, stop:0 #4a4a4a, stop:1 #3a3a3a);
    font-size: 11px;
    color: #eeeeee;
}
QPushButton:hover {
    background: qlineargradient(y1:0, y2:1, stop:0 #555555, stop:1 #454545);
}
QPushButton#startBtn {
    background: qlineargradient(y1:0, y2:1, stop:0 #5b8fd4, stop:1 #2d5a9e);
    color: white; border: 1px solid #2d5a9e; font-weight: bold;
}
QPushButton#stopBtn {
    background: qlineargradient(y1:0, y2:1, stop:0 #dd6666, stop:1 #aa3333);
    color: white; border: 1px solid #993333; font-weight: bold;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {
    padding: 3px 6px; border: 1px solid #666666;
    border-radius: 2px; background: #3c3c3c; font-size: 11px;
    color: #eeeeee;
    selection-background-color: #2d5a9e;
}
QLineEdit:focus { border: 1px solid #5b8fd4; }
QComboBox {
    padding: 3px 6px; border: 1px solid #666666;
    border-radius: 2px; background: #3c3c3c; font-size: 11px;
    color: #eeeeee;
}
QComboBox QAbstractItemView {
    background: #3c3c3c;
    color: #eeeeee;
    selection-background-color: #2d5a9e;
}
QGroupBox {
    font-weight: bold; font-size: 11px;
    border: 1px solid #555555; border-radius: 4px;
    margin-top: 8px; padding-top: 14px;
    color: #9dc0fc;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
}
QTreeWidget {
    border: 1px solid #555555; background: #2d2d2d;
    font-size: 11px; alternate-background-color: #333333;
    color: #e8e8e8;
}
QTreeWidget::item:selected { background: #2d5a9e; color: white; }
QHeaderView::section {
    background: #404040; border: 1px solid #555555;
    padding: 4px; font-weight: bold; font-size: 10px;
    color: #e0e0e0;
}
QTextEdit#logText {
    background-color: #1a1a1a; color: #c8c8c8;
    font-family: Consolas, monospace; font-size: 10px;
    border: 1px solid #444444; padding: 6px;
}
QStatusBar {
    background: #383838; border-top: 1px solid #555555;
    font-size: 10px; color: #b0b0b0;
}
QStatusBar QLabel {
    color: #b0b0b0;
}
QLabel {
    color: #e4e4e4;
}
QCheckBox, QRadioButton {
    color: #e4e4e4;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 14px;
    height: 14px;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea#settingsTabScroll, QWidget#settingsTabViewport, QWidget#settingsTabScrollInner {
    background-color: #323232;
    border: none;
}
QLabel#settingsProxyHelp {
    color: #a0a0a0;
    font-size: 10px;
}
QSplitter::handle {
    background: #505050;
}
QSplitter::handle:hover {
    background: #5b8fd4;
}
QFrame {
    color: #e4e4e4;
}
QSlider::groove:horizontal {
    border: 1px solid #555555;
    height: 5px;
    background: #3a3a3a;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #5b8fd4;
    border: 1px solid #2d5a9e;
    width: 12px;
    margin: -5px 0;
    border-radius: 3px;
}
QDialog {
    background-color: #2b2b2b;
    color: #e4e4e4;
}
QMessageBox {
    background-color: #2b2b2b;
    color: #e4e4e4;
}
QMessageBox QLabel {
    color: #e4e4e4;
    background-color: transparent;
    font-size: 11px;
}
QMessageBox QPushButton {
    min-width: 72px;
    padding: 5px 14px;
}
QInputDialog {
    background-color: #2b2b2b;
    color: #e4e4e4;
}
QInputDialog QLabel {
    color: #e4e4e4;
    background-color: transparent;
}
QToolTip {
    background-color: #3c3c3c;
    color: #e4e4e4;
    border: 1px solid #666666;
    padding: 4px;
    font-size: 10px;
}
"""

# Ordu araçları (ArmyAuxToolsDialog): QMainWindow kuralları geçerli olmadığı için ek parça.
_ARMY_AUX_DIALOG_EXTRA_LIGHT = """
QDialog {
    background-color: #f0f0f0;
}
QTextEdit {
    background-color: #ffffff;
    color: #222222;
    border: 1px solid #999999;
    border-radius: 2px;
}
QListWidget {
    background-color: #ffffff;
    color: #222222;
    border: 1px solid #aaaaaa;
    font-size: 11px;
}
"""

_ARMY_AUX_DIALOG_EXTRA_DARK = """
QDialog {
    background-color: #2b2b2b;
}
QTextEdit {
    background-color: #3c3c3c;
    color: #eeeeee;
    border: 1px solid #666666;
    border-radius: 2px;
}
QListWidget {
    background-color: #3c3c3c;
    color: #eeeeee;
    border: 1px solid #666666;
    font-size: 11px;
}
"""

# MisyonerMultiWaveDialog: gömülü ana pencerenin koyu modundan bağımsız görünüm.
_MISYONER_MULTI_DIALOG_EXTRA_LIGHT = """
QDialog {
    background-color: #f0f0f0;
}
QLabel {
    color: #222222;
}
QTableWidget {
    background-color: #ffffff;
    alternate-background-color: #f5f5f5;
    color: #222222;
    gridline-color: #cccccc;
    selection-background-color: #cde8ff;
    selection-color: #000000;
    border: 1px solid #aaaaaa;
}
QHeaderView::section {
    background-color: #ececec;
    color: #222222;
    border: 1px solid #cccccc;
    padding: 4px;
}
QLineEdit {
    background-color: #ffffff;
    color: #222222;
    border: 1px solid #999999;
}
QSpinBox {
    background-color: #ffffff;
    color: #222222;
    border: 1px solid #999999;
}
QCheckBox {
    color: #222222;
}
"""

_MISYONER_MULTI_DIALOG_EXTRA_DARK = """
QDialog {
    background-color: #2b2b2b;
}
QLabel {
    color: #e8e8e8;
}
QTableWidget {
    background-color: #3c3c3c;
    alternate-background-color: #383838;
    color: #eeeeee;
    gridline-color: #555555;
    selection-background-color: #4a6070;
    selection-color: #ffffff;
    border: 1px solid #666666;
}
QTableWidget:disabled {
    background-color: #353535;
    color: #888888;
}
QHeaderView::section {
    background-color: #454545;
    color: #eeeeee;
    border: 1px solid #555555;
    padding: 4px;
}
QLineEdit {
    background-color: #3c3c3c;
    color: #eeeeee;
    border: 1px solid #666666;
}
QSpinBox {
    background-color: #3c3c3c;
    color: #eeeeee;
    border: 1px solid #666666;
}
QCheckBox {
    color: #e8e8e8;
}
"""


def _army_aux_dialog_stylesheet(dark: bool) -> str:
    base = STYLESHEET_DARK if dark else STYLESHEET
    extra = _ARMY_AUX_DIALOG_EXTRA_DARK if dark else _ARMY_AUX_DIALOG_EXTRA_LIGHT
    return base + extra


def _misyoner_multi_dialog_stylesheet(dark: bool) -> str:
    base = STYLESHEET_DARK if dark else STYLESHEET
    extra = _MISYONER_MULTI_DIALOG_EXTRA_DARK if dark else _MISYONER_MULTI_DIALOG_EXTRA_LIGHT
    return base + extra


# ─────────────────────────────────────────────
#  HARİTA CANVAS WİDGET (İnteraktif)
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  HARİTA CANVAS WİDGET (Tile-Based)
# ─────────────────────────────────────────────


class _TileFetchSignals(QObject):
    """Tile indirme thread'inden ana thread'e sinyal."""
    tile_ready = pyqtSignal(str, bytes)  # (cache_key, png_bytes)


class TileCache:
    """Harita tile görsellerini indirip önbelleğe alan yönetici.

    Tribal Wars tile URL formatı:
        {graphics_base}{tile_name}.png
    Örn:
        https://dstr.innogamescdn.com/asset/2fe6656b/graphic///map_new/v3.png
        https://dstr.innogamescdn.com/asset/2fe6656b/graphic///map_new/gras4.png
        https://dstr.innogamescdn.com/asset/2fe6656b/graphic///map_new/forest0101.png
    """

    def __init__(self):
        self._cache = {}           # cache_key → QPixmap
        self._pending = set()      # indirme bekleyenler
        self._signals = _TileFetchSignals()
        self._signals.tile_ready.connect(self._on_tile_ready)
        self._graphics_base = ""

    def set_graphics_base(self, url):
        """TWMap.graphics URL'sini ayarla."""
        self._graphics_base = url.rstrip("/") + "/"

    def get(self, tile_name):
        """Cache'den tile pixmap döndür. Yoksa None + indirme başlat."""
        if tile_name in self._cache:
            return self._cache[tile_name]
        if tile_name not in self._pending and self._graphics_base:
            self._pending.add(tile_name)
            self._download(tile_name)
        return None

    def _download(self, tile_name):
        """Arka planda tile indir."""
        url = self._graphics_base + tile_name
        if not url.endswith(".png") and not url.endswith(".webp"):
            url += ".png"

        def _worker():
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                if data:
                    self._signals.tile_ready.emit(tile_name, data)
            except Exception:
                self._pending.discard(tile_name)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_tile_ready(self, tile_name, data):
        """Ana thread'de QPixmap oluştur."""
        px = QPixmap()
        if px.loadFromData(data):
            self._cache[tile_name] = px
        self._pending.discard(tile_name)


# Uygulama geneli tek tile cache
_tile_cache = TileCache()


class MapCanvasWidget(QWidget):
    """Tile-tabanlı interaktif harita widget'ı.

    Oyunun gerçek harita tile görsellerini kullanır:
      - TWMap sektör verilerinden tile bilgisi alınır
      - Tile görselleri CDN'den indirilir ve cache'lenir
      - 53x38 piksel grid üzerinde çizilir

    Özellikler:
      - Fare ile sürükleyerek kaydırma (pan)
      - Scroll ile yakınlaştırma/uzaklaştırma (zoom)
      - Köye tıklayınca bilgi gösterme (tooltip)
      - Köye çift tıklayınca sinyal gönderme
      - Koordinat satır/sütun başlıkları
      - Yükleme / yenilemede görünüm ~VISIBLE_TILES×VISIBLE_TILES dünya karesine sığdırılır
    """

    village_double_clicked = pyqtSignal(int, int, int)
    village_clicked = pyqtSignal(object)
    view_changed = pyqtSignal(float, float)

    # Standart tile boyutu
    TILE_W = 53
    TILE_H = 38
    # Harita sekmesinde hedeflenen görünür kare sayısı (yatay × dikey, en fazla)
    VISIBLE_TILES = 11

    # Köy seviye → tile adları (getLevelForVillagePoints sonucuna göre)
    # Level 1: 0-299 puan, Level 2: 300-999, Level 3: 1000-2999,
    # Level 4: 3000-8999, Level 5: 9000-10999, Level 6: 11000+
    # Her seviyenin normal ve _left varyantı var (koordinata göre seçilir)
    VILLAGE_LEVEL_TILES = {
        1: ("v1.png", "v1_left.png"),
        2: ("v2.png", "v2_left.png"),
        3: ("v3.png", "v3.png"),         # v3'ün _left varyantı yok
        4: ("v4.png", "v4.png"),
        5: ("v5.png", "v5.png"),
        6: ("v6.png", "v6.png"),
    }

    # Çim tile'ları
    GRASS_TILES = ["gras1.png", "gras2.png", "gras3.png", "gras4.png"]

    BG_COLOR = "#5a7a32"       # Tribal Wars harita yeşili
    GRID_COLOR = "#4a6a28"
    HEADER_BG = "#3a5a18"
    HEADER_TEXT = "#ccddaa"
    COORD_FONT_SIZE = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._villages = []
        self._village_map = {}      # (x,y) → village dict
        self._sector_tiles = {}     # (x,y) → tile_name
        self._center_x = 500.0
        self._center_y = 500.0
        self._zoom = 1.0            # 1.0 = normal, <1 = uzak, >1 = yakın
        self._min_zoom = 0.3
        self._max_zoom = 3.0
        # Tile oranı ~53:38 — minimum boyutlar haritayı yatayda çok sıkıştırmasın
        _mh = 22 + int(self.VISIBLE_TILES * self.TILE_H * 0.75)
        _mw = max(440, int(_mh * (self.TILE_W / float(self.TILE_H))))
        self.setMinimumSize(_mw, _mh)
        self._zoom_user_override = False

        self._dragging = False
        self._drag_start_pos = None
        self._drag_start_cx = 0.0
        self._drag_start_cy = 0.0
        self._hovered_village = None

        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)

        # Tile yükleme zamanlayıcı — cache dolunca yeniden çiz
        self._repaint_timer = QTimer(self)
        self._repaint_timer.timeout.connect(self.update)
        self._repaint_timer.start(500)  # 500ms'de bir yenile (tile yüklendikçe)

    # ── Veri API ───────────────────────────────

    def set_data(self, villages, cx, cy, radius=None):
        """Köy listesi + merkez. radius yalnızca uyumluluk için (yakınlaştırmada kullanılmaz)."""
        self._villages = villages
        self._village_map = {}
        for v in villages:
            self._village_map[(v["x"], v["y"])] = v
        self._center_x = float(cx)
        self._center_y = float(cy)
        self._zoom_user_override = False
        self.fit_zoom_to_visible_grid(self.VISIBLE_TILES, self.VISIBLE_TILES)
        self.update()

    def set_all_villages(self, villages):
        self._villages = villages
        self._village_map = {}
        for v in villages:
            self._village_map[(v["x"], v["y"])] = v
        self.update()

    def set_sector_tiles(self, tiles):
        """Sektör tile verilerini ayarla. tiles: {(x,y): tile_name}"""
        self._sector_tiles = tiles
        self.update()

    def set_graphics_base(self, url):
        """TWMap.graphics URL'sini tile cache'e ilet."""
        _tile_cache.set_graphics_base(url)

    def get_view(self):
        return self._center_x, self._center_y, self._zoom

    def set_center(self, cx, cy):
        self._center_x = float(cx)
        self._center_y = float(cy)
        self.update()

    def fit_zoom_to_visible_grid(self, cols=None, rows=None):
        """Widget boyutuna göre zoom: cols×rows karesi tamamen panele sığacak şekilde ölçekle.
        min(z_w,z_h) — dar/yüksek veya geniş/alçak panellerde tek eksende 'ince şerit' oluşmaz."""
        cols = cols if cols is not None else self.VISIBLE_TILES
        rows = rows if rows is not None else self.VISIBLE_TILES
        w, h = max(1, self.width()), max(1, self.height())
        header = 22
        avail_h = max(1, h - header)
        z_w = w / float(cols * self.TILE_W)
        z_h = avail_h / float(rows * self.TILE_H)
        z = min(z_w, z_h)
        self._zoom = max(self._min_zoom, min(self._max_zoom, z))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._zoom_user_override:
            self.fit_zoom_to_visible_grid(self.VISIBLE_TILES, self.VISIBLE_TILES)
            self.update()

    # ── Koordinat dönüşümleri ──────────────────

    def _tile_pixel_size(self):
        """Zoom uygulanmış tile piksel boyutu."""
        return self.TILE_W * self._zoom, self.TILE_H * self._zoom

    def _world_to_pixel(self, wx, wy):
        """Dünya koordinatı → widget pikseli."""
        tw, th = self._tile_pixel_size()
        w = self.width()
        h = self.height()
        # Header alanı
        header = 22
        # Merkez piksel
        cx_px = w / 2
        cy_px = (h - header) / 2 + header
        px = cx_px + (wx - self._center_x) * tw
        py = cy_px + (wy - self._center_y) * th
        return px, py

    def _pixel_to_world(self, px, py):
        """Widget pikseli → dünya koordinatı."""
        tw, th = self._tile_pixel_size()
        w = self.width()
        h = self.height()
        header = 22
        cx_px = w / 2
        cy_px = (h - header) / 2 + header
        if tw == 0 or th == 0:
            return self._center_x, self._center_y
        wx = (px - cx_px) / tw + self._center_x
        wy = (py - cy_px) / th + self._center_y
        return wx, wy

    def _village_at_pixel(self, px, py):
        """Piksel konumundaki köyü bul."""
        tw, th = self._tile_pixel_size()
        half_w = tw / 2
        half_h = th / 2
        # Kaba koordinat
        wx, wy = self._pixel_to_world(px, py)
        ix, iy = int(round(wx)), int(round(wy))
        # Yakın komşuları da kontrol et
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                cx, cy = ix + dx, iy + dy
                v = self._village_map.get((cx, cy))
                if v:
                    vpx, vpy = self._world_to_pixel(cx, cy)
                    if abs(px - vpx) <= half_w and abs(py - vpy) <= half_h:
                        return v
        return None

    def _get_village_tile(self, points, x=0, y=0):
        """Köy puanına ve koordinatına göre tile adı.
        Oyunun getLevelForVillagePoints eşleşmesi:
          Level 1: 0-299, Level 2: 300-999, Level 3: 1000-2999,
          Level 4: 3000-8999, Level 5: 9000-10999, Level 6: 11000+
        Koordinata göre normal veya _left varyantı seçilir.
        """
        if points < 300:
            level = 1
        elif points < 1000:
            level = 2
        elif points < 3000:
            level = 3
        elif points < 9000:
            level = 4
        elif points < 11000:
            level = 5
        else:
            level = 6

        tiles = self.VILLAGE_LEVEL_TILES.get(level, ("v1.png", "v1_left.png"))
        # Koordinata göre yön seç (deterministik)
        use_left = ((x + y) % 2 == 0)
        return tiles[1] if use_left else tiles[0]

    def _get_grass_tile(self, x, y):
        """Koordinata göre deterministic çim tile'ı."""
        idx = ((x * 7 + y * 13) ^ (x * y)) % len(self.GRASS_TILES)
        return self.GRASS_TILES[idx]

    def _get_forest_tile(self, x, y):
        """Koordinata göre deterministic orman tile'ı (ağaç yoğunluğu)."""
        # Basit pattern — her 3-4 kareden biri orman
        h = ((x * 31 + y * 17) ^ (x + y * 7)) % 10
        if h < 3:
            # Orman tile kodu: 4-bit komşuluk
            bits = ((x + y) % 2, (x * 3 + y) % 2, (y * 5 + x) % 2, (x * y) % 2)
            code = f"forest{''.join(str(b) for b in bits)}.png"
            return code
        return None

    # ── Fare olayları ──────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start_pos = event.pos()
            self._drag_start_cx = self._center_x
            self._drag_start_cy = self._center_y
            self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._dragging and self._drag_start_pos:
                moved = (event.pos() - self._drag_start_pos).manhattanLength()
                if moved < 5:
                    v = self._village_at_pixel(event.pos().x(), event.pos().y())
                    if v:
                        self.village_clicked.emit(v)
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start_pos:
            tw, th = self._tile_pixel_size()
            if tw > 0 and th > 0:
                dx_px = event.pos().x() - self._drag_start_pos.x()
                dy_px = event.pos().y() - self._drag_start_pos.y()
                self._center_x = self._drag_start_cx - dx_px / tw
                self._center_y = self._drag_start_cy - dy_px / th
                self._center_x = max(0, min(999, self._center_x))
                self._center_y = max(0, min(999, self._center_y))
                self.update()
                self.view_changed.emit(self._center_x, self._center_y)
        else:
            v = self._village_at_pixel(event.pos().x(), event.pos().y())
            if v != self._hovered_village:
                self._hovered_village = v
                if v:
                    name = v.get("name", "?")
                    pts = v.get("points", "?")
                    coord = f"({v['x']}|{v['y']})"
                    pname = v.get("player_name", "")
                    pid = v.get("player_id", 0)
                    if pid and pname:
                        player = f" | {pname}"
                    elif pid:
                        player = f" | Oyuncu: {pid}"
                    else:
                        player = " | Barbar"
                    self.setToolTip(f"{name} {coord}\nPuan: {pts}{player}")
                else:
                    wx, wy = self._pixel_to_world(event.pos().x(), event.pos().y())
                    self.setToolTip(f"({int(wx)}|{int(wy)})")
                self.update()

    def mouseDoubleClickEvent(self, event):
        v = self._village_at_pixel(event.pos().x(), event.pos().y())
        if v:
            self.village_double_clicked.emit(v.get("id", 0), v["x"], v["y"])
        else:
            wx, wy = self._pixel_to_world(event.pos().x(), event.pos().y())
            self._center_x = wx
            self._center_y = wy
            self.update()
            self.view_changed.emit(self._center_x, self._center_y)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        mx, my = event.pos().x(), event.pos().y()
        wx_before, wy_before = self._pixel_to_world(mx, my)

        factor = 1.15 if delta > 0 else 1 / 1.15
        self._zoom_user_override = True
        self._zoom = max(self._min_zoom, min(self._max_zoom, self._zoom * factor))

        wx_after, wy_after = self._pixel_to_world(mx, my)
        self._center_x += wx_before - wx_after
        self._center_y += wy_before - wy_after
        self._center_x = max(0, min(999, self._center_x))
        self._center_y = max(0, min(999, self._center_y))

        self.update()
        self.view_changed.emit(self._center_x, self._center_y)

    # ── Çizim ──────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self._zoom > 0.8)

        w = self.width()
        h = self.height()
        tw, th = self._tile_pixel_size()
        header = 22  # Üst koordinat çubuğu

        if tw < 1 or th < 1:
            painter.fillRect(0, 0, w, h, QColor(self.BG_COLOR))
            painter.end()
            return

        # ── Arka plan ──
        painter.fillRect(0, 0, w, h, QColor(self.BG_COLOR))

        # ── Görünür tile aralığı ──
        wx_min, wy_min = self._pixel_to_world(0, header)
        wx_max, wy_max = self._pixel_to_world(w, h)
        x_start = int(wx_min) - 1
        y_start = int(wy_min) - 1
        x_end = int(wx_max) + 2
        y_end = int(wy_max) + 2

        # ── Tile'ları çiz ──
        hover_v = self._hovered_village

        for ty in range(y_start, y_end):
            for tx in range(x_start, x_end):
                px, py = self._world_to_pixel(tx, ty)
                # Tile sol üst köşesi
                draw_x = int(px - tw / 2)
                draw_y = int(py - th / 2)

                # Ekran dışı hızlı atla
                if draw_x + tw < 0 or draw_x > w or draw_y + th < header or draw_y > h:
                    continue

                # Köy var mı?
                village = self._village_map.get((tx, ty))

                if village:
                    # Köy tile'ı
                    pts = village.get("points", 0)
                    tile_name = self._get_village_tile(pts, tx, ty)
                    pixmap = _tile_cache.get(tile_name)

                    if pixmap:
                        painter.drawPixmap(draw_x, draw_y, int(tw), int(th), pixmap)
                    else:
                        # Tile yüklenene kadar renkli placeholder
                        color = QColor(village.get("color", "#888888"))
                        painter.fillRect(draw_x, draw_y, int(tw), int(th),
                            QColor(self.BG_COLOR))
                        dot = max(4, int(min(tw, th) * 0.4))
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(color))
                        painter.drawRect(int(px - dot/2), int(py - dot/2), dot, dot)

                    # Köy renkli overlay (sahiplik göstergesi — yarı saydam)
                    color = QColor(village.get("color", "#888888"))
                    overlay = QColor(color)
                    overlay.setAlpha(70)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(overlay))
                    painter.drawRect(draw_x, draw_y, int(tw), int(th))

                    # Köy renkli alt çizgi (kalın sahiplik çubuğu)
                    painter.setBrush(QBrush(color))
                    bar_h = max(3, int(th * 0.18))
                    painter.drawRect(draw_x, draw_y + int(th) - bar_h, int(tw), bar_h)

                    # Köy renkli kenar çizgisi
                    painter.setPen(QPen(color, max(1, int(self._zoom * 1.5))))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(draw_x, draw_y, int(tw), int(th))

                    # Hover vurgulama
                    if hover_v and village.get("x") == hover_v.get("x") and \
                       village.get("y") == hover_v.get("y"):
                        painter.setPen(QPen(QColor("#ffffff"), 2))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(draw_x, draw_y, int(tw), int(th))
                else:
                    # Arazi tile'ı
                    forest = self._get_forest_tile(tx, ty)
                    if forest:
                        pixmap = _tile_cache.get(forest)
                        if pixmap:
                            painter.drawPixmap(draw_x, draw_y, int(tw), int(th), pixmap)
                            continue

                    grass = self._get_grass_tile(tx, ty)
                    pixmap = _tile_cache.get(grass)
                    if pixmap:
                        painter.drawPixmap(draw_x, draw_y, int(tw), int(th), pixmap)
                    # Yoksa arka plan rengi zaten çizili

        # ── Grid çizgileri (zoom yakınken) ──
        if tw >= 15:
            painter.setPen(QPen(QColor(self.GRID_COLOR), 1))
            painter.setOpacity(0.3)
            for ty in range(y_start, y_end):
                _, py = self._world_to_pixel(x_start, ty)
                line_y = int(py - th / 2)
                if header <= line_y <= h:
                    painter.drawLine(0, line_y, w, line_y)
            for tx in range(x_start, x_end):
                px, _ = self._world_to_pixel(tx, y_start)
                line_x = int(px - tw / 2)
                if 0 <= line_x <= w:
                    painter.drawLine(line_x, header, line_x, h)
            painter.setOpacity(1.0)

        # ── Koordinat başlıkları ──
        font = painter.font()
        font.setPixelSize(self.COORD_FONT_SIZE)
        painter.setFont(font)

        # Üst yatay başlık (X koordinatları)
        painter.fillRect(0, 0, w, header, QColor(self.HEADER_BG))
        painter.setPen(QColor(self.HEADER_TEXT))
        # Her tile veya her N tile'da bir numara yaz
        step = max(1, int(20 / max(tw, 1)))  # Çok sık yazmamak için
        for tx in range(x_start, x_end):
            if tx % max(step, 1) != 0:
                continue
            px, _ = self._world_to_pixel(tx, y_start)
            text_x = int(px) - 10
            if 0 <= text_x <= w - 20:
                painter.drawText(text_x, 14, str(tx))

        # Sol dikey başlık (Y koordinatları)
        left_w = 30
        painter.fillRect(0, header, left_w, h - header, QColor(self.HEADER_BG))
        painter.setPen(QColor(self.HEADER_TEXT))
        for ty in range(y_start, y_end):
            if ty % max(step, 1) != 0:
                continue
            _, py = self._world_to_pixel(x_start, ty)
            text_y = int(py) + 4
            if header <= text_y <= h:
                painter.drawText(2, text_y, str(ty))

        # ── Hover bilgi kutusu ──
        if hover_v:
            self._draw_hover_box(painter, hover_v, w, h)

        # ── Kıta etiketi ──
        if tw >= 8:
            font.setPixelSize(max(9, int(tw * 0.5)))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(200, 200, 150, 100))
            kx_s = (x_start // 100) * 100
            ky_s = (y_start // 100) * 100
            for kx in range(kx_s, x_end + 100, 100):
                for ky in range(ky_s, y_end + 100, 100):
                    px, py = self._world_to_pixel(kx + 50, ky + 50)
                    if 40 < px < w - 30 and header + 10 < py < h - 10:
                        continent = f"K{(ky // 100) * 10 + kx // 100}"
                        painter.drawText(int(px) - 10, int(py) + 4, continent)

        painter.end()

    def _draw_hover_box(self, painter, v, w, h):
        """Köy bilgi kutusu."""
        px, py = self._world_to_pixel(v["x"], v["y"])

        name = v.get("name", "?")
        coord = f"({v['x']}|{v['y']})"
        continent = f"K{(v['y'] // 100) * 10 + v['x'] // 100}"
        pts = f"Puan: {v.get('points', '?')}"
        pid = v.get("player_id", 0)
        pname = v.get("player_name", "")
        if pid and pname:
            ptext = f"Sahibi: {pname}"
        elif pid:
            ptext = f"Sahibi: ID {pid}"
        else:
            ptext = "Terk edilmiş"

        lines = [f"{name} {coord} {continent}", pts, ptext]
        line_h = 16
        box_w = max(len(l) * 7 + 4 for l in lines) + 16
        box_h = len(lines) * line_h + 10

        bx = int(px) + 15
        by = int(py) - box_h - 5
        if bx + box_w > w:
            bx = int(px) - box_w - 15
        if by < 22:
            by = int(py) + 15

        # Arka plan
        painter.setPen(QPen(QColor("#8a8a6a"), 1))
        painter.setBrush(QBrush(QColor(40, 35, 25, 230)))
        painter.drawRoundedRect(bx, by, box_w, box_h, 3, 3)

        # Başlık çizgisi
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(60, 50, 30, 200)))
        painter.drawRect(bx + 1, by + 1, box_w - 2, line_h + 2)

        # Metin
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        for i, line in enumerate(lines):
            if i == 0:
                painter.setPen(QColor("#ffeecc"))
                font.setBold(True)
                painter.setFont(font)
            else:
                painter.setPen(QColor("#ccccaa"))
                font.setBold(False)
                painter.setFont(font)
            painter.drawText(bx + 8, by + 14 + i * line_h, line)

# ─────────────────────────────────────────────
#  HARİTA ORDU GÖNDER DİALOGU
# ─────────────────────────────────────────────

class MapArmySendDialog(QDialog):
    def __init__(self, parent, queue, game_data, unit_defs, server_time_text=""):
        super().__init__(parent)
        self.setWindowTitle("⚔️ Ordu Gönder — Harita Kuyruğu")
        self.setMinimumSize(880, 520)
        self.resize(920, 580)
        self._queue = queue
        self._game_data = game_data
        self._unit_defs = unit_defs
        self._server_time_text = server_time_text
        self._results = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Kaynak köy seçimi (Ordu Gönder sekmesiyle aynı: all_villages + id, askerler id ile çözülür)
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Kaynak:"))
        self.src_combo = QComboBox()
        self.src_combo.setMinimumWidth(220)
        all_v = self._game_data.get("all_villages", [])
        current_id = self._game_data.get("village", {}).get("id", 0)
        if all_v:
            sel = 0
            for i, v in enumerate(all_v):
                name = v.get("name", "Köy")
                coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                self.src_combo.addItem(f"{name} {coord}", v.get("id", 0))
                if v.get("id") == current_id or v.get("selected"):
                    sel = i
            self.src_combo.setCurrentIndex(sel)
        else:
            v = self._game_data.get("village", {})
            if v:
                name = v.get("name", "Köy")
                coord = v.get("coord", f"({v.get('x', '?')}|{v.get('y', '?')})")
                if "(" not in str(coord):
                    coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                self.src_combo.addItem(f"{name} {coord}", v.get("id", 0))
            else:
                villages = self._game_data.get("villages", [])
                if villages:
                    for vv in villages:
                        name = vv.get("name", "Köy")
                        coord = f"({vv.get('x', '?')}|{vv.get('y', '?')})"
                        self.src_combo.addItem(f"{name} {coord}", vv.get("id", 0))
                else:
                    self.src_combo.addItem("— Köy bulunamadı —")
        self.src_combo.currentIndexChanged.connect(self._on_source_changed)
        src_row.addWidget(self.src_combo)
        src_row.addStretch()
        layout.addLayout(src_row)

        # ── Asker giriş alanları ──
        troop_group = QGroupBox("Asker Seçimi")
        troop_layout = QHBoxLayout()
        troop_layout.setSpacing(2)

        self.troop_inputs = {}
        self.troop_avail = {}
        for key, short in self._unit_defs:
            unit_frame = QFrame()
            unit_frame.setStyleSheet(
                "border: 1px solid #d0c8b0; border-radius: 3px; padding: 1px;"
                "background: #faf5eb;")
            uf_layout = QVBoxLayout(unit_frame)
            uf_layout.setContentsMargins(3, 2, 3, 2)
            uf_layout.setSpacing(1)

            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setFixedHeight(20)
            icon_lbl.setStyleSheet("border: none;")
            troop_icon_mgr.apply_to_label(icon_lbl, key)
            uf_layout.addWidget(icon_lbl)

            name_lbl = QLabel(short)
            name_lbl.setAlignment(Qt.AlignCenter)
            name_lbl.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #5a3e1b; border: none;")
            uf_layout.addWidget(name_lbl)

            spin = QSpinBox()
            spin.setRange(0, 99999)
            spin.setValue(0)
            spin.setFixedWidth(58)
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet(
                "font-size: 12px; border: 1px solid #b89b6a; background: white;")
            uf_layout.addWidget(spin)
            self.troop_inputs[key] = spin

            avail_lbl = QLabel("(0)")
            avail_lbl.setAlignment(Qt.AlignCenter)
            avail_lbl.setStyleSheet("font-size: 13px; color: #888; border: none;")
            uf_layout.addWidget(avail_lbl)
            self.troop_avail[key] = avail_lbl

            troop_layout.addWidget(unit_frame)

        troop_layout.addStretch()
        troop_group.setLayout(troop_layout)
        troop_group.setStyleSheet(
            "QGroupBox { font-weight: bold; font-size: 11px; color: #5a3e1b;"
            "border: 1px solid #b89b6a; border-radius: 4px; margin-top: 8px; padding-top: 14px;"
            "background: qlineargradient(y1:0,y2:1,stop:0 #f8f0e0,stop:1 #ece0cc); }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        layout.addWidget(troop_group)

        # ── Tür + Zaman ──
        opt_row = QHBoxLayout()
        opt_row.setSpacing(10)

        opt_row.addWidget(QLabel("Tür:"))
        self.cmd_type_combo = QComboBox()
        self.cmd_type_combo.addItems(["Saldırı", "Destek"])
        self.cmd_type_combo.setFixedWidth(90)
        self.cmd_type_combo.setStyleSheet(
            "background: #faf5eb; border: 1px solid #b89b6a; padding: 3px;")
        opt_row.addWidget(self.cmd_type_combo)

        opt_row.addSpacing(20)

        self.btn_arrive = QPushButton("Varış zamanı ayarla")
        self.btn_arrive.setCursor(Qt.PointingHandCursor)
        self.btn_arrive.setCheckable(True)
        self.btn_arrive.setStyleSheet(
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #5a3e1b;")
        self.btn_arrive.clicked.connect(lambda: self._toggle_time("arrive"))
        opt_row.addWidget(self.btn_arrive)

        self.btn_send = QPushButton("Gönderim zamanı ayarla")
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.setCheckable(True)
        self.btn_send.setStyleSheet(
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #5a3e1b;")
        self.btn_send.clicked.connect(lambda: self._toggle_time("send"))
        opt_row.addWidget(self.btn_send)

        opt_row.addStretch()
        layout.addLayout(opt_row)

        # Zaman giriş satırı
        self.time_widget = QWidget()
        time_row = QHBoxLayout(self.time_widget)
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.setSpacing(6)

        self.time_label = QLabel("Varış zamanı:")
        self.time_label.setStyleSheet("font-weight: bold; color: #5a3e1b; font-size: 11px;")
        time_row.addWidget(self.time_label)

        self.time_date = QLineEdit()
        self.time_date.setPlaceholderText("GG.AA")
        self.time_date.setFixedWidth(55)
        self.time_date.setAlignment(Qt.AlignCenter)
        self.time_date.setStyleSheet("border: 1px solid #b89b6a; padding: 3px;")
        time_row.addWidget(self.time_date)

        time_row.addWidget(QLabel("'de"))

        self.time_clock = QLineEdit()
        self.time_clock.setPlaceholderText("SS:DD:SS:ms")
        self.time_clock.setFixedWidth(110)
        self.time_clock.setAlignment(Qt.AlignCenter)
        self.time_clock.setStyleSheet("border: 1px solid #b89b6a; padding: 3px;")
        time_row.addWidget(self.time_clock)

        time_row.addStretch()
        self.time_widget.setVisible(False)
        layout.addWidget(self.time_widget)

        self._time_mode = None

        # ── Hedef kuyruğu tablosu ──
        q_group = QGroupBox(f"Hedefler ({len(self._queue)} köy)")
        q_layout = QVBoxLayout()
        self.target_table = QTreeWidget()
        self.target_table.setHeaderLabels(["", "Koordinat", "Köy Adı", "Puan", "Sahip"])
        self.target_table.setRootIsDecorated(False)
        self.target_table.setAlternatingRowColors(True)
        self.target_table.setColumnWidth(0, 30)
        self.target_table.setColumnWidth(1, 80)
        self.target_table.setColumnWidth(2, 120)
        self.target_table.setColumnWidth(3, 60)
        self.target_table.setColumnWidth(4, 100)

        for i, v in enumerate(self._queue):
            coord = f"({v['x']}|{v['y']})"
            name = v.get("name", "?")
            pts = str(v.get("points", "?"))
            pid = v.get("player_id", 0)
            pname = v.get("player_name", "")
            owner = pname if (pid and pname) else ("Barbar" if not pid else f"ID:{pid}")
            item = QTreeWidgetItem([str(i + 1), coord, name, pts, owner])
            item.setTextAlignment(0, Qt.AlignCenter)
            item.setTextAlignment(1, Qt.AlignCenter)
            item.setTextAlignment(3, Qt.AlignCenter)
            self.target_table.addTopLevelItem(item)

        q_layout.addWidget(self.target_table)
        q_group.setLayout(q_layout)
        q_group.setStyleSheet(
            "QGroupBox { font-weight: bold; font-size: 11px; color: #5a3e1b;"
            "border: 1px solid #b89b6a; border-radius: 4px; margin-top: 8px; padding-top: 14px;"
            "background: #faf8f2; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        layout.addWidget(q_group, 1)

        # ── Alt butonlar ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton("İptal")
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setFixedWidth(100)
        btn_cancel.setMinimumHeight(32)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_add = QPushButton("+ Tabloya Ekle")
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.setFixedWidth(160)
        btn_add.setMinimumHeight(32)
        btn_add.setStyleSheet(
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 4px; padding: 6px 16px;"
            "font-weight: bold; font-size: 12px; color: #5a3e1b;")
        btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(btn_add)

        layout.addLayout(btn_row)

        self._on_source_changed(self.src_combo.currentIndex())

    def _on_source_changed(self, index):
        """Ordu Gönder sekmesindeki _sa_on_source_changed ile aynı kaynak: all_villages + troops."""
        if index < 0:
            return
        village_id = self.src_combo.currentData()
        if not village_id:
            for lbl in self.troop_avail.values():
                lbl.setText("(0)")
                lbl.setStyleSheet("font-size: 13px; color: #888; border: none;")
            return

        all_v = self._game_data.get("all_villages", [])
        found_troops = None
        for v in all_v:
            if v.get("id") == village_id:
                found_troops = v.get("troops", {})
                break

        if found_troops is None:
            v = self._game_data.get("village", {})
            if v and v.get("id") == village_id:
                found_troops = self._game_data.get("troops", {})

        if found_troops is None:
            found_troops = {}

        for key, lbl in self.troop_avail.items():
            count = found_troops.get(key, 0)
            lbl.setText(f"({count})")
            if count > 0:
                lbl.setStyleSheet(
                    "font-size: 13px; font-weight: bold; color: #1a6b1a; border: none;")
            else:
                lbl.setStyleSheet("font-size: 13px; color: #888; border: none;")

    def _resolve_source_xy(self):
        """Seçili kaynak köyün x,y (mesafe hesabı için)."""
        village_id = self.src_combo.currentData()
        if not village_id:
            return 500, 500
        for v in self._game_data.get("all_villages", []):
            if v.get("id") == village_id:
                return v.get("x", 500), v.get("y", 500)
        v = self._game_data.get("village", {})
        if v.get("id") == village_id:
            return v.get("x", 500), v.get("y", 500)
        for v in self._game_data.get("villages", []):
            if v.get("id") == village_id:
                return v.get("x", 500), v.get("y", 500)
        return 500, 500

    def _toggle_time(self, mode):
        if mode == "arrive":
            self.btn_arrive.setChecked(True)
            self.btn_send.setChecked(False)
            self.time_label.setText("Varış zamanı:")
            self._time_mode = "arrive"
        else:
            self.btn_send.setChecked(True)
            self.btn_arrive.setChecked(False)
            self.time_label.setText("Gönderim zamanı:")
            self._time_mode = "send"
        self._fill_default_time()
        self.time_widget.setVisible(True)

    def _fill_default_time(self):
        """Her butona basıldığında güncel sunucu saatini (varsa) veya yerel saati doldur."""
        # Parent'tan (bot) güncel sunucu saatini al
        bot = self.parent()
        if bot and hasattr(bot, '_server_time_synced') and bot._server_time_synced and hasattr(bot, '_server_time_text') and bot._server_time_text:
            self._server_time_text = bot._server_time_text
            self._fill_from_server_time()
        elif self._server_time_text:
            self._fill_from_server_time()
        else:
            now = datetime.datetime.now()
            self.time_date.setText(now.strftime("%d.%m"))
            ms = now.microsecond // 1000
            self.time_clock.setText(now.strftime("%H:%M:%S:") + f"{ms:03d}")

    def _fill_from_server_time(self):
        """Sunucu saati text'ini parse edip form alanlarına yaz.
        Format: '18/03/2026 4:00:01.234'"""
        text = self._server_time_text
        if not text:
            return
        try:
            parts = text.split(" ", 1)
            if len(parts) != 2:
                return
            date_part = parts[0].strip()
            time_part = parts[1].strip()

            date_match = re.match(r'(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})', date_part)
            if date_match:
                day = date_match.group(1).zfill(2)
                month = date_match.group(2).zfill(2)
                self.time_date.setText(f"{day}.{month}")

            time_match = re.match(r'(\d{1,2}):(\d{2}):(\d{2})\.?(\d{0,3})', time_part)
            if time_match:
                hour = time_match.group(1).zfill(2)
                minute = time_match.group(2)
                second = time_match.group(3)
                ms = (time_match.group(4) or "0")[:3].zfill(3)
                self.time_clock.setText(f"{hour}:{minute}:{second}:{ms}")
        except Exception:
            pass

    def _on_add(self):
        has_troops = any(spin.value() > 0 for spin in self.troop_inputs.values())
        if not has_troops:
            QMessageBox.warning(self, "Uyarı", "En az bir asker girin!")
            return

        src_text = self.src_combo.currentText()

        troops = {}
        for key, spin in self.troop_inputs.items():
            if spin.value() > 0:
                troops[key] = spin.value()

        cmd_type = "Sld" if self.cmd_type_combo.currentIndex() == 0 else "Dst"

        import math

        src_x, src_y = self._resolve_source_xy()

        UNIT_SPEEDS = {
            "spear": 18, "sword": 22, "axe": 18, "archer": 18,
            "spy": 9, "light": 10, "marcher": 10, "heavy": 11,
            "ram": 30, "catapult": 30, "knight": 10, "snob": 35,
        }

        time_date = self.time_date.text().strip() if self._time_mode else ""
        time_clock = self.time_clock.text().strip() if self._time_mode else ""

        for v in self._queue:
            tgt_x, tgt_y = v["x"], v["y"]
            distance = math.sqrt((tgt_x - src_x) ** 2 + (tgt_y - src_y) ** 2)

            slowest = 0
            for unit_key in troops:
                spd = UNIT_SPEEDS.get(unit_key, 18)
                if spd > slowest:
                    slowest = spd
            travel_sec = distance * slowest * 60 if slowest else 0

            send_str, arrive_str, return_str = "—", "—", "—"
            if self._time_mode and time_date and time_clock:
                input_dt = self._parse_time(time_date, time_clock)
                if input_dt:
                    travel_delta = datetime.timedelta(seconds=travel_sec)
                    if self._time_mode == "send":
                        send_dt = input_dt
                        arrive_dt = send_dt + travel_delta
                    else:
                        arrive_dt = input_dt
                        send_dt = arrive_dt - travel_delta
                    return_dt = arrive_dt + travel_delta
                    send_str = self._format_dt(send_dt)
                    arrive_str = self._format_dt(arrive_dt)
                    return_str = self._format_dt(return_dt, ms_zero=True)

            self._results.append({
                "source": src_text,
                "tgt_x": tgt_x,
                "tgt_y": tgt_y,
                "troops": troops,
                "cmd_type": cmd_type,
                "send_time": send_str,
                "arrive_time": arrive_str,
                "return_time": return_str,
                "time_mode": self._time_mode,
            })

        self.accept()

    @staticmethod
    def _format_dt(dt, ms_zero=False):
        """datetime → dispatch'in beklediği "GG.AA'de SS:DD:SS:ms" formatı."""
        ms = 0 if ms_zero else dt.microsecond // 1000
        return f"{dt.day:02d}.{dt.month:02d}'de {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}:{ms:03d}"

    def _parse_time(self, date_str, clock_str):
        try:
            now = datetime.datetime.now()
            parts = date_str.split(".")
            day = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else now.month
            year = now.year

            time_parts = clock_str.replace(":", " ").split()
            hour = int(time_parts[0]) if len(time_parts) > 0 else 0
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0
            sec = int(time_parts[2]) if len(time_parts) > 2 else 0
            ms = int(time_parts[3]) if len(time_parts) > 3 else 0

            return datetime.datetime(year, month, day, hour, minute, sec, ms * 1000)
        except Exception:
            return None

    def get_results(self):
        return self._results


class MisyonerMultiWaveDialog(QDialog):
    """Çok dalgalı misyoner kuyruğu — tek gönderim zamanı; Ana «Ordu Gönder» kaynak/hedef/türünü kullanır."""

    def __init__(self, parent, bot):
        super().__init__(parent)
        self._bot = bot
        self.setWindowTitle("Misyoner — çok dalga")
        self.setMinimumWidth(720)
        self.resize(900, 440)

        max_w = int(getattr(bot, "SA_DISPATCH_MAX_BATCH", 5) or 5)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        hint = QLabel(
            "Kaynak, hedef ve komut türü «Ordu Gönder» sekmesinden alınır. "
            "Varış veya gönderim zamanı seçin; birinci dalgaya göre sonraki her dalga yaklaşık "
            f"{getattr(bot, 'SA_DISPATCH_WAVE_GAP_MS', 200)} ms arayla eklenir (oyunla uyumlu)."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Dalga sayısı:"))
        self._wave_spin = QSpinBox()
        self._wave_spin.setRange(1, max_w)
        self._wave_spin.setFixedWidth(56)
        row1.addWidget(self._wave_spin)
        self._auto_split_cb = QCheckBox("Eskort + misyoner otomatik böl")
        self._auto_split_cb.setToolTip(
            "Kaynak köydeki balta, hafif, koç, atlı okçu, casus ve misyonerleri dalga sayısına böler "
            "(her dalgada en fazla 1 misyoner)."
        )
        row1.addWidget(self._auto_split_cb)
        row1.addStretch()
        root.addLayout(row1)

        self._unit_defs = list(bot.SA_UNIT_DEFS)
        self._table = QTableWidget()
        self._table.setColumnCount(len(self._unit_defs))
        self._table.setHorizontalHeaderLabels([s for _, s in self._unit_defs])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(True)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table, 1)

        self._wave_spin.blockSignals(True)
        self._wave_spin.setValue(1)
        self._wave_spin.blockSignals(False)
        self._wave_spin.valueChanged.connect(self._resize_wave_rows)

        _btn_style = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 3px; padding: 4px 10px;"
            "font-weight: bold; font-size: 11px; color: #5a3e1b;"
        )
        _btn_checked_style = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #d4b896,stop:1 #b89b6a);"
            "border: 2px solid #7a5a30; border-radius: 3px; padding: 3px 9px;"
            "font-weight: bold; font-size: 11px; color: #3a2010;"
        )
        btn_row = QHBoxLayout()
        self._btn_set_arrive = QPushButton("Varış zamanı ayarla")
        self._btn_set_arrive.setCursor(Qt.PointingHandCursor)
        self._btn_set_arrive.setCheckable(True)
        self._btn_set_arrive.setStyleSheet(_btn_style)
        self._btn_set_arrive.clicked.connect(self._toggle_time_mode)
        btn_row.addWidget(self._btn_set_arrive)
        self._btn_set_send = QPushButton("Gönderim zamanı ayarla")
        self._btn_set_send.setCursor(Qt.PointingHandCursor)
        self._btn_set_send.setCheckable(True)
        self._btn_set_send.setStyleSheet(_btn_style)
        self._btn_set_send.clicked.connect(self._toggle_time_mode)
        btn_row.addWidget(self._btn_set_send)
        btn_row.addStretch()
        root.addLayout(btn_row)
        self._btn_style_normal = _btn_style
        self._btn_style_checked = _btn_checked_style

        time_inner = QHBoxLayout()
        self._time_label = QLabel("Gönderim zamanı:")
        self._time_label.setStyleSheet("font-weight: bold; color: #5a3e1b; font-size: 11px;")
        time_inner.addWidget(self._time_label)
        self._time_date = QLineEdit()
        self._time_date.setPlaceholderText("GG.AA")
        self._time_date.setFixedWidth(56)
        self._time_date.setAlignment(Qt.AlignCenter)
        time_inner.addWidget(self._time_date)
        time_inner.addWidget(QLabel("'de"))
        self._time_clock = QLineEdit()
        self._time_clock.setPlaceholderText("SS:DD:SS:ms")
        self._time_clock.setFixedWidth(110)
        self._time_clock.setAlignment(Qt.AlignCenter)
        time_inner.addWidget(self._time_clock)
        b_srv = QPushButton("Sunucu saati")
        b_srv.setCursor(Qt.PointingHandCursor)
        b_srv.clicked.connect(self._fill_server_time)
        time_inner.addWidget(b_srv)
        time_inner.addStretch()
        self._time_widget = QWidget()
        self._time_widget.setLayout(time_inner)
        self._time_widget.setVisible(False)
        root.addWidget(self._time_widget)
        self._time_mode = None

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self._auto_split_cb.stateChanged.connect(self._toggle_manual_table)

        # Köydeki mevcut birlikleri çek (hücre doğrulama için)
        try:
            sx, sy = bot._sa_get_source_coords()
            v = bot._sa_find_village_at_coord(sx, sy) if sx is not None else None
            self._troops_avail = (v.get("troops") or {}) if v else {}
        except Exception:
            self._troops_avail = {}

        self._resize_wave_rows()
        self._toggle_manual_table()
        self._table.cellChanged.connect(self._validate_cells)

        dark = bool(getattr(bot, "_dark_mode", False))
        self.setStyleSheet(_misyoner_multi_dialog_stylesheet(dark))

    def _toggle_manual_table(self):
        self._table.setEnabled(not self._auto_split_cb.isChecked())

    def _resize_wave_rows(self):
        n = self._wave_spin.value()
        old_r = self._table.rowCount()
        self._table.setRowCount(n)
        for r in range(old_r, n):
            for c in range(len(self._unit_defs)):
                it = QTableWidgetItem("0")
                it.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(r, c, it)
        for r in range(n):
            self._table.setVerticalHeaderItem(r, QTableWidgetItem(str(r + 1)))
        self._validate_cells()

    def _validate_cells(self):
        """Her sütunda birikimli toplam köydeki mevcut birliği aşarsa hücreyi kırmızı yap."""
        troops = getattr(self, "_troops_avail", {})
        self._table.blockSignals(True)
        try:
            for c, (key, _) in enumerate(self._unit_defs):
                avail = int(troops.get(key, 0) or 0)
                cumulative = 0
                for r in range(self._table.rowCount()):
                    item = self._table.item(r, c)
                    if not item:
                        continue
                    try:
                        val = max(0, int(item.text() or 0))
                    except ValueError:
                        val = 0
                    remaining = avail - cumulative
                    if avail > 0 and val > remaining:
                        item.setBackground(QColor("#c0392b"))
                        item.setForeground(QColor("#ffffff"))
                    else:
                        item.setData(Qt.BackgroundRole, None)
                        item.setData(Qt.ForegroundRole, None)
                    cumulative += val
        finally:
            self._table.blockSignals(False)

    def _fill_server_time(self):
        text = getattr(self._bot, "_server_time_text", "") or ""
        if not text or not getattr(self._bot, "_server_time_synced", False):
            return
        try:
            parts = text.split(" ", 1)
            if len(parts) != 2:
                return
            date_part = parts[0].strip()
            time_part = parts[1].strip()
            date_match = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", date_part)
            if date_match:
                day = date_match.group(1).zfill(2)
                month = date_match.group(2).zfill(2)
                self._time_date.setText(f"{day}.{month}")
            time_match = re.match(r"(\d{1,2}):(\d{2}):(\d{2})\.?(\d{0,3})", time_part)
            if time_match:
                h = time_match.group(1).zfill(2)
                m = time_match.group(2)
                s = time_match.group(3)
                ms = (time_match.group(4) or "0")[:3].zfill(3)
                self._time_clock.setText(f"{h}:{m}:{s}:{ms}")
        except Exception:
            pass

    def _toggle_time_mode(self):
        sender = self.sender()
        if sender == self._btn_set_arrive:
            if self._btn_set_arrive.isChecked():
                self._time_mode = "arrive"
                self._time_label.setText("Varış zamanı:")
                self._time_widget.setVisible(True)
                self._fill_server_time()
                self._btn_set_send.setChecked(False)
            else:
                self._time_mode = None
                self._time_widget.setVisible(False)
        elif sender == self._btn_set_send:
            if self._btn_set_send.isChecked():
                self._time_mode = "send"
                self._time_label.setText("Gönderim zamanı:")
                self._time_widget.setVisible(True)
                self._fill_server_time()
                self._btn_set_arrive.setChecked(False)
            else:
                self._time_mode = None
                self._time_widget.setVisible(False)
        self._btn_set_arrive.setStyleSheet(
            self._btn_style_checked if self._btn_set_arrive.isChecked() else self._btn_style_normal
        )
        self._btn_set_send.setStyleSheet(
            self._btn_style_checked if self._btn_set_send.isChecked() else self._btn_style_normal
        )

    def _read_troops_from_row(self, r):
        d = {}
        for c, (k, _) in enumerate(self._unit_defs):
            it = self._table.item(r, c)
            raw = (it.text() if it else "").strip()
            try:
                d[k] = max(0, int(raw))
            except ValueError:
                d[k] = 0
        return d

    def _on_accept(self):
        bot = self._bot
        if "Köy Seçin" in bot.sa_source_combo.currentText():
            QMessageBox.warning(self, "Uyarı", "Ana sekmede kaynak köy seçin.")
            return
        if not bot._sa_sync_quick_target_to_spinboxes():
            QMessageBox.warning(self, "Uyarı", "Hedef koordinatları geçerli değil.")
            return
        src_text = bot.sa_source_combo.currentText()
        src_x, src_y = bot._sa_get_source_coords()
        if src_x is None:
            QMessageBox.warning(self, "Uyarı", "Kaynak koordinat bulunamadı.")
            return
        tgt_x = bot.sa_tgt_x.value()
        tgt_y = bot.sa_tgt_y.value()
        if not self._time_mode:
            QMessageBox.warning(
                self, "Uyarı",
                "'Varış zamanı ayarla' veya 'Gönderim zamanı ayarla' butonuna basın."
            )
            return
        td = self._time_date.text().strip()
        tc = self._time_clock.text().strip()
        if not td or not tc:
            QMessageBox.warning(self, "Uyarı", "Tarih ve saati girin.")
            return
        input_dt = bot._sa_parse_time_input(td, tc)
        if input_dt is None:
            QMessageBox.warning(self, "Uyarı", "Zaman formatı hatalı (GG.AA ve SS:DD:SS:ms).")
            return
        cmd_attack = bot.cmd_type_combo.currentIndex() == 0
        n = self._wave_spin.value()

        if self._auto_split_cb.isChecked():
            v = bot._sa_find_village_at_coord(src_x, src_y)
            if not v:
                QMessageBox.warning(self, "Uyarı", "Kaynak köy veri içinde bulunamadı.")
                return
            troops_avail = v.get("troops") or {}
            troops_list = [bot._sa_troops_noble_split_wave(troops_avail, n, w) for w in range(n)]
        else:
            troops_list = [self._read_troops_from_row(r) for r in range(n)]

        # "arrive" modunda: varış zamanından yolculuğu çıkararak gönderim zamanını hesapla
        if self._time_mode == "arrive":
            try:
                used_keys = set()
                for r in range(n):
                    for c, (key, _) in enumerate(self._unit_defs):
                        it = self._table.item(r, c)
                        try:
                            if int((it.text() if it else "0") or 0) > 0:
                                used_keys.add(key)
                        except ValueError:
                            pass
                if not used_keys:
                    used_keys = {"snob"}
                distance = math.sqrt(
                    (float(tgt_x) - float(src_x)) ** 2 + (float(tgt_y) - float(src_y)) ** 2
                )
                travel_sec = bot._sa_calc_travel_time(distance, list(used_keys))
                base_send_dt = input_dt - datetime.timedelta(seconds=travel_sec)
            except Exception:
                base_send_dt = input_dt
        else:
            base_send_dt = input_dt

        gap_ms = int(getattr(bot, "SA_DISPATCH_WAVE_GAP_MS", 200) or 200)
        added = 0
        errs = []
        for w, troops_map in enumerate(troops_list):
            wave_send_dt = base_send_dt + datetime.timedelta(milliseconds=w * gap_ms)
            ok, err = bot._sa_append_row_from_values(
                src_text,
                src_x,
                src_y,
                tgt_x,
                tgt_y,
                troops_map,
                cmd_attack,
                "send",
                wave_send_dt,
                fake_dialog=False,
            )
            if ok:
                added += 1
            else:
                errs.append(f"Dalga {w + 1}: {err or '?'}")

        if added > 0:
            if errs:
                QMessageBox.warning(self, "Uyarı", "Bazı dalgalar eklenemedi:\n" + "\n".join(errs))
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "Uyarı",
                "\n".join(errs) if errs else "Hiçbir satır eklenemedi (birlik sayıları 0 olabilir).",
            )


class ArmyAuxToolsDialog(QDialog):
    """Toplu yapıştır, operasyon planı (Kami köyü özeti), hedef listesi — sekmeli pencere."""

    def __init__(self, bot, initial_page: int = 0):
        super().__init__(bot)
        self.bot = bot
        self.setWindowTitle("Ordu araçları")
        self.setMinimumSize(560, 400)
        self.resize(760, 480)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        self._tw = QTabWidget()
        self._tw.setDocumentMode(True)
        self._tw.currentChanged.connect(self._on_tab_changed)

        # --- Sekme 0: Toplu ---
        w0 = QWidget()
        v0 = QVBoxLayout(w0)
        v0.setContentsMargins(8, 8, 8, 8)
        self._hint_bulk = QLabel(
            "Forum tablosu veya BB satırları yapıştırın; «Kuyruğa aktar» ile tabloya eklenir."
        )
        self._hint_bulk.setWordWrap(True)
        v0.addWidget(self._hint_bulk)
        self.bulk_edit = QTextEdit()
        self.bulk_edit.setPlaceholderText("[table]… veya [coord]…[/coord] satırları")
        v0.addWidget(self.bulk_edit, 1)
        b_imp = QPushButton("Kuyruğa aktar")
        b_imp.setCursor(Qt.PointingHandCursor)
        b_imp.clicked.connect(self._do_bulk_import)
        v0.addWidget(b_imp)
        self._tw.addTab(w0, "Toplu yapıştır")

        # --- Sekme 1: Operasyon planı ---
        w1 = QWidget()
        v1 = QVBoxLayout(w1)
        v1.setContentsMargins(8, 8, 8, 8)
        self._hint_op = QLabel(
            "Varış zamanı ana «Ordu Gönder» sekmesinde: «Varış zamanı ayarla» açıkken tarih/saat."
        )
        self._hint_op.setWordWrap(True)
        v1.addWidget(self._hint_op)
        v1.addWidget(QLabel("Hedefler:"))
        self.plan_targets = QTextEdit()
        self.plan_targets.setPlaceholderText("505|588  veya  satır satır")
        self.plan_targets.setMaximumHeight(100)
        v1.addWidget(self.plan_targets)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        form.addWidget(QLabel("Hedef başına saldırı"), 0, 0)
        self.sp_natt = QSpinBox()
        self.sp_natt.setRange(1, 30)
        self.sp_natt.setValue(1)
        self.sp_natt.setFixedWidth(56)
        form.addWidget(self.sp_natt, 0, 1)
        form.addWidget(QLabel("Şövalye önceliği (sn)"), 0, 2)
        self.sp_gap = QSpinBox()
        self.sp_gap.setRange(10, 3600)
        self.sp_gap.setValue(120)
        self.sp_gap.setFixedWidth(80)
        form.addWidget(self.sp_gap, 0, 3)
        form.setColumnStretch(4, 1)
        v1.addLayout(form)
        b_run = QPushButton("Planla ve kuyruğa ekle")
        b_run.setCursor(Qt.PointingHandCursor)
        b_run.clicked.connect(self._do_mass_plan)
        v1.addWidget(b_run)
        self.kami_summary = QLabel("")
        self.kami_summary.setTextFormat(Qt.RichText)
        self.kami_summary.setWordWrap(True)
        v1.addWidget(self.kami_summary)
        v1.addStretch()
        self._tw.addTab(w1, "Operasyon planı")

        # --- Sekme 2: Hedefler + komutlar ---
        w2 = QWidget()
        v2 = QVBoxLayout(w2)
        v2.setContentsMargins(8, 8, 8, 8)
        spl = QSplitter(Qt.Horizontal)
        leftw = QWidget()
        lv = QVBoxLayout(leftw)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Hedef listesi"))
        self.tgt_list = QListWidget()
        self.tgt_list.setMinimumWidth(150)
        self.tgt_list.currentItemChanged.connect(self._on_tgt_changed)
        lv.addWidget(self.tgt_list, 1)
        b_ref = QPushButton("Yenile")
        b_ref.setCursor(Qt.PointingHandCursor)
        b_ref.clicked.connect(self._refresh_targets)
        lv.addWidget(b_ref)
        spl.addWidget(leftw)
        rightw = QWidget()
        rv = QVBoxLayout(rightw)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Bu hedefe giden kuyruk"))
        self.cmd_tree = QTreeWidget()
        self.cmd_tree.setHeaderLabels(["Kaynak", "Varış", "Şöv", "Koç", "Tür"])
        self.cmd_tree.setRootIsDecorated(False)
        self.cmd_tree.setAlternatingRowColors(True)
        self.cmd_tree.setColumnWidth(0, 130)
        self.cmd_tree.setColumnWidth(1, 140)
        rv.addWidget(self.cmd_tree, 1)
        spl.addWidget(rightw)
        spl.setStretchFactor(0, 0)
        spl.setStretchFactor(1, 1)
        spl.setSizes([200, 520])
        v2.addWidget(spl, 1)
        self._tw.addTab(w2, "Hedefler")

        root.addWidget(self._tw, 1)

        foot = QHBoxLayout()
        foot.addStretch()
        btn_close = QPushButton("Kapat")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)
        root.addLayout(foot)

        self._tw.setCurrentIndex(max(0, min(int(initial_page), 2)))

        self._apply_aux_theme()

    def _apply_aux_theme(self) -> None:
        dark = bool(getattr(self.bot, "_dark_mode", False))
        self.setStyleSheet(_army_aux_dialog_stylesheet(dark))
        hint_c = "#a8a8a8" if dark else "#555555"
        self._hint_bulk.setStyleSheet(f"color: {hint_c};")
        self._hint_op.setStyleSheet(f"color: {hint_c};")
        mono = "font-family: Consolas, monospace; font-size: 11px;"
        if dark:
            ed = (
                f"{mono} background-color: #3c3c3c; color: #eeeeee; "
                "border: 1px solid #666666; border-radius: 2px;"
            )
            self.kami_summary.setStyleSheet(
                "font-size: 11px; color: #e8e8e8; background: #353530; "
                "padding: 8px; border: 1px solid #555555; border-radius: 4px;"
            )
        else:
            ed = (
                f"{mono} background-color: #ffffff; color: #222222; "
                "border: 1px solid #999999; border-radius: 2px;"
            )
            self.kami_summary.setStyleSheet(
                "font-size: 11px; color: #333333; background: #f5f5f0; "
                "padding: 8px; border-radius: 4px;"
            )
        self.bulk_edit.setStyleSheet(ed)
        self.plan_targets.setStyleSheet(ed)

    def _refresh_kami_summary(self) -> None:
        self.kami_summary.setText(self.bot._sa_format_kami_koyu_summary())

    def showEvent(self, event):
        super().showEvent(event)
        if self._tw.currentIndex() == 1:
            self._refresh_kami_summary()

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1:
            self._refresh_kami_summary()
        if idx == 2:
            self._refresh_targets()

    def _do_bulk_import(self) -> None:
        self.bot._sa_bulk_import_text(self.bulk_edit.toPlainText(), msg_parent=self)

    def _do_mass_plan(self) -> None:
        b = self.bot
        if getattr(b, "_sa_time_mode", None) != "arrive":
            QMessageBox.warning(
                self,
                "Operasyon planı",
                "Ana sekmede «Varış zamanı ayarla» seçili ve tarih/saat dolu olmalı.",
            )
            return
        td = b.sa_time_date.text().strip()
        tc = b.sa_time_clock.text().strip()
        if not td or not tc:
            QMessageBox.warning(self, "Operasyon planı", "Ana sekmede varış tarihi ve saati doldurun.")
            return
        ba = b._sa_parse_time_input(td, tc)
        if ba is None:
            QMessageBox.warning(
                self, "Operasyon planı", "Tarih/saat formatı hatalı (GG.AA ve SS:DD:SS:ms)."
            )
            return
        self.bot._sa_plan_mass_attacks_with(
            self.plan_targets.toPlainText(),
            int(self.sp_natt.value()),
            int(self.sp_gap.value()),
            ba,
            msg_parent=self,
        )
        self._refresh_targets()
        self._refresh_kami_summary()

    def _refresh_targets(self) -> None:
        self.tgt_list.clear()
        seen = set()
        for xy in self.bot._sa_parse_targets_coords(self.plan_targets.toPlainText()):
            s = f"{xy[0]}|{xy[1]}"
            if s not in seen:
                seen.add(s)
                self.tgt_list.addItem(s)
        for i in range(self.bot.sa_table.topLevelItemCount()):
            t = self.bot.sa_table.topLevelItem(i).text(1).strip()
            if re.match(r"^\d{1,3}\|\d{1,3}$", t) and t not in seen:
                seen.add(t)
                self.tgt_list.addItem(t)
        self._on_tgt_changed(self.tgt_list.currentItem(), None)

    def _on_tgt_changed(self, cur, _prev=None) -> None:
        self.cmd_tree.clear()
        if cur is None:
            return
        tgt = cur.text().strip()
        for i in range(self.bot.sa_table.topLevelItemCount()):
            it = self.bot.sa_table.topLevelItem(i)
            if it.text(1).strip() != tgt:
                continue
            QTreeWidgetItem(
                self.cmd_tree,
                [it.text(0), it.text(16), it.text(12), it.text(10), it.text(14)],
            )


class SaTroopAvailLabel(QLabel):
    """Köydeki mevcut birim sayısı (yeşil); tıklanınca ilgili spinbox köydeki adede ayarlanır."""

    def __init__(self, unit_key: str, bot: "TribalWarsBot", parent=None):
        super().__init__("(0)", parent)
        self._unit_key = unit_key
        self._bot = bot
        self.setToolTip("Tıklayınca bu birimin miktarını köydeki mevcuda ayarlar.")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._bot._sa_on_avail_troop_clicked(self._unit_key)
        super().mouseReleaseEvent(event)


# ─────────────────────────────────────────────
#  ANA PENCERE
# ─────────────────────────────────────────────

class TribalWarsBot(QMainWindow):
    # Arka plan (urllib) → ana iş parçacığı: test kutusu / log; QTimer worker’da kullanılamaz.
    _telegram_test_finished = pyqtSignal(bool, str, str)  # ok, err, chat_id_normalized
    _telegram_send_error = pyqtSignal(str)  # kısa hata metni (sendMessage arka planda)

    # Birim başına nüfus (klasik Klanlar/TW; sunucunuz farklıysa bu sözlüğü güncelleyin).
    SA_UNIT_POPULATION = {
        "spear": 1,
        "sword": 1,
        "axe": 1,
        "archer": 1,
        "spy": 2,
        "light": 4,
        "marcher": 5,
        "heavy": 6,
        "ram": 5,
        "catapult": 8,
        "knight": 10,
        "snob": 100,
    }

    # Saldırı komutunda fake uyarısı: kaynak köy puanının bu yüzdesi kadar toplam nüfus gerekir.
    SA_FAKE_MIN_POP_PERCENT = 10

    # Aynı gönderim anında tek onay formunda birleştirilebilecek en fazla dalga (oyun üst sınırı ile uyumlu).
    SA_DISPATCH_MAX_BATCH = 5
    # Çok dalgada ardışık satırların gönderim zamanı farkı (oyun yaklaşık 200 ms kullanır; onay formu alanları + kuyruk).
    SA_DISPATCH_WAVE_GAP_MS = 200

    # Kami köyü eşikleri (ofansif ağırlıklı nüfus — _sa_weighted_off_pop).
    SA_KAMI_OFF_4_4 = 18000
    SA_KAMI_OFF_3_4 = 15000
    SA_KAMI_OFF_2_4 = 10000
    SA_KAMI_OFF_1_4 = 5000  # plan havuzuna giren minimum ofansif skor

    def __init__(self):
        super().__init__()
        self._settings = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        self._dark_mode = self._settings.value("ui/dark_mode", False, type=bool)
        self._apply_global_stylesheet()

        self.setWindowTitle(f"⚔ Tribal Wars Bot v{APP_VERSION} — Kabile Savaşları Otomasyon Aracı")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 820)

        self.is_running = False
        self._login_state = "idle"
        self._detected_worlds = []
        self._game_data = {}
        self._world_settings_fetched = False
        self._world_speed_from_settings = False  # yalnızca /page/settings'ten ikisi de okunduysa True
        self._trusted_world_speed = None  # ayarlar sayfasından; scrape tam dict değişince korunur
        self._trusted_unit_speed = None
        self.villages = generate_villages()
        self.selected_villages_list = []
        self.browser = None
        self._pending_command = None
        self._server_time_synced = False
        # InnoGames bot koruması / hCaptcha — tespit edilince ordu gönderimi duraklar (elle tamamlanana kadar).
        self._human_verification_required = False

        self._build_ui()
        self._start_sync_timer()
        self._start_dispatch_timer()
        # Bot koruması taraması: sabit 1–2 sn yerine birkaç saniye + jitter (düzenli periyot daha az belirgin).
        self._schedule_next_botprot_poll()

        self._telegram_test_finished.connect(self._on_telegram_test_finished)
        self._telegram_send_error.connect(self._on_telegram_send_error_slot)

    @pyqtSlot(str)
    def _on_telegram_send_error_slot(self, m: str):
        self._add_log("SİSTEM", "warn", f"Telegram: {m}")

    @pyqtSlot(bool, str, str)
    def _on_telegram_test_finished(self, ok: bool, err: str, nchat_norm: str) -> None:
        if ok:
            self._add_log(
                "AYAR",
                "info",
                f"Telegram: API yanıtı OK (chat_id={nchat_norm}). Sohbette mesajı kontrol edin.",
            )
            _tw_telegram_msgbox_on_top(
                self,
                False,
                "Telegram",
                "Test mesajı Telegram API’ye iletildi.\n\n"
                f"Chat ID: {nchat_norm}\n"
                "Mesaj yoksa: aynı ID’li sohbeti açtığınızdan emin olun; gerekirse "
                "grupta bota yönetici + mesaj izni verin veya bota özel /start gönderin.",
            )
            return
        e = (err or "").strip() or "Bilinmeyen hata"
        extra = ""
        if any(
            x in e
            for x in ("timed out", "10060", "getaddrinfo", "Name or service", "gaierror")
        ):
            extra = (
                "\n\nAğ/SSL veya Ayarlar → Proxy: proxy açıksa Telegram (api.telegram.org) "
                "için erişimi kapatıyor olabilir. Proxy’yi kapatıp test edin veya uygulamayı yeniden başlatın."
            )
        if not extra and any(
            x in e
            for x in (
                "URLError",
                "Connection",
                "10054",
                "10061",
                "Connection refused",
                "Failed to establish",
            )
        ):
            extra = (
                "\n\nProxy/HTTPS engeli olabilir; oyun web arayüzü çalışırsa bile Python (urllib) "
                "Ayarlar’daki proxy’yi kullanır — geçici olarak proxy’yi kapatıp deneyin."
            )
        _tw_telegram_msgbox_on_top(self, True, "Telegram", f"Gönderilemedi:\n{e}{extra}")
        self._add_log("AYAR", "warn", f"Telegram test hatası: {e[:200]}")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(2)

        self._build_top_panel(main_layout)

        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.tabs, 1)  # stretch=1

        self._build_browser_tab()
        self._build_villages_tab()
        self._build_army_tab()
        self._build_task_queue_tab()
        self._build_map_tab()
        self._build_scavenge_tab()
        self._build_recruit_train_tab()
        self._build_incomings_tab()
        self._build_buildings_overview_tab()
        self._build_reports_tab()
        self._build_settings_tab()
        self._build_logs_tab()

        self.statusBar().showMessage("Durum: Bekliyor | Köy: 90 | Seçili: 0")
        self._status_perm_label = QLabel(f"Tribal Wars Bot v{APP_VERSION} — PyQt5/Chromium")
        self.statusBar().addPermanentWidget(self._status_perm_label)

        self._refresh_theme_dependent_widgets()

    def _apply_global_stylesheet(self):
        QApplication.instance().setStyleSheet(
            STYLESHEET_DARK if self._dark_mode else STYLESHEET
        )

    def _on_settings_dark_mode_toggled(self, checked: bool):
        self._dark_mode = bool(checked)
        self._settings.setValue("ui/dark_mode", self._dark_mode)
        self._settings.sync()
        self._apply_global_stylesheet()
        self._refresh_theme_dependent_widgets()
        if getattr(self, "_login_state", "") == "in_game":
            self._set_login_credentials_highlight(True)

    def _on_settings_page_save(self):
        """Ayarlar sekmesindeki proxy ve Telegram alanlarını QSettings’e yazar; kalıcıdır."""
        if hasattr(self, "settings_proxy_enable_cb"):
            self._settings.setValue(
                "network/proxy_enabled", self.settings_proxy_enable_cb.isChecked()
            )
            self._settings.setValue("network/proxy_host", self.settings_proxy_host.text().strip())
            self._settings.setValue("network/proxy_port", int(self.settings_proxy_port.value()))
            ptype = self.settings_proxy_type.currentData()
            if ptype not in ("http", "socks5"):
                ptype = "http"
            self._settings.setValue("network/proxy_type", ptype)
            self._settings.setValue("network/proxy_user", self.settings_proxy_user.text().strip())
            self._settings.setValue("network/proxy_password", self.settings_proxy_pass.text())
        if hasattr(self, "settings_tg_enable_cb"):
            tg_enabled = self.settings_tg_enable_cb.isChecked()
            tg_token = self.settings_tg_token.text().strip()
            tg_chat = self.settings_tg_chat_id.text().strip()
            self._settings.setValue("notify/telegram_enabled", tg_enabled)
            self._settings.setValue("notify/telegram_bot_token", tg_token)
            self._settings.setValue("notify/telegram_chat_id", tg_chat)
            _tw_save_config({
                "telegram_enabled": tg_enabled,
                "telegram_bot_token": tg_token,
                "telegram_chat_id": tg_chat,
            })
        if hasattr(self, "settings_tg_insecure_ssl_cb"):
            tg_ssl = self.settings_tg_insecure_ssl_cb.isChecked()
            self._settings.setValue("notify/telegram_insecure_ssl", tg_ssl)
            _tw_save_config({"telegram_insecure_ssl": tg_ssl})
        self._settings.sync()
        self._add_log("AYAR", "info", "Ayarlar diske kaydedildi.")
        QMessageBox.information(
            self,
            "Ayarlar",
            "Ayarlar kaydedildi.\n\n"
            "Proxy değişikliğinin tarayıcıda tam uygulanması için uygulamayı kapatıp yeniden açın.\n"
            "Proxy şifresi ve Telegram bot token’ı yalnızca bu bilgisayardaki Qt ayarlarında tutulur.",
        )

    def _format_telegram_security_message(self, parts) -> str:
        """Güvenlik Telegram metni: oyuncu, dünya, tespit (giriş/köy yok)."""
        gd = self._game_data or {}
        p = (gd.get("player") or {}).get("name")
        p = str(p).strip() if p else ""
        p = p if p else "?"
        w = (gd.get("world_display") or gd.get("world") or "")
        w = str(w).strip()
        if not w and hasattr(self, "world_combo") and self.world_combo.count() > 0:
            w = (self.world_combo.currentText() or "").strip()
        w = w or "?"
        det = ", ".join(parts) if parts else "?"
        return (
            "Tribal Wars – Doğrulama gerekli\n"
            f"Oyuncu: {p}\n"
            f"Dünya: {w}\n"
            f"Tespit: {det}\n"
            "Otomatik ordu ve temizlik duraklatıldı."
        )

    def _notify_telegram_security(self, parts) -> None:
        tw_telegram_send_message_threaded(self, self._format_telegram_security_message(parts))

    def _on_settings_telegram_test(self):
        """Kayıtlı token/chat ile test (kaydetmeden de widget değerleri kullanılır)."""
        token = (self.settings_tg_token.text() or "").strip()
        chat = (self.settings_tg_chat_id.text() or "").strip()
        if not token or not chat:
            _tw_telegram_msgbox_on_top(
                self,
                True,
                "Telegram",
                "Bot token ve Chat ID (grup veya kişi) doldurun.",
            )
            return
        test_body = f"Tribal Wars Bot v{APP_VERSION} – test mesajı (bağlantı OK)."
        self._add_log("AYAR", "info", "Telegram: test isteği gönderiliyor (api.telegram.org)…")
        insecure = (
            self.settings_tg_insecure_ssl_cb.isChecked()
            if hasattr(self, "settings_tg_insecure_ssl_cb")
            else None
        )

        def work():
            ok, err = tw_telegram_api_send_message(
                token, chat, test_body, insecure_override=insecure
            )
            nchat = _tw_normalize_telegram_chat_id(chat)
            self._telegram_test_finished.emit(ok, err or "", nchat)

        threading.Thread(target=work, daemon=True).start()

    def _url_bar_set_loading(self, loading: bool):
        if not hasattr(self, "url_bar"):
            return
        base = "padding: 4px 8px; font-size: 12px;"
        if loading:
            bg = "#5c4a20" if self._dark_mode else "#fff8e0"
        else:
            bg = "#3c3c3c" if self._dark_mode else "white"
        self.url_bar.setStyleSheet(base + f" background: {bg};")

    def _refresh_theme_dependent_widgets(self):
        """Satır içi setStyleSheet kullanan üst/kritik bileşenleri temaya göre güncelle."""
        if self._dark_mode:
            sync_idle = "color: #b0b0b0; font-size: 10px;"
            tb = "background: #3a3a3a; border: 1px solid #555555; border-radius: 2px;"
            pinfo = (
                "font-size: 12px; padding: 4px; background: #3a3a3a; "
                "border-radius: 3px; color: #e8e8e8;"
            )
            wspd = (
                "font-size: 11px; padding: 3px 4px; background: #4a3d1c; "
                "border-radius: 3px; color: #f0d080;"
            )
            totals = "font-weight: bold; font-size: 10px; color: #c8c8c8;"
        else:
            sync_idle = "color: #555555; font-size: 10px;"
            tb = "background: #e8e8e8; border: 1px solid #bbbbbb; border-radius: 2px;"
            pinfo = "font-size: 12px; padding: 4px; background: #e8e8e8; border-radius: 3px;"
            wspd = (
                "font-size: 11px; padding: 3px 4px; background: #fff3cd; "
                "border-radius: 3px; color: #856404;"
            )
            totals = "font-weight: bold; font-size: 10px; color: #333;"

        if hasattr(self, "sync_label"):
            txt = self.sync_label.text() or ""
            if self._server_time_synced:
                self.sync_label.setStyleSheet(
                    "color: #228822; font-weight: bold; font-size: 10px;"
                )
            elif "Yerel" in txt:
                c = "#dd9933" if self._dark_mode else "#aa6600"
                self.sync_label.setStyleSheet(f"color: {c}; font-size: 10px;")
            else:
                self.sync_label.setStyleSheet(sync_idle)

        if hasattr(self, "browser_toolbar"):
            self.browser_toolbar.setStyleSheet(tb)
        if hasattr(self, "player_info_label"):
            self.player_info_label.setStyleSheet(pinfo)
        if hasattr(self, "world_speed_label"):
            self.world_speed_label.setStyleSheet(wspd)
        if hasattr(self, "url_bar"):
            self._url_bar_set_loading(False)
        if hasattr(self, "sa_totals_label"):
            self.sa_totals_label.setStyleSheet(totals)

        if hasattr(self, "sa_source_points_label"):
            pc = "#c8c8c8" if self._dark_mode else "#555555"
            self.sa_source_points_label.setStyleSheet(f"font-size: 10px; color: {pc};")

        if hasattr(self, "settings_dark_cb"):
            self.settings_dark_cb.blockSignals(True)
            self.settings_dark_cb.setChecked(self._dark_mode)
            self.settings_dark_cb.blockSignals(False)

        if hasattr(self, "enable_sending_cb"):
            if self.enable_sending_cb.isChecked():
                self.enable_sending_cb.setStyleSheet(
                    "font-weight: bold; font-size: 11px; color: #228822;"
                )
            else:
                self.enable_sending_cb.setStyleSheet(
                    "font-weight: bold; font-size: 11px; color: #cc4444;"
                )

        if hasattr(self, "_sa_unit_theme_widgets"):
            for unit_frame, name_lbl, spin in self._sa_unit_theme_widgets:
                if self._dark_mode:
                    unit_frame.setStyleSheet(
                        "border: 1px solid #555555; border-radius: 2px; padding: 1px;"
                        "background-color: #3a3a3a;"
                    )
                    name_lbl.setStyleSheet(
                        "font-size: 11px; font-weight: bold; color: #ececec; border: none;"
                    )
                    spin.setStyleSheet(
                        "font-size: 12px; border: 1px solid #666666;"
                        "background-color: #3c3c3c; color: #eeeeee;"
                    )
                else:
                    unit_frame.setStyleSheet(
                        "border: 1px solid #ddd; border-radius: 2px; padding: 1px;"
                    )
                    name_lbl.setStyleSheet(
                        "font-size: 11px; font-weight: bold; color: #444; border: none;"
                    )
                    spin.setStyleSheet("font-size: 12px; border: 1px solid #aaa;")

        if hasattr(self, "sa_time_label"):
            tc = "#d8c8a8" if self._dark_mode else "#5a3e1b"
            self.sa_time_label.setStyleSheet(
                f"font-weight: bold; color: {tc}; font-size: 11px;"
            )

        if hasattr(self, "btn_set_arrive"):
            self._sa_refresh_time_mode_button_styles()

        if hasattr(self, "sa_table"):
            if self._dark_mode:
                self.sa_table.header().setStyleSheet(
                    "QHeaderView::section { font-size: 10px; padding: 3px;"
                    "background: #404040; color: #e8e8e8; border: 1px solid #555555; }"
                )
            else:
                self.sa_table.header().setStyleSheet(
                    "QHeaderView::section { font-size: 10px; padding: 3px; }"
                )

        if hasattr(self, "map_ctrl_frame"):
            if self._dark_mode:
                self.map_ctrl_frame.setStyleSheet(
                    "QFrame#mapControlFrame { background: #353530; border: 1px solid #5c5c55;"
                    "border-radius: 5px; padding: 2px; }"
                )
            else:
                self.map_ctrl_frame.setStyleSheet(
                    "QFrame#mapControlFrame { background: #faf6ec; border: 1px solid #d8c8a8;"
                    "border-radius: 5px; padding: 2px; }"
                )

        if hasattr(self, "map_hint_lbl"):
            hc = "#b0b0b0" if self._dark_mode else "#777777"
            self.map_hint_lbl.setStyleSheet(
                f"font-size: 9px; color: {hc}; padding-left: 2px;"
            )

        if hasattr(self, "map_queue_panel"):
            if self._dark_mode:
                self.map_queue_panel.setStyleSheet(
                    "QWidget#mapQueuePanel { background: #353530; border: 1px solid #5c5c55;"
                    "border-radius: 5px; }"
                )
            else:
                self.map_queue_panel.setStyleSheet(
                    "QWidget#mapQueuePanel { background: #fdfaf4; border: 1px solid #d8c8a8;"
                    "border-radius: 5px; }"
                )

        if hasattr(self, "map_q_title"):
            if self._dark_mode:
                self.map_q_title.setStyleSheet(
                    "font-weight: bold; font-size: 12px; color: #e8dcc8;"
                    "padding: 6px; background: qlineargradient(y1:0,y2:1,stop:0 #4a4538,stop:1 #38342c);"
                    "border: 1px solid #6a6250; border-radius: 4px;"
                )
            else:
                self.map_q_title.setStyleSheet(
                    "font-weight: bold; font-size: 12px; color: #5a3e1b;"
                    "padding: 6px; background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
                    "border: 1px solid #b89b6a; border-radius: 4px;"
                )

        if hasattr(self, "map_q_hint"):
            qc = "#aaaaaa" if self._dark_mode else "#666666"
            self.map_q_hint.setStyleSheet(f"font-size: 9px; color: {qc};")

        if hasattr(self, "map_queue_list"):
            if self._dark_mode:
                self.map_queue_list.header().setStyleSheet(
                    "QHeaderView::section { font-size: 9px; padding: 2px;"
                    "background: #404040; color: #e8e8e8; border: 1px solid #555555; }"
                )
            else:
                self.map_queue_list.header().setStyleSheet(
                    "QHeaderView::section { font-size: 9px; padding: 2px;"
                    "background: #e8dcc8; border: 1px solid #c0b090; }"
                )

        if hasattr(self, "map_queue_count_label"):
            mc = "#c8c8c8" if self._dark_mode else "#555555"
            self.map_queue_count_label.setStyleSheet(
                f"font-size: 10px; color: {mc}; font-weight: bold;"
            )

        if hasattr(self, "map_delay_lbl"):
            dc = "#d8c8a8" if self._dark_mode else "#5a3e1b"
            self.map_delay_lbl.setStyleSheet(f"font-size: 10px; color: {dc};")

        if hasattr(self, "map_legend_frame"):
            if self._dark_mode:
                self.map_legend_frame.setStyleSheet(
                    "QFrame { background: #353530; border: 1px solid #5c5c55;"
                    "border-radius: 4px; padding: 4px; }"
                )
            else:
                self.map_legend_frame.setStyleSheet(
                    "QFrame { background: #f7f2e8; border: 1px solid #e0d4c0;"
                    "border-radius: 4px; padding: 4px; }"
                )

        if hasattr(self, "map_village_count_label"):
            vc = "#c8c8c8" if self._dark_mode else "#555555"
            self.map_village_count_label.setStyleSheet(
                f"font-size: 10px; color: {vc}; font-weight: bold;"
            )

        if hasattr(self, "_farm_short_labels"):
            fc = "#c8c8c8" if self._dark_mode else "#555555"
            for sl in self._farm_short_labels:
                sl.setStyleSheet(f"font-size: 9px; color: {fc}; border: none;")

        if hasattr(self, "farm_sent_label"):
            fsc = "#c8c8c8" if self._dark_mode else "#555555"
            self.farm_sent_label.setStyleSheet(f"font-size: 10px; color: {fsc};")

        if hasattr(self, "scav_opt_inner"):
            if self._dark_mode:
                self.scav_opt_inner.setStyleSheet("background-color: #323232;")
                self.scav_opt_scroll.setStyleSheet(
                    "QScrollArea { background-color: #323232; border: none; }"
                )
            else:
                self.scav_opt_inner.setStyleSheet("")
                self.scav_opt_scroll.setStyleSheet(
                    "QScrollArea { background: transparent; border: none; }"
                )

        if hasattr(self, "bq_levels_table"):
            if self._dark_mode:
                bqt = (
                    "QTableWidget { background: #2d2d2d; color: #e8e8e8; "
                    "gridline-color: #555; alternate-background-color: #333333; }"
                    "QTableWidget::item:selected { background: #3d4a5c; }"
                    "QHeaderView::section { background: #3a3a3a; color: #e0e0e0; "
                    "border: 1px solid #555555; padding: 4px; }"
                )
                bqw = (
                    "QTreeWidget { background: #2d2d2d; color: #e8e8e8; }"
                    "QTreeWidget::item { min-height: 20px; }"
                    "QTreeWidget::item:selected { background: #3d4a5c; color: #f0f0f0; }"
                    "QHeaderView::section { background: #3a3a3a; color: #e0e0e0; "
                    "border: 1px solid #555555; padding: 4px; }"
                )
            else:
                bqt = ""
                bqw = ""
            self.bq_levels_table.setStyleSheet(bqt)
            self.bq_table.setStyleSheet(bqw)
        if hasattr(self, "bq_status_label"):
            c = "#c8c8c8" if self._dark_mode else "#333333"
            self.bq_status_label.setStyleSheet(
                f"font-weight: bold; font-size: 10px; color: {c};"
            )
        if hasattr(self, "bq_flow_hint"):
            hc = "#b0b0b0" if self._dark_mode else "#555555"
            self.bq_flow_hint.setStyleSheet(
                f"font-size: 10px; color: {hc};"
            )
        if hasattr(self, "bq_enable_cb"):
            if self._dark_mode:
                self.bq_enable_cb.setStyleSheet(
                    "font-weight: bold; font-size: 11px; color: #ececec;"
                )
            else:
                self.bq_enable_cb.setStyleSheet("font-weight: bold; font-size: 11px;")

        if hasattr(self, "incomings_hint"):
            hc = "#b0b0b0" if self._dark_mode else "#555555"
            self.incomings_hint.setStyleSheet(f"font-size: 10px; color: {hc}; padding: 4px;")
        if hasattr(self, "incomings_foot"):
            fc = "#a0a0a0" if self._dark_mode else "#888888"
            self.incomings_foot.setStyleSheet(f"font-size: 9px; color: {fc};")

        if hasattr(self, "buildings_ov_hint"):
            hc = "#b0b0b0" if self._dark_mode else "#555555"
            self.buildings_ov_hint.setStyleSheet(f"font-size: 10px; color: {hc}; padding: 4px;")
        if hasattr(self, "buildings_ov_foot"):
            fc = "#a0a0a0" if self._dark_mode else "#888888"
            self.buildings_ov_foot.setStyleSheet(f"font-size: 9px; color: {fc};")

        if hasattr(self, "sa_source_combo") and self.sa_source_combo.currentIndex() >= 0:
            self._sa_on_source_changed(self.sa_source_combo.currentIndex())

    def _sa_refresh_time_mode_button_styles(self):
        """Varış / gönderim zamanı düğmeleri — koyu tema ile uyumlu stiller."""
        active_dark = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #6b5a3d,stop:1 #4a3d28);"
            "border: 2px solid #8a7d60; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #f5e6c8;"
        )
        normal_dark = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #4a4538,stop:1 #38342c);"
            "border: 1px solid #6a6250; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #e8dcc8;"
        )
        active_light = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #d4b896,stop:1 #b89b6a);"
            "border: 2px solid #8a6d3b; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #3a2a0f;"
        )
        normal_light = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #5a3e1b;"
        )
        active_style = active_dark if self._dark_mode else active_light
        normal_style = normal_dark if self._dark_mode else normal_light

        if self.btn_set_arrive.isChecked():
            self.btn_set_arrive.setStyleSheet(active_style)
            self.btn_set_send.setStyleSheet(normal_style)
        elif self.btn_set_send.isChecked():
            self.btn_set_send.setStyleSheet(active_style)
            self.btn_set_arrive.setStyleSheet(normal_style)
        else:
            self.btn_set_arrive.setStyleSheet(normal_style)
            self.btn_set_send.setStyleSheet(normal_style)

    # ── ÜST PANEL ─────────────────────────────

    def _build_top_panel(self, parent_layout):
        panel = QFrame()
        panel.setObjectName("topPanel")
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setFixedHeight(46)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Sunucu:"))
        self.server_combo = QComboBox()
        self.server_combo.setMinimumWidth(150)
        for name, url in SERVERS:
            self.server_combo.addItem(name, url)
        self.server_combo.currentIndexChanged.connect(self._on_server_changed)
        layout.addWidget(self.server_combo)

        layout.addWidget(QLabel("Dil:"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["tr", "en", "de", "fr"])
        self.lang_combo.setFixedWidth(50)
        layout.addWidget(self.lang_combo)

        self.auto_login_cb = QCheckBox("Oturum bitince yenile")
        self.auto_login_cb.setChecked(True)
        layout.addWidget(self.auto_login_cb)

        layout.addSpacing(10)

        self.login_user_label = QLabel("Kullanıcı:")
        layout.addWidget(self.login_user_label)
        self.login_input = QLineEdit()
        self.login_input.setFixedWidth(110)
        self.login_input.setPlaceholderText("kullanıcı adı")
        layout.addWidget(self.login_input)

        self.login_pass_label = QLabel("Şifre:")
        layout.addWidget(self.login_pass_label)
        self.password_input = QLineEdit()
        self.password_input.setFixedWidth(110)
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("••••••••")
        layout.addWidget(self.password_input)

        layout.addSpacing(10)

        self.start_btn = QPushButton("▶ Başlat")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self._start_bot)
        layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹ Durdur")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_bot)
        layout.addWidget(self.stop_btn)

        # Dünya seçici
        layout.addSpacing(6)
        layout.addWidget(QLabel("Dünya:"))
        self.world_combo = QComboBox()
        self.world_combo.setMinimumWidth(130)
        self.world_combo.setEnabled(False)
        self.world_combo.addItem("— Giriş yapın —")
        layout.addWidget(self.world_combo)

        self.world_select_btn = QPushButton("🌍 Gir")
        self.world_select_btn.setObjectName("startBtn")
        self.world_select_btn.setCursor(Qt.PointingHandCursor)
        self.world_select_btn.setEnabled(False)
        self.world_select_btn.clicked.connect(self._enter_world)
        layout.addWidget(self.world_select_btn)

        # Köy seçici
        layout.addSpacing(6)
        layout.addWidget(QLabel("Köy:"))
        self.village_combo = QComboBox()
        self.village_combo.setMinimumWidth(180)
        self.village_combo.setEnabled(False)
        self.village_combo.addItem("— Dünyaya girin —")
        self.village_combo.currentIndexChanged.connect(self._on_village_changed)
        layout.addWidget(self.village_combo)

        layout.addStretch()

        self.status_indicator = QLabel("● BEKLIYOR")
        self.status_indicator.setStyleSheet("color: #aa6600; font-weight: bold; font-size: 11px;")
        layout.addWidget(self.status_indicator)

        layout.addSpacing(10)

        self.sync_label = QLabel("")
        self.sync_label.setStyleSheet("color: #555555; font-size: 10px;")
        layout.addWidget(self.sync_label)

        parent_layout.addWidget(panel)

    # ── TARAYICI SEKMESİ ──────────────────────

    def _build_browser_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Araç çubuğu
        toolbar = QFrame()
        self.browser_toolbar = toolbar
        toolbar.setFrameShape(QFrame.StyledPanel)
        toolbar.setStyleSheet("background: #e8e8e8; border: 1px solid #bbbbbb; border-radius: 2px;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 3, 6, 3)
        tb_layout.setSpacing(6)

        self.back_btn = QPushButton("◀")
        self.back_btn.setFixedWidth(32)
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(lambda: self.browser.back() if self.browser else None)
        tb_layout.addWidget(self.back_btn)

        self.forward_btn = QPushButton("▶")
        self.forward_btn.setFixedWidth(32)
        self.forward_btn.setCursor(Qt.PointingHandCursor)
        self.forward_btn.clicked.connect(lambda: self.browser.forward() if self.browser else None)
        tb_layout.addWidget(self.forward_btn)

        self.reload_btn = QPushButton("⟳")
        self.reload_btn.setFixedWidth(32)
        self.reload_btn.setCursor(Qt.PointingHandCursor)
        self.reload_btn.clicked.connect(lambda: self.browser.reload() if self.browser else None)
        tb_layout.addWidget(self.reload_btn)

        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("URL...")
        self.url_bar.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        self.url_bar.returnPressed.connect(self._navigate_to_url)
        tb_layout.addWidget(self.url_bar)

        self.go_btn = QPushButton("Git")
        self.go_btn.setCursor(Qt.PointingHandCursor)
        self.go_btn.clicked.connect(self._navigate_to_url)
        tb_layout.addWidget(self.go_btn)

        self.btn_load_planner = QPushButton("Planlayıcı")
        self.btn_load_planner.setCursor(Qt.PointingHandCursor)
        self.btn_load_planner.setToolTip(
            "Hedef köy (info_village) ekranındayken: uzaktan arascript yükler (Chrome bookmarklet ile aynı URL).\n"
            "Ortam: TW_ARASCRIPT_URL (isteğe bağlı), yerel dosya için TW_PLANNER_USE_LOCAL=1"
        )
        self.btn_load_planner.clicked.connect(self._tw_load_planner_script)
        tb_layout.addWidget(self.btn_load_planner)

        toolbar.setFixedHeight(36)
        toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(toolbar)

        # Gömülü Chromium tarayıcı
        self.browser = StealthBrowser()
        self._tw_planner_bridge = TwPlannerBridge(self)
        self._tw_web_channel = QWebChannel(self.browser.stealth_page)
        self._tw_web_channel.registerObject("twPlannerBridge", self._tw_planner_bridge)
        self.browser.stealth_page.setWebChannel(self._tw_web_channel)
        self.browser.stealth_page.loadFinished.connect(self._tw_inject_planner_webchannel_hook)
        self.browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.browser.setMinimumHeight(400)
        self.browser.urlChanged.connect(self._on_url_changed)
        self.browser.loadStarted.connect(lambda: self._url_bar_set_loading(True))
        self.browser.loadFinished.connect(lambda ok: self._url_bar_set_loading(False))
        self.browser.titleChanged.connect(
            lambda title: self.setWindowTitle(f"⚔ Tribal Wars Bot — {title}") if title else None)
        layout.addWidget(self.browser, 1)  # stretch=1 ile tüm alanı kapla

        self.tabs.addTab(tab, "🌐 Tarayıcı")

        # İlk sunucu adresini yükle
        initial_url = SERVERS[0][1]
        self.url_bar.setText(initial_url)
        self.browser.navigate(initial_url)

    def _navigate_to_url(self):
        url = self.url_bar.text().strip()
        if url and not url.startswith("http"):
            url = "https://" + url
        if url and self.browser:
            self.browser.navigate(url)

    def _on_url_changed(self, url):
        self.url_bar.setText(url.toString())

    def _tw_resolve_arascript_path(self):
        """arascript.js: önce exe yanı, sonra repo kökü (tw-bot üstü), sonra tw-bot içi."""
        candidates = [
            Path(sys.executable).resolve().parent / "arascript.js",
            Path(__file__).resolve().parent.parent / "arascript.js",
            Path(__file__).resolve().parent / "arascript.js",
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None

    def _tw_load_planner_script(self):
        """Hedef köy (info_village) ekranında planlayıcı: varsayılan uzak script (bookmarklet ile aynı)."""
        if not getattr(self, "browser", None):
            return
        use_local = os.environ.get("TW_PLANNER_USE_LOCAL", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if use_local:
            script_path = self._tw_resolve_arascript_path()
            if not script_path:
                QMessageBox.warning(
                    self,
                    "Planlayıcı",
                    "arascript.js bulunamadı (yerel mod).\n"
                    "Dosyayı tw-bot üst klasörüne koyun veya TW_PLANNER_USE_LOCAL kapatın.",
                )
                return
            try:
                js_raw = script_path.read_text(encoding="utf-8")
            except OSError as e:
                QMessageBox.warning(self, "Planlayıcı", f"Dosya okunamadı:\n{e}")
                return
            payload = json.dumps(js_raw)
            wrapped = (
                "(function(){"
                "if (typeof game_data === 'undefined' || !game_data || !game_data.screen) {"
                "window.alert('Önce Tribal Wars oyun sayfasında olun (giriş yapılmış game.php).');"
                "return;"
                "}"
                "if (game_data.screen !== 'info_village') {"
                "window.alert('Planlayıcı yalnızca hedef köy ekranında çalışır. "
                "Köy profili (info_village) açıkken deneyin.');"
                "return;"
                "}"
                "if (typeof jQuery === 'undefined' || !window.jQuery) {"
                "window.alert('jQuery bulunamadı; bu sayfada planlayıcı yüklenemez.');"
                "return;"
                "}"
                f"eval({payload});"
                "})();"
            )
            self.browser.page().runJavaScript(wrapped)
            self._add_log("PLAN", "info", f"Planlayıcı yerel eval: {script_path}")
            return

        base_url = (os.environ.get("TW_ARASCRIPT_URL") or TW_PLANNER_SCRIPT_URL).strip()
        if not base_url:
            QMessageBox.warning(self, "Planlayıcı", "TW_PLANNER_SCRIPT_URL / TW_ARASCRIPT_URL boş.")
            return
        url_js = json.dumps(base_url)
        wrapped = (
            "(function(){"
            "if (typeof game_data === 'undefined' || !game_data || !game_data.screen) {"
            "window.alert('Önce Tribal Wars oyun sayfasında olun (giriş yapılmış game.php).');"
            "return;"
            "}"
            "if (game_data.screen !== 'info_village') {"
            "window.alert('Planlayıcı yalnızca hedef köy ekranında çalışır. "
            "Köy profili (info_village) açıkken deneyin.');"
            "return;"
            "}"
            "if (typeof jQuery === 'undefined' || !window.jQuery) {"
            "window.alert('jQuery bulunamadı; bu sayfada planlayıcı yüklenemez.');"
            "return;"
            "}"
            "var base = "
            + url_js
            + ";"
            "var u = base + (base.indexOf('?') >= 0 ? '&' : '?') + 'v=' + Date.now();"
            "var s = document.createElement('script');"
            "s.src = u;"
            "s.onerror = function() {"
            "window.alert('Planlayıcı indirilemedi (ağ, engel veya URL): ' + u);"
            "};"
            "document.head.appendChild(s);"
            "})();"
        )
        self.browser.page().runJavaScript(wrapped)
        self._add_log("PLAN", "info", f"Planlayıcı script yükleniyor: {base_url}")

    def _tw_inject_planner_webchannel_hook(self, ok):
        """QWebChannel istemcisi + planlayıcı «Gonder» tıklamasını Ordu Gönder kuyruğuna yönlendir."""
        if not ok or not getattr(self, "browser", None):
            return
        js = r"""
(function(){
  function __twPlannerInsertDestekUi() {
    if (document.getElementById('__tw_planner_destek_wrap')) return true;
    var elT = document.getElementById('godzina_wejscia') || document.getElementById('data_wejscia');
    if (!elT) return false;
    var wrap = document.createElement('div');
    wrap.id = '__tw_planner_destek_wrap';
    wrap.setAttribute('style',
      'margin:6px 0 4px 0;padding:4px 8px;font-size:12px;line-height:1.4;' +
      'border:1px solid #a98;border-radius:4px;background:rgba(255,230,180,0.35);display:inline-block;');
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = '__tw_planner_destek';
    try {
      var st = localStorage.getItem('tw_planner_destek');
      if (st === '1') cb.checked = true;
      else if (st === '0') cb.checked = false;
    } catch (e0) {}
    cb.addEventListener('change', function() {
      try { localStorage.setItem('tw_planner_destek', cb.checked ? '1' : '0'); } catch (e1) {}
    });
    var lab = document.createElement('label');
    lab.setAttribute('for', '__tw_planner_destek');
    lab.setAttribute('style', 'cursor:pointer;user-select:none;');
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(' Destek'));
    wrap.appendChild(document.createTextNode('TW Bot '));
    wrap.appendChild(lab);
    var tr = elT.closest('tr');
    if (tr && tr.parentNode) {
      var ntr = document.createElement('tr');
      var td = document.createElement('td');
      var cs = tr.querySelectorAll('td,th').length;
      if (cs > 1) td.setAttribute('colspan', String(cs));
      else td.setAttribute('colspan', '2');
      td.appendChild(wrap);
      ntr.appendChild(td);
      tr.parentNode.insertBefore(ntr, tr.nextSibling);
    } else if (elT.parentNode) {
      elT.parentNode.insertBefore(wrap, elT.nextSibling);
    } else {
      return false;
    }
    return true;
  }
  function __twPlannerTryInsert(n) {
    n = n || 0;
    if (__twPlannerInsertDestekUi() || n > 50) return;
    setTimeout(function() { __twPlannerTryInsert(n + 1); }, 200);
  }
  function boot() {
    if (window.__twPlannerBridgeReady) return;
    if (!window.qt || !window.qt.webChannelTransport) { setTimeout(boot, 30); return; }
    var s = document.createElement('script');
    s.src = 'qrc:///qtwebchannel/qwebchannel.js';
    s.onload = function() {
      if (window.__twPlannerBridgeReady) return;
      new QWebChannel(qt.webChannelTransport, function(ch) {
        window.twPlannerBridge = ch.objects.twPlannerBridge;
        window.__twPlannerBridgeReady = 1;
        __twPlannerTryInsert(0);
        document.addEventListener('click', function(ev) {
          if (ev.defaultPrevented || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
          var el = ev.target;
          if (el.nodeType !== 1) return;
          if (el.tagName !== 'A') el = el.closest('a');
          if (!el || el.tagName !== 'A') return;
          var href = el.href || '';
          if (href.indexOf('screen=place') < 0) return;
          if (href.indexOf('from=simulator') < 0 && href.indexOf('att_') < 0) return;
          __twPlannerInsertDestekUi();
          var elD = document.getElementById('data_wejscia');
          var elT = document.getElementById('godzina_wejscia');
          var cbEl = document.getElementById('__tw_planner_destek');
          var payload = JSON.stringify({
            d: elD && 'value' in elD ? elD.value : '',
            t: elT && 'value' in elT ? elT.value : '',
            support: !!(cbEl && cbEl.checked)
          });
          if (!window.twPlannerBridge) return;
          try {
            window.twPlannerBridge.enqueueSimulatorCommand(href, payload);
            ev.preventDefault();
            ev.stopPropagation();
          } catch (e) {}
        }, true);
      });
    };
    s.onerror = function() {};
    (document.head || document.documentElement).appendChild(s);
  }
  boot();
})();
"""
        self.browser.page().runJavaScript(js)

    def _tw_planner_resolve_source_display(self, village_id: int):
        """all_villages / village üzerinden Ordu Gönder tablosu ile uyumlu kaynak metni ve koordinat."""
        all_v = self._game_data.get("all_villages") or []
        for v in all_v:
            if int(v.get("id", 0) or 0) == int(village_id):
                sx = v.get("x")
                sy = v.get("y")
                if sx is None or sy is None:
                    return None, None, None
                coord = f"({int(sx)}|{int(sy)})"
                label = f"{v.get('name', '?')} {coord}"
                return label, int(sx), int(sy)
        v = self._game_data.get("village") or {}
        if int(v.get("id", 0) or 0) == int(village_id):
            sx = v.get("x")
            sy = v.get("y")
            if sx is None or sy is None:
                return None, None, None
            coord = f"({int(sx)}|{int(sy)})"
            label = f"{v.get('name', '?')} {coord}"
            return label, int(sx), int(sy)
        return None, None, None

    def _tw_planner_enqueue_from_href(self, href: str, date_time_json: str):
        """Planlayıcı place+simulator linkinden kuyruk satırı üret."""
        if not href or "screen=place" not in href:
            return
        try:
            payload = json.loads(date_time_json) if date_time_json else {}
        except json.JSONDecodeError:
            payload = {}
        d_raw = (payload.get("d") or "").strip()
        t_raw = (payload.get("t") or "").strip()
        d = d_raw.replace("/", ".").replace("-", ".")
        t = t_raw

        parsed = urlparse(href)
        qs = parse_qs(parsed.query, keep_blank_values=True)

        def _first(key):
            vals = qs.get(key)
            if not vals:
                return None
            return vals[0]

        village_raw = _first("village")
        if village_raw is None:
            self._add_log("PLAN", "warn", "Planlayıcı köprü: village parametresi yok")
            return
        try:
            village_id = int(village_raw)
        except ValueError:
            self._add_log("PLAN", "warn", "Planlayıcı köprü: village id sayı değil")
            return

        tx_raw = _first("x")
        ty_raw = _first("y")
        if tx_raw is None or ty_raw is None:
            QMessageBox.warning(self, "Planlayıcı", "Hedef koordinat (x, y) linkte yok.")
            return
        try:
            tgt_x = int(tx_raw)
            tgt_y = int(ty_raw)
        except ValueError:
            QMessageBox.warning(self, "Planlayıcı", "Hedef koordinatları okunamadı.")
            return

        unit_keys = {k for k, _ in self.SA_UNIT_DEFS}
        troops_map = {}
        for k, vals in qs.items():
            if not k.startswith("att_"):
                continue
            ukey = k[4:]
            if ukey not in unit_keys:
                continue
            try:
                n = int(vals[0])
            except (ValueError, TypeError, IndexError):
                continue
            if n > 0:
                troops_map[ukey] = n

        src_text, src_x, src_y = self._tw_planner_resolve_source_display(village_id)
        if src_text is None or src_x is None or src_y is None:
            QMessageBox.warning(
                self,
                "Planlayıcı",
                f"Köy id={village_id} için kaynak adı/koordinat bulunamadı.\n"
                "Köy listesinin güncel olduğundan emin olun.",
            )
            return

        arrive_dt = self._sa_parse_time_input(d, t)
        if arrive_dt is None:
            QMessageBox.warning(
                self,
                "Planlayıcı",
                "Varış tarihi/saati okunamadı.\n"
                "#data_wejscia ve #godzina_wejscia alanlarını doldurun "
                "(GG.AA ve SS:DD:SS).",
            )
            return

        support_sel = payload.get("support")
        if isinstance(support_sel, bool):
            cmd_attack = not support_sel
        else:
            qlow = (parsed.query or "").lower()
            cmd_attack = "try=supports" not in qlow and "type=support" not in qlow

        ok, err = self._sa_append_row_from_values(
            src_text,
            src_x,
            src_y,
            tgt_x,
            tgt_y,
            troops_map,
            cmd_attack,
            "arrive",
            arrive_dt,
        )
        if not ok:
            QMessageBox.warning(self, "Ordu Gönder", err or "Komut eklenemedi")
            return

        kind = "destek" if not cmd_attack else "saldırı"
        self._add_log(
            "PLAN",
            "success",
            f"Planlayıcıdan kuyruk ({kind}): {src_text} → {tgt_x}|{tgt_y}",
        )
        tab = getattr(self, "sa_tab", None)
        if tab is not None:
            idx = self.tabs.indexOf(tab)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)

    def _on_server_changed(self, index):
        if index >= 0 and self.browser:
            url = SERVERS[index][1]
            self.url_bar.setText(url)
            self.browser.navigate(url)
            self._add_log("TAR", "info", f"Sunucu değiştirildi: {SERVERS[index][0]} → {url}")
            self._set_login_credentials_highlight(False)

    # ── KÖYLER SEKMESİ ────────────────────────

    def _build_villages_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)

        # Oyuncu bilgisi
        self.player_info_label = QLabel("Oyuncu bilgisi yükleniyor...")
        self.player_info_label.setStyleSheet("font-size: 12px; padding: 4px; background: #e8e8e8; border-radius: 3px;")
        layout.addWidget(self.player_info_label)

        # Dünya hız bilgisi
        self.world_speed_label = QLabel("⚙️ Dünya Hızı: — | Birim Hızı: —")
        self.world_speed_label.setStyleSheet("font-size: 11px; padding: 3px 4px; background: #fff3cd; border-radius: 3px; color: #856404;")
        layout.addWidget(self.world_speed_label)

        # Kaynak bilgisi
        res_group = QGroupBox("Köy Kaynakları")
        res_layout = QHBoxLayout()
        self.res_wood_label = QLabel("🪵 Odun: —")
        self.res_stone_label = QLabel("🧱 Kil: —")
        self.res_iron_label = QLabel("⛏️ Demir: —")
        self.res_storage_label = QLabel("📦 Depo: —")
        self.res_pop_label = QLabel("👥 Nüfus: —")
        for lbl in [self.res_wood_label, self.res_stone_label, self.res_iron_label, self.res_storage_label, self.res_pop_label]:
            lbl.setStyleSheet("font-size: 11px; font-weight: bold; padding: 2px 8px;")
            res_layout.addWidget(lbl)
        res_layout.addStretch()
        res_group.setLayout(res_layout)
        layout.addWidget(res_group)

        # Bina seviyeleri tablosu
        build_group = QGroupBox("Bina Seviyeleri")
        build_layout = QVBoxLayout()
        self.buildings_tree = QTreeWidget()
        self.buildings_tree.setAlternatingRowColors(True)
        self.buildings_tree.setHeaderLabels(["Bina", "Seviye"])
        self.buildings_tree.setMaximumHeight(220)
        self.buildings_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        build_layout.addWidget(self.buildings_tree)
        build_group.setLayout(build_layout)
        layout.addWidget(build_group)

        # Toolbar
        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 Verileri Yenile")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._scrape_game_data)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Asker tablosu
        troop_group = QGroupBox("Askerler (Aktif Köy)")
        troop_layout = QVBoxLayout()
        self.troops_tree = QTreeWidget()
        self.troops_tree.setAlternatingRowColors(True)
        self.troops_tree.setHeaderLabels(["Birim", "Adet"])
        self.troops_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self.troops_tree.setMaximumHeight(180)
        troop_layout.addWidget(self.troops_tree)
        troop_group.setLayout(troop_layout)
        layout.addWidget(troop_group)

        # Tüm köyler tablosu
        all_group = QGroupBox("Tüm Köyler (overview_villages sayfasından)")
        all_layout = QVBoxLayout()
        self.all_villages_tree = QTreeWidget()
        self.all_villages_tree.setAlternatingRowColors(True)
        self.all_villages_tree.setHeaderLabels(["ID", "Köy Adı", "Koordinat", "Nüfus", "Askerler", ""])
        self.all_villages_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self.all_villages_tree.setColumnWidth(0, 50)
        self.all_villages_tree.setColumnWidth(5, 30)
        self.all_villages_tree.itemDoubleClicked.connect(self._on_village_double_clicked)
        all_layout.addWidget(self.all_villages_tree)

        hint_label = QLabel("💡 Köye geçmek için çift tıklayın veya üst paneldeki Köy combobox'ını kullanın")
        hint_label.setStyleSheet("font-size: 10px; color: #888; padding: 2px;")
        all_layout.addWidget(hint_label)

        all_group.setLayout(all_layout)
        layout.addWidget(all_group)

        self.tabs.addTab(tab, "🏘️ Köyler")

    # ── ORDU GÖNDERME SEKMESİ (Sending Army) ──

    def _build_army_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ═══════════════════════════════════════════
        #  SATIR 1: Aktifleştir checkbox
        # ═══════════════════════════════════════════
        row1 = QHBoxLayout()
        self.enable_sending_cb = QCheckBox("Ordu Gönderimi Aktif")
        self.enable_sending_cb.setToolTip(
            "İşaretliyken zamanı gelen kuyruk satırları oyuna gönderilir.\n"
            "İşaretsizken de köy/hedef/birlik seçip kuyruk oluşturabilirsiniz (Planlama modu)."
        )
        self.enable_sending_cb.setStyleSheet("font-weight: bold; font-size: 11px;")
        row1.addWidget(self.enable_sending_cb)

        row1.addStretch()
        layout.addLayout(row1)

        # ═══════════════════════════════════════════
        #  KOMUT GİRİŞ FORMU
        # ═══════════════════════════════════════════
        form_group = QGroupBox("Yeni Komut")
        form_layout = QVBoxLayout()
        form_layout.setSpacing(6)

        # ── Satır A: Kaynak + Hedef koordinatlar ──
        coord_row = QHBoxLayout()
        coord_row.setSpacing(6)

        coord_row.addWidget(QLabel("Kaynak:"))
        self.sa_source_combo = QComboBox()
        self.sa_source_combo.setMinimumWidth(200)
        self.sa_source_combo.addItem("— Köy Seçin —")
        self.sa_source_combo.currentIndexChanged.connect(self._sa_on_source_changed)
        coord_row.addWidget(self.sa_source_combo)
        self.sa_source_points_label = QLabel("Puan: —")
        self.sa_source_points_label.setStyleSheet("font-size: 10px;")
        coord_row.addWidget(self.sa_source_points_label)

        coord_row.addSpacing(20)

        coord_row.addWidget(QLabel("Hedef:"))
        self.sa_tgt_x = QSpinBox()
        self.sa_tgt_x.setRange(0, 999)
        self.sa_tgt_x.setFixedWidth(60)
        coord_row.addWidget(self.sa_tgt_x)
        coord_row.addWidget(QLabel("|"))
        self.sa_tgt_y = QSpinBox()
        self.sa_tgt_y.setRange(0, 999)
        self.sa_tgt_y.setFixedWidth(60)
        coord_row.addWidget(self.sa_tgt_y)

        coord_row.addSpacing(10)
        self.sa_quick_target = QLineEdit()
        self.sa_quick_target.setPlaceholderText("veya: 505|448")
        self.sa_quick_target.setFixedWidth(100)
        self.sa_quick_target.returnPressed.connect(self._sa_parse_target)
        self.sa_quick_target.editingFinished.connect(self._sa_sync_quick_target_to_spinboxes)
        coord_row.addWidget(self.sa_quick_target)

        coord_row.addStretch()
        form_layout.addLayout(coord_row)

        # ── Satır B: Asker giriş alanları ──
        troop_row = QHBoxLayout()
        troop_row.setSpacing(2)

        self.SA_UNIT_DEFS = [
            ("spear", "Mız"), ("sword", "Kıl"), ("axe", "Bal"), ("archer", "Okç"),
            ("spy", "Cas"), ("light", "HSv"), ("marcher", "AOk"), ("heavy", "ASv"),
            ("ram", "Koç"), ("catapult", "Man"), ("knight", "Şöv"), ("snob", "Mis"),
        ]

        self.sa_troop_inputs = {}
        self.sa_troop_avail = {}
        self._sa_unit_theme_widgets = []

        for key, short in self.SA_UNIT_DEFS:
            unit_frame = QFrame()
            unit_frame.setStyleSheet("border: 1px solid #ddd; border-radius: 2px; padding: 1px;")
            uf_layout = QVBoxLayout(unit_frame)
            uf_layout.setContentsMargins(2, 1, 2, 1)
            uf_layout.setSpacing(0)

            # ── Birim ikonu (16×16) ──
            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setFixedHeight(18)
            icon_lbl.setStyleSheet("border: none;")
            troop_icon_mgr.apply_to_label(icon_lbl, key)
            uf_layout.addWidget(icon_lbl)

            name_lbl = QLabel(short)
            name_lbl.setAlignment(Qt.AlignCenter)
            name_lbl.setStyleSheet("font-size: 11px; font-weight: bold; color: #444; border: none;")
            uf_layout.addWidget(name_lbl)

            spin = QSpinBox()
            spin.setRange(0, 99999)
            spin.setValue(0)
            spin.setFixedWidth(55)
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet("font-size: 12px; border: 1px solid #aaa;")
            uf_layout.addWidget(spin)
            self.sa_troop_inputs[key] = spin

            avail_lbl = SaTroopAvailLabel(key, self)
            avail_lbl.setAlignment(Qt.AlignCenter)
            avail_lbl.setStyleSheet("font-size: 13px; color: #777; border: none;")
            uf_layout.addWidget(avail_lbl)
            self.sa_troop_avail[key] = avail_lbl

            self._sa_unit_theme_widgets.append((unit_frame, name_lbl, spin))
            troop_row.addWidget(unit_frame)

        troop_row.addStretch()
        form_layout.addLayout(troop_row)

        # ── Satır C: Komut türü + Ekle butonu ──
        # ── Satır C: Tür + Zaman butonları + Ekle ──
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        action_row.addWidget(QLabel("Tür:"))
        self.cmd_type_combo = QComboBox()
        self.cmd_type_combo.addItems(["Saldırı", "Destek"])
        self.cmd_type_combo.setFixedWidth(80)
        action_row.addWidget(self.cmd_type_combo)

        action_row.addSpacing(15)

        # Varış zamanı ayarla butonu
        self.btn_set_arrive = QPushButton("Varış zamanı ayarla")
        self.btn_set_arrive.setCursor(Qt.PointingHandCursor)
        self.btn_set_arrive.setCheckable(True)
        self.btn_set_arrive.setStyleSheet(
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #5a3e1b;")
        self.btn_set_arrive.clicked.connect(self._sa_toggle_time_mode)
        action_row.addWidget(self.btn_set_arrive)

        # Gönderim zamanı ayarla butonu
        self.btn_set_send = QPushButton("Gönderim zamanı ayarla")
        self.btn_set_send.setCursor(Qt.PointingHandCursor)
        self.btn_set_send.setCheckable(True)
        self.btn_set_send.setStyleSheet(
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #5a3e1b;")
        self.btn_set_send.clicked.connect(self._sa_toggle_time_mode)
        action_row.addWidget(self.btn_set_send)

        action_row.addSpacing(15)

        self.sa_add_btn = QPushButton("+ Tabloya Ekle")
        self.sa_add_btn.setObjectName("startBtn")
        self.sa_add_btn.setCursor(Qt.PointingHandCursor)
        self.sa_add_btn.setMinimumHeight(28)
        self.sa_add_btn.clicked.connect(self._sa_add_task)
        action_row.addWidget(self.sa_add_btn)

        self.sa_misyoner_multi_btn = QPushButton("Misyoner ekle")
        self.sa_misyoner_multi_btn.setCursor(Qt.PointingHandCursor)
        self.sa_misyoner_multi_btn.setMinimumHeight(28)
        self.sa_misyoner_multi_btn.setToolTip(
            "Aynı gönderim zamanıyla birden fazla dalga kuyruğa eklenir (oyunda tek komutta birleşebilir)."
        )
        self.sa_misyoner_multi_btn.clicked.connect(self._sa_open_misyoner_multi_dialog)
        action_row.addWidget(self.sa_misyoner_multi_btn)

        action_row.addStretch()
        form_layout.addLayout(action_row)

        # ── Satır D: Zaman giriş alanları (buton seçimine göre gösterilir) ──
        self.sa_time_row = QHBoxLayout()
        self.sa_time_row.setSpacing(6)

        self.sa_time_label = QLabel("Varış zamanı:")
        self.sa_time_label.setStyleSheet("font-weight: bold; color: #5a3e1b; font-size: 11px;")
        self.sa_time_row.addWidget(self.sa_time_label)

        self.sa_time_date = QLineEdit()
        self.sa_time_date.setPlaceholderText("GG.AA")
        self.sa_time_date.setFixedWidth(50)
        self.sa_time_date.setAlignment(Qt.AlignCenter)
        self.sa_time_row.addWidget(self.sa_time_date)

        self.sa_time_row.addWidget(QLabel("'de"))

        self.sa_time_clock = QLineEdit()
        self.sa_time_clock.setPlaceholderText("SS:DD:SS:ms")
        self.sa_time_clock.setFixedWidth(100)
        self.sa_time_clock.setAlignment(Qt.AlignCenter)
        self.sa_time_row.addWidget(self.sa_time_clock)

        self.sa_time_row.addStretch()

        # Başlangıçta gizle
        self.sa_time_widget = QWidget()
        self.sa_time_widget.setLayout(self.sa_time_row)
        self.sa_time_widget.setVisible(False)
        form_layout.addWidget(self.sa_time_widget)

        # Aktif zaman modu: None / "arrive" / "send"
        self._sa_time_mode = None

        form_group.setLayout(form_layout)
        layout.addWidget(form_group)

        aux_row = QHBoxLayout()
        aux_row.setSpacing(6)
        b_operasyon = QPushButton("Operasyon")
        b_operasyon.setCursor(Qt.PointingHandCursor)
        b_operasyon.setToolTip(
            "Toplu yapıştır, operasyon planı ve hedef listesi — ayrı pencerede sekmeler."
        )
        b_operasyon.clicked.connect(lambda: self._open_army_aux_dialog(1))
        aux_row.addWidget(b_operasyon)
        aux_row.addStretch()
        layout.addLayout(aux_row)

        self.sa_table = QTreeWidget()
        self.sa_table.setAlternatingRowColors(True)
        self.sa_table.setRootIsDecorated(False)
        self.sa_table.setSelectionMode(QTreeWidget.ExtendedSelection)

        headers = [
            "Kaynak", "Hedef",
            "Mız", "Kıl", "Bal", "Okç", "Cas", "HSv", "AOk", "ASv", "Koç", "Man", "Şöv", "Mis",
            "Tür", "Gönderim Zamanı", "Varış Zamanı", "Dönüş Zamanı", "ID"
        ]
        self.sa_table.setHeaderLabels(headers)
        self.sa_table.setColumnCount(len(headers))

        col_widths = [
            120, 70,
            36, 36, 36, 36, 36, 36, 36, 36, 36, 36, 36, 36,
            50, 145, 145, 145, 30
        ]
        for i, w in enumerate(col_widths):
            self.sa_table.setColumnWidth(i, w)

        self.sa_table.header().setDefaultAlignment(Qt.AlignCenter)
        self.sa_table.header().setStyleSheet(
            "QHeaderView::section { font-size: 10px; padding: 3px; }")
        self.sa_table.setStyleSheet("QTreeWidget { font-size: 11px; }")

        layout.addWidget(self.sa_table, 1)

        # ═══════════════════════════════════════════
        #  ALT ÇUBUK
        # ═══════════════════════════════════════════
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self.sa_totals_label = QLabel("TOPLAM: 0 komut")
        self.sa_totals_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #333;")
        bottom.addWidget(self.sa_totals_label)

        bottom.addStretch()

        btn_del = QPushButton("Seçileni Sil")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self._sa_delete_selected)
        bottom.addWidget(btn_del)

        btn_clear = QPushButton("Tümünü Temizle")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.clicked.connect(self._sa_clear_all)
        bottom.addWidget(btn_clear)

        layout.addLayout(bottom)

        # Gönderim anahtarı yalnızca zamanlayıcı POST'larını açar; form her zaman düzenlenebilir
        # (kuyruk planlama gönderimden bağımsız).
        self._sa_controls = []

        self.enable_sending_cb.toggled.connect(self._toggle_sending_army)
        self._toggle_sending_army(False)

        self._sa_restore_army_queue()
        self.sa_tab = tab
        self.tabs.addTab(tab, "⚔️ Ordu Gönder")

    def _open_army_aux_dialog(self, page: int = 0):
        """Toplu yapıştır / hedef planı / hedef-komut listesi — ayrı pencere."""
        dlg = ArmyAuxToolsDialog(self, page)
        dlg.exec_()

    # ── ORDU GÖNDER YARDIMCI FONKSİYONLAR ─────

    def _toggle_sending_army(self, enabled):
        for widget in self._sa_controls:
            widget.setEnabled(enabled)
        if enabled:
            self.enable_sending_cb.setStyleSheet(
                "font-weight: bold; font-size: 11px; color: #228822;")
            self._add_log("KOMUT", "success", "Ordu gönderimi aktif edildi.")
        else:
            self.enable_sending_cb.setStyleSheet(
                "font-weight: bold; font-size: 11px; color: #cc4444;")
            if hasattr(self, 'log_text'):
                self._add_log("KOMUT", "warn", "Ordu gönderimi devre dışı.")

    def _sa_troops_for_selected_source(self):
        """Kaynak combobox'taki köyün troops sözlüğü; köy yoksa veya veri yoksa {}."""
        if not hasattr(self, "sa_source_combo"):
            return {}
        village_id = self.sa_source_combo.currentData()
        if not village_id:
            return {}
        all_v = self._game_data.get("all_villages", [])
        for v in all_v:
            if v.get("id") == village_id:
                t = v.get("troops")
                return dict(t) if isinstance(t, dict) else {}
        v = self._game_data.get("village", {})
        if v and v.get("id") == village_id:
            t = self._game_data.get("troops")
            return dict(t) if isinstance(t, dict) else {}
        return {}

    def _sa_on_avail_troop_clicked(self, unit_key: str) -> None:
        if not hasattr(self, "sa_troop_inputs") or unit_key not in self.sa_troop_inputs:
            return
        troops = self._sa_troops_for_selected_source()
        n = int(troops.get(unit_key, 0) or 0)
        if n <= 0:
            return
        self.sa_troop_inputs[unit_key].setValue(n)

    def _sa_on_source_changed(self, index):
        """Kaynak köy seçildiğinde asker mevcutlarını ve köy puanını güncelle."""
        if index < 0:
            return
        village_id = self.sa_source_combo.currentData()
        if not village_id:
            self._sa_source_points_cache = 0
            if hasattr(self, "sa_source_points_label"):
                self.sa_source_points_label.setText("Puan: —")
            muted = "#a8a8a8" if self._dark_mode else "#888"
            for lbl in self.sa_troop_avail.values():
                lbl.setText("(0)")
                lbl.setStyleSheet(
                    f"font-size: 13px; color: {muted}; border: none;"
                )
                lbl.setCursor(Qt.ArrowCursor)
            return

        all_v = self._game_data.get("all_villages", [])
        found_troops = None
        found_pts = 0
        for v in all_v:
            if v.get("id") == village_id:
                found_troops = v.get("troops", {})
                try:
                    found_pts = int(v.get("points") or 0)
                except (TypeError, ValueError):
                    found_pts = 0
                break

        if found_troops is None:
            v = self._game_data.get("village", {})
            if v and v.get("id") == village_id:
                found_troops = self._game_data.get("troops", {})
                if found_pts <= 0:
                    try:
                        found_pts = int(v.get("points") or 0)
                    except (TypeError, ValueError):
                        found_pts = 0
            else:
                found_troops = {}

        if found_troops is None:
            found_troops = {}

        self._sa_source_points_cache = max(0, found_pts)
        if hasattr(self, "sa_source_points_label"):
            if found_pts > 0:
                self.sa_source_points_label.setText(f"Puan: {found_pts:,}")
            else:
                self.sa_source_points_label.setText("Puan: —")

        muted = "#a8a8a8" if self._dark_mode else "#888"
        pos_green = "#6bdc6b" if self._dark_mode else "#1a6b1a"
        for key, lbl in self.sa_troop_avail.items():
            count = found_troops.get(key, 0)
            lbl.setText(f"({count})")
            if count > 0:
                lbl.setStyleSheet(
                    f"font-size: 13px; font-weight: bold; color: {pos_green}; border: none;"
                )
                lbl.setCursor(Qt.PointingHandCursor)
            else:
                lbl.setStyleSheet(
                    f"font-size: 13px; color: {muted}; border: none;"
                )
                lbl.setCursor(Qt.ArrowCursor)

    def _sa_sync_quick_target_to_spinboxes(self) -> bool:
        """'505|448' hızlı hedef kutusunu X/Y spinbox'larına yazar. Boşsa True (değişmez).
        Geçersiz metin varsa False (spinbox'lara dokunulmaz)."""
        text = self.sa_quick_target.text().strip()
        if not text:
            return True
        match = re.match(
            r"^\s*\(?\s*(\d{1,3})\s*[|,/]\s*(\d{1,3})\s*\)?\s*$",
            text,
        )
        if match:
            self.sa_tgt_x.setValue(int(match.group(1)))
            self.sa_tgt_y.setValue(int(match.group(2)))
            self.sa_quick_target.setStyleSheet("")
            return True
        self.sa_quick_target.setStyleSheet("border: 1px solid #cc2222;")
        return False

    def _sa_parse_target(self):
        """Enter: hızlı kutudan spinbox'lara aktar."""
        if not self._sa_sync_quick_target_to_spinboxes():
            pass  # kırmızı çerçeve yeterli

    def _sa_toggle_time_mode(self):
        """Varış/Gönderim butonlarına basıldığında zaman giriş panelini göster/gizle."""
        sender = self.sender()

        if sender == self.btn_set_arrive:
            if self.btn_set_arrive.isChecked():
                self._sa_time_mode = "arrive"
                self.sa_time_label.setText("Varış zamanı:")
                self.sa_time_widget.setVisible(True)
                self._sa_fill_server_time()
                self.btn_set_send.setChecked(False)
            else:
                self._sa_time_mode = None
                self.sa_time_widget.setVisible(False)

        elif sender == self.btn_set_send:
            if self.btn_set_send.isChecked():
                self._sa_time_mode = "send"
                self.sa_time_label.setText("Gönderim zamanı:")
                self.sa_time_widget.setVisible(True)
                self._sa_fill_server_time()
                self.btn_set_arrive.setChecked(False)
            else:
                self._sa_time_mode = None
                self.sa_time_widget.setVisible(False)

        tc = "#d8c8a8" if self._dark_mode else "#5a3e1b"
        self.sa_time_label.setStyleSheet(
            f"font-weight: bold; color: {tc}; font-size: 11px;"
        )
        self._sa_refresh_time_mode_button_styles()

    def _sa_fill_server_time(self):
        """Zaman alanlarını o anki sunucu saatiyle doldur.
        _server_time_text formatı: '18/03/2026 4:00:01.234'
        Hedef: tarih → 18.03  saat → 04:00:01:234
        """
        text = self._server_time_text
        if not text or not self._server_time_synced:
            return

        try:
            # "18/03/2026 4:00:01.234" veya "18/03/2026 14:00:01.234"
            parts = text.split(" ", 1)
            if len(parts) != 2:
                return

            date_part = parts[0].strip()   # "18/03/2026"
            time_part = parts[1].strip()   # "4:00:01.234"

            # Tarih parse: DD/MM/YYYY veya DD.MM.YYYY
            date_match = re.match(r'(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})', date_part)
            if date_match:
                day = date_match.group(1).zfill(2)
                month = date_match.group(2).zfill(2)
                self.sa_time_date.setText(f"{day}.{month}")

            # Saat parse: H:MM:SS.ms veya HH:MM:SS.ms
            time_match = re.match(r'(\d{1,2}):(\d{2}):(\d{2})\.?(\d{0,3})', time_part)
            if time_match:
                h = time_match.group(1).zfill(2)
                m = time_match.group(2)
                s = time_match.group(3)
                ms = (time_match.group(4) or "0")[:3].zfill(3)
                self.sa_time_clock.setText(f"{h}:{m}:{s}:{ms}")
        except Exception:
            pass

    def _sa_add_task(self):
        """Form verilerinden tabloya yeni komut ekle.
        Yolculuk süresini hesapla, gönderim/varış/dönüş zamanlarını otomatik doldur.
        """
        src_text = self.sa_source_combo.currentText()
        if "Köy Seçin" in src_text:
            QMessageBox.warning(self, "Uyarı", "Kaynak köy seçin!")
            return

        # Hızlı 'xxx|xxx' kutusu Enter basılmadan doldurulmuş olabilir — önce spinbox'a aktar
        if not self._sa_sync_quick_target_to_spinboxes():
            QMessageBox.warning(
                self,
                "Uyarı",
                "Hedef kutusunda geçerli koordinat yok.\nÖrnek: 505|448 veya (505|448)",
            )
            return

        tgt = f"{self.sa_tgt_x.value()}|{self.sa_tgt_y.value()}"

        troop_values = []
        has_troops = False
        troop_keys_sent = []
        for key, _ in self.SA_UNIT_DEFS:
            val = self.sa_troop_inputs[key].value()
            troop_values.append(str(val))
            if val > 0:
                has_troops = True
                troop_keys_sent.append(key)

        if not has_troops:
            QMessageBox.warning(self, "Uyarı", "En az bir asker girin!")
            return

        # Zaman modu kontrolü
        if not self._sa_time_mode:
            QMessageBox.warning(self, "Uyarı",
                "Önce 'Varış zamanı ayarla' veya 'Gönderim zamanı ayarla' butonuna basın!")
            return

        time_date = self.sa_time_date.text().strip()
        time_clock = self.sa_time_clock.text().strip()

        if not time_date or not time_clock:
            QMessageBox.warning(self, "Uyarı",
                "Zaman alanlarını doldurun!\n"
                "Tarih → GG.AA | Saat → SS:DD:SS:ms\n"
                "Örnek: 20.03  20:45:24:208")
            if not time_date:
                self.sa_time_date.setStyleSheet("border: 1px solid #cc2222;")
            if not time_clock:
                self.sa_time_clock.setStyleSheet("border: 1px solid #cc2222;")
            return

        self.sa_time_date.setStyleSheet("")
        self.sa_time_clock.setStyleSheet("")

        # ── Kaynak koordinatlarını bul ──
        src_x, src_y = self._sa_get_source_coords()
        if src_x is None:
            QMessageBox.warning(self, "Uyarı", "Kaynak köy koordinatları bulunamadı!")
            return

        tgt_x = self.sa_tgt_x.value()
        tgt_y = self.sa_tgt_y.value()

        input_dt = self._sa_parse_time_input(time_date, time_clock)
        if input_dt is None:
            QMessageBox.warning(self, "Uyarı",
                "Zaman formatı hatalı!\n"
                "Tarih → GG.AA | Saat → SS:DD:SS:ms\n"
                "Örnek: 20.03  20:45:24:208")
            return

        troops_map = {k: self.sa_troop_inputs[k].value() for k, _ in self.SA_UNIT_DEFS}
        cmd_attack = self.cmd_type_combo.currentIndex() == 0
        ok, err = self._sa_append_row_from_values(
            src_text, src_x, src_y, tgt_x, tgt_y, troops_map, cmd_attack,
            self._sa_time_mode, input_dt,
        )
        if not ok:
            QMessageBox.warning(self, "Uyarı", err or "Komut eklenemedi")

    def _sa_open_misyoner_multi_dialog(self):
        MisyonerMultiWaveDialog(self, self).exec_()

    # Koçbaşı komutu: otomatik doldurulacak birim anahtarları (baltacı, hafif, koç, atlı okçu, casus)
    SA_RAM_AUTO_KEYS = ("axe", "light", "ram", "marcher", "spy")

    def _sa_append_row_from_values(
        self,
        src_text,
        src_x,
        src_y,
        tgt_x,
        tgt_y,
        troops_map,
        cmd_attack,
        time_mode,
        input_dt,
        *,
        fake_dialog=True,
    ):
        """Gönderim kuyruğuna tek satır ekler. (True, None) veya (False, hata_metni)."""
        troops_map = dict(troops_map)

        total = sum(int(troops_map.get(k, 0) or 0) for k, _ in self.SA_UNIT_DEFS)
        if total <= 0:
            return False, "En az bir asker olmalı"
        if int(tgt_x) == 0 and int(tgt_y) == 0:
            return False, "Hedef 0|0 geçersiz — hedef X ve Y koordinatlarını girin"

        violate, fake_detail = self._sa_evaluate_fake_violation(
            cmd_attack, troops_map, src_x, src_y
        )
        if violate:
            if not fake_dialog:
                return False, fake_detail or "Fake limiti altında"
            ref_pts = self._sa_resolve_source_village_points(src_x, src_y)
            pct = int(self.SA_FAKE_MIN_POP_PERCENT)
            min_pop = max(1, int(math.ceil(ref_pts * pct / 100.0)))
            pop = self._sa_troops_total_population(troops_map)
            r = QMessageBox.question(
                self,
                "Fake limiti",
                f"Kaynak köy puanı (komutun çıktığı köy): {ref_pts}\n"
                f"Gerekli minimum nüfus (≈%{pct}): {min_pop}\n"
                f"Komuttaki toplam nüfus: {pop}\n\n"
                "Yine de kuyruğa eklemek istiyor musunuz?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return False, "Fake limiti — iptal edildi"

        tgt = f"{int(tgt_x)}|{int(tgt_y)}"
        troop_keys_sent = [k for k, _ in self.SA_UNIT_DEFS if int(troops_map.get(k, 0) or 0) > 0]
        distance = math.sqrt(
            (float(tgt_x) - float(src_x)) ** 2 + (float(tgt_y) - float(src_y)) ** 2
        )
        travel_sec = self._sa_calc_travel_time(distance, troop_keys_sent)
        travel_delta = datetime.timedelta(seconds=travel_sec)

        if time_mode == "send":
            send_dt = input_dt
            arrive_dt = send_dt + travel_delta
        elif time_mode == "arrive":
            arrive_dt = input_dt
            send_dt = arrive_dt - travel_delta
        else:
            return False, "Zaman modu geçersiz"

        return_dt = arrive_dt + travel_delta

        send_str = self._sa_format_time(send_dt)
        arrive_str = self._sa_format_time(arrive_dt)
        return_str = self._sa_format_time(return_dt, ms_zero=True)

        cmd_type = "Sld" if cmd_attack else "Dst"
        task_id = str(self.sa_table.topLevelItemCount() + 1)

        troop_values = [str(int(troops_map.get(k, 0) or 0)) for k, _ in self.SA_UNIT_DEFS]
        row_data = [src_text, tgt] + troop_values + [cmd_type, send_str, arrive_str, return_str, task_id]
        item = QTreeWidgetItem(row_data)

        for col in range(2, 14):
            item.setTextAlignment(col, Qt.AlignCenter)
            if troop_values[col - 2] != "0":
                item.setForeground(col, QColor("#2d5a9e"))
            else:
                item.setForeground(col, QColor("#ccc"))

        item.setTextAlignment(14, Qt.AlignCenter)
        for col in [15, 16, 17, 18]:
            item.setTextAlignment(col, Qt.AlignCenter)

        self.sa_table.addTopLevelItem(item)
        self._sa_update_totals()

        travel_min = travel_sec / 60
        self._add_log(
            "KOMUT",
            "info",
            f"Komut eklendi: {cmd_type} {src_text} → ({tgt}) | "
            f"Mesafe: {distance:.2f} kare | Yolculuk: {travel_min:.1f}dk | "
            f"Gönderim: {send_str} | Varış: {arrive_str} | Dönüş: {return_str}",
        )
        return True, None

    def _sa_village_xy(self, v):
        """Köy sözlüğünden x,y — sayı yoksa coord metninden çöz."""
        if not v:
            return None, None
        vx, vy = v.get("x"), v.get("y")
        if vx is not None and vy is not None:
            try:
                return int(vx), int(vy)
            except (TypeError, ValueError):
                pass
        c = v.get("coord") or ""
        cm = re.search(r"(\d+)\s*[|/]\s*(\d+)", str(c))
        if cm:
            return int(cm.group(1)), int(cm.group(2))
        return None, None

    def _sa_find_village_at_coord(self, x, y):
        """all_villages veya aktif köyde koordinata göre köy sözlüğü."""
        xi, yi = int(x), int(y)
        for v in self._game_data.get("all_villages", []):
            vx, vy = self._sa_village_xy(v)
            if vx == xi and vy == yi:
                return v
        v = self._game_data.get("village", {})
        vx, vy = self._sa_village_xy(v)
        if vx == xi and vy == yi:
            return v
        return None

    def _sa_troops_total_population(self, troops_map):
        """Komuttaki birliklerin toplam nüfus yükü (SA_UNIT_POPULATION)."""
        total = 0
        pop = self.SA_UNIT_POPULATION
        for k, _ in self.SA_UNIT_DEFS:
            n = int(troops_map.get(k, 0) or 0)
            total += n * int(pop.get(k, 1))
        return total

    def _sa_resolve_source_village_points(self, src_x, src_y):
        """Seçili kaynak köyün puanı (önbellek); yoksa koordinata göre listeden."""
        cached = int(getattr(self, "_sa_source_points_cache", 0) or 0)
        if cached > 0:
            return cached
        vt = self._sa_find_village_at_coord(src_x, src_y)
        if not vt:
            return 0
        try:
            p = int(vt.get("points") or 0)
        except (TypeError, ValueError):
            return 0
        return p if p > 0 else 0

    def _sa_evaluate_fake_violation(self, cmd_attack, troops_map, src_x, src_y):
        """
        (True, açıklama) = saldırı fake eşiğinin altında.
        Kaynak köy puanı bilinmiyorsa veya destek komutuysa (False, None).
        """
        if not cmd_attack:
            return False, None
        ref_pts = self._sa_resolve_source_village_points(src_x, src_y)
        if ref_pts <= 0:
            return False, None
        pct = int(self.SA_FAKE_MIN_POP_PERCENT)
        min_pop = max(1, int(math.ceil(ref_pts * pct / 100.0)))
        pop = self._sa_troops_total_population(troops_map)
        if pop >= min_pop:
            return False, None
        return True, f"Fake: nüfus {pop} < min. {min_pop} (kaynak {ref_pts} puan, %{pct})"

    def _sa_parse_bulk_datetime(self, s):
        """Örnek: 12-04-2026 01:03:27.438 veya 12.04.2026 01:03:27 (GG-AA-YYYY gönderim zamanı)."""
        s = (s or "").strip()
        m = re.match(
            r"(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?",
            s,
        )
        if not m:
            m = re.match(
                r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?",
                s,
            )
        if not m:
            return None
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
        ms_raw = (m.group(7) or "0")[:3]
        ms = int(ms_raw.zfill(3))
        try:
            return datetime.datetime(year, month, day, hour, minute, second, ms * 1000)
        except (ValueError, OverflowError):
            return None

    def _sa_normalize_bulk_paste(self, text):
        """Forum/NBSP/en-dash yapıştırmalarını regex ile uyumlu hale getir."""
        if not text:
            return text
        t = text.replace("\r\n", "\n").replace("\r", "\n")
        t = t.replace("\u00a0", " ").replace("\u202f", " ")
        for ch in ("\u2013", "\u2014", "\u2212"):
            t = t.replace(ch, "-")
        return t

    def _sa_parse_bulk_table_lines(self, text):
        """Forum [table] satırlarını veya basit pipe formatını çözümler."""
        rows = []
        bbcode_re = re.compile(
            r"\[\*\]\s*\[coord\]\s*(\d+)\s*\|\s*(\d+)\s*\[/coord\]\s*"
            r"\[\|\]\s*\[coord\]\s*(\d+)\s*\|\s*(\d+)\s*\[/coord\]\s*"
            r"\[\|\]\s*\[unit\]\s*(\w+)\s*\[/unit\]\s*"
            r"\[\|\]\s*([^[\|]+?)\s*"
            r"\[\|\]\s*\[b\]\s*([^[]+?)\s*\[/b\]",
            re.I | re.S,
        )
        simple_re = re.compile(
            r"^(\d{1,3})\s*\|\s*(\d{1,3})\s*\|\s*(\d{1,3})\s*\|\s*(\d{1,3})\s*\|\s*(\w+)\s*\|\s*"
            r"(Attack|Support|Saldırı|Destek)\s*\|\s*(.+)$",
            re.I,
        )
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("[/table]") or line.startswith("[**]"):
                continue
            if line.startswith("[table]"):
                continue
            m = bbcode_re.search(line)
            if m:
                rows.append({
                    "sx": int(m.group(1)),
                    "sy": int(m.group(2)),
                    "tx": int(m.group(3)),
                    "ty": int(m.group(4)),
                    "unit": m.group(5).lower(),
                    "action": m.group(6).strip(),
                    "time_raw": m.group(7).strip(),
                })
                continue
            m2 = simple_re.match(line)
            if m2:
                rows.append({
                    "sx": int(m2.group(1)),
                    "sy": int(m2.group(2)),
                    "tx": int(m2.group(3)),
                    "ty": int(m2.group(4)),
                    "unit": m2.group(5).lower(),
                    "action": m2.group(6).strip(),
                    "time_raw": m2.group(7).strip(),
                })
        return rows

    def _sa_troops_ram_full_from_village(self, village_troops):
        """Köydeki balta, hafif, koç, atlı okçu, casus — tamamı."""
        t = {k: 0 for k, _ in self.SA_UNIT_DEFS}
        vt = village_troops or {}
        for k in self.SA_RAM_AUTO_KEYS:
            t[k] = int(vt.get(k, 0) or 0)
        return t

    def _sa_troops_noble_split_wave(self, village_troops, parts, wave_index):
        """Misyoner bölme: eskort eşit parçalara; her parçada 1 misyoner (köyde yeterli yoksa önceki parçalara)."""
        t = {k: 0 for k, _ in self.SA_UNIT_DEFS}
        vt = village_troops or {}
        for k in self.SA_RAM_AUTO_KEYS:
            tot = int(vt.get(k, 0) or 0)
            t[k] = tot // parts + (1 if wave_index < (tot % parts) else 0)
        total_snob = int(vt.get("snob", 0) or 0)
        # 3 parça → her satırda 1 snob; 4 parça → yine her satırda 1 (köydeki snob yetmezse kalan parçalar 0)
        t["snob"] = 1 if wave_index < total_snob and wave_index < parts else 0
        return t

    def _sa_bulk_import_text(self, raw, msg_parent=None):
        """Toplu forum/BB metnini kuyruğa ekler. msg_parent: QMessageBox için (ör. diyalog)."""
        parent = msg_parent or self
        try:
            text = self._sa_normalize_bulk_paste(raw or "").strip()
            if not text:
                QMessageBox.information(parent, "Toplu aktarım", "Metin kutusu boş.")
                return

            parsed = self._sa_parse_bulk_table_lines(text)
            if not parsed:
                QMessageBox.warning(
                    parent,
                    "Toplu aktarım",
                    "Geçerli satır bulunamadı.\n"
                    "Beklenen örnek:\n"
                    "[*][coord]494|587[/coord][|][coord]574|611[/coord][|][unit]ram[/unit][|]Attack[|]"
                    "[b]12-04-2026 01:03:27.438[/b]",
                )
                return

            noble_parts = 3
            if any(r["unit"] == "snob" for r in parsed):
                msg = QMessageBox(parent)
                msg.setIcon(QMessageBox.Question)
                msg.setWindowTitle("Misyoner bölme")
                msg.setText(
                    "Listede misyoner (snob) komutu var.\n\n"
                    "Eskort birimleri (balta, hafif, koç, atlı okçu, casus) parça sayısına bölünür.\n"
                    "Her parçaya 1 misyoner yazılır (3 parça → 3×1, 4 parça → 4×1; köyde daha az snob varsa "
                    "yalnızca ilk parçalara 1'er konur).\n\n"
                    "Kaç parça olsun?"
                )
                b3 = msg.addButton("3 parça", QMessageBox.AcceptRole)
                b4 = msg.addButton("4 parça", QMessageBox.AcceptRole)
                b_cancel = msg.addButton(QMessageBox.Cancel)
                msg.exec_()
                clicked = msg.clickedButton()
                if clicked is None or clicked == b_cancel:
                    return
                if clicked == b3:
                    noble_parts = 3
                elif clicked == b4:
                    noble_parts = 4
                else:
                    return

            added = 0
            skipped = []

            for r in parsed:
                dt = self._sa_parse_bulk_datetime(r["time_raw"])
                if dt is None:
                    skipped.append(f"Zaman hatalı: {r['sx']}|{r['sy']} → {r['time_raw']}")
                    continue

                act = r["action"].lower()
                cmd_attack = "attack" in act or "saldır" in act
                if "support" in act or "destek" in act:
                    cmd_attack = False

                v = self._sa_find_village_at_coord(r["sx"], r["sy"])
                if not v:
                    skipped.append(f"Köy bulunamadı: ({r['sx']}|{r['sy']})")
                    continue

                src_name = v.get("name", "?")
                src_text = f"{src_name} ({r['sx']}|{r['sy']})"
                troops_avail = v.get("troops") or {}

                ut = r["unit"]
                if ut == "ram":
                    troops_map = self._sa_troops_ram_full_from_village(troops_avail)
                    ok, err = self._sa_append_row_from_values(
                        src_text,
                        r["sx"],
                        r["sy"],
                        r["tx"],
                        r["ty"],
                        troops_map,
                        cmd_attack,
                        "send",
                        dt,
                        fake_dialog=False,
                    )
                    if ok:
                        added += 1
                    else:
                        skipped.append(f"{src_text}: {err}")
                elif ut == "snob":
                    gap_ms = int(getattr(self, "SA_DISPATCH_WAVE_GAP_MS", 200) or 200)
                    for w in range(noble_parts):
                        troops_map = self._sa_troops_noble_split_wave(
                            troops_avail, noble_parts, w
                        )
                        totw = sum(int(troops_map.get(k, 0) or 0) for k, _ in self.SA_UNIT_DEFS)
                        if totw <= 0:
                            continue
                        wave_dt = dt + datetime.timedelta(milliseconds=w * gap_ms)
                        ok, err = self._sa_append_row_from_values(
                            src_text,
                            r["sx"],
                            r["sy"],
                            r["tx"],
                            r["ty"],
                            troops_map,
                            cmd_attack,
                            "send",
                            wave_dt,
                            fake_dialog=False,
                        )
                        if ok:
                            added += 1
                        else:
                            skipped.append(f"{src_text} snob parça {w + 1}: {err}")
                            break
                else:
                    troops_map = {k: 0 for k, _ in self.SA_UNIT_DEFS}
                    if ut in troops_map:
                        troops_map[ut] = int(troops_avail.get(ut, 0) or 0)
                    ok, err = self._sa_append_row_from_values(
                        src_text,
                        r["sx"],
                        r["sy"],
                        r["tx"],
                        r["ty"],
                        troops_map,
                        cmd_attack,
                        "send",
                        dt,
                        fake_dialog=False,
                    )
                    if ok:
                        added += 1
                    else:
                        skipped.append(f"{src_text} ({ut}): {err}")

            msg_lines = [f"Eklenen komut: {added}"]
            if skipped:
                msg_lines.append("\nAtlanan / hata:")
                msg_lines.extend(skipped[:15])
                if len(skipped) > 15:
                    msg_lines.append(f"... ve {len(skipped) - 15} satır daha")
            QMessageBox.information(parent, "Toplu aktarım", "\n".join(msg_lines))
        except Exception as ex:
            QMessageBox.critical(
                parent,
                "Toplu aktarım",
                f"Beklenmeyen hata:\n{ex}\n\nLütfen metni kaydedip geliştiriciye iletin.",
            )

    # ── YOLCULUK SÜRESİ HESAPLAMA ─────────────

    # Birim hızları (dakika/kare, varsayılan hız=1 dünya)
    UNIT_SPEEDS = {
        "spear": 18, "sword": 22, "axe": 18, "archer": 18,
        "spy": 9, "light": 10, "marcher": 10, "heavy": 11,
        "ram": 30, "catapult": 30, "knight": 10, "snob": 35,
    }

    INCOMINGS_UNIT_TR = {
        "spear": "Mızrak",
        "sword": "Kılıç",
        "axe": "Balta",
        "archer": "Okçu",
        "spy": "Casus",
        "light": "Hafif",
        "marcher": "Atlı okçu",
        "heavy": "Ağır",
        "ram": "Koç",
        "catapult": "Mancınık",
        "knight": "Şövalye",
        "snob": "Misyoner",
    }

    def _sa_calc_travel_time(self, distance, troop_keys):
        """Yolculuk süresini saniye olarak hesapla (TW ms hesaplamaz).
        Formül: süre_dk = mesafe × en_yavaş_birim_hızı / (world_speed × unit_speed)
        """
        slowest = 0
        for key in troop_keys:
            speed = self.UNIT_SPEEDS.get(key, 18)
            if speed > slowest:
                slowest = speed

        if slowest == 0:
            slowest = 18

        world_speed = float(self._game_data.get("world_speed", 1) or 1)
        unit_speed = float(self._game_data.get("unit_speed", 1) or 1)

        travel_seconds = round(distance * slowest * 60 / (world_speed * unit_speed))
        return travel_seconds

    def _sa_get_source_coords(self):
        """Seçili kaynak köyün koordinatlarını döndür."""
        village_id = self.sa_source_combo.currentData()
        if not village_id:
            return None, None

        # all_villages'dan bul
        all_v = self._game_data.get("all_villages", [])
        for v in all_v:
            if v.get("id") == village_id:
                return v.get("x"), v.get("y")

        # Tekli köy fallback
        v = self._game_data.get("village", {})
        if v and v.get("id") == village_id:
            return v.get("x"), v.get("y")

        # Combo text'ten parse et: "Köy Adı (533|461)"
        text = self.sa_source_combo.currentText()
        match = re.search(r'\((\d+)\|(\d+)\)', text)
        if match:
            return int(match.group(1)), int(match.group(2))

        return None, None

    def _sa_parse_time_input(self, date_str, time_str):
        """GG.AA ve SS:DD:SS:ms formatını datetime'a çevir.
        Yıl: sunucu tarihinden veya şimdiki yıldan alınır.
        """
        try:
            # Tarih: GG.AA
            dm = re.match(r'(\d{1,2})\.(\d{1,2})', date_str)
            if not dm:
                return None
            day = int(dm.group(1))
            month = int(dm.group(2))

            # Yılı sunucu tarihinden al
            year = datetime.datetime.now().year
            if self._server_time_text:
                ym = re.search(r'(\d{4})', self._server_time_text)
                if ym:
                    year = int(ym.group(1))

            # Saat: SS:DD:SS:ms (ms opsiyonel)
            tm = re.match(r'(\d{1,2}):(\d{2}):(\d{2}):?(\d{0,3})', time_str)
            if not tm:
                return None
            hour = int(tm.group(1))
            minute = int(tm.group(2))
            second = int(tm.group(3))
            ms_str = (tm.group(4) if tm.group(4) else "0")[:3]
            ms = int(ms_str.zfill(3))

            return datetime.datetime(year, month, day, hour, minute, second, ms * 1000)
        except (ValueError, OverflowError):
            return None

    def _sa_format_time(self, dt, ms_zero=False):
        """datetime'ı GG.AA'de SS:DD:SS:ms formatına çevir."""
        ms = 0 if ms_zero else dt.microsecond // 1000
        return f"{dt.day:02d}.{dt.month:02d}'de {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}:{ms:03d}"

    def _sa_delete_selected(self):
        for item in self.sa_table.selectedItems():
            idx = self.sa_table.indexOfTopLevelItem(item)
            if idx >= 0:
                self.sa_table.takeTopLevelItem(idx)
        self._sa_update_totals()

    def _sa_clear_all(self):
        self.sa_table.clear()
        self._sa_update_totals()

    def _sa_save_army_queue(self):
        """Ordu gönder tablosunu kalıcı ayarlara yazar (uygulama yeniden açılınca yüklenir)."""
        if not hasattr(self, "sa_table") or not hasattr(self, "_settings"):
            return
        try:
            n = self.sa_table.columnCount()
            rows = []
            for i in range(self.sa_table.topLevelItemCount()):
                it = self.sa_table.topLevelItem(i)
                state = str(it.data(0, Qt.UserRole) or "")
                rows.append([it.text(c) for c in range(n)] + [state])
            self._settings.setValue("army_queue/rows_json", json.dumps(rows, ensure_ascii=False))
            self._settings.sync()
        except (TypeError, ValueError):
            pass

    def _sa_restore_army_queue(self):
        """Kayıtlı ordu komutlarını tabloya yükler."""
        if not hasattr(self, "sa_table") or not hasattr(self, "_settings"):
            return
        raw = self._settings.value("army_queue/rows_json", "")
        if not raw:
            return
        try:
            rows = json.loads(str(raw))
        except json.JSONDecodeError:
            return
        if not isinstance(rows, list):
            return
        n = self.sa_table.columnCount()
        # Geçici state'ler (başarıyla gönderilmedi): yeniden pending'e düşer
        _TRANSIENT = {"caching", "cached", "confirming", "confirmed", "sending"}
        self.sa_table.clear()
        for row in rows:
            if not isinstance(row, list):
                continue
            # Geriye uyumluluk: eski kayıtlar n sütunlu, yeniler n+1 (state eklenmiş)
            if len(row) == n + 1:
                text_cols = row[:n]
                state = str(row[n])
            elif len(row) == n:
                text_cols = row
                state = ""
            else:
                continue
            # Geçici state'leri temizle
            if state in _TRANSIENT:
                state = ""
            item = QTreeWidgetItem([str(x) for x in text_cols])
            troop_values = text_cols[2:14]
            for col in range(2, 14):
                item.setTextAlignment(col, Qt.AlignCenter)
                ti = col - 2
                tv = troop_values[ti] if ti < len(troop_values) else "0"
                if tv != "0":
                    item.setForeground(col, QColor("#2d5a9e"))
                else:
                    item.setForeground(col, QColor("#ccc"))
            item.setTextAlignment(14, Qt.AlignCenter)
            for col in (15, 16, 17, 18):
                if col < n:
                    item.setTextAlignment(col, Qt.AlignCenter)
            self.sa_table.addTopLevelItem(item)
            # Kaydedilmiş state'e göre görsel uygula
            if state == "sent":
                self._dispatch_mark_sent(item)
            elif state == "error":
                self._dispatch_mark_error(item, "—")
        self._sa_update_totals()

    def _sa_update_totals(self):
        count = self.sa_table.topLevelItemCount()
        self.sa_totals_label.setText(f"TOPLAM: {count} komut")
        self._sa_save_army_queue()

    def _sa_parse_targets_coords(self, text):
        """Metinden hedef koordinatları çıkar (sıra korunur, mükerrer atlanır)."""
        seen = set()
        out = []
        for m in re.finditer(r"(\d{1,3})\s*\|\s*(\d{1,3})", text or ""):
            x, y = int(m.group(1)), int(m.group(2))
            if x < 0 or x > 999 or y < 0 or y > 999:
                continue
            key = (x, y)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _sa_troop_count(self, troops, key):
        return int((troops or {}).get(key, 0) or 0)

    def _sa_weighted_off_pop(self, troops):
        """Ofansif ağırlıklı nüfus (oyun nüfus katsayılarına yakın)."""
        t = troops or {}
        spy = self._sa_troop_count(t, "spy")
        return (
            self._sa_troop_count(t, "axe")
            + 2 * spy
            + 4 * self._sa_troop_count(t, "light")
            + 5 * self._sa_troop_count(t, "marcher")
            + 5 * self._sa_troop_count(t, "ram")
            + 8 * self._sa_troop_count(t, "catapult")
        )

    def _sa_weighted_def_pop(self, troops):
        """Savunma ağırlıklı nüfus (bilgi; Kami listesi ofansif skorla)."""
        t = troops or {}
        spy = self._sa_troop_count(t, "spy")
        return (
            self._sa_troop_count(t, "spear")
            + self._sa_troop_count(t, "sword")
            + self._sa_troop_count(t, "archer")
            + 2 * spy
            + 6 * self._sa_troop_count(t, "heavy")
        )

    def _sa_is_barbar_village(self, v):
        return "barbar" in (v.get("name") or "").lower()

    def _sa_kami_koyu_counts(self):
        """Kendi köylerinizde Kami tier sayıları (bir köy tek grupta)."""
        t4 = int(self.SA_KAMI_OFF_4_4)
        t3 = int(self.SA_KAMI_OFF_3_4)
        t2 = int(self.SA_KAMI_OFF_2_4)
        out = {"k44": 0, "k34": 0, "k24": 0}
        for v in self._game_data.get("all_villages") or []:
            if self._sa_is_barbar_village(v):
                continue
            off = self._sa_weighted_off_pop(v.get("troops") or {})
            if off >= t4:
                out["k44"] += 1
            elif off >= t3:
                out["k34"] += 1
            elif off >= t2:
                out["k24"] += 1
        return out

    def _sa_format_kami_koyu_summary(self):
        if not (self._game_data.get("all_villages") or []):
            return (
                "Köy / birlik verisi yok. Tarayıcıdan birlikler güncellendikten sonra "
                "bu metin dolacak."
            )
        c = self._sa_kami_koyu_counts()
        t4, t3, t2 = int(self.SA_KAMI_OFF_4_4), int(self.SA_KAMI_OFF_3_4), int(self.SA_KAMI_OFF_2_4)
        t1 = int(self.SA_KAMI_OFF_1_4)
        n_pool = sum(
            1
            for v in (self._game_data.get("all_villages") or [])
            if (not self._sa_is_barbar_village(v))
            and self._sa_weighted_off_pop(v.get("troops") or {}) >= t1
        )
        muted = "#a8a8a8" if self._dark_mode else "#555555"
        return (
            f"<b>Kami köyleri</b> (ofansif ağırlıklı nüfus ≥{t2}/{t3}/{t4}):<br/>"
            f"• 4/4 Kami köyü: <b>{c['k44']}</b><br/>"
            f"• 3/4 Kami köyü: <b>{c['k34']}</b><br/>"
            f"• 2/4 Kami köyü: <b>{c['k24']}</b><br/>"
            f"<span style='color:{muted};'>Plan kaynak havuzu: ofansif skor ≥{t1} olan {n_pool} köy "
            f"(barbar hariç).</span>"
        )

    def _sa_operasyon_off_pool_ok(self, v):
        if not v or self._sa_is_barbar_village(v):
            return False
        return self._sa_weighted_off_pop(v.get("troops") or {}) >= int(self.SA_KAMI_OFF_1_4)

    def _sa_is_offensive_farm_village(self, v, threshold):
        """Kendi saldırı köyü adayı: barbar değil, balta+hafif+koç toplamı eşik üstü."""
        if not v:
            return False
        name = (v.get("name") or "").lower()
        if "barbar" in name:
            return False
        t = v.get("troops") or {}
        axe = int(t.get("axe", 0) or 0)
        light = int(t.get("light", 0) or 0)
        ram = int(t.get("ram", 0) or 0)
        return axe + light + ram >= int(threshold or 0)

    def _sa_village_src_label(self, v):
        sx, sy = self._sa_village_xy(v)
        name = v.get("name", "?")
        if sx is not None and sy is not None:
            return f"{name} ({sx}|{sy})"
        return str(name)

    def _sa_troops_ram_train_with_knight(self, village_troops, with_knight):
        """Koç treni; with_knight ise köydeki tüm şövalyeler eklenir."""
        t = self._sa_troops_ram_full_from_village(village_troops)
        if with_knight:
            n = int((village_troops or {}).get("knight", 0) or 0)
            if n > 0:
                t["knight"] = n
        return t

    def _sa_plan_mass_attacks_with(
        self, targets_text, n_att, th, gap, base_arrive, msg_parent=None
    ):
        """Hedef metni + parametrelerle koç treni kuyruğu; base_arrive zaten parse edilmiş datetime."""
        parent = msg_parent or self
        targets = self._sa_parse_targets_coords(targets_text or "")
        if not targets:
            QMessageBox.warning(
                parent,
                "Hedef planı",
                "En az bir hedef yazın (örn. 505|588 veya 505|588, 500|586).",
            )
            return

        th = int(th or 0)
        off = [
            v
            for v in (self._game_data.get("all_villages") or [])
            if self._sa_is_offensive_farm_village(v, th)
        ]
        if not off:
            QMessageBox.warning(
                parent,
                "Hedef planı",
                "Saldırı köyü bulunamadı. Birlik verisini yenileyin veya «bal+hafif+koç» eşiğini düşürün.",
            )
            return

        n_att = max(1, int(n_att or 1))
        gap = max(1, int(gap or 1))
        added = 0
        skipped = []
        vi_global = 0

        for tx, ty in targets:
            def dist_key(vv):
                sx, sy = self._sa_village_xy(vv)
                if sx is None:
                    return 1e12
                return math.hypot(tx - sx, ty - sy)

            pools = sorted(off, key=dist_key)
            n_pool = len(pools)
            assignments = []
            knight_placed = False

            for ai in range(n_att):
                placed = False
                tries = 0
                while tries < n_pool * 2:
                    v = pools[vi_global % n_pool]
                    vi_global += 1
                    tries += 1
                    sx, sy = self._sa_village_xy(v)
                    if sx is None:
                        continue
                    vt = v.get("troops") or {}
                    with_knight = (not knight_placed) and int(vt.get("knight", 0) or 0) > 0
                    troops = self._sa_troops_ram_train_with_knight(vt, with_knight)
                    tot = sum(int(troops.get(k, 0) or 0) for k, _ in self.SA_UNIT_DEFS)
                    if tot <= 0:
                        continue
                    if with_knight:
                        knight_placed = True
                    assignments.append(
                        {
                            "sx": sx,
                            "sy": sy,
                            "src_text": self._sa_village_src_label(v),
                            "troops": troops,
                        }
                    )
                    placed = True
                    break
                if not placed:
                    skipped.append(f"{tx}|{ty} — {ai + 1}. saldırı için uygun köy yok")

            assignments.sort(
                key=lambda a: (
                    -min(1, int(a["troops"].get("knight", 0) or 0)),
                    -int(a["troops"].get("knight", 0) or 0),
                )
            )
            nk = sum(1 for a in assignments if int(a["troops"].get("knight", 0) or 0) > 0)
            ki = 0
            for a in assignments:
                kn = int(a["troops"].get("knight", 0) or 0)
                if kn > 0:
                    arrive = base_arrive - datetime.timedelta(
                        seconds=int(gap * max(1, nk - ki))
                    )
                    ki += 1
                else:
                    arrive = base_arrive
                ok, err = self._sa_append_row_from_values(
                    a["src_text"],
                    a["sx"],
                    a["sy"],
                    tx,
                    ty,
                    dict(a["troops"]),
                    True,
                    "arrive",
                    arrive,
                    fake_dialog=False,
                )
                if ok:
                    added += 1
                else:
                    skipped.append(f"{a['src_text']} → {tx}|{ty}: {err or '?'}")

        self._sa_update_totals()
        msg = f"Kuyruğa eklenen komut: {added}"
        if skipped:
            msg += "\n\nAtlanan / not:\n" + "\n".join(skipped[:18])
            if len(skipped) > 18:
                msg += f"\n… +{len(skipped) - 18} satır"
        QMessageBox.information(parent, "Operasyon planı", msg)

    def _update_troop_available(self):
        """Kaynak köy değiştiğinde asker mevcutlarını güncelle."""
        self._sa_on_source_changed(self.sa_source_combo.currentIndex())

    # ── KOMUT GÖNDERİM SİSTEMİ (Dispatch) ─────

    def _start_dispatch_timer(self):
        """Komut gönderim zamanlayıcısını başlat."""
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.timeout.connect(self._dispatch_check)
        self._dispatch_timer.start(10)  # 10ms — hassas zamanlama


    def _dispatch_check(self):
        """Tablodaki komutları tara:
        - 5sn kala: rally point token'ını önceden cache'le (pre-fetch)
        - Zaman gelince: sadece 2 POST ile gönder (GET yok)
        """
        if not self.enable_sending_cb.isChecked():
            return
        if not self._server_time_synced or not self._server_time_text:
            return
        if not self.is_running:
            return
        if self._human_verification_required:
            return

        # Offset uygula: negatif = daha erken gönder, pozitif = daha geç gönder
        offset_ms = self.sa_offset_input.value() if hasattr(self, 'sa_offset_input') else 0

        for i in range(self.sa_table.topLevelItemCount()):
            item = self.sa_table.topLevelItem(i)
            if not item:
                continue

            # Çok dalga: ardışık satırlar tek komutta birleştirilir; yalnızca grubun ilk satırı zamanlama görür.
            if not self._dispatch_is_batch_leader(i):
                continue

            state = item.data(0, Qt.UserRole)
            if state in ("sent", "sending", "error"):
                continue

            send_str = item.text(15)
            if not send_str or send_str == "—":
                continue

            send_dt = self._dispatch_parse_time_str(send_str)
            if send_dt is None:
                continue

            # Offset'i gönderim zamanına uygula
            adjusted_send_dt = send_dt + datetime.timedelta(milliseconds=offset_ms)
            diff_ms = self._dispatch_diff_until_send_ms(adjusted_send_dt)
            if diff_ms is None:
                continue

            # 6 saniye kala: rally point token'ını önceden cache'le (fetch bitene kadar süre)
            if 0 < diff_ms <= 6000 and state not in ("cached", "confirmed", "confirming"):
                item.setData(0, Qt.UserRole, "caching")
                self._dispatch_precache(item, i)

            # ~4.5 sn kala: try=confirm POST + JS zamanlayıcı (precache sonrası kalan pay;
            # kalan süre — elapsed formülü yerine mutlak targetUnixMs kullanıldığı için geniş pencere güvenli)
            elif 0 < diff_ms <= 4500 and state == "cached":
                item.setData(0, Qt.UserRole, "confirming")
                self._dispatch_preconfirm(item, i)

            # Zaman geldi: gönder
            elif diff_ms <= 0 and state not in ("caching", "confirming"):
                item.setData(0, Qt.UserRole, "sending")
                for col in range(item.columnCount()):
                    item.setBackground(col, QColor("#fff8e0"))
                self._dispatch_send_command(item, i)

    def _dispatch_target_coords_key(self, tgt_text):
        """Hedef metninden (555|666) koordinat çifti."""
        if not tgt_text:
            return None
        m = re.search(r"(\d+)\|(\d+)", tgt_text)
        return (m.group(1), m.group(2)) if m else None

    def _dispatch_rows_batch_compatible(self, prev_item, curr_item):
        """Aynı köy + hedef + tür; gönderim zamanı aynı veya ardışık dalga (+SA_DISPATCH_WAVE_GAP_MS ms)."""
        if not prev_item or not curr_item:
            return False
        if prev_item.text(0) != curr_item.text(0):
            return False
        if prev_item.text(14) != curr_item.text(14):
            return False
        sa = prev_item.text(15)
        sb = curr_item.text(15)
        if sa != sb:
            ta = self._dispatch_parse_time_str(sa)
            tb = self._dispatch_parse_time_str(sb)
            if ta is None or tb is None:
                return False
            gap = int(getattr(self, "SA_DISPATCH_WAVE_GAP_MS", 200) or 200)
            dms = (tb - ta).total_seconds() * 1000
            if abs(dms - gap) > 2.0:
                return False
        ka = self._dispatch_target_coords_key(prev_item.text(1))
        kb = self._dispatch_target_coords_key(curr_item.text(1))
        return ka is not None and ka == kb

    def _dispatch_is_batch_leader(self, row_idx):
        if row_idx <= 0:
            return True
        prev = self.sa_table.topLevelItem(row_idx - 1)
        curr = self.sa_table.topLevelItem(row_idx)
        if not prev or not curr:
            return True
        return not self._dispatch_rows_batch_compatible(prev, curr)

    def _dispatch_batch_indices_from(self, leader_idx):
        """Aynı grupta birleşen satır indeksleri (en fazla SA_DISPATCH_MAX_BATCH)."""
        n = self.sa_table.topLevelItemCount()
        max_b = int(getattr(self, "SA_DISPATCH_MAX_BATCH", 5) or 5)
        indices = [leader_idx]
        while len(indices) < max_b:
            j = indices[-1] + 1
            if j >= n:
                break
            prev_it = self.sa_table.topLevelItem(indices[-1])
            next_it = self.sa_table.topLevelItem(j)
            if not prev_it or not next_it:
                break
            if self._dispatch_rows_batch_compatible(prev_it, next_it):
                indices.append(j)
            else:
                break
        return indices

    def _dispatch_build_extra_train_rows(self, batch_indices):
        """Dalga 2..N için train[1]..train[N-1] gövdesi (try=confirm yalnızca 1. dalgayı taşır)."""
        rows = []
        for idx in batch_indices[1:]:
            item = self.sa_table.topLevelItem(idx)
            if not item:
                continue
            d = {}
            for col_idx, (key, _) in enumerate(self.SA_UNIT_DEFS):
                try:
                    val = int(item.text(col_idx + 2))
                    if val > 0:
                        d[key] = val
                except ValueError:
                    pass
            rows.append(d)
        return rows

    def _dispatch_parse_server_time(self):
        """Sunucu saati text'ini datetime'a çevir.
        Format: '18/03/2026 4:00:01.234'
        """
        text = self._server_time_text
        if not text:
            return None
        try:
            parts = text.split(" ", 1)
            if len(parts) != 2:
                return None

            date_part = parts[0].strip()
            time_part = parts[1].strip()

            dm = re.match(r'(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})', date_part)
            if not dm:
                return None
            day, month, year = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))

            tm = re.match(r'(\d{1,2}):(\d{2}):(\d{2})\.?(\d{0,3})', time_part)
            if not tm:
                return None
            hour = int(tm.group(1))
            minute = int(tm.group(2))
            second = int(tm.group(3))
            ms_str = (tm.group(4) if tm.group(4) else "0")[:3]
            ms = int(ms_str.zfill(3))

            return datetime.datetime(year, month, day, hour, minute, second, ms * 1000)
        except (ValueError, OverflowError):
            return None

    def _server_now_dt(self):
        """Şu anki sunucu wall-clock tahmini.

        DOM'dan gelen saat ~50 ms aralıkla yenilenir; parse edilmiş anlık değer tek başına
        basamaklı görünür (diff_ms zıplar). Son örneklemeyi perf_counter ile ilerleterek
        gönderim döngüsü (10 ms) ile uyumlu sürekli zaman elde edilir.
        """
        anchor_dt = getattr(self, "_server_time_anchor_dt", None)
        anchor_perf = getattr(self, "_server_time_anchor_perf", None)
        if anchor_dt is None or anchor_perf is None:
            return self._dispatch_parse_server_time()
        elapsed = time.perf_counter() - anchor_perf
        try:
            return anchor_dt + datetime.timedelta(seconds=elapsed)
        except OverflowError:
            return self._dispatch_parse_server_time()

    def _server_now_timing_ms(self):
        """TW Timing ekseninde şu anki sunucu zamanı (ms). DOM metninden bağımsız."""
        anchor_tms = getattr(self, "_anchor_timing_ms", None)
        anchor_perf = getattr(self, "_server_time_anchor_perf", None)
        if anchor_tms is None or anchor_perf is None:
            return None
        return anchor_tms + (time.perf_counter() - anchor_perf) * 1000.0

    def _compute_target_timing_ms(self, adjusted_send_dt):
        """Hedef gönderim anının Timing.serverNow ile aynı epoch'taki karşılığı."""
        anchor_dt = getattr(self, "_server_time_anchor_dt", None)
        anchor_tms = getattr(self, "_anchor_timing_ms", None)
        if anchor_dt is None or anchor_tms is None:
            return None
        return anchor_tms + (adjusted_send_dt - anchor_dt).total_seconds() * 1000.0

    def _dispatch_diff_until_send_ms(self, adjusted_send_dt):
        """Kalan gönderim süresi (ms): önce Timing ekseni, yoksa takvim farkı."""
        st = self._server_now_timing_ms()
        tt = self._compute_target_timing_ms(adjusted_send_dt)
        if st is not None and tt is not None:
            return tt - st
        sd = self._server_now_dt()
        if sd is None:
            return None
        return (adjusted_send_dt - sd).total_seconds() * 1000

    def _dispatch_parse_time_str(self, time_str):
        """Tablo zaman formatını datetime'a çevir.
        Format: "20.03'de 20:45:24:208" — ms 1–3 hane; ' veya Unicode apostrophe.
        """
        try:
            ts = (time_str or "").strip()
            if not ts:
                return None
            m = re.match(
                r"(\d{1,2})\.(\d{1,2})['\u2019]de (\d{1,2}):(\d{2}):(\d{2}):(\d{1,3})\s*$",
                ts,
            )
            if not m:
                return None
            day = int(m.group(1))
            month = int(m.group(2))
            hour = int(m.group(3))
            minute = int(m.group(4))
            second = int(m.group(5))
            ms = int((m.group(6) or "0")[:3].zfill(3))

            # Yılı sunucu tarihinden al
            year = datetime.datetime.now().year
            if self._server_time_text:
                ym = re.search(r'(\d{4})', self._server_time_text)
                if ym:
                    year = int(ym.group(1))

            return datetime.datetime(year, month, day, hour, minute, second, ms * 1000)
        except (ValueError, OverflowError):
            return None

    def _dispatch_precache(self, item, row_idx):
        """Gönderimden 5sn önce rally point token'larını cache'le."""
        src_text = item.text(0)
        src_match = re.search(r'\((\d+)\|(\d+)\)', src_text)
        village_id = None
        if src_match:
            src_x, src_y = int(src_match.group(1)), int(src_match.group(2))
            all_v = self._game_data.get("all_villages", [])
            for v in all_v:
                if v.get("x") == src_x and v.get("y") == src_y:
                    village_id = v.get("id")
                    break
            if village_id is None:
                v = self._game_data.get("village", {})
                if v.get("x") == src_x and v.get("y") == src_y:
                    village_id = v.get("id")
        if village_id is None:
            village_id = self._game_data.get("village", {}).get("id", "")

        cache_key = f"cache_{row_idx}_{id(item)}"

        cache_js = f"""
        (function() {{
            if (!window.__tw_bot_cache) window.__tw_bot_cache = {{}};
            fetch('/game.php?village={village_id}&screen=place', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var form = doc.getElementById('command-data-form')
                    || doc.querySelector('form#command-data-form')
                    || doc.querySelector('form[action*="try=confirm"]')
                    || doc.querySelector('form[action*="screen=place"]');
                if (form) {{
                    var data = {{}};
                    form.querySelectorAll('input[type="hidden"]').forEach(function(h) {{
                        if (h.name) data[h.name] = h.value;
                    }});
                    data.__form_action = form.getAttribute('action');
                    window.__tw_bot_cache['{cache_key}'] = JSON.stringify(data);
                }}
            }});
        }})();
        """

        self.browser.page().runJavaScript(cache_js)
        item.setData(0, Qt.UserRole, "cached")
        item.setData(1, Qt.UserRole, cache_key)  # cache key'i sakla
        self._add_log("GÖNDERİM", "info", f"Token cache'leniyor: {src_text} (5sn kala)")

    def _dispatch_preconfirm(self, item, row_idx):
        """Gönderimden ~2sn önce try=confirm POST'unu yap, ch token'ı cache'le.
        Ardından JS tarafında setTimeout ile tam gönderim anında tek POST ateşle.
        Python IPC gecikmesi sıfırlanır — sadece polling kalır."""
        src_text = item.text(0)
        tgt_text = item.text(1)
        cmd_type = item.text(14)

        src_match = re.search(r'\((\d+)\|(\d+)\)', src_text)
        village_id = None
        if src_match:
            src_x, src_y = int(src_match.group(1)), int(src_match.group(2))
            all_v = self._game_data.get("all_villages", [])
            for v in all_v:
                if v.get("x") == src_x and v.get("y") == src_y:
                    village_id = v.get("id")
                    break
            if village_id is None:
                v = self._game_data.get("village", {})
                if v.get("x") == src_x and v.get("y") == src_y:
                    village_id = v.get("id")
        if village_id is None:
            village_id = self._game_data.get("village", {}).get("id", "")

        tgt_match = re.search(r'(\d+)\|(\d+)', tgt_text)
        if not tgt_match:
            item.setData(0, Qt.UserRole, "cached")
            return
        target_x, target_y = tgt_match.group(1), tgt_match.group(2)

        troops_js_parts = []
        for col_idx, (key, _) in enumerate(self.SA_UNIT_DEFS):
            val = item.text(col_idx + 2)
            if val and val != "0":
                troops_js_parts.append(f"'{key}': '{val}'")
        troops_js_obj = "{" + ", ".join(troops_js_parts) + "}"

        attack_type = "attack" if cmd_type == "Sld" else "support"
        cache_key = item.data(1, Qt.UserRole) or ""
        cmd_id = f"cmd_{row_idx}_{id(item)}"

        # Gönderime kalan süreyi hesapla (setTimeout için göreli gecikme)
        send_str = item.text(15)
        send_dt = self._dispatch_parse_time_str(send_str)
        offset_ms = self.sa_offset_input.value() if hasattr(self, 'sa_offset_input') else 0
        if send_dt is None:
            item.setData(0, Qt.UserRole, "cached")
            return
        adjusted_send_dt = send_dt + datetime.timedelta(milliseconds=offset_ms)

        batch_indices = self._dispatch_batch_indices_from(row_idx)
        extra_train_rows = self._dispatch_build_extra_train_rows(batch_indices)
        extra_train_json = json.dumps(extra_train_rows)

        # TW Timing ekseninde hedef ms + kalan süre (DOM/Python epoch ile ~200–400 ms sapma olmaz).
        tt = self._compute_target_timing_ms(adjusted_send_dt)
        st = self._server_now_timing_ms()
        if tt is not None and st is not None:
            target_timing_ms_js = int(round(tt))
            remaining_ms = int(round(tt - st))
        else:
            server_dt = self._server_now_dt()
            if server_dt is None:
                item.setData(0, Qt.UserRole, "cached")
                return
            target_timing_ms_js = 0
            remaining_ms = int((adjusted_send_dt - server_dt).total_seconds() * 1000)

        preconfirm_js = f"""
        (function() {{
            var injectWallMs = Date.now();
            var remainingMs = {remaining_ms};
            var targetTimingMs = {target_timing_ms_js};
            var cacheKey = '{cache_key}';
            var cmdId = '{cmd_id}';
            var villageId = {village_id};
            var targetX = '{target_x}';
            var targetY = '{target_y}';
            var troops = {troops_js_obj};
            var attackType = '{attack_type}';
            var extraTrainRows = {extra_train_json};

            if (!window.__tw_bot_results) window.__tw_bot_results = {{}};
            if (!window.__tw_bot_fire) window.__tw_bot_fire = {{}};

            function __twNormTwMs(v) {{
                if (v == null || isNaN(v)) return Date.now();
                v = Math.floor(Number(v));
                if (v > 0 && v < 1e12) v *= 1000;
                return v;
            }}
            function __twServerNowMs() {{
                if (typeof Timing !== 'undefined' && typeof Timing.getCurrentServerTime === 'function') {{
                    try {{ return __twNormTwMs(Timing.getCurrentServerTime()); }} catch (e) {{}}
                }}
                if (typeof Timing !== 'undefined' && Timing.initial_server_time && Timing.pagehit_at) {{
                    var t0 = Timing.initial_server_time;
                    if (t0 < 1e12) t0 *= 1000;
                    return Math.floor(t0 + (Date.now() - Timing.pagehit_at));
                }}
                if (typeof Timing !== 'undefined' && typeof Timing.offset_from_server !== 'undefined') {{
                    return Math.floor(Date.now() - Timing.offset_from_server);
                }}
                return Date.now();
            }}

            function __twFindConfirmForm(doc) {{
                var cf0 = doc.getElementById('command-data-form')
                    || doc.querySelector('form#command-data-form');
                if (cf0 && cf0.querySelector('[name="ch"]')) return cf0;
                var forms = doc.querySelectorAll('form');
                for (var fi = 0; fi < forms.length; fi++) {{
                    if (forms[fi].querySelector('[name="ch"]')) return forms[fi];
                }}
                return cf0
                    || doc.querySelector('form[action*="try=confirm"]')
                    || doc.querySelector('form[action*="screen=place"][action*="action=command"]')
                    || doc.querySelector('form[action*="screen=place"]');
            }}

            function __twExtractCh(cf, html) {{
                if (cf) {{
                    var els = cf.querySelectorAll('input[name="ch"], button[name="ch"]');
                    for (var ei = 0; ei < els.length; ei++) {{
                        var el = els[ei];
                        var vv = el.value || el.getAttribute('value') || '';
                        if (vv) return String(vv);
                    }}
                }}
                if (cf && cf.ownerDocument) {{
                    var gd = cf.ownerDocument.querySelector('#command-data-form input[name="ch"], form input[name="ch"], input[type="hidden"][name="ch"]');
                    if (gd) {{
                        var gv = gd.value || gd.getAttribute('value') || '';
                        if (gv) return String(gv);
                    }}
                }}
                if (typeof html === 'string' && html.length) {{
                    var patterns = [
                        /name=["']ch["'][^>]*value=["']([^"']*)["']/i,
                        /value=["']([^"']*)["'][^>]*name=["']ch["']/i,
                        /name=["']ch["'][^>]*value=([^\\s>"']+)/i,
                        /name=["']ch["'][\\s\\S]*?value=["']([^"']*)["']/i
                    ];
                    for (var pi = 0; pi < patterns.length; pi++) {{
                        var mm = html.match(patterns[pi]);
                        if (mm && mm[1] != null && String(mm[1]).length) return mm[1];
                    }}
                }}
                return '';
            }}
            function __twFormHasCh(cf, html) {{
                return !!__twExtractCh(cf, html);
            }}

            function __twSubmitConfirmValue(cf) {{
                var sb = cf.querySelector('input[type="submit"][name="submit_confirm"], button[type="submit"][name="submit_confirm"], input[name="submit_confirm"], button[name="submit_confirm"]');
                if (!sb) return 'true';
                if (sb.value != null && String(sb.value) !== '') return String(sb.value);
                var a = sb.getAttribute('value');
                return (a && String(a)) || 'true';
            }}
            function __twTrainDomSkip(inpName, extraTrainRows) {{
                extraTrainRows = extraTrainRows || [];
                if (!extraTrainRows.length) return false;
                var trainM = inpName.match(/^train\\[(\\d+)\\]\\[([^\\]]+)\\]$/);
                if (!trainM) return false;
                var tix = parseInt(trainM[1], 10);
                var ukey = trainM[2];
                if (tix < 1 || tix > extraTrainRows.length) return false;
                var erow = extraTrainRows[tix - 1];
                return !!(erow && Object.prototype.hasOwnProperty.call(erow, ukey));
            }}
            function __twAppendConfirmBody(cf, html, extraTrainRows) {{
                extraTrainRows = extraTrainRows || [];
                var cd = new URLSearchParams();
                cf.querySelectorAll('input[type="hidden"], input[name="ch"], input[type="submit"][name="ch"], button[name="ch"]').forEach(function(h) {{
                    if (h.name && h.name !== 'submit_confirm') cd.append(h.name, h.value || '');
                }});
                if (!cd.get('ch')) {{
                    var cv = __twExtractCh(cf, html);
                    if (cv) cd.append('ch', cv);
                }}
                var pu = cf.querySelector('#place_confirm_units');
                if (pu) {{
                    pu.querySelectorAll('input[type="number"][name], input[type="text"][name]').forEach(function(inp) {{
                        if (!inp.name || inp.name === 'submit_confirm') return;
                        if (__twTrainDomSkip(inp.name, extraTrainRows)) return;
                        cd.append(inp.name, inp.value != null ? String(inp.value) : '');
                    }});
                    pu.querySelectorAll('select[name]').forEach(function(sel) {{
                        if (!sel.name) return;
                        if (__twTrainDomSkip(sel.name, extraTrainRows)) return;
                        cd.append(sel.name, sel.value || '');
                    }});
                    pu.querySelectorAll('input[type="checkbox"][name]').forEach(function(cb) {{
                        if (!cb.name) return;
                        if (__twTrainDomSkip(cb.name, extraTrainRows)) return;
                        if (cb.checked) cd.append(cb.name, cb.value || '1');
                    }});
                }}
                for (var ti = 0; ti < extraTrainRows.length; ti++) {{
                    var row = extraTrainRows[ti];
                    if (!row || typeof row !== 'object') continue;
                    var tidx = ti + 1;
                    for (var uk in row) {{
                        if (!Object.prototype.hasOwnProperty.call(row, uk)) continue;
                        var nv = parseInt(row[uk], 10);
                        if (!isNaN(nv) && nv > 0)
                            cd.append('train[' + tidx + '][' + uk + ']', String(nv));
                    }}
                }}
                cd.append('submit_confirm', __twSubmitConfirmValue(cf));
                return cd.toString();
            }}

            function __twWaitCache(key, attempt, done) {{
                var c = window.__tw_bot_cache && window.__tw_bot_cache[key];
                if (c) {{ done(JSON.parse(c)); return; }}
                if (attempt >= 45) {{ done(null); return; }}
                setTimeout(function() {{ __twWaitCache(key, attempt + 1, done); }}, 25);
            }}

            __twWaitCache(cacheKey, 0, function(tokenData) {{
                if (!tokenData) return;

                delete window.__tw_bot_cache[cacheKey];

                var fd = new URLSearchParams();
                for (var key in tokenData) {{
                    if (key !== '__form_action') fd.append(key, tokenData[key]);
                }}
                for (var unit in troops) {{ fd.append(unit, troops[unit]); }}
                fd.set('x', targetX);
                fd.set('y', targetY);
                if (attackType === 'attack') {{ fd.append('attack', 'true'); }}
                else {{ fd.append('support', 'true'); }}

                var formAction = tokenData.__form_action || '/game.php?village=' + villageId + '&screen=place&try=confirm';

                fetch(formAction, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: fd.toString(),
                    credentials: 'same-origin'
                }})
                .then(function(r) {{ return r.text(); }})
                .then(function(confirmHtml) {{
                    if (!confirmHtml) return;

                    var doc2 = new DOMParser().parseFromString(confirmHtml, 'text/html');
                    var cf = __twFindConfirmForm(doc2);
                    if (!cf || !__twFormHasCh(cf, confirmHtml)) return;

                    var bodyStr = __twAppendConfirmBody(cf, confirmHtml, extraTrainRows);
                    var actionUrl = cf.getAttribute('action');
                    if (!actionUrl) return;

                    window.__tw_bot_fire[cmdId] = function() {{
                        window.__tw_bot_results[cmdId] = 'SENDING';
                        fetch(actionUrl, {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                            body: bodyStr,
                            credentials: 'same-origin'
                        }})
                        .then(function(r) {{ return r.text(); }})
                        .then(function() {{
                            window.__tw_bot_results[cmdId] = 'SENT_OK';
                        }})
                        .catch(function(e) {{
                            window.__tw_bot_results[cmdId] = 'ERROR|' + String(e);
                        }});
                    }};

                    function __twTryFire() {{
                        if (!window.__tw_bot_results[cmdId] ||
                            (window.__tw_bot_results[cmdId] !== 'SENT_OK' &&
                             window.__tw_bot_results[cmdId] !== 'SENDING')) {{
                            window.__tw_bot_fire[cmdId]();
                        }}
                    }}
                    function __twRemainMs() {{
                        if (targetTimingMs > 1e11) {{
                            return targetTimingMs - __twServerNowMs();
                        }}
                        return remainingMs - (Date.now() - injectWallMs);
                    }}
                    var delayRel = Math.max(0, __twRemainMs());
                    if (delayRel > 300000) {{
                        delayRel = Math.max(0, remainingMs - (Date.now() - injectWallMs));
                    }}
                    var fireDelay = Math.min(delayRel, 180000);
                    if (fireDelay <= 0) {{
                        setTimeout(__twTryFire, 0);
                    }} else {{
                        setTimeout(__twTryFire, fireDelay);
                    }}
                }})
                .catch(function(e) {{}});
            }});
        }})();
        """

        self.browser.page().runJavaScript(preconfirm_js)
        item.setData(0, Qt.UserRole, "confirmed")
        item.setData(2, Qt.UserRole, cmd_id)
        wave_note = f" — {len(batch_indices)} dalga tek formda" if len(batch_indices) > 1 else ""
        self._add_log("GÖNDERİM", "info", f"Onay formu + zamanlayıcı hazırlanıyor: {src_text}{wave_note} (~4.5sn pencere)")

    def _dispatch_send_command(self, item, row_idx):
        """Tek bir komutu AJAX ile gönder (sayfa değişmeden)."""

        # Tablo verilerini oku
        src_text = item.text(0)
        tgt_text = item.text(1)
        cmd_type = item.text(14)

        # Kaynak köy ID'sini bul
        src_match = re.search(r'\((\d+)\|(\d+)\)', src_text)
        village_id = None
        if src_match:
            src_x, src_y = int(src_match.group(1)), int(src_match.group(2))
            all_v = self._game_data.get("all_villages", [])
            for v in all_v:
                if v.get("x") == src_x and v.get("y") == src_y:
                    village_id = v.get("id")
                    break
            if village_id is None:
                v = self._game_data.get("village", {})
                if v.get("x") == src_x and v.get("y") == src_y:
                    village_id = v.get("id")

        if village_id is None:
            village_id = self._game_data.get("village", {}).get("id", "")

        # Hedef koordinatlar (sütunda önek olabilir — re.match değil search)
        tgt_match = re.search(r'(\d+)\|(\d+)', tgt_text)
        if not tgt_match:
            self._dispatch_mark_error(item, "Hedef koordinat hatalı")
            return
        target_x = tgt_match.group(1)
        target_y = tgt_match.group(2)
        if int(target_x) == 0 and int(target_y) == 0:
            self._dispatch_mark_error(item, "Hedef 0|0 geçersiz — hedef koordinatlarını kontrol edin")
            return

        if src_match:
            sx, sy = int(src_match.group(1)), int(src_match.group(2))
            if sx == int(target_x) and sy == int(target_y):
                self._dispatch_mark_error(
                    item,
                    "Kaynak ve hedef aynı köy — oyun bu komutta ch/onay formu üretmez; hedef koordinatını değiştirin.",
                )
                return

        # Asker sayıları
        unit_keys = [k for k, _ in self.SA_UNIT_DEFS]
        troops = {}
        for col_idx, key in enumerate(unit_keys):
            try:
                val = int(item.text(2 + col_idx))
                if val > 0:
                    troops[key] = val
            except ValueError:
                pass

        if not troops:
            self._dispatch_mark_error(item, "Asker yok")
            return

        csrf = self._game_data.get("csrf", "")
        attack_type = "attack" if cmd_type == "Sld" else "support"

        troops_js_parts = []
        for unit, count in troops.items():
            troops_js_parts.append(f"'{unit}': '{count}'")
        troops_js_obj = "{" + ", ".join(troops_js_parts) + "}"

        # Preconfirm'dan gelen cmd_id varsa kullan, yoksa yeni oluştur
        preconfirm_cmd_id = item.data(2, Qt.UserRole) or ""
        cmd_id = preconfirm_cmd_id or f"cmd_{row_idx}_{id(item)}"

        cache_key = item.data(1, Qt.UserRole) or ""

        batch_indices = self._dispatch_batch_indices_from(row_idx)
        extra_train_rows = self._dispatch_build_extra_train_rows(batch_indices)
        extra_train_json = json.dumps(extra_train_rows)
        batch_items = [
            self.sa_table.topLevelItem(j) for j in batch_indices
            if self.sa_table.topLevelItem(j)
        ]

        send_js = f"""
        (function() {{
            var villageId = {village_id};
            var targetX = '{target_x}';
            var targetY = '{target_y}';
            var troops = {troops_js_obj};
            var attackType = '{attack_type}';
            var cmdId = '{cmd_id}';
            var cacheKey = '{cache_key}';
            var extraTrainRows = {extra_train_json};

            function __twFindPlaceForm(doc) {{
                return doc.getElementById('command-data-form')
                    || doc.querySelector('form#command-data-form')
                    || doc.querySelector('form[action*="try=confirm"]')
                    || doc.querySelector('form[action*="screen=place"]');
            }}
            function __twFindConfirmForm(doc) {{
                var cf0 = doc.getElementById('command-data-form')
                    || doc.querySelector('form#command-data-form');
                if (cf0 && cf0.querySelector('[name="ch"]')) return cf0;
                var forms = doc.querySelectorAll('form');
                for (var fi = 0; fi < forms.length; fi++) {{
                    if (forms[fi].querySelector('[name="ch"]')) return forms[fi];
                }}
                return cf0
                    || doc.querySelector('form[action*="try=confirm"]')
                    || doc.querySelector('form[action*="screen=place"][action*="action=command"]')
                    || doc.querySelector('form[action*="screen=place"]');
            }}
            function __twExtractCh(cf, html) {{
                if (cf) {{
                    var els = cf.querySelectorAll('input[name="ch"], button[name="ch"]');
                    for (var ei = 0; ei < els.length; ei++) {{
                        var el = els[ei];
                        var vv = el.value || el.getAttribute('value') || '';
                        if (vv) return String(vv);
                    }}
                }}
                if (cf && cf.ownerDocument) {{
                    var gd = cf.ownerDocument.querySelector('#command-data-form input[name="ch"], form input[name="ch"], input[type="hidden"][name="ch"]');
                    if (gd) {{
                        var gv = gd.value || gd.getAttribute('value') || '';
                        if (gv) return String(gv);
                    }}
                }}
                if (typeof html === 'string' && html.length) {{
                    var patterns = [
                        /name=["']ch["'][^>]*value=["']([^"']*)["']/i,
                        /value=["']([^"']*)["'][^>]*name=["']ch["']/i,
                        /name=["']ch["'][^>]*value=([^\\s>"']+)/i,
                        /name=["']ch["'][\\s\\S]*?value=["']([^"']*)["']/i
                    ];
                    for (var pi = 0; pi < patterns.length; pi++) {{
                        var mm = html.match(patterns[pi]);
                        if (mm && mm[1] != null && String(mm[1]).length) return mm[1];
                    }}
                }}
                return '';
            }}
            function __twFormHasCh(cf, html) {{
                return !!__twExtractCh(cf, html);
            }}
            function __twSubmitConfirmValue(cf) {{
                var sb = cf.querySelector('input[type="submit"][name="submit_confirm"], button[type="submit"][name="submit_confirm"], input[name="submit_confirm"], button[name="submit_confirm"]');
                if (!sb) return 'true';
                if (sb.value != null && String(sb.value) !== '') return String(sb.value);
                var a = sb.getAttribute('value');
                return (a && String(a)) || 'true';
            }}
            function __twTrainDomSkip(inpName, extraTrainRows) {{
                extraTrainRows = extraTrainRows || [];
                if (!extraTrainRows.length) return false;
                var trainM = inpName.match(/^train\\[(\\d+)\\]\\[([^\\]]+)\\]$/);
                if (!trainM) return false;
                var tix = parseInt(trainM[1], 10);
                var ukey = trainM[2];
                if (tix < 1 || tix > extraTrainRows.length) return false;
                var erow = extraTrainRows[tix - 1];
                return !!(erow && Object.prototype.hasOwnProperty.call(erow, ukey));
            }}
            function __twAppendConfirmBody(cf, html, extraTrainRows) {{
                extraTrainRows = extraTrainRows || [];
                var cd = new URLSearchParams();
                cf.querySelectorAll('input[type="hidden"], input[name="ch"], input[type="submit"][name="ch"], button[name="ch"]').forEach(function(h) {{
                    if (h.name && h.name !== 'submit_confirm') cd.append(h.name, h.value || '');
                }});
                if (!cd.get('ch')) {{
                    var cv = __twExtractCh(cf, html);
                    if (cv) cd.append('ch', cv);
                }}
                var pu = cf.querySelector('#place_confirm_units');
                if (pu) {{
                    pu.querySelectorAll('input[type="number"][name], input[type="text"][name]').forEach(function(inp) {{
                        if (!inp.name || inp.name === 'submit_confirm') return;
                        if (__twTrainDomSkip(inp.name, extraTrainRows)) return;
                        cd.append(inp.name, inp.value != null ? String(inp.value) : '');
                    }});
                    pu.querySelectorAll('select[name]').forEach(function(sel) {{
                        if (!sel.name) return;
                        if (__twTrainDomSkip(sel.name, extraTrainRows)) return;
                        cd.append(sel.name, sel.value || '');
                    }});
                    pu.querySelectorAll('input[type="checkbox"][name]').forEach(function(cb) {{
                        if (!cb.name) return;
                        if (__twTrainDomSkip(cb.name, extraTrainRows)) return;
                        if (cb.checked) cd.append(cb.name, cb.value || '1');
                    }});
                }}
                for (var ti = 0; ti < extraTrainRows.length; ti++) {{
                    var row = extraTrainRows[ti];
                    if (!row || typeof row !== 'object') continue;
                    var tidx = ti + 1;
                    for (var uk in row) {{
                        if (!Object.prototype.hasOwnProperty.call(row, uk)) continue;
                        var nv = parseInt(row[uk], 10);
                        if (!isNaN(nv) && nv > 0)
                            cd.append('train[' + tidx + '][' + uk + ']', String(nv));
                    }}
                }}
                cd.append('submit_confirm', __twSubmitConfirmValue(cf));
                return cd.toString();
            }}

            if (!window.__tw_bot_results) window.__tw_bot_results = {{}};

            // Preconfirm fire fonksiyonu varsa → tek POST (hızlı yol)
            if (window.__tw_bot_fire && window.__tw_bot_fire[cmdId]) {{
                window.__tw_bot_fire[cmdId]();
                return 'DISPATCHED';
            }}

            // JS timer zaten ateşlediyse → sadece bekle
            var existing = window.__tw_bot_results[cmdId];
            if (existing === 'SENT_OK' || existing === 'SENDING') {{
                return 'DISPATCHED';
            }}

            window.__tw_bot_results[cmdId] = 'SENDING';

            // Preconfirm yoksa → 2 adımlı gönderim (güvenilir fallback)
            var getTokens;
            var cached = window.__tw_bot_cache && window.__tw_bot_cache[cacheKey];
            if (cached) {{
                getTokens = Promise.resolve(JSON.parse(cached));
            }} else {{
                getTokens = fetch('/game.php?village=' + villageId + '&screen=place', {{credentials: 'same-origin'}})
                .then(function(r) {{ return r.text(); }})
                .then(function(html) {{
                    var doc = new DOMParser().parseFromString(html, 'text/html');
                    var form = __twFindPlaceForm(doc);
                    if (!form) return null;
                    var data = {{}};
                    form.querySelectorAll('input[type="hidden"]').forEach(function(h) {{
                        if (h.name) data[h.name] = h.value;
                    }});
                    data.__form_action = form.getAttribute('action');
                    return data;
                }});
            }}

            getTokens
            .then(function(tokenData) {{
                if (!tokenData) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|Token alinamadi';
                    return;
                }}

                var fd = new URLSearchParams();
                for (var tk in tokenData) {{
                    if (tk !== '__form_action') fd.append(tk, tokenData[tk]);
                }}
                for (var unit in troops) {{ fd.append(unit, troops[unit]); }}
                fd.set('x', targetX);
                fd.set('y', targetY);
                if (attackType === 'attack') {{ fd.append('attack', 'true'); }}
                else {{ fd.append('support', 'true'); }}

                var formAction = tokenData.__form_action || '/game.php?village=' + villageId + '&screen=place&try=confirm';

                return fetch(formAction, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: fd.toString(),
                    credentials: 'same-origin'
                }});
            }})
            .then(function(r) {{ if (r) return r.text(); }})
            .then(function(confirmHtml) {{
                if (!confirmHtml) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|Onay sayfasi bos';
                    return;
                }}

                var doc2 = new DOMParser().parseFromString(confirmHtml, 'text/html');
                var cf = __twFindConfirmForm(doc2);
                if (!cf) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|Onay formu bulunamadi';
                    return;
                }}
                if (!__twFormHasCh(cf, confirmHtml)) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|ch token bulunamadi';
                    return;
                }}
                var bodyStr = __twAppendConfirmBody(cf, confirmHtml, extraTrainRows);
                var actionUrl = cf.getAttribute('action');
                if (!actionUrl) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|Form action yok';
                    return;
                }}

                return fetch(actionUrl, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: bodyStr,
                    credentials: 'same-origin'
                }});
            }})
            .then(function(r) {{ if (r) return r.text(); }})
            .then(function() {{
                if (window.__tw_bot_results[cmdId] && window.__tw_bot_results[cmdId].startsWith('ERROR')) return;
                window.__tw_bot_results[cmdId] = 'SENT_OK';
            }})
            .catch(function(err) {{
                window.__tw_bot_results[cmdId] = 'ERROR|' + String(err);
            }});
            return 'DISPATCHED';
        }})();
        """

        wave_tag = f" [{len(batch_items)} dalga]" if len(batch_items) > 1 else ""
        self._add_log("GÖNDERİM", "info",
            f"Komut gönderiliyor{wave_tag}: {src_text} → ({tgt_text}) | {cmd_type}")

        self.browser.page().runJavaScript(send_js)

        # Sonucu polling ile kontrol et
        self._dispatch_poll_result(item, cmd_id, src_text, tgt_text, cmd_type, 0, batch_items)

    def _dispatch_poll_result(
        self, item, cmd_id, src_text, tgt_text, cmd_type, attempt, batch_items=None
    ):
        """JS tarafındaki async fetch sonucunu polling ile kontrol et."""
        rows = batch_items if batch_items else [item]

        if attempt > 100:  # 100 × 200ms = 20sn timeout
            for it in rows:
                self._dispatch_mark_error(it, "Zaman aşımı")
            self._add_log("GÖNDERİM", "error",
                f"❌ Zaman aşımı: {src_text} → ({tgt_text})")
            return

        check_js = f"window.__tw_bot_results ? window.__tw_bot_results['{cmd_id}'] || 'WAITING' : 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str == "SENT_OK":
                for it in rows:
                    self._dispatch_mark_sent(it)
                batch_note = f" ({len(rows)} dalga)" if len(rows) > 1 else ""
                self._add_log("GÖNDERİM", "success",
                    f"✅ Komut gönderildi{batch_note}: {src_text} → ({tgt_text}) | {cmd_type}")
                # Temizle
                self.browser.page().runJavaScript(
                    f"if(window.__tw_bot_results) delete window.__tw_bot_results['{cmd_id}'];")

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                for it in rows:
                    self._dispatch_mark_error(it, error)
                self._add_log("GÖNDERİM", "error",
                    f"❌ Gönderim hatası: {src_text} → ({tgt_text}) | {error}")

            elif result_str in ("WAITING", "SENDING"):
                # Henüz bitmedi, 200ms sonra tekrar kontrol et
                QTimer.singleShot(200, lambda: self._dispatch_poll_result(
                    item, cmd_id, src_text, tgt_text, cmd_type, attempt + 1, batch_items))

        self.browser.page().runJavaScript(check_js, on_poll)

    def _dispatch_mark_sent(self, item):
        """Gönderilen satırı yeşile boyar."""
        for col in range(item.columnCount()):
            item.setBackground(col, QColor("#d4f0d4"))
            item.setForeground(col, QColor("#2a7a2a"))
        item.setData(0, Qt.UserRole, "sent")
        hook = getattr(self, "_hybrid_on_dispatch_sent", None)
        if callable(hook):
            try:
                hook(item)
            except Exception:
                pass

    def _dispatch_mark_error(self, item, error_msg):
        """Hata olan satırı kırmızıya boyar."""
        for col in range(item.columnCount()):
            item.setBackground(col, QColor("#f0d4d4"))
            item.setForeground(col, QColor("#aa3333"))
        item.setData(0, Qt.UserRole, "error")
        # Açıklama olarak hata mesajını ID sütununa yaz
        item.setText(18, f"HATA")
        hook = getattr(self, "_hybrid_on_dispatch_error", None)
        if callable(hook):
            try:
                hook(item, error_msg)
            except Exception:
                pass

    def _build_task_queue_tab(self):
        """Bina kuyruğu: köy seç → karargah bina listesi + Ekle; alttaki kuyruk kalıcı, oyunda 2 inşaat doluysa bekler."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self.BQ_BUILDINGS = [
            ("main", "Ana Bina"), ("barracks", "Kışla"), ("stable", "Ahır"),
            ("garage", "Atölye"), ("watchtower", "Gözetleme Kulesi"), ("snob", "Akademi"),
            ("smith", "Demirci"), ("place", "İçtima Meydanı"),
            ("statue", "Heykel"), ("market", "Pazar"),
            ("wood", "Oduncu"), ("stone", "Kil Ocağı"),
            ("iron", "Demir Madeni"), ("farm", "Çiftlik"),
            ("storage", "Depo"), ("hide", "Gizli Depo"), ("wall", "Sur"),
        ]

        top = QHBoxLayout()
        self.bq_enable_cb = QCheckBox("Otomatik bina yükseltme (kuyruk oyunu besler, max 2 inşaat kuralı JS’te)")
        self.bq_enable_cb.setStyleSheet("font-weight: bold; font-size: 11px;")
        top.addWidget(self.bq_enable_cb)
        top.addStretch()
        layout.addLayout(top)

        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("Köy:"))
        self.bq_village_combo = QComboBox()
        self.bq_village_combo.setMinimumWidth(240)
        self.bq_village_combo.addItem("— Köy Seçin —", None)
        vrow.addWidget(self.bq_village_combo, 1)
        self.bq_village_refresh_btn = QPushButton("Seviyeleri yenile")
        self.bq_village_refresh_btn.setToolTip("Seçili köy için karargah (screen=main) bina seviyelerini çeker")
        self.bq_village_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.bq_village_refresh_btn.clicked.connect(self._bq_on_village_refresh_click)
        vrow.addWidget(self.bq_village_refresh_btn)
        layout.addLayout(vrow)

        # ── Splitter: buildings table (top) | queue table (bottom) ──
        bq_splitter = QSplitter(Qt.Vertical)
        bq_splitter.setChildrenCollapsible(False)

        # ── Top: village buildings ────────────────────────────────────
        lv_group = QGroupBox("Köydeki binalar")
        lv_lay = QVBoxLayout(lv_group)
        lv_lay.setContentsMargins(4, 4, 4, 4)
        self.bq_levels_table = QTableWidget(0, 4)
        self.bq_levels_table.setHorizontalHeaderLabels(["", "Bina", "Seviye", ""])
        self.bq_levels_table.verticalHeader().setVisible(False)
        self.bq_levels_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bq_levels_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.bq_levels_table.setIconSize(QSize(24, 24))
        self.bq_levels_table.setShowGrid(False)
        self.bq_levels_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.bq_levels_table.setColumnWidth(0, 30)
        self.bq_levels_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.bq_levels_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.bq_levels_table.setColumnWidth(2, 68)
        self.bq_levels_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.bq_levels_table.setColumnWidth(3, 62)
        lv_lay.addWidget(self.bq_levels_table)
        bq_splitter.addWidget(lv_group)

        # ── Bottom: queue table + controls ───────────────────────────
        q_container = QWidget()
        q_container_lay = QVBoxLayout(q_container)
        q_container_lay.setContentsMargins(0, 0, 0, 0)
        q_container_lay.setSpacing(4)

        q_group = QGroupBox("Bina kuyruğu  (sıra: üstten alta — yeniden başlatınca silinmez)")
        q_lay = QVBoxLayout(q_group)
        q_lay.setContentsMargins(4, 4, 4, 4)
        self.bq_table = QTreeWidget()
        self.bq_table.setAlternatingRowColors(True)
        self.bq_table.setRootIsDecorated(False)
        self.bq_table.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.bq_table.setHeaderLabels(
            ["#", "Köy", "Bina", "Hedef seviye", "Mevcut", "Durum"]
        )
        self.bq_table.setColumnCount(6)
        for i, w in enumerate([36, 180, 140, 80, 64, 250]):
            self.bq_table.setColumnWidth(i, w)
        self.bq_table.header().setDefaultAlignment(Qt.AlignCenter)
        q_lay.addWidget(self.bq_table, 1)
        q_container_lay.addWidget(q_group, 1)

        self.bq_status_label = QLabel("Kuyruk: 0 emir")
        self.bq_status_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #333;")
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addWidget(self.bq_status_label)
        bottom.addStretch()
        bq_up_btn = QPushButton("Yukarı")
        bq_up_btn.setCursor(Qt.PointingHandCursor)
        bq_up_btn.clicked.connect(self._bq_move_up)
        bottom.addWidget(bq_up_btn)
        bq_down_btn = QPushButton("Aşağı")
        bq_down_btn.setCursor(Qt.PointingHandCursor)
        bq_down_btn.clicked.connect(self._bq_move_down)
        bottom.addWidget(bq_down_btn)
        bq_del_btn = QPushButton("Seçileni sil")
        bq_del_btn.setCursor(Qt.PointingHandCursor)
        bq_del_btn.clicked.connect(self._bq_delete_selected)
        bottom.addWidget(bq_del_btn)
        bq_clear_btn = QPushButton("Tümünü temizle")
        bq_clear_btn.setCursor(Qt.PointingHandCursor)
        bq_clear_btn.clicked.connect(self._bq_clear_queue)
        bottom.addWidget(bq_clear_btn)
        q_container_lay.addLayout(bottom)

        self.bq_flow_hint = QLabel(
            "Her «Ekle» tıkı seçili köyde o bina için <b>sonraki seviyeyi</b> (mevcut+1) hedefler. "
            "Önce «Seviyeleri yenile» ile listeyi doldurun."
        )
        self.bq_flow_hint.setWordWrap(True)
        self.bq_flow_hint.setTextFormat(Qt.RichText)
        self.bq_flow_hint.setStyleSheet("font-size: 10px; color: #777;")
        q_container_lay.addWidget(self.bq_flow_hint)

        bq_splitter.addWidget(q_container)

        # Top gets ~55%, bottom ~45% of the available space
        bq_splitter.setStretchFactor(0, 55)
        bq_splitter.setStretchFactor(1, 45)
        bq_splitter.setSizes([380, 300])
        layout.addWidget(bq_splitter, 1)

        self.tabs.addTab(tab, "Bina kuyruğu")

        self._bq_processing = False
        self._bq_current_levels = {}
        self._bq_levels_cache = {}
        self._bq_levels_fetch_vid = ""
        self._bq_fetch_done_cb = None
        # id(item) -> unblock timestamp; prevents skipping while waiting for resources/queue
        self._bq_blocked_until = {}
        # Building images: bkey -> url (from game page), bkey -> QPixmap (downloaded)
        self._bq_building_imgs: dict = {}
        self._bq_building_pixmaps: dict = {}
        # Debounce timer — refreshes the levels table once after all images arrive
        self._bq_img_refresh_timer = QTimer(self)
        self._bq_img_refresh_timer.setSingleShot(True)
        self._bq_img_refresh_timer.setInterval(300)
        self._bq_img_refresh_timer.timeout.connect(self._bq_populate_levels_table)

        self.bq_village_combo.currentIndexChanged.connect(self._bq_on_village_changed)
        self.bq_enable_cb.toggled.connect(self._on_bq_enable_toggled)
        # Periyodik yedek (bina bekleme + tek atımlı uyanma birlikte)
        self._bq_timer = QTimer(self)
        self._bq_timer.timeout.connect(self._bq_auto_process)
        self._bq_timer.start(90000)
        self._bq_next_wake = QTimer(self)
        self._bq_next_wake.setSingleShot(True)
        self._bq_next_wake.timeout.connect(self._bq_auto_process)
        # Countdown display for blocked items (every 15 s)
        self._bq_cd_timer = QTimer(self)
        self._bq_cd_timer.timeout.connect(self._bq_update_blocked_countdowns)
        self._bq_cd_timer.start(15000)

        QTimer.singleShot(0, self._bq_load_persisted_queue)

    def _bq_resolve_village_label(self, vid):
        """Köy ID için kısa gösterim metni (combo veya all_villages)."""
        vs = str(vid)
        for i in range(self.bq_village_combo.count()):
            if str(self.bq_village_combo.itemData(i) or "") == vs:
                return self.bq_village_combo.itemText(i)
        for v in self._game_data.get("all_villages") or []:
            if str(v.get("id", "")) == vs:
                coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                return f"{v.get('name', '?')} {coord}"
        return vs

    def _bq_on_village_changed(self, _idx):
        if not hasattr(self, "bq_levels_table"):
            return
        vid = self.bq_village_combo.currentData()
        if not vid:
            self.bq_levels_table.setRowCount(0)
            return
        if not self.browser:
            return

        def on_done(levels):
            if levels is not None:
                self._bq_populate_levels_table()

        self._add_log("BİNA", "info", f"Köy seçildi; bina seviyeleri yükleniyor… (id={vid})")
        self._bq_fetch_main_levels_impl(str(vid), on_done)

    def _bq_on_village_refresh_click(self):
        if not self.browser:
            self._add_log("BİNA", "warn", "Tarayıcı hazır değil.")
            return
        vid = self.bq_village_combo.currentData()
        if not vid:
            QMessageBox.information(self, "Bina kuyruğu", "Önce bir köy seçin.")
            return
        self._add_log("BİNA", "info", "Bina seviyeleri çekiliyor…")
        self._bq_fetch_main_levels_impl(
            str(vid), lambda levels: self._bq_populate_levels_table() if levels else None
        )

    def _on_bq_enable_toggled(self, checked: bool):
        """Ana «Başlat» olmadan da bina kuyruğu çalışsın; açılınca hemen dene."""
        if checked:
            QTimer.singleShot(400, self._bq_auto_process)

    def _bq_populate_levels_table(self):
        if not hasattr(self, "bq_levels_table"):
            return
        t = self.bq_levels_table
        t.setUpdatesEnabled(False)
        t.setRowCount(0)
        row_h = 26
        for bkey, bname in self.BQ_BUILDINGS:
            r = t.rowCount()
            t.insertRow(r)
            t.setRowHeight(r, row_h)

            # Col 0: building icon
            pm = self._bq_building_pixmaps.get(bkey)
            ico_item = QTableWidgetItem()
            ico_item.setFlags(Qt.ItemIsEnabled)
            if pm:
                ico_item.setIcon(QIcon(pm))
            else:
                ico_item.setText("…")
                ico_item.setTextAlignment(Qt.AlignCenter)
            t.setItem(r, 0, ico_item)

            # Col 1: building name
            name_item = QTableWidgetItem(bname)
            name_item.setFlags(Qt.ItemIsEnabled)
            name_item.setFont(QFont("", 9))
            t.setItem(r, 1, name_item)

            # Col 2: current level
            cur = self._bq_current_levels.get(bkey)
            slev = "—" if cur is None else str(cur)
            lv_item = QTableWidgetItem(slev)
            lv_item.setTextAlignment(Qt.AlignCenter)
            lv_item.setFlags(Qt.ItemIsEnabled)
            if cur is not None:
                lv_item.setFont(QFont("", 9, QFont.Bold))
            t.setItem(r, 2, lv_item)

            # Col 3: Add button
            btn = QPushButton("+ Ekle")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("font-size: 10px; padding: 3px 6px;")
            btn.clicked.connect(
                lambda _c, bk=bkey, bn=bname: self._bq_add_one_upgrade(bk, bn)
            )
            t.setCellWidget(r, 3, btn)

        t.setUpdatesEnabled(True)

    def _bq_persist_queue(self):
        out = []
        for i in range(self.bq_table.topLevelItemCount()):
            it = self.bq_table.topLevelItem(i)
            if not it:
                continue
            out.append(
                {
                    "vid": str(it.data(1, Qt.UserRole) or ""),
                    "vlabel": it.text(1),
                    "bname": it.text(2),
                    "bkey": (it.data(2, Qt.UserRole) or ""),
                    "target": it.text(3),
                    "mcur": it.text(4),
                    "st": it.text(5),
                }
            )
        self._settings.setValue("bina_kuyrugu/queue_v1", json.dumps(out, ensure_ascii=False))
        self._settings.sync()

    def _bq_load_persisted_queue(self):
        if not hasattr(self, "bq_table"):
            return
        raw = (self._settings.value("bina_kuyrugu/queue_v1", "") or "").strip()
        if not raw:
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        for ent in data:
            vid = ent.get("vid", "")
            bkey = ent.get("bkey", "")
            tgt = str(ent.get("target", "1"))
            bname = ent.get("bname", bkey)
            vlabel = ent.get("vlabel", "—")
            mcur = ent.get("mcur", "?")
            st0 = (ent.get("st") or "").strip()
            if st0.startswith("Bekliyor") or st0.startswith("⏳") or "Yükseltildi" in st0 or st0 == "":
                st = st0 if st0 and not st0.startswith("Bekliyor (restored") else "Bekliyor (diskten)"
            else:
                st = st0 or "Bekliyor (diskten)"
            n = self.bq_table.topLevelItemCount() + 1
            row = QTreeWidgetItem([str(n), vlabel, bname, tgt, mcur, st])
            row.setData(1, Qt.UserRole, vid)
            row.setData(2, Qt.UserRole, bkey)
            for c in (0, 3, 4):
                row.setTextAlignment(c, Qt.AlignCenter)
            self.bq_table.addTopLevelItem(row)
        self._bq_renumber()
        self._bq_update_status()
        if self.bq_table.topLevelItemCount():
            self._add_log("BİNA", "info", f"Kuyruk diskten yüklendi: {self.bq_table.topLevelItemCount()} emir")

    def _bq_pending_max_target_for_building(self, village_id, bkey) -> int:
        """Aynı köy + bina için kuyrukta (tamamlanmamış) en yüksek hedef seviye; yoksa 0."""
        vs = str(village_id)
        m = 0
        bkey = str(bkey) if bkey is not None else ""
        for i in range(self.bq_table.topLevelItemCount()):
            it = self.bq_table.topLevelItem(i)
            if not it:
                continue
            if str(it.data(1, Qt.UserRole) or "") != vs:
                continue
            if str(it.data(2, Qt.UserRole) or "") != bkey:
                continue
            st = it.text(5) or ""
            if "Tamamlandı" in st:
                continue
            try:
                t = int((it.text(3) or "0").strip())
            except (TypeError, ValueError):
                continue
            if t > m:
                m = t
        return m

    def _bq_add_one_upgrade(self, bkey, bname):
        """Seçili köyde sıradaki hedefi kuyruğa ekle: oyun seviyesi+1 veya kuyruktaki son hedef+1."""
        vid = self.bq_village_combo.currentData()
        if not vid:
            QMessageBox.information(self, "Bina kuyruğu", "Önce köy seçin.")
            return
        cur = self._bq_current_levels.get(bkey)
        if cur is None:
            self._add_log("BİNA", "warn", f"{bname} için seviye yok — «Seviyeleri yenile» deyin.")
            return
        try:
            c = int(cur)
        except (TypeError, ValueError):
            return
        pending_max = self._bq_pending_max_target_for_building(vid, bkey)
        if pending_max > 0:
            target_level = pending_max + 1
        else:
            target_level = c + 1
        from_level = target_level - 1
        if from_level >= 30:
            QMessageBox.information(
                self, "Bina kuyruğu", f"{bname} için üst sınır (30) — yeni adım eklenemez."
            )
            return
        vlabel = self.bq_village_combo.currentText() or "—"
        status = f"Bekliyor (kaynak seviye: {from_level})"
        n = self.bq_table.topLevelItemCount() + 1
        row = QTreeWidgetItem(
            [str(n), vlabel, bname, str(target_level), str(from_level), status]
        )
        row.setData(1, Qt.UserRole, str(vid))
        row.setData(2, Qt.UserRole, bkey)
        row.setTextAlignment(0, Qt.AlignCenter)
        row.setTextAlignment(3, Qt.AlignCenter)
        row.setTextAlignment(4, Qt.AlignCenter)
        self.bq_table.addTopLevelItem(row)
        self._bq_renumber()
        self._bq_update_status()
        self._bq_persist_queue()
        self._add_log("BİNA", "info", f"Kuyruğa: {bname} → {target_level} (köy: {vlabel})")
        if self.bq_enable_cb.isChecked():
            QTimer.singleShot(600, self._bq_auto_process)

    def _bq_clear_queue(self):
        self.bq_table.clear()
        self._bq_blocked_until.clear()
        self._bq_renumber()
        self._bq_update_status()
        self._bq_persist_queue()
        self._add_log("BİNA", "info", "Bina kuyruğu temizlendi.")

    def _bq_move_up(self):
        """Seçili satırı bir yukarı taşı."""
        items = self.bq_table.selectedItems()
        if not items:
            return
        item = items[0]
        idx = self.bq_table.indexOfTopLevelItem(item)
        if idx <= 0:
            return
        self.bq_table.takeTopLevelItem(idx)
        self.bq_table.insertTopLevelItem(idx - 1, item)
        self.bq_table.setCurrentItem(item)
        self._bq_renumber()
        self._bq_persist_queue()

    def _bq_move_down(self):
        """Seçili satırı bir aşağı taşı."""
        items = self.bq_table.selectedItems()
        if not items:
            return
        item = items[0]
        idx = self.bq_table.indexOfTopLevelItem(item)
        if idx >= self.bq_table.topLevelItemCount() - 1:
            return
        self.bq_table.takeTopLevelItem(idx)
        self.bq_table.insertTopLevelItem(idx + 1, item)
        self.bq_table.setCurrentItem(item)
        self._bq_renumber()
        self._bq_persist_queue()

    def _bq_delete_selected(self):
        for item in self.bq_table.selectedItems():
            self._bq_blocked_until.pop(id(item), None)
            idx = self.bq_table.indexOfTopLevelItem(item)
            if idx >= 0:
                self.bq_table.takeTopLevelItem(idx)
        self._bq_renumber()
        self._bq_update_status()
        self._bq_persist_queue()

    def _bq_renumber(self):
        """Satır numaralarını yeniden düzenle."""
        for i in range(self.bq_table.topLevelItemCount()):
            item = self.bq_table.topLevelItem(i)
            if item:
                item.setText(0, str(i + 1))

    def _bq_update_status(self):
        count = self.bq_table.topLevelItemCount()
        self.bq_status_label.setText(f"Kuyruk: {count} emir")

    # ══════════════════════════════════════════════════
    #  SEVİYE GÜNCELLEME (karargahtan çekme)
    # ══════════════════════════════════════════════════

    def _bq_fetch_main_levels_impl(self, village_id, on_done=None):
        """Karargah (screen=main) HTML'inden bina seviyelerini çeker; sonucu önbelleğe yazar."""
        if not self.browser:
            if callable(on_done):
                on_done(None)
            return
        vid = str(village_id or "")
        if not vid:
            if callable(on_done):
                on_done(None)
            return
        self._bq_levels_fetch_vid = vid
        self._bq_fetch_done_cb = on_done

        fetch_js = f"""
        (function() {{
            window.__tw_bq_levels = 'LOADING';
            fetch('/game.php?village={vid}&screen=main', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var levels = {{}};
                var imgs   = {{}};
                var rows = doc.querySelectorAll('[id^="main_buildrow_"]');
                rows.forEach(function(row) {{
                    var bkey = row.id.replace('main_buildrow_', '');
                    var firstTd = row.querySelector('td');
                    if (firstTd) {{
                        var txt = firstTd.textContent;
                        var m = txt.match(/Seviye\\s+(\\d+)/i) || txt.match(/Level\\s+(\\d+)/i);
                        if (m) levels[bkey] = parseInt(m[1]);
                        else levels[bkey] = 0;
                    }}
                    var img = row.querySelector('.bmain_list_img, img');
                    if (img) {{
                        var src = img.getAttribute('src') || '';
                        if (src) imgs[bkey] = src;
                    }}
                }});
                var scripts = doc.querySelectorAll('script');
                for (var i = 0; i < scripts.length; i++) {{
                    var txt = scripts[i].textContent;
                    if (txt.indexOf('updateGameData') > -1) {{
                        var m = txt.match(/"buildings":\\s*(\\{{[^}}]+\\}})/);
                        if (m) {{
                            try {{
                                var bld = JSON.parse(m[1].replace(/'/g, '"'));
                                for (var k in bld) {{
                                    levels[k] = parseInt(bld[k]) || 0;
                                }}
                            }} catch(e) {{}}
                        }}
                        break;
                    }}
                }}
                window.__tw_bq_levels = JSON.stringify({{status: 'OK', levels: levels, imgs: imgs}});
            }})
            .catch(function(err) {{
                window.__tw_bq_levels = JSON.stringify({{status: 'ERROR', message: String(err)}});
            }});
        }})();
        """
        self.browser.page().runJavaScript(fetch_js)
        self._bq_poll_levels(0)

    def _bq_refresh_levels(self):
        """Eski ad: yeni akışta «Seviyeleri yenile» ile aynı."""
        self._bq_on_village_refresh_click()

    def _bq_poll_levels(self, attempt):
        if attempt > 60:
            self._add_log("BİNA", "error", "Seviye çekme zaman aşımı.")
            cb = getattr(self, "_bq_fetch_done_cb", None)
            self._bq_fetch_done_cb = None
            if callable(cb):
                cb(None)
            return

        check_js = "window.__tw_bq_levels || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"
            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(200, lambda: self._bq_poll_levels(attempt + 1))
                return

            try:
                data = json.loads(result_str)
            except Exception:
                self._add_log("BİNA", "error", "Seviye verisi parse edilemedi.")
                cb = getattr(self, "_bq_fetch_done_cb", None)
                self._bq_fetch_done_cb = None
                if callable(cb):
                    cb(None)
                return

            if data.get("status") == "ERROR":
                self._add_log("BİNA", "error", f"Hata: {data.get('message', '?')}")
                cb = getattr(self, "_bq_fetch_done_cb", None)
                self._bq_fetch_done_cb = None
                if callable(cb):
                    cb(None)
                return

            levels = data.get("levels", {})
            self._bq_current_levels = levels
            vid = getattr(self, "_bq_levels_fetch_vid", "") or ""
            if vid:
                self._bq_levels_cache[vid] = dict(levels)
            self._add_log("BİNA", "success",
                f"✅ Bina seviyeleri güncellendi: {len(levels)} bina")

            # Fetch building images via game browser (PyInstaller uyumlu, QNetworkAccessManager yok)
            new_imgs = data.get("imgs", {})
            if new_imgs:
                self._bq_building_imgs.update(new_imgs)
                missing = {k: v for k, v in new_imgs.items()
                           if k not in self._bq_building_pixmaps}
                if missing:
                    self._bq_start_img_fetch(missing)

            cb = getattr(self, "_bq_fetch_done_cb", None)
            self._bq_fetch_done_cb = None
            if callable(cb):
                cb(levels)
            else:
                self._bq_update_table_statuses()
                if hasattr(self, "bq_levels_table") and str(
                    self.bq_village_combo.currentData() or ""
                ) == str(vid):
                    self._bq_populate_levels_table()

            self.browser.page().runJavaScript("window.__tw_bq_levels = null;")

        self.browser.page().runJavaScript(check_js, on_poll)

    def _bq_start_img_fetch(self, imgs_dict: dict):
        """Oyun browserı üzerinden bina görsellerini base64 olarak çek (PyInstaller uyumlu)."""
        if not self.browser or not imgs_dict:
            return
        try:
            import json as _j
            imgs_json = _j.dumps(imgs_dict)
            js = (
                "(function(){"
                "var imgs=" + imgs_json + ";"
                "var keys=Object.keys(imgs);"
                "var remaining=keys.length;"
                "window.__tw_bq_imgdata={status:'LOADING',data:{}};"
                "if(!remaining){window.__tw_bq_imgdata.status='DONE';return;}"
                "keys.forEach(function(key){"
                "  var url=imgs[key];"
                "  fetch(url,{method:'GET',credentials:'omit',mode:'cors'})"
                "  .then(function(r){return r.blob();})"
                "  .then(function(blob){"
                "    return new Promise(function(resolve){"
                "      var fr=new FileReader();"
                "      fr.onload=function(e){resolve(e.target.result);};"
                "      fr.onerror=function(){resolve(null);};"
                "      fr.readAsDataURL(blob);"
                "    });"
                "  })"
                "  .then(function(dataUrl){"
                "    if(dataUrl)window.__tw_bq_imgdata.data[key]=dataUrl;"
                "    remaining--;"
                "    if(remaining<=0)window.__tw_bq_imgdata.status='DONE';"
                "  })"
                "  .catch(function(){"
                "    remaining--;"
                "    if(remaining<=0)window.__tw_bq_imgdata.status='DONE';"
                "  });"
                "});"
                "})();"
            )
            self.browser.page().runJavaScript(js)
            self._bq_poll_imgs(0)
        except Exception:
            pass

    def _bq_poll_imgs(self, attempt: int):
        """JS'teki __tw_bq_imgdata bitmesini bekle, base64 → QPixmap dönüştür."""
        if not self.browser:
            return
        if attempt > 80:  # 16 sn max
            self.browser.page().runJavaScript("window.__tw_bq_imgdata = null;")
            return

        def on_result(val):
            try:
                if not val or val == "null":
                    QTimer.singleShot(200, lambda: self._bq_poll_imgs(attempt + 1))
                    return
                import json as _j, base64 as _b64
                data = _j.loads(val) if isinstance(val, str) else val
                if not isinstance(data, dict):
                    QTimer.singleShot(200, lambda: self._bq_poll_imgs(attempt + 1))
                    return
                if data.get("status") != "DONE":
                    QTimer.singleShot(200, lambda: self._bq_poll_imgs(attempt + 1))
                    return
                # Process completed images
                for bkey, data_url in (data.get("data") or {}).items():
                    try:
                        _, b64 = data_url.split(",", 1)
                        raw = _b64.b64decode(b64)
                        pm = QPixmap()
                        pm.loadFromData(raw)
                        if not pm.isNull():
                            self._bq_building_pixmaps[bkey] = pm.scaled(
                                24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation
                            )
                    except Exception:
                        pass
                if self._bq_building_pixmaps and hasattr(self, "_bq_img_refresh_timer"):
                    self._bq_img_refresh_timer.start()
                self.browser.page().runJavaScript("window.__tw_bq_imgdata = null;")
            except Exception:
                pass

        self.browser.page().runJavaScript(
            "JSON.stringify(window.__tw_bq_imgdata) || null;", on_result
        )

    def _bq_update_table_statuses(self):
        """Tablodaki tüm satırların durumlarını mevcut seviyelere göre güncelle."""
        combo_vid = str(self._bq_get_active_village_id() or "")
        for i in range(self.bq_table.topLevelItemCount()):
            item = self.bq_table.topLevelItem(i)
            if not item:
                continue
            bkey = item.data(2, Qt.UserRole)
            row_vid = item.data(1, Qt.UserRole)
            if row_vid is None or str(row_vid).strip() == "":
                lv = self._bq_current_levels
            else:
                vs = str(row_vid)
                lv = self._bq_levels_cache.get(vs)
                if lv is None and vs == combo_vid:
                    lv = self._bq_current_levels
                elif lv is None:
                    lv = {}
            cur = lv.get(bkey, None)
            try:
                target = int(item.text(3))
            except ValueError:
                continue

            if cur is not None:
                import time as _time
                item.setText(4, str(cur))
                if cur >= target:
                    # Complete — also unblock
                    self._bq_blocked_until.pop(id(item), None)
                    item.setText(5, f"✅ Tamamlandı (mevcut: {cur})")
                    item.setForeground(5, QColor("#228822"))
                    for col in range(6):
                        item.setBackground(col, QColor("#e8f5e8"))
                elif self._bq_blocked_until.get(id(item), 0) > _time.time():
                    # Still blocked — don't overwrite the countdown status
                    pass
                else:
                    item.setText(5, f"Bekliyor (mevcut: {cur})")
                    item.setForeground(5, QColor("#333333"))
                    for col in range(6):
                        item.setBackground(col, QColor("#ffffff"))
        self._bq_persist_queue()

    # ══════════════════════════════════════════════════
    #  YARDIMCI
    # ══════════════════════════════════════════════════

    def _bq_get_active_village_id(self):
        """Bina kuyruğu için aktif köy ID'sini döndürür.
        Önce bq_village_combo'dan, yoksa game_data'dan alır."""
        vid = self.bq_village_combo.currentData()
        if vid:
            return vid
        return self._game_data.get("village", {}).get("id", "")

    # ══════════════════════════════════════════════════
    #  OTOMATİK YÜKSELTME (karargahtan — screen=main)
    # ══════════════════════════════════════════════════

    def _bq_schedule_build_wake(self, delay_ms: int) -> None:
        """İnşaat / kaynak beklemede: en erken işe dönüş (tek atımlı). Yedek: 90 sn _bq_timer."""
        if not hasattr(self, "_bq_next_wake"):
            return
        d = int(delay_ms)
        d = max(500, min(d, 900000))
        self._bq_next_wake.stop()
        self._bq_next_wake.setInterval(d)
        self._bq_next_wake.start()

    def _bq_update_blocked_countdowns(self):
        """15sn'de bir bloklu bina satırlarının geri sayımını güncelle."""
        import time as _tc
        now = _tc.time()
        for i in range(self.bq_table.topLevelItemCount()):
            item = self.bq_table.topLevelItem(i)
            if not item:
                continue
            key = id(item)
            unblock_at = self._bq_blocked_until.get(key, 0)
            if unblock_at <= now:
                if key in self._bq_blocked_until:
                    del self._bq_blocked_until[key]
                continue
            remain = int(unblock_at - now)
            mins = remain // 60
            secs = remain % 60
            status = item.text(5)
            kind = ("Hammadde yetersiz"
                    if ("Hammadde" in status or "yetersiz" in status or "Kaynak" in status)
                    else "Kuyruk dolu")
            item.setText(5, f"⏳ {kind} — beklemede ({mins:02d}:{secs:02d} sonra)")
            item.setForeground(5, QColor("#aa6600"))

    def _bq_auto_process(self):
        """Kuyruktaki ilk 'Bekliyor' görevi karargah sayfasından yükseltir.

        Akış:
        1. Tabloda ilk 'Bekliyor' satırını bul
        2. Karargah sayfasını çek (GET screen=main)
        3. Mevcut seviyeyi kontrol et
        4. Hedef seviyeye ulaşıldıysa → tamamlandı, sonrakine geç
        5. Ulaşılmadıysa → btn-build linkine tıkla (GET action=upgrade_building)
        6. Sonucu kontrol et
        """
        if not self.bq_enable_cb.isChecked():
            if hasattr(self, "_bq_next_wake"):
                self._bq_next_wake.stop()
            return
        if self._human_verification_required:
            return
        if self._bq_processing:
            return
        if not self.browser:
            return
        if hasattr(self, "_bq_next_wake"):
            self._bq_next_wake.stop()

        # İlk bekleyen görevi bul.
        # "Tamamlandı" ve "❌ Hata" olanları atla; BLOCKED/NO_RES/BUSY ise bekle — atla değil.
        import time as _t
        _now = _t.time()
        target_item = None
        target_idx = -1
        for i in range(self.bq_table.topLevelItemCount()):
            item = self.bq_table.topLevelItem(i)
            if not item:
                continue
            status = item.text(5)
            if "Tamamlandı" in status:
                continue
            if "❌" in status:
                continue
            # Check if this item is in a timed wait (resource shortage or full queue)
            unblock_at = self._bq_blocked_until.get(id(item), 0)
            if unblock_at > _now:
                # Still blocked — update countdown text and stop; don't try next item
                remain = int(unblock_at - _now)
                mins = remain // 60
                secs = remain % 60
                kind = ("Hammadde yetersiz" if ("Hammadde" in status or "yetersiz" in status or "Kaynak" in status)
                        else "Kuyruk dolu")
                item.setText(5, f"⏳ {kind} — beklemede ({mins:02d}:{secs:02d} sonra)")
                item.setForeground(5, QColor("#aa6600"))
                return
            # Unblock time passed — clear and proceed
            self._bq_blocked_until.pop(id(item), None)
            target_item = item
            target_idx = i
            break

        if target_item is None:
            return  # Yapılacak iş yok

        village_id = str(target_item.data(1, Qt.UserRole) or "").strip()
        if not village_id:
            village_id = str(self._bq_get_active_village_id() or "")
        if not village_id:
            return

        self._bq_processing = True
        building_key = target_item.data(2, Qt.UserRole)
        try:
            target_level = int(target_item.text(3))
        except ValueError:
            self._bq_processing = False
            return

        target_item.setText(5, "⏳ Kontrol ediliyor...")
        target_item.setForeground(5, QColor("#2d5a9e"))

        csrf = self._game_data.get("csrf", "")

        check_js = f"""
        (function() {{
            var villageId = '{village_id}';
            var buildingKey = '{building_key}';
            var targetLevel = {target_level};
            var csrf = '{csrf}';

            window.__tw_bq_result = 'CHECKING';

            fetch('/game.php?village=' + villageId + '&screen=main', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');

                // Mevcut seviyeyi al
                var row = doc.getElementById('main_buildrow_' + buildingKey);
                if (!row) {{
                    window.__tw_bq_result = 'ERROR|Bina bulunamadı: ' + buildingKey;
                    return;
                }}

                var currentLevel = 0;
                var firstTd = row.querySelector('td');
                if (firstTd) {{
                    var txt = firstTd.textContent;
                    var m = txt.match(/Seviye\\s+(\\d+)/i) || txt.match(/Level\\s+(\\d+)/i);
                    if (m) currentLevel = parseInt(m[1]);
                }}

                // Hedef seviyeye ulaştık mı?
                if (currentLevel >= targetLevel) {{
                    window.__tw_bq_result = 'DONE|' + currentLevel;
                    return;
                }}

                // İnşaat kuyruğu: aktif (data-endtime) + sıradaki (sortable_row) toplam sayı
                var buildqueue = doc.getElementById('buildqueue');
                var nowSec = Math.floor(Date.now() / 1000);
                var endTimes = [];
                if (buildqueue) {{
                    var allEnd = buildqueue.querySelectorAll('[data-endtime]');
                    for (var ai = 0; ai < allEnd.length; ai++) {{
                        var ex = parseInt(allEnd[ai].getAttribute('data-endtime'), 10);
                        if (ex > 0) endTimes.push(ex);
                    }}
                }}
                // Aktif (şu anda inşa edilen) + sıraya alınmış (sortable_row / buildorder_N)
                var activeBuilds  = endTimes.length;
                var sortableBuilds = buildqueue
                    ? buildqueue.querySelectorAll('tr.sortable_row, tr[id^="buildorder_"]').length
                    : 0;
                var totalInQueue = activeBuilds + sortableBuilds;
                var earliestEnd = 0;
                for (var ei = 0; ei < endTimes.length; ei++) {{
                    var t = endTimes[ei];
                    if (earliestEnd === 0 || t < earliestEnd) earliestEnd = t;
                }}
                var remainSec = 0;
                if (earliestEnd > nowSec) remainSec = earliestEnd - nowSec;
                // Oyunun kendi kuyruğunda (aktif + bekleyen) 2+ emir varsa bekle
                var maxQueue = 2;
                if (totalInQueue >= maxQueue) {{
                    if (remainSec < 1 && endTimes.length > 0) remainSec = 1;
                    window.__tw_bq_result = 'BUSY|' + currentLevel + '|' + totalInQueue + '|' + remainSec;
                    return;
                }}

                // ── Maliyet: her zaman parse et (yükseltme denemesinden önce kontrol için) ──
                var costWood = 0, costStone = 0, costIron = 0;
                var cwEl = row.querySelector('.cost_wood, [class*="cost_wood"]');
                var csEl = row.querySelector('.cost_stone, [class*="cost_stone"]');
                var ciEl = row.querySelector('.cost_iron, [class*="cost_iron"]');
                if (cwEl) costWood = parseInt(cwEl.textContent.replace(/\\D/g, '')) || 0;
                if (csEl) costStone = parseInt(csEl.textContent.replace(/\\D/g, '')) || 0;
                if (ciEl) costIron = parseInt(ciEl.textContent.replace(/\\D/g, '')) || 0;
                if (costWood === 0 && costStone === 0 && costIron === 0) {{
                    row.querySelectorAll('span.icon').forEach(function(sp) {{
                        var cls = sp.className || '';
                        var valTxt = sp.parentElement ? sp.parentElement.textContent.replace(/\\D/g, '') : '0';
                        var val = parseInt(valTxt) || 0;
                        if (cls.indexOf('wood') > -1)  costWood  = val;
                        else if (cls.indexOf('stone') > -1) costStone = val;
                        else if (cls.indexOf('iron')  > -1) costIron  = val;
                    }});
                }}

                // ── Mevcut kaynaklar: fetch edilen sayfadan oku ──
                var curWood = 0, curStone = 0, curIron = 0;
                var prodWood = 0, prodStone = 0, prodIron = 0;
                try {{
                    var wdEl = doc.getElementById('wood');
                    var stEl = doc.getElementById('stone');
                    var irEl = doc.getElementById('iron');
                    if (wdEl) curWood  = parseInt(wdEl.textContent.replace(/\\D/g, '')) || 0;
                    if (stEl) curStone = parseInt(stEl.textContent.replace(/\\D/g, '')) || 0;
                    if (irEl) curIron  = parseInt(irEl.textContent.replace(/\\D/g, '')) || 0;
                    if (wdEl && wdEl.title) {{ var pm  = wdEl.title.match(/(\\d+)/); if (pm)  prodWood  = parseInt(pm[1])  || 0; }}
                    if (stEl && stEl.title) {{ var pm2 = stEl.title.match(/(\\d+)/); if (pm2) prodStone = parseInt(pm2[1]) || 0; }}
                    if (irEl && irEl.title) {{ var pm3 = irEl.title.match(/(\\d+)/); if (pm3) prodIron  = parseInt(pm3[1]) || 0; }}
                }} catch(e) {{}}

                // ── Hammadde yeterliliği: yükseltmeye GEÇMEDEN kontrol ──
                var hasCost = (costWood > 0 || costStone > 0 || costIron > 0);
                var resOk   = (curWood >= costWood && curStone >= costStone && curIron >= costIron);
                if (hasCost && !resOk) {{
                    window.__tw_bq_result = 'NO_RES|' + currentLevel +
                        '|' + costWood + '|' + costStone + '|' + costIron +
                        '|' + curWood  + '|' + curStone  + '|' + curIron +
                        '|' + prodWood + '|' + prodStone + '|' + prodIron;
                    return;
                }}

                // ── Yükseltme butonu ──
                var btn = row.querySelector('a.btn-build');
                var upgradeUrl = null;
                if (btn) upgradeUrl = btn.getAttribute('href');
                if (!upgradeUrl) {{
                    var optTd = row.querySelector('.build_options');
                    if (optTd) {{
                        var allLinks = optTd.querySelectorAll('a');
                        for (var i = 0; i < allLinks.length; i++) {{
                            var href = allLinks[i].getAttribute('href') || '';
                            if (href.indexOf('action=upgrade_building') > -1 && href.indexOf('cheap') === -1) {{
                                upgradeUrl = href;
                                break;
                            }}
                        }}
                    }}
                }}

                // Hammadde yeterli ama URL yoksa → ön koşul eksik veya max seviye
                if (!upgradeUrl) {{
                    window.__tw_bq_result = 'NO_RES|' + currentLevel +
                        '|' + costWood + '|' + costStone + '|' + costIron +
                        '|' + curWood  + '|' + curStone  + '|' + curIron +
                        '|' + prodWood + '|' + prodStone + '|' + prodIron;
                    return;
                }}

                window.__tw_bq_result = 'UPGRADING|' + currentLevel;

                return fetch(upgradeUrl, {{credentials: 'same-origin'}});
            }})
            .then(function(r) {{ if (r) return r.text(); }})
            .then(function(resultHtml) {{
                var cur = window.__tw_bq_result || '';
                if (cur.startsWith('ERROR') || cur.startsWith('DONE') ||
                    cur.startsWith('BUSY') || cur.startsWith('NO_RES')) return;

                if (resultHtml && resultHtml.indexOf('buildqueue') > -1) {{
                    var level = cur.replace('UPGRADING|', '');
                    window.__tw_bq_result = 'UPGRADED|' + level;
                }} else {{
                    // Yükseltme yanıtında buildqueue yoksa sunucu reddetti — NO_RES olarak işle
                    var level2 = cur.replace('UPGRADING|', '');
                    window.__tw_bq_result = 'NO_RES|' + level2 + '|0|0|0|0|0|0|0|0|0';
                }}
            }})
            .catch(function(err) {{
                window.__tw_bq_result = 'ERROR|' + String(err);
            }});
        }})();
        """

        self.browser.page().runJavaScript(check_js)
        self._bq_poll_result(target_item, target_idx, building_key, target_level, 0)

    def _bq_poll_result(self, item, row_idx, building_key, target_level, attempt):
        """Yükseltme sonucunu polling ile kontrol et."""

        def _bq_row_village_id(it):
            s = str(it.data(1, Qt.UserRole) or "").strip()
            return s or str(self._bq_get_active_village_id() or "")

        if attempt > 60:
            item.setText(5, "Zaman aşımı")
            item.setForeground(5, QColor("#cc2222"))
            self._bq_persist_queue()
            self._bq_processing = False
            return

        check_js = "window.__tw_bq_result || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str in ("WAITING", "CHECKING"):
                QTimer.singleShot(300, lambda: self._bq_poll_result(
                    item, row_idx, building_key, target_level, attempt + 1))
                return

            if result_str.startswith("UPGRADED|"):
                self._bq_blocked_until.pop(id(item), None)
                cur_level = result_str.split("|")[1]
                new_level = int(cur_level) + 1
                rv = _bq_row_village_id(item)
                if rv:
                    d = self._bq_levels_cache.setdefault(rv, {})
                    d[building_key] = new_level
                if rv == str(self._bq_get_active_village_id() or ""):
                    self._bq_current_levels[building_key] = new_level
                if (
                    hasattr(self, "bq_levels_table")
                    and rv == str(self.bq_village_combo.currentData() or "")
                ):
                    self._bq_populate_levels_table()
                item.setText(4, str(new_level))

                if new_level >= target_level:
                    item.setText(5, f"✅ Tamamlandı (mevcut: {new_level})")
                    item.setForeground(5, QColor("#228822"))
                    for col in range(item.columnCount()):
                        item.setBackground(col, QColor("#e8f5e8"))
                    self._add_log("BİNA", "success",
                        f"✅ {building_key} → Seviye {new_level} — hedef ulaşıldı")
                else:
                    item.setText(5, f"Yükseltildi → {new_level} (hedef: {target_level})")
                    item.setForeground(5, QColor("#2d5a9e"))
                    self._add_log("BİNA", "success",
                        f"✅ {building_key} → Seviye {new_level} (hedef: {target_level})")

                self._bq_persist_queue()
                self._bq_processing = False
                self._bq_schedule_build_wake(2000)

            elif result_str.startswith("DONE|"):
                self._bq_blocked_until.pop(id(item), None)
                cur_level = result_str.split("|")[1]
                il = int(cur_level)
                rv = _bq_row_village_id(item)
                if rv:
                    d = self._bq_levels_cache.setdefault(rv, {})
                    d[building_key] = il
                if rv == str(self._bq_get_active_village_id() or ""):
                    self._bq_current_levels[building_key] = il
                if (
                    hasattr(self, "bq_levels_table")
                    and rv == str(self.bq_village_combo.currentData() or "")
                ):
                    self._bq_populate_levels_table()
                item.setText(4, cur_level)
                item.setText(5, f"✅ Tamamlandı (mevcut: {cur_level})")
                item.setForeground(5, QColor("#228822"))
                for col in range(item.columnCount()):
                    item.setBackground(col, QColor("#e8f5e8"))
                self._add_log("BİNA", "info",
                    f"{building_key} zaten Seviye {cur_level} — hedef ulaşılmış")
                self._bq_persist_queue()
                self._bq_processing = False
                self._bq_schedule_build_wake(500)

            elif result_str.startswith("BUSY|"):
                import time as _t3
                parts = result_str.split("|")
                cur_level = parts[1] if len(parts) > 1 else "?"
                queue_count = parts[2] if len(parts) > 2 else "?"
                remain_sec = int(parts[3]) if len(parts) > 3 else 0

                item.setText(4, str(cur_level))

                if remain_sec > 0:
                    wait_sec = remain_sec + 2
                    self._bq_blocked_until[id(item)] = _t3.time() + wait_sec
                    mins = remain_sec // 60
                    secs = remain_sec % 60
                    item.setText(5,
                        f"⏳ Kuyruk dolu ({queue_count}) — beklemede ({mins:02d}:{secs:02d} sonra)")
                    item.setForeground(5, QColor("#aa6600"))
                    self._add_log("BİNA", "info",
                        f"İnşaat kuyruğu dolu ({queue_count}) — slot açılışına ~{mins}dk {secs}sn; "
                        f"o zamana kadar sıra atlanmayacak.")
                    self._bq_persist_queue()
                    self._bq_processing = False
                    self._bq_schedule_build_wake(wait_sec * 1000 + 1500)
                else:
                    wait_sec = 20
                    self._bq_blocked_until[id(item)] = _t3.time() + wait_sec
                    item.setText(5, f"⏳ Kuyruk dolu ({queue_count}) — beklemede (süre okunamadı)")
                    item.setForeground(5, QColor("#aa6600"))
                    self._bq_persist_queue()
                    self._bq_processing = False
                    self._add_log("BİNA", "info", "Kuyruk dolu; [data-endtime] yok — 20 sn sonra yine dene")
                    self._bq_schedule_build_wake(20000)

            elif result_str.startswith("NO_RES|"):
                import random as _rnd, time as _t2
                parts = result_str.split("|")
                cur_level = parts[1] if len(parts) > 1 else "?"
                item.setText(4, str(cur_level))

                # Parse deficit details for the log
                detail = ""
                if len(parts) >= 11:
                    try:
                        cost_w, cost_s, cost_i = int(parts[2]), int(parts[3]), int(parts[4])
                        cur_w, cur_s, cur_i   = int(parts[5]), int(parts[6]), int(parts[7])
                        prod_w, prod_s, prod_i = int(parts[8]), int(parts[9]), int(parts[10])
                        missing = []
                        for res_name, cost, cur, prod in [
                            ("Odun", cost_w, cur_w, prod_w),
                            ("Kil",  cost_s, cur_s, prod_s),
                            ("Demir",cost_i, cur_i, prod_i),
                        ]:
                            deficit = cost - cur
                            if deficit > 0:
                                eta = f"{int((deficit/prod)*3600)}sn" if prod > 0 else "üretim yok"
                                missing.append(f"{res_name}: {deficit:,} eksik ({eta})")
                        detail = " | ".join(missing)
                    except (ValueError, IndexError):
                        detail = "Maliyet verisi okunamadı"

                # ~10 dakika rastgele bekleme; sıra atlanmaz
                wait_sec = _rnd.randint(540, 660)
                self._bq_blocked_until[id(item)] = _t2.time() + wait_sec
                mins = wait_sec // 60
                secs = wait_sec % 60

                item.setText(5, f"⏳ Hammadde yetersiz — beklemede ({mins:02d}:{secs:02d} sonra)")
                item.setForeground(5, QColor("#aa6600"))

                log_detail = f" | {detail}" if detail else ""
                self._add_log("BİNA", "info",
                    f"Hammadde yetersiz: {building_key}{log_detail} — "
                    f"~10 dk sonra ({mins}dk {secs}sn) yeniden kontrol; sıra atlanmıyor")

                self._bq_persist_queue()
                self._bq_processing = False
                self._bq_schedule_build_wake(wait_sec * 1000 + 500)

            elif result_str.startswith("UPGRADING|"):
                QTimer.singleShot(300, lambda: self._bq_poll_result(
                    item, row_idx, building_key, target_level, attempt + 1))
                return

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                item.setText(5, f"❌ Hata: {error[:40]}")
                item.setForeground(5, QColor("#cc2222"))
                self._add_log("BİNA", "error", f"❌ {building_key}: {error}")
                self._bq_persist_queue()
                self._bq_processing = False

            else:
                QTimer.singleShot(300, lambda: self._bq_poll_result(
                    item, row_idx, building_key, target_level, attempt + 1))
                return

            self.browser.page().runJavaScript("window.__tw_bq_result = null;")

        self.browser.page().runJavaScript(check_js, on_poll)

    # ── HARİTA SEKMESİ ──────────────────────────

    def _build_map_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Üst kontrol çubuğu
        ctrl_frame = QFrame()
        self.map_ctrl_frame = ctrl_frame
        ctrl_frame.setObjectName("mapControlFrame")
        ctrl_frame.setStyleSheet(
            "QFrame#mapControlFrame { background: #faf6ec; border: 1px solid #d8c8a8;"
            "border-radius: 5px; padding: 2px; }")
        ctrl_outer = QVBoxLayout(ctrl_frame)
        ctrl_outer.setContentsMargins(6, 5, 6, 5)
        ctrl_outer.setSpacing(4)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        ctrl_row.addWidget(QLabel("Merkez X:"))
        self.map_center_x = QSpinBox()
        self.map_center_x.setRange(0, 999)
        self.map_center_x.setValue(500)
        self.map_center_x.setFixedWidth(58)
        self.map_center_x.setToolTip("Haritanın ortasındaki dünya X koordinatı")
        ctrl_row.addWidget(self.map_center_x)

        ctrl_row.addWidget(QLabel("Y:"))
        self.map_center_y = QSpinBox()
        self.map_center_y.setRange(0, 999)
        self.map_center_y.setValue(500)
        self.map_center_y.setFixedWidth(58)
        self.map_center_y.setToolTip("Haritanın ortasındaki dünya Y koordinatı")
        ctrl_row.addWidget(self.map_center_y)

        ctrl_row.addSpacing(10)
        self.map_show_barbs = QCheckBox("Barbar köyleri")
        self.map_show_barbs.setChecked(True)
        ctrl_row.addWidget(self.map_show_barbs)

        self.map_show_players = QCheckBox("Oyuncu köyleri")
        self.map_show_players.setChecked(True)
        ctrl_row.addWidget(self.map_show_players)

        ctrl_row.addSpacing(10)
        self.map_load_btn = QPushButton("🗺️ Haritayı Yükle")
        self.map_load_btn.setObjectName("startBtn")
        self.map_load_btn.setCursor(Qt.PointingHandCursor)
        self.map_load_btn.clicked.connect(self._map_load_data)
        ctrl_row.addWidget(self.map_load_btn)

        self.map_fetch_diplomacy_btn = QPushButton("Diplomasiyi yenile")
        self.map_fetch_diplomacy_btn.setToolTip(
            "Harita yüklendiğinde müttefik / SA / düşman klan ID’leri otomatik alınır.\n"
            "Oyunda diplomasiyi değiştirdiyseniz bu düğmeyle tekrar senkronize edin.")
        self.map_fetch_diplomacy_btn.setCursor(Qt.PointingHandCursor)
        self.map_fetch_diplomacy_btn.clicked.connect(self._map_fetch_diplomacy)
        ctrl_row.addWidget(self.map_fetch_diplomacy_btn)

        self.map_center_me_btn = QPushButton("📍 Köyüme Git")
        self.map_center_me_btn.setCursor(Qt.PointingHandCursor)
        self.map_center_me_btn.clicked.connect(self._map_center_on_me)
        ctrl_row.addWidget(self.map_center_me_btn)

        ctrl_row.addStretch()
        ctrl_outer.addLayout(ctrl_row)

        hint_lbl = QLabel(
            "Haritada varsayılan olarak en fazla 11×11 kare görünür; tekerlek ile yakınlaştırabilirsiniz. "
            "Kaydırmak için sürükleyin. Müttefik / SA / düşman renkleri harita yüklendiğinde "
            "Klan → Diplomasi sayfasından otomatik çekilir.")
        self.map_hint_lbl = hint_lbl
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet("font-size: 9px; color: #777; padding-left: 2px;")
        ctrl_outer.addWidget(hint_lbl)

        layout.addWidget(ctrl_frame)

        # ── Harita + Sağ Panel (Splitter) ──
        map_splitter = QSplitter(Qt.Horizontal)
        map_splitter.setChildrenCollapsible(False)
        map_splitter.setHandleWidth(5)

        # Sol: Harita widget (yükseklik + genişlik birlikte; zoom min() ile orantılı sığar)
        self.map_widget = MapCanvasWidget()
        self.map_widget.setMinimumHeight(22 + MapCanvasWidget.VISIBLE_TILES * MapCanvasWidget.TILE_H * 3 // 4)
        self.map_widget.setMinimumWidth(480)
        self.map_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.map_widget.village_double_clicked.connect(self._map_on_village_dblclick)
        self.map_widget.village_clicked.connect(self._map_on_village_click)
        self.map_widget.view_changed.connect(self._map_on_view_changed)
        map_splitter.addWidget(self.map_widget)

        # Sağ: Hedef Kuyruk Paneli
        queue_panel = QWidget()
        self.map_queue_panel = queue_panel
        queue_panel.setObjectName("mapQueuePanel")
        queue_panel.setStyleSheet(
            "QWidget#mapQueuePanel { background: #fdfaf4; border: 1px solid #d8c8a8;"
            "border-radius: 5px; }")
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(8, 6, 8, 6)
        queue_layout.setSpacing(6)

        q_title = QLabel("🎯 Hedef kuyruğu")
        self.map_q_title = q_title
        q_title.setStyleSheet(
            "font-weight: bold; font-size: 12px; color: #5a3e1b;"
            "padding: 6px; background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 4px;")
        q_title.setAlignment(Qt.AlignCenter)
        queue_layout.addWidget(q_title)

        q_hint = QLabel("Köye tek tık: kuyruğa ekle · Çift tık: köy sayfası")
        self.map_q_hint = q_hint
        q_hint.setStyleSheet("font-size: 9px; color: #666;")
        q_hint.setAlignment(Qt.AlignCenter)
        q_hint.setWordWrap(True)
        queue_layout.addWidget(q_hint)

        self.map_queue_list = QTreeWidget()
        self.map_queue_list.setHeaderLabels(["#", "Koordinat", "Köy Adı", "Puan", "Sahip"])
        self.map_queue_list.setRootIsDecorated(False)
        self.map_queue_list.setAlternatingRowColors(True)
        self.map_queue_list.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.map_queue_list.setColumnWidth(0, 30)
        self.map_queue_list.setColumnWidth(1, 70)
        self.map_queue_list.setColumnWidth(2, 90)
        self.map_queue_list.setColumnWidth(3, 50)
        self.map_queue_list.setColumnWidth(4, 70)
        self.map_queue_list.header().setStyleSheet(
            "QHeaderView::section { font-size: 9px; padding: 2px;"
            "background: #e8dcc8; border: 1px solid #c0b090; }")
        queue_layout.addWidget(self.map_queue_list, 1)

        self.map_queue_count_label = QLabel("Kuyruk: 0 hedef")
        self.map_queue_count_label.setStyleSheet("font-size: 10px; color: #555; font-weight: bold;")
        queue_layout.addWidget(self.map_queue_count_label)

        q_btn_row = QHBoxLayout()
        q_btn_row.setSpacing(4)

        btn_q_remove = QPushButton("Seçileni Sil")
        btn_q_remove.setCursor(Qt.PointingHandCursor)
        btn_q_remove.setStyleSheet("font-size: 10px;")
        btn_q_remove.clicked.connect(self._map_queue_remove_selected)
        q_btn_row.addWidget(btn_q_remove)

        btn_q_clear = QPushButton("Tümünü Temizle")
        btn_q_clear.setCursor(Qt.PointingHandCursor)
        btn_q_clear.setStyleSheet("font-size: 10px;")
        btn_q_clear.clicked.connect(self._map_queue_clear)
        q_btn_row.addWidget(btn_q_clear)

        queue_layout.addLayout(q_btn_row)

        # Gecikme ayarı
        delay_row = QHBoxLayout()
        delay_row.setSpacing(4)
        delay_lbl = QLabel("Komutlar arası:")
        self.map_delay_lbl = delay_lbl
        delay_lbl.setStyleSheet("font-size: 10px; color: #5a3e1b;")
        delay_row.addWidget(delay_lbl)
        self.map_queue_delay = QSpinBox()
        self.map_queue_delay.setRange(0, 300000)
        self.map_queue_delay.setValue(5000)
        self.map_queue_delay.setSuffix(" ms")
        self.map_queue_delay.setSingleStep(500)
        self.map_queue_delay.setFixedWidth(90)
        self.map_queue_delay.setToolTip("Her komut arasına eklenecek gecikme (milisaniye)")
        self.map_queue_delay.setStyleSheet("font-size: 10px;")
        delay_row.addWidget(self.map_queue_delay)
        delay_row.addStretch()
        queue_layout.addLayout(delay_row)

        # Ordu Gönder butonu
        self.map_send_army_btn = QPushButton("⚔️ Ordu Gönder")
        self.map_send_army_btn.setCursor(Qt.PointingHandCursor)
        self.map_send_army_btn.setMinimumHeight(36)
        self.map_send_army_btn.setStyleSheet(
            "background: qlineargradient(y1:0,y2:1,stop:0 #cc4444,stop:1 #992222);"
            "color: white; font-weight: bold; font-size: 12px;"
            "border: 1px solid #881111; border-radius: 4px; padding: 6px;")
        self.map_send_army_btn.clicked.connect(self._map_open_send_dialog)
        queue_layout.addWidget(self.map_send_army_btn)

        queue_panel.setMinimumWidth(272)

        # Barbar köyleri tablosu — sağ panelin üstüne gelecek
        barb_group = QGroupBox("Yakındaki barbar köyleri")
        barb_layout = QVBoxLayout()
        barb_layout.setContentsMargins(4, 4, 4, 4)
        self.map_barb_table = QTreeWidget()
        self.map_barb_table.setAlternatingRowColors(True)
        self.map_barb_table.setRootIsDecorated(False)
        self.map_barb_table.setHeaderLabels(["Koordinat", "Puan", "Mesafe", "Köy Adı", "Durum"])
        self.map_barb_table.header().setSectionResizeMode(QHeaderView.Stretch)
        barb_layout.addWidget(self.map_barb_table)
        barb_group.setLayout(barb_layout)

        # Sağ: dikey splitter (barbar üstte, kuyruk altta)
        right_vsplit = QSplitter(Qt.Vertical)
        right_vsplit.setChildrenCollapsible(False)
        right_vsplit.setHandleWidth(4)
        right_vsplit.addWidget(barb_group)
        right_vsplit.addWidget(queue_panel)
        right_vsplit.setStretchFactor(0, 1)
        right_vsplit.setStretchFactor(1, 2)
        right_vsplit.setMinimumWidth(272)

        map_splitter.addWidget(right_vsplit)

        map_splitter.setStretchFactor(0, 5)
        map_splitter.setStretchFactor(1, 1)
        layout.addWidget(map_splitter, 1)

        def _apply_map_split_sizes():
            sw = map_splitter.width()
            if sw < 500:
                map_splitter.setSizes([780, 300])
                return
            q = min(360, max(272, sw // 4))
            m = max(520, sw - q)
            map_splitter.setSizes([m, q])
            # dikey: barbar ~35%, kuyruk ~65%
            rh = right_vsplit.height()
            if rh > 100:
                right_vsplit.setSizes([max(120, rh * 35 // 100), max(200, rh * 65 // 100)])

        QTimer.singleShot(0, _apply_map_split_sizes)

        # Kuyruk verisi
        self._map_queue = []

        # Lejand
        legend_frame = QFrame()
        self.map_legend_frame = legend_frame
        legend_frame.setStyleSheet(
            "QFrame { background: #f7f2e8; border: 1px solid #e0d4c0; border-radius: 4px; padding: 4px; }")
        legend_row = QHBoxLayout(legend_frame)
        legend_row.setContentsMargins(6, 4, 6, 4)
        legend_items = [
            ("🟡 Senin köylerin", "#e8e832"),
            ("🔵 Klan", "#4488cc"),
            ("🔴 Düşman", "#cc2222"),
            ("⚫ Barbar", "#666666"),
            ("🟤 Diğer", "#FF6600"),
        ]
        for text, color in legend_items:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 10px; color: {color}; font-weight: bold; padding: 0 6px;")
            legend_row.addWidget(lbl)
        legend_row.addStretch()
        self.map_village_count_label = QLabel("Köy: 0")
        self.map_village_count_label.setStyleSheet("font-size: 10px; color: #555; font-weight: bold;")
        legend_row.addWidget(self.map_village_count_label)
        layout.addWidget(legend_frame)

        # ═══════════════════════════════════════════
        #  OTOMATİK FARM SİSTEMİ
        # ═══════════════════════════════════════════
        farm_group = QGroupBox("Otomatik Farm")
        farm_layout = QVBoxLayout()
        farm_layout.setSpacing(4)

        # Satır 1: Aktifleştir + Mesafe + Aralık
        farm_row1 = QHBoxLayout()
        farm_row1.setSpacing(6)

        self.farm_enable_cb = QCheckBox("Otomatik Farm Aktif")
        self.farm_enable_cb.setStyleSheet("font-weight: bold; font-size: 11px;")
        farm_row1.addWidget(self.farm_enable_cb)

        farm_row1.addSpacing(10)

        self.farm_start_btn = QPushButton("▶ Farm Başlat")
        self.farm_start_btn.setObjectName("startBtn")
        self.farm_start_btn.setCursor(Qt.PointingHandCursor)
        self.farm_start_btn.clicked.connect(self._farm_start)
        farm_row1.addWidget(self.farm_start_btn)

        self.farm_stop_btn = QPushButton("⏹ Farm Durdur")
        self.farm_stop_btn.setObjectName("stopBtn")
        self.farm_stop_btn.setCursor(Qt.PointingHandCursor)
        self.farm_stop_btn.setEnabled(False)
        self.farm_stop_btn.clicked.connect(self._farm_stop)
        farm_row1.addWidget(self.farm_stop_btn)

        farm_row1.addSpacing(15)
        farm_row1.addWidget(QLabel("Max mesafe:"))
        self.farm_max_dist = QSpinBox()
        self.farm_max_dist.setRange(1, 100)
        self.farm_max_dist.setValue(20)
        self.farm_max_dist.setFixedWidth(55)
        farm_row1.addWidget(self.farm_max_dist)

        farm_row1.addSpacing(10)
        farm_row1.addWidget(QLabel("Saldırı arası:"))
        self.farm_interval = QSpinBox()
        self.farm_interval.setRange(1, 300)
        self.farm_interval.setValue(5)
        self.farm_interval.setSuffix(" sn")
        self.farm_interval.setFixedWidth(70)
        farm_row1.addWidget(self.farm_interval)

        farm_row1.addStretch()
        self.farm_status_label = QLabel("Durum: Bekliyor")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #888;")
        farm_row1.addWidget(self.farm_status_label)

        farm_layout.addLayout(farm_row1)

        # Satır 2: Birim seçimi
        farm_row2 = QHBoxLayout()
        farm_row2.setSpacing(2)

        farm_row2.addWidget(QLabel("Her saldırıda gönderilecek:"))
        farm_row2.addSpacing(6)

        self.FARM_UNITS = [
            ("spear", "Mız"), ("sword", "Kıl"), ("axe", "Bal"), ("archer", "Okç"),
            ("spy", "Cas"), ("light", "HSv"), ("marcher", "AOk"), ("heavy", "ASv"),
            ("ram", "Koç"), ("catapult", "Man"),
        ]

        self.farm_troop_inputs = {}
        self._farm_short_labels = []
        for key, short in self.FARM_UNITS:
            # ── İkon + kısaltma çerçevesi ──
            unit_frame = QFrame()
            unit_frame.setStyleSheet("border: none;")
            uf_row = QVBoxLayout(unit_frame)
            uf_row.setContentsMargins(1, 0, 1, 0)
            uf_row.setSpacing(1)

            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setFixedHeight(16)
            icon_lbl.setStyleSheet("border: none;")
            troop_icon_mgr.apply_to_label(icon_lbl, key)
            uf_row.addWidget(icon_lbl)

            short_lbl = QLabel(short)
            short_lbl.setAlignment(Qt.AlignCenter)
            short_lbl.setStyleSheet("font-size: 9px; color: #555; border: none;")
            self._farm_short_labels.append(short_lbl)
            uf_row.addWidget(short_lbl)

            farm_row2.addWidget(unit_frame)

            spin = QSpinBox()
            spin.setRange(0, 99999)
            spin.setValue(0)
            spin.setFixedWidth(55)
            spin.setStyleSheet("font-size: 10px;")
            farm_row2.addWidget(spin)
            self.farm_troop_inputs[key] = spin

        farm_row2.addStretch()
        farm_layout.addLayout(farm_row2)

        # Satır 3: Hızlı şablonlar
        farm_row3 = QHBoxLayout()
        farm_row3.setSpacing(6)
        farm_row3.addWidget(QLabel("Şablon:"))

        btn_2hsv = QPushButton("2 Hafif")
        btn_2hsv.setCursor(Qt.PointingHandCursor)
        btn_2hsv.clicked.connect(lambda: self._farm_template({"light": 2}))
        farm_row3.addWidget(btn_2hsv)

        btn_3hsv = QPushButton("3 Hafif")
        btn_3hsv.setCursor(Qt.PointingHandCursor)
        btn_3hsv.clicked.connect(lambda: self._farm_template({"light": 3}))
        farm_row3.addWidget(btn_3hsv)

        btn_4hsv = QPushButton("4 Hafif")
        btn_4hsv.setCursor(Qt.PointingHandCursor)
        btn_4hsv.clicked.connect(lambda: self._farm_template({"light": 4}))
        farm_row3.addWidget(btn_4hsv)

        farm_row3.addStretch()

        self.farm_sent_label = QLabel("Gönderilen: 0 | Kalan: 0")
        self.farm_sent_label.setStyleSheet("font-size: 10px; color: #555;")
        farm_row3.addWidget(self.farm_sent_label)

        farm_layout.addLayout(farm_row3)

        # Satır 4: Kara liste kontrolleri
        farm_row4 = QHBoxLayout()
        farm_row4.setSpacing(6)

        self.farm_auto_blacklist_cb = QCheckBox("Kayıplı köyleri otomatik kara listeye al")
        self.farm_auto_blacklist_cb.setChecked(True)
        self.farm_auto_blacklist_cb.setStyleSheet("font-size: 10px;")
        farm_row4.addWidget(self.farm_auto_blacklist_cb)

        farm_row4.addSpacing(10)
        self.farm_check_reports_btn = QPushButton("Raporları Kontrol Et")
        self.farm_check_reports_btn.setCursor(Qt.PointingHandCursor)
        self.farm_check_reports_btn.clicked.connect(self._farm_check_reports)
        farm_row4.addWidget(self.farm_check_reports_btn)

        farm_row4.addSpacing(10)
        self.farm_blacklist_label = QLabel("Kara liste: 0 köy")
        self.farm_blacklist_label.setStyleSheet("font-size: 10px; color: #cc4444;")
        farm_row4.addWidget(self.farm_blacklist_label)

        farm_row4.addSpacing(10)
        self.farm_clear_bl_btn = QPushButton("Kara Listeyi Temizle")
        self.farm_clear_bl_btn.setCursor(Qt.PointingHandCursor)
        self.farm_clear_bl_btn.clicked.connect(self._farm_clear_blacklist)
        farm_row4.addWidget(self.farm_clear_bl_btn)

        farm_row4.addStretch()
        farm_layout.addLayout(farm_row4)

        # Satır 5: Tur arası bekleme ayarları
        farm_row5 = QHBoxLayout()
        farm_row5.setSpacing(6)

        farm_row5.addWidget(QLabel("Tur arası bekleme:"))
        self.farm_round_wait_mode = QComboBox()
        self.farm_round_wait_mode.addItems(["Sabit Süre", "En Yakın Dönüş"])
        self.farm_round_wait_mode.setFixedWidth(130)
        self.farm_round_wait_mode.setCurrentIndex(0)
        self.farm_round_wait_mode.currentIndexChanged.connect(self._farm_round_mode_changed)
        farm_row5.addWidget(self.farm_round_wait_mode)

        farm_row5.addSpacing(6)
        self.farm_round_wait_time = QSpinBox()
        self.farm_round_wait_time.setRange(10, 36000)
        self.farm_round_wait_time.setValue(300)
        self.farm_round_wait_time.setSuffix(" sn")
        self.farm_round_wait_time.setFixedWidth(90)
        farm_row5.addWidget(self.farm_round_wait_time)

        farm_row5.addStretch()
        self.farm_round_wait_label = QLabel("")
        self.farm_round_wait_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
        farm_row5.addWidget(self.farm_round_wait_label)

        farm_layout.addLayout(farm_row5)

        farm_group.setLayout(farm_layout)
        layout.addWidget(farm_group)

        # Farm durumu
        self._farm_timer = QTimer(self)
        self._farm_timer.timeout.connect(self._farm_process)
        self._farm_timer.start(1000)
        self._farm_last_send = 0
        self._farm_barb_index = 0
        self._farm_sent_count = 0
        self._farm_sending = False
        self._farm_blacklist = set()
        self._farm_return_times = []  # [(dönüş_timestamp, asker_sayısı), ...]
        self._farm_round_waiting = False
        self._farm_round_wait_until = 0
        self._farm_round_return_times = []
        # (x, y) -> beklenen_dönüş_timestamp; tablo yenilenince durumu korur
        self._farm_active_coords: dict = {}

        # Harita verisi
        self._map_villages = []
        self._map_data_loaded = False
        # screen=ally&mode=contracts (#partners) ile doldurulur
        self._map_diplomacy_partners = set()
        self._map_diplomacy_nap = set()
        self._map_diplomacy_enemies = set()

        self.tabs.addTab(tab, "🗺️ Harita")

    def _map_fetch_diplomacy(self):
        """Klan → Diplomasi (contracts) HTML'inden müttefik / SA / düşman klan ID'lerini çek."""
        if not self.browser:
            self._add_log("HARİTA", "warn", "Tarayıcı hazır değil.")
            return
        vid = int(self._game_data.get("village", {}).get("id", 0) or 0)
        if not vid:
            QMessageBox.warning(
                self,
                "Diplomasi",
                "Aktif köy ID bulunamadı. Gömülü tarayıcıda oyuna girip veriyi yenileyin.",
            )
            return
        self.map_fetch_diplomacy_btn.setEnabled(False)
        self.map_fetch_diplomacy_btn.setText("…")

        dip_js = """
        (function() {
            try {
                var vid = __VID__;
                var url = window.location.origin + '/game.php?village=' + vid + '&screen=ally&mode=contracts';
                var xhr = new XMLHttpRequest();
                xhr.open('GET', url, false);
                xhr.send(null);
                var html = xhr.responseText || '';
                var t0 = html.indexOf('id="partners"');
                if (t0 < 0) {
                    return JSON.stringify({status:'ERROR',
                        message:'partners tablosu yok (giriş / yetki / farklı dil şablonu)'});
                }
                var slice = html.substring(t0, Math.min(html.length, t0 + 250000));
                function collectIds(chunk) {
                    var ids = [];
                    var re = /screen=info_ally&amp;id=(\\d+)/g;
                    var m;
                    while ((m = re.exec(chunk)) !== null) ids.push(parseInt(m[1], 10));
                    if (ids.length === 0) {
                        re = /screen=info_ally&id=(\\d+)/g;
                        while ((m = re.exec(chunk)) !== null) ids.push(parseInt(m[1], 10));
                    }
                    return ids;
                }
                var p0 = slice.indexOf('Müttefikler');
                if (p0 < 0) p0 = slice.indexOf('Allies');
                var nap0 = slice.indexOf('Saldırmazlık');
                if (nap0 < 0) nap0 = slice.indexOf('Non-aggression');
                var en0 = slice.indexOf('Düşmanlar');
                if (en0 < 0) en0 = slice.indexOf('Enemies');
                var partners = [], nap = [], enemies = [];
                if (p0 >= 0 && nap0 > p0) {
                    partners = collectIds(slice.substring(p0, nap0));
                } else if (p0 >= 0 && nap0 < 0 && en0 > p0) {
                    partners = collectIds(slice.substring(p0, en0));
                }
                if (nap0 >= 0 && en0 > nap0) {
                    nap = collectIds(slice.substring(nap0, en0));
                } else if (nap0 >= 0 && en0 < 0) {
                    var tc = slice.indexOf('</table>', nap0);
                    nap = collectIds(slice.substring(nap0, tc > 0 ? tc : slice.length));
                }
                if (en0 >= 0) {
                    var endPos = slice.indexOf('</table>', en0);
                    if (endPos < 0) endPos = slice.length;
                    enemies = collectIds(slice.substring(en0, endPos));
                }
                return JSON.stringify({status:'OK', partners:partners, nap:nap, enemies:enemies});
            } catch (e) {
                return JSON.stringify({status:'ERROR', message:String(e)});
            }
        })();
        """.replace(
            "__VID__", str(int(vid))
        )

        def on_dip(result):
            self.map_fetch_diplomacy_btn.setEnabled(True)
            self.map_fetch_diplomacy_btn.setText("Diplomayı yenile")
            if not result:
                self._add_log("HARİTA", "warn", "Diplomasi yanıtı boş.")
                return
            try:
                data = json.loads(str(result))
            except Exception:
                self._add_log("HARİTA", "error", "Diplomasi JSON parse edilemedi.")
                return
            if data.get("status") != "OK":
                self._add_log(
                    "HARİTA",
                    "warn",
                    f"Diplomasi: {data.get('message', 'bilinmeyen hata')}",
                )
                return
            self._map_diplomacy_partners = set(int(x) for x in data.get("partners") or [])
            self._map_diplomacy_nap = set(int(x) for x in data.get("nap") or [])
            self._map_diplomacy_enemies = set(int(x) for x in data.get("enemies") or [])
            self._add_log(
                "HARİTA",
                "success",
                f"Diplomasi: {len(self._map_diplomacy_partners)} müttefik, "
                f"{len(self._map_diplomacy_nap)} SA, {len(self._map_diplomacy_enemies)} düşman klanı.",
            )
            if self._map_data_loaded:
                self._map_refresh()

        self.browser.page().runJavaScript(dip_js, on_dip)

    def _map_center_on_me(self):
        """Haritayı kendi köyümün koordinatlarına ortala."""
        v = self._game_data.get("village", {})
        if v:
            x = v.get("x", 500)
            y = v.get("y", 500)
            self.map_center_x.setValue(x)
            self.map_center_y.setValue(y)
            # Widget'ın merkezini de doğrudan güncelle
            self.map_widget.set_center(x, y)
            if self._map_data_loaded:
                self._map_refresh()
            self._add_log("HARİTA", "info", f"Harita köyüne ortalandı: ({x}|{y})")

    def _map_load_data(self):
        """village.txt ve player.txt'den tüm köy + oyuncu verilerini çek."""
        if not self.browser:
            self._add_log("HARİTA", "warn", "Tarayıcı hazır değil.")
            return

        self._add_log("HARİTA", "info", "Köy ve oyuncu verileri yükleniyor...")
        self.map_load_btn.setEnabled(False)
        self.map_load_btn.setText("Yükleniyor...")

        load_js = """
        (function() {
            window.__tw_map_data = 'LOADING';
            var baseUrl = window.location.origin;

            // village.txt ve player.txt'yi paralel çek
            Promise.all([
                fetch(baseUrl + '/map/village.txt', {credentials: 'same-origin'}).then(function(r) { return r.text(); }),
                fetch(baseUrl + '/map/player.txt', {credentials: 'same-origin'}).then(function(r) { return r.text(); })
            ])
            .then(function(results) {
                var villageTxt = results[0];
                var playerTxt = results[1];

                // Oyuncuları parse et — id,name,ally_id,villages,points,rank (ally_id = 3. alan)
                var players = {};
                var pLines = playerTxt.trim().split('\\n');
                for (var p = 0; p < pLines.length; p++) {
                    var pp = pLines[p].split(',');
                    if (pp.length >= 3) {
                        var pid = parseInt(pp[0]);
                        var pname = decodeURIComponent(pp[1].replace(/\\+/g, ' '));
                        var pAlly = parseInt(pp[2], 10);
                        if (isNaN(pAlly)) pAlly = 0;
                        players[pid] = { name: pname, ally_id: pAlly };
                    }
                }

                // Köyleri parse et
                var lines = villageTxt.trim().split('\\n');
                var villages = [];
                for (var i = 0; i < lines.length; i++) {
                    var parts = lines[i].split(',');
                    if (parts.length >= 7) {
                        var playerId = parseInt(parts[4]);
                        var pinf = players[playerId];
                        var pnm = (pinf && pinf.name) ? pinf.name : '';
                        var pAid = pinf ? pinf.ally_id : 0;
                        villages.push({
                            id: parseInt(parts[0]),
                            name: decodeURIComponent(parts[1].replace(/\\+/g, ' ')),
                            x: parseInt(parts[2]),
                            y: parseInt(parts[3]),
                            player_id: playerId,
                            player_name: pnm,
                            ally_id: pAid,
                            points: parseInt(parts[5]),
                            rank: parseInt(parts[6])
                        });
                    }
                }
                window.__tw_map_data = JSON.stringify({
                    status: 'OK',
                    count: villages.length,
                    player_count: Object.keys(players).length,
                    villages: villages,
                    graphics_base: (typeof TWMap !== 'undefined' && TWMap.graphics) ? TWMap.graphics : '',
                    image_base: (typeof image_base !== 'undefined') ? image_base : ''
                });
            })
            .catch(function(err) {
                window.__tw_map_data = JSON.stringify({status: 'ERROR', message: String(err)});
            });
        })();
        """

        self.browser.page().runJavaScript(load_js)
        self._map_poll_load(0)

    def _map_poll_load(self, attempt):
        """Harita verisi yüklenene kadar polling yap."""
        if attempt > 100:  # 100 × 200ms = 20sn
            self.map_load_btn.setEnabled(True)
            self.map_load_btn.setText("🗺️ Haritayı Yükle")
            self._add_log("HARİTA", "error", "Zaman aşımı.")
            return

        check_js = "window.__tw_map_data || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(200, lambda: self._map_poll_load(attempt + 1))
                return

            self.map_load_btn.setEnabled(True)
            self.map_load_btn.setText("🗺️ Haritayı Yükle")

            try:
                data = json.loads(result_str)
            except:
                self._add_log("HARİTA", "error", "Veri parse edilemedi.")
                return

            if data.get("status") == "ERROR":
                self._add_log("HARİTA", "error", f"Hata: {data.get('message', '?')}")
                return

            self._map_villages = data.get("villages", [])
            self._map_data_loaded = True
            player_count = data.get("player_count", 0)

            # Tile grafik URL'sini widget'a ilet
            gfx = data.get("graphics_base", "") or data.get("image_base", "")
            if gfx:
                self.map_widget.set_graphics_base(gfx + ("map_new/" if "map_new" not in gfx else ""))
                self._add_log("HARİTA", "success",
                    f"✅ {len(self._map_villages)} köy + {player_count} oyuncu yüklendi! | Tile: {gfx[:50]}...")
            else:
                self._add_log("HARİTA", "success",
                    f"✅ {len(self._map_villages)} köy + {player_count} oyuncu yüklendi!")

            self._map_refresh()
            self._map_fetch_diplomacy()

            # Temizle
            self.browser.page().runJavaScript("window.__tw_map_data = null;")

        self.browser.page().runJavaScript(check_js, on_poll)

    def _map_refresh(self):
        """Haritayı ve barbar tablosunu güncelle.
        Tüm köylere renk/tip atar ve widget'a gönderir.
        Widget kendi pan/zoom'una göre görünür olanları çizer.
        """
        if not self._map_data_loaded:
            return

        cx = self.map_center_x.value()
        cy = self.map_center_y.value()
        _r_spin = getattr(self, "farm_max_dist", None)
        radius = int(_r_spin.value()) if _r_spin is not None else 50
        show_barbs = self.map_show_barbs.isChecked()
        show_players = self.map_show_players.isChecked()

        player_id = int(self._game_data.get("player", {}).get("id", 0) or 0)
        raw_my_ally = self._game_data.get("player", {}).get("ally")
        try:
            my_ally_id = int(raw_my_ally) if raw_my_ally not in (None, "", False) else 0
        except (TypeError, ValueError):
            my_ally_id = 0
        if my_ally_id <= 0:
            my_ally_id = 0

        partner_ids = set(getattr(self, "_map_diplomacy_partners", set()) or set())
        nap_ids = getattr(self, "_map_diplomacy_nap", set()) or set()
        enemy_ids = getattr(self, "_map_diplomacy_enemies", set()) or set()

        # Harita renkleri: kendi köyler sarı; kendi klan koyu mavi; müttefik açık mavi; düşman kırmızı
        color_own_village = "#e8e832"   # sadece senin köylerin (klan renginden önce)
        color_tribe_blue = "#1a4a8c"    # aynı klandaki diğer üyeler
        color_partner_blue = "#7ec8f7"  # diplomasi müttefik klanlar (+ manuel ID)
        color_nap = "#5eb8c8"           # SA
        color_enemy = "#cc2222"
        color_other = "#dd8833"
        color_barb = "#666666"

        import math
        all_colored = []
        barb_list = []

        for v in self._map_villages:
            vx = v.get("x")
            vy = v.get("y")
            if vx is None or vy is None:
                continue

            pid = int(v.get("player_id", 0) or 0)
            is_barb = pid == 0

            if is_barb and not show_barbs:
                continue
            if not is_barb and not show_players:
                continue

            vill_ally = int(v.get("ally_id", 0) or 0)

            # Önce kendi köyler (sarı), sonra düşman, sonra klan / müttefik / SA
            if is_barb:
                color = color_barb
                vtype = "barb"
            elif pid == player_id:
                color = color_own_village
                vtype = "own"
            elif vill_ally > 0 and vill_ally in enemy_ids:
                color = color_enemy
                vtype = "enemy"
            elif my_ally_id > 0 and vill_ally == my_ally_id:
                color = color_tribe_blue
                vtype = "tribe"
            elif vill_ally > 0 and vill_ally in partner_ids:
                color = color_partner_blue
                vtype = "ally"
            elif vill_ally > 0 and vill_ally in nap_ids:
                color = color_nap
                vtype = "nap"
            else:
                color = color_other
                vtype = "other"

            all_colored.append({
                "id": v.get("id", 0),
                "x": vx, "y": vy,
                "color": color, "type": vtype,
                "name": v["name"], "points": v["points"],
                "player_id": pid,
                "player_name": v.get("player_name", ""),
            })

            # Barbar tablosu için mesafe hesapla (spinbox merkezine göre)
            if is_barb:
                dx = vx - cx
                dy = vy - cy
                dist = math.sqrt(dx * dx + dy * dy)
                if dist <= radius:
                    barb_list.append(v | {"dist": dist})

        # Widget'a TÜM köyleri ver — widget kendi viewport'una göre çizer
        self.map_widget.set_all_villages(all_colored)
        self.map_widget.set_data(all_colored, cx, cy, radius)
        self.map_village_count_label.setText(f"Köy: {len(all_colored)} (toplam)")

        # Barbar tablosunu güncelle (mesafeye göre sırala)
        import time as _brt
        barb_list.sort(key=lambda b: b["dist"])
        self.map_barb_table.clear()
        active = getattr(self, "_farm_active_coords", {})
        _now_brt = _brt.time()
        for b in barb_list[:100]:  # İlk 100
            coord = f"({b['x']}|{b['y']})"
            coord_key = (int(b["x"]), int(b["y"]))
            exp = active.get(coord_key, 0)
            if exp > _now_brt:
                status = "✓ Gönderildi"
            else:
                status = ""
                active.pop(coord_key, None)
            item = QTreeWidgetItem([coord, str(b["points"]), f"{b['dist']:.1f}", b["name"], status])
            item.setTextAlignment(1, Qt.AlignCenter)
            item.setTextAlignment(2, Qt.AlignCenter)
            item.setData(0, Qt.UserRole, {"x": b["x"], "y": b["y"]})
            if status:
                item.setForeground(4, QColor("#228822"))
            self.map_barb_table.addTopLevelItem(item)

        # Farm indexini sıfırla
        self._farm_barb_index = 0
        self._farm_sent_count = 0
        self._farm_update_labels()

    # ── HARİTA KUYRUK YÖNETİMİ ────────────────

    def _map_on_village_click(self, village):
        """Haritada köye tek tıklandığında kuyruğa ekle."""
        coord = f"({village['x']}|{village['y']})"
        for existing in self._map_queue:
            if existing["x"] == village["x"] and existing["y"] == village["y"]:
                self._add_log("HARİTA", "warn", f"Köy zaten kuyrukta: {coord}")
                return
        self._map_queue.append(village)
        self._map_queue_refresh()
        name = village.get("name", "?")
        self._add_log("HARİTA", "info", f"Kuyruğa eklendi: {name} {coord}")

    def _map_queue_refresh(self):
        """Kuyruk listesini güncelle."""
        self.map_queue_list.clear()
        for i, v in enumerate(self._map_queue):
            coord = f"({v['x']}|{v['y']})"
            name = v.get("name", "?")
            pts = str(v.get("points", "?"))
            pid = v.get("player_id", 0)
            pname = v.get("player_name", "")
            if pid and pname:
                owner = pname
            elif pid:
                owner = f"ID:{pid}"
            else:
                owner = "Barbar"
            item = QTreeWidgetItem([str(i + 1), coord, name, pts, owner])
            item.setTextAlignment(0, Qt.AlignCenter)
            item.setTextAlignment(1, Qt.AlignCenter)
            item.setTextAlignment(3, Qt.AlignCenter)
            self.map_queue_list.addTopLevelItem(item)
        self.map_queue_count_label.setText(f"Kuyruk: {len(self._map_queue)} hedef")

    def _map_queue_remove_selected(self):
        """Seçili hedefleri kuyruktan kaldır."""
        selected = self.map_queue_list.selectedItems()
        if not selected:
            return
        indices = sorted(
            [self.map_queue_list.indexOfTopLevelItem(item) for item in selected],
            reverse=True)
        for idx in indices:
            if 0 <= idx < len(self._map_queue):
                self._map_queue.pop(idx)
        self._map_queue_refresh()

    def _map_queue_clear(self):
        """Kuyruğu tamamen temizle."""
        self._map_queue.clear()
        self._map_queue_refresh()

    def _map_open_send_dialog(self):
        """Ordu gönder dialogunu aç. Sonuçları birebir _sa_add_task ile aynı mantıkla ekle."""
        if not self._map_queue:
            QMessageBox.warning(self, "Uyarı", "Kuyrukta hedef köy yok!\nHaritada köylere tıklayarak ekleyin.")
            return
        dlg = MapArmySendDialog(self, self._map_queue, self._game_data, self.SA_UNIT_DEFS,
                                self._server_time_text if self._server_time_synced else "")
        if dlg.exec_() == dlg.Accepted:
            results = dlg.get_results()
            if not results:
                return
            delay_ms = self.map_queue_delay.value()
            added = 0
            for idx, entry in enumerate(results):
                entry["_delay_offset_ms"] = idx * delay_ms
                self._sa_add_task_direct(entry)
                added += 1
            self._add_log("HARİTA", "success",
                f"{added} komut eklendi! (komutlar arası {delay_ms}ms gecikme)")

    def _sa_add_task_direct(self, entry):
        """_sa_add_task ile birebir aynı mantık — form yerine parametre alır.
        Zaman hesaplama, format, tabloya ekleme tamamen aynı kod."""
        import math

        src_text = entry["source"]
        tgt_x = entry["tgt_x"]
        tgt_y = entry["tgt_y"]
        tgt = f"{tgt_x}|{tgt_y}"

        troop_values = []
        has_troops = False
        troop_keys_sent = []
        for key, _ in self.SA_UNIT_DEFS:
            val = entry["troops"].get(key, 0)
            troop_values.append(str(val))
            if val > 0:
                has_troops = True
                troop_keys_sent.append(key)

        if not has_troops:
            return

        # Kaynak koordinatlarını bul
        src_match = re.search(r'\((\d+)\|(\d+)\)', src_text)
        if src_match:
            src_x, src_y = int(src_match.group(1)), int(src_match.group(2))
        else:
            src_x, src_y = self._sa_get_source_coords()
            if src_x is None:
                return

        # Yolculuk süresini hesapla
        distance = math.sqrt((tgt_x - src_x) ** 2 + (tgt_y - src_y) ** 2)
        travel_sec = self._sa_calc_travel_time(distance, troop_keys_sent)

        # Zaman parse
        time_mode = entry.get("time_mode")
        send_time_str = entry.get("send_time", "—")
        arrive_time_str = entry.get("arrive_time", "—")

        time_str_to_parse = None
        active_mode = None
        if time_mode == "send" and send_time_str != "—":
            time_str_to_parse = send_time_str
            active_mode = "send"
        elif time_mode == "arrive" and arrive_time_str != "—":
            time_str_to_parse = arrive_time_str
            active_mode = "arrive"
        elif send_time_str != "—":
            time_str_to_parse = send_time_str
            active_mode = "send"
        elif arrive_time_str != "—":
            time_str_to_parse = arrive_time_str
            active_mode = "arrive"

        if not time_str_to_parse:
            return

        # "GG.AA'de SS:DD:SS:ms" → date ve clock parçala
        m = re.match(r"(\d{1,2})\.(\d{1,2})'de (\d{1,2}):(\d{2}):(\d{2}):(\d{3})", time_str_to_parse)
        if m:
            date_str = f"{m.group(1)}.{m.group(2)}"
            clock_str = f"{m.group(3)}:{m.group(4)}:{m.group(5)}:{m.group(6)}"
        else:
            return

        input_dt = self._sa_parse_time_input(date_str, clock_str)
        if input_dt is None:
            return

        # Gecikme offset'i uygula (haritadan sıralı komutlar için, milisaniye)
        delay_offset_ms = entry.get("_delay_offset_ms", 0)
        if delay_offset_ms > 0:
            input_dt = input_dt + datetime.timedelta(milliseconds=delay_offset_ms)

        # Gönderim / Varış / Dönüş hesapla (birebir _sa_add_task ile aynı)
        travel_delta = datetime.timedelta(seconds=travel_sec)

        if active_mode == "send":
            send_dt = input_dt
            arrive_dt = send_dt + travel_delta
        else:
            arrive_dt = input_dt
            send_dt = arrive_dt - travel_delta

        return_dt = arrive_dt + travel_delta

        send_str = self._sa_format_time(send_dt)
        arrive_str = self._sa_format_time(arrive_dt)
        return_str = self._sa_format_time(return_dt, ms_zero=True)

        cmd_type = entry.get("cmd_type", "Sld")
        task_id = str(self.sa_table.topLevelItemCount() + 1)

        row_data = [src_text, tgt] + troop_values + [cmd_type, send_str, arrive_str, return_str, task_id]
        item = QTreeWidgetItem(row_data)

        for col in range(2, 14):
            item.setTextAlignment(col, Qt.AlignCenter)
            if troop_values[col - 2] != "0":
                item.setForeground(col, QColor("#2d5a9e"))
            else:
                item.setForeground(col, QColor("#ccc"))

        item.setTextAlignment(14, Qt.AlignCenter)
        for col in [15, 16, 17, 18]:
            item.setTextAlignment(col, Qt.AlignCenter)

        self.sa_table.addTopLevelItem(item)
        self._sa_update_totals()

        travel_min = travel_sec / 60
        self._add_log("KOMUT", "info",
            f"Komut eklendi: {cmd_type} {src_text} → ({tgt}) | "
            f"Mesafe: {distance:.2f} kare | Yolculuk: {travel_min:.1f}dk | "
            f"Gönderim: {send_str} | Varış: {arrive_str} | Dönüş: {return_str}")

    # ── HARİTA SİNYAL HANDLER'LARI ────────────────

    def _map_on_village_dblclick(self, village_id, vx, vy):
        """Haritada köye çift tıklandığında o köye git."""
        if village_id and self.browser:
            base_village = self._game_data.get("village", {}).get("id", "")
            url = f"/game.php?village={base_village}&screen=info_village&id={village_id}"
            self.browser.page().runJavaScript(
                f"window.location.href = '{url}';")
            self._add_log("HARİTA", "info",
                f"Köye gidiliyor: ({vx}|{vy}) — ID: {village_id}")
        else:
            # Sadece merkezi oraya taşı
            self.map_center_x.setValue(vx)
            self.map_center_y.setValue(vy)

    def _map_on_view_changed(self, cx, cy):
        """Harita widget'ından pan/zoom sonrası merkez spinbox'larını güncelle."""
        self.map_center_x.blockSignals(True)
        self.map_center_y.blockSignals(True)

        self.map_center_x.setValue(int(max(0, min(999, round(cx)))))
        self.map_center_y.setValue(int(max(0, min(999, round(cy)))))

        self.map_center_x.blockSignals(False)
        self.map_center_y.blockSignals(False)

    # ── OTOMATİK FARM SİSTEMİ ─────────────────

    def _farm_round_mode_changed(self, index):
        self.farm_round_wait_time.setEnabled(index == 0)

    def _farm_record_return_time(self, tx, ty, troops):
        """Başarılı farm saldırısının tahmini dönüş zamanını kaydet."""
        import time, math
        v = self._game_data.get("village", {})
        src_x = v.get("x", 0)
        src_y = v.get("y", 0)
        distance = math.sqrt((tx - src_x) ** 2 + (ty - src_y) ** 2)
        troop_keys = list(troops.keys()) if isinstance(troops, dict) else []
        travel_sec = self._sa_calc_travel_time(distance, troop_keys)
        return_timestamp = time.time() + (2 * travel_sec)
        self._farm_round_return_times.append(return_timestamp)
        self._farm_active_coords[(tx, ty)] = return_timestamp

    def _farm_start(self):
        """Farm sirkülasyonunu başlat."""
        if not self._map_data_loaded:
            QMessageBox.warning(self, "Uyarı", "Önce haritayı yükleyin!")
            return

        troops = self._farm_get_troops_to_send()
        if not troops:
            QMessageBox.warning(self, "Uyarı", "En az bir asker birimi girin!")
            return

        self.farm_enable_cb.setChecked(True)
        self.farm_start_btn.setEnabled(False)
        self.farm_stop_btn.setEnabled(True)
        self._farm_barb_index = 0
        self._farm_sent_count = 0
        self._farm_last_send = 0
        self._farm_round_waiting = False
        self._farm_round_wait_until = 0
        self._farm_round_return_times = []
        self._farm_active_coords = {}
        self.farm_round_wait_label.setText("")

        # Tablodaki durumları sıfırla
        for i in range(self.map_barb_table.topLevelItemCount()):
            item = self.map_barb_table.topLevelItem(i)
            if item and item.text(4) != "⛔ Kara liste":
                item.setText(4, "")
                item.setForeground(4, QColor("#000000"))

        self._farm_update_labels()
        self.farm_status_label.setText("Durum: Farm başlatıldı!")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #228822;")
        self._add_log("FARM", "success", "▶ Farm sirkülasyonu başlatıldı")

    def _farm_stop(self):
        """Farm sirkülasyonunu durdur."""
        self.farm_enable_cb.setChecked(False)
        self.farm_start_btn.setEnabled(True)
        self.farm_stop_btn.setEnabled(False)
        self.farm_status_label.setText("Durum: Durduruldu")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc4444;")
        self._add_log("FARM", "warn", "⏹ Farm sirkülasyonu durduruldu")

    def _farm_template(self, template):
        """Hızlı şablon uygula."""
        for key, spin in self.farm_troop_inputs.items():
            spin.setValue(template.get(key, 0))

    def _farm_update_labels(self):
        """Farm durum etiketlerini güncelle."""
        total = self.map_barb_table.topLevelItemCount()
        remaining = 0
        for i in range(total):
            item = self.map_barb_table.topLevelItem(i)
            if item and item.text(4) == "":
                remaining += 1
        self.farm_sent_label.setText(f"Gönderilen: {self._farm_sent_count} | Kalan: {remaining}")

    def _farm_get_troops_to_send(self):
        """Gönderilecek asker dict'ini döndür. Boşsa None."""
        troops = {}
        for key, spin in self.farm_troop_inputs.items():
            val = spin.value()
            if val > 0:
                troops[key] = val
        return troops if troops else None

    def _farm_has_enough_troops(self, troops_needed):
        """Köyde yeterli asker var mı kontrol et."""
        available = self._game_data.get("troops", {})
        for key, needed in troops_needed.items():
            if available.get(key, 0) < needed:
                return False
        return True

    def _farm_process(self):
        """Her saniye çalışır — zamanı gelen saldırıyı gönderir."""
        if not self.farm_enable_cb.isChecked():
            return
        if self._farm_sending:
            return
        if not self._map_data_loaded:
            return

        import time
        now = time.time()

        # Tur arası bekleme kontrolü
        if self._farm_round_waiting:
            if self._farm_round_wait_until == 0:
                self.farm_status_label.setText("Durum: Dönüş zamanı hesaplanıyor...")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
                return
            remaining = self._farm_round_wait_until - now
            if remaining > 0:
                mins, secs = divmod(int(remaining), 60)
                self.farm_status_label.setText(f"Durum: Yeni tur {mins}dk {secs}sn sonra")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
                self.farm_round_wait_label.setText(f"Bekleme: {mins}dk {secs}sn")
                return
            else:
                self._farm_round_waiting = False
                self._farm_round_wait_until = 0
                self.farm_round_wait_label.setText("")
                self.farm_status_label.setText("Durum: Yeni tur başlıyor...")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #228822;")
                self._add_log("FARM", "info", "🔄 Yeni farm turu başlıyor")

        # Aralık kontrolü
        interval = self.farm_interval.value()
        if now - self._farm_last_send < interval:
            remaining_sec = int(interval - (now - self._farm_last_send))
            self.farm_status_label.setText(f"Durum: Sonraki saldırı {remaining_sec}sn")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")
            return

        # Gönderilecek askerleri kontrol et
        troops = self._farm_get_troops_to_send()
        if not troops:
            self.farm_status_label.setText("Durum: Asker seçilmedi!")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc2222;")
            return

        # Sıradaki barbar köyü bul (mesafe limiti dahilinde, henüz gönderilmemiş)
        max_dist = self.farm_max_dist.value()
        target = None
        target_item = None

        total = self.map_barb_table.topLevelItemCount()
        if total == 0:
            self.farm_status_label.setText("Durum: Barbar köy yok! Haritayı yükleyin.")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc2222;")
            return

        # Baştan itibaren ilk uygun köyü bul
        checked = 0
        idx = self._farm_barb_index
        while checked < total:
            if idx >= total:
                idx = 0  # Başa dön
            item = self.map_barb_table.topLevelItem(idx)
            if item:
                dist_text = item.text(2)
                try:
                    dist = float(dist_text)
                except:
                    dist = 999
                status = item.text(4)

                if dist <= max_dist and status == "":
                    # Kara liste kontrolü
                    coord_key = item.text(0).strip("()")  # "(504|623)" → "504|623"
                    if coord_key in self._farm_blacklist:
                        item.setText(4, "⛔ Kara liste")
                        item.setForeground(4, QColor("#cc2222"))
                        idx += 1
                        checked += 1
                        continue
                    _vd = item.data(0, Qt.UserRole)
                    if _vd:
                        _key = (int(_vd.get("x", 0)), int(_vd.get("y", 0)))
                        import time as _fat
                        if _fat.time() < self._farm_active_coords.get(_key, 0):
                            # Henüz dönmedi — atla, durumu güncelle
                            item.setText(4, "✓ Gönderildi")
                            item.setForeground(4, QColor("#228822"))
                            idx += 1
                            checked += 1
                            continue
                    target = item.data(0, Qt.UserRole)
                    target_item = item
                    self._farm_barb_index = idx + 1
                    break
            idx += 1
            checked += 1

        if not target:
            # Tüm köyler gönderildi — tabloyu sıfırla
            for i in range(total):
                it = self.map_barb_table.topLevelItem(i)
                if it:
                    it.setText(4, "")
                    it.setForeground(4, QColor("#000000"))
            self._farm_barb_index = 0
            self._farm_sent_count = 0
            self._farm_update_labels()

            mode = self.farm_round_wait_mode.currentIndex()
            if mode == 0:
                wait_sec = self.farm_round_wait_time.value()
                self._farm_round_wait_until = time.time() + wait_sec
                self._farm_round_waiting = True
                mins, secs = divmod(wait_sec, 60)
                self.farm_status_label.setText(f"Durum: Tur bitti, {mins}dk {secs}sn bekleniyor...")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
                self._add_log("FARM", "info", f"⏸ Tur tamamlandı, {mins}dk {secs}sn bekleniyor")
            else:
                self._farm_round_waiting = True
                if self._farm_round_return_times:
                    nearest = min(self._farm_round_return_times)
                    self._farm_round_wait_until = nearest + 10
                    wait_sec = max(1, int(self._farm_round_wait_until - time.time()))
                    mins, secs = divmod(wait_sec, 60)
                    self.farm_status_label.setText(
                        f"Durum: En yakın dönüş {mins}dk {secs}sn sonra (+10sn)")
                    self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
                    self._add_log("FARM", "info",
                        f"⏸ Tur tamamlandı, en yakın dönüş {mins}dk {secs}sn sonra (+10sn)")
                else:
                    fallback = 60
                    self._farm_round_wait_until = time.time() + fallback
                    self.farm_status_label.setText(f"Durum: Dönüş verisi yok, {fallback}sn bekleniyor...")
                    self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc8800;")
                    self._add_log("FARM", "warn", f"⏸ Dönüş verisi yok, {fallback}sn bekleniyor")
                self._farm_round_return_times = []
            return

        # Saldırı gönder
        self._farm_sending = True
        target_x = target["x"]
        target_y = target["y"]

        target_item.setText(4, "Gönderiliyor...")
        target_item.setForeground(4, QColor("#2d5a9e"))

        self.farm_status_label.setText(f"Durum: Saldırı → ({target_x}|{target_y})")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")

        self._farm_send_attack(target_x, target_y, troops, target_item)

    def _farm_send_attack(self, target_x, target_y, troops, table_item):
        """Barbar köye farm saldırısı gönder (aynı AJAX sistemi)."""
        village_id = self._game_data.get("village", {}).get("id", "")
        csrf = self._game_data.get("csrf", "")

        troops_js_parts = []
        for unit, count in troops.items():
            troops_js_parts.append(f"'{unit}': '{count}'")
        troops_js_obj = "{" + ", ".join(troops_js_parts) + "}"

        farm_cmd_id = f"farm_{target_x}_{target_y}"

        send_js = f"""
        (function() {{
            var villageId = {village_id};
            var targetX = '{target_x}';
            var targetY = '{target_y}';
            var troops = {troops_js_obj};
            var cmdId = '{farm_cmd_id}';

            if (!window.__tw_bot_results) window.__tw_bot_results = {{}};
            window.__tw_bot_results[cmdId] = 'SENDING';

            fetch('/game.php?village=' + villageId + '&screen=place', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(placeHtml) {{
                var doc = new DOMParser().parseFromString(placeHtml, 'text/html');
                var form = doc.getElementById('command-data-form');
                if (!form) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|Form bulunamadi';
                    return;
                }}
                var fd = new URLSearchParams();
                form.querySelectorAll('input[type="hidden"]').forEach(function(h) {{
                    if (h.name) fd.append(h.name, h.value);
                }});
                for (var unit in troops) {{ fd.append(unit, troops[unit]); }}
                fd.set('x', targetX);
                fd.set('y', targetY);
                fd.append('attack', 'true');
                return fetch(form.getAttribute('action'), {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: fd.toString(),
                    credentials: 'same-origin'
                }});
            }})
            .then(function(r) {{ if (r) return r.text(); }})
            .then(function(confirmHtml) {{
                if (!confirmHtml) return;
                var doc2 = new DOMParser().parseFromString(confirmHtml, 'text/html');
                var cf = doc2.getElementById('command-data-form');

                // Hata mesaji kontrolu (asker yok, kaynak yok vb.)
                var errorEl = doc2.querySelector('.error_box, .error, p.error');
                if (errorEl) {{
                    window.__tw_bot_results[cmdId] = 'NO_TROOPS|' + errorEl.textContent.trim().substring(0, 80);
                    return;
                }}

                if (!cf || !cf.querySelector('input[name="ch"]')) {{
                    // Onay formu yok — muhtemelen asker yetersiz
                    window.__tw_bot_results[cmdId] = 'NO_TROOPS|Asker yetersiz veya onay alinamadi';
                    return;
                }}
                var cd = new URLSearchParams();
                cf.querySelectorAll('input[type="hidden"]').forEach(function(h) {{
                    if (h.name) cd.append(h.name, h.value);
                }});
                var sbFarm = cf.querySelector('input[type="submit"][name="submit_confirm"], button[type="submit"][name="submit_confirm"], input[name="submit_confirm"], button[name="submit_confirm"]');
                var scFarm = (!sbFarm) ? 'true' : ((sbFarm.value != null && String(sbFarm.value) !== '') ? String(sbFarm.value) : ((sbFarm.getAttribute('value') || 'true')));
                cd.append('submit_confirm', scFarm);
                return fetch(cf.getAttribute('action'), {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: cd.toString(),
                    credentials: 'same-origin'
                }});
            }})
            .then(function(r) {{ if (r) return r.text(); }})
            .then(function() {{
                var cur = window.__tw_bot_results[cmdId] || '';
                if (cur.startsWith('ERROR') || cur.startsWith('NO_TROOPS')) return;
                window.__tw_bot_results[cmdId] = 'SENT_OK';
            }})
            .catch(function(err) {{
                window.__tw_bot_results[cmdId] = 'ERROR|' + String(err);
            }});
            return 'DISPATCHED';
        }})();
        """

        self.browser.page().runJavaScript(send_js)
        self._farm_poll_result(table_item, farm_cmd_id, target_x, target_y, troops, 0)

    def _farm_poll_result(self, table_item, cmd_id, tx, ty, troops, attempt):
        """Farm saldırı sonucunu polling ile kontrol et."""
        import time
        if attempt > 60:
            table_item.setText(4, "Zaman aşımı")
            table_item.setForeground(4, QColor("#cc2222"))
            self._farm_sending = False
            self._farm_last_send = time.time()
            return

        check_js = f"window.__tw_bot_results ? window.__tw_bot_results['{cmd_id}'] || 'WAITING' : 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str == "SENT_OK":
                table_item.setText(4, "✓ Gönderildi")
                table_item.setForeground(4, QColor("#228822"))
                self._farm_sent_count += 1
                self._farm_sending = False
                self._farm_last_send = time.time()
                self._farm_update_labels()
                self.farm_status_label.setText(f"Durum: ({tx}|{ty}) gönderildi ✓")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #228822;")
                self._add_log("FARM", "success", f"✅ Farm saldırısı → ({tx}|{ty})")
                self.browser.page().runJavaScript(
                    f"if(window.__tw_bot_results) delete window.__tw_bot_results['{cmd_id}'];")

                self._farm_record_return_time(tx, ty, troops)

            elif result_str.startswith("NO_TROOPS"):
                msg = result_str.replace("NO_TROOPS|", "")
                table_item.setText(4, "")
                table_item.setForeground(4, QColor("#000000"))
                self._farm_sending = False
                self._farm_barb_index = max(0, self._farm_barb_index - 1)

                # Rally point'ten gerçek dönüş zamanlarını çek
                self._farm_fetch_return_times()

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                table_item.setText(4, "✗ Hata")
                table_item.setForeground(4, QColor("#cc2222"))
                self._farm_sending = False
                self._farm_last_send = time.time()
                self._add_log("FARM", "error", f"❌ ({tx}|{ty}): {error}")
                self.farm_status_label.setText(f"Durum: Hata — ({tx}|{ty})")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc2222;")

            else:
                QTimer.singleShot(200, lambda: self._farm_poll_result(
                    table_item, cmd_id, tx, ty, troops, attempt + 1))

        self.browser.page().runJavaScript(check_js, on_poll)

    # ── DÖNÜŞ ZAMANI TAKİBİ ─────────────────────

    def _farm_fetch_return_times(self):
        """Rally point'ten dönen komutların gerçek varış zamanlarını çek."""
        import time

        village_id = self._game_data.get("village", {}).get("id", "")

        fetch_js = f"""
        (function() {{
            window.__tw_return_data = 'LOADING';
            fetch('/game.php?village={village_id}&screen=place', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var rows = doc.querySelectorAll('tr.command-row');
                var returns = [];
                var now = Math.floor(Date.now() / 1000);

                rows.forEach(function(row) {{
                    var cmdSpan = row.querySelector('.command_hover_details');
                    var cmdType = cmdSpan ? cmdSpan.getAttribute('data-command-type') : '';
                    var timerSpan = row.querySelector('[data-endtime]');
                    var endtime = timerSpan ? parseInt(timerSpan.getAttribute('data-endtime')) : 0;

                    if (endtime <= now) return;

                    if (cmdType === 'return' || cmdType === 'cancel') {{
                        // Zaten donuyor — endtime = koye varis zamani
                        returns.push(endtime);
                    }} else if (cmdType === 'attack' || cmdType === 'support') {{
                        // Giden saldiri — donus tahmini: varis + yolculuk suresi
                        // yolculuk = endtime - simdi (yaklasik)
                        var travelTime = endtime - now;
                        var returnTime = endtime + travelTime;
                        returns.push(returnTime);
                    }} else {{
                        // Bilinmeyen tip — yine de dahil et
                        var travelTime2 = endtime - now;
                        returns.push(endtime + travelTime2);
                    }}
                }});

                returns.sort();
                window.__tw_return_data = JSON.stringify({{status: 'OK', returns: returns, total: rows.length}});
            }})
            .catch(function(err) {{
                window.__tw_return_data = JSON.stringify({{status: 'ERROR', message: String(err)}});
            }});
        }})();
        """

        self.browser.page().runJavaScript(fetch_js)
        self._farm_poll_returns(0)

    def _farm_poll_returns(self, attempt):
        """Dönüş zamanları verisini polling ile al."""
        import time

        if attempt > 30:
            self._farm_last_send = time.time() + 55
            self.farm_status_label.setText("Durum: Asker yok, 60sn bekleniyor...")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc8800;")
            self._add_log("FARM", "warn", "⏸ Dönüş verisi alınamadı, 60sn bekleniyor")
            return

        check_js = "window.__tw_return_data || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(200, lambda: self._farm_poll_returns(attempt + 1))
                return

            try:
                data = json.loads(result_str)
            except:
                self._farm_last_send = time.time() + 55
                self.farm_status_label.setText("Durum: Asker yok, 60sn bekleniyor...")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc8800;")
                return

            returns = data.get("returns", [])
            self.browser.page().runJavaScript("window.__tw_return_data = null;")

            if returns:
                nearest = returns[0]
                wait_sec = max(1, nearest - int(time.time()))
                self._farm_last_send = nearest - self.farm_interval.value()
                self.farm_status_label.setText(
                    f"Durum: Asker yok, {wait_sec}sn sonra dönecek ({len(returns)} komut)")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc8800;")
                self._add_log("FARM", "warn",
                    f"⏸ Asker yetersiz, en yakın dönüş {wait_sec}sn sonra ({len(returns)} aktif komut)")
            else:
                self._farm_last_send = time.time() + 55
                self.farm_status_label.setText("Durum: Asker yok, dönen komut yok, 60sn bekleniyor...")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc8800;")
                self._add_log("FARM", "warn", "⏸ Dönen komut yok, 60sn bekleniyor")

        self.browser.page().runJavaScript(check_js, on_poll)

    # ── KARA LİSTE YÖNETİMİ ───────────────────

    def _farm_clear_blacklist(self):
        """Kara listeyi temizle."""
        self._farm_blacklist.clear()
        self.farm_blacklist_label.setText("Kara liste: 0 köy")
        # Tablodaki kara liste işaretlerini de temizle
        for i in range(self.map_barb_table.topLevelItemCount()):
            item = self.map_barb_table.topLevelItem(i)
            if item and item.text(4) == "⛔ Kara liste":
                item.setText(4, "")
                item.setForeground(4, QColor("#000000"))
        self._add_log("FARM", "info", "Kara liste temizlendi.")

    def _farm_add_to_blacklist(self, coord_str):
        """Koordinatı kara listeye ekle. Format: '504|623'"""
        self._farm_blacklist.add(coord_str)
        self.farm_blacklist_label.setText(f"Kara liste: {len(self._farm_blacklist)} köy")

    def _farm_check_reports(self):
        """Saldırı raporlarını tarayıp kayıplı barbar köyleri kara listeye ekle."""
        if not self.browser:
            return

        self.farm_check_reports_btn.setEnabled(False)
        self.farm_check_reports_btn.setText("Taranıyor...")
        self._add_log("FARM", "info", "Saldırı raporları taranıyor...")

        village_id = self._game_data.get("village", {}).get("id", "")

        scan_js = f"""
        (function() {{
            return fetch('/game.php?village={village_id}&screen=report&mode=attack', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var rows = doc.querySelectorAll('#report_list tr');
                var blacklist = [];

                rows.forEach(function(row) {{
                    // Kayipli rapor: kirmizi veya sari nokta
                    var dot = row.querySelector('img[src*="dots/red"], img[src*="dots/yellow"]');
                    if (!dot) return;

                    // Rapor satirinin text'inden koordinatlari bul
                    var text = row.textContent || '';
                    var coords = text.match(/\\(\\d{{3}}\\|\\d{{3}}\\)/g);
                    if (coords && coords.length >= 2) {{
                        // Ikinci koordinat = hedef
                        var target = coords[1].replace('(', '').replace(')', '');
                        blacklist.push(target);
                    }}
                }});

                return JSON.stringify({{status: 'OK', blacklist: blacklist}});
            }})
            .catch(function(err) {{
                return JSON.stringify({{status: 'ERROR', message: String(err)}});
            }});
        }})();
        """

        def on_scan(result):
            self.farm_check_reports_btn.setEnabled(True)
            self.farm_check_reports_btn.setText("Raporları Kontrol Et")

            if not result:
                self._add_log("FARM", "error", "Rapor tarama başarısız.")
                return

            try:
                data = json.loads(str(result))
            except:
                self._add_log("FARM", "error", "Rapor verisi parse edilemedi.")
                return

            if data.get("status") == "ERROR":
                self._add_log("FARM", "error", f"Hata: {data.get('message', '?')}")
                return

            new_coords = data.get("blacklist", [])
            added = 0
            for coord in new_coords:
                if coord not in self._farm_blacklist:
                    self._farm_add_to_blacklist(coord)
                    added += 1

            self._add_log("FARM", "warn",
                f"Rapor taraması: {len(new_coords)} kayıplı rapor, {added} yeni köy kara listeye eklendi")

        self.browser.page().runJavaScript(scan_js, on_scan)

    # ── TEMİZLİK (SCAVENGING) SEKMESİ ─────────

    def _build_scavenge_tab(self):
        tab = QWidget()
        tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Satır 1: Kontroller
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        self.scav_enable_cb = QCheckBox("Otomatik Temizlik")
        self.scav_enable_cb.setStyleSheet("font-weight: bold; font-size: 11px;")
        row1.addWidget(self.scav_enable_cb)

        row1.addSpacing(10)
        self.scav_start_btn = QPushButton("▶ Başlat")
        self.scav_start_btn.setObjectName("startBtn")
        self.scav_start_btn.setCursor(Qt.PointingHandCursor)
        self.scav_start_btn.clicked.connect(self._scav_start)
        row1.addWidget(self.scav_start_btn)

        self.scav_stop_btn = QPushButton("⏹ Durdur")
        self.scav_stop_btn.setObjectName("stopBtn")
        self.scav_stop_btn.setCursor(Qt.PointingHandCursor)
        self.scav_stop_btn.setEnabled(False)
        self.scav_stop_btn.clicked.connect(self._scav_stop)
        row1.addWidget(self.scav_stop_btn)

        row1.addSpacing(10)
        self.scav_refresh_btn = QPushButton("🔄 Durumu Güncelle")
        self.scav_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.scav_refresh_btn.clicked.connect(self._scav_refresh)
        row1.addWidget(self.scav_refresh_btn)

        row1.addSpacing(15)
        self.scav_status_label = QLabel("Durum: Bekliyor")
        self.scav_status_label.setStyleSheet("font-size: 10px; color: #888;")
        row1.addWidget(self.scav_status_label)

        row1.addStretch()
        layout.addLayout(row1)

        scav_split = QSplitter(Qt.Vertical)
        scav_split.setChildrenCollapsible(False)

        opt_scroll = QScrollArea()
        self.scav_opt_scroll = opt_scroll
        opt_scroll.setWidgetResizable(True)
        opt_scroll.setFrameShape(QFrame.NoFrame)
        opt_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        opt_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        opt_scroll.setMinimumHeight(200)
        opt_inner = QWidget()
        self.scav_opt_inner = opt_inner
        opt_layout = QVBoxLayout(opt_inner)
        opt_layout.setContentsMargins(0, 0, 0, 0)
        opt_layout.setSpacing(4)

        # Satır 2: Birim seçimi (checkbox)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("Birimler:"))

        self.SCAV_UNITS = [
            ("spear", "Mızrakçı"), ("sword", "Kılıç"), ("axe", "Baltacı"), ("archer", "Okçu"),
            ("light", "HSvari"), ("marcher", "AOkçu"), ("heavy", "ASvari"), ("knight", "Şövalye"),
        ]
        self.scav_unit_cbs = {}
        for key, name in self.SCAV_UNITS:
            cb = QCheckBox(name)
            cb.setStyleSheet("font-size: 10px;")
            troop_icon_mgr.apply_to_checkbox(cb, key)
            row2.addWidget(cb)
            self.scav_unit_cbs[key] = cb

        self.scav_unit_cbs["spear"].setChecked(True)
        self.scav_unit_cbs["sword"].setChecked(True)

        row2.addStretch()
        opt_layout.addLayout(row2)

        # Evde tut (Sophie keepHome)
        kh_row = QHBoxLayout()
        kh_row.addWidget(QLabel("Evde tut:"))
        self.scav_keep_home = {}
        for key, name in self.SCAV_UNITS:
            kh_row.addWidget(QLabel(name))
            kh = QSpinBox()
            kh.setRange(0, 999999)
            kh.setFixedWidth(64)
            kh.setToolTip(f"{name} — köyde bırakılacak minimum")
            self.scav_keep_home[key] = kh
            kh_row.addWidget(kh)
        kh_row.addStretch()
        opt_layout.addLayout(kh_row)

        # Kullanılacak temizlik seviyeleri (Sv1–Sv4)
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("Seviyeler:"))
        self.scav_cat_cbs = {}
        for oid, lbl in [
            (1, "Sv1 %10"), (2, "Sv2 %25"), (3, "Sv3 %50"), (4, "Sv4 %75"),
        ]:
            cb = QCheckBox(lbl)
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 10px;")
            self.scav_cat_cbs[oid] = cb
            cat_row.addWidget(cb)
        cat_row.addStretch()
        opt_layout.addLayout(cat_row)

        # Hedef dönüş süresi (saat) — off/def köy (Sophie runTimes); iki satırda taşma olmasın
        rt_block = QVBoxLayout()
        rt_block.setSpacing(4)
        rt_hint = QLabel(
            "Köy tipi, köydeki tüm birim sayılarına göre belirlenir (off toplamı > def toplamı → üst satır)."
        )
        rt_hint.setWordWrap(True)
        rt_hint.setStyleSheet("font-size: 9px; color: #666;")
        rt_block.addWidget(rt_hint)
        rt_sub = QHBoxLayout()
        rt_sub.setSpacing(8)
        rt_sub.addWidget(QLabel("Off köyleri:"))
        self.scav_rt_off = QDoubleSpinBox()
        self.scav_rt_off.setRange(0.1, 24.0)
        self.scav_rt_off.setSingleStep(0.5)
        self.scav_rt_off.setDecimals(2)
        self.scav_rt_off.setValue(4.0)
        self.scav_rt_off.setMinimumWidth(88)
        self.scav_rt_off.setToolTip(
            "Köyde (köy ekranındaki) off birim toplamı def’ten fazlaysa kullanılan hedef dönüş süresi saat."
        )
        rt_sub.addWidget(self.scav_rt_off)
        rt_sub.addWidget(QLabel("saat"))
        rt_sub.addSpacing(20)
        rt_sub.addWidget(QLabel("Def köyleri:"))
        self.scav_rt_def = QDoubleSpinBox()
        self.scav_rt_def.setRange(0.1, 24.0)
        self.scav_rt_def.setSingleStep(0.5)
        self.scav_rt_def.setDecimals(2)
        self.scav_rt_def.setValue(3.0)
        self.scav_rt_def.setMinimumWidth(88)
        self.scav_rt_def.setToolTip(
            "Köyde def birim toplamı off’a eşit veya fazlaysa kullanılan hedef dönüş süresi saat."
        )
        rt_sub.addWidget(self.scav_rt_def)
        rt_sub.addWidget(QLabel("saat"))
        rt_sub.addStretch()
        rt_block.addLayout(rt_sub)
        opt_layout.addLayout(rt_block)

        # Dağıtım modu (Sophie prioritiseHighCat / balanced)
        prio_row = QHBoxLayout()
        prio_row.addWidget(QLabel("Dağıtım:"))
        self.scav_prio_group = QButtonGroup(self)
        self.scav_prio_balanced = QRadioButton("Dengeli (çok asker + tüm kategoriler)")
        self.scav_prio_highfirst = QRadioButton("Önce yüksek kategori doldur (Sophie öncelik)")
        self.scav_prio_balanced.setChecked(True)
        self.scav_prio_group.addButton(self.scav_prio_balanced, 0)
        self.scav_prio_group.addButton(self.scav_prio_highfirst, 1)
        prio_row.addWidget(self.scav_prio_balanced)
        prio_row.addWidget(self.scav_prio_highfirst)
        prio_row.addStretch()
        opt_layout.addLayout(prio_row)

        opt_scroll.setWidget(opt_inner)
        scav_split.addWidget(opt_scroll)

        # Köy tablosu
        self.scav_table = QTreeWidget()
        self.scav_table.setAlternatingRowColors(True)
        self.scav_table.setRootIsDecorated(False)
        self.scav_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scav_table.setHeaderLabels([
            "Köy", "Sv1", "Sv2", "Sv3", "Sv4",
            "Atıma kalan", "Evdeki Asker", "Durum",
        ])
        for i in range(8):
            if i in (0, 6, 7):
                self.scav_table.header().setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                # ResizeToContents + çok köy = her saniye tüm satırları ölçüp dondurur
                self.scav_table.header().setSectionResizeMode(i, QHeaderView.Fixed)
                self.scav_table.setColumnWidth(i, 76 if i <= 4 else 88)
        scav_split.addWidget(self.scav_table)
        scav_split.setStretchFactor(0, 0)
        scav_split.setStretchFactor(1, 1)
        scav_split.setSizes([320, 480])
        layout.addWidget(scav_split, 1)
        self.scav_tab = tab
        self.tabs.addTab(tab, "🧹 Temizlik")

        # Timer
        self._scav_timer = QTimer(self)
        self._scav_timer.timeout.connect(self._scav_tick)
        self._scav_timer.start(1000)
        self._scav_active = False
        self._scav_sending = False
        self._scav_checking = False
        self._scav_next_send = 0
        self._scav_villages_cache = []  # Tüm köy verileri
        self._scav_world_meta = {}  # duration_factor, duration_exponent, duration_initial_seconds

    # ── TEMİZLİK (TOPLU) FONKSİYONLAR ────────

    RT_UNITS = [
        ("spear", "Mızrakçı"),
        ("sword", "Kılıç ustası"),
        ("axe", "Baltacı"),
        ("archer", "Okçu"),
        ("spy", "Casus"),
        ("light", "Hafif atlı"),
        ("marcher", "Atlı okçu"),
        ("heavy", "Ağır atlı"),
        ("ram", "Koçbaşı"),
        ("catapult", "Mancınık"),
        ("knight", "Şövalye"),
    ]

    SCAV_CARRY = {
        "spear": 25, "sword": 15, "axe": 10, "archer": 10,
        "light": 80, "marcher": 50, "heavy": 50, "knight": 100,
    }

    # Off / def sınıflandırması (Sophie unitType)
    SCAV_UNIT_OFF_DEF = {
        "spear": "def", "sword": "def", "axe": "off", "archer": "def",
        "light": "off", "marcher": "off", "heavy": "def", "knight": "def",
    }

    def _scav_start(self):
        any_checked = any(cb.isChecked() for cb in self.scav_unit_cbs.values())
        if not any_checked:
            QMessageBox.warning(self, "Uyarı", "En az bir birim türü seçin!")
            return
        self.scav_enable_cb.setChecked(True)
        self._scav_active = True
        self._scav_next_send = 0
        self.scav_start_btn.setEnabled(False)
        self.scav_stop_btn.setEnabled(True)
        self.scav_status_label.setText("Durum: Aktif")
        self.scav_status_label.setStyleSheet("font-size: 10px; color: #228822;")
        self._add_log("TEMİZLİK", "success", "▶ Toplu temizlik başlatıldı")
        self._scav_process()

    def _scav_stop(self):
        self.scav_enable_cb.setChecked(False)
        self._scav_active = False
        self.scav_start_btn.setEnabled(True)
        self.scav_stop_btn.setEnabled(False)
        self.scav_status_label.setText("Durum: Durduruldu")
        self.scav_status_label.setStyleSheet("font-size: 10px; color: #cc4444;")
        self._add_log("TEMİZLİK", "warn", "⏹ Toplu temizlik durduruldu")

    def _scav_refresh(self):
        """Manuel güncelleme butonu."""
        if self._human_verification_required:
            return
        self._scav_process()

    def _scav_tick(self):
        import time
        on_scav_tab = self.tabs.currentWidget() is getattr(self, "scav_tab", None)
        if not self._scav_active:
            if on_scav_tab:
                self._scav_update_countdowns()
            return
        if self._human_verification_required:
            self.scav_status_label.setText("Durum: Doğrulama bekleniyor — temizlik duraklatıldı")
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #cc4444;")
            if on_scav_tab:
                self._scav_update_countdowns()
            return
        if self._scav_sending or self._scav_checking:
            if on_scav_tab:
                self._scav_update_countdowns()
            return
        if not self.browser:
            return
        now = time.time()
        if on_scav_tab:
            self._scav_update_countdowns()
        if self._scav_next_send > now:
            remaining = int(self._scav_next_send - now)
            self.scav_status_label.setText(
                f"Durum: Sonraki veri çekimi ~{remaining}sn (köy süreleri tabloda)"
            )
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")
            return
        self._scav_process()

    def _scav_update_countdowns(self):
        """Tablodaki geri sayımları günceller; yalnız metin değişince yazar (UI yükünü düşürür)."""
        import time
        now = time.time()
        tree = self.scav_table
        tree.setUpdatesEnabled(False)
        try:
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                if not item:
                    continue
                for col in range(1, 5):
                    rt_data = item.data(col, Qt.UserRole)
                    if rt_data and isinstance(rt_data, (int, float)) and rt_data > 0:
                        rem = max(0, int(rt_data - now))
                        if rem > 0:
                            mins, secs = divmod(rem, 60)
                            hrs, mins = divmod(mins, 60)
                            nt = f"{hrs:02d}:{mins:02d}:{secs:02d}"
                            if item.text(col) != nt:
                                item.setText(col, nt)
                        else:
                            if item.text(col) != "✓ Bitti":
                                item.setText(col, "✓ Bitti")
                                item.setForeground(col, QColor("#228822"))

                col_ready = 5
                rts = item.data(col_ready, Qt.UserRole)
                if rts and isinstance(rts, (int, float)) and float(rts) > now:
                    rem = max(0, int(float(rts) - now))
                    mins, secs = divmod(rem, 60)
                    hrs, mins = divmod(mins, 60)
                    nt = f"{hrs:02d}:{mins:02d}:{secs:02d}"
                    if item.text(col_ready) != nt:
                        item.setText(col_ready, nt)
                        item.setForeground(col_ready, QColor("#aa6600"))
                elif rts and isinstance(rts, (int, float)) and float(rts) > 0:
                    if item.text(col_ready) != "✓ Hazır":
                        item.setText(col_ready, "✓ Hazır")
                        item.setForeground(col_ready, QColor("#228822"))
                        item.setData(col_ready, Qt.UserRole, 0)
        finally:
            tree.setUpdatesEnabled(True)

    def _scav_process(self):
        """Mass scavenging sayfasından tüm köylerin verisini çek."""
        if not self.browser:
            return
        if self._human_verification_required:
            return
        self._scav_checking = True

        # Dünya değişiminde (ör. w101 → w102) sadece _game_data eski köy id’siyle kalabiliyor; JS
        # tarafı önce oyun penceresindeki URL’deki village= kullanır, yedek: buradaki değer + combobox.
        village_id = (self._game_data.get("village") or {}).get("id", "") or ""
        if not village_id and hasattr(self, "village_combo"):
            try:
                d = self.village_combo.currentData()
                if d is not None:
                    village_id = d
            except (RuntimeError, TypeError, AttributeError):
                pass
        if not village_id:
            for v in self._game_data.get("all_villages") or []:
                if v.get("id"):
                    village_id = v.get("id")
                    break
        if not village_id:
            self._scav_checking = False
            self.scav_status_label.setText("Durum: Köy yok (önce oyun/veri yenile)")
            return

        self.scav_status_label.setText("Durum: Veriler çekiliyor...")
        self.scav_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")

        # Sophie tarzı: tüm sayfalar + dünya duration_* + tam köy JSON'u
        vid = str(village_id).replace("\\", "").replace('"', "")
        fetch_js = r"""
        (function() {
            window.__tw_scav_mass = 'LOADING';
            var __FALLBACK_VID__ = '__VILLAGE_ID__';
            function resolveVillageId() {
                try {
                    var u = new URL(window.location.href);
                    var p = u.searchParams.get('village');
                    if (p && /^[0-9]+$/.test(String(p).trim())) return String(p).trim();
                } catch (e) {}
                var s = String(__FALLBACK_VID__ != null ? __FALLBACK_VID__ : '').trim();
                return (s && /^[0-9]+$/.test(s)) ? s : '0';
            }
            var villageIdForMass = resolveVillageId();
            var baseUrl = '/game.php?village=' + encodeURIComponent(villageIdForMass)
                + '&screen=place&mode=scavenge_mass';

            function extractVillagesArray(html) {
                if (!html || html.length < 20) return null;
                var needles = [
                    '[{"village_id":', '[{"village_id" :', '[{ "village_id":', '[{ "village_id" :',
                    "[{\"village_id\":", "[{ \"village_id\":"
                ];
                var start = -1, n, idx;
                for (n = 0; n < needles.length; n++) {
                    idx = html.indexOf(needles[n]);
                    if (idx >= 0) { start = idx; break; }
                }
                if (start < 0) {
                    var vMark = html.indexOf('"village_id"');
                    if (vMark < 0) vMark = html.indexOf('village_id"');
                    if (vMark < 0) return null;
                    for (var back = vMark; back >= 0 && (vMark - back) < 2000000; back--) {
                        if (html[back] === '[') { start = back; break; }
                    }
                }
                if (start < 0) return null;
                var depth = 0;
                for (var i = start; i < html.length; i++) {
                    var c = html[i];
                    if (c === '[') depth++;
                    else if (c === ']') {
                        depth--;
                        if (depth === 0) {
                            try { return JSON.parse(html.substring(start, i + 1)); }
                            catch (e) { return null; }
                        }
                    }
                }
                return null;
            }

            function extractWorldDuration(html) {
                var m = html.match(/"duration_factor"\s*:\s*([0-9.]+)[\s\S]{0,800}?"duration_exponent"\s*:\s*([0-9.]+)[\s\S]{0,800}?"duration_initial_seconds"\s*:\s*([0-9.+-eE.]+)/);
                if (!m) return null;
                return {
                    duration_factor: parseFloat(m[1]),
                    duration_exponent: parseFloat(m[2]),
                    duration_initial_seconds: parseFloat(m[3])
                };
            }

            function extractMaxPage(html) {
                var re = /[?&]page=(\d+)/g, m, mx = 0;
                while ((m = re.exec(html)) !== null) {
                    var n = parseInt(m[1], 10);
                    if (!isNaN(n) && n > mx) mx = n;
                }
                return mx;
            }

            function normalizeVillage(v) {
                var rawOpts = v && v.options;
                var o = (rawOpts && typeof rawOpts === 'object' && !Array.isArray(rawOpts)) ? rawOpts : {};
                var opts = {};
                for (var id in o) {
                    if (!Object.prototype.hasOwnProperty.call(o, id)) continue;
                    var opt = o[id];
                    var rt = null;
                    if (opt.scavenging_squad) {
                        rt = opt.scavenging_squad.return_time;
                    }
                    opts[id] = {
                        is_locked: !!opt.is_locked,
                        is_active: !!(opt.scavenging_squad),
                        return_time: rt,
                        unlock_time: opt.unlock_time || null
                    };
                }
                return {
                    village_id: v.village_id,
                    village_name: v.village_name,
                    name: v.village_name,
                    unit_counts_home: v.unit_counts_home || {},
                    unit_carry_factor: (v.unit_carry_factor != null && v.unit_carry_factor !== undefined) ? v.unit_carry_factor : 1,
                    has_rally_point: !!v.has_rally_point,
                    options: opts
                };
            }

            async function loadAll() {
                try {
                    var r0 = await fetch(baseUrl + '&page=0', { credentials: 'same-origin' });
                    var h0 = await r0.text();
                    var world = extractWorldDuration(h0);
                    var raw0 = extractVillagesArray(h0);
                    if (!raw0 || !raw0.length) {
                        var rAlt = await fetch(baseUrl, { credentials: 'same-origin' });
                        h0 = await rAlt.text();
                        world = world || extractWorldDuration(h0);
                        raw0 = extractVillagesArray(h0);
                    }
                    if (!raw0 || !raw0.length) {
                        window.__tw_scav_mass = JSON.stringify({
                            status: 'ERROR',
                            message: 'Koy verisi bulunamadi (dizi) village=' + String(villageIdForMass)
                        });
                        return;
                    }
                    // Sayfalama: sadece extractMaxPage() yeterli degil — TW cogu zaman tum page=
                    // linklerini gostermez; bu yuzden "sayfa doluysa devam" ile tum sayfalari cek.
                    var seen = {};
                    var allRaw = [];
                    var zi, vv, idk;
                    for (zi = 0; zi < raw0.length; zi++) {
                        vv = raw0[zi];
                        idk = String(vv.village_id);
                        if (!seen[idk]) { seen[idk] = true; allRaw.push(vv); }
                    }
                    var perPage = Math.max(raw0.length, 1);
                    var p = 1;
                    var maxSafePages = 250;
                    var httpPages = 1;
                    while (p <= maxSafePages) {
                        await new Promise(function(res) { setTimeout(res, 200); });
                        var rp = await fetch(baseUrl + '&page=' + p, { credentials: 'same-origin' });
                        var hp = await rp.text();
                        var arr = extractVillagesArray(hp);
                        if (!arr || arr.length === 0) break;
                        httpPages++;
                        var added = 0;
                        for (var i = 0; i < arr.length; i++) {
                            var vx = arr[i];
                            var idk2 = String(vx.village_id);
                            if (seen[idk2]) continue;
                            seen[idk2] = true;
                            allRaw.push(vx);
                            added++;
                        }
                        if (arr.length < perPage) break;
                        if (added === 0) break;
                        perPage = Math.max(perPage, arr.length);
                        p++;
                    }
                    var result = [];
                    for (var j = 0; j < allRaw.length; j++) {
                        result.push(normalizeVillage(allRaw[j]));
                    }
                    window.__tw_scav_mass = JSON.stringify({
                        status: 'OK',
                        world: world,
                        villages: result,
                        village_count: result.length,
                        http_pages: httpPages,
                        scav_village_id_used: String(villageIdForMass)
                    });
                } catch (e) {
                    window.__tw_scav_mass = JSON.stringify({ status: 'ERROR', message: String(e) });
                }
            }
            loadAll();
        })();
        """.replace("__VILLAGE_ID__", vid)

        self.browser.page().runJavaScript(fetch_js)
        self._scav_poll_mass(0)

    def _scav_poll_mass(self, attempt):
        """Mass scav verisini polling ile al."""
        import time
        if attempt > 200:
            self.scav_status_label.setText("Durum: Veri alınamadı (zaman aşımı)")
            self._scav_checking = False
            return
        check_js = "window.__tw_scav_mass || 'WAITING';"
        def on_poll(result):
            result_str = str(result) if result else "WAITING"
            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(350, lambda: self._scav_poll_mass(attempt + 1))
                return
            self.browser.page().runJavaScript("window.__tw_scav_mass = null;")
            try:
                data = json.loads(result_str)
            except:
                self.scav_status_label.setText("Durum: Parse hatası")
                self._scav_checking = False
                return
            if data.get("status") == "ERROR":
                self.scav_status_label.setText(f"Durum: {data.get('message','?')[:40]}")
                self._scav_checking = False
                return

            villages = data.get("villages", [])
            self._scav_world_meta = data.get("world") or {}
            self._scav_villages_cache = villages
            self._scav_update_table(villages)

            nv = len(self._game_data.get("all_villages") or [])
            nsc = len(villages)
            hp = data.get("http_pages")
            vused = data.get("scav_village_id_used")
            if vused:
                self._add_log(
                    "TEMİZLİK",
                    "info",
                    f"Toplu temizlik isteği village={vused} (adres çubuğundaki köy öncelikli; dünya değiştirdiyseniz veri yenile).",
                )
            if nv > 0 and nsc < nv:
                self._add_log(
                    "TEMİZLİK",
                    "warn",
                    f"Temizlik listesi {nsc} köy (hesapta {nv} köy görünüyor). "
                    "Çok köylü hesaplarda sayfalama genişletildi; yine eksikse «Durumu Güncelle» deneyin.",
                )
            else:
                self._add_log(
                    "TEMİZLİK",
                    "info",
                    f"Temizlik köy listesi güncellendi: {nsc} köy"
                    + (f" ({hp} HTTP sayfa)" if hp is not None else ""),
                )

            # Boş seviyeleri olan köyler için toplu gönderim hazırla
            if self._scav_active and not self._human_verification_required:
                self._scav_send_all(villages)
            else:
                self._scav_checking = False

        self.browser.page().runJavaScript(check_js, on_poll)

    def _scav_update_table(self, villages):
        """Tabloyu köy verileriyle güncelle."""
        import time
        now = time.time()
        tree = self.scav_table
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()

            unit_short = {"spear":"Mız","sword":"Kıl","axe":"Bal","archer":"Okç",
                          "light":"HSv","marcher":"AOk","heavy":"ASv","knight":"Şöv",
                          "spy":"Cas","ram":"Koç","catapult":"Man","snob":"Mis"}

            selected_units = [k for k, cb in self.scav_unit_cbs.items() if cb.isChecked()]

            for v in villages:
                name = v.get("name") or v.get("village_name", "?")
                opts = v.get("options", {})
                uch = v.get("unit_counts_home") or {}
                # Gösterim: seçili birimler − evde tut
                available = {}
                for u in selected_units:
                    keep = 0
                    if u in self.scav_keep_home:
                        keep = int(self.scav_keep_home[u].value() or 0)
                    c = int(uch.get(u, 0) or 0) - keep
                    if c > 0:
                        available[u] = c

                # Evdeki asker özeti
                troop_parts = []
                for u, c in available.items():
                    if c > 0:
                        troop_parts.append(f"{unit_short.get(u, u)}:{c}")
                troops_text = ", ".join(troop_parts) if troop_parts else "—"

                # Her seviye durumu
                sv_texts = []
                sv_colors = []
                sv_return_times = []

                free_count = 0
                for oid in ["1", "2", "3", "4"]:
                    opt = opts.get(oid, {})
                    if opt.get("is_locked"):
                        ut = opt.get("unlock_time")
                        if ut and ut > now:
                            rem = max(0, int(ut - now))
                            mins, secs = divmod(rem, 60)
                            hrs, mins = divmod(mins, 60)
                            sv_texts.append(f"🔓{hrs:02d}:{mins:02d}:{secs:02d}")
                            sv_colors.append("#aa6600")
                            sv_return_times.append(ut)
                        else:
                            sv_texts.append("🔒")
                            sv_colors.append("#888888")
                            sv_return_times.append(0)
                    elif opt.get("is_active"):
                        rt = opt.get("return_time")
                        if rt:
                            rem = max(0, int(rt - now))
                            mins, secs = divmod(rem, 60)
                            hrs, mins = divmod(mins, 60)
                            sv_texts.append(f"🔄{hrs:02d}:{mins:02d}:{secs:02d}")
                        else:
                            sv_texts.append("🔄")
                        sv_colors.append("#2d5a9e")
                        sv_return_times.append(rt or 0)
                    else:
                        sv_texts.append("⏸ Boş")
                        sv_colors.append("#228822")
                        sv_return_times.append(0)
                        free_count += 1

                status = f"{free_count} boş" if free_count > 0 else "Tümü dolu"
                status_color = "#228822" if free_count > 0 else "#2d5a9e"

                # Köy bazlı: açık slotların tamamı boşalana kadar süre (UI geri sayımı)
                ready_col = 5
                if not v.get("has_rally_point"):
                    ready_text = "—"
                    ready_ts = 0.0
                    ready_color = "#888888"
                else:
                    ready_ts = self._scav_village_next_ready_unix(opts, now)
                    if ready_ts <= 0 or ready_ts <= now:
                        ready_text = "✓ Hazır"
                        ready_ts = 0.0
                        ready_color = "#228822"
                    else:
                        rem = max(0, int(ready_ts - now))
                        mins, secs = divmod(rem, 60)
                        hrs, mins = divmod(mins, 60)
                        ready_text = f"{hrs:02d}:{mins:02d}:{secs:02d}"
                        ready_color = "#aa6600"

                item = QTreeWidgetItem([
                    name,
                    sv_texts[0], sv_texts[1], sv_texts[2], sv_texts[3],
                    ready_text,
                    troops_text,
                    status,
                ])
                for col in range(1, 5):
                    item.setForeground(col, QColor(sv_colors[col - 1]))
                    item.setTextAlignment(col, Qt.AlignCenter)
                    item.setData(col, Qt.UserRole, sv_return_times[col - 1])  # Geri sayım için
                item.setForeground(ready_col, QColor(ready_color))
                item.setTextAlignment(ready_col, Qt.AlignCenter)
                item.setData(ready_col, Qt.UserRole, ready_ts if ready_ts > now else 0)
                item.setForeground(7, QColor(status_color))
                item.setTextAlignment(7, Qt.AlignCenter)
                item.setData(0, Qt.UserRole, v)  # Köy verisini sakla
                tree.addTopLevelItem(item)

        finally:
            tree.setUpdatesEnabled(True)
        n = len(villages)
        self._scav_timer.setInterval(2500 if n > 70 else 1000)

    def _scav_sophie_haul(self, time_hours, duration_factor, duration_exponent, duration_initial_seconds):
        """Sophie script ile aynı: parseInt((...)**(1/de)/100) sonra sqrt (tam sayı)."""
        try:
            df = float(duration_factor)
            de = float(duration_exponent)
            di = float(duration_initial_seconds)
        except (TypeError, ValueError):
            return 0
        if df <= 0 or de == 0:
            return 0
        t_sec = float(time_hours) * 3600.0
        try:
            inner = (t_sec / df - di) ** (1.0 / de) / 100.0
            if inner < 0 or inner != inner:  # NaN
                inner = 0.0
            mid = int(inner)
            if mid < 0:
                mid = 0
            return int(math.sqrt(mid))
        except (ValueError, OSError, OverflowError):
            return 0

    def _scav_sophie_total_loot(self, troops_allowed, unit_carry_factor):
        """Sophie calculateHaulCategories — birim taşıma × dünya carry faktörü."""
        f = float(unit_carry_factor or 1)
        total = 0.0
        for key, cnt in troops_allowed.items():
            if cnt <= 0:
                continue
            base = float(self.SCAV_CARRY.get(key, 0))
            if base <= 0:
                continue
            total += float(cnt) * f * base
        return total

    def _scav_sophie_calculate_units(
        self,
        troops_allowed,
        total_loot,
        total_haul,
        haul_category_rate,
        prioritise_high_cat,
        send_order,
    ):
        """
        Sophie calculateUnitsPerVillage (version != 'old' dalları).
        """
        unit_haul = self.SCAV_CARRY
        units_ready = {0: {}, 1: {}, 2: {}, 3: {}}
        ta = dict(troops_allowed)
        troop_number = sum(max(0, int(c)) for c in ta.values())

        def greedy_high_to_low(pool):
            p = dict(pool)
            for j in range(3, -1, -1):
                reach = float(haul_category_rate.get(j + 1, 0) or 0)
                for unit in send_order:
                    if unit not in p or p[unit] <= 0 or reach <= 0:
                        continue
                    base = float(unit_haul.get(unit, 0))
                    if base <= 0:
                        continue
                    amount_needed = int(reach // base)
                    if amount_needed > p[unit]:
                        units_ready[j][unit] = p[unit]
                        reach -= p[unit] * base
                        p[unit] = 0
                    else:
                        units_ready[j][unit] = amount_needed
                        reach = 0
                        p[unit] -= amount_needed
            return p

        if total_loot > total_haul:
            greedy_high_to_low(ta)
        else:
            if (not prioritise_high_cat) and troop_number > 130:
                for j in range(4):
                    for key in list(ta.keys()):
                        hr = float(haul_category_rate.get(j + 1, 0) or 0)
                        if total_loot > 0 and total_haul > 0:
                            val = int(
                                (total_loot / total_haul * hr) * (float(ta[key]) / total_loot)
                            )
                        else:
                            val = 0
                        units_ready[j][key] = val
            else:
                greedy_high_to_low(ta)

        return units_ready

    def _scav_all_open_slots_idle(self, opts):
        """Kilitli olmayan (açık) her temizlik slotu boştur; biri bile aktif temizlikteyse False."""
        for oid in ("1", "2", "3", "4"):
            opt = opts.get(oid) or opts.get(int(oid)) or {}
            if opt.get("is_locked"):
                continue
            if opt.get("is_active"):
                return False
        return True

    def _scav_village_next_ready_unix(self, opts, now):
        """Açık slotlarda aktif temizlik varken: hepsinin biteceği an (en geç dönüş). Hazırsa 0."""
        latest = None
        any_open_active = False
        for oid in ("1", "2", "3", "4"):
            opt = opts.get(oid) or opts.get(int(oid)) or {}
            if opt.get("is_locked"):
                continue
            if not opt.get("is_active"):
                continue
            any_open_active = True
            rt = opt.get("return_time")
            if rt and float(rt) > now:
                t = float(rt)
                if latest is None or t > latest:
                    latest = t
        if not any_open_active:
            return 0.0
        if latest is None:
            return float(now) + 20.0
        return float(latest)

    def _scav_send_all(self, villages):
        """Sophie (Shinko) mantığı; yalnız açık (kilitli olmayan) slotların tamamı boşken köye dokunulur."""
        if self._human_verification_required:
            self._scav_checking = False
            return
        import time
        now = time.time()
        all_squads = []
        sent_villages = 0

        world = self._scav_world_meta or {}
        df = world.get("duration_factor")
        de = world.get("duration_exponent")
        di = world.get("duration_initial_seconds")
        if df is None or de is None or di is None:
            self._add_log(
                "TEMİZLİK",
                "error",
                "Dünya süre parametreleri okunamadı (duration_*). Sayfayı yenileyip tekrar deneyin.",
            )
            self._scav_schedule_next_mass(villages)
            self._scav_checking = False
            return

        time_off = float(self.scav_rt_off.value())
        time_def = float(self.scav_rt_def.value())
        prioritise_high = self.scav_prio_highfirst.isChecked()
        cat_enabled = {i: self.scav_cat_cbs[i].isChecked() for i in (1, 2, 3, 4)}
        send_order = [k for k, _ in self.SCAV_UNITS if self.scav_unit_cbs[k].isChecked()]

        if not send_order:
            self._scav_schedule_next_mass(villages)
            self._scav_checking = False
            return

        loot_pct = {1: 0.10, 2: 0.25, 3: 0.50, 4: 0.75}

        for v in villages:
            if not v.get("has_rally_point"):
                continue

            opts = v.get("options", {})
            village_id = v.get("village_id")
            uch = v.get("unit_counts_home") or {}
            ucf = float(v.get("unit_carry_factor") or 1)

            # Açık slotlardan herhangi biri aktif temizlikteyse bu köye bu turda dokunma
            if not self._scav_all_open_slots_idle(opts):
                continue

            troops_allowed = {}
            for key in send_order:
                keep = int(self.scav_keep_home[key].value() or 0)
                c = int(uch.get(key, 0) or 0) - keep
                if c > 0:
                    troops_allowed[key] = c

            if sum(troops_allowed.values()) < 10:
                continue

            # Köy off/def: yalnızca gönderilecek birimlere bakma (ör. yalnız mızrak+kılıç seçiliyse
            # hepsi "def" sayılıp yanlışlıkla hep def süresi kullanılıyordu). Sınıflandırma köydeki
            # tüm birimlere göre — Sophie köy tipi mantığına daha yakın.
            type_count = {"off": 0, "def": 0}
            for prop, cnt in uch.items():
                role = self.SCAV_UNIT_OFF_DEF.get(prop)
                if role is None:
                    continue
                type_count[role] = type_count.get(role, 0) + int(cnt or 0)

            if type_count["off"] > type_count["def"]:
                haul = self._scav_sophie_haul(time_off, df, de, di)
            else:
                haul = self._scav_sophie_haul(time_def, df, de, di)

            total_loot = self._scav_sophie_total_loot(troops_allowed, ucf)
            if total_loot <= 0:
                continue

            haul_category_rate = {}
            for oid in (1, 2, 3, 4):
                sk = str(oid)
                opt = opts.get(sk) or opts.get(oid) or {}
                blocked = opt.get("is_locked") or opt.get("is_active")
                if blocked:
                    haul_category_rate[oid] = 0.0
                else:
                    haul_category_rate[oid] = float(haul) / loot_pct[oid]
                if not cat_enabled.get(oid):
                    haul_category_rate[oid] = 0.0

            total_haul = sum(haul_category_rate.values())
            if total_haul <= 0:
                continue

            units_ready = self._scav_sophie_calculate_units(
                troops_allowed,
                total_loot,
                total_haul,
                haul_category_rate,
                prioritise_high,
                send_order,
            )

            village_squads = []
            for k in range(4):
                oid = k + 1
                sk = str(oid)
                opt = opts.get(sk) or opts.get(oid) or {}
                if opt.get("is_locked") or opt.get("is_active"):
                    continue
                if not cat_enabled.get(oid):
                    continue
                uc = {u: int(c) for u, c in units_ready.get(k, {}).items() if int(c) > 0}
                level_total = sum(uc.values())
                if level_total < 10:
                    continue
                village_squads.append({
                    "village_id": village_id,
                    "candidate_squad": {
                        "unit_counts": uc,
                        "carry_max": 9999999999,
                    },
                    "option_id": oid,
                    "use_premium": False,
                })

            if village_squads:
                all_squads.extend(village_squads)
                sent_villages += 1

        if not all_squads:
            self._scav_schedule_next_mass(villages)
            self._scav_checking = False
            return

        # En verimli önce: Sv4 > Sv3 > Sv2 > Sv1; aynı seviyede köy id
        def _scav_squad_order(s):
            try:
                vid = int(s.get("village_id") or 0)
            except (TypeError, ValueError):
                vid = 0
            return (-int(s.get("option_id", 0)), vid)

        all_squads.sort(key=_scav_squad_order)

        self._add_log("TEMİZLİK", "info",
            f"Toplu gönderim (Sophie mantığı): {len(all_squads)} temizlik, {sent_villages} köy")

        self._scav_send_batch(all_squads, 0, sent_villages)

    def _scav_send_batch(self, all_squads, offset, total_villages):
        """200'lik gruplar halinde gönder."""
        batch = all_squads[offset:offset + 200]
        if not batch:
            self._scav_checking = False
            self.scav_status_label.setText(f"Durum: {total_villages} köye gönderildi ✓")
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #228822;")
            import time
            self._scav_next_send = time.time() + 10  # 10sn sonra durumu güncelle
            # Tabloyu yenile
            QTimer.singleShot(3000, self._scav_refresh)
            return

        squads_js = json.dumps(batch)

        send_js = f"""
        (function() {{
            window.__tw_scav_batch = 'SENDING';
            TribalWars.post('scavenge_api',
                {{ajaxaction: 'send_squads'}},
                {{"squad_requests": {squads_js}}},
                function(data) {{
                    var resp = data.squad_responses || (data.response && data.response.squad_responses) || [];
                    var success = resp.filter(function(r) {{ return r.success; }}).length;
                    window.__tw_scav_batch = 'OK|' + success + '/' + resp.length;
                }},
                function(err) {{
                    window.__tw_scav_batch = 'ERROR|' + String(err);
                }}
            );
        }})();
        """

        self.browser.page().runJavaScript(send_js)
        self._scav_poll_batch(all_squads, offset, total_villages, 0)

    def _scav_poll_batch(self, all_squads, offset, total_villages, attempt):
        if attempt > 40:
            self._scav_checking = False
            return
        check_js = "window.__tw_scav_batch || 'WAITING';"
        def on_poll(result):
            result_str = str(result) if result else "WAITING"
            if result_str in ("WAITING", "SENDING"):
                QTimer.singleShot(300, lambda: self._scav_poll_batch(all_squads, offset, total_villages, attempt + 1))
                return
            self.browser.page().runJavaScript("window.__tw_scav_batch = null;")

            if result_str.startswith("OK"):
                info = result_str.replace("OK|", "")
                self._add_log("TEMİZLİK", "success", f"✅ Batch gönderildi: {info}")
                # Sonraki batch
                next_offset = offset + 200
                if next_offset < len(all_squads):
                    QTimer.singleShot(500, lambda: self._scav_send_batch(all_squads, next_offset, total_villages))
                else:
                    self._scav_checking = False
                    self.scav_status_label.setText(f"Durum: {total_villages} köye gönderildi ✓")
                    self.scav_status_label.setStyleSheet("font-size: 10px; color: #228822;")
                    import time
                    self._scav_next_send = time.time() + 10
                    QTimer.singleShot(3000, self._scav_refresh)
            else:
                error = result_str.replace("ERROR|", "")
                self._add_log("TEMİZLİK", "error", f"❌ Batch hatası: {error}")
                self._scav_checking = False

        self.browser.page().runJavaScript(check_js, on_poll)

    def _scav_schedule_next_mass(self, villages):
        """Otomatik tarama aralığı: çok uzun beklememek için üst sınır (köy süreleri tabloda).
        Her `_scav_process` tüm köyleri tekrar tarar; hazır olan köylere ayrı ayrı gönderilir."""
        import time
        now = time.time()
        next_wake_delta = None

        for v in villages:
            opts = v.get("options", {})
            latest_open_active = None
            for oid in ("1", "2", "3", "4"):
                opt = opts.get(oid) or opts.get(int(oid)) or {}
                if opt.get("is_locked"):
                    continue
                if not opt.get("is_active"):
                    continue
                rt = opt.get("return_time")
                if rt and rt > now:
                    end_t = float(rt)
                else:
                    end_t = now + 25.0
                if latest_open_active is None or end_t > latest_open_active:
                    latest_open_active = end_t
            if latest_open_active is not None:
                delta = latest_open_active - now
                if next_wake_delta is None or delta < next_wake_delta:
                    next_wake_delta = delta

        # Üst sınır: 50–70 sn arası rastgele (sabit periyot tespitini zorlaştırır)
        poll_cap = random.randint(50, 70)

        if next_wake_delta is not None:
            wait = max(5, min(int(next_wake_delta) + 3, poll_cap))
            self._scav_next_send = now + wait
            self.scav_status_label.setText(
                f"Durum: Sonraki tarama ~{wait}sn (köy başı süre: Atıma kalan)"
            )
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
            self._add_log(
                "TEMİZLİK",
                "info",
                f"⏳ Sonraki tarama ~{wait}sn (tabloda her köyün kendi geri sayımı)",
            )
        else:
            idle_wait = random.randint(50, 70)
            self._scav_next_send = now + idle_wait
            self.scav_status_label.setText(f"Durum: ~{idle_wait}sn sonra tekrar kontrol")
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")

    # ── ASKER TOPLAMA (screen=train) ──────────────────────────

    RT_UNIT_SHORT = {
        "spear": "Mız", "sword": "Kıl", "axe": "Bal", "archer": "Okç",
        "spy": "Cas", "light": "HSv", "marcher": "AOk", "heavy": "ASv",
        "ram": "Koç", "catapult": "Man", "knight": "Şöv",
    }

    def _build_recruit_train_tab(self):
        tab = QWidget()
        tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Row 1: global controls
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.rt_enable_cb = QCheckBox("Otomatik asker toplama")
        self.rt_enable_cb.setStyleSheet("font-weight: bold; font-size: 11px;")
        row1.addWidget(self.rt_enable_cb)
        row1.addSpacing(10)
        self.rt_start_btn = QPushButton("▶ Başlat")
        self.rt_start_btn.setObjectName("startBtn")
        self.rt_start_btn.setCursor(Qt.PointingHandCursor)
        self.rt_start_btn.clicked.connect(self._rt_start)
        row1.addWidget(self.rt_start_btn)
        self.rt_stop_btn = QPushButton("⏹ Durdur")
        self.rt_stop_btn.setObjectName("stopBtn")
        self.rt_stop_btn.setCursor(Qt.PointingHandCursor)
        self.rt_stop_btn.setEnabled(False)
        self.rt_stop_btn.clicked.connect(self._rt_stop)
        row1.addWidget(self.rt_stop_btn)
        row1.addSpacing(8)
        self.rt_refresh_btn = QPushButton("🔄 Köyleri Güncelle")
        self.rt_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.rt_refresh_btn.clicked.connect(self._rt_refresh_villages)
        row1.addWidget(self.rt_refresh_btn)
        row1.addSpacing(12)
        self.rt_status_label = QLabel("Durum: Bekliyor")
        self.rt_status_label.setStyleSheet("font-size: 10px; color: #888;")
        row1.addWidget(self.rt_status_label)
        row1.addStretch()
        layout.addLayout(row1)

        # ── Main splitter: selection+units (top) | active training list (bottom) ──
        rt_split = QSplitter(Qt.Vertical)
        rt_split.setChildrenCollapsible(False)

        # ── Top: village selector + unit picker ──────────────────────
        top_widget = QWidget()
        top_lay = QVBoxLayout(top_widget)
        top_lay.setContentsMargins(0, 0, 0, 4)
        top_lay.setSpacing(4)

        vsel_group = QGroupBox("1. Köyleri seçin")
        vsel_lay = QVBoxLayout(vsel_group)
        vsel_lay.setContentsMargins(4, 4, 4, 4)
        self.rt_vsel_table = QTreeWidget()
        self.rt_vsel_table.setRootIsDecorated(False)
        self.rt_vsel_table.setAlternatingRowColors(True)
        self.rt_vsel_table.setHeaderLabels(["Köy", "Koordinat"])
        self.rt_vsel_table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.rt_vsel_table.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.rt_vsel_table.setMaximumHeight(170)
        vsel_lay.addWidget(self.rt_vsel_table)
        top_lay.addWidget(vsel_group)

        # Unit picker row + Ekle button
        urow = QHBoxLayout()
        urow.setSpacing(6)
        usel_group = QGroupBox("2. Birimleri seçin, ardından Ekle'ye basın")
        usel_lay = QHBoxLayout(usel_group)
        usel_lay.setContentsMargins(6, 4, 6, 4)
        usel_lay.setSpacing(6)
        self.rt_unit_cbs = {}
        for key, name in self.RT_UNITS:
            cb = QCheckBox(name)
            cb.setStyleSheet("font-size: 10px;")
            troop_icon_mgr.apply_to_checkbox(cb, key)
            usel_lay.addWidget(cb)
            self.rt_unit_cbs[key] = cb
        usel_lay.addStretch()
        urow.addWidget(usel_group, 1)

        self.rt_add_btn = QPushButton("➕ Ekle")
        self.rt_add_btn.setObjectName("startBtn")
        self.rt_add_btn.setCursor(Qt.PointingHandCursor)
        self.rt_add_btn.setMinimumWidth(80)
        self.rt_add_btn.setMinimumHeight(44)
        self.rt_add_btn.setToolTip(
            "Seçili köyleri, seçili birimlerle aktif eğitim listesine ekle"
        )
        self.rt_add_btn.clicked.connect(self._rt_add_villages)
        urow.addWidget(self.rt_add_btn)
        top_lay.addLayout(urow)
        rt_split.addWidget(top_widget)

        # ── Bottom: active training list ─────────────────────────────
        active_group = QGroupBox("Aktif eğitim köyleri")
        active_lay = QVBoxLayout(active_group)
        active_lay.setContentsMargins(4, 4, 4, 4)
        active_lay.setSpacing(4)

        self.rt_table = QTreeWidget()
        self.rt_table.setAlternatingRowColors(True)
        self.rt_table.setRootIsDecorated(False)
        self.rt_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rt_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.rt_table.setHeaderLabels(["Köy", "Birimler", "Sıradaki", "Atıma kalan", "Durum"])
        self.rt_table.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.rt_table.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.rt_table.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.rt_table.header().setSectionResizeMode(3, QHeaderView.Fixed)
        self.rt_table.setColumnWidth(3, 85)
        self.rt_table.header().setSectionResizeMode(4, QHeaderView.Stretch)
        active_lay.addWidget(self.rt_table, 1)

        act_btns = QHBoxLayout()
        act_btns.setSpacing(6)
        self.rt_remove_btn = QPushButton("🗑 Seçileni Çıkar")
        self.rt_remove_btn.setCursor(Qt.PointingHandCursor)
        self.rt_remove_btn.clicked.connect(self._rt_remove_selected)
        act_btns.addWidget(self.rt_remove_btn)
        self.rt_clear_btn = QPushButton("Tümünü Temizle")
        self.rt_clear_btn.setCursor(Qt.PointingHandCursor)
        self.rt_clear_btn.clicked.connect(self._rt_clear_active)
        act_btns.addWidget(self.rt_clear_btn)
        act_btns.addStretch()
        active_lay.addLayout(act_btns)
        rt_split.addWidget(active_group)

        rt_split.setStretchFactor(0, 0)
        rt_split.setStretchFactor(1, 1)
        rt_split.setSizes([280, 400])
        layout.addWidget(rt_split, 1)

        self.rt_tab = tab
        self.tabs.addTab(tab, "⚔️ Asker Toplama")

        # State: vid_str -> {row, next_index, next_fire, processing, units}
        self._rt_active = False
        self._rt_village_states = {}

        self._rt_timer = QTimer(self)
        self._rt_timer.timeout.connect(self._rt_tick)
        self._rt_timer.start(1000)

        self.rt_enable_cb.toggled.connect(self._on_rt_enable_toggled)

    # ── ASKER TOPLAMA: kontrol ────────────────────────────────

    def _rt_start(self):
        if not self._rt_village_states:
            QMessageBox.warning(
                self, "Uyarı",
                "Aktif liste boş.\n"
                "Köy ve birim seçip «Ekle» ye basın."
            )
            return
        self.rt_enable_cb.setChecked(True)
        self._rt_active = True
        self.rt_start_btn.setEnabled(False)
        self.rt_stop_btn.setEnabled(True)
        n = len(self._rt_village_states)
        self.rt_status_label.setText(f"Durum: Aktif ({n} köy)")
        self.rt_status_label.setStyleSheet("font-size: 10px; color: #228822;")
        self._add_log("ASKER", "success", f"▶ Otomatik asker toplama başlatıldı — {n} köy")

    def _rt_stop(self):
        self.rt_enable_cb.setChecked(False)
        self._rt_active = False
        self.rt_start_btn.setEnabled(True)
        self.rt_stop_btn.setEnabled(False)
        self.rt_status_label.setText("Durum: Durduruldu")
        self.rt_status_label.setStyleSheet("font-size: 10px; color: #cc4444;")
        self._add_log("ASKER", "warn", "⏹ Otomatik asker toplama durduruldu")

    def _on_rt_enable_toggled(self, _checked):
        pass

    def _rt_refresh_villages(self):
        """Tüm köyleri üst seçim tablosuna doldur; alt aktif liste etkilenmez."""
        if not hasattr(self, "rt_vsel_table"):
            return
        all_v = self._game_data.get("all_villages") or []
        if not all_v:
            v = self._game_data.get("village", {})
            if v:
                all_v = [v]

        self.rt_vsel_table.clear()
        for v in all_v:
            vid = str(v.get("id", "") or "")
            if not vid:
                continue
            coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
            name = v.get("name", "?")
            row = QTreeWidgetItem([name, coord])
            row.setCheckState(0, Qt.Unchecked)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setData(0, Qt.UserRole, vid)
            self.rt_vsel_table.addTopLevelItem(row)

        # Update labels in active list (village may have been renamed)
        for v in all_v:
            vid = str(v.get("id", "") or "")
            if vid in self._rt_village_states:
                coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                label = f"{v.get('name', '?')} {coord}"
                self._rt_village_states[vid]["row"].setText(0, label)

    def _rt_add_villages(self):
        """Seçili köyleri seçili birimlerle aktif eğitim listesine ekle (veya güncelle)."""
        if not hasattr(self, "rt_vsel_table"):
            return

        # Collect checked villages
        checked_vids = []
        for i in range(self.rt_vsel_table.topLevelItemCount()):
            it = self.rt_vsel_table.topLevelItem(i)
            if it and it.checkState(0) == Qt.Checked:
                vid = it.data(0, Qt.UserRole)
                if vid:
                    checked_vids.append((str(vid), it.text(0), it.text(1)))

        if not checked_vids:
            QMessageBox.warning(self, "Uyarı", "Üst listeden en az bir köy seçin.")
            return

        # Collect checked units
        selected_units = {k for k, cb in self.rt_unit_cbs.items() if cb.isChecked()}
        if not selected_units:
            QMessageBox.warning(self, "Uyarı", "En az bir birim seçin.")
            return

        unit_short_str = ", ".join(
            self.RT_UNIT_SHORT.get(k, k)
            for k, _ in self.RT_UNITS if k in selected_units
        )

        added, updated = 0, 0
        for vid, vname, coord in checked_vids:
            label = f"{vname} {coord}"
            if vid in self._rt_village_states:
                # Update existing
                st = self._rt_village_states[vid]
                st["units"] = set(selected_units)
                st["row"].setText(1, unit_short_str)
                st["row"].setText(2, "—")
                updated += 1
            else:
                row = QTreeWidgetItem([label, unit_short_str, "—", "—", "Bekliyor"])
                row.setTextAlignment(3, Qt.AlignCenter)
                for c in range(5):
                    row.setBackground(c, self._rt_bg("add"))
                self.rt_table.addTopLevelItem(row)
                self._rt_village_states[vid] = {
                    "row": row,
                    "next_index": 0,
                    "next_fire": 0.0,
                    "processing": False,
                    "units": set(selected_units),
                }
                added += 1

        parts = []
        if added:
            parts.append(f"{added} yeni köy eklendi")
        if updated:
            parts.append(f"{updated} köy güncellendi")
        msg = ", ".join(parts)
        self.rt_status_label.setText(f"Liste: {msg}")
        self._add_log("ASKER", "info", f"Aktif liste: {msg} — birimler: {unit_short_str}")

    def _rt_remove_selected(self):
        """Aktif listeden seçili satırları kaldır."""
        items = self.rt_table.selectedItems()
        if not items:
            return
        to_remove = set(id(it) for it in items)
        for vid in list(self._rt_village_states.keys()):
            st = self._rt_village_states[vid]
            if id(st.get("row")) in to_remove:
                idx = self.rt_table.indexOfTopLevelItem(st["row"])
                if idx >= 0:
                    self.rt_table.takeTopLevelItem(idx)
                del self._rt_village_states[vid]

    def _rt_clear_active(self):
        """Aktif listeyi tamamen temizle."""
        self.rt_table.clear()
        self._rt_village_states.clear()
        self.rt_status_label.setText("Durum: Liste temizlendi")

    def _rt_get_village_units(self, vid):
        """Bu köy için sıralı (key, name) listesi."""
        st = self._rt_village_states.get(str(vid))
        if not st:
            return []
        selected = st.get("units", set())
        return [(k, n) for k, n in self.RT_UNITS if k in selected]

    # ── ASKER TOPLAMA: tick & işlem ──────────────────────────

    def _rt_is_village_active(self, vid):
        st = self._rt_village_states.get(str(vid))
        return bool(st and st.get("units"))

    def _rt_bg(self, kind: str) -> QColor:
        """Return a dark-mode-aware background colour for the active training table."""
        dark = getattr(self, "_dark_mode", False)
        palette = {
            # kind: (light_hex, dark_hex)
            "add":     ("#f0f8ff", "#1e2d3d"),
            "trained": ("#e8f5e8", "#162b18"),
            "busy":    ("#fff8e8", "#2d2010"),
            "neutral": ("#f5f5f5", "#2a2a2a"),
            "error":   ("#fff0f0", "#3a1414"),
            "timeout": ("#f5f5f5", "#2a2a2a"),
        }
        light, dark_c = palette.get(kind, ("#f5f5f5", "#2a2a2a"))
        return QColor(dark_c if dark else light)

    def _rt_tick(self):
        on_tab = self.tabs.currentWidget() is getattr(self, "rt_tab", None)
        if not self._rt_active:
            if on_tab:
                self._rt_update_countdowns()
            return
        if self._human_verification_required:
            self.rt_status_label.setText("Durum: Doğrulama bekleniyor — duraklatıldı")
            self.rt_status_label.setStyleSheet("font-size: 10px; color: #cc4444;")
            if on_tab:
                self._rt_update_countdowns()
            return
        if not self.browser:
            return
        if on_tab:
            self._rt_update_countdowns()

        import time
        now = time.time()
        for vid, state in list(self._rt_village_states.items()):
            if not self._rt_is_village_active(vid):
                continue
            if state["processing"]:
                continue
            if state["next_fire"] <= now:
                self._rt_process_village(vid)

    def _rt_update_countdowns(self):
        import time
        now = time.time()
        for vid, state in self._rt_village_states.items():
            row = state.get("row")
            if not row:
                continue
            if state.get("processing"):
                continue
            nf = state.get("next_fire", 0.0)
            remain = nf - now
            if remain > 1:
                mins = int(remain) // 60
                secs = int(remain) % 60
                row.setText(3, f"{mins:02d}:{secs:02d}")
            else:
                row.setText(3, "—")

    def _rt_process_village(self, vid):
        state = self._rt_village_states.get(str(vid))
        if not state or not self.browser:
            return
        units = self._rt_get_village_units(vid)
        if not units:
            return

        state["next_index"] = state["next_index"] % len(units)
        unit_key, unit_name = units[state["next_index"]]
        state["processing"] = True
        csrf = self._game_data.get("csrf", "")
        vid_str = str(vid)
        js_global = "__tw_rt_" + vid_str

        row = state["row"]
        row.setText(2, unit_name + "…")
        row.setText(4, "Kontrol ediliyor…")
        row.setForeground(4, QColor("#2d5a9e"))

        fetch_js = (
            "(function() {"
            "window['" + js_global + "'] = 'CHECKING';"
            "var vid = '" + vid_str + "';"
            "var unitKey = '" + unit_key + "';"
            "var csrf = '" + csrf + "';"
            "fetch('/game.php?village=' + vid + '&screen=train&mode=train', {credentials: 'same-origin'})"
            ".then(function(r) { return r.text(); })"
            ".then(function(html) {"
            "  var doc = new DOMParser().parseFromString(html, 'text/html');"
            "  var inp = doc.getElementById(unitKey + '_0');"
            "  if (!inp) { window['" + js_global + "'] = 'NO_UNIT|' + unitKey; return; }"
            "  var ispan = doc.getElementById(unitKey + '_0_interaction');"
            "  if (ispan && ispan.style.display === 'none') {"
            "    window['" + js_global + "'] = 'BLOCKED|' + unitKey; return;"
            "  }"
            "  var nowSec = Math.floor(Date.now() / 1000);"
            "  var endTimes = [];"
            "  var allEnd = doc.querySelectorAll('[data-endtime]');"
            "  for (var ai = 0; ai < allEnd.length; ai++) {"
            "    var ex = parseInt(allEnd[ai].getAttribute('data-endtime'), 10);"
            "    if (ex > nowSec) endTimes.push(ex);"
            "  }"
            "  var earliest = 0;"
            "  for (var ci = 0; ci < endTimes.length; ci++) {"
            "    if (earliest === 0 || endTimes[ci] < earliest) earliest = endTimes[ci];"
            "  }"
            "  if (earliest > 0) { window['" + js_global + "'] = 'BUSY|' + (earliest - nowSec); return; }"
            "  var freshCsrf = csrf;"
            "  var scripts = doc.querySelectorAll('script');"
            "  for (var si = 0; si < scripts.length; si++) {"
            "    var m = scripts[si].textContent.match(/\"csrf\":\"([^\"]+)\"/);"
            "    if (m) { freshCsrf = m[1]; break; }"
            "  }"
            "  var buildTime = 0;"
            "  for (var sj = 0; sj < scripts.length; sj++) {"
            "    var txt = scripts[sj].textContent;"
            "    var pat = new RegExp(unitKey + ':\\\\s*\\\\{[^}]*build_time:\\\\s*([\\\\d.]+)');"
            "    var m2 = txt.match(pat);"
            "    if (m2) { buildTime = parseFloat(m2[1]); break; }"
            "  }"
            "  var allUnits = ['spear','sword','axe','archer','spy','light','marcher','heavy','ram','catapult','knight','snob'];"
            "  var body = '';"
            "  for (var ui = 0; ui < allUnits.length; ui++) {"
            "    var u = allUnits[ui];"
            "    var v = (u === unitKey) ? '1' : '';"
            "    if (body) body += '&';"
            "    body += encodeURIComponent(u) + '=' + encodeURIComponent(v);"
            "  }"
            "  window['" + js_global + "'] = 'POSTING|' + unitKey + '|' + buildTime;"
            "  return fetch("
            "    '/game.php?village=' + vid + '&screen=train&action=train&mode=train&h=' + freshCsrf,"
            "    {method: 'POST', credentials: 'same-origin',"
            "     headers: {'Content-Type': 'application/x-www-form-urlencoded'},"
            "     body: body}"
            "  );"
            "})"
            ".then(function(r) {"
            "  var cur = window['" + js_global + "'] || '';"
            "  if (!cur.startsWith('POSTING')) return;"
            "  if (!r) { window['" + js_global + "'] = 'ERROR|no response'; return; }"
            "  return r.text();"
            "})"
            ".then(function(resultHtml) {"
            "  var cur = window['" + js_global + "'] || '';"
            "  if (!cur.startsWith('POSTING')) return;"
            "  var buildTime = parseFloat((cur.split('|')[2]) || '0') || 0;"
            "  if (!resultHtml) { window['" + js_global + "'] = 'ERROR|empty html'; return; }"
            "  var doc2 = new DOMParser().parseFromString(resultHtml, 'text/html');"
            "  var errEl = doc2.querySelector('.error_box, p.error, .error');"
            "  if (errEl) {"
            "    window['" + js_global + "'] = 'ERROR|' + errEl.textContent.trim().substring(0,80);"
            "  } else {"
            "    window['" + js_global + "'] = 'TRAINED|' + buildTime;"
            "  }"
            "})"
            ".catch(function(err) {"
            "  window['" + js_global + "'] = 'ERROR|' + String(err);"
            "});"
            "})();"
        )

        self.browser.page().runJavaScript(fetch_js)
        self._rt_poll_village(vid, js_global, unit_key, unit_name, 0)

    def _rt_poll_village(self, vid, js_global, unit_key, unit_name, attempt):
        state = self._rt_village_states.get(str(vid))

        if attempt > 80 or not state:
            if state:
                state["processing"] = False
                state["next_fire"] = __import__("time").time() + 15
                row = state.get("row")
                if row:
                    row.setText(4, "Zaman aşımı — 15sn sonra tekrar")
                    row.setForeground(4, QColor("#cc4444"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("timeout"))
            return

        def on_poll(result):
            st = self._rt_village_states.get(str(vid))
            if not st:
                return
            result_str = str(result) if result else "WAITING"

            if result_str in ("WAITING", "CHECKING") or result_str.startswith("POSTING"):
                QTimer.singleShot(350, lambda: self._rt_poll_village(
                    vid, js_global, unit_key, unit_name, attempt + 1))
                return

            self.browser.page().runJavaScript("window['" + js_global + "'] = null;")
            st["processing"] = False
            row = st.get("row")

            import time
            now = time.time()

            if result_str.startswith("TRAINED|"):
                build_time_sec = 0.0
                try:
                    build_time_sec = float(result_str.split("|")[1])
                except (IndexError, ValueError):
                    pass
                village_units = self._rt_get_village_units(vid)
                st["next_index"] = (st["next_index"] + 1) % max(len(village_units), 1)
                wake_sec = build_time_sec + 2 if build_time_sec > 0 else 60
                st["next_fire"] = now + wake_sec
                mins = int(wake_sec) // 60
                secs = int(wake_sec) % 60
                if row:
                    row.setText(2, unit_name)
                    row.setText(4, f"✅ {unit_name} eğitimde — {mins:02d}:{secs:02d}")
                    row.setForeground(4, QColor("#66cc66" if getattr(self, "_dark_mode", False) else "#228822"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("trained"))
                self._add_log("ASKER", "success",
                    f"✅ {unit_name} ×1 → köy {vid} — ~{mins}dk {secs}sn")

            elif result_str.startswith("BUSY|"):
                remain = 60
                try:
                    remain = int(result_str.split("|")[1])
                except (IndexError, ValueError):
                    pass
                st["next_fire"] = now + remain + 2
                mins = remain // 60
                secs = remain % 60
                if row:
                    row.setText(4, f"⏳ Kuyruk dolu — {mins:02d}:{secs:02d}")
                    row.setForeground(4, QColor("#cc9933" if getattr(self, "_dark_mode", False) else "#aa6600"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("busy"))
                self._add_log("ASKER", "info",
                    f"Köy {vid} kuyruk dolu — {mins}dk {secs}sn bekle")

            elif result_str.startswith("BLOCKED|") or result_str.startswith("NO_UNIT|"):
                import random as _rnd2
                village_units = self._rt_get_village_units(vid)
                st["next_index"] = (st["next_index"] + 1) % max(len(village_units), 1)

                is_no_unit = result_str.startswith("NO_UNIT|")
                if is_no_unit:
                    # Birim bu dünyada mevcut değil — ~1 saat bekle
                    wait_sec = _rnd2.randint(3300, 3900)
                    reason = "bu dünyada mevcut değil"
                else:
                    # Farm dolu / hammadde yetersiz — 9-11 dakika bekle
                    wait_sec = _rnd2.randint(540, 660)
                    reason = "hammadde/farm yetersiz"

                st["next_fire"] = now + wait_sec
                mins = wait_sec // 60
                secs = wait_sec % 60
                if row:
                    row.setText(4, f"⏳ {unit_name} — {reason} ({mins:02d}:{secs:02d} sonra)")
                    row.setForeground(4, QColor("#cc9933" if getattr(self, "_dark_mode", False) else "#aa6600"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("neutral"))
                self._add_log("ASKER", "warn",
                    f"Köy {vid}: {unit_name} — {reason}, {mins}dk {secs}sn sonra tekrar denenir")

            elif result_str.startswith("ERROR|"):
                msg = result_str[6:]
                st["next_fire"] = now + 20
                if row:
                    row.setText(4, f"Hata: {msg[:50]}")
                    row.setForeground(4, QColor("#ff6666" if getattr(self, "_dark_mode", False) else "#cc4444"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("error"))
                self._add_log("ASKER", "error", f"Köy {vid} hata: {msg}")

            else:
                st["next_fire"] = now + 15

        self.browser.page().runJavaScript("window['" + js_global + "'] || 'WAITING';", on_poll)


    # ── GELEN SALDIRILAR (overview_villages &mode=incomings) ──

    def _build_incomings_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.incomings_hint = QLabel(
            "Gelen komut listesi, oturum açık gömülü tarayıcı çerezleriyle "
            "<b>game.php?…&amp;screen=overview_villages&amp;mode=incomings</b> sayfasından çekilir. "
            "Filtreler oyundaki «Gelen» genel bakış menüsüyle uyumludur."
        )
        self.incomings_hint.setWordWrap(True)
        self.incomings_hint.setStyleSheet("font-size: 10px; color: #555; padding: 4px;")
        layout.addWidget(self.incomings_hint)

        bar = QHBoxLayout()
        bar.setSpacing(8)

        bar.addWidget(QLabel("Görünüm:"))
        self.incomings_type_combo = QComboBox()
        self.incomings_type_combo.setMinimumWidth(170)
        for label, t in [
            ("Hepsi", "all"),
            ("Göz ardı edilenler", "ignored"),
            ("Göz ardı edilmeyenler", "unignored"),
        ]:
            self.incomings_type_combo.addItem(label, t)
        bar.addWidget(self.incomings_type_combo)

        bar.addWidget(QLabel("Alt tür:"))
        self.incomings_subtype_combo = QComboBox()
        self.incomings_subtype_combo.setMinimumWidth(120)
        for label, st in [
            ("Hepsi", "all"),
            ("Saldırılar", "attacks"),
            ("Destek", "supports"),
        ]:
            self.incomings_subtype_combo.addItem(label, st)
        bar.addWidget(self.incomings_subtype_combo)

        self.incomings_refresh_btn = QPushButton("🔄 Gelenleri Yükle")
        self.incomings_refresh_btn.setObjectName("startBtn")
        self.incomings_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.incomings_refresh_btn.clicked.connect(lambda: self._incomings_refresh(silent=False))
        bar.addWidget(self.incomings_refresh_btn)

        self.incomings_open_btn = QPushButton("🌐 Seçileni tarayıcıda aç")
        self.incomings_open_btn.setCursor(Qt.PointingHandCursor)
        self.incomings_open_btn.clicked.connect(self._incomings_open_selected)
        bar.addWidget(self.incomings_open_btn)

        bar.addStretch()
        self.incomings_status_label = QLabel("Hazır.")
        self.incomings_status_label.setStyleSheet("font-size: 10px; color: #666;")
        bar.addWidget(self.incomings_status_label)
        layout.addLayout(bar)

        self.incomings_tree = QTreeWidget()
        self.incomings_tree.setAlternatingRowColors(True)
        self.incomings_tree.setRootIsDecorated(False)
        self.incomings_tree.setHeaderLabels(
            [
                "Tür / tahmini en yavaş",
                "Komut / etiket",
                "Hedef",
                "Kaynak",
                "Oyuncu",
                "Mesafe",
                "Varış zamanı",
                "Varış süresi · GK · kalan",
            ]
        )
        self.incomings_tree.header().setSectionResizeMode(QHeaderView.Interactive)
        for i in range(8):
            self.incomings_tree.setColumnWidth(i, 110 if i < 6 else 140)
        self.incomings_tree.setColumnWidth(2, 180)
        self.incomings_tree.setColumnWidth(3, 180)
        self.incomings_tree.itemDoubleClicked.connect(
            lambda *_: self._incomings_open_selected()
        )
        layout.addWidget(self.incomings_tree, 1)

        self._incomings_tick_origin_mono = None
        self._incomings_countdown_timer = QTimer(self)
        self._incomings_countdown_timer.setInterval(100)
        self._incomings_countdown_timer.timeout.connect(self._incomings_tick_countdowns)
        self._incomings_countdown_timer.start()

        self._incomings_refresh_silent = False
        self._incomings_pending_auto_label = False
        self._incomings_reschedule_after_this_fetch = False
        self._incomings_auto_timer = QTimer(self)
        self._incomings_auto_timer.setSingleShot(True)
        self._incomings_auto_timer.timeout.connect(self._incomings_auto_refresh_tick)
        self._incomings_schedule_next_auto_refresh()

        self.incomings_foot = QLabel(
            "İlk sayfadaki gelen komutlar listelenir; 60–80 saniye aralığında rastgele bir zamanda sessizce yenilenir "
            "(loga yazılmaz). Mesafe ve yol süresinden tahmini en yavaş birim «Komut / etiket» ve «Tür» sütunlarında "
            "gösterilir; oyun içi komut etiketi boş olan saldırı/desteklerde aynı tahmin otomatik kaydedilir (elle "
            "yazılmış etiketlere dokunulmaz)."
        )
        self.incomings_foot.setWordWrap(True)
        self.incomings_foot.setStyleSheet("font-size: 9px; color: #888;")
        layout.addWidget(self.incomings_foot)

        self.tabs.addTab(tab, "🛡️ Gelen Saldırılar")

    def _incomings_format_remaining_ms(self, ms):
        """Kalan süreyi Ordu Gönder / dispatch ile uyumlu kısa metin (saat varsa H:MM:SS.mmm)."""
        if ms is None:
            return ""
        try:
            v = int(round(float(ms)))
        except (TypeError, ValueError):
            return ""
        sign = ""
        if v < 0:
            sign = "-"
            v = -v
        h = v // 3600000
        v %= 3600000
        m = v // 60000
        v %= 60000
        s = v // 1000
        rms = v % 1000
        if h:
            return sign + f"{h}:{m:02d}:{s:02d}.{rms:03d}"
        return sign + f"{m}:{s:02d}.{rms:03d}"

    def _incomings_try_parse_arrival_remaining_ms(self, cells):
        """Hücrelerde '20.03'de 20:45:24:208' formatı varsa sunucu saatine göre kalan ms."""
        if not self._server_time_synced:
            return None
        srv = self._server_now_dt()
        if not srv:
            return None
        for c in cells:
            if not isinstance(c, str):
                continue
            dt = self._dispatch_parse_time_str(c.strip())
            if dt:
                try:
                    return (dt - srv).total_seconds() * 1000.0
                except (TypeError, ValueError, OverflowError):
                    continue
        return None

    def _incomings_parse_source_xy(self, cells, dest_x, dest_y):
        """Kaynak köyün x|y — Gelen tablosunda önce Kaynak (3. veri sütunu), sonra diğer hücreler."""
        if dest_x is None or dest_y is None:
            return None, None
        try:
            dx, dy = int(dest_x), int(dest_y)
        except (TypeError, ValueError):
            return None, None

        def pair_from(txt):
            if not txt:
                return None
            m = re.search(r"(\d+)\s*\|\s*(\d+)", str(txt))
            if not m:
                return None
            return int(m.group(1)), int(m.group(2))

        prefer = (2, 1, 3, 4, 5, 6, 7, 0)
        for idx in prefer:
            if idx < len(cells):
                p = pair_from(cells[idx])
                if p and (p[0] != dx or p[1] != dy):
                    return p[0], p[1]

        txt = " ".join(str(c) for c in cells)
        for m in re.finditer(r"(\d+)\s*\|\s*(\d+)", txt):
            x, y = int(m.group(1)), int(m.group(2))
            if x == dx and y == dy:
                continue
            return x, y
        return None, None

    def _incomings_parse_duration_sec_from_cells(self, cells):
        """Hücrelerdeki H:MM:SS veya HH:MM:SS biçiminden en uzun süreyi saniye olarak al."""
        best = None
        for c in cells:
            if not isinstance(c, str):
                continue
            for m in re.finditer(r"\b(\d{1,3}):(\d{2}):(\d{2})\b", c):
                h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if h > 200:
                    continue
                sec = h * 3600 + mi * 60 + s
                if sec <= 0:
                    continue
                if best is None or sec > best:
                    best = sec
        return best

    def _incomings_travel_seconds_from_row(self, row_dict, cells):
        """Gönderim–varış zamanı (ms) veya tablodaki toplam süre metni."""
        s_ms = row_dict.get("send_start_ms")
        e_ms = row_dict.get("arrival_end_ms")
        if s_ms is not None and e_ms is not None:
            try:
                dt = (float(e_ms) - float(s_ms)) / 1000.0
                if 25 <= dt <= 30 * 24 * 3600:
                    return dt
            except (TypeError, ValueError):
                pass
        return self._incomings_parse_duration_sec_from_cells(cells)

    def _incomings_infer_slowest_unit_label(self, distance, travel_sec):
        """TW formülünün tersi: yol süresi + mesafe → en yakın birim hızı (dakika/kare)."""
        if distance is None or travel_sec is None:
            return None
        try:
            d = float(distance)
            t = float(travel_sec)
        except (TypeError, ValueError):
            return None
        if d <= 0 or t <= 0:
            return None
        try:
            ws = float(self._game_data.get("world_speed", 1) or 1)
            us = float(self._game_data.get("unit_speed", 1) or 1)
        except (TypeError, ValueError):
            ws, us = 1.0, 1.0
        s_need = t * ws * us / (60.0 * d)
        best_key = None
        best_d = 1e9
        for key, spd in self.UNIT_SPEEDS.items():
            diff = abs(float(spd) - s_need)
            if diff < best_d:
                best_d = diff
                best_key = key
        if best_key is None or best_d > 3.2:
            return None
        return self.INCOMINGS_UNIT_TR.get(best_key, best_key)

    def _incomings_cell_is_unlabeled(self, cell0):
        """Sunucuya otomatik etiket yazılabilir mi — elle yazılmış metin varsa dokunma."""
        s = (cell0 or "").strip()
        if not s:
            return True
        if s in ("—", "-", "–", "―"):
            return True
        return False

    def _incomings_post_command_label_job(self, job):
        """Gelen komut satırına TW QuickEdit ile tahmini en yavaş birim etiketini kaydet."""
        if not getattr(self, "browser", None):
            return
        vid = (job.get("village_id") or "").strip()
        cid = (job.get("command_id") or "").strip()
        typ = (job.get("command_type") or "other").strip()
        label = (job.get("label") or "").strip()
        csrf = (self._game_data.get("csrf") or "").strip()
        if not (vid and cid and label and csrf):
            return
        vid_j = json.dumps(vid)
        cid_j = json.dumps(cid)
        typ_j = json.dumps(typ)
        lbl_j = json.dumps(label)
        csrf_j = json.dumps(csrf)
        fetch_js = f"""
        (function() {{
            var vid = {vid_j};
            var cid = {cid_j};
            var typ = {typ_j};
            var txt = {lbl_j};
            var h = {csrf_j};
            var url = '/game.php?village=' + encodeURIComponent(vid) +
                '&screen=info_command&id=' + encodeURIComponent(cid) +
                '&type=' + encodeURIComponent(typ) +
                '&ajaxaction=edit_other_comment&h=' + encodeURIComponent(h);
            var fd = new URLSearchParams();
            fd.append('text', txt);
            fetch(url, {{
                method: 'POST',
                credentials: 'same-origin',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'X-Requested-With': 'XMLHttpRequest'
                }},
                body: fd.toString()
            }}).catch(function() {{}});
        }})();
        """
        self.browser.page().runJavaScript(fetch_js)

    def _incomings_tick_countdowns(self):
        """Devamı sütununda fetch anındaki kalan süreyi monotonic ile ilerleterek gösterir."""
        if not hasattr(self, "incomings_tree"):
            return
        origin = getattr(self, "_incomings_tick_origin_mono", None)
        if origin is None:
            return
        elapsed_ms = (time.monotonic() - origin) * 1000.0
        for i in range(self.incomings_tree.topLevelItemCount()):
            item = self.incomings_tree.topLevelItem(i)
            pay = item.data(0, Qt.UserRole)
            if not isinstance(pay, dict):
                continue
            rest = (pay.get("rest_text") or "").strip()
            rem0 = pay.get("remaining_ms_at_fetch")
            if rem0 is None:
                item.setText(7, rest if rest else "—")
                continue
            try:
                rem = float(rem0) - elapsed_ms
            except (TypeError, ValueError):
                item.setText(7, rest if rest else "—")
                continue
            cnt = self._incomings_format_remaining_ms(rem)
            if rest:
                item.setText(7, f"{rest}\nKalan: {cnt}")
            else:
                item.setText(7, "Kalan: " + cnt)

    def _incomings_schedule_next_auto_refresh(self):
        """60–80 sn sonra bir sonraki sessiz yenilemeyi tetikle (tek atış)."""
        delay_ms = random.randint(60_000, 80_000)
        self._incomings_auto_timer.stop()
        self._incomings_auto_timer.start(delay_ms)

    def _incomings_after_fetch_cycle_cleanup(self):
        """Yükleme döngüsü bitince sessiz bayrakları ve otomatik zamanlayıcı tekrarını güncelle."""
        self._incomings_refresh_silent = False
        if getattr(self, "_incomings_reschedule_after_this_fetch", False):
            self._incomings_reschedule_after_this_fetch = False
            self._incomings_schedule_next_auto_refresh()

    def _incomings_auto_refresh_tick(self):
        """Planlı: gelen listesini sessizce yenile; ardından etiketsiz komutları sunucuda işaretle."""
        if not getattr(self, "browser", None):
            self._incomings_after_fetch_cycle_cleanup()
            return
        if not self.incomings_refresh_btn.isEnabled():
            self._incomings_after_fetch_cycle_cleanup()
            return
        village_id = self._game_data.get("village", {}).get("id", "") or ""
        if not village_id:
            self._incomings_after_fetch_cycle_cleanup()
            return
        self._incomings_pending_auto_label = True
        self._incomings_reschedule_after_this_fetch = True
        self._incomings_refresh(silent=True)

    def _incomings_refresh(self, silent=False):
        """Aktif köy için overview_villages&mode=incomings HTML'ini çekip #incomings_table ayrıştır."""
        self._incomings_refresh_silent = bool(silent)
        if not self.browser:
            if not silent:
                QMessageBox.warning(self, "Gelen Saldırılar", "Tarayıcı hazır değil.")
            self._incomings_after_fetch_cycle_cleanup()
            return

        village_id = self._game_data.get("village", {}).get("id", "") or ""
        if not village_id:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Gelen Saldırılar",
                    "Aktif köy ID bulunamadı.\n"
                    "Önce oyunda giriş yapıp bir köye gidin; köy verisi senkron olunca tekrar deneyin.",
                )
            self._incomings_after_fetch_cycle_cleanup()
            return

        t = self.incomings_type_combo.currentData()
        st = self.incomings_subtype_combo.currentData()
        if t is None or t == "":
            t = "all"
        if st is None or st == "":
            st = "all"
        t = str(t)
        st = str(st)

        self.incomings_refresh_btn.setEnabled(False)
        self.incomings_refresh_btn.setText("Yükleniyor…")
        self.incomings_status_label.setText("Sunucudan gelen komutlar isteniyor…")
        if not silent:
            self._add_log("GELEN", "info", "Gelen komut listesi yükleniyor…")

        fetch_js = """
        (function() {
            window.__tw_incomings_fetch = 'LOADING';
            var villageId = """ + json.dumps(str(village_id)) + """;
            var typeP = """ + json.dumps(t) + """;
            var subtypeP = """ + json.dumps(st) + """;
            var url = '/game.php?village=' + encodeURIComponent(villageId) +
                '&screen=overview_villages&mode=incomings&group=0' +
                '&type=' + encodeURIComponent(typeP || 'all') +
                '&subtype=' + encodeURIComponent(subtypeP || 'all');

            function rowKind(row) {
                var h = (row.innerHTML || '').toLowerCase();
                if (h.indexOf('command/support') >= 0) return 'Destek';
                if (h.indexOf('command/return') >= 0) return 'Dönüş';
                if (h.indexOf('command/attack') >= 0 || h.indexOf('attack_small') >= 0
                    || h.indexOf('attack_medium') >= 0 || h.indexOf('attack_large') >= 0) return 'Saldırı';
                if (h.indexOf('command/spy') >= 0) return 'Casus';
                if (h.indexOf('command/snob') >= 0) return 'Misyoner';
                return '—';
            }

            function pickHref(row) {
                var as = row.querySelectorAll('a[href]');
                var i, u;
                for (i = 0; i < as.length; i++) {
                    u = as[i].getAttribute('href') || '';
                    if (u.indexOf('info_command') >= 0) return u;
                }
                for (i = 0; i < as.length; i++) {
                    u = as[i].getAttribute('href') || '';
                    if (u.indexOf('game.php') >= 0 && (u.indexOf('screen=place') >= 0 || u.indexOf('target=') >= 0))
                        return u;
                }
                var a0 = row.querySelector('a[href]');
                return a0 ? (a0.getAttribute('href') || '') : '';
            }

            function parseCommandMeta(href) {
                var id = null, typ = 'other';
                if (!href) return { command_id: null, command_type: typ };
                var m = /[?&]id=(\\d+)/.exec(href);
                if (m) id = m[1];
                var m2 = /[?&]type=([^&]+)/.exec(href);
                if (m2) {
                    try { typ = decodeURIComponent(m2[1]); } catch (e) { typ = m2[1]; }
                }
                return { command_id: id, command_type: typ };
            }

            function serverNowMs() {
                if (typeof Timing !== 'undefined' && typeof Timing.getCurrentServerTime === 'function') {
                    try {
                        var gv = Timing.getCurrentServerTime();
                        gv = Math.floor(Number(gv));
                        if (gv > 0 && gv < 1e12) gv *= 1000;
                        return gv;
                    } catch (e) {}
                }
                if (typeof Timing !== 'undefined' && Timing.initial_server_time && Timing.pagehit_at) {
                    var t0 = Timing.initial_server_time;
                    if (t0 < 1e12) t0 *= 1000;
                    return Math.floor(t0 + (Date.now() - Timing.pagehit_at));
                }
                if (typeof Timing !== 'undefined' && typeof Timing.offset_from_server !== 'undefined') {
                    return Math.floor(Date.now() - Timing.offset_from_server);
                }
                return Date.now();
            }

            /** TW: data-endtime bazen saniye (1e9…1e10), bazen ms (1e12+). */
            function normalizeUnixToMs(v) {
                var n = parseInt(v, 10);
                if (isNaN(n) || n <= 0) return null;
                if (n >= 1e12) return n;
                if (n >= 1e9 && n < 1e12) return n * 1000;
                return null;
            }

            function pickArrivalEndMs(row) {
                var els = row.querySelectorAll('[data-endtime]');
                var i, el, ms;
                for (i = 0; i < els.length; i++) {
                    el = els[i];
                    ms = normalizeUnixToMs(el.getAttribute('data-endtime'));
                    if (ms !== null) return ms;
                }
                els = row.querySelectorAll('[data-arrival]');
                for (i = 0; i < els.length; i++) {
                    el = els[i];
                    ms = normalizeUnixToMs(el.getAttribute('data-arrival'));
                    if (ms !== null) return ms;
                }
                return null;
            }

            function pickSendStartMs(row) {
                var els = row.querySelectorAll('[data-starttime],[data-sendtime],[data-sent],[data-outward]');
                var i, el, ms;
                for (i = 0; i < els.length; i++) {
                    el = els[i];
                    ms = normalizeUnixToMs(
                        el.getAttribute('data-starttime')
                        || el.getAttribute('data-sendtime')
                        || el.getAttribute('data-sent')
                        || el.getAttribute('data-outward')
                    );
                    if (ms !== null) return ms;
                }
                return null;
            }

            var gv = (typeof game_data !== 'undefined' && game_data.village) ? game_data.village : {};
            var destX = (gv.x !== undefined && gv.x !== null && !isNaN(Number(gv.x))) ? Number(gv.x) : null;
            var destY = (gv.y !== undefined && gv.y !== null && !isNaN(Number(gv.y))) ? Number(gv.y) : null;

            fetch(url, {credentials: 'same-origin'})
            .then(function(r) { return r.text(); })
            .then(function(html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var table = doc.getElementById('incomings_table')
                    || doc.querySelector('table#incomings_table');
                if (!table) {
                    window.__tw_incomings_fetch = JSON.stringify({
                        status: 'OK',
                        rows: [],
                        fetchUrl: url,
                        parseNote: 'no #incomings_table in HTML'
                    });
                    return;
                }
                var rows = table.querySelectorAll('tr');
                var out = [];
                var r, row, tds, texts, j, td, href, kind, sn, arrEnd, remMs, sendMs;
                sn = serverNowMs();
                for (r = 0; r < rows.length; r++) {
                    row = rows[r];
                    if (row.querySelector('th')) continue;
                    tds = row.querySelectorAll('td');
                    if (tds.length === 0) continue;
                    texts = [];
                    for (j = 0; j < tds.length; j++) {
                        td = tds[j];
                        texts.push((td.textContent || '').replace(/\\s+/g, ' ').trim());
                    }
                    href = pickHref(row);
                    var cm = parseCommandMeta(href);
                    kind = rowKind(row);
                    arrEnd = pickArrivalEndMs(row);
                    sendMs = pickSendStartMs(row);
                    remMs = (arrEnd !== null && !isNaN(arrEnd)) ? (arrEnd - sn) : null;
                    out.push({
                        cells: texts,
                        href: href,
                        kind: kind,
                        arrival_end_ms: arrEnd,
                        send_start_ms: sendMs,
                        dest_x: destX,
                        dest_y: destY,
                        remaining_ms_at_fetch: remMs,
                        command_id: cm.command_id,
                        command_type: cm.command_type
                    });
                }
                window.__tw_incomings_fetch = JSON.stringify({
                    status: 'OK',
                    rows: out,
                    fetchUrl: url,
                    parseNote: '#incomings_table tr (no th)'
                });
            })
            .catch(function(err) {
                window.__tw_incomings_fetch = JSON.stringify({
                    status: 'ERROR', message: String(err)
                });
            });
        })();
        """

        self.browser.page().runJavaScript(fetch_js)
        QTimer.singleShot(120, lambda: self._incomings_poll_load(0))

    def _incomings_poll_load(self, attempt):
        max_attempts = 140
        if attempt >= max_attempts:
            self.incomings_refresh_btn.setEnabled(True)
            self.incomings_refresh_btn.setText("🔄 Gelenleri Yükle")
            self.incomings_status_label.setText("Zaman aşımı.")
            if not getattr(self, "_incomings_refresh_silent", False):
                self._add_log("GELEN", "error", "Gelen komutlar: zaman aşımı (fetch yanıt vermedi).")
            self._incomings_after_fetch_cycle_cleanup()
            self.browser.page().runJavaScript("window.__tw_incomings_fetch=null;")
            return

        check_js = (
            "(function(){ var x = window.__tw_incomings_fetch; "
            "if (x === undefined || x === null) return 'WAITING'; return x; })();"
        )

        def on_poll(result):
            if result is None:
                result_str = "WAITING"
            else:
                result_str = str(result).strip()

            if result_str in ("WAITING", "LOADING", ""):
                QTimer.singleShot(120, lambda: self._incomings_poll_load(attempt + 1))
                return

            self.browser.page().runJavaScript("window.__tw_incomings_fetch=null;")
            self._incomings_apply_fetch_result(result_str)

        self.browser.page().runJavaScript(check_js, on_poll)

    def _incomings_apply_fetch_result(self, result_str):
        self.incomings_refresh_btn.setEnabled(True)
        self.incomings_refresh_btn.setText("🔄 Gelenleri Yükle")
        silent = getattr(self, "_incomings_refresh_silent", False)
        do_auto_label = silent and getattr(self, "_incomings_pending_auto_label", False)
        self._incomings_pending_auto_label = False
        label_jobs = []
        vid_cur = str(self._game_data.get("village", {}).get("id") or "")
        try:
            if not result_str or result_str in ("WAITING", "LOADING"):
                self._incomings_tick_origin_mono = None
                self.incomings_status_label.setText("Yanıt yok.")
                if not silent:
                    self._add_log("GELEN", "error", "Gelen komutlar: boş yanıt.")
                return

            try:
                data = json.loads(result_str)
            except Exception:
                self._incomings_tick_origin_mono = None
                self.incomings_status_label.setText("Parse hatası.")
                if not silent:
                    self._add_log("GELEN", "error", "Gelen komutlar JSON parse edilemedi.")
                return

            if data.get("status") == "ERROR":
                self._incomings_tick_origin_mono = None
                msg = data.get("message", "?")
                self.incomings_status_label.setText("Hata: " + str(msg)[:80])
                if not silent:
                    self._add_log("GELEN", "error", f"Gelen yükleme: {msg}")
                return

            rows = data.get("rows", [])
            self.incomings_tree.clear()
            if not rows:
                self._incomings_tick_origin_mono = None
            else:
                self._incomings_tick_origin_mono = time.monotonic()

            for r in rows:
                kind = str(r.get("kind", "—"))
                cells = r.get("cells") or []
                if not isinstance(cells, list):
                    cells = []

                dest_x = r.get("dest_x")
                dest_y = r.get("dest_y")
                sx, sy = self._incomings_parse_source_xy(cells, dest_x, dest_y)
                dist = None
                if sx is not None and sy is not None and dest_x is not None and dest_y is not None:
                    try:
                        dist = math.sqrt(
                            (float(sx) - float(dest_x)) ** 2 + (float(sy) - float(dest_y)) ** 2
                        )
                    except (TypeError, ValueError):
                        dist = None

                travel_sec = self._incomings_travel_seconds_from_row(r, cells)
                slow_label = None
                if dist is not None and travel_sec is not None:
                    slow_label = self._incomings_infer_slowest_unit_label(dist, travel_sec)

                klow = kind.lower()
                is_march = (
                    "sald" in klow
                    or "attack" in klow
                    or "destek" in klow
                    or "support" in klow
                )
                disp_kind = slow_label if (slow_label and is_march) else kind

                orig_komut = str(cells[0]).strip() if len(cells) > 0 else ""
                if slow_label and is_march:
                    komut_txt = slow_label
                    if orig_komut and orig_komut != slow_label:
                        komut_txt = f"{slow_label} · {orig_komut}"
                elif orig_komut:
                    komut_txt = orig_komut[:237] + ("…" if len(orig_komut) > 237 else "")
                else:
                    komut_txt = "—"
                if len(komut_txt) > 240:
                    komut_txt = komut_txt[:237] + "…"

                command_id = r.get("command_id")
                if (
                    do_auto_label
                    and slow_label
                    and is_march
                    and command_id
                    and vid_cur
                    and self._incomings_cell_is_unlabeled(orig_komut)
                ):
                    label_jobs.append(
                        {
                            "village_id": vid_cur,
                            "command_id": str(command_id),
                            "command_type": str(r.get("command_type") or "other"),
                            "label": slow_label,
                        }
                    )

                col = [disp_kind, komut_txt]
                for i in range(1, 6):
                    col.append(str(cells[i]) if i < len(cells) else "—")
                rest = " | ".join(str(cells[j]) for j in range(6, len(cells))) if len(cells) > 6 else ""
                rest_disp = rest if rest else "—"

                rem = r.get("remaining_ms_at_fetch")
                if rem is None:
                    rem = self._incomings_try_parse_arrival_remaining_ms(cells)

                col.append(rest_disp)
                item = QTreeWidgetItem(col)
                href = (r.get("href") or "").strip()
                item.setData(
                    0,
                    Qt.UserRole,
                    {
                        "href": href,
                        "kind": kind,
                        "remaining_ms_at_fetch": rem,
                        "rest_text": rest,
                        "slowest_guess": slow_label,
                        "command_id": command_id,
                    },
                )
                if rem is not None:
                    item.setText(
                        7,
                        f"{rest_disp}\nKalan: {self._incomings_format_remaining_ms(float(rem))}",
                    )
                else:
                    item.setText(7, rest_disp)
                tip = " | ".join(str(c) for c in cells[:10])
                if tip:
                    item.setToolTip(1, tip)
                if "sald" in klow or "attack" in klow:
                    item.setForeground(0, QColor("#cc4444"))
                elif "destek" in klow or "support" in klow:
                    item.setForeground(0, QColor("#228822"))
                self.incomings_tree.addTopLevelItem(item)

            n = len(rows)
            note = (data.get("parseNote") or "").strip()
            base_status = f"{n} satır ({data.get('fetchUrl', '')})" + (f" — {note}" if note else "")
            if silent:
                self.incomings_status_label.setText(base_status + " · otomatik")
            else:
                self.incomings_status_label.setText(base_status)
            if not silent:
                self._add_log(
                    "GELEN",
                    "success",
                    f"Gelen komut listesi güncellendi: {n} satır ({data.get('fetchUrl', '')})",
                )
            for idx, job in enumerate(label_jobs):
                QTimer.singleShot(
                    200 + idx * 450,
                    lambda j=job: self._incomings_post_command_label_job(j),
                )
        finally:
            self._incomings_after_fetch_cycle_cleanup()

    def _incomings_open_selected(self):
        if not self.browser:
            return
        item = self.incomings_tree.currentItem()
        if not item:
            QMessageBox.information(
                self, "Gelen Saldırılar", "Listeden bir satır seçin."
            )
            return

        payload = item.data(0, Qt.UserRole)
        if not isinstance(payload, dict):
            payload = {}

        href = (payload.get("href") or "").strip()

        if not href:
            QMessageBox.warning(
                self,
                "Gelen Saldırılar",
                "Bu satırda oyun bağlantısı yok.\n"
                "Tablo yapısı farklıysa sayfa kaynağından seçici güncellenebilir.",
            )
            return

        base = self.browser.page().url()
        target = QUrl(href)
        if target.isRelative():
            target = base.resolved(target)

        self.browser.load(target)
        self.tabs.setCurrentIndex(0)
        self._add_log("GELEN", "info", f"Tarayıcıda açılıyor: {target.toString()[:120]}")

    # ── BİNA GENEL BAKIŞ (overview_villages &mode=buildings) ──

    def _build_buildings_overview_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.buildings_ov_hint = QLabel(
            "Köy başına oyun içi bina kuyruğu özeti, oturum açık gömülü tarayıcı ile "
            "<b>game.php?…&amp;screen=overview_villages&amp;mode=buildings</b> sayfasından çekilir."
        )
        self.buildings_ov_hint.setWordWrap(True)
        self.buildings_ov_hint.setStyleSheet("font-size: 10px; color: #555; padding: 4px;")
        layout.addWidget(self.buildings_ov_hint)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("Grup:"))
        self.buildings_ov_group_spin = QSpinBox()
        self.buildings_ov_group_spin.setRange(0, 99)
        self.buildings_ov_group_spin.setValue(0)
        self.buildings_ov_group_spin.setFixedWidth(56)
        bar.addWidget(self.buildings_ov_group_spin)

        bar.addWidget(QLabel("Sayfa:"))
        self.buildings_ov_page_spin = QSpinBox()
        self.buildings_ov_page_spin.setRange(0, 99)
        self.buildings_ov_page_spin.setValue(0)
        self.buildings_ov_page_spin.setFixedWidth(56)
        bar.addWidget(self.buildings_ov_page_spin)

        self.buildings_ov_refresh_btn = QPushButton("🔄 Yükle")
        self.buildings_ov_refresh_btn.setObjectName("startBtn")
        self.buildings_ov_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.buildings_ov_refresh_btn.clicked.connect(self._buildings_overview_refresh)
        bar.addWidget(self.buildings_ov_refresh_btn)

        self.buildings_ov_open_btn = QPushButton("🌐 Seçili köyü tarayıcıda aç")
        self.buildings_ov_open_btn.setCursor(Qt.PointingHandCursor)
        self.buildings_ov_open_btn.clicked.connect(self._buildings_overview_open_selected)
        bar.addWidget(self.buildings_ov_open_btn)

        bar.addStretch()
        self.buildings_ov_status_label = QLabel("Hazır.")
        self.buildings_ov_status_label.setStyleSheet("font-size: 10px; color: #666;")
        bar.addWidget(self.buildings_ov_status_label)
        layout.addLayout(bar)

        self.buildings_ov_tree = QTreeWidget()
        self.buildings_ov_tree.setAlternatingRowColors(True)
        self.buildings_ov_tree.setRootIsDecorated(False)
        self.buildings_ov_tree.setHeaderLabels(
            ["Köy ID", "Köy", "Koordinat", "Bina kuyruğu"]
        )
        self.buildings_ov_tree.header().setSectionResizeMode(QHeaderView.Interactive)
        for i, w in enumerate((70, 200, 90, 400)):
            self.buildings_ov_tree.setColumnWidth(i, w)
        self.buildings_ov_tree.itemDoubleClicked.connect(
            lambda *_: self._buildings_overview_open_selected()
        )
        layout.addWidget(self.buildings_ov_tree, 1)

        self.buildings_ov_foot = QLabel(
            "Tablo yapısı dünya sürümüne göre değişebilir; satır yoksa oyun HTML’inde "
            "farklı seçiciler gerekebilir."
        )
        self.buildings_ov_foot.setWordWrap(True)
        self.buildings_ov_foot.setStyleSheet("font-size: 9px; color: #888;")
        layout.addWidget(self.buildings_ov_foot)

        self.tabs.addTab(tab, "🏗️ Bina Genel Bakış")

    def _buildings_overview_refresh(self):
        if not self.browser:
            QMessageBox.warning(self, "Bina Genel Bakış", "Tarayıcı hazır değil.")
            return

        village_id = self._game_data.get("village", {}).get("id", "") or ""
        if not village_id:
            QMessageBox.warning(
                self,
                "Bina Genel Bakış",
                "Aktif köy ID bulunamadı.\nÖnce oyunda giriş yapıp bir köye gidin.",
            )
            return

        group = int(self.buildings_ov_group_spin.value())
        page = int(self.buildings_ov_page_spin.value())

        self.buildings_ov_refresh_btn.setEnabled(False)
        self.buildings_ov_refresh_btn.setText("Yükleniyor…")
        self.buildings_ov_status_label.setText("Sunucudan veri isteniyor…")
        self._add_log("BİNA-ÖZET", "info", "Bina genel bakış yükleniyor…")

        vj = json.dumps(str(village_id))
        gj = json.dumps(str(group))
        pj = json.dumps(str(page))

        fetch_js = f"""
        (function() {{
            window.__tw_buildings_ov_fetch = 'LOADING';
            var villageId = {vj};
            var groupP = {gj};
            var pageP = {pj};
            var url = '/game.php?village=' + encodeURIComponent(villageId) +
                '&screen=overview_villages&mode=buildings&group=' + encodeURIComponent(groupP) +
                '&page=' + encodeURIComponent(pageP);

            fetch(url, {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var table = doc.getElementById('buildings_table')
                    || doc.querySelector('table#buildings_table');
                var root = table || doc.getElementById('villages') || doc.body;
                var rows = root ? root.querySelectorAll('tr[id^="v_"]') : doc.querySelectorAll('tr[id^="v_"]');
                var out = [];
                var i, row, idm, vid, nm, ques, ul, lis, j, li, cordM;
                for (i = 0; i < rows.length; i++) {{
                    row = rows[i];
                    idm = (row.getAttribute('id') || '').match(/^v_(\\d+)/);
                    vid = idm ? idm[1] : '';
                    nm = '';
                    var qel = row.querySelector('.quickedit-label');
                    if (qel) nm = (qel.textContent || '').trim();
                    if (!nm) {{
                        var la = row.querySelector('a[href*="village="]');
                        if (la) nm = (la.textContent || '').trim();
                    }}
                    if (!nm) {{
                        var tds0 = row.querySelectorAll('td');
                        if (tds0.length) nm = (tds0[0].textContent || '').trim();
                    }}
                    ques = [];
                    ul = row.querySelector('ul.order_queue') || row.querySelector('.order_queue');
                    if (ul) {{
                        lis = ul.querySelectorAll('li');
                        for (j = 0; j < lis.length; j++) {{
                            li = lis[j];
                            ques.push((li.textContent || '').replace(/\\s+/g, ' ').trim());
                        }}
                    }}
                    cordM = (row.textContent || '').match(/(\\d{{3}}\\|\\d{{3}})/);
                    out.push({{
                        villageId: vid,
                        name: nm || '—',
                        coord: cordM ? cordM[1] : '',
                        queueSummary: ques.length ? ques.join(' · ') : '—'
                    }});
                }}
                window.__tw_buildings_ov_fetch = JSON.stringify({{
                    status: 'OK',
                    rows: out,
                    fetchUrl: url,
                    parseNote: rows.length ? '' : 'tr[id^="v_"] bulunamadı'
                }});
            }})
            .catch(function(err) {{
                window.__tw_buildings_ov_fetch = JSON.stringify({{
                    status: 'ERROR', message: String(err)
                }});
            }});
        }})();
        """

        self.browser.page().runJavaScript(fetch_js)
        QTimer.singleShot(120, lambda: self._buildings_overview_poll_load(0))

    def _buildings_overview_poll_load(self, attempt):
        max_attempts = 140
        if attempt >= max_attempts:
            self.buildings_ov_refresh_btn.setEnabled(True)
            self.buildings_ov_refresh_btn.setText("🔄 Yükle")
            self.buildings_ov_status_label.setText("Zaman aşımı.")
            self._add_log("BİNA-ÖZET", "error", "Bina genel bakış: zaman aşımı.")
            self.browser.page().runJavaScript("window.__tw_buildings_ov_fetch=null;")
            return

        check_js = (
            "(function(){ var x = window.__tw_buildings_ov_fetch; "
            "if (x === undefined || x === null) return 'WAITING'; return x; })();"
        )

        def on_poll(result):
            if result is None:
                result_str = "WAITING"
            else:
                result_str = str(result).strip()

            if result_str in ("WAITING", "LOADING", ""):
                QTimer.singleShot(120, lambda: self._buildings_overview_poll_load(attempt + 1))
                return

            self.browser.page().runJavaScript("window.__tw_buildings_ov_fetch=null;")
            self._buildings_overview_apply_fetch_result(result_str)

        self.browser.page().runJavaScript(check_js, on_poll)

    def _buildings_overview_apply_fetch_result(self, result_str):
        self.buildings_ov_refresh_btn.setEnabled(True)
        self.buildings_ov_refresh_btn.setText("🔄 Yükle")

        if not result_str or result_str in ("WAITING", "LOADING"):
            self.buildings_ov_status_label.setText("Yanıt yok.")
            self._add_log("BİNA-ÖZET", "error", "Bina genel bakış: boş yanıt.")
            return

        try:
            data = json.loads(result_str)
        except Exception:
            self.buildings_ov_status_label.setText("Parse hatası.")
            self._add_log("BİNA-ÖZET", "error", "Bina genel bakış JSON parse edilemedi.")
            return

        if data.get("status") == "ERROR":
            msg = data.get("message", "?")
            self.buildings_ov_status_label.setText("Hata: " + str(msg)[:80])
            self._add_log("BİNA-ÖZET", "error", f"Bina genel bakış: {msg}")
            return

        rows = data.get("rows", [])
        self.buildings_ov_tree.clear()
        for r in rows:
            vid = str(r.get("villageId", "") or "")
            name = str(r.get("name", "—"))
            coord = str(r.get("coord", "") or "—")
            qs = str(r.get("queueSummary", "—"))
            item = QTreeWidgetItem([vid, name, coord, qs])
            item.setData(0, Qt.UserRole, {"villageId": vid, "href": f"/game.php?village={vid}&screen=main"})
            self.buildings_ov_tree.addTopLevelItem(item)

        n = len(rows)
        note = (data.get("parseNote") or "").strip()
        self.buildings_ov_status_label.setText(
            f"{n} köy ({data.get('fetchUrl', '')})" + (f" — {note}" if note else "")
        )
        self._add_log(
            "BİNA-ÖZET",
            "success",
            f"Bina genel bakış güncellendi: {n} köy",
        )

    def _buildings_overview_open_selected(self):
        if not self.browser:
            return
        item = self.buildings_ov_tree.currentItem()
        if not item:
            QMessageBox.information(self, "Bina Genel Bakış", "Listeden bir satır seçin.")
            return
        payload = item.data(0, Qt.UserRole)
        if not isinstance(payload, dict):
            payload = {}
        vid = (payload.get("villageId") or item.text(0) or "").strip()
        if not vid:
            QMessageBox.warning(self, "Bina Genel Bakış", "Köy ID yok.")
            return
        href = (payload.get("href") or "").strip() or f"/game.php?village={vid}&screen=main"
        base = self.browser.page().url()
        target = QUrl(href)
        if target.isRelative():
            target = base.resolved(target)
        self.browser.load(target)
        self.tabs.setCurrentIndex(0)
        self._add_log("BİNA-ÖZET", "info", f"Tarayıcıda açılıyor: {target.toString()[:120]}")

    # ── RAPORLAR ───────────────────────────────

    def _build_reports_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        hint = QLabel(
            "Rapor listesi, oturum açık gömülü tarayıcı çerezleriyle "
            "<b>game.php?…&amp;screen=report</b> adresinden çekilir (ör. tr101 …/report). "
            "Önce oyunda köye girip raporlar sayfasına en az bir kez girmiş olmanız gerekir."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 10px; color: #555; padding: 4px;")
        layout.addWidget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(8)

        bar.addWidget(QLabel("Filtre:"))
        self.reports_mode_combo = QComboBox()
        self.reports_mode_combo.setMinimumWidth(160)
        # Menüdekiyle aynı: …&screen=report&mode=… (tr101 HTML modemenu)
        for label, mode in [
            ("Hepsi", "all"),
            ("Saldırılar", "attack"),
            ("Savunmalar", "defense"),
            ("Destekler", "support"),
            ("Ticaret", "trade"),
            ("Etkinlikler", "event"),
            ("Diğer", "other"),
        ]:
            self.reports_mode_combo.addItem(label, mode)
        bar.addWidget(self.reports_mode_combo)

        self.reports_refresh_btn = QPushButton("🔄 Raporları Yükle")
        self.reports_refresh_btn.setObjectName("startBtn")
        self.reports_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.reports_refresh_btn.clicked.connect(self._reports_refresh)
        bar.addWidget(self.reports_refresh_btn)

        self.reports_open_btn = QPushButton("🌐 Seçileni tarayıcıda aç")
        self.reports_open_btn.setCursor(Qt.PointingHandCursor)
        self.reports_open_btn.clicked.connect(self._reports_open_selected)
        bar.addWidget(self.reports_open_btn)

        bar.addStretch()
        self.reports_status_label = QLabel("Hazır.")
        self.reports_status_label.setStyleSheet("font-size: 10px; color: #666;")
        bar.addWidget(self.reports_status_label)
        layout.addLayout(bar)

        self.reports_tree = QTreeWidget()
        self.reports_tree.setAlternatingRowColors(True)
        self.reports_tree.setRootIsDecorated(False)
        self.reports_tree.setHeaderLabels(
            ["Tarih", "Tür", "Kaynak", "Hedef", "Sonuç"])
        self.reports_tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self.reports_tree.setColumnWidth(0, 130)
        self.reports_tree.setColumnWidth(1, 72)
        self.reports_tree.setColumnWidth(2, 200)
        self.reports_tree.setColumnWidth(3, 200)
        self.reports_tree.setColumnWidth(4, 72)
        self.reports_tree.itemDoubleClicked.connect(
            lambda *_: self._reports_open_selected())
        layout.addWidget(self.reports_tree, 1)

        foot = QLabel(
            "İlk sayfadaki raporlar listelenir. Çift tık veya «Seçileni tarayıcıda aç» ile "
            "rapor detayına gidersiniz (sekme: Tarayıcı)."
        )
        foot.setWordWrap(True)
        foot.setStyleSheet("font-size: 9px; color: #888;")
        layout.addWidget(foot)

        self.tabs.addTab(tab, "📊 Raporlar")

    def _reports_refresh(self):
        """Aktif köy için /game.php?village=…&screen=report HTML'ini çekip tabloyu doldur."""
        if not self.browser:
            QMessageBox.warning(self, "Raporlar", "Tarayıcı hazır değil.")
            return

        village_id = self._game_data.get("village", {}).get("id", "") or ""
        if not village_id:
            QMessageBox.warning(
                self,
                "Raporlar",
                "Aktif köy ID bulunamadı.\n"
                "Önce oyunda giriş yapıp bir köye gidin; köy verisi senkron olunca tekrar deneyin.",
            )
            return

        mode = self.reports_mode_combo.currentData()
        if mode is None or mode == "":
            mode = "all"
        mode = str(mode)

        self.reports_refresh_btn.setEnabled(False)
        self.reports_refresh_btn.setText("Yükleniyor…")
        self.reports_status_label.setText("Sunucudan rapor sayfası isteniyor…")
        self._add_log("RAPOR", "info", "Rapor listesi yükleniyor…")

        # QWebEngine runJavaScript Promise döndüğünde callback'e undefined verir; fetch tamamlanınca
        # sonucu window.__tw_reports_fetch'e yazıp poll ile okuyoruz (harita yükleme ile aynı desen).
        fetch_js = """
        (function() {
            window.__tw_reports_fetch = 'LOADING';
            var villageId = """ + json.dumps(str(village_id)) + """;
            var mode = """ + json.dumps(mode) + """;
            var url = '/game.php?village=' + encodeURIComponent(villageId) +
                '&screen=report&mode=' + encodeURIComponent(mode || 'all');

            fetch(url, {credentials: 'same-origin'})
            .then(function(r) { return r.text(); })
            .then(function(html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var table = doc.querySelector('table#report_list') || doc.querySelector('#report_list');
                var rows = table ? table.querySelectorAll('tr') : [];

                function inferType(row) {
                    var h = row.innerHTML || '';
                    if (/command\\/farm\\.webp/i.test(h)) return 'Yağma';
                    if (/command\\/attack_(small|medium|large)\\.webp/i.test(h)) return 'Saldırı';
                    if (/command\\/attack\\.(webp|png)/i.test(h)) return 'Saldırı';
                    if (/command\\/support\\.webp/i.test(h)) return 'Destek';
                    if (/command\\/return\\.webp/i.test(h)) return 'Dönüş';
                    if (/command\\/snob\\.webp/i.test(h)) return 'Göç';
                    if (/command\\/spy\\.webp/i.test(h)) return 'Casus';
                    if (/command\\/knight\\.webp/i.test(h)) return 'Şövalye';
                    return '—';
                }

                function inferResult(row) {
                    var imgs = row.querySelectorAll('img[src*="dots/"]');
                    for (var i = 0; i < imgs.length; i++) {
                        var s = imgs[i].getAttribute('src') || '';
                        if (s.indexOf('dots/red_yellow') >= 0) return 'Yenilgi+bina';
                        if (s.indexOf('dots/red_blue') >= 0) return 'Yenilgi+casus';
                        if (s.indexOf('dots/green') >= 0) return 'Zafer';
                        if (s.indexOf('dots/yellow') >= 0) return 'Kayıplar';
                        if (s.indexOf('dots/red') >= 0) return 'Yenildi';
                        if (s.indexOf('dots/blue') >= 0) return 'Casuslandı';
                    }
                    return '—';
                }

                var out = [];
                for (var r = 0; r < rows.length; r++) {
                    var row = rows[r];
                    if (row.classList && row.classList.contains('report_filter')) continue;
                    if (row.querySelector('th')) continue;
                    var link = row.querySelector('a.report-link');
                    if (!link) continue;

                    var href = link.getAttribute('href') || '';
                    var lbl = row.querySelector('span.quickedit-label');
                    var subject = lbl ? (lbl.textContent || '').replace(/\\s+/g, ' ').trim() : '';
                    subject = subject.replace(/\\s*\\(yeni\\)\\s*$/i, '').trim();

                    var cells = row.querySelectorAll('td');
                    var dateStr = '—';
                    if (cells.length >= 3) {
                        var dt = cells[cells.length - 1];
                        if (dt && dt.classList && dt.classList.contains('nowrap'))
                            dateStr = (dt.textContent || '').trim();
                        else
                            dateStr = (cells[cells.length - 1].textContent || '').trim();
                    }

                    var coords = subject.match(/\\(\\d{1,3}\\|\\d{1,3}\\)/g) || [];
                    var srcPart = '', tgtPart = '';
                    if (coords.length >= 2) {
                        srcPart = coords[0];
                        tgtPart = coords[1];
                    } else if (coords.length === 1) {
                        srcPart = coords[0];
                        tgtPart = '—';
                    } else {
                        srcPart = subject.length > 90 ? subject.substring(0, 90) + '…' : subject;
                        tgtPart = '—';
                    }

                    var repId = '';
                    var m = href.match(/[?&]view=(\\d+)/);
                    if (m) repId = m[1];

                    out.push({
                        date: dateStr,
                        type: inferType(row),
                        source: srcPart,
                        target: tgtPart,
                        subject: subject,
                        result: inferResult(row),
                        href: href,
                        id: repId
                    });
                }

                window.__tw_reports_fetch = JSON.stringify({
                    status: 'OK',
                    count: out.length,
                    reports: out,
                    fetchUrl: url,
                    parseNote: 'tr101 #report_list a.report-link'
                });
            })
            .catch(function(err) {
                window.__tw_reports_fetch = JSON.stringify({
                    status: 'ERROR', message: String(err)
                });
            });
        })();
        """

        self.browser.page().runJavaScript(fetch_js)
        QTimer.singleShot(120, lambda: self._reports_poll_load(0))

    def _reports_poll_load(self, attempt):
        """fetch tamamlanana kadar window.__tw_reports_fetch oku (Promise → runJavaScript uyumsuzluğu)."""
        max_attempts = 140
        if attempt >= max_attempts:
            self.reports_refresh_btn.setEnabled(True)
            self.reports_refresh_btn.setText("🔄 Raporları Yükle")
            self.reports_status_label.setText("Zaman aşımı.")
            self._add_log("RAPOR", "error", "Rapor listesi: zaman aşımı (fetch yanıt vermedi).")
            self.browser.page().runJavaScript("window.__tw_reports_fetch=null;")
            return

        check_js = (
            "(function(){ var x = window.__tw_reports_fetch; "
            "if (x === undefined || x === null) return 'WAITING'; return x; })();"
        )

        def on_poll(result):
            raw = result
            if raw is None:
                result_str = "WAITING"
            else:
                result_str = str(raw).strip()

            if result_str in ("WAITING", "LOADING", ""):
                QTimer.singleShot(120, lambda: self._reports_poll_load(attempt + 1))
                return

            self.browser.page().runJavaScript("window.__tw_reports_fetch=null;")
            self._reports_apply_fetch_result(result_str)

        self.browser.page().runJavaScript(check_js, on_poll)

    def _reports_apply_fetch_result(self, result_str):
        self.reports_refresh_btn.setEnabled(True)
        self.reports_refresh_btn.setText("🔄 Raporları Yükle")

        if not result_str or result_str in ("WAITING", "LOADING"):
            self.reports_status_label.setText("Yanıt yok.")
            self._add_log("RAPOR", "error", "Rapor listesi: boş yanıt.")
            return

        try:
            data = json.loads(result_str)
        except Exception:
            self.reports_status_label.setText("Parse hatası.")
            self._add_log("RAPOR", "error", "Rapor JSON parse edilemedi.")
            return

        if data.get("status") == "ERROR":
            msg = data.get("message", "?")
            self.reports_status_label.setText("Hata: " + str(msg)[:80])
            self._add_log("RAPOR", "error", f"Rapor yükleme: {msg}")
            return

        reports = data.get("reports", [])
        self.reports_tree.clear()

        for r in reports:
            item = QTreeWidgetItem([
                str(r.get("date", "—")),
                str(r.get("type", "—")),
                str(r.get("source", "—")),
                str(r.get("target", "—")),
                str(r.get("result", "—")),
            ])
            href = (r.get("href") or "").strip()
            rep_id = (r.get("id") or "").strip()
            subj = (r.get("subject") or "").strip()
            item.setData(0, Qt.UserRole, {"href": href, "id": rep_id, "subject": subj})
            if subj:
                item.setToolTip(2, subj)
                item.setToolTip(3, subj)
            res = r.get("result", "")
            bad = ("Yenildi", "Yenilgi", "Kayıplar")
            if res == "Zafer":
                item.setForeground(4, QColor("#228822"))
            elif res == "Casuslandı":
                item.setForeground(4, QColor("#2a6a9e"))
            elif res == "Kayıplar":
                item.setForeground(4, QColor("#c98000"))
            elif any(res.startswith(x) for x in bad) or res == "Kayıp":
                item.setForeground(4, QColor("#cc2222"))
            self.reports_tree.addTopLevelItem(item)

        n = len(reports)
        self.reports_status_label.setText(f"{n} rapor (ilk sayfa)")
        self._add_log(
            "RAPOR",
            "success",
            f"Rapor listesi güncellendi: {n} satır ({data.get('fetchUrl', '')})",
        )

    def _reports_open_selected(self):
        """Seçili rapor bağlantısını gömülü tarayıcıda aç."""
        if not self.browser:
            return
        item = self.reports_tree.currentItem()
        if not item:
            QMessageBox.information(self, "Raporlar", "Listeden bir rapor seçin.")
            return

        payload = item.data(0, Qt.UserRole)
        if not isinstance(payload, dict):
            payload = {}

        href = (payload.get("href") or "").strip()
        rep_id = (payload.get("id") or "").strip()
        village_id = self._game_data.get("village", {}).get("id", "") or ""

        if not href and rep_id and village_id:
            href = f"/game.php?village={village_id}&screen=report&mode=all&view={rep_id}"

        if not href:
            QMessageBox.warning(
                self,
                "Raporlar",
                "Bu satırda rapor bağlantısı yok.\n"
                "Sayfa yapısı farklıysa sayfa kaynağını paylaşarak seçici güncellenebilir.",
            )
            return

        base = self.browser.page().url()
        target = QUrl(href)
        if target.isRelative():
            target = base.resolved(target)

        self.browser.load(target)
        self.tabs.setCurrentIndex(0)
        self._add_log("RAPOR", "info", f"Tarayıcıda açılıyor: {target.toString()[:120]}")

    # ── AYARLAR ────────────────────────────────

    def _build_settings_tab(self):
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("settingsTabScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("settingsTabViewport")
        inner = QWidget()
        inner.setObjectName("settingsTabScrollInner")
        inner.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        proxy_group = QGroupBox("Ağ / Proxy (isteğe bağlı, yerel bilgisayarda kayıtlı)")
        proxy_lay = QFormLayout()
        proxy_lay.setSpacing(6)
        self.settings_proxy_enable_cb = QCheckBox("Proxy kullan (tür, host ve portu açmak için işaretleyin)")
        self.settings_proxy_enable_cb.setChecked(
            self._settings.value("network/proxy_enabled", False, type=bool)
        )
        proxy_lay.addRow(self.settings_proxy_enable_cb)
        self.settings_proxy_host = QLineEdit()
        self.settings_proxy_host.setPlaceholderText("ör. proxy.saglayici.com")
        self.settings_proxy_host.setText(
            (self._settings.value("network/proxy_host", "") or "").strip()
        )
        proxy_lay.addRow("Sunucu (host):", self.settings_proxy_host)
        self.settings_proxy_port = QSpinBox()
        self.settings_proxy_port.setRange(1, 65535)
        pdef = 8080
        try:
            pdef = int(self._settings.value("network/proxy_port", pdef) or pdef)
        except (TypeError, ValueError):
            pdef = 8080
        self.settings_proxy_port.setValue(max(1, min(65535, pdef)))
        proxy_lay.addRow("Port:", self.settings_proxy_port)
        self.settings_proxy_type = QComboBox()
        self.settings_proxy_type.setMinimumWidth(180)
        self.settings_proxy_type.setToolTip("Çoğu sağlayıcıda ERR_NO_SUPPORTED_PROXIES için HTTP seçin.")
        self.settings_proxy_type.addItem("HTTP (önerilen — çoğu ISP proxy)", "http")
        self.settings_proxy_type.addItem("SOCKS5", "socks5")
        ptyp = (self._settings.value("network/proxy_type", "http") or "http").lower()
        idx = 1 if ptyp == "socks5" else 0
        self.settings_proxy_type.setCurrentIndex(idx)
        proxy_lay.addRow("Proxy türü:", self.settings_proxy_type)
        self.settings_proxy_user = QLineEdit()
        self.settings_proxy_user.setText(
            (self._settings.value("network/proxy_user", "") or "").strip()
        )
        self.settings_proxy_user.setPlaceholderText("İsteğe bağlı")
        self.settings_proxy_pass = QLineEdit()
        self.settings_proxy_pass.setText(
            (self._settings.value("network/proxy_password", "") or "").strip()
        )
        self.settings_proxy_pass.setEchoMode(QLineEdit.Password)
        self.settings_proxy_pass.setPlaceholderText("İsteğe bağlı")
        proxy_lay.addRow("Kullanıcı adı:", self.settings_proxy_user)
        proxy_lay.addRow("Şifre:", self.settings_proxy_pass)
        self.settings_proxy_help = QLabel(
            "Ayarlar uygulama ile birlikte saklanır; dağıtıma gömülü uç nokta yoktur. "
            "Kaydettikten sonra (ve proxy değişiminde) uygulamayı yeniden başlatın. "
            "Kullanıcı/şifre, Chromium’da ayrı oturur (adres satırındaki ERR_NO_SUPPORTED_PROXIES "
            "önlemek için URL’e gömülmez; Qt otomatik proxy kimliğini doldurur). "
            "Hâlâ aynı hata: türü HTTP veya sağlayıcının bu portu için doğrulayın. "
            "SOCKS5’te arka plandaki Python indirmeleri aynı türe ve ortama bağlı olmayabilir."
        )
        self.settings_proxy_help.setObjectName("settingsProxyHelp")
        self.settings_proxy_help.setWordWrap(True)
        proxy_lay.addRow(self.settings_proxy_help)
        proxy_group.setLayout(proxy_lay)
        layout.addWidget(proxy_group)

        tg_group = QGroupBox("Telegram (doğrulama / hCaptcha uyarısı)")
        tg_lay = QFormLayout()
        tg_lay.setSpacing(6)
        _cfg = _tw_load_config()
        self.settings_tg_enable_cb = QCheckBox("Telegram ile güvenlik uyarısı (hCaptcha vb.)")
        self.settings_tg_enable_cb.setChecked(
            _cfg.get("telegram_enabled", self._settings.value("notify/telegram_enabled", False, type=bool))
        )
        tg_lay.addRow(self.settings_tg_enable_cb)
        self.settings_tg_token = QLineEdit()
        self.settings_tg_token.setText(
            (_cfg.get("telegram_bot_token") or self._settings.value("notify/telegram_bot_token", "") or "").strip()
        )
        self.settings_tg_token.setEchoMode(QLineEdit.Password)
        self.settings_tg_token.setPlaceholderText("@BotFather — bot token")
        tg_lay.addRow("Bot token:", self.settings_tg_token)
        self.settings_tg_chat_id = QLineEdit()
        self.settings_tg_chat_id.setText(
            (_cfg.get("telegram_chat_id") or self._settings.value("notify/telegram_chat_id", "") or "").strip()
        )
        self.settings_tg_chat_id.setPlaceholderText("Sohbet veya grup chat_id (grup: genelde eksi id)")
        tg_lay.addRow("Chat ID:", self.settings_tg_chat_id)
        self.settings_tg_insecure_ssl_cb = QCheckBox(
            "Kurumsal ağ / SSL tarama: Telegram API için sertifika doğrulamasını atla"
        )
        self.settings_tg_insecure_ssl_cb.setChecked(
            _cfg.get("telegram_insecure_ssl", self._settings.value("notify/telegram_insecure_ssl", False, type=bool))
        )
        self.settings_tg_insecure_ssl_cb.setToolTip(
            "MITM veya kendi sertifikanız zincirde self-signed hatası verirse açın; "
            "ağdaki sertifikayı sizin gözetiminiz dışındadır, riski kabul edin."
        )
        tg_lay.addRow(self.settings_tg_insecure_ssl_cb)
        _cfg_path_str = str(_tw_config_path())
        self.settings_tg_help = QLabel(
            "Botu gruba ekleyin; grup için chat_id'yi @userinfobot veya getUpdates ile alın. "
            "Token'ı paylaşmayın. Mesaj: oyuncu adı, dünya, tespit türü. "
            "Ayarları Kaydet sonrası otomatik uyarılar çalışır. "
            "SSL hatası alırsanız: üstteki doğrulamaıyı atla'yı açıp Kaydet.\n\n"
            f"Kalıcı ayar dosyası (bot güncellenince silinmez, exe'nin yanında kalır):\n{_cfg_path_str}"
        )
        self.settings_tg_help.setWordWrap(True)
        self.settings_tg_help.setObjectName("settingsProxyHelp")
        tg_lay.addRow(self.settings_tg_help)
        self.settings_tg_test_btn = QPushButton("Test mesajı gönder")
        self.settings_tg_test_btn.setCursor(Qt.PointingHandCursor)
        self.settings_tg_test_btn.clicked.connect(self._on_settings_telegram_test)
        tg_lay.addRow(self.settings_tg_test_btn)
        tg_group.setLayout(tg_lay)
        layout.addWidget(tg_group)

        gen_group = QGroupBox("Genel Ayarlar")
        gen_layout = QFormLayout()
        gen_layout.setSpacing(8)
        self.settings_dark_cb = QCheckBox("Gece modu — koyu arayüz")
        self.settings_dark_cb.setChecked(self._dark_mode)
        self.settings_dark_cb.toggled.connect(self._on_settings_dark_mode_toggled)
        gen_layout.addRow(self.settings_dark_cb)
        for label, default in [
            ("Farm mesafesi (max):", "20"),
            ("Min. kaynak:", "500"),
            ("Saldırı arası bekleme (sn):", "30"),
            ("Max eşzamanlı saldırı:", "50"),
            ("Max farm turu:", "10"),
        ]:
            entry = QLineEdit(default)
            entry.setFixedWidth(80)
            gen_layout.addRow(label, entry)
        gen_group.setLayout(gen_layout)
        layout.addWidget(gen_group)

        notif_group = QGroupBox("Bildirimler")
        notif_layout = QVBoxLayout()
        for text, default in [
            ("Saldırı altında bildirim", True),
            ("Kaynak dolu uyarısı", True),
            ("Ses bildirimi", False),
            ("Bina tamamlandı bildirimi", True),
            ("Asker eğitimi bitti bildirimi", False),
        ]:
            cb = QCheckBox(text)
            cb.setChecked(default)
            notif_layout.addWidget(cb)
        notif_group.setLayout(notif_layout)
        layout.addWidget(notif_group)

        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        save_btn = QPushButton("Ayarları Kaydet")
        save_btn.setObjectName("startBtn")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setToolTip("Proxy alanlarını da diske yazar; proxy değiştiyse uygulamayı yeniden başlatın.")
        save_btn.clicked.connect(self._on_settings_page_save)
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(save_btn)
        save_row.addStretch()
        outer.addLayout(save_row)
        self.tabs.addTab(tab, "⚙️ Ayarlar")

    # ── LOGLAR ─────────────────────────────────

    def _build_logs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        toolbar = QHBoxLayout()
        clear_btn = QPushButton("Temizle")
        clear_btn.clicked.connect(self._clear_logs)
        toolbar.addWidget(clear_btn)
        toolbar.addWidget(QPushButton("Dışa Aktar"))
        toolbar.addSpacing(20)
        toolbar.addWidget(QLabel("Filtre:"))
        self.log_filter = QComboBox()
        self.log_filter.addItems(["Tümü", "SİSTEM", "FARM", "RAPOR", "TAR", "BİNA", "UYARI", "HATA"])
        toolbar.addWidget(self.log_filter)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.log_text = QTextEdit()
        self.log_text.setObjectName("logText")
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        self.tabs.addTab(tab, "📝 Loglar")

        self._add_log("SİSTEM", "info", f"Uygulama başlatıldı. Tribal Wars Bot v{APP_VERSION}")
        self._add_log("SİSTEM", "info", f"Harita yüklendi. {len(self.villages)} köy bulundu.")
        self._add_log("TAR", "success", "Chromium tarayıcı hazır. Anti-detection aktif.")
        self._add_log("TAR", "info", "Stealth profil: navigator.webdriver=undefined, sahte plugin/dil/WebGL")
        self._add_log("SİSTEM", "info", "Bot bağlantı için hazır. Başlat'a basın.")

    # ── BOT KONTROLÜ ───────────────────────────

    def _set_login_credentials_highlight(self, ok: bool):
        """Dünyaya giriş (oyun ekranı) sonrası kullanıcı/şifre satırını yeşil vurgula."""
        if not hasattr(self, "login_input") or not hasattr(self, "password_input"):
            return
        if ok:
            if self._dark_mode:
                inp_style = (
                    "QLineEdit { background-color: #1b3d24; border: 2px solid #43a047; "
                    "border-radius: 4px; padding: 3px; font-weight: bold; color: #e8f5e9; }"
                )
                lbl_style = "color: #81c784; font-weight: bold; font-size: 11px;"
            else:
                inp_style = (
                    "QLineEdit { background-color: #e8f5e9; border: 2px solid #43a047; "
                    "border-radius: 4px; padding: 3px; font-weight: bold; }"
                )
                lbl_style = "color: #1b5e20; font-weight: bold; font-size: 11px;"
            self.login_input.setStyleSheet(inp_style)
            self.password_input.setStyleSheet(inp_style)
            if hasattr(self, "login_user_label"):
                self.login_user_label.setStyleSheet(lbl_style)
            if hasattr(self, "login_pass_label"):
                self.login_pass_label.setStyleSheet(lbl_style)
        else:
            self.login_input.setStyleSheet("")
            self.password_input.setStyleSheet("")
            if hasattr(self, "login_user_label"):
                self.login_user_label.setStyleSheet("")
            if hasattr(self, "login_pass_label"):
                self.login_pass_label.setStyleSheet("")

    def _start_bot(self):
        username = self.login_input.text().strip()
        password = self.password_input.text().strip()

        if not username or not password:
            QMessageBox.warning(self, "Uyarı", "Kullanıcı adı ve şifre giriniz!")
            return

        self._set_login_credentials_highlight(False)
        self.is_running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_indicator.setText("● AKTİF")
        self.status_indicator.setStyleSheet("color: #228822; font-weight: bold; font-size: 11px;")
        self._add_log("SİSTEM", "success", "Bot başlatıldı!")
        self._update_status()

        # Tarayıcı sekmesine geç
        self.tabs.setCurrentIndex(0)

        # Dünya ayarlarını sıfırla
        self._world_settings_fetched = False
        self._world_speed_from_settings = False
        self._trusted_world_speed = None
        self._trusted_unit_speed = None
        self._game_data = {}

        # Giriş akışını başlat
        self._login_state = "navigating"
        server_url = SERVERS[self.server_combo.currentIndex()][1]
        self._add_log("GİRİŞ", "info", f"Sunucuya bağlanılıyor: {server_url}")

        # Önceki bağlantıyı temizle, sonra yeni bağla
        try:
            self.browser.loadFinished.disconnect(self._on_page_loaded)
        except:
            pass
        self.browser.loadFinished.connect(self._on_page_loaded)
        self.browser.navigate(server_url)

    def _on_page_loaded(self, ok):
        """Sayfa yüklendikten sonra duruma göre aksiyon al."""
        if not self.is_running:
            return

        current_url = self.browser.url().toString()
        self._add_log("TAR", "info", f"Sayfa yüklendi: {current_url}")

        # Oyun ekranına girildi mi? (her state'te kontrol et)
        if "game.php" in current_url or "/overview" in current_url:
            if self._login_state != "in_game":
                self._login_state = "in_game"
                self._add_log("GİRİŞ", "success", "✅ Oyun ekranına girildi!")
                self.world_combo.setEnabled(False)
                self.world_select_btn.setEnabled(False)
                self._set_login_credentials_highlight(True)
            # Her sayfa yüklendiğinde oyun verisini çek
            self._scrape_game_data()
            return

        # 1) Ana sayfa / Login sayfası → Giriş yap
        if self._login_state == "navigating":
            self._perform_login()

        # 2) Login sonrası → dünya seçim sayfasını kontrol et
        elif self._login_state in ("waiting_world", "world_select"):
            self._check_world_selection()

    def _check_world_selection(self):
        """Dünya seçim sayfasında mıyız kontrol et. Kesin HTML yapısı:
        - body.logged-in → giriş başarılı
        - a.world-select[href="/page/play/zzN"] → dünya linkleri
        - .world_button_active → aktif dünyalar
        - .world_button_inactive → açık ama henüz katılınmamış dünyalar
        """
        check_js = """
        (function() {
            // Giriş başarılı mı?
            var isLoggedIn = document.body.classList.contains('logged-in');
            
            if (!isLoggedIn) {
                // Hata mesajı var mı?
                var errorEl = document.querySelector('.error, .error-msg, .alert');
                if (errorEl && errorEl.textContent.trim()) {
                    return JSON.stringify({status: 'LOGIN_ERROR', message: errorEl.textContent.trim()});
                }
                return JSON.stringify({status: 'NOT_LOGGED_IN'});
            }
            
            // Hoşgeldin mesajı
            var welcomeEl = document.querySelector('.right.login h2');
            var welcomeName = welcomeEl ? welcomeEl.textContent.trim() : '';
            
            // Dünyaları topla (klanlar.org / InnoGames: .world-select; yoksa /page/play/ linkleri)
            var worlds = [];
            var worldLinks = document.querySelectorAll('a.world-select');
            if (!worldLinks.length) {
                worldLinks = document.querySelectorAll('a[href*="/page/play/"]');
            }
            var seenHref = {};
            worldLinks.forEach(function(a) {
                var href = a.getAttribute('href') || '';
                if (!href || seenHref[href]) return;
                seenHref[href] = true;
                var span = a.querySelector('span');
                var name = span ? span.textContent.trim() : '';
                if (!name) name = a.textContent.replace(/\\s+/g, ' ').trim();
                if (!name) {
                    var im = a.querySelector('img[alt]');
                    if (im) name = (im.getAttribute('alt') || '').trim();
                }
                if (!name) {
                    var m = href.match(/\\/page\\/play\\/([^/?#]+)/);
                    if (m) name = m[1];
                }
                var isActive = span ? span.classList.contains('world_button_active') : false;
                worlds.push({
                    name: name || href,
                    href: href,
                    active: isActive
                });
            });
            
            return JSON.stringify({
                status: 'WORLD_SELECT',
                welcome: welcomeName,
                worlds: worlds
            });
        })();
        """

        def on_check_result(result):
            if not result:
                return

            try:
                data = json.loads(str(result))
            except:
                self._add_log("TAR", "warn", f"Sayfa durumu belirlenemedi: {result}")
                return

            status = data.get("status", "")

            if status == "LOGIN_ERROR":
                self._add_log("GİRİŞ", "error", f"❌ Giriş hatası: {data.get('message', 'Bilinmeyen hata')}")

            elif status == "NOT_LOGGED_IN":
                self._add_log("GİRİŞ", "warn", "Henüz giriş yapılmadı. Bekleniyor...")

            elif status == "WORLD_SELECT":
                welcome = data.get("welcome", "")
                worlds = data.get("worlds", [])

                self._add_log("GİRİŞ", "success", f"✅ Giriş başarılı! {welcome}")
                self._login_state = "world_select"

                # Dünya combobox'ını doldur
                self.world_combo.clear()
                self.world_combo.setEnabled(True)
                self.world_select_btn.setEnabled(True)

                self._detected_worlds = worlds

                if worlds:
                    self._add_log("DÜNYA", "info", f"{len(worlds)} dünya bulundu:")
                    for w in worlds:
                        status_text = "⚔️ Aktif" if w["active"] else "🆕 Açık"
                        self._add_log("DÜNYA", "info", f"  → {w['name']} ({status_text}) [{w['href']}]")
                        self.world_combo.addItem(
                            f"{'⚔️' if w['active'] else '🆕'} {w['name']}",
                            w["href"]
                        )

                    self._add_log("DÜNYA", "info", "Üst panelden dünya seçip 'Gir' butonuna basın veya tarayıcıdan tıklayın.")
                else:
                    self._add_log("DÜNYA", "warn", "Hiç dünya bulunamadı!")

        self.browser.page().runJavaScript(check_js, on_check_result)

    def _enter_world(self):
        """Seçilen dünyaya gir."""
        if not hasattr(self, '_detected_worlds') or self.world_combo.currentIndex() < 0:
            return

        world_href = self.world_combo.currentData()
        world_name = self.world_combo.currentText()

        if world_href:
            # Tam URL oluştur
            base_url = SERVERS[self.server_combo.currentIndex()][1]
            full_url = base_url.rstrip('/') + world_href
            self._add_log("DÜNYA", "info", f"Dünyaya giriliyor: {world_name} → {full_url}")
            self._login_state = "waiting_world"
            self._world_settings_fetched = False
            self._world_speed_from_settings = False
            self._trusted_world_speed = None
            self._trusted_unit_speed = None
            self.browser.navigate(full_url)

    # ── OYUN VERİSİ ÇEKME ─────────────────────

    def _merge_all_villages_troops_with_previous(self, data: dict) -> None:
        """
        Sonraki taramada sayfa Birlikler tablosu yokken veya XHR boş dönünce köyler
        troops: {} ile gelir; misyoner vb. arayüzde 0 görünür. Aynı köy ID için önceki
        taramadaki troops sözlüğünü yedek olarak kullan; yeni tabloda gelen anahtarlar
        her zaman üstün (gerçekten 0 olan birim güncellenir).
        """
        prev = (self._game_data or {}).get("all_villages")
        if not prev:
            return
        new_list = data.get("all_villages") or []
        if not new_list:
            return
        prev_by_id = {}
        for v in prev:
            vid = v.get("id")
            if vid is None:
                continue
            try:
                prev_by_id[int(vid)] = v
            except (TypeError, ValueError):
                continue
        cur_vid = None
        gv = data.get("village") or {}
        if gv.get("id") is not None:
            try:
                cur_vid = int(gv["id"])
            except (TypeError, ValueError):
                pass

        for v in new_list:
            vid = v.get("id")
            if vid is None:
                continue
            try:
                vid = int(vid)
            except (TypeError, ValueError):
                continue
            p = prev_by_id.get(vid)
            if not p:
                continue
            nt = v.get("troops")
            pt = p.get("troops") or {}
            if not nt:
                if pt:
                    v["troops"] = dict(pt)
                continue
            merged = dict(pt)
            for k, val in nt.items():
                try:
                    merged[k] = int(val)
                except (TypeError, ValueError):
                    merged[k] = 0
            v["troops"] = merged

        if cur_vid is not None:
            for v in new_list:
                try:
                    if int(v.get("id", 0)) == cur_vid:
                        data["troops"] = dict(v.get("troops") or {})
                        break
                except (TypeError, ValueError):
                    continue

    def _scrape_game_data(self):
        """game_data JS değişkeninden ve DOM'dan tüm verileri çek."""
        scrape_js = """
        (function() {
            if (typeof game_data === 'undefined') {
                return JSON.stringify({status: 'NO_GAME_DATA'});
            }

            var result = {status: 'OK'};

            // Oyuncu bilgisi
            if (game_data.player) {
                result.player = {
                    name: game_data.player.name,
                    id: game_data.player.id,
                    rank: game_data.player.rank,
                    points: game_data.player.points,
                    villages: game_data.player.villages,
                    ally: game_data.player.ally
                };
            }

            // Aktif köy bilgisi
            if (game_data.village) {
                var v = game_data.village;
                result.village = {
                    id: v.id,
                    name: v.name,
                    x: v.x,
                    y: v.y,
                    points: v.points,
                    wood: Math.floor(v.wood_float || v.wood),
                    stone: Math.floor(v.stone_float || v.stone),
                    iron: Math.floor(v.iron_float || v.iron),
                    wood_prod: v.wood_prod,
                    stone_prod: v.stone_prod,
                    iron_prod: v.iron_prod,
                    storage_max: v.storage_max,
                    pop: v.pop,
                    pop_max: v.pop_max,
                    buildings: v.buildings || {},
                    coord: v.coord
                };
            }

            var unitNames = game_data.units || [];

            function unitKeyFromImgSrc(src) {
                if (!src) return null;
                var s = String(src).toLowerCase();
                var known = ['spear','sword','axe','archer','spy','light','marcher','heavy','ram','catapult','knight','snob','militia'];
                var ki, u;
                for (ki = 0; ki < known.length; ki++) {
                    u = known[ki];
                    if (s.indexOf('unit_' + u) >= 0 || s.indexOf('/unit/' + u + '.') >= 0 || s.indexOf('/unit/' + u + '_') >= 0)
                        return u;
                }
                if (s.indexOf('snob') >= 0 || s.indexOf('noble') >= 0 || s.indexOf('misyoner') >= 0 || s.indexOf('soylu') >= 0)
                    return 'snob';
                return null;
            }

            function detectUnitColumnsFromHeader(tableEl) {
                if (!tableEl) return null;
                var headerRow = tableEl.querySelector('thead tr');
                if (!headerRow) {
                    var trs = tableEl.querySelectorAll('tr');
                    var ri;
                    for (ri = 0; ri < Math.min(trs.length, 6); ri++) {
                        if (trs[ri].querySelector('th.unit-item, td.unit-item')) {
                            headerRow = trs[ri];
                            break;
                        }
                    }
                }
                if (!headerRow) return null;
                var keys = [];
                var hi, img, k, good = 0;
                var unitHdrs = headerRow.querySelectorAll('th.unit-item, td.unit-item');
                if (unitHdrs.length) {
                    for (hi = 0; hi < unitHdrs.length; hi++) {
                        img = unitHdrs[hi].querySelector('img[src]');
                        k = img ? unitKeyFromImgSrc(img.getAttribute('src')) : null;
                        keys.push(k);
                        if (k) good++;
                    }
                    if (good >= 6) return keys;
                }
                // overview_villages&mode=units (#units_table): Köy | boş | birim img… | İşlem — th.unit-item yok
                keys = [];
                good = 0;
                var allTh = headerRow.querySelectorAll('th');
                for (hi = 0; hi < allTh.length; hi++) {
                    img = allTh[hi].querySelector('img[src*="unit_"], img[src*="/unit/"]');
                    if (!img) continue;
                    k = unitKeyFromImgSrc(img.getAttribute('src'));
                    keys.push(k);
                    if (k) good++;
                }
                if (good < 6) return null;
                return keys;
            }

            function readSnobFromUnitsObject(uobj) {
                if (uobj == null) return NaN;
                if (Array.isArray(uobj)) {
                    var un = game_data.units || [];
                    var si = -1, zi;
                    for (zi = 0; zi < un.length; zi++) {
                        if (un[zi] === 'snob') { si = zi; break; }
                    }
                    if (si >= 0 && si < uobj.length) {
                        var an = parseInt(uobj[si], 10);
                        if (!isNaN(an)) return an;
                    }
                    return NaN;
                }
                if (typeof uobj !== 'object') return NaN;
                var direct = ['snob', 'noble', 'nobleman', 'snobs', 'unit_snob', 'NOBLE', 'SNOB', 'Noble', 'Snob'];
                var di, k, raw, n;
                for (di = 0; di < direct.length; di++) {
                    k = direct[di];
                    if (!Object.prototype.hasOwnProperty.call(uobj, k)) continue;
                    raw = uobj[k];
                    if (raw != null && typeof raw === 'object' && raw.count != null) raw = raw.count;
                    n = parseInt(raw, 10);
                    if (!isNaN(n)) return n;
                }
                for (k in uobj) {
                    if (!Object.prototype.hasOwnProperty.call(uobj, k)) continue;
                    var lk = String(k).toLowerCase();
                    if (lk.indexOf('snob') < 0 && lk.indexOf('noble') < 0 && lk.indexOf('misyoner') < 0 && lk.indexOf('soylu') < 0)
                        continue;
                    raw = uobj[k];
                    if (raw != null && typeof raw === 'object' && raw.count != null) raw = raw.count;
                    n = parseInt(raw, 10);
                    if (!isNaN(n)) return n;
                }
                return NaN;
            }

            function readSnobFromTroopRow(tr) {
                if (!tr) return NaN;
                var td = tr.querySelector('td.unit-item-snob, td[class*="unit-item-snobs"], td.unit-item[class*="snob"]');
                if (!td) return NaN;
                var dc = td.getAttribute('data-unit-count');
                if (dc != null && String(dc).length) {
                    var n1 = parseInt(dc, 10);
                    if (!isNaN(n1)) return n1;
                }
                var n2 = parseInt(String(td.textContent || '').replace(/[^0-9\\-]/g, ''), 10);
                return isNaN(n2) ? NaN : n2;
            }

            function parseCombinedTableRows(tableEl) {
                var out = [];
                if (!tableEl) return out;
                var colKeys = detectUnitColumnsFromHeader(tableEl);
                // row_a/row_b dışı stillerde de köy satırı olabiliyor — data-id'li quickedit kök alınır
                var qNodes = tableEl.querySelectorAll('.quickedit-vn[data-id]');
                qNodes.forEach(function(qe) {
                    var row = qe.closest ? qe.closest('tr') : null;
                    if (!row) return;
                    var vill = {};
                    vill.id = parseInt(qe.getAttribute('data-id')) || 0;
                    var label = row.querySelector('.quickedit-label');
                    if (label) {
                        vill.name = label.getAttribute('data-text') || label.textContent.trim();
                        var fullText = label.textContent.trim();
                        var coordMatch = fullText.match(/[(](\\d+)[|](\\d+)[)]/);
                        if (coordMatch) {
                            vill.x = parseInt(coordMatch[1]);
                            vill.y = parseInt(coordMatch[2]);
                        }
                    }
                    var farmCell = row.querySelector('a[href*="screen=farm"]');
                    if (farmCell) {
                        vill.farm_text = farmCell.textContent.trim();
                    }
                    vill.troops = {};
                    var cells = row.querySelectorAll('td.unit-item');
                    // #units_table: td.unit-item sayısı game_data.units ile birebir aynı — başlık img eşlemesi
                    // hatalı olsa bile snob dahil tüm sütunlar oyun sırasına göre okunur (yanlış yüksek snob düzeltmesi).
                    if (unitNames.length > 0 && cells.length === unitNames.length) {
                        var ti;
                        for (ti = 0; ti < unitNames.length; ti++) {
                            if (unitNames[ti]) {
                                vill.troops[unitNames[ti]] = parseInt(String(cells[ti].textContent || '').trim(), 10) || 0;
                            }
                        }
                    } else if (colKeys && colKeys.length === cells.length) {
                        var ci, uk;
                        for (ci = 0; ci < cells.length; ci++) {
                            uk = colKeys[ci];
                            if (uk) vill.troops[uk] = parseInt(String(cells[ci].textContent || '').trim(), 10) || 0;
                        }
                    } else {
                        for (var i = 0; i < cells.length && i < unitNames.length; i++) {
                            vill.troops[unitNames[i]] = parseInt(String(cells[i].textContent || '').trim(), 10) || 0;
                        }
                    }
                    var snDom = readSnobFromTroopRow(row);
                    if (!isNaN(snDom)) vill.troops.snob = snDom;
                    var tbody = row.closest('tbody');
                    vill.selected = row.classList.contains('selected')
                        || (tbody && tbody.classList && tbody.classList.contains('selected'));
                    if (vill.id) {
                        out.push(vill);
                    }
                });
                return out;
            }

            function enrichTroopsFromGameDataUnits(arr) {
                if (!arr || !arr.length) return;
                var vi, v, vid, uobj, sn, gv = game_data.village;
                for (vi = 0; vi < arr.length; vi++) {
                    v = arr[vi];
                    if (!v || !v.id) continue;
                    vid = parseInt(v.id, 10);
                    uobj = null;
                    if (gv && parseInt(gv.id, 10) === vid && gv.units)
                        uobj = gv.units;
                    else if (game_data.villages) {
                        var vv = game_data.villages[vid] || game_data.villages[String(vid)];
                        if (vv && vv.units) uobj = vv.units;
                    }
                    if (!uobj) continue;
                    if (!v.troops) v.troops = {};
                    sn = readSnobFromUnitsObject(uobj);
                    if (!isNaN(sn)) {
                        var curSn = parseInt(v.troops.snob, 10) || 0;
                        v.troops.snob = Math.max(curSn, sn);
                    }
                }
            }

            function mergeVillagesById(arr, more) {
                var byId = {};
                var i, v, w, j;
                for (i = 0; i < arr.length; i++) {
                    v = arr[i];
                    if (v.id) {
                        byId[v.id] = v;
                    }
                }
                function mergeTroopDicts(a, b) {
                    var out = {};
                    var k, kk, keys = {};
                    a = a || {};
                    b = b || {};
                    for (k in a) {
                        if (Object.prototype.hasOwnProperty.call(a, k)) keys[k] = true;
                    }
                    for (k in b) {
                        if (Object.prototype.hasOwnProperty.call(b, k)) keys[k] = true;
                    }
                    for (kk in keys) {
                        if (!Object.prototype.hasOwnProperty.call(keys, kk)) continue;
                        var na = parseInt(a[kk], 10) || 0;
                        var nb = parseInt(b[kk], 10) || 0;
                        out[kk] = Math.max(na, nb);
                    }
                    return out;
                }
                for (j = 0; j < more.length; j++) {
                    w = more[j];
                    if (!w.id) continue;
                    if (!byId[w.id]) {
                        byId[w.id] = w;
                    } else {
                        if (w.selected) {
                            byId[w.id].selected = true;
                        }
                        // Eski: sadece toplam asker yüksek olanı alıyordu — birleşik tabloda snob
                        // sütunu 0 kalıp diğer sütunlar yüksek olunca misyoner tamamen kayboluyordu.
                        byId[w.id].troops = mergeTroopDicts(byId[w.id].troops, w.troops);
                    }
                }
                arr.length = 0;
                Object.keys(byId).forEach(function(k) {
                    arr.push(byId[k]);
                });
            }

            function sameOverviewListPage(urlA, urlB) {
                try {
                    var ua = new URL(urlA);
                    var ub = new URL(urlB);
                    if (ua.pathname !== ub.pathname) return false;
                    // Aksi halde köy ekranı (screen=place vb.) ile overview_villages?page=0
                    // yanlışlıkla "aynı sayfa" sanılıp XHR atlanıyordu.
                    var sa = ua.searchParams.get('screen');
                    var sb = ub.searchParams.get('screen');
                    if (sa !== sb) return false;
                    var ma = ua.searchParams.get('mode');
                    var mb = ub.searchParams.get('mode');
                    if (ma !== mb) return false;
                    var pa = ua.searchParams.get('page');
                    var pb = ub.searchParams.get('page');
                    if (pa === null) pa = '0';
                    if (pb === null) pb = '0';
                    var ga = ua.searchParams.get('group');
                    var gb = ub.searchParams.get('group');
                    if (ga === null) ga = '0';
                    if (gb === null) gb = '0';
                    return pa === pb && ga === gb;
                } catch (e) {
                    return false;
                }
            }

            function mergeMissingFromGameDataVillages(arr) {
                if (!game_data.villages || typeof game_data.villages !== 'object') return;
                var curVid = game_data.village ? parseInt(game_data.village.id, 10) : 0;
                var have = {};
                var ii;
                for (ii = 0; ii < arr.length; ii++) {
                    if (arr[ii].id) have[arr[ii].id] = true;
                }
                function addMissing(vv, key) {
                    if (!vv || typeof vv !== 'object') return;
                    var vid = parseInt(vv.id != null ? vv.id : key, 10);
                    if (!vid || isNaN(vid) || have[vid]) return;
                    have[vid] = true;
                    var vx = vv.x, vy = vv.y;
                    if ((vx == null || vy == null) && vv.coord) {
                        var cm = String(vv.coord).match(/(\\d+)[|/](\\d+)/);
                        if (cm) { vx = parseInt(cm[1], 10); vy = parseInt(cm[2], 10); }
                    }
                    arr.push({
                        id: vid,
                        name: vv.name || ('#' + vid),
                        x: vx,
                        y: vy,
                        points: vv.points || 0,
                        selected: (vid === curVid),
                        troops: {},
                        farm_text: '—'
                    });
                }
                if (Array.isArray(game_data.villages)) {
                    for (ii = 0; ii < game_data.villages.length; ii++) {
                        addMissing(game_data.villages[ii], ii);
                    }
                } else {
                    for (var gvk2 in game_data.villages) {
                        if (Object.prototype.hasOwnProperty.call(game_data.villages, gvk2)) {
                            addMissing(game_data.villages[gvk2], gvk2);
                        }
                    }
                }
            }

            function fetchOverviewHtmlSync(url) {
                try {
                    var xhr = new XMLHttpRequest();
                    xhr.open('GET', url, false);
                    xhr.send(null);
                    return xhr.responseText || '';
                } catch (ex) {
                    return '';
                }
            }

            result.all_villages = [];
            var unitsTableEl = document.getElementById('units_table');
            if (unitsTableEl) {
                mergeVillagesById(result.all_villages, parseCombinedTableRows(unitsTableEl));
            }

            var table = document.getElementById('combined_table');
            var hasCombinedTableOnPage = !!table;
            if (table) {
                mergeVillagesById(result.all_villages, parseCombinedTableRows(table));

                var pager = document.querySelectorAll('a.paged-nav-item[href*="page="]');
                var seenFetch = {};
                var curHref = window.location.href;
                for (var pi = 0; pi < pager.length; pi++) {
                    var href = pager[pi].getAttribute('href');
                    if (!href || href.indexOf('page=-') !== -1) continue;
                    var absUrl;
                    try {
                        absUrl = new URL(href, window.location.origin).href;
                    } catch (e1) {
                        continue;
                    }
                    if (seenFetch[absUrl]) continue;
                    seenFetch[absUrl] = true;
                    if (sameOverviewListPage(absUrl, curHref)) continue;

                    var html = fetchOverviewHtmlSync(absUrl);
                    if (!html) continue;
                    var doc = new DOMParser().parseFromString(html, 'text/html');
                    var t2 = doc.getElementById('combined_table');
                    mergeVillagesById(result.all_villages, parseCombinedTableRows(t2));
                    mergeVillagesById(result.all_villages, parseCombinedTableRows(doc.getElementById('units_table')));
                }

                // Oyuncu köy sayısı tablodan azsa: sayfa numaralarını tek tek dene (XHR engellenirse yedek)
                var expectedVc = parseInt(result.player && result.player.villages, 10) || 0;
                if (expectedVc > result.all_villages.length) {
                    var uBig = new URL(window.location.href);
                    uBig.searchParams.set('screen', 'overview_villages');
                    uBig.searchParams.set('mode', 'combined');
                    if (!uBig.searchParams.get('group')) uBig.searchParams.set('group', '0');
                    uBig.searchParams.set('page', '0');
                    uBig.searchParams.set('page_size', '500');
                    var urlBig = uBig.href;
                    if (!seenFetch[urlBig]) {
                        seenFetch[urlBig] = true;
                        // page_size ekli URL, mevcut sayfa ile aynı page=0 olsa da içerik daha geniş olabilir — her zaman çek
                        var hBig = fetchOverviewHtmlSync(urlBig);
                        if (hBig) {
                            var dBig = new DOMParser().parseFromString(hBig, 'text/html');
                            var tBig = dBig.getElementById('combined_table');
                            mergeVillagesById(result.all_villages, parseCombinedTableRows(tBig));
                            mergeVillagesById(result.all_villages, parseCombinedTableRows(dBig.getElementById('units_table')));
                        }
                    }

                    var uSweep = new URL(window.location.href);
                    uSweep.searchParams.set('screen', 'overview_villages');
                    uSweep.searchParams.set('mode', 'combined');
                    if (!uSweep.searchParams.get('group')) uSweep.searchParams.set('group', '0');
                    var maxSweep = Math.min(80, Math.ceil(expectedVc / 5) + 6);
                    var si;
                    for (si = 0; si < maxSweep; si++) {
                        if (expectedVc > 0 && result.all_villages.length >= expectedVc) break;
                        uSweep.searchParams.set('page', String(si));
                        var surl = uSweep.href;
                        if (seenFetch[surl]) continue;
                        seenFetch[surl] = true;
                        if (sameOverviewListPage(surl, curHref)) continue;
                        var hx = fetchOverviewHtmlSync(surl);
                        if (!hx) continue;
                        var ds = new DOMParser().parseFromString(hx, 'text/html');
                        var tx = ds.getElementById('combined_table');
                        var prevLen = result.all_villages.length;
                        mergeVillagesById(result.all_villages, parseCombinedTableRows(tx));
                        mergeVillagesById(result.all_villages, parseCombinedTableRows(ds.getElementById('units_table')));
                        if (result.all_villages.length === prevLen && si > 2) {
                            break;
                        }
                    }
                }

                result.all_villages.sort(function(a, b) {
                    var an = (a.name || '').toLowerCase();
                    var bn = (b.name || '').toLowerCase();
                    if (an < bn) return -1;
                    if (an > bn) return 1;
                    return a.id - b.id;
                });
            }

            // Tabloda eksik kalan köyleri game_data.villages ile tamamla (sadece boşken değil, her zaman)
            mergeMissingFromGameDataVillages(result.all_villages);

            // Birleşik tablo bu sayfada yoksa (ör. köy ana ekranı): overview_villages/combined XHR ile çek.
            // Bazı dünyalarda tüm köyler page=-1 veya page_size=500 ile tek istekte gelir.
            var expectedVcTotal = parseInt(result.player && result.player.villages, 10) || 0;
            if (!hasCombinedTableOnPage && (result.all_villages.length === 0 || expectedVcTotal > result.all_villages.length)) {
                var seenOv = {};
                var curH = window.location.href;
                function mergeOverviewHtml(html) {
                    if (!html) return;
                    var doc = new DOMParser().parseFromString(html, 'text/html');
                    var t = doc.getElementById('combined_table');
                    var ut = doc.getElementById('units_table');
                    mergeVillagesById(result.all_villages, parseCombinedTableRows(t));
                    mergeVillagesById(result.all_villages, parseCombinedTableRows(ut));
                }
                var uBig = new URL(window.location.href);
                uBig.searchParams.set('screen', 'overview_villages');
                uBig.searchParams.set('mode', 'combined');
                if (!uBig.searchParams.get('group')) uBig.searchParams.set('group', '0');
                uBig.searchParams.set('page', '0');
                uBig.searchParams.set('page_size', '500');
                var urlBig = uBig.href;
                if (!seenOv[urlBig]) {
                    seenOv[urlBig] = true;
                    mergeOverviewHtml(fetchOverviewHtmlSync(urlBig));
                }
                if (expectedVcTotal > result.all_villages.length || result.all_villages.length === 0) {
                    var uNeg = new URL(window.location.href);
                    uNeg.searchParams.set('screen', 'overview_villages');
                    uNeg.searchParams.set('mode', 'combined');
                    if (!uNeg.searchParams.get('group')) uNeg.searchParams.set('group', '0');
                    uNeg.searchParams.set('page', '-1');
                    uNeg.searchParams.delete('page_size');
                    var urlNeg = uNeg.href;
                    if (!seenOv[urlNeg]) {
                        seenOv[urlNeg] = true;
                        mergeOverviewHtml(fetchOverviewHtmlSync(urlNeg));
                    }
                }
                if (expectedVcTotal > result.all_villages.length || result.all_villages.length === 0) {
                    var uSw = new URL(window.location.href);
                    uSw.searchParams.set('screen', 'overview_villages');
                    uSw.searchParams.set('mode', 'combined');
                    if (!uSw.searchParams.get('group')) uSw.searchParams.set('group', '0');
                    var maxSw = Math.min(80, Math.max(12, Math.ceil(expectedVcTotal / 5) + 6));
                    var sj;
                    for (sj = 0; sj < maxSw; sj++) {
                        if (expectedVcTotal > 0 && result.all_villages.length >= expectedVcTotal) break;
                        uSw.searchParams.set('page', String(sj));
                        var surl = uSw.href;
                        if (seenOv[surl]) continue;
                        seenOv[surl] = true;
                        if (sameOverviewListPage(surl, curH)) continue;
                        var hx = fetchOverviewHtmlSync(surl);
                        var prevL = result.all_villages.length;
                        mergeOverviewHtml(hx);
                        if (result.all_villages.length === prevL && sj > 2) break;
                    }
                }
                result.all_villages.sort(function(a, b) {
                    var an = (a.name || '').toLowerCase();
                    var bn = (b.name || '').toLowerCase();
                    if (an < bn) return -1;
                    if (an > bn) return 1;
                    return a.id - b.id;
                });
            }

            if (result.all_villages.length > 0) {
                result.all_villages.sort(function(a, b) {
                    var an = (a.name || '').toLowerCase();
                    var bn = (b.name || '').toLowerCase();
                    if (an < bn) return -1;
                    if (an > bn) return 1;
                    return a.id - b.id;
                });
            }

            enrichTroopsFromGameDataUnits(result.all_villages);

            // units_table bu sayfada yoksa: mode=units XHR ile snob verisini güncelle.
            // Her sayfada senkron XHR çalıştırmamak için 5 dakikalık window cache kullanılır.
            if (!unitsTableEl) {
                try {
                    var _snobNow = Date.now();
                    var _snobTtl = 5 * 60 * 1000;
                    var _snobCached = window.__tw_bot_units_cache;
                    var _snobHtml = null;
                    if (_snobCached && (_snobNow - _snobCached.t) < _snobTtl) {
                        _snobHtml = _snobCached.html;
                    } else {
                        var _uUnits = new URL(window.location.href);
                        _uUnits.searchParams.set('screen', 'overview_villages');
                        _uUnits.searchParams.set('mode', 'units');
                        if (!_uUnits.searchParams.get('group')) _uUnits.searchParams.set('group', '0');
                        _uUnits.searchParams.set('page_size', '500');
                        _uUnits.searchParams.delete('page');
                        _snobHtml = fetchOverviewHtmlSync(_uUnits.href);
                        if (_snobHtml) window.__tw_bot_units_cache = { t: _snobNow, html: _snobHtml };
                    }
                    if (_snobHtml) {
                        var _uDoc = new DOMParser().parseFromString(_snobHtml, 'text/html');
                        var _uTbl = _uDoc.getElementById('units_table');
                        if (_uTbl) mergeVillagesById(result.all_villages, parseCombinedTableRows(_uTbl));
                    }
                } catch(ex) {}
            }

            // Mevcut aktif köyün asker sayıları (seçili satırdan)
            result.troops = {};
            if (result.all_villages.length > 0) {
                var activeVillage = result.all_villages.find(function(v) { return v.selected; });
                if (activeVillage) {
                    result.troops = activeVillage.troops;
                } else {
                    result.troops = result.all_villages[0].troops;
                }
            }

            // Birleşik tabloda asker yoksa aktif köyün askerlerini game_data.village'dan al
            var troopKeysEmpty = !result.troops || Object.keys(result.troops).length === 0;
            if (troopKeysEmpty && unitNames.length && game_data.village) {
                var gv = game_data.village;
                if (gv.units && typeof gv.units === 'object' && !Array.isArray(gv.units)) {
                    result.troops = {};
                    for (var uk in gv.units) {
                        if (Object.prototype.hasOwnProperty.call(gv.units, uk)) {
                            result.troops[uk] = parseInt(gv.units[uk], 10) || 0;
                        }
                    }
                } else if (Array.isArray(gv.units)) {
                    result.troops = {};
                    for (var uii = 0; uii < unitNames.length; uii++) {
                        var val = gv.units[uii];
                        result.troops[unitNames[uii]] = parseInt(val, 10) || 0;
                    }
                } else {
                    result.troops = {};
                    for (var uj = 0; uj < unitNames.length; uj++) {
                        var unk = unitNames[uj];
                        if (gv[unk] != null && gv[unk] !== '') {
                            result.troops[unk] = parseInt(gv[unk], 10) || 0;
                        }
                    }
                }
            }

            (function patchActiveSnobTroops() {
                var gv = game_data.village;
                if (!gv || !gv.units) return;
                var sn = readSnobFromUnitsObject(gv.units);
                if (isNaN(sn)) return;
                if (!result.troops) result.troops = {};
                var cur = parseInt(result.troops.snob, 10) || 0;
                result.troops.snob = Math.max(cur, sn);
            })();

            // link_base - köy geçişi için URL pattern
            result.link_base = game_data.link_base_pure || '';
            result.world = game_data.world || '';
            result.screen = game_data.screen || '';
            result.csrf = game_data.csrf || '';
            result.world_display = '';

            // image_base — birim ikonları için CDN URL
            // Farklı sunucularda farklı alanlarda bulunabilir
            result.image_base = (
                game_data.image_base ||
                (typeof TribalWars !== 'undefined' && TribalWars.image_base) ||
                ''
            );
            // Fallback: sayfadaki herhangi bir unit PNG URL'sinden türet
            if (!result.image_base) {
                var unitImg = document.querySelector('img[src*="/graphic/unit/"]');
                if (unitImg) {
                    var src = unitImg.getAttribute('src');
                    var idx = src.indexOf('/graphic/unit/');
                    if (idx > -1) result.image_base = src.substring(0, idx) + '/';
                }
            }

            // Dünya hız ayarları + üst panelde gösterilecek dünya adı
            if (typeof TribalWars !== 'undefined' && TribalWars.worldConfig) {
                var wc = TribalWars.worldConfig;
                result.world_speed = parseFloat(wc.speed || 1);
                result.unit_speed = parseFloat(wc.unit_speed || 1);
                var wlabel = (wc.world_name || wc.worldName || wc.name || wc.displayName || '').toString().trim();
                if (wlabel) result.world_display = wlabel;
            }
            if (!result.world_display && result.world) {
                result.world_display = String(result.world);
            }
            // Alternatif: game_data içinden
            if (!result.world_speed && game_data.world_speed) {
                result.world_speed = parseFloat(game_data.world_speed);
            }
            if (!result.unit_speed && game_data.unit_speed) {
                result.unit_speed = parseFloat(game_data.unit_speed);
            }
            // config modülünden
            if (typeof TribalWars !== 'undefined' && typeof TribalWars.getGameData === 'function') {
                try {
                    var gd = TribalWars.getGameData();
                    if (gd && gd.world_config) {
                        if (!result.world_speed) result.world_speed = parseFloat(gd.world_config.speed || 1);
                        if (!result.unit_speed) result.unit_speed = parseFloat(gd.world_config.unit_speed || 1);
                    }
                } catch(e) {}
            }
            // Host adından (örn. tr101.klanlar.org) — elle dünyaya girildiğinde game_data.world boş kalabiliyor
            if (!result.world_display) {
                var host = (window.location && window.location.hostname) ? window.location.hostname : '';
                var segs = host.split('.');
                if (segs.length >= 3 && segs[0] !== 'www') {
                    result.world_display = segs[0];
                    if (!result.world) result.world = segs[0];
                }
            }

            // Diğer sekmeler (harita, ordu diyaloğu) için köy listesi anahtarı
            result.villages = result.all_villages;

            // Liste ekranlarında TribalWars.worldConfig yoksa: sayfa HTML'inde world_config / worldConfig bloğu
            if (!result.world_speed || !result.unit_speed) {
                try {
                    var h = document.documentElement.innerHTML;
                    var slice = h;
                    var k = h.indexOf('"world_config"');
                    if (k < 0) k = h.indexOf('worldConfig');
                    if (k >= 0) slice = h.substring(k, k + 15000);
                    else slice = h.substring(0, Math.min(h.length, 300000));
                    var m1 = slice.match(/"speed"\\s*:\\s*(\\d+(?:\\.\\d+)?)/);
                    var m2 = slice.match(/"unit_speed"\\s*:\\s*(\\d+(?:\\.\\d+)?)/);
                    if (m1 && !result.world_speed) result.world_speed = parseFloat(m1[1]);
                    if (m2 && !result.unit_speed) result.unit_speed = parseFloat(m2[1]);
                } catch (e) {}
            }

            return JSON.stringify(result);
        })();
        """

        def on_scrape_result(result):
            if not result:
                return
            try:
                data = json.loads(str(result))
            except:
                self._add_log("VERİ", "warn", f"Veri parse edilemedi")
                return

            if data.get("status") == "NO_GAME_DATA":
                self._add_log("VERİ", "warn", "game_data bulunamadı. Oyun sayfasında değilsiniz.")
                return

            self._merge_all_villages_troops_with_previous(data)

            # Veriyi kaydet (önceki tam atama ayarlardan gelen hızları siliyordu)
            self._game_data = data
            self._apply_trusted_speeds_to_game_data()

            # Birim ikonlarını başlat / güncelle
            image_base = data.get("image_base", "")
            if image_base:
                troop_icon_mgr.set_image_base(image_base, self._add_log)
                self._add_log("İKON", "info", f"image_base: {image_base}")

            # UI güncelle
            self._update_world_display(data)
            self._update_player_info(data)
            self._update_resources(data)
            self._update_buildings(data)
            self._update_troops(data)
            self._update_troop_available()
            self._update_village_combo(data)
            self._update_villages_list(data)
            self._update_status()

            player = data.get("player", {})
            village = data.get("village", {})
            all_v = data.get("all_villages", [])
            self._add_log("VERİ", "success",
                f"Veri güncellendi: {village.get('name', '?')} ({village.get('coord', '?')}) | "
                f"Puan: {village.get('points', 0)} | Köyler: {len(all_v)} | Dünya: {data.get('world', '?')}")

            if not getattr(self, '_world_settings_fetched', False):
                self._fetch_world_settings()

        self.browser.page().runJavaScript(scrape_js, on_scrape_result)

    def _apply_trusted_speeds_to_game_data(self):
        """Ayarlar sayfasından kesin alınan hızları scrape sonrası _game_data üzerine yaz."""
        tw = getattr(self, "_trusted_world_speed", None)
        tu = getattr(self, "_trusted_unit_speed", None)
        if tw is not None:
            try:
                v = float(tw)
                if v > 0:
                    self._game_data["world_speed"] = v
            except (TypeError, ValueError):
                pass
        if tu is not None:
            try:
                v = float(tu)
                if v > 0:
                    self._game_data["unit_speed"] = v
            except (TypeError, ValueError):
                pass

    def _fetch_world_settings(self):
        """Sunucunun /page/settings sayfasından dünya hızı ve birim hızını çek."""
        fetch_js = """
        (function() {
            var base = window.location.origin;
            var url = base + '/page/settings';
            window.__tw_world_settings = 'LOADING';
            function parseSpeedFromRawHtml(html) {
                var out = {};
                if (!html) return out;
                var k = html.indexOf('"world_config"');
                if (k < 0) k = html.indexOf('worldConfig');
                var slice = k >= 0 ? html.substring(k, k + 15000)
                    : html.substring(0, Math.min(html.length, 300000));
                var m1 = slice.match(/"speed"\\s*:\\s*(\\d+(?:\\.\\d+)?)/);
                var m2 = slice.match(/"unit_speed"\\s*:\\s*(\\d+(?:\\.\\d+)?)/);
                if (m1) out.world_speed = parseFloat(m1[1]);
                if (m2) out.unit_speed = parseFloat(m2[1]);
                /* Klanlar /page/settings: <td>Oyun hızı</td><td>1.7</td> (DOM bazen bozuk) */
                if (!out.world_speed) {
                    var t1 = html.match(/<td>\\s*Oyun[\\s\\S]*?<\\/td>\\s*<td>\\s*([0-9]+(?:\\.[0-9]+)?)/i);
                    if (t1) out.world_speed = parseFloat(t1[1]);
                }
                if (!out.unit_speed) {
                    var t2 = html.match(/<td>\\s*Birim[\\s\\S]*?<\\/td>\\s*<td>\\s*([0-9]+(?:\\.[0-9]+)?)/i);
                    if (t2) out.unit_speed = parseFloat(t2[1]);
                }
                return out;
            }
            function parseSpeedTable(doc) {
                var result = {};
                /* Klanlar TR: "Oyun hızı" / "Birim hızı" — ı (U+0131) kaynak kodda ASCII i ile eşleşmez */
                function normTr(s) {
                    return s.replace(/\\s+/g, ' ').trim().toLowerCase()
                        .replace(/\\u0131/g, 'i').replace(/\\u0130/g, 'i');
                }
                var rows = doc.querySelectorAll('table.data-table tr, table.vis tr, .data-table tr');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td, th');
                    if (cells.length < 2) return;
                    var label = normTr(cells[0].textContent);
                    var value = cells[cells.length - 1].textContent.replace(/,/g, '.').trim();
                    var num = parseFloat(value);
                    if (isNaN(num)) return;
                    if (label === 'game speed' || label === 'spielgeschwindigkeit'
                        || label.indexOf('game speed') !== -1
                        || (label.indexOf('oyun') !== -1 && label.indexOf('hiz') !== -1)) {
                        result.world_speed = num;
                    }
                    if (label === 'unit speed' || label === 'einheitengeschwindigkeit'
                        || label.indexOf('unit speed') !== -1
                        || (label.indexOf('birim') !== -1 && label.indexOf('hiz') !== -1)) {
                        result.unit_speed = num;
                    }
                });
                return result;
            }
            fetch(url, {credentials: 'same-origin'})
            .then(function(r) { return r.text(); })
            .then(function(html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var result = parseSpeedTable(doc);
                var raw = parseSpeedFromRawHtml(html);
                if (!result.world_speed && raw.world_speed) result.world_speed = raw.world_speed;
                if (!result.unit_speed && raw.unit_speed) result.unit_speed = raw.unit_speed;
                window.__tw_world_settings = JSON.stringify(result);
            })
            .catch(function(err) {
                window.__tw_world_settings = JSON.stringify({error: String(err)});
            });
        })();
        """
        self.browser.page().runJavaScript(fetch_js)
        self._poll_world_settings(0)

    def _poll_world_settings(self, attempt):
        """World settings verisini polling ile al."""
        if attempt > 30:
            self._world_settings_fetched = True
            self._world_speed_from_settings = False
            self._add_log("AYAR", "warn", "Dünya ayarları alınamadı, mevcut değerler kullanılacak")
            ws = self._game_data.get("world_speed", 1)
            us = self._game_data.get("unit_speed", 1)
            self._add_log("AYAR", "info", f"Mevcut hız: world_speed={ws}, unit_speed={us}")
            self._update_world_speed_label()
            return

        check_js = "window.__tw_world_settings || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"
            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(200, lambda: self._poll_world_settings(attempt + 1))
                return
            try:
                data = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                self._world_settings_fetched = True
                self._world_speed_from_settings = False
                self._add_log("AYAR", "warn", "Dünya ayarları parse edilemedi")
                self._update_world_speed_label()
                return

            if data.get("error"):
                self._world_settings_fetched = True
                self._world_speed_from_settings = False
                self._add_log("AYAR", "warn", f"Ayar çekme hatası: {data['error']}")
                self._update_world_speed_label()
                return

            self._world_settings_fetched = True
            ws = data.get("world_speed")
            us = data.get("unit_speed")

            def _ok_num(x):
                if x is None:
                    return False
                try:
                    return float(x) > 0
                except (TypeError, ValueError):
                    return False

            self._world_speed_from_settings = _ok_num(ws) and _ok_num(us)

            if _ok_num(ws):
                fw = float(ws)
                self._game_data["world_speed"] = fw
                self._trusted_world_speed = fw
            if _ok_num(us):
                fu = float(us)
                self._game_data["unit_speed"] = fu
                self._trusted_unit_speed = fu

            final_ws = self._game_data.get("world_speed", 1)
            final_us = self._game_data.get("unit_speed", 1)
            if self._world_speed_from_settings:
                self._add_log("AYAR", "success",
                    f"✅ Dünya ayarları alındı: Oyun hızı={final_ws}, Birim hızı={final_us}")
            else:
                self._add_log("AYAR", "info",
                    f"Ayarlar tablosunda hız satırı bulunamadı veya eksik (oyun verisi: {final_ws} / {final_us})")
            self._update_world_speed_label()

            self.browser.page().runJavaScript("window.__tw_world_settings = null;")

        self.browser.page().runJavaScript(check_js, on_poll)

    def _update_world_display(self, data):
        """Oyun içindeyken üst paneldeki dünya kutusunda aktif dünyayı göster (tarayıcıdan elle girilince de)."""
        if data.get("status") != "OK":
            return
        if not data.get("player") and not data.get("village"):
            return
        wdis = (data.get("world_display") or data.get("world") or "").strip()
        if not wdis:
            return
        wid = (data.get("world") or wdis).strip()
        self.world_combo.blockSignals(True)
        self.world_combo.clear()
        self.world_combo.addItem(f"⚔️ {wdis}", wid)
        self.world_combo.setEnabled(False)
        self.world_select_btn.setEnabled(False)
        self.world_combo.blockSignals(False)

    def _update_player_info(self, data):
        """Oyuncu bilgisi etiketini güncelle."""
        player = data.get("player", {})
        village = data.get("village", {})
        world_lbl = (data.get("world_display") or data.get("world") or "").strip() or "?"
        txt = (
            f"👤 {player.get('name', '?')} | "
            f"🏆 Sıra: {player.get('rank', '?')} | "
            f"⭐ Puan: {player.get('points', '?')} | "
            f"🏘️ Köy: {player.get('villages', '?')} | "
            f"🌍 Dünya: {world_lbl}"
        )
        self.player_info_label.setText(txt)
        self._update_world_speed_label()

    def _update_world_speed_label(self):
        """Hız gösterimi: yalnızca ölçülen değerler; yoksa — (hesaplamada .get(..., 1) ayrı)."""
        raw_ws = self._game_data.get("world_speed")
        raw_us = self._game_data.get("unit_speed")
        verified = getattr(self, "_world_speed_from_settings", False)

        def _positive_float(x):
            if x is None:
                return None
            try:
                v = float(x)
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None

        n_ws = _positive_float(raw_ws)
        n_us = _positive_float(raw_us)

        def _fmt(v):
            return str(int(v)) if v == int(v) else str(v)

        ws_text = _fmt(n_ws) if n_ws is not None else "—"
        us_text = _fmt(n_us) if n_us is not None else "—"

        if verified:
            style = "font-size: 11px; padding: 3px 4px; background: #d4edda; border-radius: 3px; color: #155724;"
            source = "(ayarlar sayfasından)"
        elif n_ws is not None or n_us is not None:
            style = "font-size: 11px; padding: 3px 4px; background: #fff3cd; border-radius: 3px; color: #856404;"
            source = "(oyun sayfası — TribalWars / game_data)"
        else:
            style = "font-size: 11px; padding: 3px 4px; background: #fff3cd; border-radius: 3px; color: #856404;"
            source = "(henüz yok — veri yenilenince veya /page/settings çekilince dolar)"

        self.world_speed_label.setText(
            f"⚙️ Dünya Hızı: {ws_text} | Birim Hızı: {us_text}  {source}")
        self.world_speed_label.setStyleSheet(style)

    def _update_resources(self, data):
        """Kaynak etiketlerini güncelle."""
        v = data.get("village", {})
        storage = v.get("storage_max", 0)

        wood = v.get("wood", 0)
        stone = v.get("stone", 0)
        iron = v.get("iron", 0)

        # Dolu oranına göre renk
        def res_color(val, mx):
            if mx == 0:
                return "#333"
            ratio = val / mx
            if ratio > 0.9:
                return "#cc2222"  # Kırmızı - taşma riski
            elif ratio > 0.7:
                return "#dd8800"  # Turuncu
            return "#228822"  # Yeşil

        self.res_wood_label.setText(f"🪵 Odun: {wood:,}")
        self.res_wood_label.setStyleSheet(f"font-size: 11px; font-weight: bold; padding: 2px 8px; color: {res_color(wood, storage)};")

        self.res_stone_label.setText(f"🧱 Kil: {stone:,}")
        self.res_stone_label.setStyleSheet(f"font-size: 11px; font-weight: bold; padding: 2px 8px; color: {res_color(stone, storage)};")

        self.res_iron_label.setText(f"⛏️ Demir: {iron:,}")
        self.res_iron_label.setStyleSheet(f"font-size: 11px; font-weight: bold; padding: 2px 8px; color: {res_color(iron, storage)};")

        self.res_storage_label.setText(f"📦 Depo: {storage:,}")
        self.res_storage_label.setStyleSheet("font-size: 11px; font-weight: bold; padding: 2px 8px; color: #333;")

        pop = v.get("pop", 0)
        pop_max = v.get("pop_max", 0)
        self.res_pop_label.setText(f"👥 Nüfus: {pop}/{pop_max} (boş: {pop_max - pop})")
        self.res_pop_label.setStyleSheet(f"font-size: 11px; font-weight: bold; padding: 2px 8px; color: {res_color(pop, pop_max)};")

    def _update_buildings(self, data):
        """Bina seviyelerini güncelle."""
        buildings = data.get("village", {}).get("buildings", {})
        if not buildings:
            return

        BUILDING_NAMES = {
            "main": "Karargah", "barracks": "Kışla", "stable": "Ahır",
            "garage": "Atölye", "church": "Kilise", "church_f": "İlk Kilise",
            "watchtower": "Gözetleme Kulesi", "snob": "Akademi",
            "smith": "Demirci", "place": "Toplanma Alanı",
            "statue": "Heykel", "market": "Pazar",
            "wood": "Kereste Kampı", "stone": "Kil Ocağı",
            "iron": "Demir Madeni", "farm": "Çiftlik",
            "storage": "Depo", "hide": "Sığınak", "wall": "Sur"
        }

        self.buildings_tree.clear()
        for key, level in buildings.items():
            name = BUILDING_NAMES.get(key, key)
            lvl = str(level)
            item = QTreeWidgetItem([name, lvl])
            if int(level) == 0:
                item.setForeground(1, QColor("#999999"))
            self.buildings_tree.addTopLevelItem(item)

    def _update_troops(self, data):
        """Asker tablosunu güncelle."""
        troops = data.get("troops", {})

        UNIT_NAMES = {
            "spear": "Mızrakçı", "sword": "Kılıççı", "axe": "Baltacı",
            "archer": "Okçu", "spy": "Casus", "light": "Hafif Süvari",
            "marcher": "Atlı Okçu", "heavy": "Ağır Süvari",
            "ram": "Koçbaşı", "catapult": "Mancınık",
            "knight": "Şövalye", "snob": "Misyoner",
            "militia": "Milis"
        }

        self.troops_tree.clear()
        for unit_key, count in troops.items():
            name = UNIT_NAMES.get(unit_key, unit_key)
            item = QTreeWidgetItem([name, str(count)])
            if count == 0:
                item.setForeground(1, QColor("#999999"))
            else:
                item.setForeground(1, QColor("#228822"))
            self.troops_tree.addTopLevelItem(item)

    def _update_village_combo(self, data):
        """Üst paneldeki köy seçiciyi güncelle."""
        all_villages = data.get("all_villages", [])
        current_id = data.get("village", {}).get("id", 0)

        # Combobox sinyalini geçici olarak kes
        self.village_combo.blockSignals(True)
        self.village_combo.clear()

        if not all_villages:
            # overview_villages sayfasında değilsek sadece aktif köyü ekle
            v = data.get("village", {})
            if v:
                self.village_combo.addItem(
                    f"{v.get('name', '?')} ({v.get('coord', '?')})",
                    v.get("id", 0)
                )
        else:
            selected_idx = 0
            for i, v in enumerate(all_villages):
                coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                label = f"{v.get('name', '?')} {coord}"
                self.village_combo.addItem(label, v.get("id", 0))
                if v.get("id") == current_id or v.get("selected"):
                    selected_idx = i
            self.village_combo.setCurrentIndex(selected_idx)

        self.village_combo.setEnabled(True)
        self.village_combo.blockSignals(False)

        # Ordu Gönder sekmesindeki kaynak köy seçiciyi güncelle
        if hasattr(self, 'sa_source_combo'):
            self.sa_source_combo.blockSignals(True)
            self.sa_source_combo.clear()

            if not all_villages:
                v = data.get("village", {})
                if v:
                    coord = v.get('coord', f"{v.get('x','?')}|{v.get('y','?')}")
                    self.sa_source_combo.addItem(
                        f"{v.get('name', '?')} ({coord})",
                        v.get("id", 0)
                    )
            else:
                sa_selected_idx = 0
                for i, v in enumerate(all_villages):
                    coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                    label = f"{v.get('name', '?')} {coord}"
                    self.sa_source_combo.addItem(label, v.get("id", 0))
                    if v.get("id") == current_id or v.get("selected"):
                        sa_selected_idx = i
                self.sa_source_combo.setCurrentIndex(sa_selected_idx)

            self.sa_source_combo.blockSignals(False)
            self._sa_on_source_changed(self.sa_source_combo.currentIndex())

        # Bina Kuyruğu sekmesindeki köy seçiciyi güncelle
        if hasattr(self, 'bq_village_combo'):
            self.bq_village_combo.blockSignals(True)
            self.bq_village_combo.clear()

            if not all_villages:
                v = data.get("village", {})
                if v:
                    coord = v.get('coord', f"{v.get('x','?')}|{v.get('y','?')}")
                    self.bq_village_combo.addItem(
                        f"{v.get('name', '?')} ({coord})", v.get("id", 0))
            else:
                bq_idx = 0
                for i, v in enumerate(all_villages):
                    coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                    label = f"{v.get('name', '?')} {coord}"
                    self.bq_village_combo.addItem(label, v.get("id", 0))
                    if v.get("id") == current_id or v.get("selected"):
                        bq_idx = i
                self.bq_village_combo.setCurrentIndex(bq_idx)

            self.bq_village_combo.blockSignals(False)

        # Asker toplama sekmesi: köy tablosunu güncelle
        if hasattr(self, 'rt_table'):
            self._rt_refresh_villages()

    def _update_villages_list(self, data):
        """Köyler sekmesindeki tüm köy tablosunu güncelle."""
        if not hasattr(self, 'all_villages_tree'):
            return

        all_villages = data.get("all_villages", [])
        self.all_villages_tree.clear()

        UNIT_NAMES_SHORT = {
            "spear": "Mız", "sword": "Kıl", "axe": "Bal",
            "archer": "Okç", "spy": "Cas", "light": "HSv",
            "marcher": "AOk", "heavy": "ASv", "ram": "Koç",
            "catapult": "Man", "knight": "Şöv", "snob": "Mis", "militia": "Mil"
        }

        for v in all_villages:
            coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
            farm = v.get("farm_text", "—")

            # Asker özetini oluştur
            troops = v.get("troops", {})
            troop_parts = []
            for unit_key, cnt in troops.items():
                if cnt > 0:
                    short = UNIT_NAMES_SHORT.get(unit_key, unit_key[:3])
                    troop_parts.append(f"{short}:{cnt}")
            troop_str = " | ".join(troop_parts) if troop_parts else "—"

            selected = "◀" if v.get("selected") else ""

            item = QTreeWidgetItem([
                str(v.get("id", 0)),
                v.get("name", "?"),
                coord,
                farm,
                troop_str,
                selected
            ])

            if v.get("selected"):
                item.setForeground(5, QColor("#2d5a9e"))

            self.all_villages_tree.addTopLevelItem(item)

    def _on_village_changed(self, index):
        """Köy combobox'ından köy seçildiğinde tarayıcıda o köye git."""
        if index < 0 or not self.is_running:
            return

        village_id = self.village_combo.currentData()
        if not village_id:
            return

        # Aynı ekranda kal, yalnızca village= parametresini değiştir (senkron veri o köye göre güncellenir)
        vid = int(village_id)
        switch_js = f"""
        (function() {{
            var vid = {vid};
            try {{
                var u = new URL(window.location.href);
                u.searchParams.set('village', String(vid));
                window.location.href = u.toString();
                return 'SWITCH_' + vid;
            }} catch (e) {{
                window.location.href = window.location.origin + '/game.php?village=' + vid + '&screen=overview';
                return 'SWITCH_FALLBACK';
            }}
        }})();
        """

        self._add_log("KÖY", "info", f"Köy değiştiriliyor → ID: {village_id}")
        self.browser.page().runJavaScript(switch_js)

    def _on_village_double_clicked(self, item, column):
        """Köyler tablosunda çift tıklanan köye geç."""
        if not self.is_running:
            return
        village_id = item.text(0)  # İlk sütun = ID
        if village_id and village_id.isdigit():
            village_name = item.text(1)
            vid = int(village_id)
            switch_js = f"""
            (function() {{
                var vid = {vid};
                try {{
                    var u = new URL(window.location.href);
                    u.searchParams.set('village', String(vid));
                    window.location.href = u.toString();
                }} catch (e) {{
                    window.location.href = window.location.origin + '/game.php?village=' + vid + '&screen=overview';
                }}
            }})();
            """
            self._add_log("KÖY", "info", f"Köye geçiliyor → {village_name} (ID: {village_id})")
            self.browser.page().runJavaScript(switch_js)

    def _perform_login(self, retry_count=0):
        """Login formunu doldur ve gönder."""
        # Zaten oyundaysak veya state değiştiyse durma
        if self._login_state == "in_game":
            return
        if retry_count > 5:
            self._add_log("GİRİŞ", "error", "Login formu 5 denemede bulunamadı, durduruluyor.")
            return

        username = self.login_input.text().strip()
        password = self.password_input.text().strip()

        self._add_log("GİRİŞ", "info", f"Giriş yapılıyor: {username}")

        # Tam form yapısı:
        # - Username: input#user[name="username"]
        # - Password: input#password[name="password"]
        # - Remember: input#remember-me (zaten checked)
        # - Login: a.btn-login (anchor, JS ile submit)
        # - Form: #login_form → POST /page/auth
        # - hCaptcha invisible var
        login_js = f"""
        (function() {{
            var userInput = document.getElementById('user');
            var passInput = document.getElementById('password');

            if (userInput && passInput) {{
                // Native setter ile değer ata (framework uyumlu)
                var nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;

                nativeSetter.call(userInput, '{username}');
                userInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                userInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                userInput.dispatchEvent(new Event('blur', {{ bubbles: true }}));

                nativeSetter.call(passInput, '{password}');
                passInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                passInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                passInput.dispatchEvent(new Event('blur', {{ bubbles: true }}));

                // Remember me zaten checked, ama emin ol
                var rememberCb = document.getElementById('remember-me');
                if (rememberCb && !rememberCb.checked) {{
                    rememberCb.click();
                }}

                // Cookie consent'i kabul et (varsa)
                var ccBtn = document.querySelector('a.btn-confirm-yes');
                if (ccBtn) {{
                    ccBtn.click();
                }}

                // SADECE btn-login'e tıkla — sitenin kendi JS'i
                // invisible hCaptcha'yı çalıştırıp form'u submit eder.
                // Manuel form.submit() captcha'yı atlar ve hata verir!
                setTimeout(function() {{
                    var loginBtn = document.querySelector('a.btn-login');
                    if (loginBtn) {{
                        loginBtn.click();
                    }}
                    return 'LOGIN_CLICKED';
                }}, 800);

                return 'FIELDS_FOUND';
            }} else {{
                return 'FIELDS_NOT_FOUND';
            }}
        }})();
        """

        self._login_state = "waiting_world"

        def on_js_result(result):
            if result and "FIELDS_FOUND" in str(result):
                self._add_log("GİRİŞ", "success", "Form alanları bulundu (#user, #password)")
                self._add_log("GİRİŞ", "info", "Bilgiler giriliyor, login butonu tıklanıyor...")
                self._add_log("GİRİŞ", "info", "Dünya seçim ekranı bekleniyor...")
            elif result and "FIELDS_NOT_FOUND" in str(result):
                if self._login_state == "in_game":
                    return
                self._add_log("GİRİŞ", "warn", "Form alanları henüz yüklenmedi. 2sn sonra tekrar deneniyor...")
                QTimer.singleShot(2000, lambda: self._perform_login(retry_count + 1))
            else:
                self._add_log("GİRİŞ", "info", f"JS sonuç: {result}")

        self.browser.page().runJavaScript(login_js, on_js_result)

    def _stop_bot(self):
        self.is_running = False
        self._login_state = "idle"
        self._set_login_credentials_highlight(False)
        self._world_settings_fetched = False
        self._world_speed_from_settings = False
        self._trusted_world_speed = None
        self._trusted_unit_speed = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_indicator.setText("● DURDURULDU")
        self.status_indicator.setStyleSheet("color: #cc4444; font-weight: bold; font-size: 11px;")
        self._add_log("SİSTEM", "warn", "Bot durduruldu.")
        self.world_speed_label.setText("⚙️ Dünya Hızı: — | Birim Hızı: —")
        self.world_speed_label.setStyleSheet("font-size: 11px; padding: 3px 4px; background: #fff3cd; border-radius: 3px; color: #856404;")
        # Dünya combobox'ı sıfırla
        self.world_combo.clear()
        self.world_combo.addItem("— Giriş yapın —")
        self.world_combo.setEnabled(False)
        self.world_select_btn.setEnabled(False)
        try:
            self.browser.loadFinished.disconnect(self._on_page_loaded)
        except:
            pass
        self._update_status()

    # ── LOG İŞLEMLERİ ─────────────────────────

    def _add_log(self, category, log_type, message):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        colors = {"info": "#88bbee", "success": "#66cc66", "warn": "#eebb44", "error": "#ee5555"}
        color = colors.get(log_type, "#cccccc")
        html = (
            f'<span style="color:#777777;">[{now}]</span> '
            f'<span style="color:#aaaaaa; font-weight:bold;">[{category}]</span> '
            f'<span style="color:{color};">{message}</span>'
        )
        self.log_text.append(html)

    def _clear_logs(self):
        self.log_text.clear()
        self._add_log("SİSTEM", "info", "Log temizlendi.")

    # ── YARDIMCI ───────────────────────────────

    def _start_sync_timer(self):
        self._server_time_text = ""

        # Ekran güncellemesi — saf Python, JS yok, 50ms
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self._tick_server_time_display)
        self.sync_timer.start(50)

        # JS resync — anchor'ı günceller, başlangıçta 2 saniye
        self._resync_timer = QTimer(self)
        self._resync_timer.timeout.connect(self._fetch_server_time)
        self._resync_timer.start(2000)

        # İlk anchor'ı hemen al
        self._fetch_server_time()

    def _tick_server_time_display(self):
        """50ms'de bir, saf Python ile sunucu saatini label'a yazar (JS çağrısı yok)."""
        if not self._server_time_synced or self._server_time_anchor_dt is None or self._server_time_anchor_perf is None:
            self._show_local_time()
            return
        try:
            elapsed = time.perf_counter() - self._server_time_anchor_perf
            dt = self._server_time_anchor_dt + datetime.timedelta(seconds=elapsed)
            ms = int((elapsed * 1000) % 1000)
            text = dt.strftime("%d/%m/%Y %H:%M:%S") + f".{ms:03d}"
            self.sync_label.setText(f"Sunucu Saati: {text}")
            self.sync_label.setStyleSheet("color: #228822; font-weight: bold; font-size: 10px;")
        except Exception:
            self._show_local_time()

    def _fetch_server_time(self):
        """Sayfadaki sunucu saatini Timing + DOM ile örnekler.

        Kesir ms DOM'dan güvenilmez (özellikle içerik zaten .xxx ile bitiyorsa); tek kaynak
        Timing/sn. Takvim satırı böyle kurulunca parse edilen anchor_dt ile serverNowMs aynı anı
        temsil eder — hedef Timing hesabında yüzlerce ms sapma oluşmaz.
        """
        if not self.browser:
            self._show_local_time()
            return

        fetch_js = """
        (function() {
            var timeEl = document.getElementById('serverTime');
            var dateEl = document.getElementById('serverDate');
            
            if (!timeEl) return 'NONE';
            
            var dateStr = dateEl ? dateEl.textContent.trim() : '';
            
            function twNormalizeMs(v) {
                if (v == null || isNaN(v)) return Date.now();
                v = Math.floor(Number(v));
                if (v > 0 && v < 1e12) v *= 1000;
                return v;
            }
            function twSnNow() {
                if (typeof Timing !== 'undefined' && typeof Timing.getCurrentServerTime === 'function') {
                    try { return twNormalizeMs(Timing.getCurrentServerTime()); } catch (e) {}
                }
                if (typeof Timing !== 'undefined' && Timing.initial_server_time && Timing.pagehit_at) {
                    var t0 = Timing.initial_server_time;
                    if (t0 < 1e12) t0 *= 1000;
                    return Math.floor(t0 + (Date.now() - Timing.pagehit_at));
                }
                if (typeof Timing !== 'undefined' && typeof Timing.offset_from_server !== 'undefined') {
                    return Math.floor(Date.now() - Timing.offset_from_server);
                }
                return Date.now();
            }
            
            var sn = twSnNow();
            var fracMs = ('00' + (Math.floor(sn) % 1000)).slice(-3);
            var z = function(n) { return (n < 10 ? '0' : '') + n; };
            var dloc = new Date(sn);
            var dsp = dateStr && dateStr.length ? dateStr : (z(dloc.getDate()) + '/' + z(dloc.getMonth() + 1) + '/' + dloc.getFullYear());
            var tp = dloc.getHours() + ':' + z(dloc.getMinutes()) + ':' + z(dloc.getSeconds()) + '.' + fracMs;
            var line = dsp + ' ' + tp;
            
            return JSON.stringify({ text: line, serverNowMs: sn });
        })();
        """

        def on_result(result):
            if not result or not isinstance(result, str) or result == 'NONE':
                self._server_time_anchor_dt = None
                self._server_time_anchor_perf = None
                self._anchor_timing_ms = None
                self._show_local_time()
                return

            text = ""
            timing_ms = None
            try:
                data = json.loads(result)
                text = (data.get("text") or "").strip()
                timing_ms = data.get("serverNowMs")
            except (json.JSONDecodeError, TypeError, AttributeError):
                text = result.strip()
                timing_ms = None

            if not text:
                self._server_time_anchor_dt = None
                self._server_time_anchor_perf = None
                self._anchor_timing_ms = None
                self._show_local_time()
                return

            self._server_time_text = text
            self._server_time_synced = True
            parsed = self._dispatch_parse_server_time()
            if parsed is not None:
                self._server_time_anchor_dt = parsed
                self._server_time_anchor_perf = time.perf_counter()
                try:
                    tm = int(timing_ms) if timing_ms is not None else None
                    if tm is not None and tm > 0 and tm < 10**12:
                        tm = int(tm * 1000)
                    self._anchor_timing_ms = tm
                except (TypeError, ValueError):
                    self._anchor_timing_ms = None
            else:
                self._server_time_anchor_dt = None
                self._server_time_anchor_perf = None
                self._anchor_timing_ms = None

            # Dispatch aktifse (bekleyen gönderim varsa) 50ms, yoksa 2 saniye
            dispatch_active = False
            try:
                if (hasattr(self, "sa_table") and
                        hasattr(self, "enable_sending_cb") and
                        self.enable_sending_cb.isChecked()):
                    for _i in range(self.sa_table.topLevelItemCount()):
                        _it = self.sa_table.topLevelItem(_i)
                        if _it and _it.data(0, Qt.UserRole) not in ("sent", "error"):
                            dispatch_active = True
                            break
            except Exception:
                pass
            new_interval = 50 if dispatch_active else 2000
            if hasattr(self, "_resync_timer") and self._resync_timer.interval() != new_interval:
                self._resync_timer.setInterval(new_interval)

        self.browser.page().runJavaScript(fetch_js, on_result)

    def _schedule_next_botprot_poll(self):
        """Bir sonraki DOM kontrolünü 4.5–9.5 sn arası rastgele gecikmeyle planla."""
        delay_ms = random.randint(4500, 9500)
        QTimer.singleShot(delay_ms, self._poll_bot_protection_reschedule)

    def _poll_bot_protection_reschedule(self):
        self._poll_bot_protection()
        self._schedule_next_botprot_poll()

    def _poll_bot_protection(self):
        """Sayfada bot koruması (#botprotection_quest), zorunlu modal veya hCaptcha var mı kontrol et."""
        if not self.browser:
            return

        detect_js = r"""
        (function() {
            function visible(el) {
                if (!el) return false;
                var s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity || '1') < 0.05) return false;
                var r = el.getBoundingClientRect();
                return r.width > 2 && r.height > 2 && r.bottom > 0 && r.right > 0;
            }
            var quest = document.getElementById('botprotection_quest');
            var questHint = false;
            if (quest) {
                var qn = quest.querySelector('.quest_new');
                questHint = visible(quest) || visible(qn);
            }
            var blocking = false;
            var links = document.querySelectorAll('a.btn.btn-default');
            for (var i = 0; i < links.length; i++) {
                var t = (links[i].textContent || '').replace(/\s+/g, ' ').trim();
                if (t.indexOf('Bot koruma kontrol') !== -1 && visible(links[i])) {
                    blocking = true;
                    break;
                }
            }
            /* id="checkbox" / anchor-tc oyun arayüzünde başka amaçlarla da olabiliyor — yanlış alarm üretmesin */
            var hc = false;
            var hcIframes = document.querySelectorAll('iframe[src*="hcaptcha"], iframe[src*="newassets.hcaptcha"]');
            for (var hi = 0; hi < hcIframes.length; hi++) {
                if (visible(hcIframes[hi])) { hc = true; break; }
            }
            if (!hc) {
                var caps = document.querySelectorAll('.h-captcha');
                for (var ci = 0; ci < caps.length; ci++) {
                    if (visible(caps[ci])) { hc = true; break; }
                }
            }

            return JSON.stringify({ quest: questHint, blocking: blocking, hcaptcha: hc });
        })();
        """

        def on_det(result):
            if not result:
                return
            try:
                d = json.loads(str(result))
            except (json.JSONDecodeError, TypeError):
                return
            active = bool(d.get("quest") or d.get("blocking") or d.get("hcaptcha"))
            if active:
                if not self._human_verification_required:
                    self._human_verification_required = True
                    parts = []
                    if d.get("quest"):
                        parts.append("görev (botprotection_quest)")
                    if d.get("blocking"):
                        parts.append("Bot koruma kontrolü")
                    if d.get("hcaptcha"):
                        parts.append("hCaptcha")
                    self._add_log(
                        "GÜVENLİK",
                        "warn",
                        "Doğrulama ekranı algılandı (" + ", ".join(parts) + "). Otomatik ordu gönderimi duraklatıldı.",
                    )
                    self._notify_telegram_security(parts)
            else:
                if self._human_verification_required:
                    self._human_verification_required = False
                    self._add_log(
                        "GÜVENLİK",
                        "info",
                        "Doğrulama ekranı kalktı — otomatik ordu gönderimi yeniden etkin.",
                    )
                    if hasattr(self, "bq_enable_cb") and self.bq_enable_cb.isChecked():
                        QTimer.singleShot(500, self._bq_auto_process)

        self.browser.page().runJavaScript(detect_js, on_det)

    def _show_local_time(self):
        """Sunucu saati alınamadığında yerel saati göster."""
        now = datetime.datetime.now()
        ms = now.microsecond // 1000
        time_str = now.strftime("%Y.%m.%d %H:%M:%S") + f".{ms:03d}"
        self.sync_label.setText(f"Yerel Saat: {time_str} (senkronize değil)")
        c = "#dd9933" if self._dark_mode else "#aa6600"
        self.sync_label.setStyleSheet(f"color: {c}; font-size: 10px;")

    def _update_status(self):
        state = "Bot çalışıyor" if self.is_running else "Bekliyor"
        gd = self._game_data
        if gd and gd.get("village"):
            v = gd["village"]
            p = gd.get("player", {})
            self.statusBar().showMessage(
                f"Durum: {state} | {v.get('name', '?')} ({v.get('coord', '?')}) | "
                f"Puan: {v.get('points', 0)} | Sıra: {p.get('rank', '?')} | "
                f"🪵{v.get('wood', 0)} 🧱{v.get('stone', 0)} ⛏️{v.get('iron', 0)} | "
                f"Dünya: {gd.get('world', '?')}"
            )
        else:
            self.statusBar().showMessage(
                f"Durum: {state} | Seçili: {len(self.selected_villages_list)}"
            )


# ─────────────────────────────────────────────
#  BAŞLATMA
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Chromium GPU rasterization — QApplication oluşturulmadan önce ayarlanmalı
    for _flag in [
        "--enable-gpu-rasterization",
        "--enable-accelerated-2d-canvas",
        "--ignore-gpu-blocklist",
    ]:
        if _flag not in sys.argv:
            sys.argv.append(_flag)

    app = QApplication(sys.argv)
    tw_apply_saved_proxy_environment()
    app.setFont(QFont("Segoe UI", 9))

    window = TribalWarsBot()
    window.show()
    sys.exit(app.exec_())
