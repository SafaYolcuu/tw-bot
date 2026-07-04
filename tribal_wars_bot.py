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
import shutil
import subprocess
import zipfile
import urllib.error
import urllib.request
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, urlencode

from PyQt5.QtCore import Qt, QUrl, QTimer, QTime, QDate, QSize, pyqtSignal, QObject, QSettings, pyqtSlot
from PyQt5.QtGui import QFont, QColor, QBrush, QPainter, QPen, QPixmap, QIcon, QPalette
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QSpinBox, QTreeWidget, QTreeWidgetItem, QTextEdit, QSplitter,
    QListWidget, QListWidgetItem,
    QFrame, QGroupBox, QGridLayout, QHeaderView, QStatusBar, QScrollArea,
    QSizePolicy, QFormLayout, QMessageBox, QTableWidget, QTableWidgetItem,
    QTimeEdit, QDateEdit, QAbstractItemView, QDoubleSpinBox, QSlider,
    QDialog, QDialogButtonBox, QRadioButton, QButtonGroup, QInputDialog,
    QMenuBar, QAction, QProgressDialog,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel

# ─────────────────────────────────────────────
#  SABİTLER
# ─────────────────────────────────────────────

# EXE'nin guncel oldugunu dogrulamak icin her onemli degisiklikte artirin.
APP_VERSION = "1.2.1"

# Otomatik guncelleme — kullaniciya GitHub adresi gosterilmez; yalnizca bu URL okunur.
UPDATE_MANIFEST_URL = "https://safayolcuu.github.io/tw-bot/bot-update.json"
UPDATE_USER_AGENT = f"TribalWarsBot/{APP_VERSION}"

SERVERS = [
    ("klanlar.org", "https://www.klanlar.org"),
    ("tribalwars.works", "https://www.tribalwars.works"),
    ("tribalwars.net", "https://www.tribalwars.net"),
    ("tribalwars.com.tr", "https://www.tribalwars.com.tr"),
    ("tribalwars.co.uk", "https://www.tribalwars.co.uk"),
    ("tribalwars.de", "https://www.die-staemme.de"),
    ("tribalwars.nl", "https://www.tribalwars.nl"),
]

# Tüm dünyalarda ortak yedek birim listesi (oyun game_data.units gelene kadar).
DEFAULT_UNIT_DEFS = [
    ("spear", "Mız"), ("sword", "Kıl"), ("axe", "Bal"), ("archer", "Okç"),
    ("spy", "Cas"), ("light", "HSv"), ("marcher", "AOk"), ("heavy", "ASv"),
    ("ram", "Koç"), ("catapult", "Man"), ("knight", "Şöv"), ("snob", "Mis"),
    ("militia", "Mil"),
]

# Ordu gönderimine yazılamayan birimler (köyde kalır).
NON_SENDABLE_UNIT_KEYS = frozenset({"militia"})


def sa_sendable_unit_defs(unit_defs):
    """Saldırı/destek kuyruğunda gösterilecek birimler (milis hariç)."""
    return [(k, s) for k, s in unit_defs if k not in NON_SENDABLE_UNIT_KEYS]


# Tablo sütunları 2–13: sabit başlık sırası (Mız…Mis).
SA_QUEUE_TABLE_TROOP_KEYS = [
    k for k, _ in DEFAULT_UNIT_DEFS if k not in NON_SENDABLE_UNIT_KEYS
]


UNIT_LABELS_TR = dict(DEFAULT_UNIT_DEFS)

# Birim baz hızları (dk/kare, hız=1 dünya); sunucudan alınamazsa yedek.
DEFAULT_UNIT_SPEEDS = {
    "spear": 18, "sword": 22, "axe": 18, "archer": 18,
    "spy": 9, "light": 10, "marcher": 10, "heavy": 11,
    "ram": 30, "catapult": 30, "knight": 10, "snob": 35,
    "militia": 0.02,
}


@dataclass
class WorldContext:
    """Dünyaya özgü ayarlar — scrape ve /page/settings ile doldurulur."""
    world_id: str = ""
    world_display: str = ""
    world_speed: float = 1.0
    unit_speed: float = 1.0
    speeds_verified: bool = False
    fake_min_pop_percent: float = 10.0
    fake_limit_verified: bool = False
    units: list = field(default_factory=list)
    unit_speeds: dict = field(default_factory=dict)
    image_base: str = ""


# Planlayıcı: Chrome bookmarklet ile aynı kaynak (script src — eval/CSP uyumu).
TW_PLANNER_SCRIPT_URL = "https://safayolcuu.github.io/klanlar/arascript.js"

# QSettings: org/app — build’e gömülü proxy yok, kullanıcı tercihleri diske gider.
QSETTINGS_ORG = "TribalWarsBot"
QSETTINGS_APP = "TWB"

# Telegram: tw_config / QSettings'te chat_id yok veya boşsa kullanılır (yeni build / ilk kurulum).
TW_DEFAULT_TELEGRAM_CHAT_ID = "-1003923196486"

def _tw_app_install_dir() -> Path:
    """Kurulum klasoru — frozen exe veya script dizini."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# Kalici ayar dosyasi: exe'nin yaninda tw_config.json.
# Yeni exe dagitilsa bile bu dosya silinmez; arkadaslar sadece exe'yi gunceller.
def _tw_config_path() -> Path:
    """Exe'nin (veya script'in) bulundugu klasorde tw_config.json dondur."""
    return _tw_app_install_dir() / "tw_config.json"


def _tw_version_tuple(version_str: str):
    """'1.10.0' -> (1, 10, 0); karsilastirma icin."""
    parts = []
    for piece in re.split(r"[.\-]", str(version_str or "").strip()):
        piece = piece.strip()
        if not piece:
            continue
        m = re.match(r"(\d+)", piece)
        if m:
            parts.append(int(m.group(1)))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _tw_http_get_bytes(url: str, *, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UPDATE_USER_AGENT, "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _tw_http_get_json(url: str, *, timeout: float = 20.0) -> dict:
    raw = _tw_http_get_bytes(url, timeout=timeout)
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Gecersiz guncelleme yaniti")
    return data


def _tw_fetch_update_manifest() -> dict:
    """Arka plan thread: surum manifestini cek."""
    try:
        manifest = _tw_http_get_json(UPDATE_MANIFEST_URL)
        return {"ok": True, "manifest": manifest, "error": ""}
    except Exception as ex:
        return {"ok": False, "manifest": None, "error": str(ex)[:500]}


def _tw_download_update_package(download_url: str, dest_zip: Path) -> dict:
    """Arka plan thread: guncelleme zip indir."""
    try:
        data = _tw_http_get_bytes(download_url, timeout=180.0)
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        dest_zip.write_bytes(data)
        if dest_zip.stat().st_size < 1024:
            raise ValueError("Indirilen dosya cok kucuk")
        return {"ok": True, "path": str(dest_zip), "error": ""}
    except Exception as ex:
        return {"ok": False, "path": "", "error": str(ex)[:500]}


def _tw_extract_update_zip(zip_path: Path, extract_dir: Path) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    # Zip kokunde tek klasor varsa (TribalWarsBot-1.2.0/) icerigini yukari tasi
    children = [p for p in extract_dir.iterdir() if p.name not in ("__MACOSX",)]
    if len(children) == 1 and children[0].is_dir():
        inner = children[0]
        staging = extract_dir.parent / "_staging_flat"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.move(str(inner), str(staging))
        shutil.rmtree(extract_dir, ignore_errors=True)
        staging.rename(extract_dir)


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


def _tw_sorted_player_villages(all_villages):
    """Oyuncu köylerini ada göre (büyük/küçük harf duyarsız), eşit ada göre koordinat ile sıralar."""
    if not all_villages:
        return []

    def _key(v):
        return (
            str(v.get("name") or "").casefold(),
            int(v.get("x", -1) or -1),
            int(v.get("y", -1) or -1),
        )

    return sorted(all_villages, key=_key)


# Üst bar / Ordu Gönder / Bina kuyruğu köy combobox — okunabilir yazı
TW_VILLAGE_COMBO_STYLE = (
    "QComboBox { font-size: 12px; min-height: 24px; padding: 2px 8px; }\n"
    "QComboBox QAbstractItemView { font-size: 12px; padding: 2px; min-height: 22px; }"
)


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


def _tw_resolved_telegram_chat_id(cfg: dict, s: QSettings) -> str:
    """tw_config ve QSettings'ten chat_id; boşsa TW_DEFAULT_TELEGRAM_CHAT_ID."""
    raw = (cfg.get("telegram_chat_id") or s.value("notify/telegram_chat_id", TW_DEFAULT_TELEGRAM_CHAT_ID) or "").strip()
    raw = _tw_normalize_telegram_chat_id(raw)
    return raw if raw else TW_DEFAULT_TELEGRAM_CHAT_ID


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


# Bright Data Web Unlocker (deneme / API doğrulama). Oyun proxy'si kullanılmaz.
BRIGHT_REQUEST_URL = "https://api.brightdata.com/request"
BRIGHT_DEFAULT_TEST_URL = "https://geo.brdtest.com/welcome.txt?product=unlocker&method=api"


def bright_web_unlocker_request(
    api_token: str,
    zone: str,
    target_url: str,
    response_format: str = "raw",
    timeout_sec: int = 120,
    insecure_ssl: bool = False,
) -> tuple:
    """Tek `request` çağrısı. Dönüş: (başarılı, gövde veya hata metni).

    `insecure_ssl` True: kurumsal SSL tarama için sertifika doğrulaması kapalı (risk kullanıcıda).
    """
    api_token = (api_token or "").strip()
    zone = (zone or "").strip()
    target_url = (target_url or "").strip()
    fmt = (response_format or "raw").strip().lower() or "raw"
    if not api_token or not zone or not target_url:
        return (False, "API token, zone ve URL boş olamaz.")
    payload = {"zone": zone, "url": target_url, "format": fmt}
    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BRIGHT_REQUEST_URL,
        data=body_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
    )
    if insecure_ssl:
        ctx = ssl._create_unverified_context()
    else:
        try:
            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    try:
        with opener.open(req, timeout=timeout_sec) as resp:
            raw = resp.read()
        text = raw.decode("utf-8", "replace")
        return (True, text)
    except urllib.error.HTTPError as e:
        try:
            hbody = e.read().decode("utf-8", "replace")[:1200]
        except Exception:
            hbody = ""
        return (False, f"HTTP {e.code} {e.reason or ''}\n{hbody}".strip())
    except urllib.error.URLError as e:
        r = e.reason
        r = r if (r and str(r).strip()) else str(e)
        return (False, str(r)[:800])
    except Exception as e:
        return (False, str(e)[:800])


def _tw_aux_msgbox_parent(widget):
    """Ordu araçları diyalogunda QMessageBox parent=diyalog olunca OK ile pencere de kapanabiliyor."""
    bot = getattr(widget, "bot", None)
    return bot if bot is not None else widget


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
            chat = _tw_resolved_telegram_chat_id(cfg, s)
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

    def refresh_label_pixmap(self, label: "QLabel", unit_key: str):
        """Önbellekte ikon varsa tekrar uygula (diyalog stilinden sonra). Yoksa apply_to_label."""
        if unit_key in self._cache:
            label.setPixmap(self._cache[unit_key])
        else:
            self.apply_to_label(label, unit_key)

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


class TwMapCoordBridge(QObject):
    """Harita koordinat seçici → Fake planı hedef alanı (QWebChannel)."""

    def __init__(self, bot):
        super().__init__(bot)
        self._bot = bot

    @pyqtSlot(str)
    def setCoords(self, coords_text: str):
        if hasattr(self._bot, "_tw_set_fake_targets_from_map"):
            self._bot._tw_set_fake_targets_from_map(coords_text)


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

_SA_COMMAND_EDIT_DIALOG_EXTRA_LIGHT = """
QDialog {
    background-color: #d8d8d8;
}
QFrame#saCmdEditPanel {
    background-color: #e8e8e8;
    border: 1px solid #b0b0b0;
    border-radius: 4px;
}
QLabel#saCmdEditTitle {
    font-size: 14px;
    font-weight: bold;
    color: #222222;
}
QLabel:not(#saCmdEditUnitIcon) {
    color: #222222;
}
QLabel#saCmdEditUnitIcon {
    background-color: transparent;
    border: none;
    padding: 2px;
}
QDialog QLabel#saCmdEditUnitIcon {
    background-color: transparent;
    border: none;
    padding: 2px;
    color: rgba(0, 0, 0, 0);
}
QLineEdit {
    background-color: #ffffff;
    color: #222222;
    border: 1px solid #999999;
    border-radius: 2px;
    padding: 3px 4px;
    min-height: 20px;
}
QLineEdit:read-only {
    background-color: #f0f0f0;
    color: #444444;
}
QComboBox, QSpinBox {
    background-color: #ffffff;
    color: #222222;
    border: 1px solid #999999;
    border-radius: 2px;
    padding: 2px 4px;
}
QCheckBox {
    color: #222222;
}
QPushButton {
    padding: 4px 10px;
}
QScrollArea#saCmdEditUnitsScroll {
    border: 1px solid #c0c0c0;
    background-color: #f5f5f5;
    border-radius: 3px;
}
QScrollArea#saCmdEditUnitsScroll QWidget#qt_scrollarea_viewport {
    background-color: #f5f5f5;
    border: none;
}
QScrollArea#saCmdEditUnitsScroll > QWidget > QWidget {
    background-color: #f5f5f5;
}
QWidget#saCmdEditUnitsRoot {
    background-color: #f5f5f5;
}
QFrame#saCmdEditUnitCell {
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 4px;
}
QDialogButtonBox {
    background-color: #d8d8d8;
    border-top: 1px solid #b0b0b0;
    padding-top: 8px;
}
QDialogButtonBox QPushButton {
    padding: 6px 16px;
    min-width: 72px;
}
"""

_SA_COMMAND_EDIT_DIALOG_EXTRA_DARK = """
QDialog {
    background-color: #1e1e1e;
    color: #d0d0d0;
}
QFrame#saCmdEditPanel {
    background-color: #252526;
    border: 1px solid #3f3f46;
    border-radius: 4px;
}
QLabel#saCmdEditTitle {
    font-size: 14px;
    font-weight: bold;
    color: #e8e8e8;
}
QLabel:not(#saCmdEditUnitIcon) {
    color: #d0d0d0;
}
QLabel#saCmdEditUnitIcon {
    background-color: transparent;
    border: none;
    padding: 2px;
}
QDialog QLabel#saCmdEditUnitIcon {
    background-color: transparent;
    border: none;
    padding: 2px;
    color: rgba(0, 0, 0, 0);
}
QLineEdit {
    background-color: #2d2d30;
    color: #ececec;
    border: 1px solid #555555;
    border-radius: 2px;
    padding: 3px 4px;
    min-height: 20px;
    selection-background-color: #264f78;
}
QLineEdit:focus {
    border: 1px solid #0078d4;
}
QLineEdit:read-only {
    background-color: #1e1e1e;
    color: #a0a0a0;
    border: 1px solid #404040;
}
QComboBox, QSpinBox {
    background-color: #2d2d30;
    color: #ececec;
    border: 1px solid #555555;
    border-radius: 2px;
    padding: 2px 4px;
}
QComboBox QAbstractItemView {
    background-color: #2d2d30;
    color: #ececec;
    selection-background-color: #264f78;
}
QCheckBox {
    color: #e0e0e0;
}
QPushButton {
    background-color: #3c3c3c;
    color: #ececec;
    border: 1px solid #555555;
    padding: 4px 10px;
    border-radius: 2px;
}
QPushButton:hover {
    background-color: #4a4a4a;
}
QScrollArea#saCmdEditUnitsScroll {
    border: none;
    background-color: #252526;
}
QScrollArea#saCmdEditUnitsScroll QWidget#qt_scrollarea_viewport {
    background-color: #252526;
    border: none;
}
QWidget#saCmdEditUnitsViewport {
    background-color: #252526;
    border: none;
}
QScrollArea#saCmdEditUnitsScroll > QWidget > QWidget {
    background-color: #252526;
}
QWidget#saCmdEditUnitsRoot {
    background-color: #252526;
}
QFrame#saCmdEditUnitCell {
    background-color: #2d2d30;
    border: 1px solid #404040;
    border-radius: 4px;
}
QDialogButtonBox {
    background-color: #1e1e1e;
    border-top: 1px solid #3f3f46;
    padding-top: 8px;
}
QDialogButtonBox QPushButton {
    background-color: #3c3c3c;
    color: #ececec;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 6px 18px;
    min-width: 76px;
    min-height: 24px;
}
QDialogButtonBox QPushButton:hover {
    background-color: #4a4a4a;
}
QDialogButtonBox QPushButton:default {
    background-color: #264f78;
    border: 1px solid #5b8fd4;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #555555;
    background-color: #3c3c3c;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #c0c0c0;
    margin-right: 6px;
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #3c3c3c;
    border: 1px solid #555555;
    width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: #4a4a4a;
}
QSpinBox::up-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid #c0c0c0;
    width: 0;
    height: 0;
}
QSpinBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #c0c0c0;
    width: 0;
    height: 0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #666666;
    border-radius: 2px;
    background-color: #2d2d30;
}
QCheckBox::indicator:checked {
    background-color: #264f78;
    border: 1px solid #5b8fd4;
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


def _sa_command_edit_dialog_stylesheet(dark: bool) -> str:
    """Yalnızca diyalog stilleri — ana STYLESHEET_DARK birleşimi beyaz/kontrast hatalarına yol açıyordu."""
    return _SA_COMMAND_EDIT_DIALOG_EXTRA_DARK if dark else _SA_COMMAND_EDIT_DIALOG_EXTRA_LIGHT


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
        self.src_combo.setMinimumWidth(280)
        self.src_combo.setStyleSheet(TW_VILLAGE_COMBO_STYLE)
        all_v = self._game_data.get("all_villages", [])
        ordered = _tw_sorted_player_villages(all_v) if all_v else []
        current_id = self._game_data.get("village", {}).get("id", 0)
        if ordered:
            sel = 0
            for i, v in enumerate(ordered):
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
                    for vv in _tw_sorted_player_villages(villages):
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

        UNIT_SPEEDS = DEFAULT_UNIT_SPEEDS
        bot = getattr(self, "_bot", None)
        def _spd(key):
            if bot and hasattr(bot, "_get_unit_travel_speed"):
                return bot._get_unit_travel_speed(key)
            return UNIT_SPEEDS.get(key, 18)

        time_date = self.time_date.text().strip() if self._time_mode else ""
        time_clock = self.time_clock.text().strip() if self._time_mode else ""

        for v in self._queue:
            tgt_x, tgt_y = v["x"], v["y"]
            distance = math.sqrt((tgt_x - src_x) ** 2 + (tgt_y - src_y) ** 2)

            slowest = 0
            for unit_key in troops:
                spd = _spd(unit_key)
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


class SaCommandEditDialog(QDialog):
    """Kuyruk satırı: iki sütunlu ayar paneli; varış/gönderim senkron; ana pencere koyu modu."""

    _CATAPULT_UI_TARGETS = (
        ("(varsayılan)", ""),
        ("Duvar", "wall"),
        ("Merkez binası", "main"),
        ("Depo", "storage"),
        ("Gizli depo", "hide"),
        ("Kışla", "barracks"),
        ("Ahır", "stable"),
        ("Atölye", "garage"),
        ("Demirci", "smith"),
        ("Oduncu", "wood"),
        ("Taşçı", "stone"),
        ("Demir madeni", "iron"),
        ("Çiftlik", "farm"),
        ("Market", "market"),
        ("Toplanma yeri", "place"),
        ("Gözlem kulesi", "watchtower"),
    )

    @staticmethod
    def _sec_to_hms(sec: int) -> str:
        sec = int(max(0, sec))
        h, r = divmod(sec, 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def __init__(self, parent, bot, item: QTreeWidgetItem):
        super().__init__(parent)
        self._bot = bot
        self._item = item
        self.setWindowTitle("Ayarlar — komut")
        self.setMinimumSize(720, 420)
        self.resize(820, 460)

        self._block_refresh = False

        src_t = item.text(0)
        sm = re.search(r"\((\d+)\|(\d+)\)", src_t)
        tm = re.search(r"(\d+)\|(\d+)", item.text(1) or "")
        self._sx = int(sm.group(1))
        self._sy = int(sm.group(2))
        self._tx = int(tm.group(1))
        self._ty = int(tm.group(2))

        sset = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        dark = bool(
            sset.value("ui/dark_mode", False, type=bool)
            or getattr(bot, "_dark_mode", False)
        )
        self.setStyleSheet(_sa_command_edit_dialog_stylesheet(dark))
        if dark:
            self.setAttribute(Qt.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel("Ayarlar")
        title.setObjectName("saCmdEditTitle")
        outer.addWidget(title)

        main_row = QHBoxLayout()
        main_row.setSpacing(12)

        left = QFrame()
        left.setObjectName("saCmdEditPanel")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(8)

        foot = QLabel(f"{item.text(0)}\n→ {item.text(1)}")
        foot.setWordWrap(True)
        foot.setStyleSheet("font-size: 10px; color: #888888;" if dark else "font-size: 10px; color: #555555;")
        lv.addWidget(foot)

        typ_row = QHBoxLayout()
        typ_row.addWidget(QLabel("Tür:"))
        self._cmd_combo = QComboBox()
        self._cmd_combo.addItems(["Saldırı", "Destek"])
        ct = (item.text(14) or "").strip()
        self._cmd_combo.setCurrentIndex(0 if ct == "Sld" else 1)
        self._cmd_combo.setMinimumWidth(120)
        typ_row.addWidget(self._cmd_combo)
        typ_row.addStretch()
        lv.addLayout(typ_row)

        self._chk_arrive_master = QCheckBox("Varış zamanına göre kilitle (gönderim / dönüş otomatik)")
        self._chk_arrive_master.setChecked(True)
        self._chk_arrive_master.setToolTip(
            "İşaretliyken varış tarih+saatini düzenlersiniz; gönderim ve dönüş yolculuk süresine göre hesaplanır.\n"
            "İşaretsizken gönderim zamanını düzenlersiniz; varış ve dönüş buna göre güncellenir."
        )
        lv.addWidget(self._chk_arrive_master)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        def _mk_time_row(label, row):
            la = QLabel(label)
            te = QLineEdit()
            te.setPlaceholderText("SS:DD:SS:ms")
            te.setFixedWidth(118)
            de = QLineEdit()
            de.setPlaceholderText("GG.AA")
            de.setFixedWidth(52)
            de.setAlignment(Qt.AlignCenter)
            grid.addWidget(la, row, 0)
            grid.addWidget(te, row, 1)
            grid.addWidget(de, row, 2)
            return de, te

        self._d_send, self._t_send = _mk_time_row("Gönderim:", 0)
        self._d_arr, self._t_arr = _mk_time_row("Varış:", 1)
        self._d_ret, self._t_ret = _mk_time_row("Dönüş:", 2)

        tl = QLabel("Yolculuk süresi:")
        self._travel_leg = QLineEdit()
        self._travel_leg.setReadOnly(True)
        self._travel_leg.setPlaceholderText("HH:MM:SS")
        self._travel_leg.setFixedWidth(118)
        grid.addWidget(tl, 3, 0)
        grid.addWidget(self._travel_leg, 3, 1)

        lv.addLayout(grid)

        srv_row = QHBoxLayout()
        b_srv = QPushButton("Sunucu saati (referans satırına)")
        b_srv.setCursor(Qt.PointingHandCursor)
        b_srv.clicked.connect(self._fill_anchor_from_server)
        srv_row.addWidget(b_srv)
        srv_row.addStretch()
        lv.addLayout(srv_row)

        main_row.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("saCmdEditPanel")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 10, 10, 10)
        rv.setSpacing(6)

        units_wrap = QWidget()
        units_wrap.setObjectName("saCmdEditUnitsRoot")
        ug = QGridLayout(units_wrap)
        ug.setContentsMargins(0, 0, 0, 0)
        ug.setSpacing(4)
        self._unit_spins = {}
        self._unit_icon_labels = []
        for i, key in enumerate(SA_QUEUE_TABLE_TROOP_KEYS):
            try:
                v0 = int(item.text(2 + i) or 0)
            except ValueError:
                v0 = 0
            fr = QFrame()
            fr.setObjectName("saCmdEditUnitCell")
            vb = QVBoxLayout(fr)
            vb.setContentsMargins(2, 2, 2, 2)
            vb.setSpacing(2)
            ic = QLabel()
            ic.setObjectName("saCmdEditUnitIcon")
            ic.setAlignment(Qt.AlignCenter)
            ic.setScaledContents(True)
            ic.setFixedSize(32, 24)
            troop_icon_mgr.apply_to_label(ic, key)
            self._unit_icon_labels.append((key, ic))
            vb.addWidget(ic)
            sp = QSpinBox()
            sp.setRange(0, 99999)
            sp.setValue(v0)
            sp.setFixedWidth(58)
            self._unit_spins[key] = sp
            vb.addWidget(sp, alignment=Qt.AlignCenter)
            r, c = divmod(i, 4)
            ug.addWidget(fr, r, c)
        units_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        sc = QScrollArea()
        sc.setObjectName("saCmdEditUnitsScroll")
        sc.setWidgetResizable(True)
        sc.setWidget(units_wrap)
        sc.setMinimumHeight(200)
        sc.setFrameShape(QFrame.NoFrame)
        rv.addWidget(sc, 1)

        if dark:
            fill = QColor("#252526")
            unit_bg = "#252526"
            # Windows: viewport genelde diyalog QSS'ini yok sayıp Base ile beyaz boyar;
            # yerel stil + palet ikisini de zorunlu kıl.
            sc.setAttribute(Qt.WA_StyledBackground, True)
            sc.setAutoFillBackground(True)
            scpal = sc.palette()
            scpal.setColor(QPalette.Window, fill)
            scpal.setColor(QPalette.Base, fill)
            sc.setPalette(scpal)
            sc.setStyleSheet(
                f"QScrollArea#saCmdEditUnitsScroll {{ background-color: {unit_bg}; border: none; }}"
            )
            vp = sc.viewport()
            vp.setObjectName("saCmdEditUnitsViewport")
            vp.setAttribute(Qt.WA_StyledBackground, True)
            vp.setAutoFillBackground(True)
            vpal = vp.palette()
            vpal.setColor(QPalette.Window, fill)
            vpal.setColor(QPalette.Base, fill)
            vp.setPalette(vpal)
            vp.setStyleSheet(f"QWidget#saCmdEditUnitsViewport {{ background-color: {unit_bg}; border: none; }}")
            units_wrap.setAttribute(Qt.WA_StyledBackground, True)
            units_wrap.setAutoFillBackground(True)
            upal = units_wrap.palette()
            upal.setColor(QPalette.Window, fill)
            upal.setColor(QPalette.Base, fill)
            units_wrap.setPalette(upal)
            units_wrap.setStyleSheet(
                f"QWidget#saCmdEditUnitsRoot {{ background-color: {unit_bg}; }}"
            )

        rv.addWidget(QLabel("Mancınık hedefi:"))
        self._combo_catapult = QComboBox()
        for lab, val in self._CATAPULT_UI_TARGETS:
            self._combo_catapult.addItem(lab, val)
        saved_cat = item.data(0, bot.SA_QUEUE_ITEM_ROLE_CATAPULT)
        sv = (saved_cat or "").strip() if saved_cat else ""
        for j in range(self._combo_catapult.count()):
            if self._combo_catapult.itemData(j) == sv:
                self._combo_catapult.setCurrentIndex(j)
                break
        self._combo_catapult.setMinimumWidth(180)
        rv.addWidget(self._combo_catapult)

        main_row.addWidget(right, 1)
        outer.addLayout(main_row, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self._chk_arrive_master.stateChanged.connect(self._on_anchor_mode_changed)
        for pair in (
            (self._d_send, self._t_send),
            (self._d_arr, self._t_arr),
            (self._d_ret, self._t_ret),
        ):
            pair[0].editingFinished.connect(self._on_time_edited)
            pair[1].editingFinished.connect(self._on_time_edited)
        for sp in self._unit_spins.values():
            sp.valueChanged.connect(self._on_troops_changed)
        self._cmd_combo.currentIndexChanged.connect(self._on_troops_changed)

        snd0 = bot._dispatch_parse_time_str(item.text(15))
        arr0 = bot._dispatch_parse_time_str(item.text(16))
        if arr0:
            self._chk_arrive_master.setChecked(True)
            self._fill_row_edits(self._d_arr, self._t_arr, arr0)
        elif snd0:
            self._chk_arrive_master.setChecked(False)
            self._fill_row_edits(self._d_send, self._t_send, snd0)
        else:
            self._chk_arrive_master.setChecked(True)
        self._apply_editable_mask()
        self._full_refresh()
        self._sa_refresh_unit_icons()

    def showEvent(self, event):
        super().showEvent(event)
        self._sa_refresh_unit_icons()

    def _sa_refresh_unit_icons(self):
        for key, lbl in getattr(self, "_unit_icon_labels", ()):
            troop_icon_mgr.refresh_label_pixmap(lbl, key)

    def _fill_row_edits(self, d_e: QLineEdit, t_e: QLineEdit, dt):
        d_e.setText(f"{dt.day:02d}.{dt.month:02d}")
        ms = dt.microsecond // 1000
        t_e.setText(f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}:{ms:03d}")

    def _read_dt(self, d_e: QLineEdit, t_e: QLineEdit):
        return self._bot._sa_parse_time_input(d_e.text().strip(), t_e.text().strip())

    def _troops_map(self):
        return {k: self._unit_spins[k].value() for k in SA_QUEUE_TABLE_TROOP_KEYS}

    def _travel_seconds(self):
        tm = self._troops_map()
        keys = [k for k in SA_QUEUE_TABLE_TROOP_KEYS if int(tm.get(k, 0) or 0) > 0]
        if not keys:
            return 0
        dist = math.sqrt(
            (float(self._tx) - float(self._sx)) ** 2 + (float(self._ty) - float(self._sy)) ** 2
        )
        cmd_attack = self._cmd_combo.currentIndex() == 0
        return int(
            self._bot._sa_calc_travel_time(
                dist, keys, troops_map=tm, cmd_attack=cmd_attack
            )
        )

    def _apply_editable_mask(self):
        arr_m = self._chk_arrive_master.isChecked()
        for e in (self._d_send, self._t_send, self._d_ret, self._t_ret):
            e.setReadOnly(arr_m)
        for e in (self._d_arr, self._t_arr):
            e.setReadOnly(not arr_m)
        self._travel_leg.setReadOnly(True)

    def _on_anchor_mode_changed(self, _state=None):
        if self._block_refresh:
            return
        self._apply_editable_mask()
        self._full_refresh()

    def _on_time_edited(self):
        if self._block_refresh:
            return
        self._full_refresh()

    def _on_troops_changed(self, *_a):
        if self._block_refresh:
            return
        self._full_refresh()

    def _full_refresh(self):
        if self._block_refresh:
            return
        ts = self._travel_seconds()
        self._travel_leg.setText(self._sec_to_hms(ts))

        troops_map = self._troops_map()
        total = sum(int(troops_map.get(k, 0) or 0) for k in SA_QUEUE_TABLE_TROOP_KEYS)
        if total <= 0:
            return

        mode = "arrive" if self._chk_arrive_master.isChecked() else "send"
        if mode == "arrive":
            anchor = self._read_dt(self._d_arr, self._t_arr)
        else:
            anchor = self._read_dt(self._d_send, self._t_send)
        if anchor is None:
            return

        cmd_attack = self._cmd_combo.currentIndex() == 0
        send_dt, arrive_dt, ret_dt = self._bot._sa_compute_timeline_from_anchor(
            self._sx, self._sy, self._tx, self._ty, troops_map, mode, anchor, cmd_attack
        )
        if send_dt is None:
            return

        self._block_refresh = True
        try:
            self._fill_row_edits(self._d_send, self._t_send, send_dt)
            self._fill_row_edits(self._d_arr, self._t_arr, arrive_dt)
            self._fill_row_edits(self._d_ret, self._t_ret, ret_dt)
        finally:
            self._block_refresh = False

    def _fill_anchor_from_server(self):
        text = getattr(self._bot, "_server_time_text", "") or ""
        if not text or not getattr(self._bot, "_server_time_synced", False):
            QMessageBox.information(self, "Sunucu saati", "Sunucu saati henüz senkron değil.")
            return
        try:
            parts = text.split(" ", 1)
            if len(parts) != 2:
                return
            date_part = parts[0].strip()
            time_part = parts[1].strip()
            d_txt, t_txt = None, None
            date_match = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", date_part)
            if date_match:
                d_txt = f"{date_match.group(1).zfill(2)}.{date_match.group(2).zfill(2)}"
            time_match = re.match(r"(\d{1,2}):(\d{2}):(\d{2})\.?(\d{0,3})", time_part)
            if time_match:
                h = time_match.group(1).zfill(2)
                m = time_match.group(2)
                s = time_match.group(3)
                ms = (time_match.group(4) or "0")[:3].zfill(3)
                t_txt = f"{h}:{m}:{s}:{ms}"
            if d_txt and t_txt:
                if self._chk_arrive_master.isChecked():
                    self._d_arr.setText(d_txt)
                    self._t_arr.setText(t_txt)
                else:
                    self._d_send.setText(d_txt)
                    self._t_send.setText(t_txt)
                self._full_refresh()
        except Exception:
            pass

    def _on_ok(self):
        troops_map = self._troops_map()
        total = sum(int(troops_map.get(k, 0) or 0) for k in SA_QUEUE_TABLE_TROOP_KEYS)
        if total <= 0:
            QMessageBox.warning(self, "Komut", "En az bir asker girin.")
            return

        ok_stock, stock_msg = self._bot._sa_validate_troops_within_village_stock(
            troops_map, self._sx, self._sy
        )
        if not ok_stock:
            QMessageBox.warning(self, "Komut", stock_msg)
            return

        mode = "arrive" if self._chk_arrive_master.isChecked() else "send"
        if mode == "arrive":
            anchor = self._read_dt(self._d_arr, self._t_arr)
        else:
            anchor = self._read_dt(self._d_send, self._t_send)
        if anchor is None:
            QMessageBox.warning(self, "Komut", "Referans tarih/saat geçersiz (GG.AA ve SS:DD:SS:ms).")
            return

        cmd_attack = self._cmd_combo.currentIndex() == 0
        violate, fake_detail = self._bot._sa_evaluate_fake_violation(
            cmd_attack, troops_map, self._sx, self._sy
        )
        if violate:
            ref_pts = self._bot._sa_resolve_source_village_points(self._sx, self._sy)
            pct = self._bot._sa_fake_min_pop_percent()
            pct_s = self._bot._format_fake_pct(pct)
            min_pop = max(1, int(math.ceil(ref_pts * pct / 100.0)))
            pop = self._bot._sa_troops_total_population(troops_map)
            r = QMessageBox.question(
                self,
                "Fake limiti",
                f"Kaynak köy puanı: {ref_pts}\n"
                f"Gerekli minimum nüfus (≈%{pct_s}): {min_pop}\n"
                f"Komuttaki toplam nüfus: {pop}\n\n"
                "Yine de kaydetmek istiyor musunuz?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return

        send_dt, arrive_dt, return_dt = self._bot._sa_compute_timeline_from_anchor(
            self._sx, self._sy, self._tx, self._ty, troops_map, mode, anchor, cmd_attack
        )
        if send_dt is None:
            QMessageBox.warning(self, "Komut", "Zaman hesaplanamadı.")
            return
        send_str = self._bot._sa_format_time(send_dt)
        arrive_str = self._bot._sa_format_time(arrive_dt)
        return_str = self._bot._sa_format_time(return_dt, ms_zero=True)
        cmd_type = "Sld" if cmd_attack else "Dst"

        tid = (self._item.text(18) or "").strip()
        if not tid.isdigit():
            row_i = self._bot.sa_table.indexOfTopLevelItem(self._item)
            tid = str(max(1, row_i + 1))

        self._bot._sa_reset_queue_item_dispatch_state(self._item)

        troop_values = self._bot._sa_queue_format_troop_values(troops_map)
        for i, tv in enumerate(troop_values):
            self._item.setText(2 + i, tv)
        self._item.setText(14, cmd_type)
        self._item.setText(15, send_str)
        self._item.setText(16, arrive_str)
        self._item.setText(17, return_str)
        self._item.setText(18, tid)
        self._item.setData(0, self._bot.SA_QUEUE_ITEM_ROLE_TIME_MODE, mode)

        cv = self._combo_catapult.currentData()
        if cv:
            self._item.setData(0, self._bot.SA_QUEUE_ITEM_ROLE_CATAPULT, cv)
        else:
            self._item.setData(0, self._bot.SA_QUEUE_ITEM_ROLE_CATAPULT, None)

        self._bot._sa_style_sa_queue_troop_cells(self._item, troop_values)
        for col in (14, 15, 16, 17, 18):
            self._item.setTextAlignment(col, Qt.AlignCenter)

        self._bot._sa_save_army_queue()
        self._bot._sa_update_totals()
        self._bot._add_log(
            "KOMUT",
            "info",
            f"Komut güncellendi: {cmd_type} → Gönderim {send_str} | Varış {arrive_str}",
        )
        self.accept()

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
    """Toplu yapıştır, fake/destek planı ve hedef listesi — sekmeli pencere."""

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
        self.bulk_edit.setPlaceholderText(
            "[table] / forum satırları veya planlayıcı export (#N | [unit]ram … 592|610 -> 612|458)"
        )
        v0.addWidget(self.bulk_edit, 1)
        b_imp = QPushButton("Kuyruğa aktar")
        b_imp.setCursor(Qt.PointingHandCursor)
        b_imp.clicked.connect(self._do_bulk_import)
        v0.addWidget(b_imp)
        self._tw.addTab(w0, "Toplu yapıştır")

        # --- Sekme 1: Fake planı ---
        w_fake = QWidget()
        v_fake = QVBoxLayout(w_fake)
        v_fake.setContentsMargins(8, 8, 8, 8)
        self._hint_fake = QLabel(
            "Her hedefe en fazla N fake. Önce farklı köyler (az kullanılan), aynı hedefe en uzak "
            "yetinen; kuyruk sırası atışa en az süre kalanlara göre (Sophie). Köy başına en fazla N. "
            "Hedef: Tarayıcı → «Koordinatlar» → «Bot'a aktar»."
        )
        self._hint_fake.setWordWrap(True)
        v_fake.addWidget(self._hint_fake)
        arrive_row = QHBoxLayout()
        arrive_row.setSpacing(6)
        arrive_lbl = QLabel("Varış zamanı:")
        arrive_lbl.setStyleSheet("font-weight: bold;")
        arrive_row.addWidget(arrive_lbl)
        self.fake_arrive_date = QLineEdit()
        self.fake_arrive_date.setPlaceholderText("GG.AA")
        self.fake_arrive_date.setFixedWidth(56)
        self.fake_arrive_date.setAlignment(Qt.AlignCenter)
        arrive_row.addWidget(self.fake_arrive_date)
        arrive_row.addWidget(QLabel("'de"))
        self.fake_arrive_clock = QLineEdit()
        self.fake_arrive_clock.setPlaceholderText("SS:DD:SS:ms")
        self.fake_arrive_clock.setFixedWidth(108)
        self.fake_arrive_clock.setAlignment(Qt.AlignCenter)
        arrive_row.addWidget(self.fake_arrive_clock)
        b_fake_from_main = QPushButton("Ana sekmeden al")
        b_fake_from_main.setCursor(Qt.PointingHandCursor)
        b_fake_from_main.setToolTip("Ordu Gönder sekmesindeki tarih/saati buraya kopyalar.")
        b_fake_from_main.clicked.connect(self._fake_copy_arrival_from_main)
        arrive_row.addWidget(b_fake_from_main)
        arrive_row.addStretch()
        v_fake.addLayout(arrive_row)
        v_fake.addWidget(QLabel("Hedef koordinatları:"))
        self.fake_targets = QTextEdit()
        self.fake_targets.setPlaceholderText("505|588  veya  satır satır")
        self.fake_targets.setMaximumHeight(100)
        v_fake.addWidget(self.fake_targets)
        fake_clip_row = QHBoxLayout()
        b_fake_paste = QPushButton("Panodan yapıştır")
        b_fake_paste.setCursor(Qt.PointingHandCursor)
        b_fake_paste.setToolTip(
            "Map Coord Picker veya başka kaynaktan kopyalanan 505|588 … metnini hedef alanına ekler."
        )
        b_fake_paste.clicked.connect(self._paste_fake_targets_from_clipboard)
        fake_clip_row.addWidget(b_fake_paste)
        b_fake_clear = QPushButton("Hedefleri temizle")
        b_fake_clear.setCursor(Qt.PointingHandCursor)
        b_fake_clear.clicked.connect(lambda: self.fake_targets.clear())
        fake_clip_row.addWidget(b_fake_clear)
        fake_clip_row.addStretch()
        v_fake.addLayout(fake_clip_row)
        fake_form = QGridLayout()
        fake_form.setHorizontalSpacing(12)
        fake_form.setVerticalSpacing(6)
        fake_form.addWidget(QLabel("Köy başına max fake"), 0, 0)
        self.sp_fake_per_village = QSpinBox()
        self.sp_fake_per_village.setRange(1, 10)
        self.sp_fake_per_village.setValue(1)
        self.sp_fake_per_village.setFixedWidth(56)
        fake_form.addWidget(self.sp_fake_per_village, 0, 1)
        self.cb_fake_limit = QCheckBox(bot._fake_limit_checkbox_text())
        self.cb_fake_limit.setChecked(True)
        fake_form.addWidget(self.cb_fake_limit, 0, 2, 1, 2)
        fake_form.setColumnStretch(4, 1)
        v_fake.addLayout(fake_form)
        v_fake.addWidget(QLabel("Fake birimleri (en az bir koç veya mancınık seçili olmalı):"))
        unit_wrap = QWidget()
        unit_grid = QGridLayout(unit_wrap)
        unit_grid.setContentsMargins(0, 0, 0, 0)
        unit_grid.setHorizontalSpacing(10)
        unit_grid.setVerticalSpacing(4)
        self.fake_unit_checks = {}
        defs = getattr(bot, "SA_UNIT_DEFS", None) or list(DEFAULT_UNIT_DEFS)
        for i, (ukey, short) in enumerate(defs):
            cb = QCheckBox(short)
            cb.setProperty("unit_key", ukey)
            if ukey in ("ram", "catapult", "spear"):
                cb.setChecked(True)
            self.fake_unit_checks[ukey] = cb
            unit_grid.addWidget(cb, i // 6, i % 6)
        v_fake.addWidget(unit_wrap)
        b_fake = QPushButton("Fake planla ve kuyruğa ekle")
        b_fake.setCursor(Qt.PointingHandCursor)
        b_fake.clicked.connect(self._do_fake_plan)
        v_fake.addWidget(b_fake)
        v_fake.addStretch()
        self._tw.addTab(w_fake, "Fake planı")

        # --- Sekme 2: Şablonlu destek ---
        w_sup = QWidget()
        v_sup = QVBoxLayout(w_sup)
        v_sup.setContentsMargins(8, 8, 8, 8)
        self._hint_support = QLabel(
            "Tek hedef koordinatına, seçili gruptan farklı köylerden şablonla destek planlar. "
            "Her yeni hedef için ayrı planlayın; Ordu Gönder kuyruğunda bekleyen köyler "
            "sonraki planlarda tekrar seçilmez. Stok yetersizse kısmi gönderim (min(stok, şablon))."
        )
        self._hint_support.setWordWrap(True)
        v_sup.addWidget(self._hint_support)
        sup_arrive_row = QHBoxLayout()
        sup_arrive_row.setSpacing(6)
        sup_arrive_lbl = QLabel("Varış zamanı:")
        sup_arrive_lbl.setStyleSheet("font-weight: bold;")
        sup_arrive_row.addWidget(sup_arrive_lbl)
        self.support_arrive_date = QLineEdit()
        self.support_arrive_date.setPlaceholderText("GG.AA")
        self.support_arrive_date.setFixedWidth(56)
        self.support_arrive_date.setAlignment(Qt.AlignCenter)
        sup_arrive_row.addWidget(self.support_arrive_date)
        sup_arrive_row.addWidget(QLabel("'de"))
        self.support_arrive_clock = QLineEdit()
        self.support_arrive_clock.setPlaceholderText("SS:DD:SS:ms")
        self.support_arrive_clock.setFixedWidth(108)
        self.support_arrive_clock.setAlignment(Qt.AlignCenter)
        sup_arrive_row.addWidget(self.support_arrive_clock)
        sup_arrive_row.addStretch()
        v_sup.addLayout(sup_arrive_row)
        sup_tgt_row = QHBoxLayout()
        sup_tgt_row.setSpacing(6)
        sup_tgt_row.addWidget(QLabel("Hedef koordinat:"))
        self.support_target_coord = QLineEdit()
        self.support_target_coord.setPlaceholderText("505|588")
        self.support_target_coord.setFixedWidth(88)
        self.support_target_coord.setAlignment(Qt.AlignCenter)
        self.support_target_coord.textChanged.connect(self._support_save_settings)
        sup_tgt_row.addWidget(self.support_target_coord)
        sup_tgt_row.addWidget(QLabel("Hedef başına köy sayısı:"))
        self.sp_support_villages_count = QSpinBox()
        self.sp_support_villages_count.setRange(1, 99)
        self.sp_support_villages_count.setValue(1)
        self.sp_support_villages_count.setFixedWidth(52)
        self.sp_support_villages_count.valueChanged.connect(
            lambda *_: self._support_save_settings()
        )
        sup_tgt_row.addWidget(self.sp_support_villages_count)
        sup_tgt_row.addStretch()
        v_sup.addLayout(sup_tgt_row)
        sup_form = QGridLayout()
        sup_form.setHorizontalSpacing(12)
        sup_form.setVerticalSpacing(6)
        sup_form.addWidget(QLabel("Köy grubu"), 0, 0)
        self.cb_support_group = QComboBox()
        self.cb_support_group.setMinimumWidth(160)
        sup_form.addWidget(self.cb_support_group, 0, 1)
        sup_form.setColumnStretch(2, 1)
        v_sup.addLayout(sup_form)
        v_sup.addWidget(QLabel("Asker şablonu (köy başına; stok yetersizse kısmi gönderilir):"))
        sup_unit_wrap = QWidget()
        sup_unit_grid = QGridLayout(sup_unit_wrap)
        sup_unit_grid.setContentsMargins(0, 0, 0, 0)
        sup_unit_grid.setHorizontalSpacing(8)
        sup_unit_grid.setVerticalSpacing(4)
        self.support_unit_spins = {}
        sup_defs = getattr(bot, "SA_UNIT_DEFS", None) or list(DEFAULT_UNIT_DEFS)
        for i, (ukey, short) in enumerate(sup_defs):
            sp = QSpinBox()
            sp.setRange(0, 99999)
            sp.setValue(0)
            sp.setFixedWidth(72)
            sp.setSuffix(f" {short}")
            self.support_unit_spins[ukey] = sp
            sup_unit_grid.addWidget(sp, i // 4, i % 4)
        v_sup.addWidget(sup_unit_wrap)
        tpl_row = QHBoxLayout()
        tpl_row.setSpacing(6)
        tpl_row.addWidget(QLabel("Şablon:"))
        self.cb_support_template = QComboBox()
        self.cb_support_template.setMinimumWidth(120)
        tpl_row.addWidget(self.cb_support_template)
        b_tpl_save = QPushButton("Kaydet")
        b_tpl_save.setCursor(Qt.PointingHandCursor)
        b_tpl_save.clicked.connect(self._support_template_save_named)
        tpl_row.addWidget(b_tpl_save)
        b_tpl_load = QPushButton("Yükle")
        b_tpl_load.setCursor(Qt.PointingHandCursor)
        b_tpl_load.clicked.connect(self._support_template_load_named)
        tpl_row.addWidget(b_tpl_load)
        b_tpl_del = QPushButton("Sil")
        b_tpl_del.setCursor(Qt.PointingHandCursor)
        b_tpl_del.clicked.connect(self._support_template_delete_named)
        tpl_row.addWidget(b_tpl_del)
        tpl_row.addStretch()
        v_sup.addLayout(tpl_row)
        b_sup = QPushButton("Destek planla ve kuyruğa ekle")
        b_sup.setCursor(Qt.PointingHandCursor)
        b_sup.clicked.connect(self._do_support_template_plan)
        v_sup.addWidget(b_sup)
        v_sup.addStretch()
        self._tw.addTab(w_sup, "Şablonlu destek")

        # --- Sekme 3: Hedefler + komutlar ---
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
        self.cmd_tree.setHeaderLabels(
            ["Kaynak", "Gönderim", "Varış", "Şöv", "Koç", "Tür", "Durum"]
        )
        self.cmd_tree.setRootIsDecorated(False)
        self.cmd_tree.setAlternatingRowColors(True)
        self.cmd_tree.setToolTip(
            "Bekleyen komut: çift tıkla düzenle. Yeşil satırlar gönderilmiş komutlardır."
        )
        self.cmd_tree.setColumnWidth(0, 130)
        self.cmd_tree.setColumnWidth(1, 140)
        self.cmd_tree.setColumnWidth(2, 140)
        self.cmd_tree.itemDoubleClicked.connect(self._on_cmd_tree_double_clicked)
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

        self._tw.setCurrentIndex(max(0, min(int(initial_page), 3)))

        self._fake_load_settings()
        self._support_load_settings()
        self.bot._refresh_support_plan_groups()
        self._apply_aux_theme()

    def _fake_load_settings(self) -> None:
        """Fake planı: son varış zamanı ve birim seçimini QSettings'ten yükle."""
        if not hasattr(self, "fake_arrive_date"):
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        td = (s.value("fake_plan/arrive_date", "") or "").strip()
        tc = (s.value("fake_plan/arrive_clock", "") or "").strip()
        if td:
            self.fake_arrive_date.setText(td)
        if tc:
            self.fake_arrive_clock.setText(tc)
        units_raw = (s.value("fake_plan/units", "") or "").strip()
        if units_raw:
            selected = {u.strip() for u in units_raw.split(",") if u.strip()}
            for ukey, cb in self.fake_unit_checks.items():
                cb.setChecked(ukey in selected)
        pv = s.value("fake_plan/per_village")
        if pv is not None:
            try:
                self.sp_fake_per_village.setValue(max(1, min(10, int(pv))))
            except (TypeError, ValueError):
                pass
        fl = s.value("fake_plan/limit")
        if fl is not None:
            self.cb_fake_limit.setChecked(bool(fl) if not isinstance(fl, str) else fl.lower() in ("1", "true", "yes"))

    def _fake_save_settings(self) -> None:
        """Fake planı tercihlerini diske yaz."""
        if not hasattr(self, "fake_arrive_date"):
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.setValue("fake_plan/arrive_date", self.fake_arrive_date.text().strip())
        s.setValue("fake_plan/arrive_clock", self.fake_arrive_clock.text().strip())
        s.setValue("fake_plan/units", ",".join(self._fake_selected_unit_keys()))
        s.setValue("fake_plan/per_village", int(self.sp_fake_per_village.value()))
        s.setValue("fake_plan/limit", self.cb_fake_limit.isChecked())
        s.sync()

    def closeEvent(self, event):
        self._fake_save_settings()
        self._support_save_settings()
        super().closeEvent(event)

    def accept(self):
        self._fake_save_settings()
        self._support_save_settings()
        super().accept()

    def _mb_parent(self):
        return self.bot

    def _apply_aux_theme(self) -> None:
        dark = bool(getattr(self.bot, "_dark_mode", False))
        self.setStyleSheet(_army_aux_dialog_stylesheet(dark))
        hint_c = "#a8a8a8" if dark else "#555555"
        self._hint_bulk.setStyleSheet(f"color: {hint_c};")
        if hasattr(self, "_hint_fake"):
            self._hint_fake.setStyleSheet(f"color: {hint_c};")
        if hasattr(self, "_hint_support"):
            self._hint_support.setStyleSheet(f"color: {hint_c};")
        mono = "font-family: Consolas, monospace; font-size: 11px;"
        if dark:
            ed = (
                f"{mono} background-color: #3c3c3c; color: #eeeeee; "
                "border: 1px solid #666666; border-radius: 2px;"
            )
        else:
            ed = (
                f"{mono} background-color: #ffffff; color: #222222; "
                "border: 1px solid #999999; border-radius: 2px;"
            )
        self.bulk_edit.setStyleSheet(ed)
        if hasattr(self, "fake_targets"):
            self.fake_targets.setStyleSheet(ed)
        inp = "padding: 3px 6px; font-size: 11px;"
        if hasattr(self, "fake_arrive_date"):
            self.fake_arrive_date.setStyleSheet(ed + inp)
            self.fake_arrive_clock.setStyleSheet(ed + inp)
        if hasattr(self, "support_target_coord"):
            self.support_target_coord.setStyleSheet(ed + inp)
        if hasattr(self, "support_arrive_date"):
            self.support_arrive_date.setStyleSheet(ed + inp)
            self.support_arrive_clock.setStyleSheet(ed + inp)

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1:
            if hasattr(self, "cb_fake_limit"):
                self.cb_fake_limit.setText(self.bot._fake_limit_checkbox_text())
            self._fake_prefill_arrival_time_if_empty()
        if idx == 2:
            self._support_prefill_arrival_time_if_empty()
            self.bot._refresh_support_plan_groups()
        if idx == 3:
            self._refresh_targets()

    def _fake_prefill_arrival_time_if_empty(self) -> None:
        """Fake sekmesi: boşsa ana sekme veya sunucu saatinden varış öner."""
        if not hasattr(self, "fake_arrive_date"):
            return
        if self.fake_arrive_date.text().strip() and self.fake_arrive_clock.text().strip():
            return
        b = self.bot
        td = getattr(b, "sa_time_date", None)
        tc = getattr(b, "sa_time_clock", None)
        if td and tc and td.text().strip() and tc.text().strip():
            self.fake_arrive_date.setText(td.text().strip())
            self.fake_arrive_clock.setText(tc.text().strip())
            return
        now = datetime.datetime.now()
        year = now.year
        txt = getattr(b, "_server_time_text", "") or ""
        if txt:
            ym = re.search(r"(\d{4})", txt)
            if ym:
                year = int(ym.group(1))
        try:
            dt = datetime.datetime(
                year, now.month, now.day, now.hour, now.minute, now.second
            )
        except ValueError:
            dt = now
        self.fake_arrive_date.setText(f"{dt.day:02d}.{dt.month:02d}")
        ms = dt.microsecond // 1000
        self.fake_arrive_clock.setText(
            f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}:{ms:03d}"
        )

    def _fake_copy_arrival_from_main(self) -> None:
        b = self.bot
        td = b.sa_time_date.text().strip() if hasattr(b, "sa_time_date") else ""
        tc = b.sa_time_clock.text().strip() if hasattr(b, "sa_time_clock") else ""
        if not td or not tc:
            QMessageBox.warning(
                self._mb_parent(),
                "Fake planı",
                "Ana Ordu Gönder sekmesinde tarih ve saat dolu değil.",
            )
            return
        self.fake_arrive_date.setText(td)
        self.fake_arrive_clock.setText(tc)
        self._fake_save_settings()

    def _do_bulk_import(self) -> None:
        self.bot._sa_bulk_import_text(self.bulk_edit.toPlainText(), msg_parent=self.bot)

    def _fake_selected_unit_keys(self):
        return [k for k, cb in self.fake_unit_checks.items() if cb.isChecked()]

    def _paste_fake_targets_from_clipboard(self) -> None:
        """Map Coord Picker vb. panodaki koordinatları fake hedef alanına aktarır."""
        text = QApplication.clipboard().text() or ""
        coords = self.bot._sa_parse_targets_coords(text)
        if not coords:
            QMessageBox.warning(
                self._mb_parent(),
                "Fake planı",
                "Panoda geçerli koordinat bulunamadı.\n"
                "Örnek: 505|588 veya 505|588 500|591 (Map Coord Picker → Kopyala).",
            )
            return
        merged = self.bot._sa_parse_targets_coords(
            self.fake_targets.toPlainText() + " " + text
        )
        line = " ".join(f"{x}|{y}" for x, y in merged)
        self.fake_targets.setPlainText(line)

    def _do_fake_plan(self) -> None:
        b = self.bot
        td = self.fake_arrive_date.text().strip()
        tc = self.fake_arrive_clock.text().strip()
        if not td or not tc:
            QMessageBox.warning(
                self._mb_parent(),
                "Fake planı",
                "Varış tarihi (GG.AA) ve saati (SS:DD:SS:ms) doldurun.",
            )
            return
        ba = b._sa_parse_time_input(td, tc)
        if ba is None:
            QMessageBox.warning(
                self._mb_parent(),
                "Fake planı",
                "Tarih/saat formatı hatalı (GG.AA ve SS:DD:SS:ms).",
            )
            return
        units = self._fake_selected_unit_keys()
        if not any(u in units for u in ("ram", "catapult")):
            QMessageBox.warning(
                self._mb_parent(),
                "Fake planı",
                "En az koç veya mancınık seçili olmalı.",
            )
            return
        b._sa_plan_mass_fakes_with(
            self.fake_targets.toPlainText(),
            int(self.sp_fake_per_village.value()),
            units,
            self.cb_fake_limit.isChecked(),
            ba,
            msg_parent=self.bot,
        )
        self._fake_save_settings()
        self._refresh_targets()

    def _support_load_settings(self) -> None:
        """Şablonlu destek: QSettings'ten son tercihleri yükle."""
        if not hasattr(self, "support_arrive_date"):
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        td = (s.value("support_plan/arrive_date", "") or "").strip()
        tc = (s.value("support_plan/arrive_clock", "") or "").strip()
        if td:
            self.support_arrive_date.setText(td)
        if tc:
            self.support_arrive_clock.setText(tc)
        coord = (s.value("support_plan/target_coord", "") or "").strip()
        if not coord:
            targets_raw = (s.value("support_plan/targets", "") or "").strip()
            parts = targets_raw.split()
            if parts:
                coord = parts[0]
        if coord and hasattr(self, "support_target_coord"):
            m = re.match(r"(\d+)\s*\|\s*(\d+)", coord)
            if m:
                self.support_target_coord.setText(f"{int(m.group(1))}|{int(m.group(2))}")
        vp = s.value("support_plan/villages_per_target")
        if vp is not None and hasattr(self, "sp_support_villages_count"):
            try:
                self.sp_support_villages_count.setValue(max(1, min(99, int(vp))))
            except (TypeError, ValueError):
                pass
        gid = (s.value("support_plan/selected_group_id", "") or "").strip()
        if gid and hasattr(self, "cb_support_group"):
            for i in range(self.cb_support_group.count()):
                if str(self.cb_support_group.itemData(i) or "") == gid:
                    self.cb_support_group.setCurrentIndex(i)
                    break
        self._refresh_support_template_combo()
        names_raw = (s.value("support_plan/templates", "") or "").strip()
        if names_raw:
            first = names_raw.split(",")[0].strip()
            if first:
                idx = self.cb_support_template.findText(first)
                if idx >= 0:
                    self.cb_support_template.setCurrentIndex(idx)
                    self._support_template_load_named(silent=True)

    def _support_save_settings(self) -> None:
        """Şablonlu destek tercihlerini diske yaz."""
        if not hasattr(self, "support_arrive_date"):
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.setValue("support_plan/arrive_date", self.support_arrive_date.text().strip())
        s.setValue("support_plan/arrive_clock", self.support_arrive_clock.text().strip())
        if hasattr(self, "support_target_coord"):
            s.setValue("support_plan/target_coord", self.support_target_coord.text().strip())
        if hasattr(self, "sp_support_villages_count"):
            s.setValue(
                "support_plan/villages_per_target",
                int(self.sp_support_villages_count.value()),
            )
        gid = self.cb_support_group.currentData()
        if gid is not None:
            s.setValue("support_plan/selected_group_id", str(gid))
        s.sync()

    def _support_template_values(self) -> dict:
        return {
            k: int(sp.value())
            for k, sp in self.support_unit_spins.items()
            if int(sp.value()) > 0
        }

    def _support_template_set_values(self, tpl: dict) -> None:
        for k, sp in self.support_unit_spins.items():
            sp.setValue(max(0, int(tpl.get(k, 0) or 0)))

    def _refresh_support_template_combo(self) -> None:
        if not hasattr(self, "cb_support_template"):
            return
        cur = self.cb_support_template.currentText()
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        names_raw = (s.value("support_plan/templates", "") or "").strip()
        names = [n.strip() for n in names_raw.split(",") if n.strip()]
        self.cb_support_template.blockSignals(True)
        self.cb_support_template.clear()
        for n in names:
            self.cb_support_template.addItem(n)
        if cur:
            idx = self.cb_support_template.findText(cur)
            if idx >= 0:
                self.cb_support_template.setCurrentIndex(idx)
        self.cb_support_template.blockSignals(False)

    def _support_template_save_named(self) -> None:
        name, ok = QInputDialog.getText(self._mb_parent(), "Şablon kaydet", "Şablon adı:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            QMessageBox.warning(self._mb_parent(), "Şablonlu destek", "Şablon adı boş olamaz.")
            return
        tpl = self._support_template_values()
        if not tpl:
            QMessageBox.warning(
                self._mb_parent(),
                "Şablonlu destek",
                "En az bir birimde 0'dan büyük değer girin.",
            )
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.setValue(f"support_plan/template/{name}", json.dumps(tpl))
        names_raw = (s.value("support_plan/templates", "") or "").strip()
        names = [n.strip() for n in names_raw.split(",") if n.strip()]
        if name not in names:
            names.append(name)
            s.setValue("support_plan/templates", ",".join(names))
        s.sync()
        self._refresh_support_template_combo()
        idx = self.cb_support_template.findText(name)
        if idx >= 0:
            self.cb_support_template.setCurrentIndex(idx)

    def _support_template_load_named(self, silent: bool = False) -> None:
        name = self.cb_support_template.currentText().strip()
        if not name:
            if not silent:
                QMessageBox.warning(self._mb_parent(), "Şablonlu destek", "Yüklenecek şablon seçin.")
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        raw = s.value(f"support_plan/template/{name}", "")
        if not raw:
            if not silent:
                QMessageBox.warning(
                    self._mb_parent(), "Şablonlu destek", f"«{name}» şablonu bulunamadı."
                )
            return
        try:
            tpl = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            if not silent:
                QMessageBox.warning(self._mb_parent(), "Şablonlu destek", "Şablon verisi okunamadı.")
            return
        self._support_template_set_values(tpl)

    def _support_template_delete_named(self) -> None:
        name = self.cb_support_template.currentText().strip()
        if not name:
            QMessageBox.warning(self._mb_parent(), "Şablonlu destek", "Silinecek şablon seçin.")
            return
        r = QMessageBox.question(
            self._mb_parent(),
            "Şablon sil",
            f"«{name}» şablonu silinsin mi?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.remove(f"support_plan/template/{name}")
        names_raw = (s.value("support_plan/templates", "") or "").strip()
        names = [n.strip() for n in names_raw.split(",") if n.strip() and n.strip() != name]
        s.setValue("support_plan/templates", ",".join(names))
        s.sync()
        self._refresh_support_template_combo()
        for sp in self.support_unit_spins.values():
            sp.setValue(0)

    def _support_prefill_arrival_time_if_empty(self) -> None:
        """Boşsa sunucu saatini öner; ana sekme kullanılmaz."""
        if not hasattr(self, "support_arrive_date"):
            return
        if self.support_arrive_date.text().strip() and self.support_arrive_clock.text().strip():
            return
        b = self.bot
        now = datetime.datetime.now()
        year = now.year
        txt = getattr(b, "_server_time_text", "") or ""
        if txt:
            ym = re.search(r"(\d{4})", txt)
            if ym:
                year = int(ym.group(1))
        try:
            dt = datetime.datetime(
                year, now.month, now.day, now.hour, now.minute, now.second
            )
        except ValueError:
            dt = now
        self.support_arrive_date.setText(dt.strftime("%d.%m"))
        self.support_arrive_clock.setText(dt.strftime("%H:%M:%S:000"))

    def _do_support_template_plan(self) -> None:
        b = self.bot
        td = self.support_arrive_date.text().strip()
        tc = self.support_arrive_clock.text().strip()
        if not td or not tc:
            QMessageBox.warning(
                self._mb_parent(),
                "Şablonlu destek",
                "Varış tarihi (GG.AA) ve saati (SS:DD:SS:ms) doldurun.",
            )
            return
        ba = b._sa_parse_time_input(td, tc)
        if ba is None:
            QMessageBox.warning(
                self._mb_parent(),
                "Şablonlu destek",
                "Tarih/saat formatı hatalı (GG.AA ve SS:DD:SS:ms).",
            )
            return
        if self.cb_support_group.count() <= 0:
            QMessageBox.warning(
                self._mb_parent(),
                "Şablonlu destek",
                "Köy grubu yok. Oyunda Gruplar tanımlayın ve «Veriyi yenile» yapın.",
            )
            return
        group = self.cb_support_group.currentData()
        if group is None:
            QMessageBox.warning(self._mb_parent(), "Şablonlu destek", "Köy grubu seçin.")
            return
        template = self._support_template_values()
        if not template:
            QMessageBox.warning(
                self._mb_parent(),
                "Şablonlu destek",
                "Asker şablonunda en az bir birim 0'dan büyük olmalı.",
            )
            return
        coord_raw = self.support_target_coord.text().strip()
        if re.search(r"[\s,;]", coord_raw) and len(b._sa_parse_targets_coords(coord_raw)) > 1:
            QMessageBox.warning(
                self._mb_parent(),
                "Şablonlu destek",
                "Yalnızca bir hedef koordinatı girin (örn. 505|588).\n"
                "Farklı hedefler için planlamayı ayrı ayrı yapın.",
            )
            return
        b._sa_plan_mass_support_with_template(
            coord_raw,
            int(self.sp_support_villages_count.value()),
            group,
            template,
            ba,
            msg_parent=self.bot,
        )
        self._support_save_settings()
        self._refresh_targets()

    def _aux_cmd_sort_key(self, item):
        arr = self.bot._dispatch_parse_time_str(item.text(16))
        send = self.bot._dispatch_parse_time_str(item.text(15))
        arr_ts = arr.timestamp() if arr else float("inf")
        send_ts = send.timestamp() if send else float("inf")
        return (arr_ts, send_ts)

    def _aux_cmd_status_label(self, src_item, status: str) -> str:
        if status == "pending":
            return ""
        if status == "sent":
            return "Gönderildi"
        detail = (src_item.text(19) or "").strip()
        return detail if detail else "Hata"

    def _aux_style_cmd_tree_row(self, tree_item, status: str) -> None:
        if status == "sent":
            bg, fg = QColor("#d4f0d4"), QColor("#2a7a2a")
        elif status == "error":
            bg, fg = QColor("#f0d4d4"), QColor("#aa3333")
        else:
            return
        for col in range(tree_item.columnCount()):
            tree_item.setBackground(col, bg)
            tree_item.setForeground(col, fg)

    def _aux_collect_target_commands(self, tgt: str):
        rows = []
        for i in range(self.bot.sa_table.topLevelItemCount()):
            it = self.bot.sa_table.topLevelItem(i)
            if it.text(1).strip() != tgt:
                continue
            rows.append((it, "pending"))
        hist = getattr(self.bot, "sa_history_table", None)
        if hist is not None:
            for i in range(hist.topLevelItemCount()):
                it = hist.topLevelItem(i)
                if it.text(1).strip() != tgt:
                    continue
                st = str(it.data(0, Qt.UserRole) or "")
                if st not in ("sent", "error"):
                    st = "sent" if "Gönderildi" in (it.text(19) or "") else "error"
                rows.append((it, st))
        rows.sort(key=lambda pair: self._aux_cmd_sort_key(pair[0]))
        return rows

    def _refresh_targets(self) -> None:
        targets = {}
        sup_txt = (
            getattr(self, "support_target_coord", None) and self.support_target_coord.text()
            or ""
        )

        def note_coord(coord: str, item=None) -> None:
            if not re.match(r"^\d{1,3}\|\d{1,3}$", coord):
                return
            key = self._aux_cmd_sort_key(item) if item is not None else (float("inf"), float("inf"))
            prev = targets.get(coord)
            if prev is None or key < prev:
                targets[coord] = key

        for src in (
            getattr(self, "fake_targets", None) and self.fake_targets.toPlainText() or "",
            sup_txt,
        ):
            for xy in self.bot._sa_parse_targets_coords(src):
                note_coord(f"{xy[0]}|{xy[1]}")
        for i in range(self.bot.sa_table.topLevelItemCount()):
            it = self.bot.sa_table.topLevelItem(i)
            note_coord(it.text(1).strip(), it)
        hist = getattr(self.bot, "sa_history_table", None)
        if hist is not None:
            for i in range(hist.topLevelItemCount()):
                it = hist.topLevelItem(i)
                note_coord(it.text(1).strip(), it)

        cur = self.tgt_list.currentItem()
        cur_txt = cur.text().strip() if cur else ""
        self.tgt_list.clear()
        for coord in sorted(targets.keys(), key=lambda c: (targets[c], c)):
            self.tgt_list.addItem(coord)
        if cur_txt:
            matches = self.tgt_list.findItems(cur_txt, Qt.MatchExactly)
            if matches:
                self.tgt_list.setCurrentItem(matches[0])
        self._on_tgt_changed(self.tgt_list.currentItem(), None)

    def _on_tgt_changed(self, cur, _prev=None) -> None:
        self.cmd_tree.clear()
        if cur is None:
            return
        tgt = cur.text().strip()
        for src_it, status in self._aux_collect_target_commands(tgt):
            ti = QTreeWidgetItem(
                self.cmd_tree,
                [
                    src_it.text(0),
                    src_it.text(15),
                    src_it.text(16),
                    src_it.text(12),
                    src_it.text(10),
                    src_it.text(14),
                    self._aux_cmd_status_label(src_it, status),
                ],
            )
            ti.setData(0, Qt.UserRole, src_it)
            ti.setData(0, Qt.UserRole + 1, status)
            self._aux_style_cmd_tree_row(ti, status)
            for col in range(ti.columnCount()):
                ti.setTextAlignment(col, Qt.AlignCenter)

    def _on_cmd_tree_double_clicked(self, item, _column) -> None:
        if not item:
            return
        status = str(item.data(0, Qt.UserRole + 1) or "pending")
        src = item.data(0, Qt.UserRole)
        if status == "pending":
            if src and self.bot._sa_try_edit_queue_item(src, parent=self):
                self._on_tgt_changed(self.tgt_list.currentItem(), None)
            return
        QMessageBox.information(
            self,
            "Hedefler",
            "Bu komut zaten gönderildi / tamamlandı; Hedefler'de görüntülenir, düzenlenemez.",
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
    _bright_test_finished = pyqtSignal(bool, str)  # ok, body_or_err (log’da token yok)
    _update_check_finished = pyqtSignal(object, bool)  # result dict, manual
    _update_download_finished = pyqtSignal(object)  # result dict

    # Ordu kuyruğu satırı: ek veri (mancınık hedefi — oyun `building` anahtarı, örn. wall)
    SA_QUEUE_ITEM_ROLE_CATAPULT = Qt.UserRole + 30
    SA_QUEUE_ITEM_ROLE_TIME_MODE = Qt.UserRole + 31

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
    # Tamamlanan ordu satırları (gönderildi/hata) — ana kuyruktan ayrı; yeniden gönderilmez.
    SA_ARMY_HISTORY_MAX_ROWS = 400

    # Harita barbar tablosu: yoğun bölgelerde en yakın N satır (eski 500, ~20 karede doluyordu)
    MAP_BARB_LIST_MAX_ROWS = 4000

    # InnoGames "çok sık istek" uyarısı: JS poll aralığı (runJavaScript)
    TW_JS_POLL_MS = 520

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
        self._world_ctx = WorldContext()
        self._world_settings_fetched = False
        self._unit_speeds_fetched = False
        self._world_speed_from_settings = False
        self._trusted_world_speed = None
        self._trusted_unit_speed = None
        self.SA_UNIT_DEFS = list(DEFAULT_UNIT_DEFS)
        self.villages = generate_villages()
        self.selected_villages_list = []
        self.browser = None
        self._pending_command = None
        self._server_time_synced = False
        # InnoGames bot koruması / hCaptcha — tespit edilince ordu gönderimi duraklar (elle tamamlanana kadar).
        self._human_verification_required = False
        self._botprot_hidden_hint = False
        self._botprot_last_parts = []
        self._botprot_last_detection = {}
        self._botprot_fast_poll_until = 0.0  # şüpheli durumda hızlı tarama penceresi (unix time)
        self._last_scraped_village_id = None
        self._village_troops_refresh_timer = None
        self._last_active_troops_fp = None
        self._last_active_troops_vid = None
        self._sa_source_user_picked = False
        self._troops_loading_until = 0.0

        self._build_ui()
        self._start_sync_timer()
        self._start_dispatch_timer()
        # Bot koruması: adaptif DOM taraması (sunucuya istek gitmez — yalnızca runJavaScript).
        self._schedule_next_botprot_poll()
        self._schedule_next_troops_watch_poll()

        self._telegram_test_finished.connect(self._on_telegram_test_finished)
        self._telegram_send_error.connect(self._on_telegram_send_error_slot)
        self._bright_test_finished.connect(self._on_bright_test_finished)
        self._update_check_finished.connect(self._on_update_check_finished)
        self._update_download_finished.connect(self._on_update_download_finished)
        self._update_progress = None
        self._pending_update_manifest = None
        QTimer.singleShot(4500, lambda: self._check_for_updates_async(manual=False))

    @pyqtSlot(str)
    def _on_telegram_send_error_slot(self, m: str):
        self._add_log("SİSTEM", "warn", f"Telegram: {m}")

    @pyqtSlot(bool, str)
    def _on_bright_test_finished(self, ok: bool, body_or_err: str) -> None:
        """Bright Web Unlocker test thread sonucu (ana thread)."""
        text = (body_or_err or "").strip()
        if ok:
            prev = text[:1800] + ("…" if len(text) > 1800 else "")
            self._add_log("AYAR", "success", f"Bright Web Unlocker: test OK (yanıt {len(text)} karakter)")
            _tw_telegram_msgbox_on_top(
                self,
                False,
                "Bright Web Unlocker",
                "İstek başarılı.\n\n"
                "Bu yalnızca API + zone doğrulamasıdır; gömülü tarayıcıdaki hCaptcha otomatik "
                "çözülmez. İleride oturum köprüsü ayrı adımdır.\n\n"
                f"Özet:\n{prev}",
            )
        else:
            self._add_log("AYAR", "warn", f"Bright Web Unlocker: {text[:400]}")
            _tw_telegram_msgbox_on_top(self, True, "Bright Web Unlocker", text[:3500])

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

    # ── OTOMATİK GÜNCELLEME ───────────────────

    def _build_menu_bar(self):
        mb = self.menuBar()
        help_menu = mb.addMenu("Yardım")
        act_check = QAction("Güncellemeleri kontrol et", self)
        act_check.triggered.connect(lambda: self._check_for_updates_async(manual=True))
        help_menu.addAction(act_check)
        act_about = QAction(f"Sürüm {APP_VERSION}", self)
        act_about.triggered.connect(self._show_about_version)
        help_menu.addAction(act_about)

    def _show_about_version(self):
        QMessageBox.information(
            self,
            "Tribal Wars Bot",
            f"Sürüm: {APP_VERSION}\n\n"
            "Güncellemeler: Yardım → Güncellemeleri kontrol et",
        )

    def _check_for_updates_async(self, *, manual: bool = False):
        if getattr(self, "_update_check_running", False):
            if manual:
                QMessageBox.information(self, "Güncelleme", "Kontrol zaten devam ediyor…")
            return
        self._update_check_running = True
        if manual:
            self._add_log("SİSTEM", "info", "Güncelleme kontrol ediliyor…")

        def work():
            result = _tw_fetch_update_manifest()
            self._update_check_finished.emit(result, manual)

        threading.Thread(target=work, daemon=True).start()

    @pyqtSlot(object, bool)
    def _on_update_check_finished(self, result, manual: bool):
        self._update_check_running = False
        if not isinstance(result, dict) or not result.get("ok"):
            err = (result or {}).get("error", "Bilinmeyen hata") if isinstance(result, dict) else "?"
            if manual:
                QMessageBox.warning(
                    self,
                    "Güncelleme",
                    f"Güncelleme sunucusuna ulaşılamadı.\n\n{err[:400]}",
                )
            return

        manifest = result.get("manifest") or {}
        remote = str(manifest.get("version") or "").strip()
        if not remote:
            if manual:
                QMessageBox.warning(self, "Güncelleme", "Sunucu yanıtı geçersiz.")
            return

        if _tw_version_tuple(remote) <= _tw_version_tuple(APP_VERSION):
            if manual:
                QMessageBox.information(
                    self,
                    "Güncelleme",
                    f"En güncel sürümü kullanıyorsunuz.\n\nYüklü: v{APP_VERSION}",
                )
            return

        skipped = (self._settings.value("update/skipped_version", "") or "").strip()
        if not manual and skipped == remote:
            return

        self._show_update_offer_dialog(manifest)

    def _show_update_offer_dialog(self, manifest: dict):
        remote = str(manifest.get("version") or "").strip()
        changelog = (
            manifest.get("changelog_tr") or manifest.get("changelog") or ""
        ).strip()
        body = (
            f"Yeni sürüm hazır: v{remote}\n"
            f"Yüklü sürüm: v{APP_VERSION}\n\n"
            "Güncellemeyi indirip kurmak için «İndir ve kur» seçin.\n"
            "Ayar dosyanız (tw_config.json) korunur."
        )
        if changelog:
            body += f"\n\n{changelog}"

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Güncelleme mevcut")
        msg.setText(body)
        btn_dl = msg.addButton("İndir ve kur", QMessageBox.AcceptRole)
        btn_later = msg.addButton("Daha sonra", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() == btn_dl:
            self._start_update_download(manifest)
        elif msg.clickedButton() == btn_later:
            self._settings.setValue("update/skipped_version", remote)
            self._settings.sync()

    def _start_update_download(self, manifest: dict):
        download_url = str(manifest.get("download_url") or "").strip()
        if not download_url:
            QMessageBox.warning(self, "Güncelleme", "İndirme adresi tanımlı değil.")
            return

        self._pending_update_manifest = manifest
        install = _tw_app_install_dir()
        update_dir = install / "guncelleme"
        zip_path = update_dir / "update.zip"

        if self._update_progress is None:
            self._update_progress = QProgressDialog(
                "Güncelleme indiriliyor…", "İptal", 0, 0, self
            )
            self._update_progress.setWindowTitle("Güncelleme")
            self._update_progress.setWindowModality(Qt.ApplicationModal)
            self._update_progress.setMinimumDuration(0)
            self._update_progress.canceled.connect(self._cancel_update_download)
        self._update_progress.setLabelText("Güncelleme indiriliyor…")
        self._update_progress.show()
        self._update_download_cancelled = False
        self._add_log("SİSTEM", "info", f"Güncelleme v{manifest.get('version', '?')} indiriliyor…")

        def work():
            if getattr(self, "_update_download_cancelled", False):
                self._update_download_finished.emit(
                    {"ok": False, "error": "İptal edildi", "path": ""}
                )
                return
            result = _tw_download_update_package(download_url, zip_path)
            self._update_download_finished.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _cancel_update_download(self):
        self._update_download_cancelled = True

    @pyqtSlot(object)
    def _on_update_download_finished(self, result):
        if self._update_progress is not None:
            self._update_progress.hide()

        if not isinstance(result, dict) or not result.get("ok"):
            err = (result or {}).get("error", "?") if isinstance(result, dict) else "?"
            QMessageBox.warning(
                self,
                "Güncelleme",
                f"İndirme başarısız.\n\n{err[:400]}",
            )
            self._add_log("SİSTEM", "warn", f"Güncelleme indirilemedi: {err[:120]}")
            return

        zip_path = Path(result.get("path") or "")
        if not zip_path.is_file():
            QMessageBox.warning(self, "Güncelleme", "İndirilen dosya bulunamadı.")
            return

        install = _tw_app_install_dir()
        package_dir = install / "guncelleme" / "package"
        try:
            _tw_extract_update_zip(zip_path, package_dir)
        except Exception as ex:
            QMessageBox.warning(
                self,
                "Güncelleme",
                f"Paket açılamadı.\n\n{ex}",
            )
            self._add_log("SİSTEM", "warn", f"Güncelleme zip acilamadi: {ex}")
            return

        manifest = self._pending_update_manifest or {}
        remote = str(manifest.get("version") or "").strip()
        self._settings.setValue("update/skipped_version", "")
        self._settings.sync()
        self._add_log("SİSTEM", "success", f"Güncelleme v{remote} indirildi; kurulum baslatiliyor.")

        reply = QMessageBox.question(
            self,
            "Güncelleme hazır",
            f"v{remote} indirildi.\n\n"
            "Bot kapanacak ve dosyalar güncellenecek.\n"
            "tw_config.json ayar dosyanız korunur.\n\n"
            "Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            try:
                os.startfile(str(install / "guncelleme"))
            except Exception:
                pass
            QMessageBox.information(
                self,
                "Güncelleme",
                "Kurulum klasörü: guncelleme\\\n\n"
                "Botu kapatıp guncelle.bat dosyasına çift tıklayın.",
            )
            return

        self._launch_update_and_quit()

    def _launch_update_and_quit(self):
        install = _tw_app_install_dir()
        bat = install / "guncelle.bat"
        if not bat.is_file():
            bat_candidates = []
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                bat_candidates.append(Path(sys._MEIPASS) / "guncelle.bat")
            bat_candidates.append(Path(__file__).resolve().parent / "guncelle.bat")
            for src in bat_candidates:
                if src.is_file():
                    try:
                        shutil.copy2(src, bat)
                        break
                    except Exception:
                        pass
        if not bat.is_file():
            QMessageBox.warning(
                self,
                "Güncelleme",
                "guncelle.bat bulunamadı.\n\n"
                f"Manuel: {install / 'guncelleme' / 'package'} içeriğini "
                "bot klasörüne kopyalayın (tw_config.json hariç).",
            )
            return
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", str(bat)],
                cwd=str(install),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except Exception as ex:
            QMessageBox.warning(self, "Güncelleme", f"Kurulum başlatılamadı:\n{ex}")
            return
        QApplication.instance().quit()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(2)

        self._build_menu_bar()
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
        if hasattr(self, "settings_bright_enable_cb"):
            self._settings.setValue(
                "bright/enabled", self.settings_bright_enable_cb.isChecked()
            )
            self._settings.setValue("bright/api_token", self.settings_bright_token.text().strip())
            self._settings.setValue("bright/zone", self.settings_bright_zone.text().strip())
            self._settings.setValue("bright/test_url", self.settings_bright_test_url.text().strip())
            fmt = self.settings_bright_format.currentData() or "raw"
            self._settings.setValue("bright/format", fmt)
            self._settings.setValue(
                "bright/insecure_ssl",
                self.settings_bright_insecure_ssl_cb.isChecked()
                if hasattr(self, "settings_bright_insecure_ssl_cb")
                else False,
            )
        self._settings.sync()
        self._add_log("AYAR", "info", "Ayarlar diske kaydedildi.")
        QMessageBox.information(
            self,
            "Ayarlar",
            "Ayarlar kaydedildi.\n\n"
            "Proxy değişikliğinin tarayıcıda tam uygulanması için uygulamayı kapatıp yeniden açın.\n"
            "Proxy şifresi, Telegram bot token’ı ve Bright API token’ı yalnızca bu bilgisayardaki "
            "Qt ayarlarında tutulur (tw_config.json’a yazılmaz).",
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

    def _on_settings_bright_test(self):
        """Bright Web Unlocker duman testi (widget değerleri; Kaydet şart değil)."""
        token = (self.settings_bright_token.text() or "").strip()
        zone = (self.settings_bright_zone.text() or "").strip()
        url = (self.settings_bright_test_url.text() or "").strip() or BRIGHT_DEFAULT_TEST_URL
        fmt = (self.settings_bright_format.currentData() or "raw")
        if not token or not zone:
            _tw_telegram_msgbox_on_top(
                self,
                True,
                "Bright Web Unlocker",
                "API token ve zone adını doldurun (Bright panel — Web Unlocker zone).",
            )
            return
        insecure = (
            self.settings_bright_insecure_ssl_cb.isChecked()
            if hasattr(self, "settings_bright_insecure_ssl_cb")
            else False
        )
        self._add_log("AYAR", "info", "Bright: test isteği gönderiliyor (api.brightdata.com)…")

        def work():
            ok, msg = bright_web_unlocker_request(
                token, zone, url, response_format=fmt, timeout_sec=120, insecure_ssl=insecure
            )
            self._bright_test_finished.emit(ok, msg or "")

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

        if hasattr(self, "sa_source_combo"):
            self._sa_apply_source_combo_list_theme()

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
        if hasattr(self, "sa_history_table"):
            self.sa_history_table.header().setStyleSheet(self.sa_table.header().styleSheet())

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
        self.village_combo.setMinimumWidth(220)
        self.village_combo.setStyleSheet(TW_VILLAGE_COMBO_STYLE)
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

        self.botprot_banner = QLabel("")
        self.botprot_banner.setVisible(False)
        self.botprot_banner.setStyleSheet(
            "color: #cc5500; font-weight: bold; font-size: 10px; padding: 2px 6px;"
            "background: #fff3cd; border: 1px solid #e0c080; border-radius: 3px;"
        )
        layout.addWidget(self.botprot_banner)

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

        self.btn_load_map_picker = QPushButton("Koordinatlar")
        self.btn_load_map_picker.setCursor(Qt.PointingHandCursor)
        self.btn_load_map_picker.setToolTip(
            "Harita (map) ekranında köy seçici paneli açar.\n"
            "«Bot'a aktar» → Ordu Gönder → Araçlar → Fake planı hedefleri.\n"
            "Script: tw-bot/map-coord-picker.js (gömülü, ağ isteği yok)."
        )
        self.btn_load_map_picker.clicked.connect(self._tw_load_map_coord_picker_script)
        tb_layout.addWidget(self.btn_load_map_picker)

        toolbar.setFixedHeight(36)
        toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(toolbar)

        # Gömülü Chromium tarayıcı
        self.browser = StealthBrowser()
        self._tw_planner_bridge = TwPlannerBridge(self)
        self._tw_map_coord_bridge = TwMapCoordBridge(self)
        self._tw_web_channel = QWebChannel(self.browser.stealth_page)
        self._tw_web_channel.registerObject("twPlannerBridge", self._tw_planner_bridge)
        self._tw_web_channel.registerObject("twMapCoordBridge", self._tw_map_coord_bridge)
        self._map_picked_fake_targets = ""
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

    def _tw_resolve_map_coord_picker_path(self):
        """map-coord-picker.js: tw-bot gömülü, frozen build, exe yanı, repo kökü."""
        candidates = [
            Path(__file__).resolve().parent / "map-coord-picker.js",
        ]
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            candidates.insert(0, Path(sys._MEIPASS) / "map-coord-picker.js")
        candidates.extend([
            Path(sys.executable).resolve().parent / "map-coord-picker.js",
            Path(__file__).resolve().parent.parent / "map-coord-picker.js",
        ])
        for p in candidates:
            if p.is_file():
                return p
        return None

    def _tw_map_coord_picker_eval_wrapped(self, js_raw: str) -> str:
        payload = json.dumps(js_raw)
        return (
            "(function(){"
            "if (typeof game_data === 'undefined' || !game_data || !game_data.screen) {"
            "window.alert('Önce Tribal Wars oyun sayfasında olun.');"
            "return;"
            "}"
            "if (game_data.screen !== 'map') {"
            "window.alert('Koordinat seçici yalnızca harita (map) ekranında çalışır. "
            "Haritaya gidin veya URL: ...&screen=map');"
            "return;"
            "}"
            "if (typeof jQuery === 'undefined' || !window.jQuery) {"
            "window.alert('jQuery bulunamadı.');"
            "return;"
            "}"
            f"eval({payload});"
            "})();"
        )

    def _tw_load_map_coord_picker_script(self):
        """Harita (map) ekranında koordinat seçici paneli."""
        if not getattr(self, "browser", None):
            return
        script_path = self._tw_resolve_map_coord_picker_path()
        override_url = os.environ.get("TW_MAP_COORD_PICKER_URL", "").strip()

        if script_path:
            try:
                js_raw = script_path.read_text(encoding="utf-8")
            except OSError as e:
                QMessageBox.warning(self, "Koordinat seçici", f"Dosya okunamadı:\n{e}")
                return
            self.browser.page().runJavaScript(self._tw_map_coord_picker_eval_wrapped(js_raw))
            self._add_log("HARITA", "info", f"Koordinat seçici yerel: {script_path}")
            return

        if override_url:
            url_js = json.dumps(override_url)
            wrapped = (
                "(function(){"
                "if (typeof game_data === 'undefined' || !game_data || !game_data.screen) {"
                "window.alert('Önce Tribal Wars oyun sayfasında olun.');"
                "return;"
                "}"
                "if (game_data.screen !== 'map') {"
                "window.alert('Koordinat seçici yalnızca harita (map) ekranında çalışır.');"
                "return;"
                "}"
                "if (typeof jQuery === 'undefined' || !window.jQuery) {"
                "window.alert('jQuery bulunamadı.');"
                "return;"
                "}"
                "var base = "
                + url_js
                + ";"
                "var u = base + (base.indexOf('?') >= 0 ? '&' : '?') + 'v=' + Date.now();"
                "var s = document.createElement('script');"
                "s.src = u;"
                "s.onerror = function() {"
                "window.alert('Koordinat seçici indirilemedi: ' + u);"
                "};"
                "document.head.appendChild(s);"
                "})();"
            )
            self.browser.page().runJavaScript(wrapped)
            self._add_log("HARITA", "info", f"Koordinat seçici uzak (override): {override_url}")
            return

        QMessageBox.warning(
            self,
            "Koordinat seçici",
            "map-coord-picker.js bot içinde bulunamadı.\n"
            "tw-bot/map-coord-picker.js dosyasının mevcut olduğundan emin olun.",
        )

    def _tw_set_fake_targets_from_map(self, coords_text: str) -> None:
        """Harita seçiciden gelen koordinatları Fake planı hedef alanına yazar."""
        merged = self._sa_parse_targets_coords(coords_text or "")
        if not merged:
            self._add_log("HARITA", "warn", "Bot'a aktar: geçerli koordinat yok")
            QMessageBox.warning(
                self,
                "Koordinat seçici",
                "Geçerli koordinat bulunamadı (örn. 505|588).",
            )
            return
        line = " ".join(f"{x}|{y}" for x, y in merged)
        prev = getattr(self, "_map_picked_fake_targets", "") or ""
        if prev.strip():
            merged = self._sa_parse_targets_coords(prev + " " + line)
            line = " ".join(f"{x}|{y}" for x, y in merged)
        self._map_picked_fake_targets = line

        dlg = getattr(self, "_army_aux_dialog", None)
        if dlg is not None and hasattr(dlg, "fake_targets"):
            dlg.fake_targets.setPlainText(line)

        self._add_log("HARITA", "info", f"Fake hedefleri: {len(merged)} koordinat")
        QMessageBox.information(
            self,
            "Koordinat seçici",
            f"{len(merged)} hedef Fake planına yazıldı.\n"
            "Ordu Gönder → Araçlar → Fake planı sekmesinden planlayabilirsiniz.",
        )

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
        if (ch.objects.twMapCoordBridge) window.twMapCoordBridge = ch.objects.twMapCoordBridge;
        window.__twPlannerBridgeReady = 1;
        window.__twMapCoordBridgeReady = 1;
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

        idx = self.tabs.addTab(tab, "🏘️ Köyler")
        self.tabs.tabBar().setTabVisible(idx, False)
        self._tab_idx_villages = idx

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
        self.sa_source_combo.setMinimumWidth(280)
        self.sa_source_combo.addItem("— Köy Seçin —")
        self.sa_source_combo.currentIndexChanged.connect(self._sa_on_source_user_changed)
        self._sa_apply_source_combo_list_theme()
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

        self.sa_troop_inputs = {}
        self.sa_troop_avail = {}
        self.sa_unit_frames = {}
        self._sa_unit_theme_widgets = []

        for key, short in sa_sendable_unit_defs(DEFAULT_UNIT_DEFS):
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
            self.sa_unit_frames[key] = unit_frame
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
        b_operasyon = QPushButton("Araçlar")
        b_operasyon.setCursor(Qt.PointingHandCursor)
        b_operasyon.setToolTip(
            "Toplu yapıştır, fake/destek planı ve hedef listesi — ayrı pencerede sekmeler."
        )
        b_operasyon.clicked.connect(lambda: self._open_army_aux_dialog(0))
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

        self.sa_table.itemDoubleClicked.connect(self._sa_on_army_queue_item_double_clicked)

        hist_headers = headers + ["Sonuç"]
        self.sa_history_table = QTreeWidget()
        self.sa_history_table.setAlternatingRowColors(True)
        self.sa_history_table.setRootIsDecorated(False)
        self.sa_history_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.sa_history_table.setHeaderLabels(hist_headers)
        self.sa_history_table.setColumnCount(len(hist_headers))
        hist_widths = col_widths + [220]
        for i, w in enumerate(hist_widths):
            self.sa_history_table.setColumnWidth(i, w)
        self.sa_history_table.header().setDefaultAlignment(Qt.AlignCenter)
        self.sa_history_table.header().setStyleSheet(self.sa_table.header().styleSheet())
        self.sa_history_table.setStyleSheet("QTreeWidget { font-size: 11px; }")
        self.sa_history_table.itemDoubleClicked.connect(self._sa_on_army_history_item_double_clicked)

        sa_split = QSplitter(Qt.Vertical)
        sa_split.setChildrenCollapsible(False)
        w_top = QWidget()
        v_top = QVBoxLayout(w_top)
        v_top.setContentsMargins(0, 0, 0, 0)
        v_top.setSpacing(2)
        lbl_pending = QLabel("Bekleyen komutlar (gönderim zamanlayıcısı yalnızca bu listeyi kullanır)")
        lbl_pending.setStyleSheet("font-size: 10px; color: #555;")
        v_top.addWidget(lbl_pending)
        v_top.addWidget(self.sa_table, 1)
        sa_split.addWidget(w_top)
        w_bot = QWidget()
        v_bot = QVBoxLayout(w_bot)
        v_bot.setContentsMargins(0, 0, 0, 0)
        v_bot.setSpacing(2)
        lbl_hist = QLabel(
            "Tamamlanan (başarılı veya hata — yeniden gönderilmez; uygulama kapanınca ayrı kaydedilir)"
        )
        lbl_hist.setStyleSheet("font-size: 10px; color: #555;")
        lbl_hist.setWordWrap(True)
        v_bot.addWidget(lbl_hist)
        v_bot.addWidget(self.sa_history_table, 1)
        sa_split.addWidget(w_bot)
        sa_split.setStretchFactor(0, 3)
        sa_split.setStretchFactor(1, 1)
        layout.addWidget(sa_split, 1)

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

        btn_hist_clear = QPushButton("Geçmişi temizle")
        btn_hist_clear.setCursor(Qt.PointingHandCursor)
        btn_hist_clear.setToolTip("Tamamlanan komutlar tablosunu boşaltır (geri alınamaz).")
        btn_hist_clear.clicked.connect(self._sa_clear_army_history)
        bottom.addWidget(btn_hist_clear)

        layout.addLayout(bottom)

        # Gönderim anahtarı yalnızca zamanlayıcı POST'larını açar; form her zaman düzenlenebilir
        # (kuyruk planlama gönderimden bağımsız).
        self._sa_controls = []

        self.enable_sending_cb.toggled.connect(self._toggle_sending_army)
        self._toggle_sending_army(False)

        self._sa_restore_army_history()
        self._sa_restore_army_queue()
        self.sa_tab = tab
        self.tabs.addTab(tab, "⚔️ Ordu Gönder")

    def _open_army_aux_dialog(self, page: int = 0):
        """Toplu yapıştır / fake-destek planı / hedef-komut listesi — ayrı pencere."""
        dlg = ArmyAuxToolsDialog(self, page)
        self._army_aux_dialog = dlg
        dlg.cb_fake_limit.setText(self._fake_limit_checkbox_text())
        pending = getattr(self, "_map_picked_fake_targets", "") or ""
        if pending.strip() and hasattr(dlg, "fake_targets"):
            dlg.fake_targets.setPlainText(pending.strip())
        if int(page) == 1:
            dlg._fake_prefill_arrival_time_if_empty()
        elif int(page) == 2:
            dlg._support_prefill_arrival_time_if_empty()
        try:
            dlg.exec_()
        finally:
            self._army_aux_dialog = None

    # ── ORDU GÖNDER YARDIMCI FONKSİYONLAR ─────

    def _toggle_sending_army(self, enabled):
        for widget in self._sa_controls:
            widget.setEnabled(enabled)
        if enabled:
            self.enable_sending_cb.setStyleSheet(
                "font-weight: bold; font-size: 11px; color: #228822;")
            self._add_log("KOMUT", "success", "Ordu gönderimi aktif edildi.")
            # Gönderim öncesi tek taze DOM örnekleme; sonra anchor perf ile ilerler (gezinmede bozulmaz).
            try:
                self._fetch_server_time(force=True)
            except Exception:
                pass
        else:
            self.enable_sending_cb.setStyleSheet(
                "font-weight: bold; font-size: 11px; color: #cc4444;")
            if hasattr(self, 'log_text'):
                self._add_log("KOMUT", "warn", "Ordu gönderimi devre dışı.")

    def _sa_apply_source_combo_list_theme(self):
        """Kaynak köy açılır listesi: zebra (zıt) satır renkleri + okunaklı yazı."""
        if not hasattr(self, "sa_source_combo"):
            return
        combo = self.sa_source_combo
        view = combo.view()
        view.setAlternatingRowColors(True)
        view.setMinimumWidth(max(combo.minimumWidth(), 280))
        if self._dark_mode:
            combo.setStyleSheet(
                "QComboBox {\n"
                "  font-size: 12px; min-height: 24px; padding: 2px 8px;\n"
                "  background: #3c3c3c; color: #eeeeee; border: 1px solid #666666;\n"
                "}\n"
                "QComboBox QAbstractItemView {\n"
                "  font-size: 12px; padding: 4px 8px; min-height: 24px;\n"
                "  background: #2a2a2d; alternate-background-color: #3a3f48;\n"
                "  color: #f0f0f0; outline: 0; border: 1px solid #555555;\n"
                "}\n"
                "QComboBox QAbstractItemView::item:selected {\n"
                "  background: #2a5a9e; color: #ffffff;\n"
                "}"
            )
        else:
            combo.setStyleSheet(
                "QComboBox {\n"
                "  font-size: 12px; min-height: 24px; padding: 2px 8px;\n"
                "}\n"
                "QComboBox QAbstractItemView {\n"
                "  font-size: 12px; padding: 4px 8px; min-height: 24px;\n"
                "  background: #ffffff; alternate-background-color: #dde8f4;\n"
                "  color: #1a1a1a; outline: 0;\n"
                "}\n"
                "QComboBox QAbstractItemView::item:selected {\n"
                "  background: #a8c8f0; color: #000000;\n"
                "}"
            )

    def _sa_troops_for_selected_source(self):
        """Kaynak combobox'taki köyün troops sözlüğü; köy yoksa veya veri yoksa {}."""
        if not hasattr(self, "sa_source_combo"):
            return {}
        village_id = self.sa_source_combo.currentData()
        if not village_id:
            return {}
        all_v = self._game_data.get("all_villages", [])
        for v in all_v:
            if self._sa_same_village_id(v.get("id"), village_id):
                t = v.get("troops")
                return dict(t) if isinstance(t, dict) else {}
        v = self._game_data.get("village", {})
        if v and self._sa_same_village_id(v.get("id"), village_id):
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

    @staticmethod
    def _sa_same_village_id(a, b):
        """combo.currentData() ile JSON kayıtları arasında int/str farkını tolere eder."""
        if a is None or b is None:
            return False
        try:
            return int(a) == int(b)
        except (TypeError, ValueError):
            return str(a) == str(b)

    @staticmethod
    def _sa_points_from_village_dict(v):
        """Köy dict'inden puan; bilinmiyorsa None (0 geçerli bir değer)."""
        if not v or not isinstance(v, dict):
            return None
        raw = v.get("points")
        if raw is None or raw == "":
            return None
        try:
            s = str(raw).replace(",", "").replace("\u00a0", "").strip()
            if re.match(r"^\d{1,3}(\.\d{3})+$", s):
                s = s.replace(".", "")
            return int(float(s))
        except (TypeError, ValueError):
            return None

    def _sa_resolve_village_points(self, v):
        """Köy puanı: tablo dict → game_data.villages yedek."""
        p = self._sa_points_from_village_dict(v)
        if p is not None:
            return p
        if not v or not isinstance(v, dict):
            return None
        vid = v.get("id")
        if vid is None:
            return None
        try:
            vid_i = int(vid)
        except (TypeError, ValueError):
            return None
        gv = self._game_data.get("villages")
        candidates = []
        if isinstance(gv, dict):
            candidates.append(gv.get(vid_i))
            candidates.append(gv.get(str(vid_i)))
        elif isinstance(gv, list):
            for vv in gv:
                if isinstance(vv, dict) and self._sa_same_village_id(vv.get("id"), vid_i):
                    candidates.append(vv)
                    break
        for vv in candidates:
            if not vv:
                continue
            p2 = self._sa_points_from_village_dict(vv)
            if p2 is not None:
                return p2
        return None

    def _sa_village_has_siege_stock(self, troops, selected_unit_keys):
        sel = set(selected_unit_keys or [])
        t = troops or {}
        if "ram" in sel and self._sa_troop_count(t, "ram") > 0:
            return True
        if "catapult" in sel and self._sa_troop_count(t, "catapult") > 0:
            return True
        return False

    def _sa_on_source_user_changed(self, index):
        """Kullanıcı kaynak köyü elle seçtiyse üst köy değişiminde otomatik eşlemeyi kapat."""
        if hasattr(self, "sa_source_combo") and not self.sa_source_combo.signalsBlocked():
            self._sa_source_user_picked = True
        self._sa_on_source_changed(index)

    def _sa_on_source_changed(self, index):
        """Kaynak köy seçildiğinde asker mevcutlarını ve köy puanını güncelle."""
        if index < 0:
            return
        village_id = self.sa_source_combo.currentData()
        if not village_id:
            self._sa_source_points_cache = 0
            self._sa_source_points_cache_xy = None
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
        found_pts = None
        for v in all_v:
            if self._sa_same_village_id(v.get("id"), village_id):
                found_pts = self._sa_points_from_village_dict(v)
                break

        found_troops = self._sa_resolve_village_troops(village_id)

        gv = (self._game_data or {}).get("village") or {}
        if self._sa_same_village_id(gv.get("id"), village_id):
            gfp = self._sa_points_from_village_dict(gv)
            if gfp is not None:
                found_pts = gfp if found_pts is None else max(found_pts, gfp)

        self._sa_source_points_cache = int(found_pts) if found_pts is not None else 0
        vx, vy = self._sa_village_xy(
            next(
                (
                    v
                    for v in self._game_data.get("all_villages", [])
                    if self._sa_same_village_id(v.get("id"), village_id)
                ),
                {},
            )
        )
        self._sa_source_points_cache_xy = (
            (int(vx), int(vy)) if vx is not None and vy is not None else None
        )
        if hasattr(self, "sa_source_points_label"):
            if found_pts is not None:
                self.sa_source_points_label.setText(f"Puan: {found_pts:,}")
            else:
                self.sa_source_points_label.setText("Puan: —")

        muted = "#a8a8a8" if self._dark_mode else "#888"
        pos_green = "#6bdc6b" if self._dark_mode else "#1a6b1a"
        loading = time.time() < getattr(self, "_troops_loading_until", 0.0)
        for key, lbl in self.sa_troop_avail.items():
            count = self._sa_troop_count(found_troops, key)
            if loading and not count:
                lbl.setText("(…)")
                lbl.setStyleSheet(
                    f"font-size: 13px; color: {muted}; border: none;"
                )
                lbl.setCursor(Qt.ArrowCursor)
                continue
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
        for key, _ in self._sa_sendable_unit_defs():
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
        raw_src_x, raw_src_y = self._sa_get_source_coords()
        src_x, src_y = self._sa_resolve_travel_source_coords(src_text, raw_src_x, raw_src_y)
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

        troops_map = {
            k: self.sa_troop_inputs[k].value() for k, _ in self._sa_sendable_unit_defs()
        }
        cmd_attack = self.cmd_type_combo.currentIndex() == 0

        ok_stock, stock_msg = self._sa_validate_troops_within_village_stock(
            troops_map, src_x, src_y
        )
        if not ok_stock:
            QMessageBox.warning(self, "Uyarı", stock_msg)
            return
        ok, err = self._sa_append_row_from_values(
            src_text, src_x, src_y, tgt_x, tgt_y, troops_map, cmd_attack,
            self._sa_time_mode, input_dt,
        )
        if not ok:
            QMessageBox.warning(self, "Uyarı", err or "Komut eklenemedi")

    def _sa_open_misyoner_multi_dialog(self):
        MisyonerMultiWaveDialog(self, self).exec_()

    # Koçbaşı komutu: otomatik doldurulacak birim anahtarları (baltacı, hafif, koç, mancınık, atlı okçu, casus, şövalye)
    SA_RAM_AUTO_KEYS = ("axe", "light", "ram", "catapult", "marcher", "spy", "knight")
    # Toplu yapıştır: [unit]axe[/unit] + saldırı — köydeki balta, hafif, atlı okçu, casus, şövalye
    SA_BULK_AXE_ATTACK_KEYS = ("axe", "light", "marcher", "spy", "knight")
    # Toplu yapıştır: [unit]sword[/unit] + destek — köydeki mızrak, kılıç, casus, ağır, şövalye
    SA_BULK_SWORD_SUPPORT_KEYS = ("spear", "sword", "spy", "heavy", "knight")

    def _sa_get_travel_speed_factors(self):
        """Yolculuk formülü için world_speed ve unit_speed (oyunla aynı bölenler)."""
        self._apply_trusted_speeds_to_game_data()
        try:
            ws = float(self._game_data.get("world_speed", 1) or 1)
            us = float(self._game_data.get("unit_speed", 1) or 1)
        except (TypeError, ValueError):
            ws, us = 1.0, 1.0
        if ws <= 0:
            ws = 1.0
        if us <= 0:
            us = 1.0
        return ws, us

    def _sa_queue_format_troop_values(self, troops_map):
        """Tablo sütunları 2–13 için sabit 12 birim (Mız…Mis) metin listesi."""
        troops_map = troops_map or {}
        return [
            str(int(troops_map.get(k, 0) or 0)) for k in SA_QUEUE_TABLE_TROOP_KEYS
        ]

    def _sa_queue_troop_map_from_item(self, item):
        """Kuyruk satırından birim sözlüğü — tablo sütun sırası sabittir."""
        troops = {}
        for i, key in enumerate(SA_QUEUE_TABLE_TROOP_KEYS):
            try:
                val = int(item.text(2 + i) or 0)
            except ValueError:
                val = 0
            if val > 0:
                troops[key] = val
        return troops

    def _sa_queue_row_cmd_valid(self, item):
        return (item.text(14) or "").strip() in ("Sld", "Dst")

    def _sa_try_realign_queue_row(self, item):
        """Eski kayıtlarda birim sütun sayısı 12'den azsa zaman sütunlarını kaydırır."""
        if self._sa_queue_row_cmd_valid(item):
            return False
        n = len(SA_QUEUE_TABLE_TROOP_KEYS)
        cmd_col = 2 + n
        if cmd_col >= item.columnCount():
            return False
        cmd = (item.text(cmd_col) or "").strip()
        if cmd not in ("Sld", "Dst"):
            return False
        send_s = item.text(cmd_col + 1)
        arr_s = item.text(cmd_col + 2)
        ret_s = item.text(cmd_col + 3)
        tid = item.text(cmd_col + 4) if cmd_col + 4 < item.columnCount() else ""
        if not tid:
            tid = item.text(18) or "1"
        raw = [item.text(c) for c in range(2, cmd_col)]
        troop_values = (raw + ["0"] * n)[:n]
        for i, tv in enumerate(troop_values):
            item.setText(2 + i, tv)
        item.setText(14, cmd)
        item.setText(15, send_s)
        item.setText(16, arr_s)
        item.setText(17, ret_s)
        item.setText(18, tid)
        return True

    def _sa_infer_queue_time_mode(self, item):
        stored = item.data(0, self.SA_QUEUE_ITEM_ROLE_TIME_MODE)
        if stored in ("send", "arrive"):
            return stored
        send_dt = self._dispatch_parse_time_str(item.text(15))
        arrive_dt = self._dispatch_parse_time_str(item.text(16))
        if send_dt and arrive_dt and arrive_dt >= send_dt:
            return "send"
        if arrive_dt:
            return "arrive"
        return "send"

    def _sa_recompute_queue_row_timelines(self, item):
        """Bekleyen satırın gönderim/varış/dönüş sütunlarını güncel yolculuk süresiyle yeniden yazar."""
        if not item:
            return False
        state = item.data(0, Qt.UserRole)
        if state in ("sent", "error", "sending"):
            return False
        self._sa_try_realign_queue_row(item)
        if not self._sa_queue_row_cmd_valid(item):
            return False

        src_text = item.text(0)
        tgt_m = re.search(r"(\d+)\|(\d+)", item.text(1) or "")
        if not tgt_m:
            return False
        tgt_x, tgt_y = int(tgt_m.group(1)), int(tgt_m.group(2))

        travel_src_x, travel_src_y = self._sa_resolve_travel_source_coords(
            src_text, None, None
        )
        if travel_src_x is None:
            return False

        troops_map = self._sa_queue_troop_map_from_item(item)
        if not troops_map:
            return False

        cmd_attack = (item.text(14) or "").strip() == "Sld"
        time_mode = self._sa_infer_queue_time_mode(item)
        if time_mode == "arrive":
            anchor = self._dispatch_parse_time_str(item.text(16))
        else:
            anchor = self._dispatch_parse_time_str(item.text(15))
        if anchor is None:
            return False

        send_dt, arrive_dt, return_dt = self._sa_compute_timeline_from_anchor(
            travel_src_x,
            travel_src_y,
            tgt_x,
            tgt_y,
            troops_map,
            time_mode,
            anchor,
            cmd_attack,
        )
        if send_dt is None:
            return False

        send_str = self._sa_format_time(send_dt)
        arrive_str = self._sa_format_time(arrive_dt)
        return_str = self._sa_format_time(return_dt, ms_zero=True)
        old = (item.text(15), item.text(16), item.text(17))
        item.setText(15, send_str)
        item.setText(16, arrive_str)
        item.setText(17, return_str)
        item.setData(0, self.SA_QUEUE_ITEM_ROLE_TIME_MODE, time_mode)

        if old != (send_str, arrive_str, return_str):
            return True
        return False

    def _sa_refresh_all_queue_timelines(self):
        """Birim hızları veya kuyruk yükleme sonrası bekleyen satırların zaman sütunlarını güncelle."""
        if not hasattr(self, "sa_table"):
            return 0
        updated = 0
        for i in range(self.sa_table.topLevelItemCount()):
            item = self.sa_table.topLevelItem(i)
            if item and self._sa_recompute_queue_row_timelines(item):
                updated += 1
        if updated:
            self._sa_save_army_queue()
        return updated

    def _sa_compute_timeline_from_anchor(
        self, src_x, src_y, tgt_x, tgt_y, troops_map, time_mode, input_dt, cmd_attack=True,
        *, arrive_dt_fixed=None,
    ):
        """
        `time_mode`: 'send' | 'arrive'. `input_dt` o moddaki referans zaman.
        Dönüş: (send_dt, arrive_dt, return_dt) veya asker/hedef/mod hatasında (None, None, None).
        """
        troops_map = dict(troops_map)
        total = sum(int(troops_map.get(k, 0) or 0) for k, _ in self.SA_UNIT_DEFS)
        if total <= 0:
            return None, None, None
        if int(tgt_x) == 0 and int(tgt_y) == 0:
            return None, None, None
        troop_keys_sent = [k for k, _ in self.SA_UNIT_DEFS if int(troops_map.get(k, 0) or 0) > 0]
        distance = math.sqrt(
            (float(tgt_x) - float(src_x)) ** 2 + (float(tgt_y) - float(src_y)) ** 2
        )
        travel_sec = self._sa_calc_travel_time(
            distance, troop_keys_sent, troops_map=troops_map, cmd_attack=cmd_attack
        )
        travel_delta = datetime.timedelta(seconds=travel_sec)

        if time_mode == "send" and arrive_dt_fixed is not None:
            send_dt = input_dt
            arrive_dt = arrive_dt_fixed
        elif time_mode == "send":
            send_dt = input_dt
            arrive_dt = send_dt + travel_delta
        elif time_mode == "arrive":
            arrive_dt = input_dt
            send_dt = arrive_dt - travel_delta
        else:
            return None, None, None

        return_dt = arrive_dt + travel_delta
        return send_dt, arrive_dt, return_dt

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
        check_fake_limit=True,
        arrive_dt_fixed=None,
    ):
        """Gönderim kuyruğuna tek satır ekler. (True, None) veya (False, hata_metni)."""
        troops_map = dict(troops_map)

        total = sum(int(troops_map.get(k, 0) or 0) for k, _ in self.SA_UNIT_DEFS)
        if total <= 0:
            return False, "En az bir asker olmalı"
        if int(tgt_x) == 0 and int(tgt_y) == 0:
            return False, "Hedef 0|0 geçersiz — hedef X ve Y koordinatlarını girin"

        travel_src_x, travel_src_y = self._sa_resolve_travel_source_coords(
            src_text, src_x, src_y
        )
        if travel_src_x is None:
            return False, "Kaynak koordinatları bulunamadı"

        violate, fake_detail = (False, None)
        if check_fake_limit:
            violate, fake_detail = self._sa_evaluate_fake_violation(
                cmd_attack, troops_map, travel_src_x, travel_src_y
            )
        if violate:
            if not fake_dialog:
                return False, fake_detail or "Fake limiti altında"
            ref_pts = self._sa_resolve_source_village_points(travel_src_x, travel_src_y)
            pct = self._sa_fake_min_pop_percent()
            pct_s = self._format_fake_pct(pct)
            min_pop = max(1, int(math.ceil(ref_pts * pct / 100.0)))
            pop = self._sa_troops_total_population(troops_map)
            r = QMessageBox.question(
                self,
                "Fake limiti",
                f"Kaynak köy puanı (komutun çıktığı köy): {ref_pts}\n"
                f"Gerekli minimum nüfus (≈%{pct_s}): {min_pop}\n"
                f"Komuttaki toplam nüfus: {pop}\n\n"
                "Yine de kuyruğa eklemek istiyor musunuz?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return False, "Fake limiti — iptal edildi"

        tgt = f"{int(tgt_x)}|{int(tgt_y)}"
        send_dt, arrive_dt, return_dt = self._sa_compute_timeline_from_anchor(
            travel_src_x,
            travel_src_y,
            tgt_x,
            tgt_y,
            troops_map,
            time_mode,
            input_dt,
            cmd_attack,
            arrive_dt_fixed=arrive_dt_fixed,
        )
        if send_dt is None:
            return False, "Zaman modu geçersiz veya asker/hedef uyumsuz"

        troop_keys_sent = [k for k, _ in self.SA_UNIT_DEFS if int(troops_map.get(k, 0) or 0) > 0]
        distance = math.sqrt(
            (float(tgt_x) - float(travel_src_x)) ** 2
            + (float(tgt_y) - float(travel_src_y)) ** 2
        )
        travel_sec = self._sa_calc_travel_time(
            distance, troop_keys_sent, troops_map=troops_map, cmd_attack=cmd_attack
        )

        send_str = self._sa_format_time(send_dt)
        arrive_str = self._sa_format_time(arrive_dt)
        return_str = self._sa_format_time(return_dt, ms_zero=True)

        cmd_type = "Sld" if cmd_attack else "Dst"
        task_id = str(self.sa_table.topLevelItemCount() + 1)

        troop_values = self._sa_queue_format_troop_values(troops_map)
        row_data = [src_text, tgt] + troop_values + [cmd_type, send_str, arrive_str, return_str, task_id]
        item = QTreeWidgetItem(row_data)
        item.setData(0, self.SA_QUEUE_ITEM_ROLE_TIME_MODE, time_mode)

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

    @staticmethod
    def _sa_coords_from_src_text(src_text):
        """Kuyruk/kombo satır metnindeki (x|y) parantezini çöz."""
        m = re.search(r"\((\d+)\s*\|\s*(\d+)\)", str(src_text or ""))
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    def _sa_resolve_travel_source_coords(self, src_text, src_x, src_y):
        """Yolculuk hesabı kaynağı — tabloda görünen metin öncelikli."""
        tx, ty = self._sa_coords_from_src_text(src_text)
        if tx is not None:
            return tx, ty
        if src_x is not None and src_y is not None:
            try:
                return int(src_x), int(src_y)
            except (TypeError, ValueError):
                pass
        return None, None

    def _sa_village_id_from_queue_src(self, src_text):
        """Kuyruk satırı kaynak metninden (isim (x|y)) köy ID."""
        m = re.search(r"\((\d+)\s*\|\s*(\d+)\)", str(src_text or ""))
        if not m:
            return None
        v = self._sa_find_village_at_coord(int(m.group(1)), int(m.group(2)))
        if v and v.get("id") is not None:
            try:
                return int(v.get("id"))
            except (TypeError, ValueError):
                return v.get("id")
        return None

    def _sa_support_reserved_from_queue(self):
        """Bekleyen destek kuyruğundaki köy ID'leri — sonraki planlarda tekrar seçilmez."""
        reserved = set()
        for i in range(self.sa_table.topLevelItemCount()):
            item = self.sa_table.topLevelItem(i)
            if not item:
                continue
            state = item.data(0, Qt.UserRole)
            if state in ("sent", "error"):
                continue
            if (item.text(14) or "").strip() != "Dst":
                continue
            vid = self._sa_village_id_from_queue_src(item.text(0))
            if vid is not None:
                reserved.add(vid)
        return reserved

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

    def _sa_validate_troops_within_village_stock(self, troops_map, src_x, src_y):
        """
        Kaynak köyün `troops` sözlüğü biliniyorsa, komuttaki miktarların stoğu aşmamasını kontrol eder.
        Dönüş: (True, None) veya (False, kullanıcıya gösterilecek metin).
        Veri yoksa veya köy bulunamazsa doğrulama atlanır (True, None).
        """
        v = self._sa_find_village_at_coord(src_x, src_y)
        if not v:
            return True, None
        avail = v.get("troops")
        if not isinstance(avail, dict):
            return True, None
        over = []
        tr = getattr(self, "INCOMINGS_UNIT_TR", None) or {}
        for key, _ in self.SA_UNIT_DEFS:
            want = int(troops_map.get(key, 0) or 0)
            cap = int(avail.get(key, 0) or 0)
            if want > cap:
                label = tr.get(key, key)
                over.append(f"{label}: yazdığınız {want}, köyde {cap}")
        if not over:
            return True, None
        max_lines = 14
        body = "\n".join(over[:max_lines])
        if len(over) > max_lines:
            body += f"\n… ve {len(over) - max_lines} birim daha"
        return False, (
            "Bazı birimler köydekinden fazla:\n\n"
            + body
            + "\n\nMiktarları düzeltin veya birlik verisini yenileyin.\n"
            "(İngilizce sunucularda şövalye = knight.)"
        )

    def _sa_troops_total_population(self, troops_map):
        """Komuttaki birliklerin toplam nüfus yükü (SA_UNIT_POPULATION)."""
        total = 0
        pop = self.SA_UNIT_POPULATION
        for k, _ in self.SA_UNIT_DEFS:
            n = int(troops_map.get(k, 0) or 0)
            total += n * int(pop.get(k, 1))
        return total

    def _sa_resolve_source_village_points(self, src_x, src_y):
        """Seçili kaynak köyün puanı; önbellek yalnızca aynı koordinat için geçerli."""
        try:
            sx, sy = int(src_x), int(src_y)
        except (TypeError, ValueError):
            sx, sy = None, None
        cached = int(getattr(self, "_sa_source_points_cache", 0) or 0)
        cache_xy = getattr(self, "_sa_source_points_cache_xy", None)
        if cached > 0 and cache_xy == (sx, sy):
            return cached
        vt = self._sa_find_village_at_coord(src_x, src_y)
        if not vt:
            return 0
        p = self._sa_points_from_village_dict(vt)
        if p is None:
            return 0
        return p if p > 0 else 0

    def _format_fake_pct(self, pct: float) -> str:
        p = float(pct)
        return str(int(p)) if p == int(p) else str(p)

    def _sa_fake_min_pop_percent(self) -> float:
        ctx = self._world_ctx
        if ctx.fake_limit_verified:
            return float(ctx.fake_min_pop_percent)
        return float(self.SA_FAKE_MIN_POP_PERCENT)

    def _fake_limit_checkbox_text(self) -> str:
        pct = self._sa_fake_min_pop_percent()
        if pct <= 0:
            return "Fake limiti uygula (dünyada pasif — yalnızca koç/mancınık)"
        return f"Fake limiti uygula (köy puanının %{self._format_fake_pct(pct)} nüfusu)"

    def _update_fake_limit_ui(self) -> None:
        dlg = getattr(self, "_army_aux_dialog", None)
        if dlg is not None and hasattr(dlg, "cb_fake_limit"):
            dlg.cb_fake_limit.setText(self._fake_limit_checkbox_text())

    def _sa_evaluate_fake_violation(
        self, cmd_attack, troops_map, src_x, src_y, *, ref_pts=None
    ):
        """
        (True, açıklama) = saldırı fake eşiğinin altında.
        Kaynak köy puanı bilinmiyorsa veya destek komutuysa (False, None).
        ref_pts verilirse koordinat araması yapılmaz (fake planı ile aynı puan).
        """
        if not cmd_attack:
            return False, None
        if ref_pts is None:
            ref_pts = self._sa_resolve_source_village_points(src_x, src_y)
        else:
            try:
                ref_pts = int(ref_pts)
            except (TypeError, ValueError):
                ref_pts = 0
        if ref_pts <= 0:
            return False, None
        pct = self._sa_fake_min_pop_percent()
        if pct <= 0:
            return False, None
        pct_s = self._format_fake_pct(pct)
        min_pop = max(1, int(math.ceil(ref_pts * pct / 100.0)))
        pop = self._sa_troops_total_population(troops_map)
        if pop >= min_pop:
            return False, None
        return True, f"Fake: nüfus {pop} < min. {min_pop} (kaynak {ref_pts} puan, %{pct_s})"

    def _sa_parse_bulk_datetime(self, s):
        """Örnek: 12-04-2026 01:03:27.438, 12.04.2026 01:03:27 veya 2026-06-12 05:50:53.000."""
        s = re.sub(r"\[[^\]]*\]", "", (s or "")).strip()
        m = re.match(
            r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?",
            s,
        )
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
            ms_raw = (m.group(7) or "0")[:3]
            ms = int(ms_raw.zfill(3))
            try:
                return datetime.datetime(year, month, day, hour, minute, second, ms * 1000)
            except (ValueError, OverflowError):
                return None
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

    def _sa_parse_bulk_planner_line(self, line):
        """Planlayıcı / TW export: #7 | [unit]ram[/unit] Clear | 2026-06-12 [b]05:50:53[/b] | ... | 592|610 -> 612|458."""
        line = (line or "").strip()
        if not line or "->" not in line:
            return None
        um = re.search(r"\[unit\](\w+)\[/unit\]", line, re.I)
        if not um:
            return None
        cm = re.search(
            r"(\d{1,3})\|(\d{1,3})\s*->\s*(\d{1,3})\|(\d{1,3})",
            line,
        )
        if not cm:
            return None
        tm = re.search(
            r"(\d{4}-\d{2}-\d{2})\s*\[b\]\s*(\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?)\s*\[/b\]",
            line,
            re.I,
        )
        if not tm:
            tm = re.search(
                r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?)",
                line,
            )
        if not tm:
            return None
        url_act = re.search(r"\[url=[^\]]+\](\w+)\[/url\]", line, re.I)
        if url_act:
            action = url_act.group(1).strip()
        else:
            am = re.search(r"\[unit\]\w+\[/unit\]\s*([^|]+)", line, re.I)
            raw_act = am.group(1) if am else "Attack"
            action = re.sub(r"\[[^\]]*\]", "", raw_act)
            action = re.sub(r"\s+", " ", action).strip() or "Attack"
        send_raw = f"{tm.group(1)} {tm.group(2)}"
        arrive_raw = None
        for am2 in re.finditer(
            r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?)",
            line,
        ):
            cand = f"{am2.group(1)} {am2.group(2)}"
            if cand != send_raw:
                arrive_raw = cand
                break
        return {
            "sx": int(cm.group(1)),
            "sy": int(cm.group(2)),
            "tx": int(cm.group(3)),
            "ty": int(cm.group(4)),
            "unit": um.group(1).lower(),
            "action": action,
            "time_raw": send_raw,
            "arrive_raw": arrive_raw,
        }

    def _sa_parse_bulk_table_lines(self, text):
        """Forum [table], basit pipe ve planlayıcı export satırlarını çözümler."""
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
                continue
            mp = self._sa_parse_bulk_planner_line(line)
            if mp:
                rows.append(mp)
        return rows

    def _sa_troops_ram_full_from_village(self, village_troops):
        """Köydeki balta, hafif, koç, mancınık, atlı okçu, casus — tamamı."""
        t = {k: 0 for k, _ in self.SA_UNIT_DEFS}
        vt = village_troops or {}
        for k in self.SA_RAM_AUTO_KEYS:
            t[k] = int(vt.get(k, 0) or 0)
        return t

    def _sa_troops_copy_keys_from_village(self, village_troops, keys):
        """Köyden yalnızca `keys` içindeki birimlerin tam sayısını alır (diğerleri 0)."""
        t = {k: 0 for k, _ in self.SA_UNIT_DEFS}
        vt = village_troops or {}
        for k in keys:
            if k in t:
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
        parent = _tw_aux_msgbox_parent(msg_parent or self)
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
                    "Desteklenen örnekler:\n"
                    "[*][coord]494|587[/coord][|][coord]574|611[/coord][|][unit]ram[/unit][|]Attack[|]"
                    "[b]12-04-2026 01:03:27.438[/b]\n"
                    "#7 | [unit]ram[/unit] Clear | 2026-06-12 [b]05:50:53.000[/b] | "
                    "2026-06-15 09:00:00.000 | 592|610 -> 612|458 | [url=...]Attack[/url]",
                )
                return

            ws, us = self._sa_get_travel_speed_factors()
            speed_note = ""
            if not getattr(self, "_world_speed_from_settings", False):
                speed_note = (
                    f"\n\nNot: Dünya/birim hızı ayarlar sayfasından doğrulanmadı "
                    f"(şu an ws={ws}, us={us}). Yolculuk sapması olursa oyun sayfasında "
                    "«Veriyi yenile» yapın; üstteki hız etiketini kontrol edin."
                )

            noble_parts = 3
            if any(r["unit"] == "snob" for r in parsed):
                msg = QMessageBox(parent)
                msg.setIcon(QMessageBox.Question)
                msg.setWindowTitle("Misyoner bölme")
                msg.setText(
                    "Listede misyoner (snob) komutu var.\n\n"
                    "Eskort birimleri (balta, hafif, koç, mancınık, atlı okçu, casus) parça sayısına bölünür.\n"
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

                arrive_fixed = None
                if r.get("arrive_raw"):
                    arrive_fixed = self._sa_parse_bulk_datetime(r["arrive_raw"])

                act = r["action"].lower()
                cmd_attack = (
                    "attack" in act
                    or "saldır" in act
                    or "clear" in act
                    or "fake" in act
                )
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
                        arrive_dt_fixed=arrive_fixed,
                    )
                    if ok:
                        added += 1
                    else:
                        skipped.append(f"{src_text}: {err}")
                elif ut == "axe" and cmd_attack:
                    troops_map = self._sa_troops_copy_keys_from_village(
                        troops_avail, self.SA_BULK_AXE_ATTACK_KEYS
                    )
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
                        arrive_dt_fixed=arrive_fixed,
                    )
                    if ok:
                        added += 1
                    else:
                        skipped.append(f"{src_text}: {err}")
                elif ut == "sword" and not cmd_attack:
                    troops_map = self._sa_troops_copy_keys_from_village(
                        troops_avail, self.SA_BULK_SWORD_SUPPORT_KEYS
                    )
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
                        arrive_dt_fixed=arrive_fixed,
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
                        arrive_dt_fixed=arrive_fixed,
                    )
                    if ok:
                        added += 1
                    else:
                        skipped.append(f"{src_text} ({ut}): {err}")

            msg_lines = [f"Eklenen komut: {added}"]
            if speed_note:
                msg_lines.append(speed_note)
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

    # Birim hızları (dakika/kare, varsayılan hız=1 dünya) — yedek; asıl kaynak WorldContext
    UNIT_SPEEDS = DEFAULT_UNIT_SPEEDS

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

    def _sa_calc_travel_time(
        self, distance, troop_keys, *, troops_map=None, cmd_attack=None
    ):
        """Yolculuk süresini saniye olarak hesapla (TW ms hesaplamaz).
        Formül: süre_dk = mesafe × en_yavaş_birim_hızı / (world_speed × unit_speed)
        Destek + şövalye (knight): en yavaş birim yerine şövalye hızı kullanılır.
        """
        use_knight_pace = False
        if cmd_attack is False:
            if troops_map is not None:
                use_knight_pace = int(troops_map.get("knight", 0) or 0) > 0
            elif "knight" in troop_keys:
                use_knight_pace = True

        if use_knight_pace:
            slowest = self._get_unit_travel_speed("knight")
        else:
            slowest = 0
            for key in troop_keys:
                speed = self._get_unit_travel_speed(key)
                if speed > slowest:
                    slowest = speed

        if slowest == 0:
            slowest = self._get_unit_travel_speed("spear")

        world_speed, unit_speed = self._sa_get_travel_speed_factors()

        # TW istemcisi: Math.round(distance * slowest * 60 / world_speed / unit_speed)
        travel_seconds = int(
            round(distance * slowest * 60.0 / (world_speed * unit_speed))
        )
        return max(0, travel_seconds)

    def _sa_get_source_coords(self):
        """Seçili kaynak köyün koordinatlarını döndür."""
        village_id = self.sa_source_combo.currentData()
        if not village_id:
            return None, None

        all_v = self._game_data.get("all_villages", [])
        for v in all_v:
            if self._sa_same_village_id(v.get("id"), village_id):
                xy = self._sa_village_xy(v)
                if xy[0] is not None and xy[1] is not None:
                    return xy

        v = self._game_data.get("village", {})
        if v and self._sa_same_village_id(v.get("id"), village_id):
            xy = self._sa_village_xy(v)
            if xy[0] is not None and xy[1] is not None:
                return xy

        return self._sa_coords_from_src_text(self.sa_source_combo.currentText())

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

    def _sa_reset_queue_item_dispatch_state(self, item):
        """Gönderim önbelleği / hata renklerini sıfırla; satır yeniden beklenebilir."""
        dark = bool(getattr(self, "_dark_mode", False))
        default_fg = QColor("#e8e8e8" if dark else "#000000")
        for col in range(item.columnCount()):
            item.setBackground(col, QBrush())
            item.setForeground(col, default_fg)
        item.setData(0, Qt.UserRole, "")
        item.setData(1, Qt.UserRole, None)
        item.setData(2, Qt.UserRole, None)

    def _sa_style_sa_queue_troop_cells(self, item, troop_values):
        for col in range(2, 14):
            item.setTextAlignment(col, Qt.AlignCenter)
            tv = troop_values[col - 2]
            if tv != "0":
                item.setForeground(col, QColor("#2d5a9e"))
            else:
                item.setForeground(col, QColor("#ccc"))

    def _sa_try_edit_queue_item(self, item, parent=None) -> bool:
        """Bekleyen kuyruk satırını düzenle; reddedilen durumlarda False."""
        if not item:
            return False
        dlg_parent = parent or self
        state = str(item.data(0, Qt.UserRole) or "")
        if state == "sent":
            QMessageBox.information(
                dlg_parent, "Ordu Gönder", "Bu satır zaten gönderildi; düzenlenemez."
            )
            return False
        if state in ("cached", "caching", "confirming", "confirmed", "sending"):
            QMessageBox.warning(
                dlg_parent, "Ordu Gönder", "Bu satır gönderim sürecinde; şu an düzenlenemez."
            )
            return False
        if not re.search(r"\((\d+)\|(\d+)\)", item.text(0) or ""):
            QMessageBox.warning(
                dlg_parent, "Ordu Gönder", "Kaynak satırından koordinat okunamadı."
            )
            return False
        if not re.search(r"(\d+)\|(\d+)", item.text(1) or ""):
            QMessageBox.warning(dlg_parent, "Ordu Gönder", "Hedef koordinat geçersiz.")
            return False
        try:
            return SaCommandEditDialog(dlg_parent, self, item).exec_() == QDialog.Accepted
        except Exception as exc:
            QMessageBox.critical(
                dlg_parent,
                "Ordu Gönder",
                f"Komut düzenleme penceresi açılamadı:\n{exc}",
            )
            return False

    def _sa_on_army_queue_item_double_clicked(self, item, column):
        self._sa_try_edit_queue_item(item)

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
                if state in ("sent", "error"):
                    continue
                catapult = str(it.data(0, self.SA_QUEUE_ITEM_ROLE_CATAPULT) or "")
                rows.append([it.text(c) for c in range(n)] + [state, catapult])
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
            # Geriye uyumluluk: n | n+1 (state) | n+2 (state+catapult)
            if len(row) >= n + 2:
                text_cols = row[:n]
                state = str(row[n])
                catapult = str(row[n + 1] or "")
            elif len(row) == n + 1:
                text_cols = row[:n]
                state = str(row[n])
                catapult = ""
            elif len(row) == n:
                text_cols = row
                state = ""
                catapult = ""
            else:
                continue
            # Geçici state'leri temizle
            if state in _TRANSIENT:
                state = ""
            # Eski kayıt: tamamlananlar ana kuyrukta kalmasın (yeniden gönderim riski)
            if state == "sent":
                self._sa_history_append_row_from_saved(
                    text_cols, catapult, "sent", "Gönderildi (kayıttan)"
                )
                continue
            if state == "error":
                err_txt = ""
                if len(text_cols) > 18:
                    err_txt = str(text_cols[18]).strip()
                if not err_txt or err_txt == "HATA":
                    err_txt = "Kayıtlı hata (ayrıntı yok)"
                self._sa_history_append_row_from_saved(text_cols, catapult, "error", err_txt)
                continue

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
            if catapult:
                item.setData(0, self.SA_QUEUE_ITEM_ROLE_CATAPULT, catapult)
        if getattr(self, "_unit_speeds_fetched", False):
            self._sa_refresh_all_queue_timelines()
        self._sa_update_totals()

    def _sa_update_totals(self):
        count = self.sa_table.topLevelItemCount()
        nh = (
            self.sa_history_table.topLevelItemCount()
            if hasattr(self, "sa_history_table")
            else 0
        )
        self.sa_totals_label.setText(f"BEKLEYEN: {count} komut  |  GEÇMİŞ: {nh}")
        self._sa_save_army_queue()
        self._sa_save_army_history()

    def _sa_trim_army_history_overflow(self):
        if not hasattr(self, "sa_history_table"):
            return
        mx = int(getattr(self, "SA_ARMY_HISTORY_MAX_ROWS", 400) or 400)
        while self.sa_history_table.topLevelItemCount() > mx:
            self.sa_history_table.takeTopLevelItem(self.sa_history_table.topLevelItemCount() - 1)

    def _sa_history_style_row(self, item, status):
        for col in range(item.columnCount()):
            if status == "sent":
                item.setBackground(col, QColor("#d4f0d4"))
                item.setForeground(col, QColor("#2a7a2a"))
            else:
                item.setBackground(col, QColor("#f0d4d4"))
                item.setForeground(col, QColor("#aa3333"))
            item.setTextAlignment(col, Qt.AlignCenter)

    def _sa_history_append_row_from_saved(self, text_cols, catapult, status, detail):
        """Kayıt dosyasından gelen tamamlanan satırı geçmiş tabloya ekler."""
        if not hasattr(self, "sa_history_table"):
            return
        n = self.sa_table.columnCount()
        padded = [str(text_cols[i]) if i < len(text_cols) else "" for i in range(n)]
        d = (detail or "—").strip()
        if len(d) > 500:
            d = d[:497] + "..."
        hi = QTreeWidgetItem(padded + [d])
        self._sa_history_style_row(hi, status)
        hi.setData(0, Qt.UserRole, status)
        if catapult:
            hi.setData(0, self.SA_QUEUE_ITEM_ROLE_CATAPULT, catapult)
        self.sa_history_table.insertTopLevelItem(0, hi)
        self._sa_trim_army_history_overflow()

    def _sa_move_completed_row_to_history(self, item, status, detail):
        """Ana kuyruktan tek satırı geçmişe taşır (item zaten boyanmış olabilir)."""
        if not hasattr(self, "sa_history_table"):
            return
        idx = self.sa_table.indexOfTopLevelItem(item)
        if idx < 0:
            return
        it = self.sa_table.takeTopLevelItem(idx)
        if not it:
            return
        d = (detail or "—").strip()
        if len(d) > 500:
            d = d[:497] + "..."
        it.setText(19, d)
        it.setData(0, Qt.UserRole, status)
        for col in range(it.columnCount()):
            it.setTextAlignment(col, Qt.AlignCenter)
        self.sa_history_table.insertTopLevelItem(0, it)
        self._sa_trim_army_history_overflow()

    def _sa_move_completed_rows_batch(self, items, status, detail):
        """Aynı anda tamamlanan birden fazla satırı güvenli indeks sırasıyla geçmişe taşır."""
        idxs = sorted(
            {self.sa_table.indexOfTopLevelItem(it) for it in items if it is not None},
            reverse=True,
        )
        d = (detail or "—").strip()
        if len(d) > 500:
            d = d[:497] + "..."
        for idx in idxs:
            if idx < 0:
                continue
            it = self.sa_table.takeTopLevelItem(idx)
            if not it:
                continue
            it.setText(19, d)
            it.setData(0, Qt.UserRole, status)
            for col in range(it.columnCount()):
                it.setTextAlignment(col, Qt.AlignCenter)
            self.sa_history_table.insertTopLevelItem(0, it)
        self._sa_trim_army_history_overflow()
        self._sa_update_totals()

    def _sa_save_army_history(self):
        if not hasattr(self, "sa_history_table") or not hasattr(self, "_settings"):
            return
        try:
            n = self.sa_history_table.columnCount()
            rows = []
            for i in range(self.sa_history_table.topLevelItemCount()):
                it = self.sa_history_table.topLevelItem(i)
                catapult = str(it.data(0, self.SA_QUEUE_ITEM_ROLE_CATAPULT) or "")
                rows.append([it.text(c) for c in range(n)] + [catapult])
            self._settings.setValue("army_history/rows_json", json.dumps(rows, ensure_ascii=False))
            self._settings.sync()
        except (TypeError, ValueError):
            pass

    def _sa_restore_army_history(self):
        if not hasattr(self, "sa_history_table") or not hasattr(self, "_settings"):
            return
        raw = self._settings.value("army_history/rows_json", "")
        if not raw:
            return
        try:
            rows = json.loads(str(raw))
        except json.JSONDecodeError:
            return
        if not isinstance(rows, list):
            return
        n = self.sa_history_table.columnCount()
        self.sa_history_table.clear()
        for row in rows:
            if not isinstance(row, list) or len(row) < n:
                continue
            text_cols = [str(x) for x in row[:n]]
            catapult = str(row[n]) if len(row) > n else ""
            detail = str(text_cols[-1]) if text_cols else ""
            status = "sent" if "Gönderildi" in detail else "error"
            hi = QTreeWidgetItem(text_cols)
            self._sa_history_style_row(hi, status)
            hi.setData(0, Qt.UserRole, status)
            if catapult:
                hi.setData(0, self.SA_QUEUE_ITEM_ROLE_CATAPULT, catapult)
            self.sa_history_table.addTopLevelItem(hi)
        self._sa_trim_army_history_overflow()

    def _sa_clear_army_history(self):
        if not hasattr(self, "sa_history_table"):
            return
        self.sa_history_table.clear()
        self._sa_update_totals()

    def _sa_on_army_history_item_double_clicked(self, item, column):
        QMessageBox.information(
            self,
            "Tamamlanan komut",
            "Geçmiş satırlar salt okunurdur.\n"
            "Yeni komut eklemek için üstteki «Bekleyen komutlar» tablosunu kullanın.",
        )

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
        raw = (troops or {}).get(key, 0)
        if raw is None or raw == "":
            return 0
        try:
            if isinstance(raw, (int, float)):
                return max(0, int(raw))
            s = str(raw).strip().replace("\u00a0", "").replace(",", "")
            if not s or s in ("-", "?"):
                return 0
            if re.match(r"^\d{1,3}(\.\d{3})+$", s):
                s = s.replace(".", "")
            return max(0, int(float(s)) if "." in s else int(s))
        except (TypeError, ValueError):
            return 0

    def _sa_troops_sum(self, troops) -> int:
        if not troops or not isinstance(troops, dict):
            return 0
        total = 0
        for val in troops.values():
            try:
                total += max(0, int(val))
            except (TypeError, ValueError):
                pass
        return total

    def _sa_merge_troops_max_snob(self, base, *others):
        """Misyoner: tablo/game_data uyumsuzluğunda daha yüksek değeri koru."""
        out = dict(base or {})
        best = self._sa_troop_count(out, "snob")
        for src in others:
            if isinstance(src, dict):
                best = max(best, self._sa_troop_count(src, "snob"))
        if best > 0:
            out["snob"] = best
        return out

    def _sa_resolve_village_troops(self, village_id):
        """Ordu Gönder stok etiketi — all_villages + tarayıcıdaki aktif köy yedeği."""
        stale = None
        for v in (self._game_data or {}).get("all_villages") or []:
            if not self._sa_same_village_id(v.get("id"), village_id):
                continue
            t = v.get("troops")
            if isinstance(t, dict) and self._sa_troops_sum(t) > 0:
                result = dict(t)
                gv = (self._game_data or {}).get("village") or {}
                if self._sa_same_village_id(gv.get("id"), village_id):
                    active = (self._game_data or {}).get("troops") or {}
                    result = self._sa_merge_troops_max_snob(result, active)
                return result
            if isinstance(t, dict):
                stale = dict(t)
            break

        gv = (self._game_data or {}).get("village") or {}
        if self._sa_same_village_id(gv.get("id"), village_id):
            active = (self._game_data or {}).get("troops") or {}
            if self._sa_troops_sum(active) > 0:
                if isinstance(stale, dict) and stale:
                    return self._sa_merge_troops_max_snob(stale, active)
                return dict(active)

        return stale if isinstance(stale, dict) else {}

    def _sa_is_barbar_village(self, v):
        return "barbar" in (v.get("name") or "").lower()

    def _sa_village_src_label(self, v):
        sx, sy = self._sa_village_xy(v)
        name = v.get("name", "?")
        if sx is not None and sy is not None:
            return f"{name} ({sx}|{sy})"
        return str(name)

    def _refresh_support_plan_groups(self) -> None:
        """Şablonlu destek sekmesindeki grup combobox'ını village_groups ile güncelle."""
        dlg = getattr(self, "_army_aux_dialog", None)
        if dlg is None or not hasattr(dlg, "cb_support_group"):
            return
        groups = self._game_data.get("village_groups") or []
        cur_gid = None
        if dlg.cb_support_group.count() > 0:
            cur_gid = dlg.cb_support_group.currentData()
        dlg.cb_support_group.blockSignals(True)
        dlg.cb_support_group.clear()
        for g in groups:
            gid = str(g.get("id", "") or "")
            name = (g.get("name") or "").strip() or gid
            gtype = (g.get("type") or "static").strip()
            label = f"{name} ({gtype})"
            dlg.cb_support_group.addItem(label, g)
        if cur_gid is not None:
            for i in range(dlg.cb_support_group.count()):
                g = dlg.cb_support_group.itemData(i)
                if g and str(g.get("id", "")) == str(cur_gid.get("id", "") if isinstance(cur_gid, dict) else cur_gid):
                    dlg.cb_support_group.setCurrentIndex(i)
                    break
        else:
            s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
            gid_saved = (s.value("support_plan/selected_group_id", "") or "").strip()
            if gid_saved:
                for i in range(dlg.cb_support_group.count()):
                    g = dlg.cb_support_group.itemData(i)
                    if g and str(g.get("id", "")) == gid_saved:
                        dlg.cb_support_group.setCurrentIndex(i)
                        break
        dlg.cb_support_group.blockSignals(False)

    def _sa_village_in_group(self, village, group_id, group_name, group_type) -> bool:
        names = village.get("group_names") or []
        if group_name and group_name in names:
            return True
        return False

    def _sa_build_troops_from_template(self, stock, template):
        """Şablondan kısmi asker paketi: her birim min(stok, şablon)."""
        tpl = template or {}
        if not tpl:
            return None
        troops = {k: 0 for k, _ in self.SA_UNIT_DEFS}
        total = 0
        for k, n in tpl.items():
            try:
                want = max(0, int(n))
            except (TypeError, ValueError):
                continue
            if want <= 0:
                continue
            have = self._sa_troop_count(stock, k)
            send_n = min(have, want)
            if send_n > 0:
                troops[k] = send_n
                total += send_n
        if total <= 0:
            return None
        return troops

    def _sa_build_fake_troops(
        self, village_troops, selected_unit_keys, enforce_fake_limit, village_points
    ):
        """Seçili birimlerle fake komutu; limit açıksa nüfusu eşit dağılımla tamamlar."""
        selected_set = set(selected_unit_keys or [])
        if not selected_set:
            return None

        vt = village_troops or {}
        cycle = [k for k, _ in self.SA_UNIT_DEFS if k in selected_set]
        if not cycle:
            return None

        if selected_set & {"ram", "catapult"}:
            if self._sa_troop_count(vt, "ram") + self._sa_troop_count(vt, "catapult") < 1:
                return None

        troops = {k: 0 for k, _ in self.SA_UNIT_DEFS}

        pct = self._sa_fake_min_pop_percent()
        effective_enforce = enforce_fake_limit and pct > 0

        if not effective_enforce:
            for pref in ("ram", "catapult"):
                if pref in selected_set and self._sa_troop_count(vt, pref) >= 1:
                    troops[pref] = 1
                    return troops
            for k in cycle:
                if self._sa_troop_count(vt, k) >= 1:
                    troops[k] = 1
                    return troops
            return None

        if village_points is None or village_points <= 0:
            return None

        min_pop = max(1, int(math.ceil(village_points * pct / 100.0)))
        pop_table = self.SA_UNIT_POPULATION
        stock = {k: self._sa_troop_count(vt, k) for k in cycle}
        if sum(stock.values()) <= 0:
            return None

        cur_pop = 0
        idx = 0
        max_iters = min_pop * 20 + sum(stock.values()) + 16

        for _ in range(max_iters):
            if cur_pop >= min_pop:
                break
            added = False
            for _pass in range(len(cycle)):
                k = cycle[idx % len(cycle)]
                idx += 1
                if stock.get(k, 0) <= 0:
                    continue
                pop_cost = int(pop_table.get(k, 1))
                if pop_cost <= 0:
                    continue
                troops[k] += 1
                stock[k] -= 1
                cur_pop += pop_cost
                added = True
                if cur_pop >= min_pop:
                    break
            if not added:
                return None

        if self._sa_troops_total_population(troops) < min_pop:
            return None
        return troops

    def _sa_subtract_troops_from_stock(self, stock, troops_map):
        """Köy stokundan gönderilen miktarları düş (kalan stok sözlüğü)."""
        out = dict(stock or {})
        for k, _ in self.SA_UNIT_DEFS:
            n = int(troops_map.get(k, 0) or 0)
            if n <= 0:
                continue
            out[k] = max(0, self._sa_troop_count(out, k) - n)
        return out

    def _sa_plan_mass_fakes_with(
        self,
        targets_text,
        max_per_source,
        selected_unit_keys,
        enforce_fake_limit,
        base_arrive,
        msg_parent=None,
    ):
        """Fake kuyruğu: hedef başına farklı köy önceliği, kuyruk sırası Sophie (acil atış)."""
        parent = _tw_aux_msgbox_parent(msg_parent or self)
        ws, us = self._sa_get_travel_speed_factors()
        if not getattr(self, "_world_speed_from_settings", False):
            self._add_log(
                "PLAN",
                "warn",
                f"Fake planı: dünya/birim hızı doğrulanmadı (ws={ws}, us={us}). "
                "Gönderim zamanı sapabilir — oyun sayfasında Veriyi yenileyin.",
            )
        targets = self._sa_parse_targets_coords(targets_text or "")
        if not targets:
            QMessageBox.warning(
                parent,
                "Fake planı",
                "En az bir hedef yazın (örn. 505|588 veya 505|588, 500|586).",
            )
            return

        if not any(u in (selected_unit_keys or []) for u in ("ram", "catapult")):
            QMessageBox.warning(
                parent, "Fake planı", "En az koç veya mancınık seçili olmalı."
            )
            return

        villages = [
            v
            for v in (self._game_data.get("all_villages") or [])
            if v and not self._sa_is_barbar_village(v)
        ]
        if not villages:
            QMessageBox.warning(
                parent,
                "Fake planı",
                "Köy / birlik verisi yok. Tarayıcıdan birlikleri yenileyin.",
            )
            return

        max_per_source = max(1, int(max_per_source or 1))
        now = self._server_now_dt() or datetime.datetime.now()
        if base_arrive <= now:
            QMessageBox.warning(
                parent,
                "Fake planı",
                f"Varış zamanı sunucu saatinden önce veya aynı anda.\n\n"
                f"Varış: {base_arrive.strftime('%d.%m %H:%M:%S')}\n"
                f"Sunucu: {now.strftime('%d.%m %H:%M:%S')}\n\n"
                "Fake planı sekmesinde varışı ileri alın (en uzak köye yetecek kadar).",
            )
            return

        pool = []
        n_no_coord = 0
        n_no_siege = 0
        n_no_pts = 0
        for v in villages:
            vid = v.get("id")
            sx, sy = self._sa_village_xy(v)
            if sx is None or vid is None:
                n_no_coord += 1
                continue
            stock0 = v.get("troops") or {}
            if not self._sa_village_has_siege_stock(stock0, selected_unit_keys):
                n_no_siege += 1
                continue
            pts = self._sa_resolve_village_points(v)
            if enforce_fake_limit and (pts is None or pts <= 0):
                n_no_pts += 1
                continue
            pool.append(
                {
                    "v": v,
                    "vid": vid,
                    "sx": sx,
                    "sy": sy,
                    "pts": pts,
                    "pts_known": pts is not None and pts > 0,
                }
            )

        if not pool:
            extra_pts = (
                f"\n• Puan bilinmiyor (fake limiti açık): {n_no_pts}"
                if enforce_fake_limit and n_no_pts
                else ""
            )
            QMessageBox.warning(
                parent,
                "Fake planı",
                f"Uygun kaynak köy yok ({len(villages)} köy listelendi).\n\n"
                f"• Koordinatsız: {n_no_coord}\n"
                f"• Seçili kuşatma yok (koç/mancınık): {n_no_siege}"
                f"{extra_pts}\n\n"
                "Tarayıcıda oyun sayfasını yenileyin (köy puanları üretim "
                "sekmesinden otomatik çekilir); fake birimlerinde en az koç veya "
                "mancınık işaretli olsun.",
            )
            return

        usage = {}
        remaining = {p["vid"]: dict(p["v"].get("troops") or {}) for p in pool}
        target_hits = {(tx, ty): 0 for tx, ty in targets}
        added = 0
        skipped = []
        n_late = 0
        max_total = len(targets) * max_per_source
        max_rounds = max_total * max(len(pool), 1) + 16

        for _round in range(max_rounds):
            if added >= max_total:
                break
            candidates = []
            for tx, ty in targets:
                coord = (tx, ty)
                if target_hits.get(coord, 0) >= max_per_source:
                    continue
                for p in pool:
                    vid = p["vid"]
                    if usage.get(vid, 0) >= max_per_source:
                        continue
                    ref_pts = self._sa_resolve_village_points(p["v"])
                    if enforce_fake_limit and (ref_pts is None or ref_pts <= 0):
                        continue
                    p["pts"] = ref_pts
                    stock = remaining.get(vid) or {}
                    troops_map = self._sa_build_fake_troops(
                        stock,
                        selected_unit_keys,
                        enforce_fake_limit,
                        ref_pts,
                    )
                    if not troops_map:
                        continue
                    troop_keys = [
                        k
                        for k, _ in self.SA_UNIT_DEFS
                        if int(troops_map.get(k, 0) or 0) > 0
                    ]
                    sx, sy = p["sx"], p["sy"]
                    dist = math.hypot(tx - sx, ty - sy)
                    travel_sec = self._sa_calc_travel_time(
                        dist, troop_keys, troops_map=troops_map, cmd_attack=True
                    )
                    launch_dt = base_arrive - datetime.timedelta(
                        seconds=travel_sec
                    )
                    if launch_dt <= now:
                        n_late += 1
                        continue
                    time_till = (launch_dt - now).total_seconds()
                    candidates.append(
                        {
                            "time_till": time_till,
                            "usage": usage.get(vid, 0),
                            "dist": dist,
                            "tx": tx,
                            "ty": ty,
                            "coord": coord,
                            "p": p,
                            "vid": vid,
                            "sx": sx,
                            "sy": sy,
                            "ref_pts": ref_pts,
                            "troops_map": troops_map,
                            "stock": stock,
                        }
                    )

            if not candidates:
                break

            # Hedef başına: önce az kullanılan köy, sonra atışa az süre kalan (uzak).
            by_target = {}
            for c in candidates:
                key = c["coord"]
                prev = by_target.get(key)
                if prev is None or (c["usage"], c["time_till"], -c["dist"]) < (
                    prev["usage"],
                    prev["time_till"],
                    -prev["dist"],
                ):
                    by_target[key] = c

            round_picks = list(by_target.values())
            # Sophie: bu turda en acil hedef–köy çifti önce kuyruğa (timeTillFake artan).
            round_picks.sort(
                key=lambda c: (c["time_till"], c["usage"], -c["dist"])
            )

            assigned_round = False
            for pick in round_picks:
                p = pick["p"]
                vid = pick["vid"]
                tx, ty = pick["tx"], pick["ty"]
                coord = pick["coord"]
                sx, sy = pick["sx"], pick["sy"]
                troops_map = pick["troops_map"]
                stock = pick["stock"]
                ref_pts = pick["ref_pts"]

                if enforce_fake_limit:
                    violate, detail = self._sa_evaluate_fake_violation(
                        True, troops_map, sx, sy, ref_pts=ref_pts
                    )
                    if violate:
                        skipped.append(
                            f"{tx}|{ty} ← {p['v'].get('name', '?')}: {detail}"
                        )
                        continue

                old_cache = int(getattr(self, "_sa_source_points_cache", 0) or 0)
                old_cache_xy = getattr(self, "_sa_source_points_cache_xy", None)
                if enforce_fake_limit and ref_pts:
                    self._sa_source_points_cache = int(ref_pts)
                    self._sa_source_points_cache_xy = (int(sx), int(sy))
                try:
                    ok, err = self._sa_append_row_from_values(
                        self._sa_village_src_label(p["v"]),
                        sx,
                        sy,
                        tx,
                        ty,
                        dict(troops_map),
                        True,
                        "arrive",
                        base_arrive,
                        fake_dialog=False,
                        check_fake_limit=enforce_fake_limit,
                    )
                finally:
                    self._sa_source_points_cache = old_cache
                    self._sa_source_points_cache_xy = old_cache_xy

                if ok:
                    added += 1
                    usage[vid] = usage.get(vid, 0) + 1
                    target_hits[coord] = target_hits.get(coord, 0) + 1
                    remaining[vid] = self._sa_subtract_troops_from_stock(
                        stock, troops_map
                    )
                    assigned_round = True
                    break
                skipped.append(
                    f"{self._sa_village_src_label(p['v'])} → {tx}|{ty}: {err or '?'}"
                )

            if not assigned_round:
                break

        n_no_template = sum(
            1 for tx, ty in targets if target_hits.get((tx, ty), 0) == 0
        )

        for tx, ty in targets:
            h = target_hits.get((tx, ty), 0)
            if h == 0:
                skipped.append(f"Hedef atanamadı: {tx}|{ty}")
            elif h < max_per_source:
                skipped.append(
                    f"{tx}|{ty}: {h}/{max_per_source} fake (köy/asker yetersiz)"
                )

        self._sa_update_totals()
        msg = (
            f"Kuyruğa eklenen fake: {added}\n"
            f"Kaynak havuzu: {len(pool)} köy (toplam {len(villages)} köy)"
        )
        if n_late or n_no_template:
            msg += (
                f"\n• Varış çok yakın / geçmiş (sunucu saati): {n_late} köy–hedef denemesi"
                f"\n• Yeterli asker / şablon yok: {n_no_template} hedef"
            )
            if n_late:
                msg += "\n  → Varış saatini ileri alın (en uzak köyden yetişecek kadar)."
        if enforce_fake_limit and n_no_pts:
            msg += (
                f"\n• {n_no_pts} köy puanı okunamadı → fake limiti açıkken atlanır "
                "(oyun sayfasını yenileyin; puanlar üretim sekmesinden çekilir)"
            )
        if skipped:
            msg += "\n\nAtlanan / not:\n" + "\n".join(skipped[:20])
            if len(skipped) > 20:
                msg += f"\n… +{len(skipped) - 20} satır"
        QMessageBox.information(parent, "Fake planı", msg)

    def _sa_plan_mass_support_with_template(
        self,
        targets_text,
        villages_per_target,
        group,
        template,
        base_arrive,
        msg_parent=None,
    ):
        """Şablonlu destek kuyruğu: seçili gruptaki köyler, Sophie sıralama."""
        parent = _tw_aux_msgbox_parent(msg_parent or self)
        ws, us = self._sa_get_travel_speed_factors()
        if not getattr(self, "_world_speed_from_settings", False):
            self._add_log(
                "PLAN",
                "warn",
                f"Şablonlu destek: dünya/birim hızı doğrulanmadı (ws={ws}, us={us}). "
                "Gönderim zamanı sapabilir — oyun sayfasında Veriyi yenileyin.",
            )
        raw = (targets_text or "").strip()
        if re.search(r"[\s,;]", raw) and len(self._sa_parse_targets_coords(raw)) > 1:
            QMessageBox.warning(
                parent,
                "Şablonlu destek",
                "Yalnızca bir hedef koordinatı girin (örn. 505|588).",
            )
            return
        targets = self._sa_parse_targets_coords(raw)
        if not targets:
            QMessageBox.warning(
                parent,
                "Şablonlu destek",
                "Geçerli bir hedef yazın (örn. 505|588).",
            )
            return
        if len(targets) > 1:
            QMessageBox.warning(
                parent,
                "Şablonlu destek",
                "Yalnızca bir hedef koordinatı planlanabilir.",
            )
            return

        if not template or not any(int(v or 0) > 0 for v in template.values()):
            QMessageBox.warning(
                parent,
                "Şablonlu destek",
                "Asker şablonunda en az bir birim 0'dan büyük olmalı.",
            )
            return

        group = group or {}
        group_name = (group.get("name") or "").strip()
        group_id = str(group.get("id", "") or "")
        group_type = (group.get("type") or "static").strip()
        if not group_name:
            QMessageBox.warning(parent, "Şablonlu destek", "Geçerli bir köy grubu seçin.")
            return

        all_villages = self._game_data.get("all_villages") or []
        villages = [
            v
            for v in all_villages
            if v
            and not self._sa_is_barbar_village(v)
            and self._sa_village_in_group(v, group_id, group_name, group_type)
        ]
        if not villages:
            QMessageBox.warning(
                parent,
                "Şablonlu destek",
                "Gruplar verisi yok veya seçili grupta köy yok — Veriyi yenileyin "
                "veya oyunda grupları kontrol edin.",
            )
            return

        villages_per_target = max(1, int(villages_per_target or 1))
        now = self._server_now_dt() or datetime.datetime.now()
        if base_arrive <= now:
            QMessageBox.warning(
                parent,
                "Şablonlu destek",
                f"Varış zamanı sunucu saatinden önce veya aynı anda.\n\n"
                f"Varış: {base_arrive.strftime('%d.%m %H:%M:%S')}\n"
                f"Sunucu: {now.strftime('%d.%m %H:%M:%S')}\n\n"
                "Varışı ileri alın (en uzak köyden yetişecek kadar).",
            )
            return

        reserved_vids = self._sa_support_reserved_from_queue()
        n_reserved_skip = 0

        pool = []
        n_no_coord = 0
        n_no_stock = 0
        for v in villages:
            vid = v.get("id")
            sx, sy = self._sa_village_xy(v)
            if sx is None or vid is None:
                n_no_coord += 1
                continue
            if vid in reserved_vids:
                n_reserved_skip += 1
                continue
            stock0 = v.get("troops") or {}
            if not self._sa_build_troops_from_template(stock0, template):
                n_no_stock += 1
                continue
            pool.append(
                {
                    "v": v,
                    "vid": vid,
                    "sx": sx,
                    "sy": sy,
                }
            )

        if not pool:
            QMessageBox.warning(
                parent,
                "Şablonlu destek",
                f"Uygun kaynak köy yok ({len(villages)} köy grupta).\n\n"
                f"• Kuyrukta rezerve: {n_reserved_skip}\n"
                f"• Koordinatsız: {n_no_coord}\n"
                f"• Şablona uygun stok yok: {n_no_stock}\n\n"
                "Tarayıcıdan birlikleri yenileyin, şablonu düşürün veya kuyruğu kontrol edin.",
            )
            return

        usage = {}
        remaining = {p["vid"]: dict(p["v"].get("troops") or {}) for p in pool}
        target_hits = {(tx, ty): 0 for tx, ty in targets}
        added = 0
        skipped = []
        n_late = 0
        max_total = len(targets) * villages_per_target
        max_rounds = max_total * max(len(pool), 1) + 16

        for _round in range(max_rounds):
            if added >= max_total:
                break
            candidates = []
            for tx, ty in targets:
                coord = (tx, ty)
                if target_hits.get(coord, 0) >= villages_per_target:
                    continue
                for p in pool:
                    vid = p["vid"]
                    if usage.get(vid, 0) >= 1:
                        continue
                    if vid in reserved_vids:
                        continue
                    stock = remaining.get(vid) or {}
                    troops_map = self._sa_build_troops_from_template(stock, template)
                    if not troops_map:
                        continue
                    troop_keys = [
                        k
                        for k, _ in self.SA_UNIT_DEFS
                        if int(troops_map.get(k, 0) or 0) > 0
                    ]
                    sx, sy = p["sx"], p["sy"]
                    dist = math.hypot(tx - sx, ty - sy)
                    travel_sec = self._sa_calc_travel_time(
                        dist, troop_keys, troops_map=troops_map, cmd_attack=False
                    )
                    launch_dt = base_arrive - datetime.timedelta(seconds=travel_sec)
                    if launch_dt <= now:
                        n_late += 1
                        continue
                    time_till = (launch_dt - now).total_seconds()
                    candidates.append(
                        {
                            "time_till": time_till,
                            "usage": usage.get(vid, 0),
                            "dist": dist,
                            "tx": tx,
                            "ty": ty,
                            "coord": coord,
                            "p": p,
                            "vid": vid,
                            "sx": sx,
                            "sy": sy,
                            "troops_map": troops_map,
                            "stock": stock,
                        }
                    )

            if not candidates:
                break

            by_target = {}
            for c in candidates:
                key = c["coord"]
                prev = by_target.get(key)
                if prev is None or (c["usage"], c["time_till"], -c["dist"]) < (
                    prev["usage"],
                    prev["time_till"],
                    -prev["dist"],
                ):
                    by_target[key] = c

            round_picks = list(by_target.values())
            round_picks.sort(key=lambda c: (c["time_till"], c["usage"], -c["dist"]))

            assigned_round = False
            for pick in round_picks:
                p = pick["p"]
                vid = pick["vid"]
                tx, ty = pick["tx"], pick["ty"]
                coord = pick["coord"]
                sx, sy = pick["sx"], pick["sy"]
                troops_map = pick["troops_map"]
                stock = pick["stock"]

                ok, err = self._sa_append_row_from_values(
                    self._sa_village_src_label(p["v"]),
                    sx,
                    sy,
                    tx,
                    ty,
                    dict(troops_map),
                    False,
                    "arrive",
                    base_arrive,
                    fake_dialog=False,
                    check_fake_limit=False,
                )

                if ok:
                    added += 1
                    usage[vid] = usage.get(vid, 0) + 1
                    reserved_vids.add(vid)
                    target_hits[coord] = target_hits.get(coord, 0) + 1
                    remaining[vid] = self._sa_subtract_troops_from_stock(
                        stock, troops_map
                    )
                    assigned_round = True
                    break
                skipped.append(
                    f"{self._sa_village_src_label(p['v'])} → {tx}|{ty}: {err or '?'}"
                )

            if not assigned_round:
                break

        n_no_template = sum(
            1 for tx, ty in targets if target_hits.get((tx, ty), 0) == 0
        )

        for tx, ty in targets:
            h = target_hits.get((tx, ty), 0)
            if h == 0:
                skipped.append(f"Hedef atanamadı: {tx}|{ty}")
            elif h < villages_per_target:
                skipped.append(
                    f"{tx}|{ty}: {h}/{villages_per_target} destek (köy/asker yetersiz)"
                )

        self._sa_update_totals()
        msg = (
            f"Kuyruğa eklenen destek: {added} / hedef başına {villages_per_target}\n"
            f"Grup «{group_name}»: {len(pool)} uygun köy (toplam {len(villages)} grupta)"
        )
        if n_reserved_skip:
            msg += f"\n• Kuyrukta rezerve olduğu için hariç: {n_reserved_skip} köy"
        if n_late or n_no_template:
            msg += (
                f"\n• Varış çok yakın / geçmiş (sunucu saati): {n_late} köy–hedef denemesi"
                f"\n• Yeterli asker / şablon yok: {n_no_template} hedef"
            )
            if n_late:
                msg += "\n  → Varış saatini ileri alın (en uzak köyden yetişecek kadar)."
        if skipped:
            msg += "\n\nAtlanan / not:\n" + "\n".join(skipped[:20])
            if len(skipped) > 20:
                msg += f"\n… +{len(skipped) - 20} satır"
        QMessageBox.information(parent, "Şablonlu destek", msg)

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
            for col_idx, (key, _) in enumerate(self._sa_sendable_unit_defs()):
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
        for col_idx, key in enumerate(SA_QUEUE_TABLE_TROOP_KEYS):
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

        # Asker sayıları (tablo sütunları sabit Mız…Mis sırası)
        troops = self._sa_queue_troop_map_from_item(item)

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
                if (/hcaptcha|botprotection|bot\\s*koruma|captcha/i.test(confirmHtml)) {{
                    window.__tw_bot_results[cmdId] = 'ERROR|BOTPROT|Dogrulama sayfasi';
                    return;
                }}

                var doc2 = new DOMParser().parseFromString(confirmHtml, 'text/html');
                var cf = __twFindConfirmForm(doc2);
                if (!cf) {{
                    if (/hcaptcha|botprotection|bot\\s*koruma|captcha/i.test(confirmHtml)) {{
                        window.__tw_bot_results[cmdId] = 'ERROR|BOTPROT|Onay formu yok';
                    }} else {{
                        window.__tw_bot_results[cmdId] = 'ERROR|Onay formu bulunamadi';
                    }}
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
                self._dispatch_mark_error(it, "Zaman aşımı", move_to_history=False)
            self._sa_move_completed_rows_batch(rows, "error", "Zaman aşımı")
            self._add_log("GÖNDERİM", "error",
                f"❌ Zaman aşımı: {src_text} → ({tgt_text})")
            return

        check_js = f"window.__tw_bot_results ? window.__tw_bot_results['{cmd_id}'] || 'WAITING' : 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str == "SENT_OK":
                for it in rows:
                    self._dispatch_mark_sent(it, move_to_history=False)
                self._sa_move_completed_rows_batch(rows, "sent", "Gönderildi")
                batch_note = f" ({len(rows)} dalga)" if len(rows) > 1 else ""
                self._add_log("GÖNDERİM", "success",
                    f"✅ Komut gönderildi{batch_note}: {src_text} → ({tgt_text}) | {cmd_type}")
                # Temizle
                self.browser.page().runJavaScript(
                    f"if(window.__tw_bot_results) delete window.__tw_bot_results['{cmd_id}'];")

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                if error.startswith("BOTPROT") or self._dispatch_error_suggests_botprot(error):
                    self._botprot_start_fast_poll(90)
                    self._set_human_verification_state(
                        True,
                        ["gönderim engellendi (muhtemel doğrulama)"],
                        hidden=True,
                    )
                    QTimer.singleShot(100, self._poll_bot_protection)
                for it in rows:
                    self._dispatch_mark_error(it, error, move_to_history=False)
                self._sa_move_completed_rows_batch(rows, "error", error or "—")
                self._add_log("GÖNDERİM", "error",
                    f"❌ Gönderim hatası: {src_text} → ({tgt_text}) | {error}")

            elif result_str in ("WAITING", "SENDING"):
                # Henüz bitmedi, 200ms sonra tekrar kontrol et
                QTimer.singleShot(200, lambda: self._dispatch_poll_result(
                    item, cmd_id, src_text, tgt_text, cmd_type, attempt + 1, batch_items))

        self.browser.page().runJavaScript(check_js, on_poll)

    def _dispatch_mark_sent(self, item, *, move_to_history=True):
        """Gönderilen satırı yeşile boyar; istenirse geçmiş tabloya taşır."""
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
        if move_to_history:
            self._sa_move_completed_row_to_history(item, "sent", "Gönderildi")
        QTimer.singleShot(500, self._poll_active_village_troops)

    def _dispatch_mark_error(self, item, error_msg, *, move_to_history=True):
        """Hata satırını kırmızıya boyar; ayrıntı geçmişte «Sonuç» sütununda (ID korunur)."""
        for col in range(item.columnCount()):
            item.setBackground(col, QColor("#f0d4d4"))
            item.setForeground(col, QColor("#aa3333"))
        item.setData(0, Qt.UserRole, "error")
        hook = getattr(self, "_hybrid_on_dispatch_error", None)
        if callable(hook):
            try:
                hook(item, error_msg)
            except Exception:
                pass
        if move_to_history:
            self._sa_move_completed_row_to_history(item, "error", error_msg or "—")

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
        self.bq_village_combo.setMinimumWidth(260)
        self.bq_village_combo.setStyleSheet(TW_VILLAGE_COMBO_STYLE)
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

        q_group = QGroupBox("Bina kuyruğu — seçili köye özel (sıra: üstten alta)")
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
        self.bq_table.setColumnHidden(1, True)
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
            "Her köyün kuyruğu ayrı tutulur; köy değiştirince alttaki tablo o köye ait emirleri gösterir. "
            "Her «Ekle» seçili köyde o bina için <b>sonraki seviyeyi</b> hedefler — önce «Seviyeleri yenile»."
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
        self._bq_queues_by_vid = {}
        self._bq_current_levels = {}
        self._bq_current_in_progress = {}
        self._bq_levels_cache = {}
        self._bq_in_progress_cache = {}
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

    def _bq_block_key(self, village_id, bkey, target_level):
        try:
            tgt = int(target_level)
        except (TypeError, ValueError):
            tgt = 0
        return (str(village_id or ""), str(bkey or ""), tgt)

    def _bq_block_key_from_item(self, item, village_id=None):
        vid = village_id
        if vid is None:
            vid = str(item.data(1, Qt.UserRole) or "") or str(self._bq_get_active_village_id() or "")
        bkey = str(item.data(2, Qt.UserRole) or "")
        return self._bq_block_key(vid, bkey, (item.text(3) or "0").strip())

    def _bq_flush_table_to_store(self, village_id=None):
        """Görünen tabloyu belirtilen köyün belleğe yaz."""
        if not hasattr(self, "bq_table"):
            return
        vid = village_id
        if vid is None:
            vid = self.bq_village_combo.currentData()
        if not vid:
            return
        vs = str(vid)
        out = []
        for i in range(self.bq_table.topLevelItemCount()):
            it = self.bq_table.topLevelItem(i)
            if not it:
                continue
            out.append(
                {
                    "bname": it.text(2),
                    "bkey": str(it.data(2, Qt.UserRole) or ""),
                    "target": it.text(3),
                    "mcur": it.text(4),
                    "st": it.text(5),
                    "vlabel": self.bq_village_combo.currentText() or "—",
                }
            )
        self._bq_queues_by_vid[vs] = out

    def _bq_fill_table_from_store(self, village_id):
        """Bellekteki köy kuyruğunu tabloya yükle."""
        if not hasattr(self, "bq_table"):
            return
        vs = str(village_id or "")
        self.bq_table.clear()
        vlabel = self._bq_resolve_village_label(vs)
        for ent in self._bq_queues_by_vid.get(vs) or []:
            bkey = ent.get("bkey", "")
            tgt = str(ent.get("target", "1"))
            bname = ent.get("bname", bkey)
            mcur = ent.get("mcur", "?")
            st = ent.get("st") or "Bekliyor"
            elabel = ent.get("vlabel") or vlabel
            n = self.bq_table.topLevelItemCount() + 1
            row = QTreeWidgetItem([str(n), elabel, bname, tgt, mcur, st])
            row.setData(1, Qt.UserRole, vs)
            row.setData(2, Qt.UserRole, bkey)
            row.setTextAlignment(0, Qt.AlignCenter)
            row.setTextAlignment(2, Qt.AlignCenter)
            row.setTextAlignment(3, Qt.AlignCenter)
            self.bq_table.addTopLevelItem(row)
        self._bq_renumber()
        self._bq_update_status()

    def _bq_switch_village_queue(self, new_vid):
        """Köy değişince önceki köyün kuyruğunu kaydet, yenisininkini göster."""
        if not hasattr(self, "bq_table"):
            return
        old_vid = getattr(self, "_bq_display_vid", None)
        if old_vid is not None and str(old_vid) != str(new_vid or ""):
            self._bq_flush_table_to_store(old_vid)
        self._bq_display_vid = str(new_vid) if new_vid else None
        if new_vid:
            self._bq_fill_table_from_store(new_vid)
        else:
            self.bq_table.clear()
            self._bq_renumber()
            self._bq_update_status()

    def _bq_item_for_entry(self, village_id, entry_idx):
        if str(self.bq_village_combo.currentData() or "") != str(village_id):
            return None
        if entry_idx < 0 or entry_idx >= self.bq_table.topLevelItemCount():
            return None
        return self.bq_table.topLevelItem(entry_idx)

    def _bq_set_entry_fields(self, village_id, entry_idx, *, mcur=None, status=None, item=None):
        vs = str(village_id)
        entries = self._bq_queues_by_vid.get(vs)
        if not entries or entry_idx < 0 or entry_idx >= len(entries):
            return
        ent = entries[entry_idx]
        if mcur is not None:
            ent["mcur"] = str(mcur)
        if status is not None:
            ent["st"] = status
        if item is None:
            item = self._bq_item_for_entry(vs, entry_idx)
        if item is not None:
            if mcur is not None:
                item.setText(4, str(mcur))
            if status is not None:
                item.setText(5, status)

    def _bq_village_ids_in_order(self):
        ids = []
        seen = set()
        if hasattr(self, "bq_village_combo"):
            for i in range(self.bq_village_combo.count()):
                d = self.bq_village_combo.itemData(i)
                if d is None:
                    continue
                s = str(d)
                if s and s not in seen:
                    seen.add(s)
                    ids.append(s)
        for k in sorted(self._bq_queues_by_vid.keys()):
            if k and k not in seen:
                seen.add(k)
                ids.append(k)
        return ids

    def _bq_find_first_processable(self):
        """Her köyde sıradaki ilk işlenebilir emri bul (köyler birbirini bloklamaz)."""
        import time as _t
        now = _t.time()
        for vid in self._bq_village_ids_in_order():
            entries = self._bq_queues_by_vid.get(vid) or []
            for idx, ent in enumerate(entries):
                st = ent.get("st") or ""
                if "Tamamlandı" in st or "❌" in st:
                    continue
                bkey = ent.get("bkey", "")
                try:
                    target_level = int(ent.get("target", 0))
                except (TypeError, ValueError):
                    continue
                bk = self._bq_block_key(vid, bkey, target_level)
                unblock_at = self._bq_blocked_until.get(bk, 0)
                if unblock_at > now:
                    item = self._bq_item_for_entry(vid, idx)
                    if item:
                        remain = int(unblock_at - now)
                        mins = remain // 60
                        secs = remain % 60
                        kind = (
                            "Hammadde yetersiz"
                            if ("Hammadde" in st or "yetersiz" in st or "Kaynak" in st)
                            else "Kuyruk dolu"
                        )
                        st_new = f"⏳ {kind} — beklemede ({mins:02d}:{secs:02d} sonra)"
                        self._bq_set_entry_fields(vid, idx, status=st_new, item=item)
                        item.setForeground(5, QColor("#aa6600"))
                    break
                self._bq_blocked_until.pop(bk, None)
                return vid, idx, ent
        return None, -1, None

    def _bq_on_village_changed(self, _idx):
        if not hasattr(self, "bq_levels_table"):
            return
        vid = self.bq_village_combo.currentData()
        self._bq_switch_village_queue(vid)
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
        self._bq_flush_table_to_store()
        self._settings.setValue(
            "bina_kuyrugu/queues_by_vid_v1",
            json.dumps(self._bq_queues_by_vid, ensure_ascii=False),
        )
        self._settings.sync()

    def _bq_load_persisted_queue(self):
        if not hasattr(self, "bq_table"):
            return
        raw = (self._settings.value("bina_kuyrugu/queues_by_vid_v1", "") or "").strip()
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    self._bq_queues_by_vid = {
                        str(k): list(v) if isinstance(v, list) else []
                        for k, v in loaded.items()
                    }
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if not self._bq_queues_by_vid:
            legacy = (self._settings.value("bina_kuyrugu/queue_v1", "") or "").strip()
            if legacy:
                try:
                    data = json.loads(legacy)
                except (json.JSONDecodeError, TypeError, ValueError):
                    data = []
                if isinstance(data, list):
                    for ent in data:
                        vid = str(ent.get("vid", "") or "").strip()
                        if not vid:
                            continue
                        st0 = (ent.get("st") or "").strip()
                        if st0.startswith("Bekliyor") or st0.startswith("⏳") or "Yükseltildi" in st0 or st0 == "":
                            st = (
                                st0
                                if st0 and not st0.startswith("Bekliyor (restored")
                                else "Bekliyor (diskten)"
                            )
                        else:
                            st = st0 or "Bekliyor (diskten)"
                        self._bq_queues_by_vid.setdefault(vid, []).append(
                            {
                                "vlabel": ent.get("vlabel", "—"),
                                "bname": ent.get("bname", ent.get("bkey", "")),
                                "bkey": ent.get("bkey", ""),
                                "target": str(ent.get("target", "1")),
                                "mcur": ent.get("mcur", "?"),
                                "st": st,
                            }
                        )
        self._bq_display_vid = None
        cur = self.bq_village_combo.currentData() if hasattr(self, "bq_village_combo") else None
        if cur:
            self._bq_switch_village_queue(cur)
        total = sum(len(v) for v in self._bq_queues_by_vid.values())
        if total:
            self._add_log(
                "BİNA",
                "info",
                f"Kuyruk diskten yüklendi: {total} emir, {len(self._bq_queues_by_vid)} köy",
            )

    def _bq_pending_max_target_for_building(self, village_id, bkey) -> int:
        """Aynı köy + bina için kuyrukta (tamamlanmamış) en yüksek hedef seviye; yoksa 0."""
        vs = str(village_id)
        m = 0
        bkey = str(bkey) if bkey is not None else ""
        self._bq_flush_table_to_store(vs)
        for ent in self._bq_queues_by_vid.get(vs) or []:
            if str(ent.get("bkey", "")) != bkey:
                continue
            st = ent.get("st") or ""
            if "Tamamlandı" in st:
                continue
            try:
                t = int(str(ent.get("target", "0")).strip())
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
        vid = self.bq_village_combo.currentData()
        if not vid:
            return
        vs = str(vid)
        self.bq_table.clear()
        self._bq_queues_by_vid[vs] = []
        keys_del = [k for k in self._bq_blocked_until if k[0] == vs]
        for k in keys_del:
            del self._bq_blocked_until[k]
        self._bq_renumber()
        self._bq_update_status()
        self._bq_persist_queue()
        self._add_log("BİNA", "info", f"Bina kuyruğu temizlendi (köy: {self.bq_village_combo.currentText()})")

    def _bq_move_up(self):
        """Seçili satırı bir yukarı taşı."""
        vid = self.bq_village_combo.currentData()
        if not vid:
            return
        vs = str(vid)
        entries = self._bq_queues_by_vid.setdefault(vs, [])
        items = self.bq_table.selectedItems()
        if not items:
            return
        item = items[0]
        idx = self.bq_table.indexOfTopLevelItem(item)
        if idx <= 0:
            return
        entries[idx - 1], entries[idx] = entries[idx], entries[idx - 1]
        self.bq_table.takeTopLevelItem(idx)
        self.bq_table.insertTopLevelItem(idx - 1, item)
        self.bq_table.setCurrentItem(item)
        self._bq_renumber()
        self._bq_persist_queue()

    def _bq_move_down(self):
        """Seçili satırı bir aşağı taşı."""
        vid = self.bq_village_combo.currentData()
        if not vid:
            return
        vs = str(vid)
        entries = self._bq_queues_by_vid.setdefault(vs, [])
        items = self.bq_table.selectedItems()
        if not items:
            return
        item = items[0]
        idx = self.bq_table.indexOfTopLevelItem(item)
        if idx >= self.bq_table.topLevelItemCount() - 1:
            return
        entries[idx + 1], entries[idx] = entries[idx], entries[idx + 1]
        self.bq_table.takeTopLevelItem(idx)
        self.bq_table.insertTopLevelItem(idx + 1, item)
        self.bq_table.setCurrentItem(item)
        self._bq_renumber()
        self._bq_persist_queue()

    def _bq_delete_selected(self):
        vid = self.bq_village_combo.currentData()
        vs = str(vid) if vid else ""
        entries = self._bq_queues_by_vid.get(vs, []) if vs else []
        indices = sorted(
            {
                self.bq_table.indexOfTopLevelItem(it)
                for it in self.bq_table.selectedItems()
            },
            reverse=True,
        )
        for idx in indices:
            if idx < 0:
                continue
            item = self.bq_table.topLevelItem(idx)
            if item:
                self._bq_blocked_until.pop(self._bq_block_key_from_item(item, vs), None)
            self.bq_table.takeTopLevelItem(idx)
            if 0 <= idx < len(entries):
                entries.pop(idx)
        if vs:
            self._bq_queues_by_vid[vs] = entries
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
        total = sum(len(v) for v in self._bq_queues_by_vid.values())
        n_vill = len([k for k, v in self._bq_queues_by_vid.items() if v])
        if total > count and n_vill > 1:
            self.bq_status_label.setText(
                f"Bu köy: {count} emir  |  Tüm köyler: {total} emir ({n_vill} köy)"
            )
        else:
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
                // Aktif inşaat kuyruğunu parse et: hangi bina hangi seviyeye doğru gidiyor?
                var inProgress = {{}};
                var bqEl = doc.getElementById('buildqueue');
                if (bqEl) {{
                    var bqRows = bqEl.querySelectorAll('tr');
                    for (var bqi = 0; bqi < bqRows.length; bqi++) {{
                        var bqr = bqRows[bqi];
                        var cl = bqr.querySelector('a[href*="cancel_order"]');
                        if (!cl) continue;
                        var clh = cl.getAttribute('href') || '';
                        var bqm = clh.match(/buildingid=([^&]+)/);
                        if (!bqm) continue;
                        var bqKey = bqm[1];
                        var bqTxt = bqr.textContent;
                        var bqlm = bqTxt.match(/Level\\s+(\\d+)/i) || bqTxt.match(/Seviye\\s+(\\d+)/i);
                        if (bqlm) {{
                            var bqLv = parseInt(bqlm[1]);
                            if (!inProgress[bqKey] || bqLv > inProgress[bqKey])
                                inProgress[bqKey] = bqLv;
                        }}
                    }}
                }}
                window.__tw_bq_levels = JSON.stringify({{status: 'OK', levels: levels, imgs: imgs, in_progress: inProgress}});
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
            in_progress = data.get("in_progress", {})
            self._bq_current_levels = levels
            self._bq_current_in_progress = in_progress
            vid = getattr(self, "_bq_levels_fetch_vid", "") or ""
            if vid:
                self._bq_levels_cache[vid] = dict(levels)
                self._bq_in_progress_cache[vid] = dict(in_progress)
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
        """Seçili köyün kuyruk satırlarının durumlarını mevcut seviyelere göre güncelle."""
        combo_vid = str(self._bq_get_active_village_id() or "")
        self._bq_flush_table_to_store(combo_vid)
        entries = self._bq_queues_by_vid.get(combo_vid) or []
        for i, ent in enumerate(entries):
            item = self._bq_item_for_entry(combo_vid, i)
            bkey = ent.get("bkey", "")
            lv = self._bq_levels_cache.get(combo_vid)
            if lv is None:
                lv = self._bq_current_levels
            cur = lv.get(bkey, None)
            try:
                target = int(str(ent.get("target", "0")))
            except (TypeError, ValueError):
                continue

            if cur is not None:
                import time as _time
                ent["mcur"] = str(cur)
                if item:
                    item.setText(4, str(cur))
                ip = self._bq_in_progress_cache.get(combo_vid)
                if ip is None:
                    ip = self._bq_current_in_progress
                in_prog_level = ip.get(bkey) if ip else None
                bk = self._bq_block_key(combo_vid, bkey, target)
                if cur >= target:
                    self._bq_blocked_until.pop(bk, None)
                    st = f"✅ Tamamlandı (mevcut: {cur})"
                    ent["st"] = st
                    if item:
                        item.setText(5, st)
                        item.setForeground(5, QColor("#228822"))
                        for col in range(6):
                            item.setBackground(col, QColor("#e8f5e8"))
                elif in_prog_level is not None and in_prog_level >= target:
                    if self._bq_blocked_until.get(bk, 0) <= _time.time():
                        st = f"⏳ Oyun kuyruğunda yapılıyor (mevcut: {cur})"
                        ent["st"] = st
                        if item:
                            item.setText(5, st)
                            item.setForeground(5, QColor("#2d5a9e"))
                            for col in range(6):
                                item.setBackground(col, QColor(
                                    "#1a2a3a" if getattr(self, "_dark_mode", False) else "#e8f0ff"))
                elif self._bq_blocked_until.get(bk, 0) > _time.time():
                    pass
                else:
                    st = f"Bekliyor (mevcut: {cur})"
                    ent["st"] = st
                    if item:
                        item.setText(5, st)
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
        combo_vid = str(self.bq_village_combo.currentData() or "") if hasattr(self, "bq_village_combo") else ""
        for vid, entries in list(self._bq_queues_by_vid.items()):
            for i, ent in enumerate(entries):
                bkey = ent.get("bkey", "")
                try:
                    tgt = int(str(ent.get("target", "0")))
                except (TypeError, ValueError):
                    continue
                bk = self._bq_block_key(vid, bkey, tgt)
                unblock_at = self._bq_blocked_until.get(bk, 0)
                if unblock_at <= now:
                    self._bq_blocked_until.pop(bk, None)
                    continue
                remain = int(unblock_at - now)
                mins = remain // 60
                secs = remain % 60
                status = ent.get("st") or ""
                kind = ("Hammadde yetersiz"
                        if ("Hammadde" in status or "yetersiz" in status or "Kaynak" in status)
                        else "Kuyruk dolu")
                st_new = f"⏳ {kind} — beklemede ({mins:02d}:{secs:02d} sonra)"
                ent["st"] = st_new
                if vid == combo_vid:
                    item = self._bq_item_for_entry(vid, i)
                    if item:
                        item.setText(5, st_new)
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

        self._bq_flush_table_to_store()
        village_id, target_idx, target_ent = self._bq_find_first_processable()
        if target_ent is None or target_idx < 0:
            return

        target_item = self._bq_item_for_entry(village_id, target_idx)
        building_key = target_ent.get("bkey", "")
        try:
            target_level = int(str(target_ent.get("target", "0")))
        except (TypeError, ValueError):
            return

        self._bq_processing = True
        self._bq_set_entry_fields(
            village_id, target_idx, status="⏳ Kontrol ediliyor...", item=target_item
        )
        if target_item:
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

                // Hedef bina zaten oyunun kuyruğunda hedef seviyeye doğru inşa ediliyorsa bekle
                if (buildqueue) {{
                    var qrows = buildqueue.querySelectorAll('tr');
                    for (var qi = 0; qi < qrows.length; qi++) {{
                        var qrow = qrows[qi];
                        var cancelLink = qrow.querySelector('a[href*="cancel_order"]');
                        if (!cancelLink) continue;
                        var chref = cancelLink.getAttribute('href') || '';
                        var bm = chref.match(/buildingid=([^&]+)/);
                        if (!bm || bm[1] !== buildingKey) continue;
                        // Bu bina kuyruğunda — hedef seviyeyi metinden çek
                        var rowTxt = qrow.textContent;
                        var lm = rowTxt.match(/Level\\s+(\\d+)/i) || rowTxt.match(/Seviye\\s+(\\d+)/i);
                        var qTargetLv = lm ? parseInt(lm[1]) : 0;
                        if (qTargetLv === targetLevel) {{
                            var timerEl = qrow.querySelector('[data-endtime]');
                            var finishSec = timerEl ? (parseInt(timerEl.getAttribute('data-endtime')) || 0) : 0;
                            var remainBq = finishSec > nowSec ? finishSec - nowSec : 90;
                            window.__tw_bq_result = 'ALREADY_BUILDING|' + currentLevel + '|' + remainBq;
                            return;
                        }}
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
                    cur.startsWith('BUSY') || cur.startsWith('NO_RES') ||
                    cur.startsWith('ALREADY_BUILDING')) return;

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
        self._bq_poll_result(
            village_id, target_item, target_idx, building_key, target_level, 0
        )

    def _bq_poll_result(
        self, village_id, item, row_idx, building_key, target_level, attempt
    ):
        """Yükseltme sonucunu polling ile kontrol et."""
        rv = str(village_id or "")

        if attempt > 60:
            self._bq_set_entry_fields(rv, row_idx, status="Zaman aşımı", item=item)
            if item:
                item.setForeground(5, QColor("#cc2222"))
            self._bq_persist_queue()
            self._bq_processing = False
            return

        check_js = "window.__tw_bq_result || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"
            blk = self._bq_block_key(rv, building_key, target_level)

            def _poll_again():
                self._bq_poll_result(
                    rv, item, row_idx, building_key, target_level, attempt + 1
                )

            if result_str in ("WAITING", "CHECKING"):
                QTimer.singleShot(300, _poll_again)
                return

            if result_str.startswith("UPGRADED|"):
                self._bq_blocked_until.pop(blk, None)
                cur_level = result_str.split("|")[1]
                new_level = int(cur_level) + 1
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

                if new_level >= target_level:
                    st = f"✅ Tamamlandı (mevcut: {new_level})"
                    self._add_log("BİNA", "success",
                        f"✅ {building_key} → Seviye {new_level} — hedef ulaşıldı")
                else:
                    st = f"Yükseltildi → {new_level} (hedef: {target_level})"
                    self._add_log("BİNA", "success",
                        f"✅ {building_key} → Seviye {new_level} (hedef: {target_level})")
                self._bq_set_entry_fields(rv, row_idx, mcur=str(new_level), status=st, item=item)
                if item and new_level >= target_level:
                    item.setForeground(5, QColor("#228822"))
                    for col in range(item.columnCount()):
                        item.setBackground(col, QColor("#e8f5e8"))
                elif item:
                    item.setForeground(5, QColor("#2d5a9e"))

                self._bq_persist_queue()
                self._bq_processing = False
                self._bq_schedule_build_wake(2000)

            elif result_str.startswith("DONE|"):
                self._bq_blocked_until.pop(blk, None)
                cur_level = result_str.split("|")[1]
                il = int(cur_level)
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
                st = f"✅ Tamamlandı (mevcut: {cur_level})"
                self._bq_set_entry_fields(rv, row_idx, mcur=cur_level, status=st, item=item)
                if item:
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

                if remain_sec > 0:
                    wait_sec = remain_sec + 2
                    self._bq_blocked_until[blk] = _t3.time() + wait_sec
                    mins = remain_sec // 60
                    secs = remain_sec % 60
                    st = f"⏳ Kuyruk dolu ({queue_count}) — beklemede ({mins:02d}:{secs:02d} sonra)"
                    self._add_log("BİNA", "info",
                        f"İnşaat kuyruğu dolu ({queue_count}) — slot açılışına ~{mins}dk {secs}sn; "
                        f"o zamana kadar sıra atlanmayacak.")
                    self._bq_schedule_build_wake(wait_sec * 1000 + 1500)
                else:
                    wait_sec = 20
                    self._bq_blocked_until[blk] = _t3.time() + wait_sec
                    st = f"⏳ Kuyruk dolu ({queue_count}) — beklemede (süre okunamadı)"
                    self._add_log("BİNA", "info", "Kuyruk dolu; [data-endtime] yok — 20 sn sonra yine dene")
                    self._bq_schedule_build_wake(20000)
                self._bq_set_entry_fields(rv, row_idx, mcur=str(cur_level), status=st, item=item)
                if item:
                    item.setForeground(5, QColor("#aa6600"))
                self._bq_persist_queue()
                self._bq_processing = False

            elif result_str.startswith("ALREADY_BUILDING|"):
                import time as _tab
                parts = result_str.split("|")
                cur_level = parts[1] if len(parts) > 1 else "?"
                remain_sec = int(parts[2]) if len(parts) > 2 else 90
                wait_sec = max(remain_sec + 5, 30)
                self._bq_blocked_until[blk] = _tab.time() + wait_sec
                mins = remain_sec // 60
                secs = remain_sec % 60
                st = f"⏳ Oyun kuyruğunda yapılıyor — {mins:02d}:{secs:02d} sonra tamamlanır"
                self._bq_set_entry_fields(rv, row_idx, mcur=str(cur_level), status=st, item=item)
                if item:
                    item.setForeground(5, QColor("#2d5a9e"))
                    for col in range(item.columnCount()):
                        item.setBackground(col, QColor(
                            "#1a2a3a" if getattr(self, "_dark_mode", False) else "#e8f0ff"))
                self._add_log("BİNA", "info",
                    f"{building_key} → seviye {target_level} zaten oyun kuyruğunda yapılıyor; "
                    f"~{mins}dk {secs}sn sonra tekrar kontrol edilecek")
                self._bq_persist_queue()
                self._bq_processing = False
                self._bq_schedule_build_wake(wait_sec * 1000 + 500)

            elif result_str.startswith("NO_RES|"):
                import random as _rnd, time as _t2
                parts = result_str.split("|")
                cur_level = parts[1] if len(parts) > 1 else "?"

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

                wait_sec = _rnd.randint(540, 660)
                self._bq_blocked_until[blk] = _t2.time() + wait_sec
                mins = wait_sec // 60
                secs = wait_sec % 60
                st = f"⏳ Hammadde yetersiz — beklemede ({mins:02d}:{secs:02d} sonra)"
                self._bq_set_entry_fields(rv, row_idx, mcur=str(cur_level), status=st, item=item)
                if item:
                    item.setForeground(5, QColor("#aa6600"))

                log_detail = f" | {detail}" if detail else ""
                self._add_log("BİNA", "info",
                    f"Hammadde yetersiz: {building_key}{log_detail} — "
                    f"~10 dk sonra ({mins}dk {secs}sn) yeniden kontrol; sıra atlanmıyor")

                self._bq_persist_queue()
                self._bq_processing = False
                self._bq_schedule_build_wake(wait_sec * 1000 + 500)

            elif result_str.startswith("UPGRADING|"):
                QTimer.singleShot(300, _poll_again)
                return

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                st = f"❌ Hata: {error[:40]}"
                self._bq_set_entry_fields(rv, row_idx, status=st, item=item)
                if item:
                    item.setForeground(5, QColor("#cc2222"))
                self._add_log("BİNA", "error", f"❌ {building_key}: {error}")
                self._bq_persist_queue()
                self._bq_processing = False

            else:
                QTimer.singleShot(300, _poll_again)
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
        self._map_diplomacy_defer = QTimer(self)
        self._map_diplomacy_defer.setSingleShot(True)
        self._map_diplomacy_defer.timeout.connect(self._map_fetch_diplomacy)

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
        barb_layout.setSpacing(4)

        # Mesafe filtresi satırı
        barb_filter_row = QHBoxLayout()
        barb_filter_row.addWidget(QLabel("Maks mesafe:"))
        self.map_barb_radius = QSpinBox()
        self.map_barb_radius.setRange(1, 999)
        self.map_barb_radius.setValue(20)
        self.map_barb_radius.setFixedWidth(72)
        self.map_barb_radius.setToolTip(
            "Merkeze en fazla bu kadar kare uzaklıktaki barbarlar listelenir (satır sayısı değil). "
            "Yoğun bölgelerde çok köy varsa en yakınlar önce listelenir; tablo en fazla "
            f"{self.MAP_BARB_LIST_MAX_ROWS} satır gösterir."
        )
        self.map_barb_radius.valueChanged.connect(self._map_on_barb_radius_changed)
        barb_filter_row.addWidget(self.map_barb_radius)
        barb_filter_btn = QPushButton("Filtrele")
        barb_filter_btn.setFixedWidth(70)
        barb_filter_btn.clicked.connect(self._map_refresh_barb_table)
        barb_filter_row.addWidget(barb_filter_btn)
        barb_filter_row.addStretch()
        barb_layout.addLayout(barb_filter_row)

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
        self.farm_max_dist.setRange(1, 999)
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

        farm_row1b = QHBoxLayout()
        farm_row1b.setSpacing(6)
        farm_row1b.addWidget(QLabel("Mod:"))
        self.farm_la_mode = QComboBox()
        self.farm_la_mode.addItems(["Tek şablon", "A + B (sıralı)"])
        self.farm_la_mode.setFixedWidth(140)
        self.farm_la_mode.setToolTip(
            "Çift mod: önce seçilen şablon en yakın köylere, ardından diğer şablon "
            "kalan yakın köylere atanır."
        )
        self.farm_la_mode.currentIndexChanged.connect(self._farm_sync_la_mode_ui)
        farm_row1b.addWidget(self.farm_la_mode)

        farm_row1b.addSpacing(6)
        farm_row1b.addWidget(QLabel("Şablon:"))
        self.farm_la_template = QComboBox()
        self.farm_la_template.addItems(["Şablon A", "Şablon B"])
        self.farm_la_template.setFixedWidth(110)
        self.farm_la_template.setToolTip(
            "Yağma Asistanındaki A/B şablonları kullanılır (yağma saldırısı).\n"
            "Birlik sayıları oyundaki Yağma Asistanı → Şablonlar ekranından gelir."
        )
        farm_row1b.addWidget(self.farm_la_template)

        farm_row1b.addSpacing(6)
        self.farm_la_order = QComboBox()
        self.farm_la_order.addItems(["Önce B, sonra A", "Önce A, sonra B"])
        self.farm_la_order.setFixedWidth(140)
        self.farm_la_order.setToolTip("Çift modda hangi şablon grubunun önce gönderileceği.")
        self.farm_la_order.setVisible(False)
        farm_row1b.addWidget(self.farm_la_order)

        self.farm_la_use_a = QCheckBox("A")
        self.farm_la_use_a.setChecked(True)
        self.farm_la_use_a.setVisible(False)
        self.farm_la_use_a.stateChanged.connect(self._farm_update_la_preview)
        farm_row1b.addWidget(self.farm_la_use_a)

        self.farm_la_use_b = QCheckBox("B")
        self.farm_la_use_b.setChecked(True)
        self.farm_la_use_b.setVisible(False)
        self.farm_la_use_b.stateChanged.connect(self._farm_update_la_preview)
        farm_row1b.addWidget(self.farm_la_use_b)

        self.farm_la_hint_label = QLabel(
            "Premium Yağma Asistanı gerekir — birlikler şablondan gönderilir"
        )
        self.farm_la_hint_label.setStyleSheet("font-size: 10px; color: #666;")
        farm_row1b.addWidget(self.farm_la_hint_label)
        farm_row1b.addStretch()
        farm_layout.addLayout(farm_row1b)

        # Satır 2: Birim seçimi (bilgi — LA şablonu kullanılır)
        farm_row2 = QHBoxLayout()
        farm_row2.setSpacing(2)

        farm_row2.addWidget(QLabel("Şablon birlikleri (bilgi):"))
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
            spin.setEnabled(False)
            spin.setToolTip("Yağma Asistanı şablonu kullanılır; değerler şablondan okunur.")
            farm_row2.addWidget(spin)
            self.farm_troop_inputs[key] = spin

        farm_row2.addStretch()
        farm_layout.addLayout(farm_row2)

        # Satır 3: LA şablon önizleme
        farm_row3 = QHBoxLayout()
        farm_row3.setSpacing(6)
        farm_row3.addWidget(QLabel("Şablon önizleme:"))

        self.farm_la_preview_label = QLabel("—")
        self.farm_la_preview_label.setStyleSheet("font-size: 10px; color: #555;")
        farm_row3.addWidget(self.farm_la_preview_label)
        self.farm_la_template.currentIndexChanged.connect(self._farm_update_la_preview)
        self.farm_la_mode.currentIndexChanged.connect(self._farm_update_la_preview)

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
        self.farm_round_wait_time.setToolTip(
            "Tur arası gerçek bekleme: bu süre ±60 saniye rastgele; en az 10 saniye."
        )
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
        self._farm_report_scan_blocking_farm = False
        self._farm_la_template_a: int | None = None
        self._farm_la_template_b: int | None = None
        self._farm_la_template_troops: dict = {}
        self._farm_la_templates_fetching = False
        self._farm_tpl_assignments: dict = {}
        self._farm_active_phase = "B"
        self._farm_sent_count_a = 0
        self._farm_sent_count_b = 0
        self._farm_waiting_returns = False
        self._farm_fallback_log_at = 0.0
        self._farm_fallback_log_pair = ""
        # LA koordinat kuyruğu: giden yağma + plunder_list senkronu
        self._farm_outgoing: dict = {}
        self._farm_outgoing_coords: set = set()
        self._farm_la_attackable: dict = {"A": set(), "B": set()}
        self._farm_la_all_vids: set = set()
        self._farm_la_villages: dict = {}
        self._farm_assign_pools: dict = {"A": [], "B": []}
        self._farm_sabit_sent: dict = {"A": set(), "B": set()}
        self._farm_la_sync_log_snapshot = None
        self._farm_la_sync_ts = 0.0
        self._farm_la_sync_last = 0.0
        self._farm_la_sync_inflight = False
        self._farm_place_sync_ts = 0.0
        self._farm_place_sync_last = 0.0
        self._farm_place_sync_inflight = False
        self._farm_place_outbound: set = set()
        self._farm_place_returning: set = set()
        self._farm_la_sync_ready = False

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
        if attempt > 50:  # 50 × 400ms ≈ 20sn
            self.map_load_btn.setEnabled(True)
            self.map_load_btn.setText("🗺️ Haritayı Yükle")
            self._add_log("HARİTA", "error", "Zaman aşımı.")
            return

        check_js = "window.__tw_map_data || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._map_poll_load(attempt + 1))
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
            self._map_diplomacy_defer.stop()
            self._map_diplomacy_defer.start(5000)

            # Temizle
            self.browser.page().runJavaScript("window.__tw_map_data = null;")

        self.browser.page().runJavaScript(check_js, on_poll)

    def _map_on_barb_radius_changed(self, _value: int = 0) -> None:
        """Maks mesafe değişince tabloyu yenile (harita verisi yüklüyse)."""
        if getattr(self, "_map_data_loaded", False):
            self._map_refresh_barb_table()

    def _map_refresh_barb_table(self):
        """Sadece barbar tablosunu yenile (haritayı yeniden çizmeden)."""
        if not self._map_data_loaded or not self._map_villages:
            return
        import math, time as _brt2
        cx = self.map_center_x.value()
        cy = self.map_center_y.value()
        barb_radius = getattr(self, "map_barb_radius", None)
        barb_r = int(barb_radius.value()) if barb_radius is not None else 20

        barb_list = []
        for v in self._map_villages:
            vx = v.get("x")
            vy = v.get("y")
            if vx is None or vy is None:
                continue
            if int(v.get("player_id", 0) or 0) != 0:
                continue
            dx = vx - cx
            dy = vy - cy
            dist = math.sqrt(dx * dx + dy * dy)
            if dist <= barb_r:
                barb_list.append(v | {"dist": dist})

        barb_list.sort(key=lambda b: b["dist"])
        self.map_barb_table.clear()
        active = getattr(self, "_farm_active_coords", {})
        _now2 = _brt2.time()
        for b in barb_list[: self.MAP_BARB_LIST_MAX_ROWS]:
            coord = f"({b['x']}|{b['y']})"
            coord_key = (int(b["x"]), int(b["y"]))
            exp = active.get(coord_key, 0)
            status = "✓ Gönderildi" if exp > _now2 else ""
            item = QTreeWidgetItem([coord, str(b["points"]), f"{b['dist']:.1f}", b["name"], status])
            item.setTextAlignment(1, Qt.AlignCenter)
            item.setTextAlignment(2, Qt.AlignCenter)
            item.setData(0, Qt.UserRole, {
                "id": b.get("id", 0),
                "x": b["x"],
                "y": b["y"],
            })
            if status:
                item.setForeground(4, QColor("#228822"))
            self.map_barb_table.addTopLevelItem(item)
        self._farm_barb_index = 0
        self._farm_sent_count = 0
        self._farm_update_labels()

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

            # Barbar tablosu için mesafe hesapla (barb spinbox'ına göre)
            if is_barb:
                dx = vx - cx
                dy = vy - cy
                dist = math.sqrt(dx * dx + dy * dy)
                barb_radius = getattr(self, "map_barb_radius", None)
                barb_r = int(barb_radius.value()) if barb_radius is not None else radius
                if dist <= barb_r:
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
        for b in barb_list[: self.MAP_BARB_LIST_MAX_ROWS]:
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
            item.setData(0, Qt.UserRole, {
                "id": b.get("id", 0),
                "x": b["x"],
                "y": b["y"],
            })
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

        troop_values = self._sa_queue_format_troop_values(entry.get("troops") or {})
        has_troops = any(int(v) > 0 for v in troop_values)
        troop_keys_sent = [k for k in SA_QUEUE_TABLE_TROOP_KEYS if int((entry.get("troops") or {}).get(k, 0) or 0) > 0]

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
        troops_map = entry.get("troops") or {}
        cmd_attack = entry.get("cmd_type", "Sld") != "Dst"
        travel_sec = self._sa_calc_travel_time(
            distance, troop_keys_sent, troops_map=troops_map, cmd_attack=cmd_attack
        )

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
        item.setData(0, self.SA_QUEUE_ITEM_ROLE_TIME_MODE, active_mode or "send")

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

    def _farm_is_sabit_round_wait(self) -> bool:
        """True = tur arası «Sabit süre» (combo ilk seçenek)."""
        cb = getattr(self, "farm_round_wait_mode", None)
        return cb is None or cb.currentIndex() == 0

    def _farm_clear_for_new_round(self) -> None:
        """Yeni tur: durum sütununu temizle (kara liste hariç), sayaçları sıfırla."""
        dark = getattr(self, "_dark_mode", False)
        fg = QColor("#ffffff" if dark else "#000000")
        for i in range(self.map_barb_table.topLevelItemCount()):
            it = self.map_barb_table.topLevelItem(i)
            if not it or it.text(4) == "⛔ Kara liste":
                continue
            it.setText(4, "")
            it.setForeground(4, fg)
            vd = dict(it.data(0, Qt.UserRole) or {})
            vd.pop("assigned_tpl", None)
            it.setData(0, Qt.UserRole, vd)
        self._farm_barb_index = 0
        self._farm_sent_count = 0
        self._farm_sent_count_a = 0
        self._farm_sent_count_b = 0
        self._farm_tpl_assignments = {}
        self._farm_outgoing = {}
        self._farm_outgoing_coords = set()
        self._farm_assign_pools = {"A": [], "B": []}
        self._farm_sabit_sent = {"A": set(), "B": set()}
        if self._farm_is_dual_mode():
            self._farm_compute_template_assignments()
        self._farm_update_labels()

    def _farm_finish_round_sabit_sure(self, reason: str) -> None:
        """Sabit süre modu: tur biter; bekleme = kullanıcı süresi + [-60, +60] sn (en az 10 sn)."""
        import time
        self._farm_clear_for_new_round()
        self._farm_round_return_times.clear()
        base = int(self.farm_round_wait_time.value())
        wait_sec = max(10, base + random.randint(-60, 60))
        self._farm_round_wait_until = time.time() + wait_sec
        self._farm_round_waiting = True
        self._farm_last_send = 0
        mins, secs = divmod(wait_sec, 60)
        self.farm_status_label.setText(
            f"Durum: Tur bitti, {mins}dk {secs}sn bekleniyor (~±1 dk rastgele)...")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
        self._add_log(
            "FARM", "info",
            f"⏸ {reason} — {mins}dk {secs}sn bekleme (taban {base}s, ±60s); yeni tur liste başından",
        )

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

    def _farm_la_feature_active(self, fa) -> bool:
        """FarmAssistent / FarmAssistant active bayrağı."""
        if not fa or not isinstance(fa, dict):
            return False
        active = fa.get("active")
        if active in (True, 1, "1", "true", "True"):
            return True
        return bool(active)

    def _farm_has_la_premium(self) -> bool:
        """Yağma Asistanı premium aktif mi? (bot cache)"""
        gd = self._game_data or {}
        features = gd.get("features") or {}
        for key in ("FarmAssistent", "FarmAssistant", "farm_assistent", "farm_assistant"):
            if self._farm_la_feature_active(features.get(key)):
                return True
        prem = gd.get("premium") or {}
        if isinstance(prem, dict) and prem.get("farm_assistant"):
            return True
        if self._farm_la_template_a:
            return True
        return False

    def _farm_check_la_premium_live(self, callback) -> None:
        """Tarayıcıdaki game_data.features üzerinden canlı premium kontrolü."""
        if not getattr(self, "browser", None):
            callback(False, "Tarayıcı hazır değil.")
            return
        check_js = """
        (function() {
            if (typeof game_data === 'undefined' || !game_data) {
                return JSON.stringify({status: 'NO_GAME_DATA'});
            }
            var f = game_data.features || {};
            var fa = f.FarmAssistent || f.FarmAssistant || {};
            return JSON.stringify({
                status: 'OK',
                active: !!(fa && fa.active),
                key: f.FarmAssistent ? 'FarmAssistent' : (f.FarmAssistant ? 'FarmAssistant' : '')
            });
        })();
        """

        def on_result(result) -> None:
            try:
                import json
                data = json.loads(str(result or "{}"))
            except Exception:
                callback(None, "")
                return
            if data.get("status") != "OK":
                callback(None, "")
                return
            active = bool(data.get("active"))
            if active and data.get("key"):
                self._game_data.setdefault("features", {})[data["key"]] = {"active": True}
            callback(active, "")

        self.browser.page().runJavaScript(check_js, on_result)

    def _farm_is_dual_mode(self) -> bool:
        """A+B sıralı çift şablon modu aktif mi?"""
        cb = getattr(self, "farm_la_mode", None)
        return cb is not None and cb.currentIndex() == 1

    def _farm_sync_la_mode_ui(self, _index: int = 0) -> None:
        """Tek/çift moda göre kontrolleri göster/gizle."""
        dual = self._farm_is_dual_mode()
        self.farm_la_template.setVisible(not dual)
        self.farm_la_order.setVisible(dual)
        self.farm_la_use_a.setVisible(dual)
        self.farm_la_use_b.setVisible(dual)
        self._farm_update_la_preview()

    def _farm_template_enabled(self, key: str) -> bool:
        if not self._farm_is_dual_mode():
            sel = "A" if self.farm_la_template.currentIndex() == 0 else "B"
            return key == sel
        if key == "A":
            return self.farm_la_use_a.isChecked()
        return self.farm_la_use_b.isChecked()

    def _farm_get_template_id_by_key(self, key: str) -> int | None:
        if key == "A":
            return self._farm_la_template_a
        return self._farm_la_template_b

    def _farm_get_template_troops_by_key(self, key: str) -> dict | None:
        """A veya B şablonunun birlik dict'i."""
        tid = self._farm_get_template_id_by_key(key)
        if not tid:
            return None
        raw = self._farm_la_template_troops.get(str(tid))
        if raw is None:
            raw = self._farm_la_template_troops.get(tid)
        if not raw:
            return None
        troops = {k: int(v) for k, v in raw.items() if int(v) > 0}
        return troops if troops else None

    def _farm_template_travel_seconds(self, key: str, distance: float = 1.0) -> float:
        """Şablonun referans mesafedeki yolculuk süresi (yüksek = yavaş)."""
        troops = self._farm_get_template_troops_by_key(key)
        if not troops:
            return 0.0
        return float(self._sa_calc_travel_time(distance, list(troops.keys())))

    def _farm_slow_template_key(self) -> str:
        """Yolculuk süresine göre yavaş şablon (A veya B)."""
        ta = self._farm_template_travel_seconds("A")
        tb = self._farm_template_travel_seconds("B")
        if tb > ta:
            return "B"
        if ta > tb:
            return "A"
        return "B"

    def _farm_fast_template_key(self) -> str:
        slow = self._farm_slow_template_key()
        return "A" if slow == "B" else "B"

    def _farm_max_sends_for_template(self, key: str) -> int:
        """Köydeki birliklerle bu şablondan kaç saldırı yapılabilir."""
        troops = self._farm_get_template_troops_by_key(key)
        if not troops:
            return 0
        available = self._game_data.get("troops", {}) or {}
        limits = []
        for unit, need in troops.items():
            if need > 0:
                limits.append(int(available.get(unit, 0) or 0) // int(need))
        return min(limits) if limits else 0

    def _farm_enabled_template_keys(self) -> list:
        """Aktif checkbox'lara göre kullanılabilir şablon anahtarları."""
        return [k for k in ("A", "B") if self._farm_template_enabled(k)]

    def _farm_can_send_template(self, key: str) -> bool:
        """Şablonda asker var, köyde yeterli ve bekleyen atanan köy var mı?"""
        if not self._farm_template_enabled(key):
            return False
        troops = self._farm_get_template_troops_by_key(key)
        if not troops or not self._farm_has_enough_troops(troops):
            return False
        if self._farm_count_pending_for_template(key) == 0:
            return False
        return bool(self._farm_get_template_id_by_key(key))

    def _farm_deduct_sent_troops(self, troops: dict) -> None:
        """Başarılı yağmadan sonra köy stoğundan gönderilen birlikleri düş."""
        if not troops:
            return
        gd = self._game_data.setdefault("troops", {})
        for unit, count in troops.items():
            try:
                cur = int(gd.get(unit, 0) or 0)
                need = int(count)
            except (TypeError, ValueError):
                continue
            gd[unit] = max(0, cur - need)

    def _farm_log_template_fallback(self, from_key: str, to_key: str) -> None:
        """Şablon yedek geçiş logunu spam etme (en fazla 15 sn'de bir)."""
        import time
        pair = f"{from_key}->{to_key}"
        now = time.time()
        if (
            pair == getattr(self, "_farm_fallback_log_pair", "")
            and now - getattr(self, "_farm_fallback_log_at", 0) < 15
        ):
            return
        self._farm_fallback_log_pair = pair
        self._farm_fallback_log_at = now
        self._add_log(
            "FARM", "info",
            f"↪ Şablon {from_key} asker yok — {to_key} deneniyor")

    def _farm_begin_wait_for_returns(self, reason: str = "") -> None:
        """En yakın dönüş modu: asker kalmadı, dönüş zamanı bekle."""
        import time
        if getattr(self, "_farm_waiting_returns", False):
            return
        self._farm_waiting_returns = True
        msg = reason or "Gönderilecek asker kalmadı — dönüş bekleniyor"
        self._add_log("FARM", "info", f"⏸ {msg}")
        self.farm_status_label.setText(f"Durum: {msg}")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc8800;")
        self._farm_sync_la_plunder_list()
        self._farm_sync_place_commands()
        self._farm_fetch_return_times()

    def _farm_table_item_for_vid(self, vid: int):
        vid = int(vid)
        for i in range(self.map_barb_table.topLevelItemCount()):
            item = self.map_barb_table.topLevelItem(i)
            if not item:
                continue
            vd = item.data(0, Qt.UserRole) or {}
            if int(vd.get("id") or 0) == vid:
                return item
        return None

    def _farm_skip_outbound_vid(self, vid: int, x=None, y=None) -> bool:
        """Giden yağma veya place attack — aday havuzundan çıkar."""
        vid = int(vid)
        if vid in self._farm_outgoing:
            return True
        outbound = getattr(self, "_farm_place_outbound", set())
        returning = getattr(self, "_farm_place_returning", set())
        if vid in outbound and vid not in returning:
            return True
        if x is not None and y is not None:
            if (int(x), int(y)) in getattr(self, "_farm_outgoing_coords", set()):
                return True
        return False

    def _farm_mark_outgoing(
            self, vid: int, x: int, y: int, tpl_key: str, tpl_id: int) -> None:
        import time
        vid = int(vid)
        x, y = int(x), int(y)
        self._farm_outgoing[vid] = {
            "x": x, "y": y,
            "tpl_key": tpl_key, "tpl_id": int(tpl_id or 0),
            "since": time.time(),
        }
        if not hasattr(self, "_farm_outgoing_coords"):
            self._farm_outgoing_coords = set()
        self._farm_outgoing_coords.add((x, y))
        item = self._farm_table_item_for_vid(vid)
        if item:
            lbl = f"Yolda {tpl_key}" if tpl_key else "Yolda"
            item.setText(4, lbl)
            item.setForeground(4, QColor("#2d5a9e"))

    def _farm_release_village(self, vid: int) -> None:
        vid = int(vid)
        out = self._farm_outgoing.pop(vid, None)
        if out and hasattr(self, "_farm_outgoing_coords"):
            self._farm_outgoing_coords.discard(
                (int(out.get("x", 0)), int(out.get("y", 0))))
        self._farm_place_outbound.discard(vid)
        item = self._farm_table_item_for_vid(vid)
        if item:
            st = (item.text(4) or "").strip()
            if st.startswith("Yolda") or st.startswith("✓"):
                dark = getattr(self, "_dark_mode", False)
                item.setText(4, "")
                item.setForeground(4, QColor("#ffffff" if dark else "#000000"))

    def _farm_maybe_release_expired(self, vid: int) -> bool:
        """Sync başarısızsa 2 bacak yolculuk sonrası outgoing serbest bırak."""
        out = self._farm_outgoing.get(int(vid))
        if not out:
            return False
        vid = int(vid)
        if vid in getattr(self, "_farm_place_outbound", set()):
            if vid not in getattr(self, "_farm_place_returning", set()):
                return False
        import math
        import time
        since = out.get("since", 0)
        x, y = out.get("x", 0), out.get("y", 0)
        tpl_key = out.get("tpl_key", "A")
        troops = self._farm_get_template_troops_by_key(tpl_key) or {}
        v = self._game_data.get("village", {})
        dist = math.sqrt(
            (x - v.get("x", 0)) ** 2 + (y - v.get("y", 0)) ** 2)
        travel = self._sa_calc_travel_time(dist, list(troops.keys()))
        if time.time() - since > max(travel * 2, 120) + 30:
            self._farm_release_village(vid)
            return True
        return False

    def _farm_is_village_attackable(self, vid: int, tpl_key: str) -> bool:
        """LA plunder_list + place sync: hedef şablon için saldırılabilir mi?"""
        vid = int(vid)
        tpl_key = tpl_key or "A"

        if vid in getattr(self, "_farm_place_returning", set()):
            self._farm_release_village(vid)

        self._farm_maybe_release_expired(vid)

        if vid in self._farm_outgoing:
            return False

        meta = getattr(self, "_farm_la_villages", {}).get(vid)
        if meta:
            if (int(meta["x"]), int(meta["y"])) in getattr(
                    self, "_farm_outgoing_coords", set()):
                return False

        outbound = getattr(self, "_farm_place_outbound", set())
        returning = getattr(self, "_farm_place_returning", set())
        if vid in outbound and vid not in returning:
            return False

        if self._farm_is_sabit_round_wait() and self._farm_is_sabit_sent(vid, tpl_key):
            return False

        if self._farm_la_sync_ts > 0:
            all_vids = getattr(self, "_farm_la_all_vids", set())
            if vid not in all_vids:
                return False
            return vid in self._farm_la_attackable.get(tpl_key, set())

        return True

    def _farm_apply_la_sync_result(self, data: dict) -> None:
        import math
        import time
        if data.get("status") != "OK":
            msg = data.get("message", "bilinmeyen hata")
            self._add_log("FARM", "warn", f"LA plunder sync: {msg[:80]}")
            return

        self._farm_la_attackable = {
            "A": set(int(v) for v in data.get("A", [])),
            "B": set(int(v) for v in data.get("B", [])),
        }
        self._farm_la_all_vids = set(int(v) for v in data.get("allVids", []))

        v = self._game_data.get("village", {})
        src_x = int(v.get("x", 0) or 0)
        src_y = int(v.get("y", 0) or 0)
        self._farm_la_villages = {}
        for vill in data.get("villages", []):
            try:
                vid = int(vill.get("vid", 0))
                x = int(vill.get("x", 0))
                y = int(vill.get("y", 0))
            except (TypeError, ValueError):
                continue
            if not vid:
                continue
            dist = math.sqrt((x - src_x) ** 2 + (y - src_y) ** 2)
            self._farm_la_villages[vid] = {"x": x, "y": y, "dist": dist}

        self._farm_la_sync_ts = time.time()
        self._farm_la_sync_ready = True
        n_a = len(self._farm_la_attackable["A"])
        n_b = len(self._farm_la_attackable["B"])
        snapshot = (n_a, n_b)
        if snapshot != getattr(self, "_farm_la_sync_log_snapshot", None):
            self._farm_la_sync_log_snapshot = snapshot
            self._add_log(
                "FARM", "info",
                f"LA plunder sync: A={n_a} B={n_b} saldırılabilir köy")

        for vid in list(self._farm_outgoing.keys()):
            if vid in getattr(self, "_farm_place_returning", set()):
                self._farm_release_village(vid)
                continue
            if vid in getattr(self, "_farm_place_outbound", set()):
                continue
            out = self._farm_outgoing.get(vid) or {}
            key = out.get("tpl_key", "A")
            if vid in self._farm_la_attackable.get(key, set()):
                if vid in self._farm_la_all_vids:
                    self._farm_release_village(vid)

        if self.farm_enable_cb.isChecked():
            self._farm_compute_template_assignments()

    def _farm_village_data_for_vid(self, vid: int) -> dict | None:
        vid = int(vid)
        meta = getattr(self, "_farm_la_villages", {}).get(vid)
        if meta:
            return {"id": vid, "x": meta["x"], "y": meta["y"]}
        item = self._farm_table_item_for_vid(vid)
        if item:
            vd = dict(item.data(0, Qt.UserRole) or {})
            if int(vd.get("id") or 0) == vid:
                return vd
        return None

    def _farm_build_la_candidates(self, tpl_key: str) -> list:
        """LA plunder_list birincil; barbar tablosu yalnızca LA geçmişinde olmayan köyler için."""
        import math
        max_dist = self.farm_max_dist.value()
        v = self._game_data.get("village", {})
        src_x = int(v.get("x", 0) or 0)
        src_y = int(v.get("y", 0) or 0)
        tpl_key = tpl_key or "A"
        candidates = []
        seen: set = set()

        la_vids = getattr(self, "_farm_la_villages", {}) or {}
        attackable = self._farm_la_attackable.get(tpl_key, set())
        for vid in attackable:
            vid = int(vid)
            meta = la_vids.get(vid)
            if not meta:
                continue
            x, y = int(meta["x"]), int(meta["y"])
            if self._farm_skip_outbound_vid(vid, x, y):
                continue
            dist = float(meta.get("dist", 0))
            coord_key = f"{x}|{y}"
            if coord_key in self._farm_blacklist:
                continue
            if dist > max_dist:
                continue
            if not self._farm_is_village_attackable(vid, tpl_key):
                continue
            vd = {"id": vid, "x": x, "y": y}
            candidates.append((dist, vid, vd))
            seen.add(vid)

        all_la = getattr(self, "_farm_la_all_vids", set())
        for i in range(self.map_barb_table.topLevelItemCount()):
            item = self.map_barb_table.topLevelItem(i)
            if not item:
                continue
            vd = dict(item.data(0, Qt.UserRole) or {})
            vid = int(vd.get("id") or 0)
            if not vid or vid in seen:
                continue
            if self._farm_skip_outbound_vid(vid, vd.get("x"), vd.get("y")):
                continue
            if self._farm_la_sync_ts > 0 and vid in all_la:
                continue
            coord_key = item.text(0).strip("()")
            if coord_key in self._farm_blacklist:
                continue
            try:
                dist = float(item.text(2))
            except (TypeError, ValueError):
                x, y = int(vd.get("x", 0)), int(vd.get("y", 0))
                dist = math.sqrt((x - src_x) ** 2 + (y - src_y) ** 2)
            if dist > max_dist:
                continue
            if not self._farm_is_village_attackable(vid, tpl_key):
                continue
            candidates.append((dist, vid, vd))
            seen.add(vid)

        candidates.sort(key=lambda c: c[0])
        return candidates

    def _farm_is_sabit_sent(self, vid: int, tpl_key: str) -> bool:
        return int(vid) in getattr(self, "_farm_sabit_sent", {}).get(tpl_key, set())

    def _farm_mark_sabit_sent(self, vid: int, tpl_key: str) -> None:
        sent = getattr(self, "_farm_sabit_sent", None)
        if sent is None:
            self._farm_sabit_sent = {"A": set(), "B": set()}
            sent = self._farm_sabit_sent
        sent.setdefault(tpl_key, set()).add(int(vid))

    def _farm_sync_la_plunder_list(self) -> None:
        """am_farm plunder_list — hide_attacked varsayılan (saldırılan köyler hariç)."""
        if not self.browser or self._farm_la_sync_inflight:
            return
        village_id = self._game_data.get("village", {}).get("id", "")
        if not village_id:
            return

        import time
        self._farm_la_sync_inflight = True
        self._farm_la_sync_last = time.time()

        fetch_js = f"""
        (function() {{
            window.__tw_la_plunder = 'LOADING';
            var villageId = {village_id};
            var baseUrl = '/game.php?village=' + villageId
                + '&screen=am_farm&order=distance&dir=asc';

            function parsePage(html) {{
                var out = {{A: [], B: [], allVids: [], villages: []}};
                var doc = new DOMParser().parseFromString(html, 'text/html');
                doc.querySelectorAll('tr[id^="village_"]').forEach(function(row) {{
                    var m = row.id.match(/village_(\\d+)/);
                    if (!m) return;
                    var vid = parseInt(m[1], 10);
                    out.allVids.push(vid);
                    var coordRe = /\\((\\d+)\\|(\\d+)\\)/;
                    var coordMatch = (row.textContent || '').match(coordRe);
                    var vx = coordMatch ? parseInt(coordMatch[1], 10) : 0;
                    var vy = coordMatch ? parseInt(coordMatch[2], 10) : 0;
                    out.villages.push({{vid: vid, x: vx, y: vy}});
                    var iconA = row.querySelector('a.farm_village_' + vid + '.farm_icon_a');
                    var iconB = row.querySelector('a.farm_village_' + vid + '.farm_icon_b');
                    function active(el) {{
                        if (!el) return false;
                        var cls = el.className || '';
                        return cls.indexOf('farm_icon_disabled') < 0
                            && cls.indexOf('done') < 0;
                    }}
                    if (active(iconA)) out.A.push(vid);
                    if (active(iconB)) out.B.push(vid);
                }});
                var maxPage = 0;
                doc.querySelectorAll('#plunder_list_nav a.paged-nav-item').forEach(function(a) {{
                    var pm = (a.getAttribute('href') || '').match(/Farm_page=(\\d+)/);
                    if (pm) maxPage = Math.max(maxPage, parseInt(pm[1], 10));
                }});
                return {{out: out, maxPage: maxPage}};
            }}

            fetch(baseUrl + '&Farm_page=0', {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html0) {{
                var p0 = parsePage(html0);
                var merged = {{A: p0.out.A.slice(), B: p0.out.B.slice(),
                    allVids: p0.out.allVids.slice(), villages: p0.out.villages.slice()}};
                var maxPage = p0.maxPage;
                var chain = Promise.resolve();
                for (var pg = 1; pg <= maxPage; pg++) {{
                    (function(page) {{
                        chain = chain.then(function() {{
                            return fetch(baseUrl + '&Farm_page=' + page,
                                {{credentials: 'same-origin'}})
                            .then(function(r) {{ return r.text(); }})
                            .then(function(html) {{
                                var pr = parsePage(html);
                                merged.A = merged.A.concat(pr.out.A);
                                merged.B = merged.B.concat(pr.out.B);
                                merged.allVids = merged.allVids.concat(pr.out.allVids);
                                merged.villages = merged.villages.concat(pr.out.villages);
                            }});
                        }});
                    }})(pg);
                }}
                return chain.then(function() {{
                    window.__tw_la_plunder = JSON.stringify({{
                        status: 'OK', A: merged.A, B: merged.B,
                        allVids: merged.allVids, villages: merged.villages,
                        pages: maxPage + 1
                    }});
                }});
            }})
            .catch(function(err) {{
                window.__tw_la_plunder = JSON.stringify({{
                    status: 'ERROR', message: String(err)
                }});
            }});
        }})();
        """

        def _poll(attempt: int):
            if attempt > 40:
                self._farm_la_sync_inflight = False
                self._add_log("FARM", "warn", "LA plunder sync zaman aşımı")
                return

            def _on(result):
                result_str = str(result) if result else "WAITING"
                if result_str in ("WAITING", "LOADING"):
                    QTimer.singleShot(
                        self.TW_JS_POLL_MS,
                        lambda: _poll(attempt + 1))
                    return
                self._farm_la_sync_inflight = False
                self.browser.page().runJavaScript(
                    "window.__tw_la_plunder = null;")
                try:
                    data = json.loads(result_str)
                except Exception:
                    self._add_log("FARM", "warn", "LA plunder sync JSON hatası")
                    return
                self._farm_apply_la_sync_result(data)

            self.browser.page().runJavaScript(
                "window.__tw_la_plunder || 'WAITING';", _on)

        self.browser.page().runJavaScript(fetch_js)
        QTimer.singleShot(self.TW_JS_POLL_MS, lambda: _poll(0))

    def _farm_apply_place_sync_result(self, data: dict) -> None:
        import time
        if data.get("status") != "OK":
            return
        self._farm_place_outbound = set(int(v) for v in data.get("outbound", []))
        self._farm_place_returning = set(int(v) for v in data.get("returning", []))
        self._farm_place_sync_ts = time.time()
        for vid in self._farm_place_returning:
            if vid in self._farm_outgoing:
                self._farm_release_village(vid)
        for vid in self._farm_place_outbound:
            if vid not in self._farm_place_returning:
                item = self._farm_table_item_for_vid(vid)
                if item and not (item.text(4) or "").startswith("Yolda"):
                    vd = item.data(0, Qt.UserRole) or {}
                    tpl = vd.get("assigned_tpl") or "?"
                    item.setText(4, f"Yolda {tpl}")
                    item.setForeground(4, QColor("#2d5a9e"))

    def _farm_sync_place_commands(self) -> None:
        """Place komut listesi — attack/return ile erken serbest bırakma."""
        if not self.browser or self._farm_place_sync_inflight:
            return
        village_id = self._game_data.get("village", {}).get("id", "")
        if not village_id:
            return

        import time
        self._farm_place_sync_inflight = True
        self._farm_place_sync_last = time.time()

        fetch_js = f"""
        (function() {{
            window.__tw_place_cmds = 'LOADING';
            fetch('/game.php?village={village_id}&screen=place',
                {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var rows = doc.querySelectorAll('tr.command-row');
                var outbound = [];
                var returning = [];
                var now = Math.floor(Date.now() / 1000);

                rows.forEach(function(row) {{
                    var cmdSpan = row.querySelector('.command_hover_details');
                    var cmdType = cmdSpan
                        ? (cmdSpan.getAttribute('data-command-type') || '') : '';
                    var timerSpan = row.querySelector('[data-endtime]');
                    var endtime = timerSpan
                        ? parseInt(timerSpan.getAttribute('data-endtime'), 10) : 0;
                    if (endtime <= now) return;

                    var vid = 0;
                    var links = row.querySelectorAll('a[href*="info_village"]');
                    links.forEach(function(a) {{
                        var hm = (a.getAttribute('href') || a.href || '')
                            .match(/info_village[^&]*&(?:amp;)?id=(\\d+)/);
                        if (hm) vid = parseInt(hm[1], 10);
                    }});
                    if (!vid) {{
                        var coordRe = /\\((\\d+)\\|(\\d+)\\)/;
                        var text = row.textContent || '';
                        var cm = text.match(coordRe);
                        if (cm) {{
                            window.__tw_place_coord = cm[1] + '|' + cm[2];
                        }}
                    }}
                    if (!vid) return;

                    if (cmdType === 'return' || cmdType === 'cancel') {{
                        returning.push(vid);
                    }} else if (cmdType === 'attack') {{
                        outbound.push(vid);
                    }}
                }});

                window.__tw_place_cmds = JSON.stringify({{
                    status: 'OK', outbound: outbound, returning: returning,
                    total: rows.length
                }});
            }})
            .catch(function(err) {{
                window.__tw_place_cmds = JSON.stringify({{
                    status: 'ERROR', message: String(err)
                }});
            }});
        }})();
        """

        def _poll(attempt: int):
            if attempt > 30:
                self._farm_place_sync_inflight = False
                return

            def _on(result):
                result_str = str(result) if result else "WAITING"
                if result_str in ("WAITING", "LOADING"):
                    QTimer.singleShot(
                        self.TW_JS_POLL_MS,
                        lambda: _poll(attempt + 1))
                    return
                self._farm_place_sync_inflight = False
                self.browser.page().runJavaScript(
                    "window.__tw_place_cmds = null;")
                try:
                    data = json.loads(result_str)
                except Exception:
                    return
                self._farm_apply_place_sync_result(data)

            self.browser.page().runJavaScript(
                "window.__tw_place_cmds || 'WAITING';", _on)

        self.browser.page().runJavaScript(fetch_js)
        QTimer.singleShot(self.TW_JS_POLL_MS, lambda: _poll(0))

    def _farm_resolve_dual_send_template(
            self, preferred: str | None = None,
            exclude: str | None = None) -> tuple:
        """Önce tercih edilen faz; asker yoksa sıradaki/diğer şablonu dene."""
        order = self._farm_get_phase_order()
        try_keys: list = []
        if preferred and preferred != exclude:
            try_keys.append(preferred)
        for k in order:
            if k not in try_keys and k != exclude:
                try_keys.append(k)
        for k in self._farm_enabled_template_keys():
            if k not in try_keys and k != exclude:
                try_keys.append(k)
        for key in try_keys:
            if self._farm_can_send_template(key):
                return (
                    key,
                    self._farm_get_template_troops_by_key(key),
                    self._farm_get_template_id_by_key(key),
                )
        return None, None, None

    def _farm_dual_all_templates_no_troops(self) -> bool:
        """Bekleyen atama varken hiçbir şablonda gönderilecek asker kalmadı mı?"""
        has_pending = False
        for key in self._farm_enabled_template_keys():
            if self._farm_count_pending_for_template(key) > 0:
                has_pending = True
                if self._farm_can_send_template(key):
                    return False
        return has_pending

    def _farm_get_phase_order(self) -> list:
        """Kullanıcının seçtiği gönderim sırası."""
        if self.farm_la_order.currentIndex() == 0:
            return ["B", "A"]
        return ["A", "B"]

    def _farm_reset_active_phase(self) -> None:
        """Çift modda ilk gönderim fazını ayarla (asker varsa öncelikli)."""
        resolved, _, _ = self._farm_resolve_dual_send_template()
        if resolved:
            self._farm_active_phase = resolved
            return
        for key in self._farm_get_phase_order():
            if not self._farm_template_enabled(key):
                continue
            if self._farm_count_pending_for_template(key) > 0:
                self._farm_active_phase = key
                return
        order = self._farm_get_phase_order()
        self._farm_active_phase = order[0] if order else "B"

    def _farm_item_status_sent(self, status: str) -> bool:
        st = (status or "").strip()
        return st.startswith("✓")

    def _farm_item_is_pending(self, item) -> bool:
        if not item:
            return False
        st = item.text(4)
        if st in ("⛔ Kara liste", "✗ ID yok", "✗ Hata", "✗ Premium", "Zaman aşımı"):
            return False
        if self._farm_is_sabit_round_wait() and self._farm_item_status_sent(st):
            return False
        return True

    def _farm_count_pending_for_template(self, tpl_key: str) -> int:
        count = 0
        pool = getattr(self, "_farm_assign_pools", {}).get(tpl_key, [])
        if pool:
            for _dist, vid, _vd in pool:
                if self._farm_is_sabit_round_wait() and self._farm_is_sabit_sent(vid, tpl_key):
                    continue
                if not self._farm_is_village_attackable(vid, tpl_key):
                    continue
                count += 1
            return count
        for vid, assigned in self._farm_tpl_assignments.items():
            if assigned != tpl_key:
                continue
            if self._farm_is_sabit_round_wait() and self._farm_is_sabit_sent(vid, tpl_key):
                continue
            if not self._farm_is_village_attackable(vid, tpl_key):
                continue
            count += 1
        return count

    def _farm_has_attackable_work(self) -> bool:
        """Tur bitmeden önce: saldırılabilir hedef veya yolda komut var mı?"""
        if self._farm_outgoing:
            return True
        if self._farm_is_dual_mode():
            for key in self._farm_enabled_template_keys():
                if self._farm_count_pending_for_template(key) > 0:
                    return True
        else:
            key = "A" if self.farm_la_template.currentIndex() == 0 else "B"
            if self._farm_count_pending_for_template(key) > 0:
                return True
        return False

    def _farm_try_advance_phase(self) -> bool:
        """Mevcut faz bittiyse sıradaki şablon fazına geç."""
        order = self._farm_get_phase_order()
        current = getattr(self, "_farm_active_phase", order[0])
        try:
            start = order.index(current) + 1
        except ValueError:
            start = 0
        for key in order[start:]:
            if not self._farm_template_enabled(key):
                continue
            if self._farm_count_pending_for_template(key) > 0:
                self._farm_active_phase = key
                self._add_log(
                    "FARM", "info",
                    f"▶ Faz {current} tamamlandı — {key} fazına geçiliyor")
                return True
        return False

    def _farm_compute_template_assignments(self) -> None:
        """LA plunder_list birincil — tüm saldırılabilir köyler (asker limiti yok)."""
        self._farm_tpl_assignments = {}
        self._farm_assign_pools = {"A": [], "B": []}

        if not self._farm_is_dual_mode():
            key = "A" if self.farm_la_template.currentIndex() == 0 else "B"
            pool = self._farm_build_la_candidates(key)
            self._farm_assign_pools[key] = pool
            for _dist, vid, vd in pool:
                self._farm_tpl_assignments[int(vid)] = key
                item = self._farm_table_item_for_vid(vid)
                if item:
                    vdt = dict(item.data(0, Qt.UserRole) or vd)
                    vdt["assigned_tpl"] = key
                    item.setData(0, Qt.UserRole, vdt)
            return

        keys_in_order = [
            k for k in self._farm_get_phase_order()
            if self._farm_template_enabled(k)
        ]
        counts: dict = {}
        for key in keys_in_order:
            pool = self._farm_build_la_candidates(key)
            self._farm_assign_pools[key] = pool
            counts[key] = len(pool)
            for _dist, vid, vd in pool:
                vid = int(vid)
                self._farm_tpl_assignments[vid] = key
                item = self._farm_table_item_for_vid(vid)
                if item:
                    vdt = dict(item.data(0, Qt.UserRole) or vd)
                    vdt["assigned_tpl"] = key
                    item.setData(0, Qt.UserRole, vdt)

        self._farm_reset_active_phase()
        parts = [f"{k}→{counts.get(k, 0)}" for k in keys_in_order]
        self._add_log(
            "FARM", "info",
            f"📋 Atama (LA): {', '.join(parts)}")

    def _farm_next_assigned_village(self, tpl_key: str):
        """Belirtilen şablona atanmış en yakın saldırılabilir köy (LA havuzu)."""
        pool = getattr(self, "_farm_assign_pools", {}).get(tpl_key, [])
        for _dist, vid, vd in pool:
            vid = int(vid)
            if self._farm_is_sabit_round_wait() and self._farm_is_sabit_sent(vid, tpl_key):
                continue
            if not self._farm_is_village_attackable(vid, tpl_key):
                item = self._farm_table_item_for_vid(vid)
                if item:
                    st = (item.text(4) or "").strip()
                    if not st.startswith("Yolda"):
                        item.setText(4, f"Yolda {tpl_key}")
                        item.setForeground(4, QColor("#2d5a9e"))
                continue
            item = self._farm_table_item_for_vid(vid)
            return item, vd
        return None, None

    def _farm_next_single_template_village(self, tpl_key: str):
        """Tek şablon modu — LA havuzundan sıradaki köy."""
        pool = getattr(self, "_farm_assign_pools", {}).get(tpl_key)
        if not pool:
            pool = self._farm_build_la_candidates(tpl_key)
            self._farm_assign_pools[tpl_key] = pool
        start = getattr(self, "_farm_barb_index", 0)
        n = len(pool)
        if n == 0:
            return None, None
        for offset in range(n):
            idx = (start + offset) % n
            _dist, vid, vd = pool[idx]
            vid = int(vid)
            if self._farm_is_sabit_round_wait() and self._farm_is_sabit_sent(vid, tpl_key):
                continue
            if not self._farm_is_village_attackable(vid, tpl_key):
                item = self._farm_table_item_for_vid(vid)
                if item and not (item.text(4) or "").startswith("Yolda"):
                    item.setText(4, f"Yolda {tpl_key}")
                    item.setForeground(4, QColor("#2d5a9e"))
                continue
            self._farm_barb_index = idx + 1
            item = self._farm_table_item_for_vid(vid)
            return item, vd
        return None, None

    def _farm_selected_template_id(self) -> int | None:
        """Seçili A/B şablonunun oyun içi ID'si (tek mod)."""
        idx = self.farm_la_template.currentIndex()
        if idx == 0:
            return self._farm_la_template_a
        return self._farm_la_template_b

    def _farm_get_la_template_troops(self) -> dict | None:
        """Seçili LA şablonunun birlik dict'i (tek mod)."""
        if self._farm_is_dual_mode():
            phase = getattr(self, "_farm_active_phase", "B")
            return self._farm_get_template_troops_by_key(phase)
        key = "A" if self.farm_la_template.currentIndex() == 0 else "B"
        return self._farm_get_template_troops_by_key(key)

    def _farm_update_la_preview(self) -> None:
        """Şablon önizleme etiketini ve bilgi spinbox'larını güncelle."""
        if self._farm_is_dual_mode():
            parts = []
            combined = {}
            for key in ("A", "B"):
                if not self._farm_template_enabled(key):
                    continue
                troops = self._farm_get_template_troops_by_key(key) or {}
                tid = self._farm_get_template_id_by_key(key)
                if troops:
                    unit_parts = [f"{k}:{v}" for k, v in troops.items()]
                    label = f"{key}"
                    if tid:
                        label += f"(ID {tid})"
                    parts.append(f"{label}: {', '.join(unit_parts)}")
                    for uk, uv in troops.items():
                        combined[uk] = combined.get(uk, 0) + int(uv)
            order = "→".join(self._farm_get_phase_order())
            self.farm_la_preview_label.setText(
                (" | ".join(parts) + f" | sıra: {order}") if parts else "—")
            for uk, spin in self.farm_troop_inputs.items():
                spin.setValue(int(combined.get(uk, 0)))
            return

        tid = self._farm_selected_template_id()
        troops = self._farm_get_template_troops_by_key(
            "A" if self.farm_la_template.currentIndex() == 0 else "B") or {}
        if tid and troops:
            parts = [f"{k}:{v}" for k, v in troops.items()]
            self.farm_la_preview_label.setText(f"ID {tid} — {', '.join(parts)}")
        elif tid:
            self.farm_la_preview_label.setText(f"ID {tid}")
        else:
            self.farm_la_preview_label.setText("—")
        for key, spin in self.farm_troop_inputs.items():
            spin.setValue(int(troops.get(key, 0)))

    def _farm_fetch_la_template_ids(self, callback=None) -> None:
        """am_farm sayfasından Yağma Asistanı A/B şablon ID'lerini çek."""
        if self._farm_la_templates_fetching:
            return
        village_id = self._game_data.get("village", {}).get("id", "")
        csrf = self._game_data.get("csrf", "")
        if not village_id or not csrf:
            if callback:
                callback(False, "Oyun verisi yok — önce giriş yapın.")
            return

        self._farm_la_templates_fetching = True

        fetch_js = f"""
        (function() {{
            window.__tw_la_templates = 'LOADING';
            var vid = {village_id};
            var csrf = '{csrf}';
            var url = '/game.php?village=' + vid + '&screen=am_farm&mode=farm&h=' + encodeURIComponent(csrf);
            fetch(url, {{credentials: 'same-origin'}})
            .then(function(r) {{ return r.text(); }})
            .then(function(html) {{
                var out = {{a: null, b: null, troops: {{}}, error: null}};
                if (/farm_assistent_inactive|premium_feature_locked|farm_assistant_inactive/i.test(html) &&
                    !/farm_icon_a/.test(html) && !/sendUnits/.test(html)) {{
                    out.error = 'Yağma Asistanı aktif degil veya premium kapali';
                    window.__tw_la_templates = JSON.stringify(out);
                    return;
                }}
                var ma = html.match(/farm_icon_a[^>]*onclick="[^"]*sendUnits\\(this,\\s*\\d+,\\s*(\\d+)\\)/);
                var mb = html.match(/farm_icon_b[^>]*onclick="[^"]*sendUnits\\(this,\\s*\\d+,\\s*(\\d+)\\)/);
                if (ma) out.a = parseInt(ma[1], 10);
                if (mb) out.b = parseInt(mb[1], 10);
                if (!out.a) {{
                    var ha = html.match(/name="template\\[(\\d+)\\]\\[id\\]"[^>]*class="[^"]*farm_icon_a/);
                    if (!ha) ha = html.match(/farm_icon_a[\\s\\S]{{0,400}}?template\\[(\\d+)\\]/);
                    if (ha) out.a = parseInt(ha[1], 10);
                }}
                if (!out.b) {{
                    var hb = html.match(/name="template\\[(\\d+)\\]\\[id\\]"[^>]*class="[^"]*farm_icon_b/);
                    if (!hb) hb = html.match(/farm_icon_b[\\s\\S]{{0,400}}?template\\[(\\d+)\\]/);
                    if (hb) out.b = parseInt(hb[1], 10);
                }}
                var tplOrder = [];
                var tre = /Accountmanager\\.farm\\.templates\\['t_(\\d+)'\\]/g, tm;
                while ((tm = tre.exec(html)) !== null) {{
                    var id = parseInt(tm[1], 10);
                    if (tplOrder.indexOf(id) < 0) tplOrder.push(id);
                }}
                if (!out.a && tplOrder.length >= 1) out.a = tplOrder[0];
                if (!out.b && tplOrder.length >= 2) out.b = tplOrder[1];
                var troopRe = /Accountmanager\\.farm\\.templates\\['t_(\\d+)'\\]\\['(\\w+)'\\]\\s*=\\s*(\\d+)/g;
                while ((tm = troopRe.exec(html)) !== null) {{
                    var tid = tm[1], unit = tm[2], cnt = parseInt(tm[3], 10);
                    if (!out.troops[tid]) out.troops[tid] = {{}};
                    out.troops[tid][unit] = cnt;
                }}
                if (!out.a) out.error = 'Sablon A bulunamadi';
                window.__tw_la_templates = JSON.stringify(out);
            }})
            .catch(function(e) {{
                window.__tw_la_templates = JSON.stringify({{error: String(e)}});
            }});
        }})();
        """

        def _poll_templates(attempt: int = 0) -> None:
            if attempt > 50:
                self._farm_la_templates_fetching = False
                if callback:
                    callback(False, "Yağma şablonları yüklenemedi (zaman aşımı).")
                return

            def _on_poll(val) -> None:
                s = str(val or "")
                if s in ("", "LOADING", "undefined", "null"):
                    QTimer.singleShot(
                        self.TW_JS_POLL_MS, lambda: _poll_templates(attempt + 1))
                    return
                self._farm_la_templates_fetching = False
                try:
                    import json
                    data = json.loads(s)
                except Exception:
                    if callback:
                        callback(False, "Yağma şablon verisi okunamadı.")
                    return
                if data.get("error"):
                    if callback:
                        callback(False, data["error"])
                    return
                self._farm_la_template_a = data.get("a")
                self._farm_la_template_b = data.get("b")
                self._farm_la_template_troops = data.get("troops") or {}
                self._farm_update_la_preview()
                if not self._farm_la_template_a:
                    if callback:
                        callback(False, "Şablon A bulunamadı.")
                    return
                if callback:
                    callback(True)

            self.browser.page().runJavaScript(
                "window.__tw_la_templates || 'LOADING';", _on_poll)

        self.browser.page().runJavaScript(fetch_js)
        QTimer.singleShot(self.TW_JS_POLL_MS, lambda: _poll_templates(0))

    def _farm_do_start(self) -> None:
        """Farm döngüsünü etkinleştir (şablonlar hazır)."""
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
        self._farm_tpl_assignments = {}
        self._farm_sent_count_a = 0
        self._farm_sent_count_b = 0
        self._farm_waiting_returns = False
        self._farm_fallback_log_at = 0.0
        self._farm_fallback_log_pair = ""
        self._farm_outgoing = {}
        self._farm_outgoing_coords = set()
        self._farm_la_attackable = {"A": set(), "B": set()}
        self._farm_la_all_vids = set()
        self._farm_la_villages = {}
        self._farm_assign_pools = {"A": [], "B": []}
        self._farm_sabit_sent = {"A": set(), "B": set()}
        self._farm_la_sync_log_snapshot = None
        self._farm_la_sync_ts = 0.0
        self._farm_la_sync_last = 0.0
        self._farm_la_sync_ready = False
        self._farm_place_outbound = set()
        self._farm_place_returning = set()

        for i in range(self.map_barb_table.topLevelItemCount()):
            item = self.map_barb_table.topLevelItem(i)
            if item and item.text(4) != "⛔ Kara liste":
                item.setText(4, "")
                item.setForeground(4, QColor("#000000"))
                vd = dict(item.data(0, Qt.UserRole) or {})
                vd.pop("assigned_tpl", None)
                item.setData(0, Qt.UserRole, vd)

        self._farm_compute_template_assignments()
        self._farm_update_labels()
        self._farm_sync_la_plunder_list()
        if self._farm_is_dual_mode():
            phase = getattr(self, "_farm_active_phase", "B")
            n_a = sum(1 for k in self._farm_tpl_assignments.values() if k == "A")
            n_b = sum(1 for k in self._farm_tpl_assignments.values() if k == "B")
            order = "→".join(self._farm_get_phase_order())
            self.farm_status_label.setText(
                f"Durum: Çift farm başladı (A:{n_a} B:{n_b}, sıra {order})")
            self._add_log(
                "FARM", "success",
                f"▶ Çift yağma farm — A:{n_a} B:{n_b}, sıra {order}, önce {phase}")
        else:
            tpl = self._farm_selected_template_id()
            tpl_name = "A" if self.farm_la_template.currentIndex() == 0 else "B"
            self.farm_status_label.setText(
                f"Durum: Farm başlatıldı (şablon {tpl_name}, ID {tpl})")
            self._add_log("FARM", "success",
                          f"▶ Yağma farm başlatıldı — şablon {tpl_name} (ID {tpl})")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #228822;")

    def _farm_start(self):
        """Farm sirkülasyonunu başlat."""
        if not self._map_data_loaded:
            QMessageBox.warning(self, "Uyarı", "Önce haritayı yükleyin!")
            return

        def _on_templates(ok: bool, msg: str = "") -> None:
            if not ok:
                QMessageBox.warning(
                    self, "Uyarı",
                    msg or "Yağma şablonları alınamadı.")
                return
            self._farm_do_start()

        def _begin_template_load() -> None:
            if self._farm_la_template_a:
                _on_templates(True)
            else:
                self.farm_status_label.setText("Durum: Yağma şablonları yükleniyor...")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
                self._farm_fetch_la_template_ids(_on_templates)

        if self._farm_has_la_premium():
            _begin_template_load()
            return

        # Cache'de yok — tarayıcıdaki game_data'dan canlı kontrol
        def _on_live(active, _msg: str) -> None:
            if active is True:
                _begin_template_load()
            elif active is False:
                QMessageBox.warning(
                    self, "Uyarı",
                    "Yağma Asistanı premium özelliği aktif görünmüyor.\n\n"
                    "Oyunda Yağma Asistanı açıksa önce herhangi bir oyun sayfasına "
                    "gidip veri yenilenmesini bekleyin, sonra tekrar deneyin.")
            else:
                # game_data okunamadı — am_farm şablon çekimi ile doğrula
                _begin_template_load()

        self._farm_check_la_premium_live(_on_live)

    def _farm_stop(self):
        """Farm sirkülasyonunu durdur."""
        self.farm_enable_cb.setChecked(False)
        self.farm_start_btn.setEnabled(True)
        self.farm_stop_btn.setEnabled(False)
        self.farm_status_label.setText("Durum: Durduruldu")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc4444;")
        self._farm_report_scan_blocking_farm = False
        self._add_log("FARM", "warn", "⏹ Farm sirkülasyonu durduruldu")

    def _farm_template(self, template):
        """Hızlı şablon uygula."""
        for key, spin in self.farm_troop_inputs.items():
            spin.setValue(template.get(key, 0))

    def _farm_update_labels(self):
        """Farm durum etiketlerini güncelle."""
        remaining = 0
        if self._farm_is_dual_mode():
            for key in self._farm_enabled_template_keys():
                remaining += self._farm_count_pending_for_template(key)
        else:
            key = "A" if self.farm_la_template.currentIndex() == 0 else "B"
            remaining = self._farm_count_pending_for_template(key)
        sa = getattr(self, "_farm_sent_count_a", 0)
        sb = getattr(self, "_farm_sent_count_b", 0)
        if self._farm_is_dual_mode():
            self.farm_sent_label.setText(
                f"Gönderilen: {self._farm_sent_count} (A:{sa} B:{sb}) | Kalan: {remaining}")
        else:
            self.farm_sent_label.setText(
                f"Gönderilen: {self._farm_sent_count} | Kalan: {remaining}")

    def _farm_get_troops_to_send(self):
        """Gönderilecek asker dict'i — seçili Yağma Asistanı şablonundan."""
        return self._farm_get_la_template_troops()

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
        if self._human_verification_required:
            return

        import time
        now = time.time()

        # Tur arası bekleme kontrolü (sync tur beklerken çalışmaz)
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
                self._farm_begin_pre_round_report_scan()
                return

        # LA plunder_list + place komut senkronu (throttle ~18 sn)
        if now - getattr(self, "_farm_la_sync_last", 0) >= 18:
            if not getattr(self, "_farm_la_sync_inflight", False):
                self._farm_sync_la_plunder_list()
        if now - getattr(self, "_farm_place_sync_last", 0) >= 18:
            if not getattr(self, "_farm_place_sync_inflight", False):
                self._farm_sync_place_commands()

        if getattr(self, "_farm_report_scan_blocking_farm", False):
            self.farm_status_label.setText("Durum: Raporlar taranıyor (kara liste)...")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
            return

        if not getattr(self, "_farm_la_sync_ready", False):
            if not getattr(self, "_farm_la_sync_inflight", False):
                self._farm_sync_la_plunder_list()
            self.farm_status_label.setText("Durum: LA listesi senkronize ediliyor...")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")
            return

        # Aralık kontrolü
        interval = self.farm_interval.value()
        if now - self._farm_last_send < interval:
            remaining_sec = int(interval - (now - self._farm_last_send))
            self.farm_status_label.setText(f"Durum: Sonraki saldırı {remaining_sec}sn")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")
            return

        if (
            getattr(self, "_farm_waiting_returns", False)
            and self._farm_is_dual_mode()
            and self._farm_dual_all_templates_no_troops()
        ):
            return

        # Gönderilecek askerleri / şablon kontrolü
        if self._farm_is_dual_mode():
            if not self._farm_tpl_assignments:
                self._farm_compute_template_assignments()
            preferred = getattr(self, "_farm_active_phase", "B")
            if preferred and self._farm_count_pending_for_template(preferred) == 0:
                self._farm_try_advance_phase()
                preferred = getattr(self, "_farm_active_phase", preferred)
            prev_preferred = preferred
            tpl_key, troops, template_id = self._farm_resolve_dual_send_template(
                preferred)
            if tpl_key:
                if tpl_key != prev_preferred:
                    self._farm_log_template_fallback(prev_preferred, tpl_key)
                    self.farm_status_label.setText(
                        f"Durum: {prev_preferred} asker yok → {tpl_key}")
                    self.farm_status_label.setStyleSheet(
                        "font-size: 10px; color: #aa6600;")
                self._farm_active_phase = tpl_key
                self._farm_waiting_returns = False
            dual_round_done = not tpl_key
        else:
            tpl_key = "A" if self.farm_la_template.currentIndex() == 0 else "B"
            template_id = self._farm_selected_template_id()
            troops = self._farm_get_troops_to_send()
            dual_round_done = False

        if not dual_round_done and not template_id:
            self.farm_status_label.setText("Durum: Yağma şablonu yükleniyor...")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")
            if not self._farm_la_templates_fetching:
                self._farm_fetch_la_template_ids()
            return

        if not dual_round_done and not troops:
            self.farm_status_label.setText("Durum: Şablon birlikleri okunamadı!")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc2222;")
            return

        if dual_round_done and self._farm_is_dual_mode():
            if self._farm_dual_all_templates_no_troops():
                if self._farm_is_sabit_round_wait():
                    self.farm_status_label.setText("Durum: Asker yetersiz")
                    self.farm_status_label.setStyleSheet(
                        "font-size: 10px; color: #cc8800;")
                    self._farm_finish_round_sabit_sure(
                        "Asker yetersiz — tüm şablonlar boş")
                else:
                    self._farm_begin_wait_for_returns()
                return

        # Sabit süre: tek şablonda asker kalmadıysa turu kapat
        if (
            not dual_round_done
            and not self._farm_is_dual_mode()
            and self._farm_is_sabit_round_wait()
            and troops
            and not self._farm_has_enough_troops(troops)
        ):
            self._farm_finish_round_sabit_sure(
                "Yetersiz asker — kalan köyler bu turda atlanıyor")
            return

        target = None
        target_item = None

        la_ready = bool(getattr(self, "_farm_la_villages", {}))
        total = self.map_barb_table.topLevelItemCount()
        if not la_ready and total == 0:
            self.farm_status_label.setText(
                "Durum: LA listesi / barbar köy yok! Farm başlatın.")
            self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc2222;")
            return

        if dual_round_done:
            pass
        elif self._farm_is_dual_mode():
            target_item, target = self._farm_next_assigned_village(tpl_key)
            if not target and self._farm_try_advance_phase():
                return
        else:
            target_item, target = self._farm_next_single_template_village(tpl_key)

        if not target:
            if self._farm_has_attackable_work():
                if self._farm_outgoing:
                    self.farm_status_label.setText("Durum: Köyler yolda — dönüş bekleniyor")
                    self.farm_status_label.setStyleSheet(
                        "font-size: 10px; color: #2d5a9e;")
                    if not getattr(self, "_farm_waiting_returns", False):
                        if not self._farm_is_sabit_round_wait():
                            self._farm_begin_wait_for_returns(
                                "Hedefler yolda — dönüş bekleniyor")
                    return
                if dual_round_done and self._farm_is_dual_mode():
                    pass
                else:
                    self.farm_status_label.setText(
                        "Durum: Saldırılabilir köy var — asker/bekleme")
                    self.farm_status_label.setStyleSheet(
                        "font-size: 10px; color: #aa6600;")
                    return
            if (
                not self._farm_is_sabit_round_wait()
                and self._farm_outgoing
                and not getattr(self, "_farm_waiting_returns", False)
            ):
                self._farm_begin_wait_for_returns(
                    "Tüm hedefler yolda — dönüş bekleniyor")
                return
            mode = self.farm_round_wait_mode.currentIndex()
            if mode == 0:
                self._farm_finish_round_sabit_sure(
                    "Tur tamamlandı (tüm uygun köylere gönderildi)")
            else:
                self._farm_clear_for_new_round()
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
                self._farm_round_return_times.clear()
            return

        # Yağma saldırısı gönder
        self._farm_sending = True
        target_id = int(target["id"])
        target_x = target["x"]
        target_y = target["y"]

        if target_item:
            target_item.setText(4, f"{tpl_key}→")
            target_item.setForeground(4, QColor("#2d5a9e"))

        self.farm_status_label.setText(
            f"Durum: Yağma {tpl_key} → ({target_x}|{target_y}) ID {target_id}")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")

        self._farm_send_attack(
            target_id, target_x, target_y, template_id, troops, target_item, tpl_key)

    def _farm_send_attack(
            self, target_id, target_x, target_y, template_id, troops, table_item,
            tpl_key=""):
        """Barbar köye Yağma Asistanı ile yağma saldırısı gönder."""
        village_id = self._game_data.get("village", {}).get("id", "")
        csrf = self._game_data.get("csrf", "")

        farm_cmd_id = f"farm_{target_id}_{template_id}"

        self._farm_mark_outgoing(
            int(target_id), int(target_x), int(target_y),
            tpl_key or "A", int(template_id or 0))

        send_js = f"""
        (function() {{
            var sourceId = {village_id};
            var targetId = {target_id};
            var templateId = {template_id};
            var csrf = '{csrf}';
            var cmdId = '{farm_cmd_id}';

            if (!window.__tw_bot_results) window.__tw_bot_results = {{}};
            window.__tw_bot_results[cmdId] = 'SENDING';

            var payload = {{
                target: targetId,
                template_id: templateId,
                source: sourceId
            }};

            function laOk(resp) {{
                if (!resp) {{
                    window.__tw_bot_results[cmdId] = 'SENT_OK';
                    return;
                }}
                if (resp.error) {{
                    var err = String(resp.error);
                    if (/asker|birlik|unit|yeterli|enough/i.test(err)) {{
                        window.__tw_bot_results[cmdId] = 'NO_TROOPS|' + err.substring(0, 120);
                    }} else if (/premium|yağma|yagma|asistan/i.test(err)) {{
                        window.__tw_bot_results[cmdId] = 'PREMIUM|' + err.substring(0, 120);
                    }} else if (/hızlı|hizli|click|rate|çok hızlı/i.test(err)) {{
                        window.__tw_bot_results[cmdId] = 'RATE_LIMIT|' + err.substring(0, 120);
                    }} else {{
                        window.__tw_bot_results[cmdId] = 'ERROR|' + err.substring(0, 120);
                    }}
                    return;
                }}
                if (resp.success === false) {{
                    var msg = resp.message ? String(resp.message) : 'Gonderim basarisiz';
                    window.__tw_bot_results[cmdId] = 'ERROR|' + msg.substring(0, 120);
                    return;
                }}
                window.__tw_bot_results[cmdId] = 'SENT_OK';
            }}

            function laFail(msg) {{
                window.__tw_bot_results[cmdId] = 'ERROR|' + String(msg).substring(0, 120);
            }}

            if (typeof TribalWars !== 'undefined' && typeof Accountmanager !== 'undefined' &&
                Accountmanager.send_units_link) {{
                try {{
                    TribalWars.post(Accountmanager.send_units_link, null, payload,
                        function(resp) {{ laOk(resp); }},
                        function() {{ laFail('Yağma gonderimi reddedildi'); }}
                    );
                    return 'DISPATCHED';
                }} catch (e) {{
                    /* fetch yedegine dus */
                }}
            }}

            var postUrl = '/game.php?village=' + sourceId +
                '&screen=am_farm&mode=farm&ajaxaction=farm&json=1&h=' + encodeURIComponent(csrf);
            fetch(postUrl, {{
                method: 'POST',
                credentials: 'same-origin',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'TribalWars-Ajax': '1'
                }},
                body: new URLSearchParams({{
                    target: String(targetId),
                    template_id: String(templateId),
                    source: String(sourceId)
                }}).toString()
            }})
            .then(function(r) {{
                return r.text().then(function(t) {{
                    try {{ return JSON.parse(t); }}
                    catch (e) {{ return {{raw: t}}; }}
                }});
            }})
            .then(function(data) {{
                if (data && data.error) {{
                    laOk(data);
                }} else if (data && data.raw && /error|hata/i.test(data.raw)) {{
                    laFail(data.raw.substring(0, 120));
                }} else {{
                    laOk(data);
                }}
            }})
            .catch(function(err) {{
                laFail(err);
            }});
            return 'DISPATCHED';
        }})();
        """

        self.browser.page().runJavaScript(send_js)
        self._farm_poll_result(
            table_item, farm_cmd_id, target_x, target_y, troops, 0, tpl_key)

    def _farm_poll_vid_from_cmd(self, cmd_id: str) -> int:
        try:
            parts = str(cmd_id).split("_")
            if len(parts) >= 2:
                return int(parts[1])
        except (TypeError, ValueError):
            pass
        return 0

    def _farm_poll_result(
            self, table_item, cmd_id, tx, ty, troops, attempt, tpl_key=""):
        """Farm yağma saldırısı sonucunu polling ile kontrol et."""
        import time
        if attempt > 60:
            if table_item:
                table_item.setText(4, "Zaman aşımı")
                table_item.setForeground(4, QColor("#cc2222"))
            self._farm_release_village(self._farm_poll_vid_from_cmd(cmd_id))
            self._farm_sending = False
            self._farm_last_send = time.time()
            return

        check_js = f"window.__tw_bot_results ? window.__tw_bot_results['{cmd_id}'] || 'WAITING' : 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"

            if result_str in ("SENT_OK", "SENDING") and result_str == "SENDING" and attempt < 55:
                QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._farm_poll_result(
                    table_item, cmd_id, tx, ty, troops, attempt + 1, tpl_key))
                return

            if result_str == "SENT_OK":
                target_vid = 0
                if table_item:
                    vd = table_item.data(0, Qt.UserRole) or {}
                    target_vid = int(vd.get("id") or 0)
                if not target_vid:
                    target_vid = self._farm_poll_vid_from_cmd(cmd_id)
                tpl_id = self._farm_get_template_id_by_key(tpl_key) if tpl_key else 0
                if self._farm_is_sabit_round_wait():
                    if target_vid and tpl_key:
                        self._farm_mark_sabit_sent(target_vid, tpl_key)
                    if table_item:
                        done_lbl = f"✓ {tpl_key}" if tpl_key else "✓ Yağma"
                        table_item.setText(4, done_lbl)
                        table_item.setForeground(4, QColor("#228822"))
                elif target_vid and target_vid not in self._farm_outgoing:
                    self._farm_mark_outgoing(
                        target_vid, tx, ty, tpl_key or "A", tpl_id or 0)
                elif table_item and not self._farm_is_sabit_round_wait():
                    done_lbl = f"Yolda {tpl_key}" if tpl_key else "Yolda"
                    table_item.setText(4, done_lbl)
                    table_item.setForeground(4, QColor("#2d5a9e"))
                self._farm_sent_count += 1
                if tpl_key == "A":
                    self._farm_sent_count_a += 1
                elif tpl_key == "B":
                    self._farm_sent_count_b += 1
                self._farm_sending = False
                self._farm_last_send = time.time()
                self._farm_update_labels()
                lbl = f"({tx}|{ty}) yağma {tpl_key} ✓" if tpl_key else f"({tx}|{ty}) yağma ✓"
                self.farm_status_label.setText(f"Durum: {lbl}")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #228822;")
                log_tpl = f" {tpl_key}" if tpl_key else ""
                self._add_log("FARM", "success", f"✅ Yağma{log_tpl} → ({tx}|{ty})")
                self.browser.page().runJavaScript(
                    f"if(window.__tw_bot_results) delete window.__tw_bot_results['{cmd_id}'];")

                self._farm_deduct_sent_troops(troops)
                self._farm_waiting_returns = False

                if not self._farm_is_sabit_round_wait():
                    self._farm_record_return_time(tx, ty, troops)
                    QTimer.singleShot(1500, self._farm_sync_la_plunder_list)
                    QTimer.singleShot(800, self._farm_sync_place_commands)

            elif result_str.startswith("NO_TROOPS"):
                msg = result_str.replace("NO_TROOPS|", "")
                self._farm_release_village(self._farm_poll_vid_from_cmd(cmd_id))
                if table_item:
                    table_item.setText(4, "")
                    table_item.setForeground(4, QColor("#000000"))
                self._farm_sending = False
                if not self._farm_is_dual_mode():
                    self._farm_barb_index = max(0, self._farm_barb_index - 1)

                if self._farm_is_dual_mode():
                    alt_key, _, _ = self._farm_resolve_dual_send_template(
                        None, exclude=tpl_key)
                    if alt_key and alt_key != tpl_key:
                        self._farm_active_phase = alt_key
                        self._farm_log_template_fallback(tpl_key, alt_key)
                        self.farm_status_label.setText(
                            f"Durum: {tpl_key} asker yok → {alt_key}")
                        self.farm_status_label.setStyleSheet(
                            "font-size: 10px; color: #aa6600;")
                        return
                    if self._farm_dual_all_templates_no_troops():
                        if self._farm_is_sabit_round_wait():
                            self.farm_status_label.setText("Durum: Asker yetersiz")
                            self.farm_status_label.setStyleSheet(
                                "font-size: 10px; color: #cc8800;")
                            self._farm_finish_round_sabit_sure(
                                "Asker yetersiz — tüm şablonlar boş")
                        else:
                            self._farm_begin_wait_for_returns(
                                "Asker yetersiz — dönüş bekleniyor")
                        return

                self.farm_status_label.setText(f"Durum: Asker yetersiz — {msg[:40]}")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc8800;")

                if self._farm_is_sabit_round_wait():
                    self._farm_finish_round_sabit_sure(
                        "Şablonda yeterli asker yok — tur sonu")
                else:
                    self._farm_fetch_return_times()

            elif result_str.startswith("PREMIUM"):
                msg = result_str.replace("PREMIUM|", "")
                self._farm_release_village(self._farm_poll_vid_from_cmd(cmd_id))
                if table_item:
                    table_item.setText(4, "✗ Premium")
                    table_item.setForeground(4, QColor("#cc2222"))
                self._farm_sending = False
                self._farm_stop()
                self._add_log("FARM", "error", f"❌ Yağma Asistanı: {msg}")
                QMessageBox.warning(
                    self, "Yağma Asistanı",
                    msg or "Yağma Asistanı premium özelliği gerekli.")

            elif result_str.startswith("RATE_LIMIT"):
                msg = result_str.replace("RATE_LIMIT|", "")
                self._farm_release_village(self._farm_poll_vid_from_cmd(cmd_id))
                if table_item:
                    table_item.setText(4, "")
                    table_item.setForeground(4, QColor("#000000"))
                self._farm_sending = False
                self._farm_last_send = time.time() + 1.5
                self.farm_status_label.setText("Durum: Çok hızlı — kısa bekleme")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #aa6600;")
                self._add_log("FARM", "warn", f"⏳ Hız sınırı: {msg}")

            elif result_str.startswith("ERROR"):
                error = result_str.replace("ERROR|", "")
                self._farm_release_village(self._farm_poll_vid_from_cmd(cmd_id))
                if table_item:
                    table_item.setText(4, "✗ Hata")
                    table_item.setForeground(4, QColor("#cc2222"))
                self._farm_sending = False
                self._farm_last_send = time.time()
                self._add_log("FARM", "error", f"❌ ({tx}|{ty}): {error}")
                self.farm_status_label.setText(f"Durum: Hata — ({tx}|{ty})")
                self.farm_status_label.setStyleSheet("font-size: 10px; color: #cc2222;")

            else:
                QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._farm_poll_result(
                    table_item, cmd_id, tx, ty, troops, attempt + 1, tpl_key))

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
                QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._farm_poll_returns(attempt + 1))
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
                self._farm_waiting_returns = False
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

    def _farm_sync_blacklist_to_barb_table(self):
        """Barbar tablosunda kara listedeki satırları işaretle."""
        dark = getattr(self, "_dark_mode", False)
        fg = QColor("#ff8888" if dark else "#cc2222")
        for i in range(self.map_barb_table.topLevelItemCount()):
            item = self.map_barb_table.topLevelItem(i)
            if not item:
                continue
            coord_key = item.text(0).strip("()").replace(" ", "")
            if coord_key in self._farm_blacklist:
                item.setText(4, "⛔ Kara liste")
                item.setForeground(4, fg)

    def _farm_remove_blacklisted_from_barb_table(self):
        """Kara listedeki koordinatların satırlarını barbar tablosundan kaldır."""
        i = 0
        while i < self.map_barb_table.topLevelItemCount():
            item = self.map_barb_table.topLevelItem(i)
            if not item:
                i += 1
                continue
            coord_key = item.text(0).strip("()").replace(" ", "")
            if coord_key in self._farm_blacklist:
                vd = item.data(0, Qt.UserRole)
                if isinstance(vd, dict):
                    k = (int(vd.get("x", 0)), int(vd.get("y", 0)))
                    self._farm_active_coords.pop(k, None)
                self.map_barb_table.takeTopLevelItem(i)
            else:
                i += 1

    def _farm_begin_pre_round_report_scan(self):
        """Yeni farm turu öncesi: raporlardan kara listeyi güncelle, tabloyu temizle."""
        if not self.browser:
            return
        self._farm_report_scan_blocking_farm = True
        self.farm_status_label.setText("Durum: Yeni tur — raporlar taranıyor (kara liste)...")
        self.farm_status_label.setStyleSheet("font-size: 10px; color: #2d5a9e;")

        def _done():
            self._farm_report_scan_blocking_farm = False
            self._farm_update_labels()

        self._farm_run_report_blacklist_scan(ui_feedback=False, remove_rows=True, on_done=_done)

    def _farm_run_report_blacklist_scan(
        self, ui_feedback: bool = True, remove_rows: bool = True, on_done=None
    ):
        """Saldırı raporlarını (en fazla 3 sayfa) tarayıp kara listeyi güncelle."""
        if not self.browser:
            if callable(on_done):
                on_done()
            return

        if ui_feedback:
            self.farm_check_reports_btn.setEnabled(False)
            self.farm_check_reports_btn.setText("Taranıyor...")
        self._add_log("FARM", "info", "Saldırı raporları taranıyor (en fazla 3 sayfa)...")

        village_id = str(self._game_data.get("village", {}).get("id", "") or "")
        if not village_id:
            self._add_log("FARM", "error", "Aktif köy ID yok; rapor taranamıyor.")
            if ui_feedback:
                self.farm_check_reports_btn.setEnabled(True)
                self.farm_check_reports_btn.setText("Raporları Kontrol Et")
            if callable(on_done):
                on_done()
            return

        vid_j = json.dumps(village_id)

        scan_js = (
            """
(function() {
            function hasGreenVictory(row) {
                var imgs = row.querySelectorAll('img[src]');
                for (var i = 0; i < imgs.length; i++) {
                    var s = (imgs[i].getAttribute('src') || '').toLowerCase();
                    if (s.indexOf('dots/green') >= 0) return true;
                    if (s.indexOf('dot') >= 0 && s.indexOf('green') >= 0) return true;
                    var dt = (imgs[i].getAttribute('data-title') || '').toLowerCase();
                    if (dt.indexOf('tam zafer') >= 0 || dt.indexOf('full victory') >= 0) return true;
                }
                return false;
            }
            function rowIsLossReport(row) {
                if (hasGreenVictory(row)) return false;
                var imgs = row.querySelectorAll('img[src]');
                for (var i = 0; i < imgs.length; i++) {
                    var s = (imgs[i].getAttribute('src') || '').toLowerCase();
                    if (s.indexOf('dot') >= 0 && (s.indexOf('red') >= 0 || s.indexOf('yellow') >= 0))
                        return true;
                    if (s.indexOf('dots/red') >= 0 || s.indexOf('dots/yellow') >= 0) return true;
                }
                return false;
            }
            function coordPairsFromText(text) {
                var re = /\\(\\s*(\\d{1,5})\\s*\\|\\s*(\\d{1,5})\\s*\\)/g;
                var pairs = [], m;
                while ((m = re.exec(text)) !== null) {
                    pairs.push(m[1] + '|' + m[2]);
                }
                return pairs;
            }
            function coordFromLinks(row) {
                var as = row.querySelectorAll('a[href]');
                var found = [];
                for (var i = 0; i < as.length; i++) {
                    var h = (as[i].getAttribute('href') || '').replace(/&amp;/g, '&');
                    var m = h.match(/[?&]x=(\\d+)&y=(\\d+)/);
                    if (m) found.push(m[1] + '|' + m[2]);
                }
                return found;
            }
            function pickTargetCoord(pairs, linkPairs) {
                var all = pairs.concat(linkPairs);
                if (all.length === 0) return null;
                if (all.length >= 2) return all[all.length - 1];
                return all[0];
            }
            function pickFarmBlacklistCoord(text, linkPairs) {
                var m = text.match(/Barbar[\\s\\S]{0,180}?\\(\\s*(\\d{1,5})\\s*\\|\\s*(\\d{1,5})\\s*\\)/i);
                if (m) return m[1] + '|' + m[2];
                m = text.match(/barbarian[\\s\\S]{0,180}?\\(\\s*(\\d{1,5})\\s*\\|\\s*(\\d{1,5})\\s*\\)/i);
                if (m) return m[1] + '|' + m[2];
                var pairs = coordPairsFromText(text);
                if (pairs.length >= 2) return pairs[0];
                return pickTargetCoord(pairs, linkPairs);
            }
            function getNextFromFromHtml(html) {
                var re = /href="([^"]*screen=report[^"]*from=(\\d+)[^"]*)"/gi;
                var last = null;
                var m;
                while ((m = re.exec(html)) !== null) last = m[2];
                return last;
            }
            function parseRowsFromHtml(html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var rows = doc.querySelectorAll(
                    '#report_list tr, table#report_list tbody tr, #report_list > tbody > tr, .report-list tr'
                );
                var seen = {};
                var blacklist = [];
                rows.forEach(function(row) {
                    if (!rowIsLossReport(row)) return;
                    var text = row.textContent || '';
                    var linkPairs = coordFromLinks(row);
                    var target = pickFarmBlacklistCoord(text, linkPairs);
                    if (target && !seen[target]) {
                        seen[target] = true;
                        blacklist.push(target);
                    }
                });
                return { blacklist: blacklist, rowCount: rows.length, nextFrom: getNextFromFromHtml(html) };
            }
            var villageId = """
            + vid_j
            + r""";
            var base = '/game.php?village=' + villageId + '&screen=report&mode=attack';
            function fetchChain(fromVal, depth, accSeen, accList) {
                var url = base + (fromVal ? '&from=' + encodeURIComponent(fromVal) : '');
                return fetch(url, {credentials: 'same-origin'})
                    .then(function(r) { return r.text(); })
                    .then(function(html) {
                        var r = parseRowsFromHtml(html);
                        r.blacklist.forEach(function(c) {
                            if (!accSeen[c]) { accSeen[c] = 1; accList.push(c); }
                        });
                        var nf = r.nextFrom;
                        if (depth >= 2 || !nf || (fromVal !== '' && String(nf) === String(fromVal))) {
                            return JSON.stringify({
                                status: 'OK',
                                blacklist: accList,
                                rowCount: r.rowCount,
                                pages: depth + 1
                            });
                        }
                        return new Promise(function(resolve, reject) {
                            setTimeout(function() {
                                try {
                                    resolve(fetchChain(nf, depth + 1, accSeen, accList));
                                } catch (e) { reject(e); }
                            }, 900);
                        });
                    });
            }
            window.__tw_farm_report_fetch = 'LOADING';
            fetchChain('', 0, {}, []).then(function(s) {
                window.__tw_farm_report_fetch = s;
            }).catch(function(err) {
                window.__tw_farm_report_fetch = JSON.stringify({ status: 'ERROR', message: String(err) });
            });
        })();
"""
        )

        self.browser.page().runJavaScript(scan_js)
        QTimer.singleShot(
            self.TW_JS_POLL_MS,
            lambda: self._farm_report_scan_poll(0, village_id, ui_feedback, remove_rows, on_done),
        )

    def _farm_report_scan_poll(self, attempt, village_id, ui_feedback, remove_rows, on_done):
        """QWebEngine Promise/runJavaScript uyumsuzluğu: sonucu window.__tw_farm_report_fetch üzerinden oku."""
        max_attempts = 48

        def finish():
            if ui_feedback:
                self.farm_check_reports_btn.setEnabled(True)
                self.farm_check_reports_btn.setText("Raporları Kontrol Et")
            if callable(on_done):
                on_done()

        if attempt >= max_attempts:
            self._add_log("FARM", "error", "Rapor tarama zaman aşımı (fetch).")
            self.browser.page().runJavaScript("window.__tw_farm_report_fetch=null;")
            finish()
            return

        check_js = (
            "(function(){ var x = window.__tw_farm_report_fetch; "
            "if (x === undefined || x === null) return 'WAITING'; return x; })();"
        )

        def on_poll(result):
            raw = result
            if raw is None:
                QTimer.singleShot(
                    self.TW_JS_POLL_MS,
                    lambda: self._farm_report_scan_poll(
                        attempt + 1, village_id, ui_feedback, remove_rows, on_done
                    ),
                )
                return
            if isinstance(raw, str):
                result_str = raw.strip()
                if result_str in ("WAITING", "LOADING", ""):
                    QTimer.singleShot(
                        self.TW_JS_POLL_MS,
                        lambda: self._farm_report_scan_poll(
                            attempt + 1, village_id, ui_feedback, remove_rows, on_done
                        ),
                    )
                    return

            self.browser.page().runJavaScript("window.__tw_farm_report_fetch=null;")
            try:
                self._farm_apply_report_scan_payload(
                    raw, village_id, ui_feedback, remove_rows
                )
            finally:
                finish()

        self.browser.page().runJavaScript(check_js, on_poll)

    def _farm_apply_report_scan_payload(self, result, village_id, ui_feedback, remove_rows):
        if ui_feedback:
            self.farm_check_reports_btn.setEnabled(True)
            self.farm_check_reports_btn.setText("Raporları Kontrol Et")

        if result is None or (isinstance(result, str) and not result.strip()):
            self._add_log("FARM", "error", "Rapor tarama başarısız.")
            return

        if isinstance(result, dict):
            data = result
        else:
            try:
                data = json.loads(str(result).strip())
            except Exception:
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

        if remove_rows:
            self._farm_remove_blacklisted_from_barb_table()
        else:
            self._farm_sync_blacklist_to_barb_table()

        pages = data.get("pages", "?")
        rows_scanned = data.get("rowCount", "?")
        if len(new_coords) == 0:
            self._add_log(
                "FARM",
                "warn",
                f"Rapor taraması ({pages} sayfa): kayıplı satırda hedef koordinat bulunamadı "
                f"(son sayfa ~{rows_scanned} satır). mode=attack ilk sayfada olduğunuzdan emin olun.",
            )
        else:
            self._add_log(
                "FARM",
                "warn",
                f"Rapor taraması ({pages} sayfa): {len(new_coords)} hedef, {added} yeni kara listeye; "
                f"barbar tablosu güncellendi.",
            )

    def _farm_check_reports(self):
        """Saldırı raporlarını tarayıp kayıplı barbar köyleri kara listeye ekle (manuel)."""
        self._farm_run_report_blacklist_scan(ui_feedback=True, remove_rows=True, on_done=None)

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

    # Hangi birim hangi binadan eğitilir (bağımsız kuyruklar)
    _RT_BUILDINGS = {
        "barracks": {"units": {"spear", "sword", "axe", "archer"}, "label": "Kışla"},
        "stable":   {"units": {"spy", "light", "marcher", "heavy"}, "label": "Ahır"},
        "workshop": {"units": {"ram", "catapult"},                  "label": "Atölye"},
        "other":    {"units": {"knight"},                            "label": "Diğer"},
    }

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

        def _make_bld_states():
            return {bk: {"next_index": 0, "next_fire": 0.0, "processing": False}
                    for bk in self._RT_BUILDINGS}

        added, updated = 0, 0
        for vid, vname, coord in checked_vids:
            label = f"{vname} {coord}"
            if vid in self._rt_village_states:
                # Update existing — preserve timers, reset indices
                st = self._rt_village_states[vid]
                st["units"] = set(selected_units)
                st["row"].setText(1, unit_short_str)
                st["row"].setText(2, "—")
                for bk in self._RT_BUILDINGS:
                    if bk not in st["buildings"]:
                        st["buildings"][bk] = {"next_index": 0, "next_fire": 0.0, "processing": False}
                    else:
                        st["buildings"][bk]["next_index"] = 0
                st["bld_current"] = {}
                updated += 1
            else:
                row = QTreeWidgetItem([label, unit_short_str, "—", "—", "Bekliyor"])
                row.setTextAlignment(3, Qt.AlignCenter)
                for c in range(5):
                    row.setBackground(c, self._rt_bg("add"))
                self.rt_table.addTopLevelItem(row)
                self._rt_village_states[vid] = {
                    "row": row,
                    "units": set(selected_units),
                    "buildings": _make_bld_states(),
                    "bld_current": {},
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

    def _rt_get_building_units(self, vid, bld_key):
        """Belirli bir binaya ait seçili (key, name) listesi."""
        st = self._rt_village_states.get(str(vid))
        if not st:
            return []
        selected = st.get("units", set())
        bld_units_set = self._RT_BUILDINGS.get(bld_key, {}).get("units", set())
        return [(k, n) for k, n in self.RT_UNITS if k in selected and k in bld_units_set]

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
            for bld_key in self._RT_BUILDINGS:
                bld_units = self._rt_get_building_units(vid, bld_key)
                if not bld_units:
                    continue
                bst = state["buildings"][bld_key]
                if bst["processing"]:
                    continue
                if bst["next_fire"] <= now:
                    self._rt_process_building(vid, bld_key)

    def _rt_update_countdowns(self):
        import time
        now = time.time()
        for vid, state in self._rt_village_states.items():
            row = state.get("row")
            if not row:
                continue
            # Soonest next_fire across all buildings that have units
            min_fire = None
            any_processing = False
            for bld_key in self._RT_BUILDINGS:
                bld_units = self._rt_get_building_units(vid, bld_key)
                if not bld_units:
                    continue
                bst = state["buildings"].get(bld_key, {})
                if bst.get("processing"):
                    any_processing = True
                    continue
                nf = bst.get("next_fire", 0.0)
                if min_fire is None or nf < min_fire:
                    min_fire = nf
            if any_processing:
                pass  # col 3 already set by poll handler
            elif min_fire is not None:
                remain = min_fire - now
                if remain > 1:
                    mins = int(remain) // 60
                    secs = int(remain) % 60
                    row.setText(3, f"{mins:02d}:{secs:02d}")
                else:
                    row.setText(3, "—")
            # Rebuild col 2 from per-building current units
            bld_current = state.get("bld_current", {})
            parts = []
            for bld_key, bld_info in self._RT_BUILDINGS.items():
                unit_name = bld_current.get(bld_key)
                if unit_name:
                    parts.append(f"{bld_info['label']}: {unit_name}")
            if parts:
                row.setText(2, " | ".join(parts))

    def _rt_abort_building_poll_for_human(self, vid, bld_key, js_global):
        """Doğrulama/hCaptcha aktifken devam eden eğitim poll zincirini kes (processing + JS global)."""
        state = self._rt_village_states.get(str(vid))
        if state:
            bst = state.get("buildings", {}).get(bld_key)
            if bst:
                bst["processing"] = False
            row = state.get("row")
            if row:
                row.setText(4, "Doğrulama bekleniyor — durdu")
                row.setForeground(4, QColor("#cc4444"))
        if self.browser:
            k = json.dumps(js_global)
            self.browser.page().runJavaScript(f"try {{ window[{k}] = null; }} catch (e) {{}}")

    def _rt_process_building(self, vid, bld_key):
        """Belirli bir bina kuyruğunu işle (kışla/ahır/atölye bağımsız)."""
        state = self._rt_village_states.get(str(vid))
        if not state or not self.browser:
            return
        units = self._rt_get_building_units(vid, bld_key)
        if not units:
            return
        if self._human_verification_required:
            return

        bst = state["buildings"][bld_key]
        bst["next_index"] = bst["next_index"] % len(units)
        unit_key, unit_name = units[bst["next_index"]]
        bst["processing"] = True
        csrf = self._game_data.get("csrf", "")
        vid_str = str(vid)
        js_global = "__tw_rt_" + vid_str + "_" + bld_key

        row = state["row"]
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
        self._rt_poll_building(vid, bld_key, js_global, unit_key, unit_name, 0)

    def _rt_poll_building(self, vid, bld_key, js_global, unit_key, unit_name, attempt):
        state = self._rt_village_states.get(str(vid))
        bld_label = self._RT_BUILDINGS.get(bld_key, {}).get("label", bld_key)

        if self._human_verification_required:
            self._rt_abort_building_poll_for_human(vid, bld_key, js_global)
            return

        if attempt > 80 or not state:
            if state:
                bst = state["buildings"].get(bld_key, {})
                bst["processing"] = False
                bst["next_fire"] = __import__("time").time() + 15
                row = state.get("row")
                if row:
                    row.setText(4, f"[{bld_label}] Zaman aşımı — 15sn sonra tekrar")
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
                if self._human_verification_required:
                    self._rt_abort_building_poll_for_human(vid, bld_key, js_global)
                    return
                QTimer.singleShot(350, lambda: self._rt_poll_building(
                    vid, bld_key, js_global, unit_key, unit_name, attempt + 1))
                return

            self.browser.page().runJavaScript("window['" + js_global + "'] = null;")
            bst = st["buildings"].get(bld_key, {})
            bst["processing"] = False
            row = st.get("row")

            import time
            now = time.time()

            if result_str.startswith("TRAINED|"):
                build_time_sec = 0.0
                try:
                    build_time_sec = float(result_str.split("|")[1])
                except (IndexError, ValueError):
                    pass
                bld_units = self._rt_get_building_units(vid, bld_key)
                bst["next_index"] = (bst["next_index"] + 1) % max(len(bld_units), 1)
                wake_sec = build_time_sec + 2 if build_time_sec > 0 else 60
                bst["next_fire"] = now + wake_sec
                mins = int(wake_sec) // 60
                secs = int(wake_sec) % 60
                st.setdefault("bld_current", {})[bld_key] = unit_name
                if row:
                    row.setText(3, f"{mins:02d}:{secs:02d}")
                    row.setText(4, f"[{bld_label}] ✅ {unit_name} — {mins:02d}:{secs:02d}")
                    row.setForeground(4, QColor("#66cc66" if getattr(self, "_dark_mode", False) else "#228822"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("trained"))
                self._add_log("ASKER", "success",
                    f"✅ [{bld_label}] {unit_name} ×1 → köy {vid} — ~{mins}dk {secs}sn")

            elif result_str.startswith("BUSY|"):
                remain = 60
                try:
                    remain = int(result_str.split("|")[1])
                except (IndexError, ValueError):
                    pass
                bst["next_fire"] = now + remain + 2
                mins = remain // 60
                secs = remain % 60
                if row:
                    row.setText(4, f"[{bld_label}] ⏳ Kuyruk dolu — {mins:02d}:{secs:02d}")
                    row.setForeground(4, QColor("#cc9933" if getattr(self, "_dark_mode", False) else "#aa6600"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("busy"))
                self._add_log("ASKER", "info",
                    f"[{bld_label}] Köy {vid} kuyruk dolu — {mins}dk {secs}sn bekle")

            elif result_str.startswith("BLOCKED|") or result_str.startswith("NO_UNIT|"):
                import random as _rnd2
                bld_units = self._rt_get_building_units(vid, bld_key)
                bst["next_index"] = (bst["next_index"] + 1) % max(len(bld_units), 1)

                is_no_unit = result_str.startswith("NO_UNIT|")
                if is_no_unit:
                    wait_sec = _rnd2.randint(3300, 3900)
                    reason = "bu dünyada mevcut değil"
                else:
                    wait_sec = _rnd2.randint(540, 660)
                    reason = "hammadde/farm yetersiz"

                bst["next_fire"] = now + wait_sec
                mins = wait_sec // 60
                secs = wait_sec % 60
                if row:
                    row.setText(4, f"[{bld_label}] ⏳ {unit_name} — {reason} ({mins:02d}:{secs:02d})")
                    row.setForeground(4, QColor("#cc9933" if getattr(self, "_dark_mode", False) else "#aa6600"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("neutral"))
                self._add_log("ASKER", "warn",
                    f"[{bld_label}] Köy {vid}: {unit_name} — {reason}, {mins}dk {secs}sn sonra tekrar")

            elif result_str.startswith("ERROR|"):
                msg = result_str[6:]
                bst["next_fire"] = now + 20
                if row:
                    row.setText(4, f"[{bld_label}] Hata: {msg[:50]}")
                    row.setForeground(4, QColor("#ff6666" if getattr(self, "_dark_mode", False) else "#cc4444"))
                    for c in range(5):
                        row.setBackground(c, self._rt_bg("error"))
                self._add_log("ASKER", "error", f"[{bld_label}] Köy {vid} hata: {msg}")

            else:
                bst["next_fire"] = now + 15

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

        self.incomings_auto_tag_cb = QCheckBox("Otomatik etiketleme")
        self.incomings_auto_tag_cb.setCursor(Qt.PointingHandCursor)
        self.incomings_auto_tag_cb.setToolTip(
            "Açıkken 60–80 sn aralıkla liste sessizce yenilenir; etiketsiz saldırı/destek "
            "satırlarına tahmini en yavaş birim etiketi AJAX ile yazılır. "
            "Tarayıcı sayfası değiştirilmez."
        )
        self.incomings_auto_tag_cb.setChecked(False)
        self.incomings_auto_tag_cb.toggled.connect(self._incomings_on_auto_tag_toggled)
        bar.addWidget(self.incomings_auto_tag_cb)

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

        self.incomings_foot = QLabel(
            "İlk sayfadaki gelen komutlar listelenir. «Otomatik etiketleme» açıkken 60–80 saniye "
            "aralığında liste sessizce yenilenir (loga yazılmaz). Mesafe ve yol süresinden tahmini "
            "en yavaş birim «Komut / etiket» ve «Tür» sütunlarında gösterilir; oyun içi komut etiketi "
            "boş olan saldırı/desteklerde aynı tahmin otomatik kaydedilir (elle yazılmış etiketlere "
            "dokunulmaz). Kapalıyken yalnızca «Gelenleri Yükle» ile manuel güncelleme yapılır."
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

    def _incomings_on_auto_tag_toggled(self, checked: bool) -> None:
        """Otomatik etiketleme açık/kapalı: zamanlayıcıyı başlat veya durdur."""
        if checked:
            self._incomings_schedule_next_auto_refresh()
            QTimer.singleShot(2000, self._incomings_auto_refresh_tick)
        else:
            self._incomings_auto_timer.stop()
            self._incomings_pending_auto_label = False
            self._incomings_reschedule_after_this_fetch = False

    def _incomings_schedule_next_auto_refresh(self):
        """60–80 sn sonra bir sonraki sessiz yenilemeyi tetikle (tek atış)."""
        if not getattr(self, "incomings_auto_tag_cb", None) or not self.incomings_auto_tag_cb.isChecked():
            if getattr(self, "_incomings_auto_timer", None):
                self._incomings_auto_timer.stop()
            return
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
        if not getattr(self, "incomings_auto_tag_cb", None) or not self.incomings_auto_tag_cb.isChecked():
            self._incomings_after_fetch_cycle_cleanup()
            return
        if getattr(self, "_human_verification_required", False):
            self._incomings_schedule_next_auto_refresh()
            return
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
        QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._incomings_poll_load(0))

    def _incomings_poll_load(self, attempt):
        max_attempts = 48
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
                QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._incomings_poll_load(attempt + 1))
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

        idx = self.tabs.addTab(tab, "🏗️ Bina Genel Bakış")
        self.tabs.tabBar().setTabVisible(idx, False)
        self._tab_idx_buildings_ov = idx

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
        QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._buildings_overview_poll_load(0))

    def _buildings_overview_poll_load(self, attempt):
        max_attempts = 48
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
                QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._buildings_overview_poll_load(attempt + 1))
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
        QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._reports_poll_load(0))

    def _reports_poll_load(self, attempt):
        """fetch tamamlanana kadar window.__tw_reports_fetch oku (Promise → runJavaScript uyumsuzluğu)."""
        max_attempts = 48
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
                QTimer.singleShot(self.TW_JS_POLL_MS, lambda: self._reports_poll_load(attempt + 1))
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
        self.settings_tg_chat_id.setText(_tw_resolved_telegram_chat_id(_cfg, self._settings))
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
        self.settings_tg_help = QLabel(
            "Botu gruba ekleyin; grup için chat_id'yi @userinfobot veya getUpdates ile alın. "
            "Token'ı paylaşmayın. Mesaj: oyuncu adı, dünya, tespit türü. "
            "Ayarları Kaydet sonrası otomatik uyarılar çalışır. "
            "SSL hatası alırsanız: üstteki doğrulamaıyı atla'yı açıp Kaydet."
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

        bright_group = QGroupBox("Bright Data — Web Unlocker (deneme)")
        bright_lay = QFormLayout()
        bright_lay.setSpacing(6)
        self.settings_bright_enable_cb = QCheckBox(
            "Bright Web Unlocker ayarlarını kullan (şimdilik yalnızca test butonu; otomatik CAPTCHA yok)"
        )
        self.settings_bright_enable_cb.setChecked(
            self._settings.value("bright/enabled", False, type=bool)
        )
        bright_lay.addRow(self.settings_bright_enable_cb)
        self.settings_bright_token = QLineEdit()
        self.settings_bright_token.setText(
            (self._settings.value("bright/api_token", "") or "").strip()
        )
        self.settings_bright_token.setEchoMode(QLineEdit.Password)
        self.settings_bright_token.setPlaceholderText("Bright — API anahtarı (Bearer)")
        bright_lay.addRow("API token:", self.settings_bright_token)
        self.settings_bright_zone = QLineEdit()
        self.settings_bright_zone.setText(
            (self._settings.value("bright/zone", "") or "").strip()
        )
        self.settings_bright_zone.setPlaceholderText("Web Unlocker zone adı (panel)")
        bright_lay.addRow("Zone:", self.settings_bright_zone)
        self.settings_bright_test_url = QLineEdit()
        self.settings_bright_test_url.setText(
            (self._settings.value("bright/test_url", "") or "").strip()
            or BRIGHT_DEFAULT_TEST_URL
        )
        self.settings_bright_test_url.setPlaceholderText("Test URL (varsayılan: Bright duman sayfası)")
        bright_lay.addRow("Test URL:", self.settings_bright_test_url)
        self.settings_bright_format = QComboBox()
        for lab, val in [("raw (metin)", "raw"), ("json", "json")]:
            self.settings_bright_format.addItem(lab, val)
        fmt_saved = (self._settings.value("bright/format", "raw") or "raw").strip().lower()
        for i in range(self.settings_bright_format.count()):
            if self.settings_bright_format.itemData(i) == fmt_saved:
                self.settings_bright_format.setCurrentIndex(i)
                break
        bright_lay.addRow("Yanıt formatı:", self.settings_bright_format)
        self.settings_bright_insecure_ssl_cb = QCheckBox(
            "Kurumsal ağ: Bright API için SSL doğrulamasını atla"
        )
        self.settings_bright_insecure_ssl_cb.setChecked(
            self._settings.value("bright/insecure_ssl", False, type=bool)
        )
        self.settings_bright_insecure_ssl_cb.setToolTip(
            "Telegram’daki seçenekle aynı mantık; yalnızca api.brightdata.com isteği için."
        )
        bright_lay.addRow(self.settings_bright_insecure_ssl_cb)
        self.settings_bright_help = QLabel(
            "Önce «Bright isteği test et» ile zone + token doğrula (Bright’ın geo.brdtest duman URL’si veya "
            "kendi URL’n). Bu, gömülü Chromium’daki oyun oturumundan bağımsız bir sunucu isteğidir; "
            "CAPTCHA’yı tek başına ‘çözdürmez’ — ileride çerez/header köprüsü veya farklı Bright ürünü gerekir."
        )
        self.settings_bright_help.setWordWrap(True)
        self.settings_bright_help.setObjectName("settingsProxyHelp")
        bright_lay.addRow(self.settings_bright_help)
        self.settings_bright_test_btn = QPushButton("Bright isteği test et")
        self.settings_bright_test_btn.setCursor(Qt.PointingHandCursor)
        self.settings_bright_test_btn.clicked.connect(self._on_settings_bright_test)
        bright_lay.addRow(self.settings_bright_test_btn)
        bright_group.setLayout(bright_lay)
        layout.addWidget(bright_group)

        game_data_group = QGroupBox("Oyun verisi")
        game_data_lay = QVBoxLayout()
        game_data_lay.setSpacing(6)
        self.refresh_btn = QPushButton("🔄 Verileri Yenile")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setToolTip(
            "Tüm köy listesi, birlikler, kaynaklar ve bina seviyelerini oyundan yeniden çeker."
        )
        self.refresh_btn.clicked.connect(
            lambda: self._scrape_game_data(force_troops_refresh=True)
        )
        game_data_lay.addWidget(self.refresh_btn)
        self.refresh_troops_bulk_btn = QPushButton("⚔ Birlikleri toplu oku")
        self.refresh_troops_bulk_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_troops_bulk_btn.setToolTip(
            "Genel Bakış → Birlikler (overview_villages&mode=units) sayfasından tüm köylerin "
            "askerlerini arka planda çeker. Tarayıcıda Birlikler ekranındayken otomatik de tetiklenir."
        )
        self.refresh_troops_bulk_btn.clicked.connect(self._scrape_troops_bulk)
        game_data_lay.addWidget(self.refresh_troops_bulk_btn)
        game_data_hint = QLabel(
            "Oyuna girdikten sonra veriler ~12 sn içinde otomatik çekilir; "
            "hemen güncellemek için bu düğmeyi kullanın."
        )
        game_data_hint.setWordWrap(True)
        game_data_hint.setObjectName("settingsProxyHelp")
        game_data_lay.addWidget(game_data_hint)
        game_data_group.setLayout(game_data_lay)
        layout.addWidget(game_data_group)

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
        self._human_verification_required = False
        self._botprot_hidden_hint = False
        self._botprot_last_parts = []
        self._botprot_fast_poll_until = 0.0
        self._update_botprot_ui()
        self._add_log("SİSTEM", "success", "Bot başlatıldı!")
        self._update_status()

        # Tarayıcı sekmesine geç
        self.tabs.setCurrentIndex(0)

        # Dünya ayarlarını sıfırla
        self._reset_world_context()
        self._game_data = {}
        self._tw_post_login_scrape_scheduled = False

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
                if not getattr(self, "_tw_post_login_scrape_scheduled", False):
                    self._tw_post_login_scrape_scheduled = True
                    self._add_log(
                        "VERİ",
                        "info",
                        "Veriler ~12 sn sonra bir kez otomatik çekilecek; bu sürede tarayıcıda gezinebilirsiniz. "
                        "Hemen güncellemek için Ayarlar sekmesinde «Verileri Yenile» kullanın.",
                    )
                    QTimer.singleShot(12000, self._tw_scrape_once_if_in_game)
            QTimer.singleShot(300, self._poll_bot_protection)
            QTimer.singleShot(100, self._schedule_village_change_troops_refresh)
            QTimer.singleShot(200, lambda: self._refresh_active_village_troops_fast())
            if self._url_is_units_overview(current_url):
                self._schedule_units_overview_scrape()
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
            self._reset_world_context()
            self.browser.navigate(full_url)

    # ── DÜNYA PROFİLİ (WorldContext) ─────────

    def _reset_world_context(self) -> None:
        """Dünya değişimi / bot başlat-durdur: merkezi dünya profilini sıfırla."""
        self._world_ctx = WorldContext()
        self._world_settings_fetched = False
        self._unit_speeds_fetched = False
        self._world_speed_from_settings = False
        self._trusted_world_speed = None
        self._trusted_unit_speed = None
        self.SA_UNIT_DEFS = list(DEFAULT_UNIT_DEFS)
        if hasattr(self, "sa_unit_frames"):
            self._sync_sa_unit_visibility()

    def _sync_speed_flags_to_legacy(self) -> None:
        """WorldContext hız bayraklarını eski _trusted_* alanlarıyla senkron tut."""
        ctx = self._world_ctx
        self._world_speed_from_settings = bool(ctx.speeds_verified)
        if ctx.speeds_verified:
            self._trusted_world_speed = ctx.world_speed
            self._trusted_unit_speed = ctx.unit_speed

    def _active_unit_defs(self):
        """Bu dünyada aktif birim listesi (game_data.units veya yedek)."""
        ctx = self._world_ctx
        if ctx.units:
            return [
                (k, UNIT_LABELS_TR.get(k, k[:3].title()))
                for k in ctx.units
            ]
        return list(DEFAULT_UNIT_DEFS)

    def _sa_sendable_unit_defs(self):
        """Ordu gönder kuyruğu / form — milis hariç."""
        return sa_sendable_unit_defs(self.SA_UNIT_DEFS)

    def _get_unit_travel_speed(self, unit_key: str) -> float:
        """Birim yolculuk hızı (dk/kare); sunucu verisi yoksa DEFAULT_UNIT_SPEEDS."""
        ctx = self._world_ctx
        raw = (ctx.unit_speeds or {}).get(unit_key)
        if raw is not None:
            try:
                v = float(raw)
                if v > 0:
                    default = float(DEFAULT_UNIT_SPEEDS.get(unit_key, 18))
                    # get_unit_info / UnitPopup bazen dk/kare yerine
                    # ws*us/(dk*60) ≈ 1/yol_süresi(sn) oranı döndürür (log: spy≈0.00189 → 529sn).
                    if v < 0.15 and default >= 1.0:
                        ws, us = self._sa_get_travel_speed_factors()
                        v = (ws * us) / (v * 60.0)
                    return v
            except (TypeError, ValueError):
                pass
        return float(DEFAULT_UNIT_SPEEDS.get(unit_key, 18))

    def _sync_sa_unit_visibility(self) -> None:
        """Ana sekmedeki birim kutularını dünyanın birim listesine göre göster/gizle."""
        frames = getattr(self, "sa_unit_frames", None)
        if not frames:
            return
        active = {k for k, _ in self._sa_sendable_unit_defs()}
        if not self._world_ctx.units:
            active = {k for k, _ in sa_sendable_unit_defs(DEFAULT_UNIT_DEFS)}
        for key, frame in frames.items():
            frame.setVisible(key in active)

    def _apply_world_context(self, data: dict) -> None:
        """Scrape veya ayar fetch sonrası merkezi dünya profilini güncelle."""
        if not data or not isinstance(data, dict):
            return
        ctx = self._world_ctx

        wdis = (data.get("world_display") or data.get("world") or "").strip()
        if wdis:
            ctx.world_display = wdis
            ctx.world_id = wdis
        wid = (data.get("world") or "").strip()
        if wid:
            ctx.world_id = wid

        ib = (data.get("image_base") or "").strip()
        if ib:
            ctx.image_base = ib

        units = data.get("units")
        if isinstance(units, list) and units:
            ctx.units = [str(u) for u in units if u]
        elif isinstance(units, dict) and units:
            ctx.units = [str(k) for k in units.keys() if k]

        us_map = data.get("unit_speeds")
        if isinstance(us_map, dict) and us_map:
            parsed = {}
            for k, v in us_map.items():
                try:
                    fv = float(v)
                    if fv > 0:
                        parsed[str(k)] = fv
                except (TypeError, ValueError):
                    pass
            if parsed:
                ctx.unit_speeds.update(parsed)

        def _pos_float(x):
            if x is None:
                return None
            try:
                v = float(x)
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None

        if ctx.speeds_verified:
            ws, us = ctx.world_speed, ctx.unit_speed
        else:
            ws = _pos_float(self._trusted_world_speed) or _pos_float(data.get("world_speed"))
            us = _pos_float(self._trusted_unit_speed) or _pos_float(data.get("unit_speed"))
            if ws is not None:
                ctx.world_speed = ws
            if us is not None:
                ctx.unit_speed = us

        self._game_data["world_speed"] = ctx.world_speed
        self._game_data["unit_speed"] = ctx.unit_speed
        if ctx.units:
            self._game_data["units"] = list(ctx.units)
        if ctx.unit_speeds:
            self._game_data["unit_speeds"] = dict(ctx.unit_speeds)

        self.SA_UNIT_DEFS = self._active_unit_defs()
        self._sync_sa_unit_visibility()
        self._sync_speed_flags_to_legacy()

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

            def _troops_sum(t):
                if not t or not isinstance(t, dict):
                    return 0
                s = 0
                for val in t.values():
                    try:
                        s += int(val)
                    except (TypeError, ValueError):
                        pass
                return s

            if v.get("troops_fresh"):
                if isinstance(nt, dict):
                    fresh = {}
                    for k, val in nt.items():
                        try:
                            fresh[k] = int(val)
                        except (TypeError, ValueError):
                            fresh[k] = 0
                    v["troops"] = self._sa_merge_troops_max_snob(fresh, pt)
                else:
                    v["troops"] = self._sa_merge_troops_max_snob({}, pt)
                if not v.get("group_names") and p.get("group_names"):
                    v["group_names"] = list(p.get("group_names") or [])
                continue

            if not nt or _troops_sum(nt) == 0:
                if _troops_sum(pt) > 0:
                    v["troops"] = dict(pt)
                continue
            merged = dict(pt)
            for k, val in nt.items():
                try:
                    merged[k] = int(val)
                except (TypeError, ValueError):
                    merged[k] = 0
            merged = self._sa_merge_troops_max_snob(merged, pt)
            v["troops"] = merged
            if not v.get("group_names") and p.get("group_names"):
                v["group_names"] = list(p.get("group_names") or [])

        if cur_vid is not None:
            for v in new_list:
                try:
                    if int(v.get("id", 0)) == cur_vid:
                        data["troops"] = dict(v.get("troops") or {})
                        break
                except (TypeError, ValueError):
                    continue

    def _tw_scrape_once_if_in_game(self) -> None:
        """İlk oyun girişinde tek otomatik scrape; her sayfa yüklemesinde çağrılmaz (tarayıcı donması / rate limit)."""
        if not self.is_running or not getattr(self, "browser", None):
            return
        u = self.browser.url().toString()
        if "game.php" not in u and "/overview" not in u:
            return
        self._scrape_game_data()

    def _url_is_units_overview(self, url: str) -> bool:
        u = (url or "").lower()
        return "overview_villages" in u and ("mode=units" in u or "mode%3dunits" in u)

    def _schedule_units_overview_scrape(self, delay_ms: int = 900) -> None:
        """Birlikler ekranı yüklendiğinde toplu asker okumasını gecikmeli tetikle."""
        if not self.is_running or self._login_state != "in_game":
            return
        t = getattr(self, "_units_overview_scrape_timer", None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._scrape_troops_bulk)
        self._units_overview_scrape_timer = t
        t.start(max(200, int(delay_ms)))

    def _scrape_troops_bulk(self) -> None:
        """Tüm köy birliklerini overview_villages&mode=units üzerinden toplu çek."""
        if not self.is_running or not getattr(self, "browser", None):
            return
        u = self.browser.url().toString()
        if "game.php" not in u and "/overview" not in u:
            self._add_log("VERİ", "warn", "Birlikler okunamadı: oyun sayfasında değilsiniz.")
            return
        self._add_log(
            "VERİ",
            "info",
            "Birlikler toplu okunuyor (Genel Bakış → Birlikler / XHR)...",
        )
        self._scrape_game_data(force_troops_refresh=True)

    def _schedule_village_change_troops_refresh(self) -> None:
        """Köy değişiminde hızlı game_data okuması (tam scrape yok)."""
        if not self.is_running or not getattr(self, "browser", None):
            return
        u = self.browser.url().toString()
        if "game.php" not in u and "/overview" not in u:
            return

        def on_vid(result):
            try:
                vid = int(result) if result else 0
            except (TypeError, ValueError):
                return
            if vid <= 0:
                return
            last = getattr(self, "_last_scraped_village_id", None)
            if last is not None and vid == last:
                return
            self._sa_source_user_picked = False
            self._last_active_troops_fp = None
            self._last_active_troops_vid = None
            self._troops_loading_until = time.time() + 2.0
            t = getattr(self, "_village_troops_refresh_timer", None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
            self._refresh_active_village_troops_fast()

        self.browser.page().runJavaScript(
            "(typeof game_data !== 'undefined' && game_data.village) ? game_data.village.id : 0",
            on_vid,
        )

    def _sa_sync_source_combo_to_village(self, village_id) -> None:
        """Tarayıcıdaki aktif köy ile Ordu Gönder kaynak seçicisini eşle."""
        if not hasattr(self, "sa_source_combo"):
            return
        try:
            vid = int(village_id)
        except (TypeError, ValueError):
            return
        for i in range(self.sa_source_combo.count()):
            if self._sa_same_village_id(self.sa_source_combo.itemData(i), vid):
                if self.sa_source_combo.currentIndex() != i:
                    self.sa_source_combo.blockSignals(True)
                    self.sa_source_combo.setCurrentIndex(i)
                    self.sa_source_combo.blockSignals(False)
                self._sa_on_source_changed(i)
                return

    def _url_is_troops_sensitive(self, url: str) -> bool:
        """Place, kışla, eğitim veya köy özeti ekranlarında birlik değişimi olası."""
        if not url:
            return False
        u = url.lower()
        if "game.php" not in u and "/overview" not in u:
            return False
        screen = ""
        try:
            qs = parse_qs(urlparse(url).query)
            screen = (qs.get("screen") or [""])[0].lower()
        except Exception:
            pass
        if not screen:
            for marker in ("screen=place", "screen=barracks", "screen=train", "screen=overview_villages"):
                if marker in u:
                    return True
            return False
        return screen in ("place", "barracks", "train", "overview_villages")

    def _active_village_troops_read_js(self) -> str:
        """Aktif köy birlikleri: game_data.village.units, köy özeti widget, VillageOverview."""
        return r"""
        (function() {
            if (typeof game_data === 'undefined' || !game_data.village) {
                return JSON.stringify({status: 'NO_DATA'});
            }
            var gv = game_data.village;
            var vid = parseInt(gv.id, 10) || 0;
            if (!vid) return JSON.stringify({status: 'NO_DATA'});
            var unitNames = game_data.units || [];
            function sumTroops(troops) {
                if (!troops || typeof troops !== 'object') return 0;
                var s = 0, tk;
                for (tk in troops) {
                    if (!Object.prototype.hasOwnProperty.call(troops, tk)) continue;
                    s += parseInt(troops[tk], 10) || 0;
                }
                return s;
            }
            function unitsObjectToTroops(uobj) {
                var troops = {};
                if (uobj == null) return troops;
                if (Array.isArray(uobj)) {
                    var ui;
                    for (ui = 0; ui < unitNames.length && ui < uobj.length; ui++) {
                        if (unitNames[ui]) troops[unitNames[ui]] = parseInt(uobj[ui], 10) || 0;
                    }
                    return troops;
                }
                if (typeof uobj !== 'object') return troops;
                var k, raw, n;
                for (k in uobj) {
                    if (!Object.prototype.hasOwnProperty.call(uobj, k)) continue;
                    raw = uobj[k];
                    if (raw != null && typeof raw === 'object' && raw.count != null) raw = raw.count;
                    n = parseInt(raw, 10);
                    if (!isNaN(n)) troops[k] = n;
                }
                return troops;
            }
            function mergeTroopsMax(into, from) {
                if (!from || typeof from !== 'object') return into;
                var k, n, cur;
                for (k in from) {
                    if (!Object.prototype.hasOwnProperty.call(from, k)) continue;
                    n = parseInt(from[k], 10) || 0;
                    cur = parseInt(into[k], 10) || 0;
                    if (n > cur) into[k] = n;
                }
                return into;
            }
            function readSnobFromUnitsObject(uobj) {
                if (uobj == null) return NaN;
                if (Array.isArray(uobj)) {
                    var si = -1, zi;
                    for (zi = 0; zi < unitNames.length; zi++) {
                        if (unitNames[zi] === 'snob') { si = zi; break; }
                    }
                    if (si >= 0 && si < uobj.length) {
                        var an = parseInt(uobj[si], 10);
                        if (!isNaN(an)) return an;
                    }
                    return NaN;
                }
                if (typeof uobj !== 'object') return NaN;
                var direct = ['snob', 'noble', 'nobleman', 'snobs', 'unit_snob'];
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
                    if (lk.indexOf('snob') < 0 && lk.indexOf('noble') < 0 && lk.indexOf('misyoner') < 0)
                        continue;
                    raw = uobj[k];
                    if (raw != null && typeof raw === 'object' && raw.count != null) raw = raw.count;
                    n = parseInt(raw, 10);
                    if (!isNaN(n)) return n;
                }
                return NaN;
            }
            function patchSnob(troops, uobj) {
                if (!uobj) return troops;
                var sn = readSnobFromUnitsObject(uobj);
                if (!isNaN(sn)) {
                    troops.snob = Math.max(parseInt(troops.snob, 10) || 0, sn);
                }
                return troops;
            }
            function readTroopsFromOverviewWidget() {
                var out = {};
                var table = document.getElementById('unit_overview_table');
                if (!table) return out;
                var rows = table.querySelectorAll('tr.all_unit');
                if (!rows.length) rows = table.querySelectorAll('tr.home_unit');
                var ri, row, link, strong, key, txt, m, n;
                for (ri = 0; ri < rows.length; ri++) {
                    row = rows[ri];
                    link = row.querySelector('a.unit_link[data-unit]');
                    strong = row.querySelector('strong[data-count]');
                    if (!link && !strong) continue;
                    key = (link && link.getAttribute('data-unit')) ||
                        (strong && strong.getAttribute('data-count'));
                    if (!key) continue;
                    n = NaN;
                    if (strong) {
                        txt = (strong.textContent || '').trim();
                        m = txt.match(/^(\d[\d.]*)/);
                        if (m) {
                            n = parseInt(String(m[1]).replace(/\./g, ''), 10);
                        }
                    }
                    if (isNaN(n)) n = 0;
                    out[key] = n;
                }
                return out;
            }
            function readTroopsFromVillageOverviewUnits() {
                var out = {};
                if (typeof VillageOverview === 'undefined' || !VillageOverview.units) return out;
                var packs = VillageOverview.units;
                var pi, pack, k, raw, n, cur;
                for (pi = 0; pi < packs.length; pi++) {
                    pack = packs[pi];
                    if (!pack || typeof pack !== 'object') continue;
                    for (k in pack) {
                        if (!Object.prototype.hasOwnProperty.call(pack, k)) continue;
                        raw = pack[k];
                        if (raw != null && typeof raw === 'object') continue;
                        n = parseInt(raw, 10);
                        if (isNaN(n)) continue;
                        cur = parseInt(out[k], 10) || 0;
                        if (n > cur) out[k] = n;
                    }
                }
                return out;
            }
            function readTroopsFromPlaceInputs() {
                var out = {};
                var ui, un, inp, v;
                for (ui = 0; ui < unitNames.length; ui++) {
                    un = unitNames[ui];
                    if (!un) continue;
                    inp = document.querySelector(
                        '#units_display input[name="' + un + '"], ' +
                        '#show_units input[name="' + un + '"], ' +
                        'input.units_input[name="' + un + '"]'
                    );
                    if (!inp) continue;
                    v = parseInt(inp.value, 10);
                    if (!isNaN(v)) out[un] = v;
                }
                return out;
            }
            var troops = unitsObjectToTroops(gv.units);
            if (sumTroops(troops) === 0 && unitNames.length) {
                var uk, tmp = {};
                for (uk = 0; uk < unitNames.length; uk++) {
                    var un = unitNames[uk];
                    if (un && gv[un] != null) tmp[un] = parseInt(gv[un], 10) || 0;
                }
                if (sumTroops(tmp) > 0) troops = tmp;
            }
            troops = mergeTroopsMax(troops, readTroopsFromOverviewWidget());
            troops = mergeTroopsMax(troops, readTroopsFromVillageOverviewUnits());
            troops = mergeTroopsMax(troops, readTroopsFromPlaceInputs());
            troops = patchSnob(troops, gv.units);
            if (gv.snob != null) {
                var gn = parseInt(gv.snob, 10);
                if (!isNaN(gn)) {
                    troops.snob = Math.max(parseInt(troops.snob, 10) || 0, gn);
                }
            }
            return JSON.stringify({
                status: 'OK',
                village_id: vid,
                troops: troops,
                fingerprint: JSON.stringify(troops)
            });
        })();
        """

    def _refresh_active_village_troops_fast(self, retries=0, max_retries=4) -> None:
        """Aktif köy birliklerini game_data'dan oku; sayfa hazır değilse kısa aralıklarla yeniden dene."""
        if not self.is_running or not getattr(self, "browser", None):
            return
        if self._login_state != "in_game":
            return
        u = self.browser.url().toString()
        if "game.php" not in u and "/overview" not in u:
            return

        def on_result(result):
            if not result:
                if retries < max_retries:
                    QTimer.singleShot(
                        250 + random.randint(0, 150),
                        lambda: self._refresh_active_village_troops_fast(
                            retries + 1, max_retries
                        ),
                    )
                return
            try:
                data = json.loads(str(result))
            except (json.JSONDecodeError, TypeError, ValueError):
                if retries < max_retries:
                    QTimer.singleShot(
                        250 + random.randint(0, 150),
                        lambda: self._refresh_active_village_troops_fast(
                            retries + 1, max_retries
                        ),
                    )
                return
            if data.get("status") != "OK":
                if retries < max_retries:
                    QTimer.singleShot(
                        250 + random.randint(0, 150),
                        lambda: self._refresh_active_village_troops_fast(
                            retries + 1, max_retries
                        ),
                    )
                return
            try:
                vid = int(data.get("village_id", 0))
            except (TypeError, ValueError):
                return
            if vid <= 0:
                return
            troops = data.get("troops")
            if not isinstance(troops, dict):
                return
            fp = data.get("fingerprint") or ""
            if fp == getattr(self, "_last_active_troops_fp", None) and vid == getattr(
                self, "_last_active_troops_vid", None
            ):
                return
            self._last_active_troops_fp = fp
            self._last_active_troops_vid = vid
            try:
                self._last_scraped_village_id = vid
            except (TypeError, ValueError):
                pass
            self._troops_loading_until = 0.0
            self._apply_active_village_troops(vid, troops)

        self.browser.page().runJavaScript(self._active_village_troops_read_js(), on_result)

    def _poll_active_village_troops(self) -> None:
        """Aktif köy birliklerini yerel game_data'dan oku; değiştiyse UI güncelle."""
        if not self.is_running or not getattr(self, "browser", None):
            return
        if self._login_state != "in_game":
            return
        u = self.browser.url().toString()
        if "game.php" not in u and "/overview" not in u:
            return

        def on_result(result):
            if not result:
                return
            try:
                data = json.loads(str(result))
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if data.get("status") != "OK":
                return
            try:
                vid = int(data.get("village_id", 0))
            except (TypeError, ValueError):
                return
            if vid <= 0:
                return
            troops = data.get("troops")
            if not isinstance(troops, dict):
                return
            fp = data.get("fingerprint") or ""
            if fp == getattr(self, "_last_active_troops_fp", None) and vid == getattr(
                self, "_last_active_troops_vid", None
            ):
                return
            self._last_active_troops_fp = fp
            self._last_active_troops_vid = vid
            self._apply_active_village_troops(vid, troops)

        self.browser.page().runJavaScript(self._active_village_troops_read_js(), on_result)

    def _apply_active_village_troops(self, village_id: int, troops: dict) -> None:
        """Aktif köy stokunu _game_data ve UI'a yaz (replace, Math.max yok)."""
        if not getattr(self, "_game_data", None):
            self._game_data = {}
        gd = self._game_data
        clean = {}
        for k, val in (troops or {}).items():
            try:
                clean[k] = int(val)
            except (TypeError, ValueError):
                clean[k] = 0
        if self._sa_troops_sum(clean) == 0:
            alt = self._sa_resolve_village_troops(village_id)
            if self._sa_troops_sum(alt) > 0:
                clean = dict(alt)
        prev_troops = None
        for v in gd.get("all_villages") or []:
            try:
                if int(v.get("id", 0)) == village_id:
                    prev_troops = v.get("troops")
                    clean = self._sa_merge_troops_max_snob(clean, prev_troops)
                    break
            except (TypeError, ValueError):
                continue
        gd["troops"] = dict(clean)
        updated = False
        for v in gd.get("all_villages") or []:
            try:
                if int(v.get("id", 0)) == village_id:
                    if self._sa_troops_sum(clean) == 0 and self._sa_troops_sum(v.get("troops")) > 0:
                        clean = dict(v.get("troops") or {})
                        gd["troops"] = dict(clean)
                    v["troops"] = dict(clean)
                    v["troops_fresh"] = True
                    updated = True
                    break
            except (TypeError, ValueError):
                continue
        if not updated:
            gv = gd.get("village") or {}
            entry = {"id": village_id, "troops": dict(clean), "troops_fresh": True}
            if self._sa_same_village_id(gv.get("id"), village_id):
                for k in ("name", "x", "y", "coord", "points"):
                    if gv.get(k) is not None:
                        entry[k] = gv[k]
            gd.setdefault("all_villages", []).append(entry)
        gv = gd.get("village") or {}
        if self._sa_same_village_id(gv.get("id"), village_id):
            gv["troops"] = dict(clean)
        self._update_troops(gd)
        self._update_troop_available()
        if hasattr(self, "all_villages_tree"):
            self._update_villages_list(gd)
        if not getattr(self, "_sa_source_user_picked", False):
            self._sa_sync_source_combo_to_village(village_id)

    def _schedule_next_troops_watch_poll(self) -> None:
        """Adaptif aktif köy birlik izleme: hassas ekranda 6–10 sn, normalde 18–28 sn."""
        in_game = (
            self.is_running
            and self._login_state == "in_game"
            and self.browser
            and ("game.php" in self.browser.url().toString() or "/overview" in self.browser.url().toString())
        )
        if in_game and self._url_is_troops_sensitive(self.browser.url().toString()):
            delay_ms = random.randint(6000, 10000)
        elif in_game:
            delay_ms = random.randint(18000, 28000)
        else:
            delay_ms = random.randint(20000, 30000)
        QTimer.singleShot(delay_ms, self._troops_watch_poll_reschedule)

    def _troops_watch_poll_reschedule(self) -> None:
        if (
            self.is_running
            and self._login_state == "in_game"
            and self.browser
            and ("game.php" in self.browser.url().toString() or "/overview" in self.browser.url().toString())
        ):
            self._poll_active_village_troops()
        self._schedule_next_troops_watch_poll()

    def _scrape_game_data(self, *, force_troops_refresh: bool = False):
        """game_data JS değişkeninden ve DOM'dan tüm verileri çek."""
        force_troops_js = "true" if force_troops_refresh else "false"
        scrape_js = """
        (function() {
            var forceTroopsRefresh = """ + force_troops_js + """;
            if (forceTroopsRefresh) {
                try { delete window.__tw_bot_units_cache; } catch(e) {}
            }

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
            if (!Array.isArray(unitNames)) {
                if (unitNames && typeof unitNames === 'object') {
                    unitNames = Object.keys(unitNames);
                } else {
                    unitNames = [];
                }
            }

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
                var n = readUnitCountFromCell(td);
                return n > 0 || (td.getAttribute('data-unit-count') != null) ? n : NaN;
            }

            function readUnitCountFromCell(td) {
                if (!td) return 0;
                var dc = td.getAttribute('data-unit-count');
                if (dc != null && String(dc).length) {
                    var n0 = parseInt(dc, 10);
                    if (!isNaN(n0)) return n0;
                }
                var inner = td.querySelector('[data-unit-count]');
                if (inner && inner !== td) {
                    dc = inner.getAttribute('data-unit-count');
                    if (dc != null && String(dc).length) {
                        var n1 = parseInt(dc, 10);
                        if (!isNaN(n1)) return n1;
                    }
                }
                var txt = String(td.textContent || '').replace(/[^0-9\\-]/g, '');
                if (txt.length) {
                    var n2 = parseInt(txt, 10);
                    if (!isNaN(n2)) return n2;
                }
                return 0;
            }

            function parsePointsText(raw) {
                if (raw == null || raw === '') return 0;
                var s = String(raw).replace(/\\u00a0/g, '').replace(/\\s+/g, '').trim();
                if (!s) return 0;
                if (/^\\d{1,3}(\\.\\d{3})+$/.test(s)) {
                    return parseInt(s.replace(/\\./g, ''), 10) || 0;
                }
                if (/^\\d{1,3}(,\\d{3})+$/.test(s)) {
                    return parseInt(s.replace(/,/g, ''), 10) || 0;
                }
                var digits = s.replace(/[^0-9]/g, '');
                if (!digits) return 0;
                var n = parseInt(digits, 10);
                return isNaN(n) ? 0 : n;
            }

            function detectPointsColumnIndex(tableEl) {
                if (!tableEl) return -1;
                var ths = tableEl.querySelectorAll('thead th');
                var hi, ht, ha;
                for (hi = 0; hi < ths.length; hi++) {
                    ht = (ths[hi].textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (ht === 'puan' || ht === 'points' || ht === 'punkte' || ht === 'punti' || ht === 'point') {
                        return hi;
                    }
                    ha = ths[hi].querySelector('a[href*="order=points"]');
                    if (ha) return hi;
                }
                return -1;
            }

            function parseProductionTableRows(tableEl) {
                var out = [];
                if (!tableEl) return out;
                var ptsCol = detectPointsColumnIndex(tableEl);
                if (ptsCol < 0) ptsCol = 2;
                var qNodes = tableEl.querySelectorAll('.quickedit-vn[data-id]');
                qNodes.forEach(function(qe) {
                    var row = qe.closest ? qe.closest('tr') : null;
                    if (!row) return;
                    var vill = {};
                    vill.id = parseInt(qe.getAttribute('data-id'), 10) || 0;
                    var label = row.querySelector('.quickedit-label');
                    if (label) {
                        vill.name = label.getAttribute('data-text') || label.textContent.trim();
                        var fullText = (label.textContent || '').trim();
                        var coordMatch = fullText.match(/[(](\\d+)[|](\\d+)[)]/);
                        if (coordMatch) {
                            vill.x = parseInt(coordMatch[1], 10);
                            vill.y = parseInt(coordMatch[2], 10);
                        }
                    }
                    var tds = row.querySelectorAll('td');
                    if (tds.length > ptsCol) {
                        var pts = parsePointsText(tds[ptsCol].textContent);
                        if (pts > 0) vill.points = pts;
                    }
                    if (vill.id) out.push(vill);
                });
                return out;
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
                    if (!cells.length) cells = row.querySelectorAll('td[data-unit-count]');
                    if (!cells.length) cells = row.querySelectorAll('td[class*="unit-item"]');
                    // #units_table bazı dünyalarda td.unit-item kullanmaz — köy sütunu sonrası sayı hücreleri
                    if (!cells.length && unitNames.length > 0) {
                        var allTds = row.querySelectorAll('td');
                        var startIdx = 1;
                        var endIdx = allTds.length;
                        if (endIdx > startIdx && allTds[endIdx - 1].querySelector('a[href*="screen=place"], a[href*="screen=info_village"]')) {
                            endIdx -= 1;
                        }
                        if (endIdx - startIdx === unitNames.length) {
                            var tdi, tmpCells = [];
                            for (tdi = startIdx; tdi < endIdx; tdi++) tmpCells.push(allTds[tdi]);
                            cells = tmpCells;
                        }
                    }
                    // #units_table: td.unit-item sayısı game_data.units ile birebir aynı — başlık img eşlemesi
                    // hatalı olsa bile snob dahil tüm sütunlar oyun sırasına göre okunur (yanlış yüksek snob düzeltmesi).
                    if (unitNames.length > 0 && cells.length === unitNames.length) {
                        var ti;
                        for (ti = 0; ti < unitNames.length; ti++) {
                            if (unitNames[ti]) {
                                vill.troops[unitNames[ti]] = readUnitCountFromCell(cells[ti]);
                            }
                        }
                    } else if (colKeys && colKeys.length === cells.length) {
                        var ci, uk;
                        for (ci = 0; ci < cells.length; ci++) {
                            uk = colKeys[ci];
                            if (uk) vill.troops[uk] = readUnitCountFromCell(cells[ci]);
                        }
                    } else {
                        for (var i = 0; i < cells.length && i < unitNames.length; i++) {
                            vill.troops[unitNames[i]] = readUnitCountFromCell(cells[i]);
                        }
                    }
                    var snDom = readSnobFromTroopRow(row);
                    if (!isNaN(snDom)) vill.troops.snob = snDom;
                    var tbody = row.closest('tbody');
                    vill.selected = row.classList.contains('selected')
                        || (tbody && tbody.classList && tbody.classList.contains('selected'));
                    vill.troops_fresh = true;
                    if (vill.id) {
                        out.push(vill);
                    }
                });
                return out;
            }

            function pointsFromGameDataEntry(vv, key) {
                if (vv == null) return 0;
                if (typeof vv === 'object' && !Array.isArray(vv)) {
                    var pid = parseInt(vv.id != null ? vv.id : key, 10);
                    var p1 = parsePointsText(vv.points);
                    if (p1 > 0) return p1;
                    return 0;
                }
                if (Array.isArray(vv)) {
                    var ai, pv;
                    for (ai = vv.length - 1; ai >= 0; ai--) {
                        pv = parsePointsText(vv[ai]);
                        if (pv >= 26 && pv <= 150000) return pv;
                    }
                }
                return 0;
            }

            function lookupGameDataVillage(vid) {
                if (!game_data.villages) return null;
                var vv = game_data.villages[vid] || game_data.villages[String(vid)];
                if (vv) return vv;
                if (!Array.isArray(game_data.villages)) return null;
                var jj;
                for (jj = 0; jj < game_data.villages.length; jj++) {
                    var row = game_data.villages[jj];
                    if (!row) continue;
                    var rid = Array.isArray(row) ? parseInt(row[0], 10) : parseInt(row.id, 10);
                    if (rid === vid) return row;
                }
                return null;
            }

            function enrichVillagePointsFromGameData(arr) {
                if (!arr || !arr.length || !game_data.villages) return;
                var vi, v, vid, vv, pts;
                for (vi = 0; vi < arr.length; vi++) {
                    v = arr[vi];
                    if (!v || !v.id) continue;
                    if (parsePointsText(v.points) > 0) continue;
                    vid = parseInt(v.id, 10);
                    vv = lookupGameDataVillage(vid);
                    if (!vv) continue;
                    pts = pointsFromGameDataEntry(vv, vid);
                    if (pts > 0) v.points = pts;
                }
            }

            function unitsObjectToTroops(uobj) {
                var troops = {};
                if (uobj == null) return troops;
                if (Array.isArray(uobj)) {
                    var ui;
                    for (ui = 0; ui < unitNames.length && ui < uobj.length; ui++) {
                        if (unitNames[ui]) troops[unitNames[ui]] = parseInt(uobj[ui], 10) || 0;
                    }
                    return troops;
                }
                if (typeof uobj !== 'object') return troops;
                var k, raw, n;
                for (k in uobj) {
                    if (!Object.prototype.hasOwnProperty.call(uobj, k)) continue;
                    raw = uobj[k];
                    if (raw != null && typeof raw === 'object' && raw.count != null) raw = raw.count;
                    n = parseInt(raw, 10);
                    if (!isNaN(n)) troops[k] = n;
                }
                return troops;
            }

            function sumTroops(troops) {
                if (!troops || typeof troops !== 'object') return 0;
                var s = 0, tk;
                for (tk in troops) {
                    if (!Object.prototype.hasOwnProperty.call(troops, tk)) continue;
                    s += parseInt(troops[tk], 10) || 0;
                }
                return s;
            }

            function enrichTroopsFromGameDataUnits(arr) {
                if (!arr || !arr.length) return;
                var vi, v, vid, uobj, fromGd, gv = game_data.village;
                for (vi = 0; vi < arr.length; vi++) {
                    v = arr[vi];
                    if (!v || !v.id) continue;
                    vid = parseInt(v.id, 10);
                    uobj = null;
                    fromGd = null;
                    if (gv && parseInt(gv.id, 10) === vid && gv.units)
                        uobj = gv.units;
                    else if (game_data.villages) {
                        var vv = lookupGameDataVillage(vid);
                        if (vv && vv.units) uobj = vv.units;
                    }
                    if (uobj) {
                        fromGd = unitsObjectToTroops(uobj);
                    } else if (game_data.villages) {
                        var vv2 = lookupGameDataVillage(vid);
                        if (vv2) {
                            fromGd = unitsObjectToTroops(vv2);
                            if (sumTroops(fromGd) === 0) {
                                var uk2, tmp = {};
                                for (uk2 = 0; uk2 < unitNames.length; uk2++) {
                                    var un = unitNames[uk2];
                                    if (un && vv2[un] != null) tmp[un] = parseInt(vv2[un], 10) || 0;
                                }
                                if (sumTroops(tmp) > 0) fromGd = tmp;
                            }
                        }
                    }
                    if (!fromGd || sumTroops(fromGd) === 0) continue;
                    if (v.troops_fresh === true) {
                        if (uobj) {
                            var snFresh = readSnobFromUnitsObject(uobj);
                            if (!isNaN(snFresh)) {
                                if (!v.troops) v.troops = {};
                                var curSnF = parseInt(v.troops.snob, 10) || 0;
                                v.troops.snob = Math.max(curSnF, snFresh);
                            }
                        }
                        continue;
                    }
                    if (!v.troops) v.troops = {};
                    if (sumTroops(v.troops) === 0) {
                        v.troops = fromGd;
                    } else {
                        var ck, cur;
                        for (ck in fromGd) {
                            if (!Object.prototype.hasOwnProperty.call(fromGd, ck)) continue;
                            cur = parseInt(v.troops[ck], 10) || 0;
                            v.troops[ck] = Math.max(cur, fromGd[ck]);
                        }
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
                        if (w.name && (!byId[w.id].name || byId[w.id].name === ('#' + w.id))) {
                            byId[w.id].name = w.name;
                        }
                        if (w.x != null && w.y != null) {
                            byId[w.id].x = w.x;
                            byId[w.id].y = w.y;
                        }
                        if (w.points != null && w.points !== '') {
                            var wp = (typeof w.points === 'number') ? w.points : parsePointsText(w.points);
                            if (wp > 0) byId[w.id].points = wp;
                        }
                        if (w.farm_text) byId[w.id].farm_text = w.farm_text;
                        if (w.troops_fresh === true) {
                            byId[w.id].troops = w.troops || {};
                            byId[w.id].troops_fresh = true;
                        } else {
                            byId[w.id].troops = mergeTroopDicts(byId[w.id].troops, w.troops);
                        }
                        if (w.group_names && w.group_names.length) {
                            if (!byId[w.id].group_names) byId[w.id].group_names = [];
                            var gn = byId[w.id].group_names;
                            for (var gi = 0; gi < w.group_names.length; gi++) {
                                var gnm = w.group_names[gi];
                                if (gnm && gn.indexOf(gnm) < 0) gn.push(gnm);
                            }
                        }
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
                    var ptsMiss = pointsFromGameDataEntry(vv, key);
                    arr.push({
                        id: vid,
                        name: vv.name || (Array.isArray(vv) && vv[3] ? String(vv[3]) : ('#' + vid)),
                        x: vx,
                        y: vy,
                        points: ptsMiss > 0 ? ptsMiss : 0,
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

            function parseVillageGroupCatalog(doc) {
                var groups = [];
                var seen = {};
                var html = doc.documentElement ? (doc.documentElement.innerHTML || '') : '';
                var m = html.match(/VillageGroups\\.displayGroupInfo\\s*\\(\\s*(\\{[\\s\\S]*?\\})\\s*,\\s*['"]group_list['"]\\s*\\)/);
                if (m) {
                    try {
                        var data = JSON.parse(m[1]);
                        var list = data.result || [];
                        var i;
                        for (i = 0; i < list.length; i++) {
                            var g = list[i];
                            var gid = String(g.group_id != null ? g.group_id : (g.id != null ? g.id : ''));
                            if (!gid || gid === '0' || seen[gid]) continue;
                            seen[gid] = true;
                            groups.push({
                                id: gid,
                                name: String(g.name || '').trim(),
                                type: 'static'
                            });
                        }
                    } catch (eG) {}
                }
                var items = doc.querySelectorAll('.group-menu-item[data-group-id]');
                var j;
                for (j = 0; j < items.length; j++) {
                    var el = items[j];
                    var gid2 = String(el.getAttribute('data-group-id') || '');
                    if (!gid2 || gid2 === '0' || seen[gid2]) continue;
                    var gtype = String(el.getAttribute('data-group-type') || 'static').toLowerCase();
                    var gname = String(el.textContent || '').replace(/[\\[\\]>]/g, '').trim();
                    seen[gid2] = true;
                    groups.push({ id: gid2, name: gname, type: gtype });
                }
                return groups;
            }

            function parseStaticGroupMembership(doc) {
                var out = {};
                var table = doc.getElementById('group_assign_table');
                if (!table) return out;
                var nodes = table.querySelectorAll('.quickedit-vn[data-id]');
                var i;
                for (i = 0; i < nodes.length; i++) {
                    var vid = parseInt(nodes[i].getAttribute('data-id'), 10) || 0;
                    if (!vid) continue;
                    var namesEl = doc.getElementById('assigned_groups_' + vid + '_names');
                    if (!namesEl) continue;
                    var text = String(namesEl.textContent || '').trim();
                    if (!text) continue;
                    var parts = text.split(';').map(function(s) { return s.trim(); }).filter(Boolean);
                    if (parts.length) out[vid] = parts;
                }
                return out;
            }

            function parseGroupPageVillageIds(doc) {
                var ids = [];
                var seen = {};
                doc.querySelectorAll('.quickedit-vn[data-id]').forEach(function(qe) {
                    var vid = parseInt(qe.getAttribute('data-id'), 10) || 0;
                    if (vid && !seen[vid]) {
                        seen[vid] = true;
                        ids.push(vid);
                    }
                });
                return ids;
            }

            function applyGroupNameToVillages(villages, vid, groupName) {
                if (!groupName) return;
                var vi, v, names;
                for (vi = 0; vi < villages.length; vi++) {
                    v = villages[vi];
                    if (!v || parseInt(v.id, 10) !== vid) continue;
                    if (!v.group_names) v.group_names = [];
                    names = v.group_names;
                    if (names.indexOf(groupName) < 0) names.push(groupName);
                    return;
                }
            }

            result.all_villages = [];
            var unitsTableEl = document.getElementById('units_table');
            var onUnitsOverviewPage = false;
            try {
                var _curOv = new URL(window.location.href);
                onUnitsOverviewPage = _curOv.searchParams.get('screen') === 'overview_villages'
                    && _curOv.searchParams.get('mode') === 'units';
            } catch (exOv) {}

            function paginateUnitsOverviewTable(rootDoc, baseHref, seenFetch) {
                if (!rootDoc) return;
                seenFetch = seenFetch || {};
                var tbl = rootDoc.getElementById('units_table');
                if (tbl) {
                    mergeVillagesById(result.all_villages, parseCombinedTableRows(tbl));
                }
                var pager = rootDoc.querySelectorAll('a.paged-nav-item[href*="page="]');
                var curHref = baseHref || window.location.href;
                var pi, href, absUrl, html, doc;
                for (pi = 0; pi < pager.length; pi++) {
                    href = pager[pi].getAttribute('href');
                    if (!href || href.indexOf('page=-') !== -1) continue;
                    try {
                        absUrl = new URL(href, window.location.origin).href;
                    } catch (ePg) {
                        continue;
                    }
                    if (seenFetch[absUrl]) continue;
                    seenFetch[absUrl] = true;
                    if (sameOverviewListPage(absUrl, curHref)) continue;
                    html = fetchOverviewHtmlSync(absUrl);
                    if (!html) continue;
                    doc = new DOMParser().parseFromString(html, 'text/html');
                    paginateUnitsOverviewTable(doc, absUrl, seenFetch);
                }
            }

            if (unitsTableEl) {
                var unitsSeenFetch = {};
                paginateUnitsOverviewTable(document, window.location.href, unitsSeenFetch);
                var expectedUnitsVc = parseInt(result.player && result.player.villages, 10) || 0;
                if (onUnitsOverviewPage && expectedUnitsVc > result.all_villages.length) {
                    var uUnitsSweep = new URL(window.location.href);
                    uUnitsSweep.searchParams.set('screen', 'overview_villages');
                    uUnitsSweep.searchParams.set('mode', 'units');
                    if (!uUnitsSweep.searchParams.get('group')) uUnitsSweep.searchParams.set('group', '0');
                    uUnitsSweep.searchParams.set('page_size', '500');
                    var maxUnitsSweep = Math.min(80, Math.ceil(expectedUnitsVc / 5) + 6);
                    var usi;
                    for (usi = 0; usi < maxUnitsSweep; usi++) {
                        if (expectedUnitsVc > 0 && result.all_villages.length >= expectedUnitsVc) break;
                        uUnitsSweep.searchParams.set('page', String(usi));
                        var uUnitsUrl = uUnitsSweep.href;
                        if (unitsSeenFetch[uUnitsUrl]) continue;
                        unitsSeenFetch[uUnitsUrl] = true;
                        if (sameOverviewListPage(uUnitsUrl, window.location.href)) continue;
                        var uHtml = fetchOverviewHtmlSync(uUnitsUrl);
                        if (!uHtml) continue;
                        var uDoc = new DOMParser().parseFromString(uHtml, 'text/html');
                        var prevUnitsLen = result.all_villages.length;
                        paginateUnitsOverviewTable(uDoc, uUnitsUrl, unitsSeenFetch);
                        if (uDoc.querySelectorAll('.quickedit-vn[data-id]').length === 0 && usi > 0) break;
                        if (result.all_villages.length === prevUnitsLen && usi > 2) break;
                    }
                }
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

            // Köy grupları: overview_villages mode=groups (statik üyelik + dinamik gruplar)
            result.village_groups = [];
            try {
                var groupByVid = {};
                var catalog = [];
                var seenGHtml = {};
                var uG = new URL(window.location.href);
                uG.searchParams.set('screen', 'overview_villages');
                uG.searchParams.set('mode', 'groups');
                uG.searchParams.set('type', 'static');
                uG.searchParams.set('group', '0');
                var maxGP = Math.min(12, Math.max(4, Math.ceil((expectedVcTotal || result.all_villages.length) / 100) + 2));
                var gpi;
                for (gpi = 0; gpi < maxGP; gpi++) {
                    uG.searchParams.set('page', String(gpi));
                    var gurl = uG.href;
                    if (seenGHtml[gurl]) continue;
                    seenGHtml[gurl] = true;
                    var ghtml = fetchOverviewHtmlSync(gurl);
                    if (!ghtml) continue;
                    var gdoc = new DOMParser().parseFromString(ghtml, 'text/html');
                    if (gpi === 0) catalog = parseVillageGroupCatalog(gdoc);
                    var memb = parseStaticGroupMembership(gdoc);
                    var mk;
                    for (mk in memb) {
                        if (!Object.prototype.hasOwnProperty.call(memb, mk)) continue;
                        groupByVid[mk] = memb[mk];
                    }
                    if (gdoc.querySelectorAll('.quickedit-vn[data-id]').length === 0 && gpi > 0) break;
                }
                result.village_groups = catalog;
                var vi2, v2, vid2, gnames;
                for (vi2 = 0; vi2 < result.all_villages.length; vi2++) {
                    v2 = result.all_villages[vi2];
                    if (!v2 || !v2.id) continue;
                    vid2 = String(v2.id);
                    gnames = groupByVid[vid2];
                    if (gnames && gnames.length) v2.group_names = gnames.slice();
                    else if (!v2.group_names) v2.group_names = [];
                }
                var dynMax = 20;
                var di, dg, uD, dseen, dp, durl, dhtml, ddoc, dvids, dj;
                for (di = 0; di < result.village_groups.length; di++) {
                    dg = result.village_groups[di];
                    if (!dg || dg.type !== 'dynamic') continue;
                    if (dynMax-- <= 0) break;
                    uD = new URL(window.location.href);
                    uD.searchParams.set('screen', 'overview_villages');
                    uD.searchParams.set('mode', 'groups');
                    uD.searchParams.set('group', dg.id);
                    dseen = {};
                    for (dp = 0; dp < maxGP; dp++) {
                        uD.searchParams.set('page', String(dp));
                        durl = uD.href;
                        if (dseen[durl]) continue;
                        dseen[durl] = true;
                        dhtml = fetchOverviewHtmlSync(durl);
                        if (!dhtml) continue;
                        ddoc = new DOMParser().parseFromString(dhtml, 'text/html');
                        dvids = parseGroupPageVillageIds(ddoc);
                        if (dvids.length === 0 && dp > 0) break;
                        for (dj = 0; dj < dvids.length; dj++) {
                            applyGroupNameToVillages(result.all_villages, dvids[dj], dg.name);
                        }
                    }
                }
            } catch (eg) {}

            var prodTableEl = document.getElementById('production_table');
            if (prodTableEl) {
                mergeVillagesById(result.all_villages, parseProductionTableRows(prodTableEl));
            }

            // Köy puanları: overview_villages mode=prod (üretim tablosu — «Puan» sütunu).
            try {
                var _ptsNow = Date.now();
                var _ptsTtl = 5 * 60 * 1000;
                var _ptsCached = window.__tw_bot_prod_cache;
                var _ptsHtml = null;
                var _nPtsLocal = 0;
                for (var _pk = 0; _pk < result.all_villages.length; _pk++) {
                    if (parsePointsText(result.all_villages[_pk].points) > 0) _nPtsLocal++;
                }
                var _expectedPtsVc0 = parseInt(result.player && result.player.villages, 10) || 0;
                if (!prodTableEl) {
                    if (_ptsCached && (_ptsNow - _ptsCached.t) < _ptsTtl
                        && (_expectedPtsVc0 <= 0 || _nPtsLocal >= _expectedPtsVc0)) {
                        _ptsHtml = _ptsCached.html;
                    } else {
                        var _uProd = new URL(window.location.href);
                        _uProd.searchParams.set('screen', 'overview_villages');
                        _uProd.searchParams.set('mode', 'prod');
                        if (!_uProd.searchParams.get('group')) _uProd.searchParams.set('group', '0');
                        _uProd.searchParams.set('page', '-1');
                        _uProd.searchParams.set('page_size', '500');
                        _ptsHtml = fetchOverviewHtmlSync(_uProd.href);
                        if (_ptsHtml) window.__tw_bot_prod_cache = { t: _ptsNow, html: _ptsHtml };
                    }
                }
                if (_ptsHtml) {
                    var _pDoc = new DOMParser().parseFromString(_ptsHtml, 'text/html');
                    var _pTbl = _pDoc.getElementById('production_table');
                    if (_pTbl) mergeVillagesById(result.all_villages, parseProductionTableRows(_pTbl));
                }
                var expectedPtsVc = parseInt(result.player && result.player.villages, 10) || 0;
                var nWithPts = 0;
                for (var _pi = 0; _pi < result.all_villages.length; _pi++) {
                    if (parsePointsText(result.all_villages[_pi].points) > 0) nWithPts++;
                }
                if (expectedPtsVc > 0 && nWithPts < expectedPtsVc) {
                    var _uProdSw = new URL(window.location.href);
                    _uProdSw.searchParams.set('screen', 'overview_villages');
                    _uProdSw.searchParams.set('mode', 'prod');
                    if (!_uProdSw.searchParams.get('group')) _uProdSw.searchParams.set('group', '0');
                    _uProdSw.searchParams.set('page_size', '500');
                    var _maxProdSw = Math.min(80, Math.ceil(expectedPtsVc / 5) + 6);
                    var _psi;
                    for (_psi = 0; _psi < _maxProdSw; _psi++) {
                        if (nWithPts >= expectedPtsVc) break;
                        _uProdSw.searchParams.set('page', String(_psi));
                        var _purl = _uProdSw.href;
                        var _ph = fetchOverviewHtmlSync(_purl);
                        if (!_ph) continue;
                        var _pd = new DOMParser().parseFromString(_ph, 'text/html');
                        var _pt = _pd.getElementById('production_table');
                        if (!_pt) continue;
                        var _prevPts = nWithPts;
                        mergeVillagesById(result.all_villages, parseProductionTableRows(_pt));
                        nWithPts = 0;
                        for (var _pj = 0; _pj < result.all_villages.length; _pj++) {
                            if (parsePointsText(result.all_villages[_pj].points) > 0) nWithPts++;
                        }
                        if (nWithPts === _prevPts && _psi > 2) break;
                    }
                }
            } catch (exPts) {}

            enrichVillagePointsFromGameData(result.all_villages);
            enrichTroopsFromGameDataUnits(result.all_villages);

            function villagesTroopsLookEmpty(arr) {
                if (!arr || !arr.length) return true;
                var i;
                for (i = 0; i < arr.length; i++) {
                    if (sumTroops(arr[i].troops) > 0) return false;
                }
                return true;
            }

            function fetchUnitsOverviewIntoVillages() {
                try {
                    var _snobNow = Date.now();
                    var _snobTtl = 5 * 60 * 1000;
                    var _unitsSeen = {};
                    var _expectedU = parseInt(result.player && result.player.villages, 10) || result.all_villages.length || 0;
                    var _maxUP = Math.min(80, Math.max(4, Math.ceil(_expectedU / 5) + 4));
                    var _up, _uUnits = new URL(window.location.href);
                    _uUnits.searchParams.set('screen', 'overview_villages');
                    _uUnits.searchParams.set('mode', 'units');
                    if (!_uUnits.searchParams.get('group')) _uUnits.searchParams.set('group', '0');
                    _uUnits.searchParams.set('page_size', '500');
                    // page=-1: bazı dünyalarda tüm köyler tek istekte
                    var _uNeg = new URL(_uUnits.href);
                    _uNeg.searchParams.set('page', '-1');
                    var _negUrl = _uNeg.href;
                    if (!_unitsSeen[_negUrl]) {
                        _unitsSeen[_negUrl] = true;
                        var _negHtml = fetchOverviewHtmlSync(_negUrl);
                        if (_negHtml) {
                            var _negDoc = new DOMParser().parseFromString(_negHtml, 'text/html');
                            var _negTbl = _negDoc.getElementById('units_table');
                            if (_negTbl) {
                                mergeVillagesById(result.all_villages, parseCombinedTableRows(_negTbl));
                            }
                        }
                    }
                    for (_up = 0; _up < _maxUP; _up++) {
                        if (!forceTroopsRefresh && !onUnitsOverviewPage
                            && _expectedU > 0 && result.all_villages.length > 0
                            && !villagesTroopsLookEmpty(result.all_villages)) break;
                        if (_expectedU > 0 && result.all_villages.length >= _expectedU) break;
                        _uUnits.searchParams.set('page', String(_up));
                        var _uUrl = _uUnits.href;
                        if (_unitsSeen[_uUrl]) continue;
                        _unitsSeen[_uUrl] = true;
                        var _snobCached = window.__tw_bot_units_cache;
                        var _snobHtml = null;
                        if (!forceTroopsRefresh && _snobCached && _snobCached.url === _uUrl && (_snobNow - _snobCached.t) < _snobTtl) {
                            _snobHtml = _snobCached.html;
                        } else {
                            _snobHtml = fetchOverviewHtmlSync(_uUrl);
                            if (_snobHtml) window.__tw_bot_units_cache = { t: _snobNow, url: _uUrl, html: _snobHtml };
                        }
                        if (!_snobHtml) continue;
                        var _uDoc = new DOMParser().parseFromString(_snobHtml, 'text/html');
                        var _uTbl = _uDoc.getElementById('units_table');
                        if (!_uTbl) break;
                        var _prevL = result.all_villages.length;
                        mergeVillagesById(result.all_villages, parseCombinedTableRows(_uTbl));
                        if (_uDoc.querySelectorAll('.quickedit-vn[data-id]').length === 0 && _up > 0) break;
                        if (result.all_villages.length === _prevL && _up > 2) break;
                    }
                } catch(exU) {}
            }

            if (forceTroopsRefresh || onUnitsOverviewPage || !unitsTableEl || villagesTroopsLookEmpty(result.all_villages)) {
                fetchUnitsOverviewIntoVillages();
                enrichTroopsFromGameDataUnits(result.all_villages);
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
            if ((troopKeysEmpty || sumTroops(result.troops) === 0) && unitNames.length && game_data.village) {
                var gv = game_data.village;
                var gdTroops = unitsObjectToTroops(gv.units);
                if (sumTroops(gdTroops) === 0) {
                    var uk3, tmp2 = {};
                    for (uk3 = 0; uk3 < unitNames.length; uk3++) {
                        var unk3 = unitNames[uk3];
                        if (unk3 && gv[unk3] != null) tmp2[unk3] = parseInt(gv[unk3], 10) || 0;
                    }
                    gdTroops = tmp2;
                }
                if (sumTroops(gdTroops) > 0) result.troops = gdTroops;
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

            // Premium özellikleri (Yağma Asistanı vb.)
            if (game_data.features) {
                var gf = game_data.features;
                result.features = {};
                var fk, fe;
                for (fk in gf) {
                    if (!Object.prototype.hasOwnProperty.call(gf, fk)) continue;
                    fe = gf[fk];
                    if (fe && typeof fe === 'object' && ('active' in fe)) {
                        result.features[fk] = { active: !!fe.active };
                    }
                }
            }

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

            result.troops_villages_with_stock = 0;
            for (var _tv = 0; _tv < result.all_villages.length; _tv++) {
                if (sumTroops(result.all_villages[_tv].troops) > 0) result.troops_villages_with_stock++;
            }

            result.units = unitNames.slice ? unitNames.slice() : (unitNames || []);

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

            gv_id = (data.get("village") or {}).get("id")
            if gv_id is not None:
                try:
                    self._last_scraped_village_id = int(gv_id)
                    active_troops = data.get("troops") or {}
                    self._last_active_troops_fp = json.dumps(active_troops, sort_keys=True)
                    self._last_active_troops_vid = int(gv_id)
                except (TypeError, ValueError):
                    pass

            # Veriyi kaydet (önceki tam atama ayarlardan gelen hızları siliyordu)
            self._game_data = data
            self._apply_trusted_speeds_to_game_data()
            self._apply_world_context(data)

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
            vgroups = data.get("village_groups") or []
            n_with_groups = sum(1 for v in all_v if v.get("group_names"))
            n_with_troops = data.get("troops_villages_with_stock", 0)
            self._add_log("VERİ", "success",
                f"Veri güncellendi: {village.get('name', '?')} ({village.get('coord', '?')}) | "
                f"Puan: {village.get('points', 0)} | Köyler: {len(all_v)} | "
                f"Askerli köy: {n_with_troops} | "
                f"Gruplar: {len(vgroups)} | Köy+grup: {n_with_groups} | "
                f"Dünya: {data.get('world', '?')}")
            self._refresh_support_plan_groups()

            QTimer.singleShot(200, self._poll_bot_protection)

            if not getattr(self, '_world_settings_fetched', False):
                self._fetch_world_settings()
            elif not getattr(self, '_unit_speeds_fetched', False):
                self._fetch_unit_speeds()

        self.browser.page().runJavaScript(scrape_js, on_scrape_result)

    def _apply_trusted_speeds_to_game_data(self):
        """Ayarlar sayfasından kesin alınan hızları scrape sonrası _game_data ve WorldContext üzerine yaz."""
        tw = getattr(self, "_trusted_world_speed", None)
        tu = getattr(self, "_trusted_unit_speed", None)
        ctx = self._world_ctx
        if tw is not None:
            try:
                v = float(tw)
                if v > 0:
                    self._game_data["world_speed"] = v
                    if ctx.speeds_verified:
                        ctx.world_speed = v
            except (TypeError, ValueError):
                pass
        if tu is not None:
            try:
                v = float(tu)
                if v > 0:
                    self._game_data["unit_speed"] = v
                    if ctx.speeds_verified:
                        ctx.unit_speed = v
            except (TypeError, ValueError):
                pass

    def _fetch_world_settings(self):
        """Sunucunun /page/settings sayfasından dünya hızı, birim hızı ve fake limitini çek."""
        fetch_js = """
        (function() {
            var base = window.location.origin;
            var url = base + '/page/settings';
            window.__tw_world_settings = 'LOADING';
            function normTr(s) {
                return s.replace(/\\s+/g, ' ').trim().toLowerCase()
                    .replace(/\\u0131/g, 'i').replace(/\\u0130/g, 'i');
            }
            function parseFakePctFromValue(value) {
                if (!value) return null;
                var v = normTr(value);
                if (v === 'pasif' || v === 'inaktif' || v === 'inactive' || v === 'passive') return 0;
                var m = String(value).match(/(\\d+(?:\\.\\d+)?)\\s*%/);
                if (m) return parseFloat(m[1]);
                return null;
            }
            function isFakeLimitLabel(label) {
                if (!label) return false;
                if (label.indexOf('aldatma') !== -1 && label.indexOf('sinir') !== -1) return true;
                if (label.indexOf('fake limit') !== -1) return true;
                if (label.indexOf('fake-limit') !== -1) return true;
                return false;
            }
            function parseFakeLimitFromRawHtml(html) {
                var out = {};
                if (!html) return out;
                var row = html.match(
                    /<td>\\s*(?:Aldatma[\\s\\S]*?|Fake\\s*limit)[\\s\\S]*?<\\/td>\\s*<td>\\s*([^<]+)/i
                );
                if (row) {
                    var pct = parseFakePctFromValue(row[1]);
                    if (pct !== null) out.fake_min_pop_percent = pct;
                }
                var k = html.indexOf('"world_config"');
                if (k < 0) k = html.indexOf('worldConfig');
                var slice = k >= 0 ? html.substring(k, k + 15000)
                    : html.substring(0, Math.min(html.length, 300000));
                var wc = slice.match(/"fake_limit"\\s*:\\s*(\\d+(?:\\.\\d+)?)/);
                if (wc && out.fake_min_pop_percent == null) {
                    var n = parseFloat(wc[1]);
                    if (n > 0 && n < 1) out.fake_min_pop_percent = n * 100;
                    else out.fake_min_pop_percent = n;
                }
                return out;
            }
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
            function parseSettingsTable(doc) {
                var result = {};
                var rows = doc.querySelectorAll('table.data-table tr, table.vis tr, .data-table tr');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td, th');
                    if (cells.length < 2) return;
                    var label = normTr(cells[0].textContent);
                    var value = cells[cells.length - 1].textContent.replace(/,/g, '.').trim();
                    var num = parseFloat(value);
                    if (!isNaN(num)) {
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
                    }
                    if (isFakeLimitLabel(label)) {
                        var pct = parseFakePctFromValue(value);
                        if (pct !== null) result.fake_min_pop_percent = pct;
                    }
                });
                return result;
            }
            fetch(url, {credentials: 'same-origin'})
            .then(function(r) { return r.text(); })
            .then(function(html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var rawSpeed = parseSpeedFromRawHtml(html);
                var rawFake = parseFakeLimitFromRawHtml(html);
                var table = parseSettingsTable(doc);
                var result = {};
                if (rawSpeed.world_speed) result.world_speed = rawSpeed.world_speed;
                if (rawSpeed.unit_speed) result.unit_speed = rawSpeed.unit_speed;
                if (!result.world_speed && table.world_speed) result.world_speed = table.world_speed;
                if (!result.unit_speed && table.unit_speed) result.unit_speed = table.unit_speed;
                if (rawFake.fake_min_pop_percent != null) {
                    result.fake_min_pop_percent = rawFake.fake_min_pop_percent;
                } else if (table.fake_min_pop_percent != null) {
                    result.fake_min_pop_percent = table.fake_min_pop_percent;
                }
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
            self._world_ctx.speeds_verified = False
            self._add_log("AYAR", "warn", "Dünya ayarları alınamadı, mevcut değerler kullanılacak")
            ws = self._game_data.get("world_speed", 1)
            us = self._game_data.get("unit_speed", 1)
            self._add_log("AYAR", "info", f"Mevcut hız: world_speed={ws}, unit_speed={us}")
            self._update_world_speed_label()
            if not getattr(self, "_unit_speeds_fetched", False):
                self._fetch_unit_speeds()
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
            ctx = self._world_ctx
            ctx.speeds_verified = self._world_speed_from_settings

            if _ok_num(ws):
                fw = float(ws)
                self._game_data["world_speed"] = fw
                self._trusted_world_speed = fw
                ctx.world_speed = fw
            if _ok_num(us):
                fu = float(us)
                self._game_data["unit_speed"] = fu
                self._trusted_unit_speed = fu
                ctx.unit_speed = fu

            fake_pct = data.get("fake_min_pop_percent")
            if fake_pct is not None:
                try:
                    ctx.fake_min_pop_percent = float(fake_pct)
                    ctx.fake_limit_verified = True
                    if ctx.fake_min_pop_percent <= 0:
                        self._add_log(
                            "AYAR", "info",
                            "Fake limiti: Pasif (ayarlar sayfasından)",
                        )
                    else:
                        self._add_log(
                            "AYAR", "success",
                            f"Fake limiti: %{self._format_fake_pct(ctx.fake_min_pop_percent)} "
                            "(ayarlar sayfasından)",
                        )
                except (TypeError, ValueError):
                    pass

            self._apply_world_context(self._game_data)

            final_ws = self._game_data.get("world_speed", 1)
            final_us = self._game_data.get("unit_speed", 1)
            if self._world_speed_from_settings:
                self._add_log("AYAR", "success",
                    f"✅ Dünya ayarları alındı: Oyun hızı={final_ws}, Birim hızı={final_us}")
            else:
                self._add_log("AYAR", "info",
                    f"Ayarlar tablosunda hız satırı bulunamadı veya eksik (oyun verisi: {final_ws} / {final_us})")
            self._update_world_speed_label()
            self._update_fake_limit_ui()

            self.browser.page().runJavaScript("window.__tw_world_settings = null;")

            if not getattr(self, "_unit_speeds_fetched", False):
                self._fetch_unit_speeds()

        self.browser.page().runJavaScript(check_js, on_poll)

    def _fetch_unit_speeds(self):
        """Sunucudan birim baz yolculuk hızlarını çek (UnitPopup veya get_unit_info)."""
        fetch_js = """
        (function() {
            window.__tw_unit_speeds = 'LOADING';
            function parseUnitData(ud) {
                var out = {};
                if (!ud || typeof ud !== 'object') return out;
                var k, row, spd, n;
                for (k in ud) {
                    if (!Object.prototype.hasOwnProperty.call(ud, k)) continue;
                    row = ud[k];
                    if (row != null && typeof row === 'object') {
                        spd = row.travel_time != null ? row.travel_time : row.speed;
                    } else {
                        spd = row;
                    }
                    if (spd == null) continue;
                    n = parseFloat(spd);
                    if (!isNaN(n) && n > 0) out[k] = n;
                }
                return out;
            }
            function finish(obj) {
                window.__tw_unit_speeds = JSON.stringify(obj || {});
            }
            function fallback() {
                fetch('/interface.php?func=get_unit_info', {credentials: 'same-origin'})
                .then(function(r) { return r.text(); })
                .then(function(txt) {
                    try {
                        var j = JSON.parse(txt);
                        var o = parseUnitData(j);
                        if (Object.keys(o).length) { finish(o); return; }
                    } catch (ex) {}
                    finish({});
                })
                .catch(function() { finish({}); });
            }
            try {
                if (typeof UnitPopup !== 'undefined' && typeof UnitPopup.fetchData === 'function') {
                    UnitPopup.fetchData(function() {
                        var o = parseUnitData(UnitPopup.unit_data);
                        if (Object.keys(o).length) finish(o);
                        else fallback();
                    });
                    return;
                }
            } catch (exU) {}
            fallback();
        })();
        """
        self.browser.page().runJavaScript(fetch_js)
        self._poll_unit_speeds(0)

    def _poll_unit_speeds(self, attempt):
        """Birim hız verisini polling ile al."""
        if attempt > 30:
            self._unit_speeds_fetched = True
            return

        check_js = "window.__tw_unit_speeds || 'WAITING';"

        def on_poll(result):
            result_str = str(result) if result else "WAITING"
            if result_str in ("WAITING", "LOADING"):
                QTimer.singleShot(200, lambda: self._poll_unit_speeds(attempt + 1))
                return
            self._unit_speeds_fetched = True
            try:
                data = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                self.browser.page().runJavaScript("window.__tw_unit_speeds = null;")
                return
            if not isinstance(data, dict) or not data:
                self.browser.page().runJavaScript("window.__tw_unit_speeds = null;")
                return
            self._world_ctx.unit_speeds.update(
                {str(k): float(v) for k, v in data.items() if v}
            )
            self._game_data["unit_speeds"] = dict(self._world_ctx.unit_speeds)
            updated = self._sa_refresh_all_queue_timelines()
            self._add_log(
                "AYAR", "info",
                f"Birim hızları sunucudan alındı ({len(self._world_ctx.unit_speeds)} birim)"
                + (f"; kuyruk zamanları güncellendi ({updated} satır)" if updated else ""),
            )
            self.browser.page().runJavaScript("window.__tw_unit_speeds = null;")

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
        verified = getattr(self, "_world_speed_from_settings", False) or self._world_ctx.speeds_verified

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

        ctx = self._world_ctx
        fake_part = ""
        if ctx.fake_limit_verified:
            if ctx.fake_min_pop_percent <= 0:
                fake_part = " | Fake: Pasif"
            else:
                fake_part = f" | Fake: %{self._format_fake_pct(ctx.fake_min_pop_percent)}"

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
            f"⚙️ Dünya Hızı: {ws_text} | Birim Hızı: {us_text}{fake_part}  {source}")
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
        gv = data.get("village") or {}
        cur_vid = gv.get("id")
        if cur_vid is not None:
            try:
                cur_vid = int(cur_vid)
                for v in data.get("all_villages") or []:
                    if int(v.get("id", 0)) == cur_vid:
                        troops = v.get("troops") or troops
                        break
            except (TypeError, ValueError):
                pass

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
        ordered_villages = _tw_sorted_player_villages(all_villages) if all_villages else []

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
            for i, v in enumerate(ordered_villages):
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
                for i, v in enumerate(ordered_villages):
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
            prev_bq_vid = self.bq_village_combo.currentData()
            if prev_bq_vid and hasattr(self, "_bq_flush_table_to_store"):
                self._bq_flush_table_to_store(prev_bq_vid)
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
                for i, v in enumerate(ordered_villages):
                    coord = f"({v.get('x', '?')}|{v.get('y', '?')})"
                    label = f"{v.get('name', '?')} {coord}"
                    self.bq_village_combo.addItem(label, v.get("id", 0))
                    if v.get("id") == current_id or v.get("selected"):
                        bq_idx = i
                self.bq_village_combo.setCurrentIndex(bq_idx)

            self.bq_village_combo.blockSignals(False)
            new_bq_vid = self.bq_village_combo.currentData()
            if hasattr(self, "_bq_switch_village_queue"):
                self._bq_switch_village_queue(new_bq_vid)

        # Asker toplama sekmesi: köy tablosunu güncelle
        if hasattr(self, 'rt_table'):
            self._rt_refresh_villages()

    def _update_villages_list(self, data):
        """Köyler sekmesindeki tüm köy tablosunu güncelle."""
        if not hasattr(self, 'all_villages_tree'):
            return

        all_villages = data.get("all_villages", [])
        ordered_v = _tw_sorted_player_villages(all_villages)
        self.all_villages_tree.clear()

        UNIT_NAMES_SHORT = {
            "spear": "Mız", "sword": "Kıl", "axe": "Bal",
            "archer": "Okç", "spy": "Cas", "light": "HSv",
            "marcher": "AOk", "heavy": "ASv", "ram": "Koç",
            "catapult": "Man", "knight": "Şöv", "snob": "Mis", "militia": "Mil"
        }

        for v in ordered_v:
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
        self._sa_source_user_picked = False
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
        self._tw_post_login_scrape_scheduled = False
        self._set_login_credentials_highlight(False)
        self._reset_world_context()
        self._human_verification_required = False
        self._botprot_hidden_hint = False
        self._botprot_last_parts = []
        self._botprot_clear_fast_poll()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_indicator.setText("● DURDURULDU")
        self.status_indicator.setStyleSheet("color: #cc4444; font-weight: bold; font-size: 11px;")
        self._add_log("SİSTEM", "warn", "Bot durduruldu.")
        self.world_speed_label.setText("⚙️ Dünya Hızı: — | Birim Hızı: —")
        self.world_speed_label.setStyleSheet("font-size: 11px; padding: 3px 4px; background: #fff3cd; border-radius: 3px; color: #856404;")
        self._update_botprot_ui()
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

    def _invalidate_server_time_sync(self):
        """Sunucu saati güvenilmez / yok — gönderim zamanı DOM'a güvenmesin."""
        self._server_time_synced = False
        self._server_time_text = ""
        self._server_time_anchor_dt = None
        self._server_time_anchor_perf = None
        self._anchor_timing_ms = None

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

    def _fetch_server_time(self, force=False):
        """Sayfadaki sunucu saatini Timing + DOM ile örnekler.

        Kesir ms DOM'dan güvenilmez (özellikle içerik zaten .xxx ile bitiyorsa); tek kaynak
        Timing/sn. Takvim satırı böyle kurulunca parse edilen anchor_dt ile serverNowMs aynı anı
        temsil eder — hedef Timing hesabında yüzlerce ms sapma oluşmaz.

        Ordu gönderimi açıkken ve zaten geçerli anchor varken (force değilse) DOM'dan yeniden
        örnekleme yapılmaz; içeride gezinince yanlış serverTime/Timing ile anchor sıçraması ve
        erken gönderim engellenir — süre perf_counter ile ilerletilir.
        """
        if not self.browser:
            self._show_local_time()
            return

        if not force:
            try:
                if (
                    getattr(self, "enable_sending_cb", None)
                    and self.enable_sending_cb.isChecked()
                    and getattr(self, "_server_time_anchor_dt", None) is not None
                    and getattr(self, "_server_time_anchor_perf", None) is not None
                ):
                    return
            except Exception:
                pass

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
                self._invalidate_server_time_sync()
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
                self._invalidate_server_time_sync()
                self._show_local_time()
                return

            old_text_snapshot = (getattr(self, "_server_time_text", None) or "")
            self._server_time_text = text
            parsed = self._dispatch_parse_server_time()
            if parsed is None:
                self._invalidate_server_time_sync()
                self._show_local_time()
                return

            # Önceki anchor ile tutarsız büyük sıçrama (forum / giriş sayfası vb.) — yok say.
            if not force:
                old_dt = getattr(self, "_server_time_anchor_dt", None)
                old_pf = getattr(self, "_server_time_anchor_perf", None)
                if old_dt is not None and old_pf is not None:
                    try:
                        prev_now = old_dt + datetime.timedelta(seconds=time.perf_counter() - old_pf)
                        skew_s = abs((parsed - prev_now).total_seconds())
                        if skew_s > 90.0:
                            if hasattr(self, "log_text"):
                                self._add_log(
                                    "SİSTEM",
                                    "warn",
                                    f"Sunucu saati örneklemesi yok sayıldı (~{skew_s:.0f}s sapma; güvenilir oyun sayfasında olun).",
                                )
                            self._server_time_text = old_text_snapshot
                            return
                    except Exception:
                        pass

            self._server_time_synced = True
            self._server_time_anchor_dt = parsed
            self._server_time_anchor_perf = time.perf_counter()
            try:
                tm = int(timing_ms) if timing_ms is not None else None
                if tm is not None and tm > 0 and tm < 10**12:
                    tm = int(tm * 1000)
                self._anchor_timing_ms = tm
            except (TypeError, ValueError):
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

    def _botprot_in_fast_mode(self) -> bool:
        """Doğrulama aktif veya yakın zamanda şüpheli sinyal — hızlı DOM taraması."""
        if self._human_verification_required:
            return True
        return time.time() < float(getattr(self, "_botprot_fast_poll_until", 0) or 0)

    def _botprot_start_fast_poll(self, seconds: int = 90) -> None:
        """Gönderim hatası / gizli şüphe sonrası kısa süreli hızlı tarama."""
        until = time.time() + max(30, int(seconds))
        prev = float(getattr(self, "_botprot_fast_poll_until", 0) or 0)
        if until > prev + 5:
            self._botprot_fast_poll_until = until
            self._add_log(
                "GÜVENLİK",
                "info",
                f"Doğrulama taraması hızlandırıldı (~{seconds} sn, yalnızca yerel DOM).",
            )

    def _botprot_clear_fast_poll(self) -> None:
        self._botprot_fast_poll_until = 0.0

    def _schedule_next_botprot_poll(self):
        """Adaptif DOM kontrolü: şüphede ~3 sn, normal oyunda 10–15 sn, aksi 8–15 sn."""
        in_game = (
            self.is_running
            and self._login_state == "in_game"
            and self.browser
            and ("game.php" in self.browser.url().toString() or "/overview" in self.browser.url().toString())
        )
        if self._botprot_in_fast_mode():
            delay_ms = random.randint(2500, 3500)
        elif in_game:
            delay_ms = random.randint(10000, 15000)
        else:
            delay_ms = random.randint(8000, 15000)
        QTimer.singleShot(delay_ms, self._poll_bot_protection_reschedule)

    def _poll_bot_protection_reschedule(self):
        self._poll_bot_protection()
        self._schedule_next_botprot_poll()

    def _botprot_detect_js(self):
        """Katmanlı bot koruması tespiti — görünür + gizli DOM + URL/metin."""
        return r"""
        (function() {
            function visible(el) {
                if (!el) return false;
                var s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity || '1') < 0.05) return false;
                var r = el.getBoundingClientRect();
                return r.width > 2 && r.height > 2 && r.bottom > 0 && r.right > 0;
            }
            function inDom(el) {
                return !!(el && document.body && document.body.contains(el));
            }
            function isInvisibleCaptcha(el) {
                if (!el) return false;
                var ds = (el.getAttribute('data-size') || '').toLowerCase();
                if (ds === 'invisible') return true;
                var r = el.getBoundingClientRect();
                return r.width <= 2 && r.height <= 2;
            }
            function normTr(s) {
                return String(s || '').replace(/\s+/g, ' ').trim().toLowerCase()
                    .replace(/\u0131/g, 'i').replace(/\u0130/g, 'i');
            }
            function textHasBotHint(s) {
                var t = normTr(s);
                if (!t) return false;
                return t.indexOf('bot koruma') >= 0 || t.indexOf('bot protection') >= 0
                    || t.indexOf('guvenlik kontrol') >= 0 || t.indexOf('güvenlik kontrol') >= 0
                    || (t.indexOf('dogrulama') >= 0 && t.indexOf('captcha') < 0)
                    || t.indexOf('doğrulama') >= 0 || t.indexOf('hcaptcha') >= 0;
            }
            var href = (window.location && window.location.href) ? window.location.href.toLowerCase() : '';
            var inGame = href.indexOf('game.php') >= 0 || href.indexOf('/overview') >= 0;
            var loginPage = href.indexOf('/page/auth') >= 0
                || !!document.getElementById('login_form')
                || !!document.getElementById('user');

            var quest = document.getElementById('botprotection_quest');
            var questVisible = false;
            var questDom = inDom(quest);
            if (quest) {
                var qn = quest.querySelector('.quest_new');
                questVisible = visible(quest) || visible(qn);
            }

            var blockingVisible = false;
            var links = document.querySelectorAll('a.btn.btn-default');
            for (var i = 0; i < links.length; i++) {
                var t = (links[i].textContent || '').replace(/\s+/g, ' ').trim();
                if (t.indexOf('Bot koruma kontrol') !== -1 && visible(links[i])) {
                    blockingVisible = true;
                    break;
                }
            }

            var hcaptchaVisible = false;
            var hcaptchaDom = false;
            var hcaptchaInvisible = false;
            var hcIframes = document.querySelectorAll('iframe[src*="hcaptcha"], iframe[src*="newassets.hcaptcha"]');
            for (var hi = 0; hi < hcIframes.length; hi++) {
                if (!inDom(hcIframes[hi])) continue;
                hcaptchaDom = true;
                if (visible(hcIframes[hi])) hcaptchaVisible = true;
                if (isInvisibleCaptcha(hcIframes[hi])) hcaptchaInvisible = true;
            }
            var caps = document.querySelectorAll('.h-captcha');
            for (var ci = 0; ci < caps.length; ci++) {
                if (!inDom(caps[ci])) continue;
                hcaptchaDom = true;
                if (visible(caps[ci])) hcaptchaVisible = true;
                if (isInvisibleCaptcha(caps[ci])) hcaptchaInvisible = true;
            }

            var urlHint = /botprotection|bot_protection|captcha|hcaptcha|verify/i.test(href);

            var textVisible = false;
            var textHint = false;
            var popSel = '#popup_box, .popup_box, #popup, .popup, [class*="popup"]';
            var pops = document.querySelectorAll(popSel);
            for (var pi = 0; pi < pops.length; pi++) {
                var pt = pops[pi].textContent || '';
                if (!textHasBotHint(pt)) continue;
                textHint = true;
                if (visible(pops[pi])) textVisible = true;
            }
            if (quest && textHasBotHint(quest.textContent || '')) {
                textHint = true;
                if (visible(quest)) textVisible = true;
            }

            return JSON.stringify({
                quest_visible: questVisible,
                quest_dom: questDom,
                hcaptcha_visible: hcaptchaVisible,
                hcaptcha_dom: hcaptchaDom,
                hcaptcha_invisible: hcaptchaInvisible,
                blocking_visible: blockingVisible,
                url_hint: urlHint,
                text_visible: textVisible,
                text_hint: textHint,
                in_game: inGame,
                login_page: loginPage
            });
        })();
        """

    def _botprot_signals_from_detection(self, d):
        """Tespit JSON → (active, parts, hidden)."""
        if not d or not isinstance(d, dict):
            return False, [], False
        login_page = bool(d.get("login_page"))
        in_game = bool(d.get("in_game"))
        parts = []
        if d.get("quest_visible"):
            parts.append("görev (botprotection_quest)")
        if d.get("blocking_visible"):
            parts.append("Bot koruma kontrolü")
        if d.get("hcaptcha_visible"):
            parts.append("hCaptcha (görünür)")
        if d.get("text_visible"):
            parts.append("doğrulama penceresi")
        if parts:
            return True, parts, False
        if login_page:
            return False, [], False
        if in_game:
            if d.get("quest_dom"):
                parts.append("görev (DOM)")
            if d.get("hcaptcha_dom"):
                parts.append("hCaptcha (DOM)")
            if d.get("hcaptcha_invisible"):
                parts.append("hCaptcha (invisible)")
            if d.get("url_hint"):
                parts.append("URL ipucu")
            if d.get("text_hint"):
                parts.append("metin ipucu")
            if parts:
                return True, parts, True
        return False, [], False

    @staticmethod
    def _dispatch_error_suggests_botprot(error: str) -> bool:
        if not error:
            return False
        el = error.lower()
        markers = (
            "botprot",
            "onay formu bulunamadi",
            "onay formu yok",
            "onay sayfasi bos",
            "token alinamadi",
            "ch token",
        )
        return any(m in el for m in markers)

    def _update_botprot_ui(self):
        """Üst panel: doğrulama durumu göstergesi ve banner."""
        if not hasattr(self, "status_indicator"):
            return
        if self._human_verification_required:
            hidden = bool(getattr(self, "_botprot_hidden_hint", False))
            if hidden:
                self.status_indicator.setText("● DOĞRULAMA?")
                tip = "Gizli bot koruması şüphesi — tarayıcı sekmesini kontrol edin"
            else:
                self.status_indicator.setText("● DOĞRULAMA")
                tip = "Bot koruması algılandı — tarayıcıda tamamlayın"
            self.status_indicator.setStyleSheet(
                "color: #cc7700; font-weight: bold; font-size: 11px;"
            )
            self.status_indicator.setToolTip(tip)
            if hasattr(self, "botprot_banner"):
                parts = getattr(self, "_botprot_last_parts", []) or []
                hint = " (gizli olabilir)" if hidden else ""
                detail = ", ".join(parts[:3]) if parts else "doğrulama"
                self.botprot_banner.setText(f"Bot koruması{hint}: {detail} — tarayıcıda tamamlayın")
                self.botprot_banner.setVisible(True)
        else:
            self.status_indicator.setToolTip("")
            if hasattr(self, "botprot_banner"):
                self.botprot_banner.setVisible(False)
            if self.is_running:
                self.status_indicator.setText("● AKTİF")
                self.status_indicator.setStyleSheet(
                    "color: #228822; font-weight: bold; font-size: 11px;"
                )
            else:
                self.status_indicator.setText("● DURDURULDU")
                self.status_indicator.setStyleSheet(
                    "color: #cc4444; font-weight: bold; font-size: 11px;"
                )

    def _set_human_verification_state(self, active, parts, *, hidden=False):
        """Merkezi doğrulama bayrağı + log + Telegram + UI."""
        parts = [p for p in (parts or []) if p]
        if active:
            was = self._human_verification_required
            self._human_verification_required = True
            self._botprot_hidden_hint = bool(hidden)
            self._botprot_last_parts = list(parts)
            if hidden:
                self._botprot_start_fast_poll(120)
            self._update_botprot_ui()
            if not was:
                msg = "Doğrulama algılandı (" + ", ".join(parts) + "). Otomatik işlemler duraklatıldı."
                if hidden:
                    msg += " Gizli olabilir — tarayıcı sekmesini kontrol edin."
                self._add_log("GÜVENLİK", "warn", msg)
                self._notify_telegram_security(parts)
                if hasattr(self, "_rt_stop"):
                    self._rt_stop()
        else:
            if self._human_verification_required:
                self._human_verification_required = False
                self._botprot_hidden_hint = False
                self._botprot_last_parts = []
                self._botprot_clear_fast_poll()
                self._update_botprot_ui()
                self._add_log(
                    "GÜVENLİK",
                    "info",
                    "Doğrulama ekranı kalktı — otomatik işlemler yeniden etkin.",
                )
                if hasattr(self, "bq_enable_cb") and self.bq_enable_cb.isChecked():
                    QTimer.singleShot(500, self._bq_auto_process)

    def _apply_botprot_detection(self, d):
        """Tespit sonucunu değerlendir ve durumu güncelle."""
        self._botprot_last_detection = dict(d) if isinstance(d, dict) else {}
        active, parts, hidden = self._botprot_signals_from_detection(d)
        if active:
            self._set_human_verification_state(True, parts, hidden=hidden)
        else:
            self._set_human_verification_state(False, [])

    def _poll_bot_protection(self):
        """Sayfada bot koruması (görünür veya gizli) var mı kontrol et."""
        if not self.browser:
            return

        def on_det(result):
            if not result:
                return
            try:
                d = json.loads(str(result))
            except (json.JSONDecodeError, TypeError):
                return
            self._apply_botprot_detection(d)

        self.browser.page().runJavaScript(self._botprot_detect_js(), on_det)

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
        if self._human_verification_required:
            vtag = "DOĞRULAMA?" if getattr(self, "_botprot_hidden_hint", False) else "DOĞRULAMA"
            state = f"{state} | {vtag}"
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
