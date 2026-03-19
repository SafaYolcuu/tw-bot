"""
Tribal Wars Bot v1.0.0 — Kabile Savaşları Otomasyon Aracı
PyQt5 + QWebEngineView (Chromium tabanlı gömülü tarayıcı)

Gereksinimler:
    pip install PyQt5 PyQtWebEngine
"""

import sys
import re
import json
import random
import datetime
import urllib.request
import threading
from PyQt5.QtCore import Qt, QUrl, QTimer, QTime, QDate, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QColor, QBrush, QPainter, QPen, QPixmap, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QSpinBox, QTreeWidget, QTreeWidgetItem, QTextEdit, QSplitter,
    QFrame, QGroupBox, QGridLayout, QHeaderView, QStatusBar,
    QSizePolicy, QFormLayout, QMessageBox, QTableWidget, QTableWidgetItem,
    QTimeEdit, QDateEdit, QAbstractItemView, QDoubleSpinBox, QSlider
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineProfile, QWebEngineSettings

# ─────────────────────────────────────────────
#  SABİTLER
# ─────────────────────────────────────────────

SERVERS = [
    ("tribalwars.works", "https://www.tribalwars.works"),
    ("tribalwars.net", "https://www.tribalwars.net"),
    ("tribalwars.com.tr", "https://www.tribalwars.com.tr"),
    ("tribalwars.co.uk", "https://www.tribalwars.co.uk"),
    ("tribalwars.de", "https://www.die-staemme.de"),
]

VILLAGE_TYPES = [
    ("yours", "#e8e832", "Senin"),
    ("enemy", "#cc2222", "Düşman"),
    ("ally", "#4488cc", "Müttefik"),
    ("other", "#dd8833", "Diğer"),
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

    def javaScriptConsoleMessage(self, level, message, line, source):
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
"""


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
    """

    village_double_clicked = pyqtSignal(int, int, int)
    view_changed = pyqtSignal(float, float, float)

    # Standart tile boyutu
    TILE_W = 53
    TILE_H = 38

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
        self.setMinimumSize(400, 300)

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

    def set_data(self, villages, cx, cy, radius):
        """Uyumluluk arayüzü."""
        self._villages = villages
        self._village_map = {}
        for v in villages:
            self._village_map[(v["x"], v["y"])] = v
        self._center_x = float(cx)
        self._center_y = float(cy)
        # radius → zoom dönüşümü: radius=30 → zoom=1.0
        self._zoom = max(self._min_zoom, min(self._max_zoom, 30.0 / max(radius, 1)))
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
                self.view_changed.emit(self._center_x, self._center_y,
                    30.0 / max(self._zoom, 0.01))
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
            self.view_changed.emit(self._center_x, self._center_y,
                30.0 / max(self._zoom, 0.01))

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        mx, my = event.pos().x(), event.pos().y()
        wx_before, wy_before = self._pixel_to_world(mx, my)

        factor = 1.15 if delta > 0 else 1 / 1.15
        self._zoom = max(self._min_zoom, min(self._max_zoom, self._zoom * factor))

        wx_after, wy_after = self._pixel_to_world(mx, my)
        self._center_x += wx_before - wx_after
        self._center_y += wy_before - wy_after
        self._center_x = max(0, min(999, self._center_x))
        self._center_y = max(0, min(999, self._center_y))

        self.update()
        self.view_changed.emit(self._center_x, self._center_y,
            30.0 / max(self._zoom, 0.01))

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

                    # Köy renkli alt çizgi (sahiplik göstergesi)
                    color = QColor(village.get("color", "#888888"))
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(color))
                    bar_h = max(2, int(th * 0.08))
                    painter.drawRect(draw_x, draw_y + int(th) - bar_h, int(tw), bar_h)

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
#  ANA PENCERE
# ─────────────────────────────────────────────

class TribalWarsBot(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚔ Tribal Wars Bot v1.0.0 — Kabile Savaşları Otomasyon Aracı")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 820)

        self.is_running = False
        self._login_state = "idle"
        self._detected_worlds = []
        self._game_data = {}
        self.villages = generate_villages()
        self.selected_villages_list = []
        self.browser = None
        self._pending_command = None
        self._server_time_synced = False

        self._build_ui()
        self._start_sync_timer()
        self._start_dispatch_timer()

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
        self._build_reports_tab()
        self._build_settings_tab()
        self._build_logs_tab()

        self.statusBar().showMessage("Durum: Bekliyor | Köy: 90 | Seçili: 0")
        self.statusBar().addPermanentWidget(QLabel("Tribal Wars Bot v1.0.0 — PyQt5/Chromium"))

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

        layout.addWidget(QLabel("Kullanıcı:"))
        self.login_input = QLineEdit()
        self.login_input.setFixedWidth(110)
        self.login_input.setPlaceholderText("kullanıcı adı")
        layout.addWidget(self.login_input)

        layout.addWidget(QLabel("Şifre:"))
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

        toolbar.setFixedHeight(36)
        toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(toolbar)

        # Gömülü Chromium tarayıcı
        self.browser = StealthBrowser()
        self.browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.browser.setMinimumHeight(400)
        self.browser.urlChanged.connect(self._on_url_changed)
        self.browser.loadStarted.connect(
            lambda: self.url_bar.setStyleSheet("padding: 4px 8px; font-size: 12px; background: #fff8e0;"))
        self.browser.loadFinished.connect(
            lambda ok: self.url_bar.setStyleSheet("padding: 4px 8px; font-size: 12px; background: white;"))
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

    def _on_server_changed(self, index):
        if index >= 0 and self.browser:
            url = SERVERS[index][1]
            self.url_bar.setText(url)
            self.browser.navigate(url)
            self._add_log("TAR", "info", f"Sunucu değiştirildi: {SERVERS[index][0]} → {url}")

    # ── KÖYLER SEKMESİ ────────────────────────

    def _build_villages_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)

        # Oyuncu bilgisi
        self.player_info_label = QLabel("Oyuncu bilgisi yükleniyor...")
        self.player_info_label.setStyleSheet("font-size: 12px; padding: 4px; background: #e8e8e8; border-radius: 3px;")
        layout.addWidget(self.player_info_label)

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
        coord_row.addWidget(self.sa_quick_target)

        coord_row.addStretch()
        form_layout.addLayout(coord_row)

        # ── Satır B: Asker giriş alanları ──
        troop_row = QHBoxLayout()
        troop_row.setSpacing(2)

        self.SA_UNIT_DEFS = [
            ("spear", "Mız"), ("sword", "Kıl"), ("axe", "Bal"), ("archer", "Okç"),
            ("spy", "Cas"), ("light", "HSv"), ("marcher", "AOk"), ("heavy", "ASv"),
            ("ram", "Koç"), ("catapult", "Man"), ("knight", "Şöv"), ("snob", "Soy"),
        ]

        self.sa_troop_inputs = {}
        self.sa_troop_avail = {}

        for key, short in self.SA_UNIT_DEFS:
            unit_frame = QFrame()
            unit_frame.setStyleSheet("border: 1px solid #ddd; border-radius: 2px; padding: 1px;")
            uf_layout = QVBoxLayout(unit_frame)
            uf_layout.setContentsMargins(2, 1, 2, 1)
            uf_layout.setSpacing(0)

            # ── Birim ikonu (16×16) ──
            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setFixedHeight(16)
            icon_lbl.setStyleSheet("border: none;")
            troop_icon_mgr.apply_to_label(icon_lbl, key)
            uf_layout.addWidget(icon_lbl)

            name_lbl = QLabel(short)
            name_lbl.setAlignment(Qt.AlignCenter)
            name_lbl.setStyleSheet("font-size: 9px; font-weight: bold; color: #555; border: none;")
            uf_layout.addWidget(name_lbl)

            spin = QSpinBox()
            spin.setRange(0, 99999)
            spin.setValue(0)
            spin.setFixedWidth(55)
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet("font-size: 10px; border: 1px solid #aaa;")
            uf_layout.addWidget(spin)
            self.sa_troop_inputs[key] = spin

            avail_lbl = QLabel("(0)")
            avail_lbl.setAlignment(Qt.AlignCenter)
            avail_lbl.setStyleSheet("font-size: 8px; color: #888; border: none;")
            uf_layout.addWidget(avail_lbl)
            self.sa_troop_avail[key] = avail_lbl

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

        # ═══════════════════════════════════════════
        #  ANA TABLO
        # ═══════════════════════════════════════════
        self.sa_table = QTreeWidget()
        self.sa_table.setAlternatingRowColors(True)
        self.sa_table.setRootIsDecorated(False)
        self.sa_table.setSelectionMode(QTreeWidget.ExtendedSelection)

        headers = [
            "Kaynak", "Hedef",
            "Mız", "Kıl", "Bal", "Okç", "Cas", "HSv", "AOk", "ASv", "Koç", "Man", "Şöv", "Soy",
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
            "QHeaderView::section { font-size: 9px; padding: 2px; }")

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

        # ── Kontrol listesi + başlangıçta devre dışı ──
        self._sa_controls = [
            self.sa_source_combo, self.sa_tgt_x, self.sa_tgt_y,
            self.sa_quick_target, self.cmd_type_combo,
            self.btn_set_arrive, self.btn_set_send,
            self.sa_time_date, self.sa_time_clock,
            self.sa_add_btn,
            self.sa_table, btn_del, btn_clear,
        ]
        for spin in self.sa_troop_inputs.values():
            self._sa_controls.append(spin)

        self.enable_sending_cb.toggled.connect(self._toggle_sending_army)
        self._toggle_sending_army(False)

        self.tabs.addTab(tab, "⚔️ Ordu Gönder")

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

    def _sa_on_source_changed(self, index):
        """Kaynak köy seçildiğinde asker mevcutlarını güncelle."""
        if index < 0:
            return
        village_id = self.sa_source_combo.currentData()
        if not village_id:
            # Mevcutları sıfırla
            for lbl in self.sa_troop_avail.values():
                lbl.setText("(0)")
                lbl.setStyleSheet("font-size: 8px; color: #999; border: none;")
            return

        # all_villages verisinden bul
        all_v = self._game_data.get("all_villages", [])
        found_troops = None
        for v in all_v:
            if v.get("id") == village_id:
                found_troops = v.get("troops", {})
                break

        # Tekli köy verisi fallback
        if found_troops is None:
            v = self._game_data.get("village", {})
            if v and v.get("id") == village_id:
                found_troops = self._game_data.get("troops", {})

        if found_troops is None:
            found_troops = {}

        for key, lbl in self.sa_troop_avail.items():
            count = found_troops.get(key, 0)
            lbl.setText(f"({count})")
            if count > 0:
                lbl.setStyleSheet("font-size: 8px; color: #228822; border: none;")
            else:
                lbl.setStyleSheet("font-size: 8px; color: #999; border: none;")

    def _sa_parse_target(self):
        text = self.sa_quick_target.text().strip()
        match = re.match(r'(\d{1,3})\s*[|,/]\s*(\d{1,3})', text)
        if match:
            self.sa_tgt_x.setValue(int(match.group(1)))
            self.sa_tgt_y.setValue(int(match.group(2)))
            self.sa_quick_target.setStyleSheet("")
        else:
            self.sa_quick_target.setStyleSheet("border: 1px solid #cc2222;")

    def _sa_toggle_time_mode(self):
        """Varış/Gönderim butonlarına basıldığında zaman giriş panelini göster/gizle."""
        sender = self.sender()
        active_style = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #d4b896,stop:1 #b89b6a);"
            "border: 2px solid #8a6d3b; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #3a2a0f;")
        normal_style = (
            "background: qlineargradient(y1:0,y2:1,stop:0 #f5e6c8,stop:1 #d4b896);"
            "border: 1px solid #b89b6a; border-radius: 3px; padding: 4px 12px;"
            "font-weight: bold; font-size: 11px; color: #5a3e1b;")

        if sender == self.btn_set_arrive:
            if self.btn_set_arrive.isChecked():
                self._sa_time_mode = "arrive"
                self.sa_time_label.setText("Varış zamanı:")
                self.sa_time_widget.setVisible(True)
                self._sa_fill_server_time()
                self.btn_set_arrive.setStyleSheet(active_style)
                self.btn_set_send.setChecked(False)
                self.btn_set_send.setStyleSheet(normal_style)
            else:
                self._sa_time_mode = None
                self.sa_time_widget.setVisible(False)
                self.btn_set_arrive.setStyleSheet(normal_style)

        elif sender == self.btn_set_send:
            if self.btn_set_send.isChecked():
                self._sa_time_mode = "send"
                self.sa_time_label.setText("Gönderim zamanı:")
                self.sa_time_widget.setVisible(True)
                self._sa_fill_server_time()
                self.btn_set_send.setStyleSheet(active_style)
                self.btn_set_arrive.setChecked(False)
                self.btn_set_arrive.setStyleSheet(normal_style)
            else:
                self._sa_time_mode = None
                self.sa_time_widget.setVisible(False)
                self.btn_set_send.setStyleSheet(normal_style)

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
                ms = time_match.group(4) if time_match.group(4) else "000"
                ms = ms.ljust(3, '0')
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

        # ── Yolculuk süresini hesapla ──
        import math
        distance = math.sqrt((tgt_x - src_x) ** 2 + (tgt_y - src_y) ** 2)

        travel_sec = self._sa_calc_travel_time(distance, troop_keys_sent)

        # ── Girilen zamanı parse et ──
        input_dt = self._sa_parse_time_input(time_date, time_clock)
        if input_dt is None:
            QMessageBox.warning(self, "Uyarı",
                "Zaman formatı hatalı!\n"
                "Tarih → GG.AA | Saat → SS:DD:SS:ms\n"
                "Örnek: 20.03  20:45:24:208")
            return

        # ── Gönderim / Varış / Dönüş hesapla ──
        # Yolculuk sadece saniye — ms değişmez, gönderim ms'si = varış ms'si
        travel_delta = datetime.timedelta(seconds=travel_sec)

        if self._sa_time_mode == "send":
            send_dt = input_dt
            arrive_dt = send_dt + travel_delta
        else:  # arrive
            arrive_dt = input_dt
            send_dt = arrive_dt - travel_delta

        return_dt = arrive_dt + travel_delta

        send_str = self._sa_format_time(send_dt)
        arrive_str = self._sa_format_time(arrive_dt)
        return_str = self._sa_format_time(return_dt, ms_zero=True)

        cmd_type = "Sld" if self.cmd_type_combo.currentIndex() == 0 else "Dst"
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
        mode_label = "Gönderim" if self._sa_time_mode == "send" else "Varış"
        self._add_log("KOMUT", "info",
            f"Komut eklendi: {cmd_type} {src_text} → ({tgt}) | "
            f"Mesafe: {distance:.2f} kare | Yolculuk: {travel_min:.1f}dk | "
            f"Gönderim: {send_str} | Varış: {arrive_str} | Dönüş: {return_str}")

    # ── YOLCULUK SÜRESİ HESAPLAMA ─────────────

    # Birim hızları (dakika/kare, varsayılan hız=1 dünya)
    UNIT_SPEEDS = {
        "spear": 18, "sword": 22, "axe": 18, "archer": 18,
        "spy": 9, "light": 10, "marcher": 10, "heavy": 11,
        "ram": 30, "catapult": 30, "knight": 10, "snob": 35,
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
            ms_str = tm.group(4) if tm.group(4) else "0"
            ms = int(ms_str.ljust(3, '0'))

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

    def _sa_update_totals(self):
        count = self.sa_table.topLevelItemCount()
        self.sa_totals_label.setText(f"TOPLAM: {count} komut")

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

        server_dt = self._dispatch_parse_server_time()
        if server_dt is None:
            return

        # Offset uygula: negatif = daha erken gönder, pozitif = daha geç gönder
        offset_ms = self.sa_offset_input.value() if hasattr(self, 'sa_offset_input') else 0

        for i in range(self.sa_table.topLevelItemCount()):
            item = self.sa_table.topLevelItem(i)
            if not item:
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
            diff_ms = (adjusted_send_dt - server_dt).total_seconds() * 1000

            # 5 saniye kala: token'ı önceden cache'le
            if 0 < diff_ms <= 5000 and state != "cached":
                item.setData(0, Qt.UserRole, "caching")
                self._dispatch_precache(item, i)

            # Zaman geldi: gönder
            elif diff_ms <= 0 and state not in ("caching",):
                item.setData(0, Qt.UserRole, "sending")
                for col in range(item.columnCount()):
                    item.setBackground(col, QColor("#fff8e0"))
                self._dispatch_send_command(item, i)

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
            ms_str = tm.group(4) if tm.group(4) else "0"
            ms = int(ms_str.ljust(3, '0'))

            return datetime.datetime(year, month, day, hour, minute, second, ms * 1000)
        except (ValueError, OverflowError):
            return None

    def _dispatch_parse_time_str(self, time_str):
        """Tablo zaman formatını datetime'a çevir.
        Format: "20.03'de 20:45:24:208"
        """
        try:
            m = re.match(r"(\d{1,2})\.(\d{1,2})'de (\d{1,2}):(\d{2}):(\d{2}):(\d{3})", time_str)
            if not m:
                return None
            day = int(m.group(1))
            month = int(m.group(2))
            hour = int(m.group(3))
            minute = int(m.group(4))
            second = int(m.group(5))
            ms = int(m.group(6))

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
                var form = doc.getElementById('command-data-form');
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

        # Hedef koordinatlar
        tgt_match = re.match(r'(\d+)\|(\d+)', tgt_text)
        if not tgt_match:
            self._dispatch_mark_error(item, "Hedef koordinat hatalı")
            return
        target_x = tgt_match.group(1)
        target_y = tgt_match.group(2)

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

        # Unique ID for this command's result
        cmd_id = f"cmd_{row_idx}_{id(item)}"

        # Cache key (precache'den)
        cache_key = item.data(1, Qt.UserRole) or ""

        send_js = f"""
        (function() {{
            var villageId = {village_id};
            var targetX = '{target_x}';
            var targetY = '{target_y}';
            var troops = {troops_js_obj};
            var attackType = '{attack_type}';
            var cmdId = '{cmd_id}';
            var cacheKey = '{cache_key}';

            if (!window.__tw_bot_results) window.__tw_bot_results = {{}};
            window.__tw_bot_results[cmdId] = 'SENDING';

            // Cache'den token'ları al veya yoksa GET yap
            var getTokens;
            var cached = window.__tw_bot_cache && window.__tw_bot_cache[cacheKey];
            if (cached) {{
                getTokens = Promise.resolve(JSON.parse(cached));
            }} else {{
                getTokens = fetch('/game.php?village=' + villageId + '&screen=place', {{credentials: 'same-origin'}})
                .then(function(r) {{ return r.text(); }})
                .then(function(html) {{
                    var doc = new DOMParser().parseFromString(html, 'text/html');
                    var form = doc.getElementById('command-data-form');
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

                // ADIM 1: POST -> try=confirm (askerler + koordinat + token)
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

                return fetch(formAction, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: fd.toString(),
                    credentials: 'same-origin'
                }});
            }})
            .then(function(r) {{ if (r) return r.text(); }})
            .then(function(confirmHtml) {{
                if (!confirmHtml) return;

                // ADIM 2: Onay -> ch token + POST action=command
                var doc2 = new DOMParser().parseFromString(confirmHtml, 'text/html');
                var cf = doc2.getElementById('command-data-form');
                if (!cf) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|Onay formu bulunamadi';
                    return;
                }}
                if (!cf.querySelector('input[name="ch"]')) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|ch token bulunamadi';
                    return;
                }}
                var cd = new URLSearchParams();
                cf.querySelectorAll('input[type="hidden"]').forEach(function(h) {{
                    if (h.name) cd.append(h.name, h.value);
                }});
                cd.append('submit_confirm', 'true');

                return fetch(cf.getAttribute('action'), {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: cd.toString(),
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

        self._add_log("GÖNDERİM", "info",
            f"Komut gönderiliyor: {src_text} → ({tgt_text}) | {cmd_type}")

        # JS'i çalıştır (hemen "DISPATCHED" döner, sonuç async gelir)
        self.browser.page().runJavaScript(send_js)

        # Sonucu polling ile kontrol et
        self._dispatch_poll_result(item, cmd_id, src_text, tgt_text, cmd_type, 0)

    def _dispatch_poll_result(self, item, cmd_id, src_text, tgt_text, cmd_type, attempt):
        """JS tarafındaki async fetch sonucunu polling ile kontrol et."""
        if attempt > 100:  # 100 × 200ms = 20sn timeout
            self._dispatch_mark_error(item, "Zaman aşımı")
            self._add_log("GÖNDERİM", "error",
                f"❌ Zaman aşımı: {src_text} → ({tgt_text})")
            return

        check_js = f"window.__tw_bot_results ? window.__tw_bot_results['{cmd_id}'] || 'WAITING' : 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str == "SENT_OK":
                self._dispatch_mark_sent(item)
                self._add_log("GÖNDERİM", "success",
                    f"✅ Komut gönderildi: {src_text} → ({tgt_text}) | {cmd_type}")
                # Temizle
                self.browser.page().runJavaScript(
                    f"if(window.__tw_bot_results) delete window.__tw_bot_results['{cmd_id}'];")

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                self._dispatch_mark_error(item, error)
                self._add_log("GÖNDERİM", "error",
                    f"❌ Gönderim hatası: {src_text} → ({tgt_text}) | {error}")

            elif result_str in ("WAITING", "SENDING"):
                # Henüz bitmedi, 200ms sonra tekrar kontrol et
                QTimer.singleShot(200, lambda: self._dispatch_poll_result(
                    item, cmd_id, src_text, tgt_text, cmd_type, attempt + 1))

        self.browser.page().runJavaScript(check_js, on_poll)

    def _dispatch_mark_sent(self, item):
        """Gönderilen satırı yeşile boyar."""
        for col in range(item.columnCount()):
            item.setBackground(col, QColor("#d4f0d4"))
            item.setForeground(col, QColor("#2a7a2a"))
        item.setData(0, Qt.UserRole, "sent")

    def _dispatch_mark_error(self, item, error_msg):
        """Hata olan satırı kırmızıya boyar."""
        for col in range(item.columnCount()):
            item.setBackground(col, QColor("#f0d4d4"))
            item.setForeground(col, QColor("#aa3333"))
        item.setData(0, Qt.UserRole, "error")
        # Açıklama olarak hata mesajını ID sütununa yaz
        item.setText(18, f"HATA")

    def _build_task_queue_tab(self):
        """Gömülü şablon tabanlı bina kuyruğu sekmesi.

        Bot kendi içinde bina şablonlarını tutar.
        Kullanıcı hazır şablonlardan seçebilir veya kendi şablonunu oluşturabilir.
        Bot, sırayla karargah sayfasından (screen=main) bina yükseltir.
        Oyunun hesap yöneticisi API'si kullanılmaz.
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ═══════════════════════════════════════════
        #  GÖMÜlÜ ŞABLONLAR
        # ═══════════════════════════════════════════
        self._bq_builtin_templates = {
            "Hammaddeler (Temel)": [
                ("main", 1), ("storage", 15), ("farm", 5),
                ("wood", 10), ("stone", 10), ("iron", 10),
                ("wood", 15), ("stone", 15), ("iron", 15),
                ("storage", 20), ("farm", 10),
                ("wood", 20), ("stone", 20), ("iron", 20),
                ("storage", 25), ("farm", 15),
                ("wood", 25), ("stone", 25), ("iron", 25),
                ("storage", 30), ("farm", 20),
                ("wood", 30), ("stone", 30), ("iron", 30),
                ("farm", 25), ("farm", 30),
            ],
            "Saldırgan": [
                ("main", 1), ("storage", 15),
                ("wood", 10), ("stone", 10), ("iron", 10), ("farm", 5),
                ("main", 11), ("place", 1),
                ("wood", 15), ("stone", 15), ("iron", 15), ("farm", 10),
                ("barracks", 10), ("storage", 20),
                ("wood", 20), ("stone", 20), ("iron", 20), ("farm", 15),
                ("main", 16), ("barracks", 20),
                ("wood", 25), ("stone", 25), ("iron", 25),
                ("storage", 25), ("farm", 20),
                ("smith", 10), ("stable", 10),
                ("wood", 30), ("stone", 30), ("iron", 30),
                ("storage", 30), ("farm", 25),
                ("main", 21), ("barracks", 25),
                ("stable", 20), ("smith", 20),
                ("wall", 10), ("market", 15),
                ("farm", 30), ("snob", 1), ("garage", 5),
                ("wall", 20), ("market", 25),
            ],
            "Savunucu": [
                ("main", 1), ("storage", 15),
                ("wood", 10), ("stone", 10), ("iron", 10), ("farm", 5),
                ("main", 11), ("place", 1),
                ("wood", 15), ("stone", 15), ("iron", 15), ("farm", 10),
                ("barracks", 15), ("wall", 10), ("storage", 20),
                ("wood", 20), ("stone", 20), ("iron", 20), ("farm", 15),
                ("main", 16), ("barracks", 25),
                ("wood", 25), ("stone", 25), ("iron", 25),
                ("storage", 25), ("farm", 20),
                ("smith", 10), ("stable", 10), ("wall", 20),
                ("wood", 30), ("stone", 30), ("iron", 30),
                ("storage", 30), ("farm", 25),
                ("main", 21), ("smith", 20),
                ("stable", 20), ("market", 15),
                ("farm", 30), ("snob", 1), ("garage", 5),
                ("market", 25), ("watchtower", 5),
            ],
            "Depo + Hammadde": [
                ("main", 1), ("storage", 15),
                ("stone", 1), ("wood", 1), ("iron", 1),
                ("stone", 5), ("wood", 5), ("iron", 5), ("farm", 5),
                ("storage", 20),
                ("wood", 10), ("stone", 10), ("iron", 10), ("farm", 10),
                ("main", 11),
                ("wood", 15), ("stone", 15), ("iron", 15), ("farm", 15),
                ("main", 16), ("storage", 25),
                ("wood", 20), ("stone", 20), ("iron", 20), ("farm", 20),
                ("main", 21), ("storage", 30),
                ("wood", 25), ("stone", 25), ("iron", 25), ("farm", 25),
                ("wood", 30), ("stone", 30), ("iron", 30), ("farm", 30),
                ("market", 10), ("market", 20), ("market", 25),
            ],
            "Hızlı Kışla": [
                ("main", 1), ("storage", 10), ("farm", 5),
                ("wood", 5), ("stone", 5), ("iron", 5),
                ("main", 5), ("barracks", 5),
                ("wood", 10), ("stone", 10), ("iron", 10), ("farm", 10),
                ("main", 11), ("barracks", 15),
                ("wood", 15), ("stone", 15), ("iron", 15), ("farm", 15),
                ("storage", 20), ("main", 16),
                ("barracks", 20), ("barracks", 25),
                ("wood", 20), ("stone", 20), ("iron", 20), ("farm", 20),
                ("storage", 25), ("main", 21),
            ],
        }
        # Kullanıcı şablonları (çalışma zamanında eklenir)
        self._bq_custom_templates = {}

        # ═══════════════════════════════════════════
        #  SATIR 1: Aktifleştir + Köy seçimi
        # ═══════════════════════════════════════════
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.bq_enable_cb = QCheckBox("Otomatik Bina Yükseltme Aktif")
        self.bq_enable_cb.setStyleSheet("font-weight: bold; font-size: 11px;")
        row1.addWidget(self.bq_enable_cb)

        row1.addSpacing(15)
        row1.addWidget(QLabel("Köy:"))
        self.bq_village_combo = QComboBox()
        self.bq_village_combo.setMinimumWidth(200)
        self.bq_village_combo.addItem("— Köy Seçin —")
        row1.addWidget(self.bq_village_combo)

        row1.addStretch()
        layout.addLayout(row1)

        # ═══════════════════════════════════════════
        #  SATIR 2: Şablon seçimi + Yükle + Yeni Şablon
        # ═══════════════════════════════════════════
        tmpl_row = QHBoxLayout()
        tmpl_row.setSpacing(6)

        tmpl_row.addWidget(QLabel("Şablon:"))
        self.bq_template_combo = QComboBox()
        self.bq_template_combo.setMinimumWidth(220)
        self._bq_refresh_template_combo()
        tmpl_row.addWidget(self.bq_template_combo)

        self.bq_load_tmpl_btn = QPushButton("📋 Kuyruğa Yükle")
        self.bq_load_tmpl_btn.setCursor(Qt.PointingHandCursor)
        self.bq_load_tmpl_btn.setObjectName("startBtn")
        self.bq_load_tmpl_btn.clicked.connect(self._bq_load_template)
        tmpl_row.addWidget(self.bq_load_tmpl_btn)

        tmpl_row.addSpacing(15)

        self.bq_new_tmpl_btn = QPushButton("+ Yeni Şablon Oluştur")
        self.bq_new_tmpl_btn.setCursor(Qt.PointingHandCursor)
        self.bq_new_tmpl_btn.clicked.connect(self._bq_create_template)
        tmpl_row.addWidget(self.bq_new_tmpl_btn)

        self.bq_save_tmpl_btn = QPushButton("💾 Tabloyu Şablon Olarak Kaydet")
        self.bq_save_tmpl_btn.setCursor(Qt.PointingHandCursor)
        self.bq_save_tmpl_btn.clicked.connect(self._bq_save_as_template)
        tmpl_row.addWidget(self.bq_save_tmpl_btn)

        self.bq_del_tmpl_btn = QPushButton("🗑 Şablonu Sil")
        self.bq_del_tmpl_btn.setCursor(Qt.PointingHandCursor)
        self.bq_del_tmpl_btn.clicked.connect(self._bq_delete_template)
        tmpl_row.addWidget(self.bq_del_tmpl_btn)

        tmpl_row.addStretch()
        layout.addLayout(tmpl_row)

        # ═══════════════════════════════════════════
        #  SATIR 3: Bina ekleme formu
        # ═══════════════════════════════════════════
        form_group = QGroupBox("Kuyruğa Bina Ekle")
        form_layout = QHBoxLayout()
        form_layout.setSpacing(6)

        form_layout.addWidget(QLabel("Bina:"))
        self.bq_building_combo = QComboBox()
        self.bq_building_combo.setMinimumWidth(140)
        self.BQ_BUILDINGS = [
            ("main", "Ana Bina"), ("barracks", "Kışla"), ("stable", "Ahır"),
            ("garage", "Atölye"), ("watchtower", "Gözetleme Kulesi"), ("snob", "Akademi"),
            ("smith", "Demirci"), ("place", "İçtima Meydanı"),
            ("statue", "Heykel"), ("market", "Pazar"),
            ("wood", "Oduncu"), ("stone", "Kil Ocağı"),
            ("iron", "Demir Madeni"), ("farm", "Çiftlik"),
            ("storage", "Depo"), ("hide", "Gizli Depo"), ("wall", "Sur"),
        ]
        for key, name in self.BQ_BUILDINGS:
            self.bq_building_combo.addItem(name, key)
        form_layout.addWidget(self.bq_building_combo)

        form_layout.addSpacing(6)
        form_layout.addWidget(QLabel("Hedef Seviye:"))
        self.bq_target_level = QSpinBox()
        self.bq_target_level.setRange(1, 30)
        self.bq_target_level.setValue(10)
        self.bq_target_level.setFixedWidth(55)
        form_layout.addWidget(self.bq_target_level)

        form_layout.addSpacing(6)
        self.bq_add_btn = QPushButton("+ Kuyruğa Ekle")
        self.bq_add_btn.setObjectName("startBtn")
        self.bq_add_btn.setCursor(Qt.PointingHandCursor)
        self.bq_add_btn.clicked.connect(self._bq_add_to_queue)
        form_layout.addWidget(self.bq_add_btn)

        form_layout.addStretch()
        form_group.setLayout(form_layout)
        layout.addWidget(form_group)

        # ═══════════════════════════════════════════
        #  ANA TABLO: İnşa kuyruğu
        # ═══════════════════════════════════════════
        self.bq_table = QTreeWidget()
        self.bq_table.setAlternatingRowColors(True)
        self.bq_table.setRootIsDecorated(False)
        self.bq_table.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.bq_table.setHeaderLabels(["#", "Bina", "Hedef Seviye", "Mevcut", "Durum"])
        self.bq_table.setColumnCount(5)
        col_widths = [40, 200, 100, 80, 250]
        for i, w in enumerate(col_widths):
            self.bq_table.setColumnWidth(i, w)
        self.bq_table.header().setDefaultAlignment(Qt.AlignCenter)
        layout.addWidget(self.bq_table, 1)

        # ═══════════════════════════════════════════
        #  ALT ÇUBUK
        # ═══════════════════════════════════════════
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self.bq_status_label = QLabel("Kuyruk: 0 emir")
        self.bq_status_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #333;")
        bottom.addWidget(self.bq_status_label)

        bottom.addStretch()

        bq_up_btn = QPushButton("⬆ Yukarı")
        bq_up_btn.setCursor(Qt.PointingHandCursor)
        bq_up_btn.clicked.connect(self._bq_move_up)
        bottom.addWidget(bq_up_btn)

        bq_down_btn = QPushButton("⬇ Aşağı")
        bq_down_btn.setCursor(Qt.PointingHandCursor)
        bq_down_btn.clicked.connect(self._bq_move_down)
        bottom.addWidget(bq_down_btn)

        bq_del_btn = QPushButton("Seçileni Sil")
        bq_del_btn.setCursor(Qt.PointingHandCursor)
        bq_del_btn.clicked.connect(self._bq_delete_selected)
        bottom.addWidget(bq_del_btn)

        bq_clear_btn = QPushButton("Tümünü Temizle")
        bq_clear_btn.setCursor(Qt.PointingHandCursor)
        bq_clear_btn.clicked.connect(lambda: (self.bq_table.clear(), self._bq_update_status()))
        bottom.addWidget(bq_clear_btn)

        bq_refresh_btn = QPushButton("🔄 Seviyeleri Güncelle")
        bq_refresh_btn.setCursor(Qt.PointingHandCursor)
        bq_refresh_btn.setToolTip("Karargahtan mevcut bina seviyelerini çeker")
        bq_refresh_btn.clicked.connect(self._bq_refresh_levels)
        bottom.addWidget(bq_refresh_btn)

        layout.addLayout(bottom)

        self.tabs.addTab(tab, "🏗️ Bina Kuyruğu")

        # ── Dahili durum ──
        self._bq_processing = False
        self._bq_current_levels = {}  # {"main": 21, "barracks": 20, ...}

        # Otomatik yükseltme timer'ı (10sn aralıkla karargahtan yükseltme)
        self._bq_timer = QTimer(self)
        self._bq_timer.timeout.connect(self._bq_auto_process)
        self._bq_timer.start(30000)  # 30sn fallback — asıl zamanlama inşaat bitiş süresine göre

    # ══════════════════════════════════════════════════
    #  ŞABLON YÖNETİMİ
    # ══════════════════════════════════════════════════

    def _bq_get_all_templates(self):
        """Gömülü + kullanıcı şablonlarını birleşik döndürür."""
        result = {}
        for name, items in self._bq_builtin_templates.items():
            result[name] = items
        for name, items in self._bq_custom_templates.items():
            result[name] = items
        return result

    def _bq_refresh_template_combo(self):
        """Şablon combobox'ını güncelle."""
        self.bq_template_combo.clear()
        all_t = self._bq_get_all_templates()
        for name in all_t:
            prefix = "📌 " if name in self._bq_builtin_templates else "👤 "
            self.bq_template_combo.addItem(f"{prefix}{name}", name)

    def _bq_load_template(self):
        """Seçili şablonu tabloya yükler."""
        tmpl_name = self.bq_template_combo.currentData()
        all_t = self._bq_get_all_templates()
        if tmpl_name not in all_t:
            QMessageBox.warning(self, "Uyarı", "Şablon bulunamadı!")
            return

        items = all_t[tmpl_name]
        self.bq_table.clear()

        bname = {k: n for k, n in self.BQ_BUILDINGS}

        for idx, (bkey, target_level) in enumerate(items):
            name = bname.get(bkey, bkey)
            cur = self._bq_current_levels.get(bkey, "?")

            if cur != "?" and int(cur) >= target_level:
                status = f"✅ Tamamlandı (mevcut: {cur})"
            elif cur != "?":
                status = f"Bekliyor (mevcut: {cur})"
            else:
                status = "Seviye bilinmiyor"

            row = QTreeWidgetItem([
                str(idx + 1), name, str(target_level), str(cur), status
            ])
            row.setData(1, Qt.UserRole, bkey)  # bina key sakla
            row.setTextAlignment(0, Qt.AlignCenter)
            row.setTextAlignment(2, Qt.AlignCenter)
            row.setTextAlignment(3, Qt.AlignCenter)

            if "Tamamlandı" in status:
                row.setForeground(4, QColor("#228822"))
                for col in range(5):
                    row.setBackground(col, QColor("#e8f5e8"))

            self.bq_table.addTopLevelItem(row)

        self._bq_update_status()
        self._add_log("BİNA", "info",
            f"Şablon yüklendi: {tmpl_name} — {len(items)} emir")

    def _bq_create_template(self):
        """Boş bir şablon oluştur."""
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Yeni Şablon", "Şablon adı:")
        if ok and name.strip():
            name = name.strip()
            if name in self._bq_get_all_templates():
                QMessageBox.warning(self, "Uyarı", f"'{name}' zaten mevcut!")
                return
            self._bq_custom_templates[name] = []
            self._bq_refresh_template_combo()
            # Yeni şablonu seç
            idx = self.bq_template_combo.findData(name)
            if idx >= 0:
                self.bq_template_combo.setCurrentIndex(idx)
            self.bq_table.clear()
            self._bq_update_status()
            self._add_log("BİNA", "success", f"Yeni şablon oluşturuldu: {name}")

    def _bq_save_as_template(self):
        """Tablodaki mevcut kuyruğu şablon olarak kaydet."""
        from PyQt5.QtWidgets import QInputDialog
        # Mevcut seçili şablon adını varsayılan olarak göster
        current_name = self.bq_template_combo.currentData() or ""
        name, ok = QInputDialog.getText(self, "Şablonu Kaydet",
            "Şablon adı:", text=current_name)
        if not ok or not name.strip():
            return
        name = name.strip()

        # Gömülü şablonun üzerine yazmayı engelle
        if name in self._bq_builtin_templates:
            QMessageBox.warning(self, "Uyarı",
                f"'{name}' gömülü şablondur, üzerine yazılamaz.\nFarklı bir ad girin.")
            return

        # Tablodan kuyruğu oku
        items = []
        for i in range(self.bq_table.topLevelItemCount()):
            row = self.bq_table.topLevelItem(i)
            if not row:
                continue
            bkey = row.data(1, Qt.UserRole)
            try:
                target = int(row.text(2))
            except ValueError:
                continue
            items.append((bkey, target))

        if not items:
            QMessageBox.warning(self, "Uyarı", "Kuyruk boş!")
            return

        self._bq_custom_templates[name] = items
        self._bq_refresh_template_combo()
        # Kaydedilen şablonu seç
        idx = self.bq_template_combo.findData(name)
        if idx >= 0:
            self.bq_template_combo.setCurrentIndex(idx)
        self._add_log("BİNA", "success",
            f"Şablon kaydedildi: {name} — {len(items)} emir")

    def _bq_delete_template(self):
        """Seçili kullanıcı şablonunu sil."""
        tmpl_name = self.bq_template_combo.currentData()
        if not tmpl_name:
            return
        if tmpl_name in self._bq_builtin_templates:
            QMessageBox.warning(self, "Uyarı",
                f"'{tmpl_name}' gömülü şablondur, silinemez.")
            return
        if tmpl_name not in self._bq_custom_templates:
            QMessageBox.warning(self, "Uyarı", "Silinecek kullanıcı şablonu bulunamadı.")
            return

        reply = QMessageBox.question(self, "Onay",
            f"'{tmpl_name}' şablonunu silmek istiyor musunuz?",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        del self._bq_custom_templates[tmpl_name]
        self._bq_refresh_template_combo()
        self._add_log("BİNA", "warn", f"Şablon silindi: {tmpl_name}")

    # ══════════════════════════════════════════════════
    #  KUYRUK DÜZENLEME
    # ══════════════════════════════════════════════════

    def _bq_add_to_queue(self):
        """Tabloya yeni bina emri ekle."""
        bkey = self.bq_building_combo.currentData()
        bname_dict = {k: n for k, n in self.BQ_BUILDINGS}
        name = bname_dict.get(bkey, bkey)
        target_level = self.bq_target_level.value()

        cur = self._bq_current_levels.get(bkey, "?")
        if cur != "?" and int(cur) >= target_level:
            status = f"✅ Tamamlandı (mevcut: {cur})"
        elif cur != "?":
            status = f"Bekliyor (mevcut: {cur})"
        else:
            status = "Seviye bilinmiyor"

        idx = self.bq_table.topLevelItemCount() + 1
        row = QTreeWidgetItem([
            str(idx), name, str(target_level), str(cur), status
        ])
        row.setData(1, Qt.UserRole, bkey)
        row.setTextAlignment(0, Qt.AlignCenter)
        row.setTextAlignment(2, Qt.AlignCenter)
        row.setTextAlignment(3, Qt.AlignCenter)

        if "Tamamlandı" in status:
            row.setForeground(4, QColor("#228822"))
            for col in range(5):
                row.setBackground(col, QColor("#e8f5e8"))

        self.bq_table.addTopLevelItem(row)
        self._bq_update_status()
        self._add_log("BİNA", "info",
            f"Kuyruğa eklendi: {name} → Seviye {target_level}")

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

    def _bq_delete_selected(self):
        for item in self.bq_table.selectedItems():
            idx = self.bq_table.indexOfTopLevelItem(item)
            if idx >= 0:
                self.bq_table.takeTopLevelItem(idx)
        self._bq_renumber()
        self._bq_update_status()

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

    def _bq_refresh_levels(self):
        """Karargah sayfasından mevcut bina seviyelerini çeker
        ve tablodaki durumları günceller."""
        if not self.browser:
            self._add_log("BİNA", "warn", "Tarayıcı hazır değil.")
            return

        village_id = self._bq_get_active_village_id()
        if not village_id:
            self._add_log("BİNA", "warn", "Köy verisi yok.")
            return

        self._add_log("BİNA", "info", "Bina seviyeleri çekiliyor...")

        fetch_js = f"""
        (function() {{
            window.__tw_bq_levels = 'LOADING';
            fetch('/game.php?village={village_id}&screen=main', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var levels = {{}};

                // id="main_buildrow_BUILDING" satırlarından seviye oku
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
                }});

                // Alternatif: game_data.village.buildings'den de alınabilir
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

                window.__tw_bq_levels = JSON.stringify({{status: 'OK', levels: levels}});
            }})
            .catch(function(err) {{
                window.__tw_bq_levels = JSON.stringify({{status: 'ERROR', message: String(err)}});
            }});
        }})();
        """
        self.browser.page().runJavaScript(fetch_js)
        self._bq_poll_levels(0)

    def _bq_poll_levels(self, attempt):
        if attempt > 60:
            self._add_log("BİNA", "error", "Seviye çekme zaman aşımı.")
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
                return

            if data.get("status") == "ERROR":
                self._add_log("BİNA", "error", f"Hata: {data.get('message', '?')}")
                return

            levels = data.get("levels", {})
            self._bq_current_levels = levels
            self._add_log("BİNA", "success",
                f"✅ Bina seviyeleri güncellendi: {len(levels)} bina")

            # Tablodaki durumları güncelle
            self._bq_update_table_statuses()

            self.browser.page().runJavaScript("window.__tw_bq_levels = null;")

        self.browser.page().runJavaScript(check_js, on_poll)

    def _bq_update_table_statuses(self):
        """Tablodaki tüm satırların durumlarını mevcut seviyelere göre güncelle."""
        for i in range(self.bq_table.topLevelItemCount()):
            item = self.bq_table.topLevelItem(i)
            if not item:
                continue
            bkey = item.data(1, Qt.UserRole)
            cur = self._bq_current_levels.get(bkey, None)
            try:
                target = int(item.text(2))
            except ValueError:
                continue

            if cur is not None:
                item.setText(3, str(cur))
                if cur >= target:
                    item.setText(4, f"✅ Tamamlandı (mevcut: {cur})")
                    item.setForeground(4, QColor("#228822"))
                    for col in range(5):
                        item.setBackground(col, QColor("#e8f5e8"))
                else:
                    item.setText(4, f"Bekliyor (mevcut: {cur})")
                    item.setForeground(4, QColor("#333333"))
                    for col in range(5):
                        item.setBackground(col, QColor("#ffffff"))

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
            return
        if not self.is_running:
            return
        if self._bq_processing:
            return
        if not self.browser:
            return

        village_id = self._bq_get_active_village_id()
        if not village_id:
            return

        # İlk bekleyen görevi bul
        # "Tamamlandı" ve "Hata" olanları atla, geri kalan her şeyi dene
        target_item = None
        target_idx = -1
        for i in range(self.bq_table.topLevelItemCount()):
            item = self.bq_table.topLevelItem(i)
            if not item:
                continue
            status = item.text(4)
            if "Tamamlandı" in status:
                continue
            if "❌" in status:
                continue
            # BUSY, NO_RES, Yükseltildi, Bekliyor — hepsini tekrar dene
            target_item = item
            target_idx = i
            break

        if target_item is None:
            return  # Yapılacak iş yok

        self._bq_processing = True
        building_key = target_item.data(1, Qt.UserRole)
        try:
            target_level = int(target_item.text(2))
        except ValueError:
            self._bq_processing = False
            return

        target_item.setText(4, "⏳ Kontrol ediliyor...")
        target_item.setForeground(4, QColor("#2d5a9e"))

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

                // İnşaat kuyruğu dolu mu kontrol et
                // #buildqueue içindeki satırlar (header hariç, gerçek inşaatlar)
                var buildqueue = doc.getElementById('buildqueue');
                var activeCount = 0;
                var earliestEnd = 0;  // en erken biten inşaatın unix timestamp'i
                var nowSec = Math.floor(Date.now() / 1000);
                if (buildqueue) {{
                    var allRows = buildqueue.querySelectorAll('tr');
                    allRows.forEach(function(r) {{
                        var timerEl = r.querySelector('[data-endtime]');
                        if (timerEl) {{
                            activeCount++;
                            var endtime = parseInt(timerEl.getAttribute('data-endtime'));
                            if (endtime > 0 && (earliestEnd === 0 || endtime < earliestEnd)) {{
                                earliestEnd = endtime;
                            }}
                        }} else if (r.querySelector('.timer')) {{
                            activeCount++;
                        }}
                    }});
                }}

                // Premium hesap max 2 eşzamanlı inşaat (Hesap Yöneticisi ile 5)
                var maxQueue = 2;
                if (activeCount >= maxQueue) {{
                    // Kalan süreyi saniye olarak hesapla
                    var remainSec = (earliestEnd > nowSec) ? (earliestEnd - nowSec) : 0;
                    window.__tw_bq_result = 'BUSY|' + currentLevel + '|' + activeCount + '|' + remainSec;
                    return;
                }}

                // Yükseltme butonu
                var btn = row.querySelector('a.btn-build');
                var upgradeUrl = null;
                if (btn) {{
                    upgradeUrl = btn.getAttribute('href');
                }}

                if (!upgradeUrl) {{
                    // build_options'dan link ara
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

                if (!upgradeUrl) {{
                    // Kaynak yetersiz veya ön koşul — maliyet ve mevcut kaynakları çek
                    var costWood = 0, costStone = 0, costIron = 0;
                    var costTds = row.querySelectorAll('td');
                    // Karargah tablosunda maliyet hücreleri: class="cost_wood", "cost_stone", "cost_iron"
                    var cwEl = row.querySelector('.cost_wood, [class*="cost_wood"]');
                    var csEl = row.querySelector('.cost_stone, [class*="cost_stone"]');
                    var ciEl = row.querySelector('.cost_iron, [class*="cost_iron"]');
                    if (cwEl) costWood = parseInt(cwEl.textContent.replace(/\\D/g, '')) || 0;
                    if (csEl) costStone = parseInt(csEl.textContent.replace(/\\D/g, '')) || 0;
                    if (ciEl) costIron = parseInt(ciEl.textContent.replace(/\\D/g, '')) || 0;

                    // Alternatif: span.icon + sonraki span'dan maliyet okuma
                    if (costWood === 0 && costStone === 0 && costIron === 0) {{
                        var spans = row.querySelectorAll('span.icon');
                        spans.forEach(function(sp) {{
                            var cls = sp.className || '';
                            var valEl = sp.parentElement;
                            var valTxt = valEl ? valEl.textContent.replace(/\\D/g, '') : '0';
                            var val = parseInt(valTxt) || 0;
                            if (cls.indexOf('wood') > -1) costWood = val;
                            else if (cls.indexOf('stone') > -1) costStone = val;
                            else if (cls.indexOf('iron') > -1) costIron = val;
                        }});
                    }}

                    // Mevcut kaynaklar ve üretim hızı (game_data'dan)
                    var curWood = 0, curStone = 0, curIron = 0;
                    var prodWood = 0, prodStone = 0, prodIron = 0;
                    try {{
                        var wd = document.getElementById('wood');
                        var st = document.getElementById('stone');
                        var ir = document.getElementById('iron');
                        if (wd) curWood = parseInt(wd.textContent.replace(/\\D/g, '')) || 0;
                        if (st) curStone = parseInt(st.textContent.replace(/\\D/g, '')) || 0;
                        if (ir) curIron = parseInt(ir.textContent.replace(/\\D/g, '')) || 0;

                        // Üretim hızı: title="Odun - saatte XXXX" formatında
                        if (wd && wd.title) {{
                            var pm = wd.title.match(/(\\d+)/);
                            if (pm) prodWood = parseInt(pm[1]) || 0;
                        }}
                        if (st && st.title) {{
                            var pm2 = st.title.match(/(\\d+)/);
                            if (pm2) prodStone = parseInt(pm2[1]) || 0;
                        }}
                        if (ir && ir.title) {{
                            var pm3 = ir.title.match(/(\\d+)/);
                            if (pm3) prodIron = parseInt(pm3[1]) || 0;
                        }}
                    }} catch(e) {{}}

                    window.__tw_bq_result = 'NO_RES|' + currentLevel +
                        '|' + costWood + '|' + costStone + '|' + costIron +
                        '|' + curWood + '|' + curStone + '|' + curIron +
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
                }} else if (resultHtml && resultHtml.indexOf('error') > -1) {{
                    var doc3 = new DOMParser().parseFromString(resultHtml, 'text/html');
                    var err = doc3.querySelector('.error_box, .error, p.error');
                    var errMsg = err ? err.textContent.trim().substring(0, 60) : 'Bilinmeyen hata';
                    window.__tw_bq_result = 'ERROR|' + errMsg;
                }} else {{
                    var level2 = cur.replace('UPGRADING|', '');
                    window.__tw_bq_result = 'UPGRADED|' + level2;
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
        if attempt > 60:
            item.setText(4, "Zaman aşımı")
            item.setForeground(4, QColor("#cc2222"))
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
                cur_level = result_str.split("|")[1]
                new_level = int(cur_level) + 1
                self._bq_current_levels[building_key] = new_level
                item.setText(3, str(new_level))

                if new_level >= target_level:
                    item.setText(4, f"✅ Tamamlandı (mevcut: {new_level})")
                    item.setForeground(4, QColor("#228822"))
                    for col in range(item.columnCount()):
                        item.setBackground(col, QColor("#e8f5e8"))
                    self._add_log("BİNA", "success",
                        f"✅ {building_key} → Seviye {new_level} — hedef ulaşıldı")
                else:
                    item.setText(4, f"Yükseltildi → {new_level} (hedef: {target_level})")
                    item.setForeground(4, QColor("#2d5a9e"))
                    self._add_log("BİNA", "success",
                        f"✅ {building_key} → Seviye {new_level} (hedef: {target_level})")

                self._bq_processing = False
                # Hemen sonraki görevi dene (2sn gecikme — sunucu yükü azaltmak için)
                QTimer.singleShot(2000, self._bq_auto_process)

            elif result_str.startswith("DONE|"):
                cur_level = result_str.split("|")[1]
                self._bq_current_levels[building_key] = int(cur_level)
                item.setText(3, cur_level)
                item.setText(4, f"✅ Tamamlandı (mevcut: {cur_level})")
                item.setForeground(4, QColor("#228822"))
                for col in range(item.columnCount()):
                    item.setBackground(col, QColor("#e8f5e8"))
                self._add_log("BİNA", "info",
                    f"{building_key} zaten Seviye {cur_level} — hedef ulaşılmış")
                self._bq_processing = False
                # Hemen sonraki göreve geç
                QTimer.singleShot(500, self._bq_auto_process)

            elif result_str.startswith("BUSY|"):
                parts = result_str.split("|")
                cur_level = parts[1] if len(parts) > 1 else "?"
                queue_count = parts[2] if len(parts) > 2 else "?"
                remain_sec = int(parts[3]) if len(parts) > 3 else 0

                item.setText(3, cur_level)

                if remain_sec > 0:
                    # İnşaat bitene kadar bekle + 2sn güvenlik payı
                    wait_sec = remain_sec + 2
                    mins = remain_sec // 60
                    secs = remain_sec % 60
                    item.setText(4,
                        f"⏳ Kuyruk dolu ({queue_count}) — {mins}dk {secs}sn sonra tekrar")
                    item.setForeground(4, QColor("#aa6600"))
                    self._add_log("BİNA", "info",
                        f"İnşaat kuyruğu dolu ({queue_count} aktif) — "
                        f"{mins}dk {secs}sn sonra tekrar denenecek")
                    self._bq_processing = False
                    # Normal timer yerine özel gecikme ile tekrar dene
                    QTimer.singleShot(wait_sec * 1000, self._bq_auto_process)
                else:
                    item.setText(4, f"⏳ Kuyruk dolu ({queue_count}) — bekleniyor...")
                    item.setForeground(4, QColor("#aa6600"))
                    self._bq_processing = False

            elif result_str.startswith("NO_RES|"):
                parts = result_str.split("|")
                cur_level = parts[1] if len(parts) > 1 else "?"
                item.setText(3, cur_level)

                # Maliyet ve kaynak verilerini parse et
                # Format: NO_RES|level|costW|costS|costI|curW|curS|curI|prodW|prodS|prodI
                wait_sec = 120  # varsayılan 2dk
                detail = ""

                if len(parts) >= 11:
                    try:
                        cost_w = int(parts[2])
                        cost_s = int(parts[3])
                        cost_i = int(parts[4])
                        cur_w = int(parts[5])
                        cur_s = int(parts[6])
                        cur_i = int(parts[7])
                        prod_w = int(parts[8])   # saatte
                        prod_s = int(parts[9])
                        prod_i = int(parts[10])

                        # Her kaynak için eksik miktarı ve bekleme süresini hesapla
                        max_wait = 0
                        missing = []

                        for res_name, cost, cur, prod in [
                            ("Odun", cost_w, cur_w, prod_w),
                            ("Kil", cost_s, cur_s, prod_s),
                            ("Demir", cost_i, cur_i, prod_i),
                        ]:
                            deficit = cost - cur
                            if deficit > 0:
                                if prod > 0:
                                    # Saatlik üretim → saniyeye çevir
                                    sec_needed = (deficit / prod) * 3600
                                    if sec_needed > max_wait:
                                        max_wait = sec_needed
                                    missing.append(f"{res_name}: {deficit:,} eksik ({int(sec_needed)}sn)")
                                else:
                                    missing.append(f"{res_name}: {deficit:,} eksik (üretim yok!)")
                                    max_wait = max(max_wait, 600)  # 10dk fallback

                        if max_wait > 0:
                            wait_sec = int(max_wait) + 5  # +5sn güvenlik payı
                            detail = " | ".join(missing)
                        else:
                            # Maliyet 0 ama buton yok — muhtemelen ön koşul
                            wait_sec = 120
                            detail = "Ön koşul karşılanmamış olabilir"

                    except (ValueError, IndexError):
                        detail = "Maliyet verisi okunamadı"
                        wait_sec = 120

                mins = wait_sec // 60
                secs = wait_sec % 60

                status_text = f"⏳ Kaynak yetersiz — {mins}dk {secs}sn sonra tekrar"
                item.setText(4, status_text)
                item.setForeground(4, QColor("#aa6600"))

                if detail:
                    self._add_log("BİNA", "info",
                        f"Kaynak yetersiz: {building_key} | {detail} | "
                        f"Tekrar: {mins}dk {secs}sn sonra")
                else:
                    self._add_log("BİNA", "info",
                        f"Kaynak yetersiz: {building_key} — {mins}dk {secs}sn sonra tekrar")

                self._bq_processing = False
                QTimer.singleShot(wait_sec * 1000, self._bq_auto_process)

            elif result_str.startswith("UPGRADING|"):
                QTimer.singleShot(300, lambda: self._bq_poll_result(
                    item, row_idx, building_key, target_level, attempt + 1))
                return

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                item.setText(4, f"❌ Hata: {error[:40]}")
                item.setForeground(4, QColor("#cc2222"))
                self._add_log("BİNA", "error", f"❌ {building_key}: {error}")
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
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Üst kontrol çubuğu
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)

        ctrl_row.addWidget(QLabel("Merkez X:"))
        self.map_center_x = QSpinBox()
        self.map_center_x.setRange(0, 999)
        self.map_center_x.setValue(500)
        self.map_center_x.setFixedWidth(60)
        ctrl_row.addWidget(self.map_center_x)

        ctrl_row.addWidget(QLabel("Y:"))
        self.map_center_y = QSpinBox()
        self.map_center_y.setRange(0, 999)
        self.map_center_y.setValue(500)
        self.map_center_y.setFixedWidth(60)
        ctrl_row.addWidget(self.map_center_y)

        ctrl_row.addSpacing(10)
        ctrl_row.addWidget(QLabel("Yarıçap:"))
        self.map_radius = QSpinBox()
        self.map_radius.setRange(5, 100)
        self.map_radius.setValue(30)
        self.map_radius.setFixedWidth(60)
        ctrl_row.addWidget(self.map_radius)

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

        self.map_center_me_btn = QPushButton("📍 Köyüme Git")
        self.map_center_me_btn.setCursor(Qt.PointingHandCursor)
        self.map_center_me_btn.clicked.connect(self._map_center_on_me)
        ctrl_row.addWidget(self.map_center_me_btn)

        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        # Harita widget
        self.map_widget = MapCanvasWidget()
        self.map_widget.setMinimumHeight(400)
        self.map_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Sinyalleri bağla
        self.map_widget.village_double_clicked.connect(self._map_on_village_dblclick)
        self.map_widget.view_changed.connect(self._map_on_view_changed)
        layout.addWidget(self.map_widget, 1)

        # Lejand
        legend_row = QHBoxLayout()
        legend_items = [
            ("🟡 Senin köylerin", "#e8e832"),
            ("🔵 Klan", "#4488cc"),
            ("🔴 Düşman", "#cc2222"),
            ("⚫ Barbar", "#666666"),
            ("🟤 Diğer", "#dd8833"),
        ]
        for text, color in legend_items:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 10px; color: {color}; font-weight: bold; padding: 0 6px;")
            legend_row.addWidget(lbl)
        legend_row.addStretch()
        self.map_village_count_label = QLabel("Köy: 0")
        self.map_village_count_label.setStyleSheet("font-size: 10px; color: #555;")
        legend_row.addWidget(self.map_village_count_label)
        layout.addLayout(legend_row)

        # Barbar köyleri tablosu
        barb_group = QGroupBox("Yakındaki Barbar Köyleri")
        barb_layout = QVBoxLayout()
        self.map_barb_table = QTreeWidget()
        self.map_barb_table.setAlternatingRowColors(True)
        self.map_barb_table.setRootIsDecorated(False)
        self.map_barb_table.setHeaderLabels(["Koordinat", "Puan", "Mesafe", "Köy Adı", "Durum"])
        self.map_barb_table.header().setSectionResizeMode(QHeaderView.Stretch)
        self.map_barb_table.setMaximumHeight(200)
        barb_layout.addWidget(self.map_barb_table)
        barb_group.setLayout(barb_layout)
        layout.addWidget(barb_group)

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

        btn_hsv = QPushButton("25 HSv")
        btn_hsv.setCursor(Qt.PointingHandCursor)
        btn_hsv.clicked.connect(lambda: self._farm_template({"light": 25}))
        farm_row3.addWidget(btn_hsv)

        btn_bal_hsv = QPushButton("10 Bal + 15 HSv")
        btn_bal_hsv.setCursor(Qt.PointingHandCursor)
        btn_bal_hsv.clicked.connect(lambda: self._farm_template({"axe": 10, "light": 15}))
        farm_row3.addWidget(btn_bal_hsv)

        btn_cas = QPushButton("1 Cas")
        btn_cas.setCursor(Qt.PointingHandCursor)
        btn_cas.clicked.connect(lambda: self._farm_template({"spy": 1}))
        farm_row3.addWidget(btn_cas)

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

        # Harita verisi
        self._map_villages = []
        self._map_data_loaded = False

        self.tabs.addTab(tab, "🗺️ Harita")

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

                // Oyuncuları parse et — id,name,ally_id,villages,points,rank
                var players = {};
                var pLines = playerTxt.trim().split('\\n');
                for (var p = 0; p < pLines.length; p++) {
                    var pp = pLines[p].split(',');
                    if (pp.length >= 3) {
                        var pid = parseInt(pp[0]);
                        var pname = decodeURIComponent(pp[1].replace(/\\+/g, ' '));
                        players[pid] = pname;
                    }
                }

                // Köyleri parse et
                var lines = villageTxt.trim().split('\\n');
                var villages = [];
                for (var i = 0; i < lines.length; i++) {
                    var parts = lines[i].split(',');
                    if (parts.length >= 7) {
                        var playerId = parseInt(parts[4]);
                        villages.push({
                            id: parseInt(parts[0]),
                            name: decodeURIComponent(parts[1].replace(/\\+/g, ' ')),
                            x: parseInt(parts[2]),
                            y: parseInt(parts[3]),
                            player_id: playerId,
                            player_name: players[playerId] || '',
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
        radius = self.map_radius.value()
        show_barbs = self.map_show_barbs.isChecked()
        show_players = self.map_show_players.isChecked()

        player_id = self._game_data.get("player", {}).get("id", 0)

        import math
        all_colored = []
        barb_list = []

        for v in self._map_villages:
            vx = v.get("x")
            vy = v.get("y")
            if vx is None or vy is None:
                continue

            pid = v["player_id"]
            is_barb = pid == 0

            if is_barb and not show_barbs:
                continue
            if not is_barb and not show_players:
                continue

            # Renk belirle
            if pid == player_id:
                color = "#e8e832"  # Sarı — kendi
                vtype = "own"
            elif is_barb:
                color = "#666666"  # Gri — barbar
                vtype = "barb"
            else:
                color = "#dd8833"  # Turuncu — diğer
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
        barb_list.sort(key=lambda b: b["dist"])
        self.map_barb_table.clear()
        for b in barb_list[:100]:  # İlk 100
            coord = f"({b['x']}|{b['y']})"
            item = QTreeWidgetItem([coord, str(b["points"]), f"{b['dist']:.1f}", b["name"], ""])
            item.setTextAlignment(1, Qt.AlignCenter)
            item.setTextAlignment(2, Qt.AlignCenter)
            item.setData(0, Qt.UserRole, {"x": b["x"], "y": b["y"]})
            self.map_barb_table.addTopLevelItem(item)

        # Farm indexini sıfırla
        self._farm_barb_index = 0
        self._farm_sent_count = 0
        self._farm_update_labels()

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

    def _map_on_view_changed(self, cx, cy, zoom):
        """Harita widget'ından pan/zoom sinyali geldiğinde spinbox'ları güncelle."""
        # Spinbox değişim sinyalini geçici olarak engelle (sonsuz döngü önleme)
        self.map_center_x.blockSignals(True)
        self.map_center_y.blockSignals(True)
        self.map_radius.blockSignals(True)

        self.map_center_x.setValue(int(cx))
        self.map_center_y.setValue(int(cy))
        self.map_radius.setValue(int(zoom))

        self.map_center_x.blockSignals(False)
        self.map_center_y.blockSignals(False)
        self.map_radius.blockSignals(False)

    # ── OTOMATİK FARM SİSTEMİ ─────────────────

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

        # Aralık kontrolü
        import time
        now = time.time()
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
                    target = item.data(0, Qt.UserRole)
                    target_item = item
                    self._farm_barb_index = idx + 1
                    break
            idx += 1
            checked += 1

        if not target:
            # Tüm köyler gönderildi, başa sar
            self.farm_status_label.setText("Durum: Tur tamamlandı, başa sarılıyor...")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
            for i in range(total):
                it = self.map_barb_table.topLevelItem(i)
                if it:
                    it.setText(4, "")
                    it.setForeground(4, QColor("#000000"))
            self._farm_barb_index = 0
            self._farm_sent_count = 0
            self._farm_update_labels()
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
                cd.append('submit_confirm', 'true');
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
        layout.addLayout(row2)

        # Köy tablosu
        self.scav_table = QTreeWidget()
        self.scav_table.setAlternatingRowColors(True)
        self.scav_table.setRootIsDecorated(False)
        self.scav_table.setHeaderLabels([
            "Köy", "Sv1", "Sv2", "Sv3", "Sv4", "Evdeki Asker", "Durum"
        ])
        for i in range(7):
            self.scav_table.header().setSectionResizeMode(i,
                QHeaderView.Stretch if i in (0, 5, 6) else QHeaderView.ResizeToContents)
        layout.addWidget(self.scav_table, 1)

        layout.addStretch()
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

    # ── TEMİZLİK (TOPLU) FONKSİYONLAR ────────

    SCAV_CARRY = {
        "spear": 25, "sword": 15, "axe": 10, "archer": 10,
        "light": 80, "marcher": 50, "heavy": 50, "knight": 100,
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
        self._scav_process()

    def _scav_tick(self):
        import time
        if not self._scav_active:
            # Aktif olmasa bile tablodaki geri sayımları güncelle
            self._scav_update_countdowns()
            return
        if self._scav_sending or self._scav_checking:
            self._scav_update_countdowns()
            return
        if not self.browser:
            return
        now = time.time()
        self._scav_update_countdowns()
        if self._scav_next_send > now:
            remaining = int(self._scav_next_send - now)
            self.scav_status_label.setText(f"Durum: Sonraki kontrol {remaining}sn")
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")
            return
        self._scav_process()

    def _scav_update_countdowns(self):
        """Tablodaki geri sayımları her saniye güncelle."""
        import time
        now = time.time()
        for i in range(self.scav_table.topLevelItemCount()):
            item = self.scav_table.topLevelItem(i)
            if not item:
                continue
            # Sv1-Sv4 sütunları (index 1-4)
            for col in range(1, 5):
                rt_data = item.data(col, Qt.UserRole)
                if rt_data and isinstance(rt_data, (int, float)) and rt_data > 0:
                    rem = max(0, int(rt_data - now))
                    if rem > 0:
                        mins, secs = divmod(rem, 60)
                        hrs, mins = divmod(mins, 60)
                        item.setText(col, f"{hrs:02d}:{mins:02d}:{secs:02d}")
                    else:
                        item.setText(col, "✓ Bitti")
                        item.setForeground(col, QColor("#228822"))

    def _scav_process(self):
        """Mass scavenging sayfasından tüm köylerin verisini çek."""
        if not self.browser:
            return
        self._scav_checking = True

        village_id = self._game_data.get("village", {}).get("id", "")
        if not village_id:
            self._scav_checking = False
            return

        self.scav_status_label.setText("Durum: Veriler çekiliyor...")
        self.scav_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")

        selected_units = [k for k, cb in self.scav_unit_cbs.items() if cb.isChecked()]
        selected_js = json.dumps(selected_units)

        # Mass scavenging sayfasını fetch et (tüm köyler tek sayfada veya sayfalanmış)
        fetch_js = f"""
        (function() {{
            window.__tw_scav_mass = 'LOADING';
            var baseUrl = '/game.php?village={village_id}&screen=place&mode=scavenge_mass';

            fetch(baseUrl, {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                try {{
                    // Köy verilerini bul: [...] array
                    var match = html.match(/\\[(\\{{"village_id"[\\s\\S]*?\\}})\\]/);
                    if (!match) {{
                        window.__tw_scav_mass = JSON.stringify({{status:'ERROR',message:'Köy verisi bulunamadi'}});
                        return;
                    }}
                    var villages = JSON.parse('[' + match[1] + ']');
                    var selectedUnits = {selected_js};

                    var result = [];
                    villages.forEach(function(v) {{
                        // Evdeki seçili askerleri
                        var available = {{}};
                        var totalHome = 0;
                        selectedUnits.forEach(function(u) {{
                            var c = v.unit_counts_home[u] || 0;
                            if (c > 0) {{ available[u] = c; totalHome += c; }}
                        }});

                        var opts = {{}};
                        for (var id in v.options) {{
                            var opt = v.options[id];
                            var sq = null;
                            if (opt.scavenging_squad) {{
                                sq = {{
                                    unit_counts: opt.scavenging_squad.unit_counts,
                                    return_time: opt.scavenging_squad.return_time
                                }};
                            }}
                            opts[id] = {{
                                is_locked: opt.is_locked,
                                is_active: opt.scavenging_squad !== null,
                                return_time: opt.scavenging_squad ? opt.scavenging_squad.return_time : null,
                                unlock_time: opt.unlock_time || null,
                                squad: sq
                            }};
                        }}

                        result.push({{
                            village_id: v.village_id,
                            name: v.village_name,
                            available: available,
                            total_home: totalHome,
                            options: opts,
                            has_rally_point: v.has_rally_point
                        }});
                    }});

                    window.__tw_scav_mass = JSON.stringify({{status:'OK', villages: result}});
                }} catch(e) {{
                    window.__tw_scav_mass = JSON.stringify({{status:'ERROR',message:e.message}});
                }}
            }})
            .catch(function(err) {{
                window.__tw_scav_mass = JSON.stringify({{status:'ERROR',message:String(err)}});
            }});
        }})();
        """

        self.browser.page().runJavaScript(fetch_js)
        self._scav_poll_mass(0)

    def _scav_poll_mass(self, attempt):
        """Mass scav verisini polling ile al."""
        import time
        if attempt > 40:
            self.scav_status_label.setText("Durum: Veri alınamadı")
            self._scav_checking = False
            return
        check_js = "window.__tw_scav_mass || 'WAITING';"
        def on_poll(result):
            result_str = str(result) if result else "WAITING"
            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(300, lambda: self._scav_poll_mass(attempt + 1))
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
            self._scav_villages_cache = villages
            self._scav_update_table(villages)

            # Boş seviyeleri olan köyler için toplu gönderim hazırla
            if self._scav_active:
                self._scav_send_all(villages)
            else:
                self._scav_checking = False

        self.browser.page().runJavaScript(check_js, on_poll)

    def _scav_update_table(self, villages):
        """Tabloyu köy verileriyle güncelle."""
        import time
        now = time.time()
        self.scav_table.clear()

        unit_short = {"spear":"Mız","sword":"Kıl","axe":"Bal","archer":"Okç",
                      "light":"HSv","marcher":"AOk","heavy":"ASv","knight":"Şöv",
                      "spy":"Cas","ram":"Koç","catapult":"Man","snob":"Mis"}

        for v in villages:
            name = v.get("name", "?")
            opts = v.get("options", {})
            available = v.get("available", {})

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

            item = QTreeWidgetItem([name, sv_texts[0], sv_texts[1], sv_texts[2], sv_texts[3], troops_text, status])
            for col in range(1, 5):
                item.setForeground(col, QColor(sv_colors[col - 1]))
                item.setTextAlignment(col, Qt.AlignCenter)
                item.setData(col, Qt.UserRole, sv_return_times[col - 1])  # Geri sayım için
            item.setForeground(6, QColor(status_color))
            item.setTextAlignment(6, Qt.AlignCenter)
            item.setData(0, Qt.UserRole, v)  # Köy verisini sakla
            self.scav_table.addTopLevelItem(item)

    def _scav_send_all(self, villages):
        """Tüm köylerdeki boş seviyelere toplu gönderim yap."""
        import time
        # Seviye → ganimetin ne kadarını alır (göreceli ağırlık)
        loot_factors = {1: 0.10, 2: 0.25, 3: 0.50, 4: 0.75}
        now = time.time()
        all_squads = []
        sent_villages = 0

        for v in villages:
            if not v.get("has_rally_point"):
                continue

            opts = v.get("options", {})
            available = dict(v.get("available", {}))
            village_id = v.get("village_id")
            total_troops = sum(available.values())

            # Boş seviyeleri yüksekten düşüğe topla
            free_options = []
            for oid in ["4", "3", "2", "1"]:
                opt = opts.get(oid, {})
                if not opt.get("is_locked") and not opt.get("is_active"):
                    free_options.append(int(oid))

            if not free_options or total_troops < 10:
                continue

            # Her seviyenin göreceli ağırlığını hesapla
            total_weight = sum(loot_factors[o] for o in free_options)

            # remaining: her adımda azalan havuz
            remaining = dict(available)
            village_squads = []

            for i, opt_id in enumerate(free_options):
                is_last = (i == len(free_options) - 1)

                if is_last:
                    # Son seviye: kalan tüm askerler buraya
                    troops_for_level = {u: c for u, c in remaining.items() if c > 0}
                else:
                    share = loot_factors[opt_id] / total_weight
                    troops_for_level = {}
                    for unit, count in remaining.items():
                        if count <= 0:
                            continue
                        alloc = max(1, round(count * share))
                        alloc = min(alloc, count)
                        troops_for_level[unit] = alloc

                level_total = sum(troops_for_level.values())
                if level_total < 10:
                    # Bu seviye için yeterli asker yok; kalanları bir sonrakine bırak
                    continue

                village_squads.append({
                    "village_id": village_id,
                    "candidate_squad": {
                        "unit_counts": troops_for_level,
                        "carry_max": 9999999999
                    },
                    "option_id": opt_id,
                    "use_premium": False
                })

                # Kullanılan askerleri havuzdan düş
                for u, c in troops_for_level.items():
                    remaining[u] = remaining.get(u, 0) - c

            if village_squads:
                all_squads.extend(village_squads)
                sent_villages += 1

        if not all_squads:
            self._scav_schedule_next_mass(villages)
            self._scav_checking = False
            return

        self._add_log("TEMİZLİK", "info",
            f"Toplu gönderim: {len(all_squads)} temizlik, {sent_villages} köy")

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
        """Tüm köylerdeki en yakın dönüşü bul, o zamana kadar bekle."""
        import time
        now = time.time()
        nearest = None

        for v in villages:
            for oid, opt in v.get("options", {}).items():
                rt = opt.get("return_time")
                if rt and rt > now:
                    if nearest is None or rt < nearest:
                        nearest = rt

        if nearest:
            wait = max(5, int(nearest - now) + 3)
            self._scav_next_send = now + wait
            self.scav_status_label.setText(f"Durum: Tümü dolu, {wait}sn sonra dönecek")
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
            self._add_log("TEMİZLİK", "info", f"⏳ En yakın dönüş {wait}sn sonra")
        else:
            self._scav_next_send = now + 60
            self.scav_status_label.setText("Durum: 60sn sonra tekrar kontrol")
            self.scav_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")

    # ── RAPORLAR ───────────────────────────────

    def _build_reports_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)

        tree = QTreeWidget()
        tree.setAlternatingRowColors(True)
        tree.setHeaderLabels(["Tarih", "Tür", "Kaynak", "Hedef", "Sonuç"])
        tree.header().setSectionResizeMode(QHeaderView.Stretch)

        reports = [
            ("2024-01-20 18:45", "Saldırı", "Köy 1 (502|446)", "Köy 12 (505|448)", "Zafer"),
            ("2024-01-20 18:40", "Farm", "Köy 1 (502|446)", "Köy 23 (512|455)", "Zafer"),
            ("2024-01-20 18:35", "Savunma", "Köy 3 (508|450)", "Köy 1 (502|446)", "Kayıp"),
            ("2024-01-20 18:30", "Keşif", "Köy 1 (502|446)", "Köy 45 (518|462)", "Başarılı"),
        ]
        for r in reports:
            tree.addTopLevelItem(QTreeWidgetItem(list(r)))

        layout.addWidget(tree)
        self.tabs.addTab(tab, "📊 Raporlar")

    # ── AYARLAR ────────────────────────────────

    def _build_settings_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)

        gen_group = QGroupBox("Genel Ayarlar")
        gen_layout = QFormLayout()
        gen_layout.setSpacing(8)
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

        save_btn = QPushButton("Ayarları Kaydet")
        save_btn.setObjectName("startBtn")
        save_btn.setCursor(Qt.PointingHandCursor)
        layout.addWidget(save_btn, alignment=Qt.AlignCenter)

        layout.addStretch()
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
        self.log_filter.addItems(["Tümü", "SİSTEM", "FARM", "TAR", "BİNA", "UYARI", "HATA"])
        toolbar.addWidget(self.log_filter)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.log_text = QTextEdit()
        self.log_text.setObjectName("logText")
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        self.tabs.addTab(tab, "📝 Loglar")

        self._add_log("SİSTEM", "info", "Uygulama başlatıldı. Tribal Wars Bot v1.0.0")
        self._add_log("SİSTEM", "info", f"Harita yüklendi. {len(self.villages)} köy bulundu.")
        self._add_log("TAR", "success", "Chromium tarayıcı hazır. Anti-detection aktif.")
        self._add_log("TAR", "info", "Stealth profil: navigator.webdriver=undefined, sahte plugin/dil/WebGL")
        self._add_log("SİSTEM", "info", "Bot bağlantı için hazır. Başlat'a basın.")

    # ── BOT KONTROLÜ ───────────────────────────

    def _start_bot(self):
        username = self.login_input.text().strip()
        password = self.password_input.text().strip()

        if not username or not password:
            QMessageBox.warning(self, "Uyarı", "Kullanıcı adı ve şifre giriniz!")
            return

        self.is_running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_indicator.setText("● AKTİF")
        self.status_indicator.setStyleSheet("color: #228822; font-weight: bold; font-size: 11px;")
        self._add_log("SİSTEM", "success", "Bot başlatıldı!")
        self._update_status()

        # Tarayıcı sekmesine geç
        self.tabs.setCurrentIndex(0)

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
            
            // Dünyaları topla
            var worlds = [];
            var worldLinks = document.querySelectorAll('a.world-select');
            worldLinks.forEach(function(a) {
                var span = a.querySelector('span');
                var name = span ? span.textContent.trim() : a.textContent.trim();
                var href = a.getAttribute('href');
                var isActive = span ? span.classList.contains('world_button_active') : false;
                worlds.push({
                    name: name,
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
            self.browser.navigate(full_url)

    # ── OYUN VERİSİ ÇEKME ─────────────────────

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

            // Tüm köyleri combined_table'dan çek
            result.all_villages = [];
            var table = document.getElementById('combined_table');
            var unitNames = game_data.units || [];
            
            if (table) {
                var rows = table.querySelectorAll('tr.row_a, tr.row_b');
                rows.forEach(function(row) {
                    var vill = {};
                    
                    // Köy adı ve ID — quickedit-vn span'dan
                    var qe = row.querySelector('.quickedit-vn');
                    if (qe) {
                        vill.id = parseInt(qe.getAttribute('data-id')) || 0;
                    }
                    
                    var label = row.querySelector('.quickedit-label');
                    if (label) {
                        vill.name = label.getAttribute('data-text') || label.textContent.trim();
                        // Koordinatları text'ten parse et: "Köy adı (508|477) K45"
                        var fullText = label.textContent.trim();
                        var coordMatch = fullText.match(/[(](\\d+)[|](\\d+)[)]/);
                        if (coordMatch) {
                            vill.x = parseInt(coordMatch[1]);
                            vill.y = parseInt(coordMatch[2]);
                        }
                    }
                    
                    // Farm (boş nüfus)
                    var farmCell = row.querySelector('a[href*="screen=farm"]');
                    if (farmCell) {
                        vill.farm_text = farmCell.textContent.trim();
                    }
                    
                    // Askerler
                    vill.troops = {};
                    var cells = row.querySelectorAll('td.unit-item');
                    for (var i = 0; i < cells.length && i < unitNames.length; i++) {
                        vill.troops[unitNames[i]] = parseInt(cells[i].textContent.trim()) || 0;
                    }
                    
                    // Seçili köy mü?
                    vill.selected = row.classList.contains('selected');
                    
                    if (vill.id) {
                        result.all_villages.push(vill);
                    }
                });
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

            // link_base - köy geçişi için URL pattern
            result.link_base = game_data.link_base_pure || '';
            result.world = game_data.world || '';
            result.screen = game_data.screen || '';
            result.csrf = game_data.csrf || '';

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

            // Dünya hız ayarları
            if (typeof TribalWars !== 'undefined' && TribalWars.worldConfig) {
                var wc = TribalWars.worldConfig;
                result.world_speed = parseFloat(wc.speed || 1);
                result.unit_speed = parseFloat(wc.unit_speed || 1);
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

            # Veriyi kaydet
            self._game_data = data

            # Birim ikonlarını başlat / güncelle
            image_base = data.get("image_base", "")
            if image_base:
                troop_icon_mgr.set_image_base(image_base, self._add_log)
                self._add_log("İKON", "info", f"image_base: {image_base}")

            # UI güncelle
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

        self.browser.page().runJavaScript(scrape_js, on_scrape_result)

    def _update_player_info(self, data):
        """Oyuncu bilgisi etiketini güncelle."""
        player = data.get("player", {})
        village = data.get("village", {})
        txt = (
            f"👤 {player.get('name', '?')} | "
            f"🏆 Sıra: {player.get('rank', '?')} | "
            f"⭐ Puan: {player.get('points', '?')} | "
            f"🏘️ Köy: {player.get('villages', '?')} | "
            f"🌍 Dünya: {data.get('world', '?')}"
        )
        self.player_info_label.setText(txt)

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
            "knight": "Şövalye", "snob": "Soylular",
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
            "catapult": "Man", "knight": "Şöv", "snob": "Soy", "militia": "Mil"
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

        # overview_villages sayfasına git (tüm köyleri görmek için)
        switch_js = f"""
        (function() {{
            // game_data.link_base_pure pattern: /game.php?village=581&screen=
            // Köy ID'sini değiştirerek yeni URL oluştur
            var baseUrl = window.location.origin;
            var newUrl = baseUrl + '/game.php?village={village_id}&screen=overview_villages&mode=combined';
            window.location.href = newUrl;
            return 'SWITCHING_TO_' + {village_id};
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
            switch_js = f"""
            (function() {{
                var baseUrl = window.location.origin;
                var newUrl = baseUrl + '/game.php?village={village_id}&screen=overview_villages&mode=combined';
                window.location.href = newUrl;
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
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_indicator.setText("● DURDURULDU")
        self.status_indicator.setStyleSheet("color: #cc4444; font-weight: bold; font-size: 11px;")
        self._add_log("SİSTEM", "warn", "Bot durduruldu.")
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
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self._fetch_server_time)
        self.sync_timer.start(50)  # 50ms — akıcı milisaniye
        self._server_time_text = ""
        self._fetch_server_time()

    def _fetch_server_time(self):
        """Sayfadaki sunucu saatini doğrudan oku.
        
        Tribal Wars kendi JS'i ile #serverTime span'ını her saniye güncelliyor.
        Biz sadece DOM'dan okuyoruz — hesaplama yapmıyoruz.
        Milisaniye için Timing nesnesinden faydalanıyoruz.
        """
        if not self.browser:
            self._show_local_time()
            return

        fetch_js = """
        (function() {
            var timeEl = document.getElementById('serverTime');
            var dateEl = document.getElementById('serverDate');
            
            if (!timeEl) return 'NONE';
            
            var timeStr = timeEl.textContent.trim();
            var dateStr = dateEl ? dateEl.textContent.trim() : '';
            
            // Milisaniye: Timing nesnesinden hesapla
            var ms = '000';
            if (typeof Timing !== 'undefined' && Timing.initial_server_time && Timing.pagehit_at) {
                var serverNowMs = Timing.initial_server_time + (Date.now() - Timing.pagehit_at);
                ms = ('00' + (serverNowMs % 1000)).slice(-3);
            } else if (typeof Timing !== 'undefined' && typeof Timing.offset_from_server !== 'undefined') {
                var serverNowMs2 = Date.now() - Timing.offset_from_server;
                ms = ('00' + (serverNowMs2 % 1000)).slice(-3);
            }
            
            return dateStr + ' ' + timeStr + '.' + ms;
        })();
        """

        def on_result(result):
            if not result or not isinstance(result, str) or result == 'NONE':
                self._show_local_time()
                return

            self._server_time_text = result.strip()
            self._server_time_synced = True
            self.sync_label.setText(f"Sunucu Saati: {self._server_time_text}")
            self.sync_label.setStyleSheet(
                "color: #228822; font-weight: bold; font-size: 10px;")

        self.browser.page().runJavaScript(fetch_js, on_result)

    def _show_local_time(self):
        """Sunucu saati alınamadığında yerel saati göster."""
        now = datetime.datetime.now()
        ms = now.microsecond // 1000
        time_str = now.strftime("%Y.%m.%d %H:%M:%S") + f".{ms:03d}"
        self.sync_label.setText(f"Yerel Saat: {time_str} (senkronize değil)")
        self.sync_label.setStyleSheet("color: #aa6600; font-size: 10px;")

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
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))
    app.setStyleSheet(STYLESHEET)

    window = TribalWarsBot()
    window.show()
    sys.exit(app.exec_())
