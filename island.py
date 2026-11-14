"""Капри-остров v11: музыкальная капсула + синхронный текст песни.
Трек/прогресс/управление — media.py. Обложки — Deezer → iTunes.
Текст — LRCLIB (синхронный) → lyrics.ovh, кэш на диске. Клик по строке = перемотка.
Палитра из обложки, спектр 12 полос, громкость, таймер сна, перетаскивание."""
import ctypes
import datetime
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

log = logging.getLogger("capri")
u = ctypes.windll.user32

THEMES = {
    "cyber": {"a": (255, 34, 96), "b": (150, 70, 255), "c": (40, 255, 150), "info": (43, 217, 255),
              "bg": (5, 10, 12), "surf": (11, 24, 29), "text": (225, 248, 240), "muted": (122, 160, 149)},
    "ice": {"a": (64, 196, 255), "b": (90, 120, 255), "c": (180, 255, 250), "info": (120, 220, 255),
            "bg": (6, 10, 16), "surf": (12, 20, 32), "text": (226, 242, 255), "muted": (120, 150, 180)},
    "magma": {"a": (255, 84, 40), "b": (255, 40, 90), "c": (255, 190, 60), "info": (255, 150, 80),
              "bg": (14, 7, 6), "surf": (28, 14, 12), "text": (255, 238, 226), "muted": (180, 140, 120)},
    "violet": {"a": (176, 96, 255), "b": (96, 60, 220), "c": (120, 255, 220), "info": (200, 140, 255),
               "bg": (10, 6, 18), "surf": (22, 14, 38), "text": (238, 230, 255), "muted": (150, 130, 185)},
    "amber": {"a": (255, 176, 46), "b": (255, 110, 30), "c": (255, 230, 140), "info": (255, 200, 90),
              "bg": (14, 11, 6), "surf": (30, 22, 12), "text": (255, 244, 226), "muted": (180, 155, 120)},
    "mono": {"a": (235, 240, 245), "b": (140, 150, 165), "c": (200, 210, 220), "info": (170, 185, 200),
             "bg": (8, 9, 11), "surf": (20, 22, 26), "text": (235, 240, 245), "muted": (130, 138, 150)},
}
THEME_NAMES = {"cyber": "Киберпанк", "ice": "Лёд", "magma": "Магма",
               "violet": "Фиолет", "amber": "Янтарь", "mono": "Монохром"}

DEFAULTS = {"enabled": True, "position": "top", "opacity": 0.97, "scale": 1.0, "eq": True,
            "eq_sensitivity": 1.0, "eq_bars": 9, "blur": False, "glow": 1.0, "anim_speed": 1.0,
            "theme": "cyber", "auto_hide_fullscreen": True, "auto_collapse": True,
            "show_art": True, "online_covers": True, "spectrum": True,
            "dynamic_palette": True, "reactive_glow": True, "peak_hold": True,
            "compact_play": True, "idle_clock": True, "quick_actions": True,
            "lyrics": True, "lyrics_in_compact": True, "lyrics_glow": True,
            "use_custom_pos": False, "custom_pos": [0, 0]}

BLOCK, RATE, NBANDS = 1024, 22050, 12
BAND_EDGES = [30, 60, 100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300, 10000]
WEEKDAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
UA = "CapriIsland/1.1 (github.com/grishkov0106-cmd/capricorn-app)"


def _load_settings(base):
    data = dict(DEFAULTS)
    try:
        with open(os.path.join(base, "island.json"), encoding="utf-8") as f:
            data.update(json.load(f))
    except Exception:
        pass
    return data


def _save_settings(base, data):
    try:
        with open(os.path.join(base, "island.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Не удалось сохранить island.json")


def _com_init():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass


def _media_key(vk):
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)


def _fmt_time(sec):
    if sec is None:
        return "--:--"
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _clean_source(s):
    s = str(s or "")
    low = s.lower()
    for key, name in (("yandex", "Яндекс Музыка"), ("яндекс", "Яндекс Музыка"), ("spotify", "Spotify"),
                      ("chrome", "Chrome"), ("msedge", "Edge"), ("firefox", "Firefox"), ("opera", "Opera"),
                      ("brave", "Brave"), ("vlc", "VLC"), ("aimp", "AIMP"), ("foobar", "foobar2000")):
        if key in low:
            return name
    s = re.sub(r"\.exe$", "", s, flags=re.I).split("!")[-1]
    return s.replace("_", " ").strip() or "Плеер"


def _split_artist(title, artist):
    if not artist and re.search(r"\s+[—–-]\s+", title):
        a, t = re.split(r"\s+[—–-]\s+", title, 1)
        return t.strip(), a.strip()
    return title, artist


def _get_json(url, timeout=7):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _get_bytes(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _clean_query(title, artist):
    t = re.sub(r"\(.*?\)|\[.*?\]", "", title)
    t = re.sub(r"\b(feat|ft|prod)\b.*$", "", t, flags=re.I).strip()
    a = re.split(r"[,&]", artist)[0].strip()
    return t or title, a


# ---------------- громкость ----------------
def _endpoint():
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def volume_get():
    try:
        ep = _endpoint()
        return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())
    except Exception:
        return None, False


def volume_set(percent):
    def do():
        _com_init()
        try:
            ep = _endpoint()
            ep.SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None)
            if percent > 0:
                ep.SetMute(0, None)
        except Exception:
            log.exception("Остров: громкость не изменилась")
    threading.Thread(target=do, daemon=True).start()


def volume_mute():
    def do():
        _com_init()
        try:
            ep = _endpoint()
            ep.SetMute(0 if ep.GetMute() else 1, None)
        except Exception:
            log.exception("Остров: мьют не сработал")
    threading.Thread(target=do, daemon=True).start()


# ---------------- трек ----------------
_BASE_WARNED = False


def read_base():
    global _BASE_WARNED
    try:
        import media
        np = media.now_playing()
    except Exception:
        if not _BASE_WARNED:
            _BASE_WARNED = True
            log.exception("Остров: media.now_playing() не сработал")
        return None
    if not np:
        return None
    title = artist = source = ""
    playing = None
    if isinstance(np, dict):
        title = str(np.get("title") or "")
        artist = str(np.get("artist") or "")
        source = _clean_source(np.get("source") or np.get("app"))
        playing = np.get("playing")
    elif isinstance(np, (list, tuple)):
        title = str(np[0] or "") if len(np) > 0 else ""
        source = _clean_source(np[1]) if len(np) > 1 else ""
        third = np[2] if len(np) > 2 else None
        if isinstance(third, bool):
            playing = third
        elif isinstance(third, str):
            artist = third
    else:
        title = str(np)
    title, artist = _split_artist(title, artist)
    if not title:
        return None
    return {"title": title, "artist": artist, "source": source, "playing": playing,
            "position": None, "duration": None, "art": None}


_EXTRA_WARNED = False


async def _extra_async(media):
    s = await media._pick()
    if s is None:
        return None
    props = await s.try_get_media_properties_async()
    pos, end = media._position(s)
    title, artist = _split_artist((props.title or "").strip(), (props.artist or "").strip())
    return {"key": (title, artist), "position": pos, "duration": end if end > 0 else None}


def send_control(action, seconds=None):
    def do():
        _com_init()
        done = threading.Event()

        def native():
            try:
                import media
                if action == "seek":
                    media.seek(seconds=float(seconds))
                else:
                    media.control({"play": "toggle", "next": "next", "prev": "prev"}.get(action, "toggle"))
            except Exception:
                log.exception("Остров: media.control/seek не сработал")
            finally:
                done.set()

        threading.Thread(target=native, daemon=True).start()
        if not done.wait(3.0) and action != "seek":
            _media_key({"play": 0xB3, "next": 0xB0, "prev": 0xB1}.get(action, 0xB3))
    threading.Thread(target=do, daemon=True).start()


# ---------------- обложки ----------------
_ART = {"key": None, "bytes": None}
_ART_MEM = {}
_MISS = object()


def _cover_online(title, artist):
    t, a = _clean_query(title, artist)
    queries = [q for q in (f"{a} {t}".strip(), t) if q]
    for q in queries:
        try:
            d = _get_json("https://api.deezer.com/search?limit=1&q=" + urllib.parse.quote(q))
            item = (d.get("data") or [None])[0]
            if item:
                alb = item.get("album") or {}
                url = alb.get("cover_xl") or alb.get("cover_big") or alb.get("cover_medium")
                if url:
                    data = _get_bytes(url)
                    if data:
                        log.info("Остров: обложка с Deezer — %s", t)
                        return data
        except Exception as e:
            log.info("Остров: Deezer недоступен (%s)", type(e).__name__)
            break
    for q in queries:
        try:
            d = _get_json("https://itunes.apple.com/search?media=music&entity=song&limit=1&term="
                          + urllib.parse.quote(q))
            item = (d.get("results") or [None])[0]
            if item and item.get("artworkUrl100"):
                data = _get_bytes(item["artworkUrl100"].replace("100x100", "600x600"))
                if data:
                    log.info("Остров: обложка с iTunes — %s", t)
                    return data
        except Exception as e:
            log.info("Остров: iTunes недоступен (%s)", type(e).__name__)
            break
    log.info("Остров: обложка в сети не найдена — %s / %s", t, a)
    return None


def _cache_path(base, folder, key, ext):
    h = hashlib.md5(("|".join(str(x) for x in key)).encode("utf-8", "ignore")).hexdigest()
    d = os.path.join(base, folder)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ext)


def _art_disk_path(base, key):
    return _cache_path(base, "island_covers", key, ".img")


def _art_disk(base, key):
    try:
        p = _art_disk_path(base, key)
        if os.path.isfile(p):
            return open(p, "rb").read() or None
    except Exception:
        pass
    return None


def _art_save(base, key, data):
    if not data:
        return
    try:
        with open(_art_disk_path(base, key), "wb") as f:
            f.write(data)
    except Exception:
        pass


def palette_from_cover(pm):
    try:
        img = pm.toImage().scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        img = img.convertToFormat(QImage.Format_RGB32)
        buckets = {}
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                h, s, v = max(0, c.hue()), c.saturation(), c.value()
                key = (h // 18, s // 70, v // 70)
                b = buckets.setdefault(key, [0, 0, 0, 0, s, v, h])
                b[0] += c.red(); b[1] += c.green(); b[2] += c.blue(); b[3] += 1
        if not buckets:
            return None, None
        ranked = sorted(buckets.values(), key=lambda b: -(b[3] * (0.35 + b[4] / 255)))
        dom = ranked[0]
        accent = None
        for b in ranked[1:]:
            if abs(b[6] - dom[6]) > 40 and b[4] > 70:
                accent = b
                break
        accent = accent or (ranked[1] if len(ranked) > 1 else dom)

        def mk(b, sat, val):
            return QColor.fromHsv(b[6], min(255, max(sat, int(b[4] * 1.15))),
                                  min(255, max(val, int(b[5] * 1.2))))
        return mk(dom, 150, 185), mk(accent, 140, 170)
    except Exception:
        return None, None


# ---------------- текст песни ----------------
_LYR = {"key": None, "data": None}
_LYR_MEM = {}
LRC_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


def _parse_lrc(text):
    out = []
    for line in (text or "").splitlines():
        stamps = LRC_RE.findall(line)
        if not stamps:
            continue
        body = LRC_RE.sub("", line).strip()
        if not body:
            continue
        for m, s, cs in stamps:
            frac = 0.0
            if cs:
                frac = int(cs) / {1: 10, 2: 100, 3: 1000}.get(len(cs), 100)
            out.append((int(m) * 60 + int(s) + frac, body))
    out.sort(key=lambda x: x[0])
    return out


def _mk_lyrics(d):
    synced = _parse_lrc(d.get("syncedLyrics"))
    plain = [l.strip() for l in (d.get("plainLyrics") or "").splitlines() if l.strip()]
    if not synced and not plain:
        return None
    return {"synced": synced, "plain": plain, "source": "LRCLIB",
            "instrumental": bool(d.get("instrumental"))}


def _lyrics_online(title, artist, duration):
    t, a = _clean_query(title, artist)
    params = {"track_name": t, "artist_name": a}
    if duration:
        params["duration"] = int(duration)
    try:
        d = _get_json("https://lrclib.net/api/get?" + urllib.parse.urlencode(params))
        if isinstance(d, dict):
            r = _mk_lyrics(d)
            if r:
                log.info("Остров: текст с LRCLIB (%s) — %s",
                         "синхронный" if r["synced"] else "обычный", t)
                return r
    except Exception as e:
        log.info("Остров: LRCLIB get недоступен (%s)", type(e).__name__)
    try:
        d = _get_json("https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": f"{a} {t}".strip()}))
        if isinstance(d, list):
            for item in d[:6]:
                r = _mk_lyrics(item)
                if r:
                    log.info("Остров: текст найден поиском LRCLIB — %s", t)
                    return r
    except Exception as e:
        log.info("Остров: LRCLIB search недоступен (%s)", type(e).__name__)
    try:
        d = _get_json(f"https://api.lyrics.ovh/v1/{urllib.parse.quote(a)}/{urllib.parse.quote(t)}")
        if d.get("lyrics"):
            plain = [l.strip() for l in d["lyrics"].splitlines() if l.strip()]
            if plain:
                log.info("Остров: текст с lyrics.ovh — %s", t)
                return {"synced": [], "plain": plain, "source": "lyrics.ovh", "instrumental": False}
    except Exception as e:
        log.info("Остров: lyrics.ovh недоступен (%s)", type(e).__name__)
    log.info("Остров: текст не найден — %s / %s", t, a)
    return None


def _lyrics_disk(base, key):
    try:
        p = _cache_path(base, "island_lyrics", key, ".json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if not d:
                return None
            d["synced"] = [(float(t), s) for t, s in d.get("synced", [])]
            return d
    except Exception:
        pass
    return None


def _lyrics_save(base, key, data):
    try:
        with open(_cache_path(base, "island_lyrics", key, ".json"), "w", encoding="utf-8") as f:
            json.dump(data or {}, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------- Acrylic ----------------
class _ACCENT(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WCA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def _apply_blur(hwnd, on):
    try:
        accent = _ACCENT(4 if on else 0, 0, 0xB0101815 if on else 0, 0)
        data = _WCA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        if on:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
    except Exception:
        pass


def _fullscreen_active():
    """Настоящий фуллскрин: окно ТОЧНО совпадает с границами монитора.
    Развёрнутые окна (VS Code, браузер) фуллскрином не считаются."""
    try:
        from ctypes import wintypes
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False

        class MONINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", ctypes.c_uint)]

        mon = u.MonitorFromWindow(hwnd, 2)
        mi = MONINFO()
        mi.cbSize = ctypes.sizeof(MONINFO)
        if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return False
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        m = mi.rcMonitor
        return r.left == m.left and r.top == m.top and r.right == m.right and r.bottom == m.bottom
    except Exception:
        return False


# ---------------- фоновые потоки ----------------
class _MediaBridge(QObject):
    track = Signal(object)
    bands = Signal(object)
    volume = Signal(object)
    lyrics = Signal(object)

    def __init__(self, settings, base):
        super().__init__()
        self.s = settings
        self.base = base
        self._stop = False
        self._rec = None
        self._np = None
        self._extra = {}
        self._extra_time = 0.0
        self._want_art = None
        self._want_lyrics = None
        threading.Thread(target=self._main_loop, daemon=True).start()
        threading.Thread(target=self._extra_loop, daemon=True).start()
        threading.Thread(target=self._art_loop, daemon=True).start()
        threading.Thread(target=self._lyrics_loop, daemon=True).start()

    def _main_loop(self):
        _com_init()
        meter = self._meter()
        last = last_vol = 0.0
        announced = False
        while not self._stop:
            self.bands.emit(self._read_bands(meter))
            now = time.time()
            if now - last_vol > 1.2:
                last_vol = now
                self.volume.emit(volume_get())
            if now - last > 1.0:
                last = now
                t = read_base()
                if t:
                    key = (t["title"], t["artist"])
                    ex = self._extra
                    if ex and ex.get("key") == key and now - self._extra_time < 15:
                        t["position"] = ex.get("position")
                        t["duration"] = ex.get("duration")
                    if _ART["key"] == key and _ART["bytes"]:
                        t["art"] = _ART["bytes"]
                    self._want_art = key
                    self._want_lyrics = (t["title"], t["artist"], int(t["duration"] or 0))
                    if not announced:
                        announced = True
                        log.info("Остров: трек получен — %s (%s)", t["title"], t["source"])
                self.track.emit(t)
            if self._rec is None:
                time.sleep(0.05)

    def _extra_loop(self):
        global _EXTRA_WARNED
        _com_init()
        while not self._stop:
            try:
                import media
                data = media._run(_extra_async(media))
                if data:
                    self._extra = data
                    self._extra_time = time.time()
            except Exception as e:
                if not _EXTRA_WARNED:
                    _EXTRA_WARNED = True
                    log.info("Остров: прогресс недоступен (%s: %s)", type(e).__name__, e)
            time.sleep(1.5)

    def _art_loop(self):
        while not self._stop:
            key = self._want_art
            if self.s.get("show_art", True) and key and key != _ART["key"]:
                data = _ART_MEM.get(key, _MISS)
                if data is _MISS:
                    data = _art_disk(self.base, key)
                    if data:
                        log.info("Остров: обложка из кэша — %s", key[0])
                    elif self.s.get("online_covers", True):
                        log.info("Остров: ищу обложку — %s / %s", key[0], key[1])
                        data = _cover_online(*key)
                        _art_save(self.base, key, data)
                    _ART_MEM[key] = data
                _ART["key"], _ART["bytes"] = key, data
            time.sleep(1.0)

    def _lyrics_loop(self):
        while not self._stop:
            want = self._want_lyrics
            if self.s.get("lyrics", True) and want:
                key = (want[0], want[1])
                if key != _LYR["key"]:
                    data = _LYR_MEM.get(key, _MISS)
                    if data is _MISS:
                        data = _lyrics_disk(self.base, key)
                        if data:
                            log.info("Остров: текст из кэша — %s", key[0])
                        else:
                            log.info("Остров: ищу текст — %s / %s", key[0], key[1])
                            data = _lyrics_online(*want)
                            _lyrics_save(self.base, key, data)
                        _LYR_MEM[key] = data
                    _LYR["key"], _LYR["data"] = key, data
                    self.lyrics.emit({"key": key, "data": data})
            time.sleep(1.0)

    def _open_spectrum(self):
        try:
            import warnings
            import numpy as np
            import soundcard as sc
            try:
                from soundcard.mediafoundation import SoundcardRuntimeWarning
                warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
            except Exception:
                warnings.filterwarnings("ignore", message=".*discontinuity.*")
            spk = sc.default_speaker()
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
            rec = mic.recorder(samplerate=RATE, blocksize=BLOCK)
            rec.__enter__()
            self._np = np
            self._rec = rec
            freqs = np.fft.rfftfreq(BLOCK, 1 / RATE)
            self._win = np.hanning(BLOCK)
            self._idx = [(freqs >= BAND_EDGES[i]) & (freqs < BAND_EDGES[i + 1]) for i in range(NBANDS)]
            self._spec_max = 1e-6
            self._spec_fail = 0
            log.info("Остров: честный спектр запущен (%d полос)", NBANDS)
            return True
        except Exception:
            self._rec = None
            return False

    def _close_spectrum(self):
        try:
            if self._rec:
                self._rec.__exit__(None, None, None)
        except Exception:
            pass
        self._rec = None

    def _meter(self):
        try:
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            dev = AudioUtilities.GetSpeakers()
            iface = dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
            return cast(iface, POINTER(IAudioMeterInformation))
        except Exception:
            return None

    def _read_bands(self, meter):
        if self.s.get("spectrum", True):
            if self._rec is None:
                self._open_spectrum()
            if self._rec is not None:
                try:
                    data = self._rec.record(numframes=BLOCK)
                    mono = data.mean(axis=1) if getattr(data, "ndim", 1) > 1 else data
                    fft = self._np.abs(self._np.fft.rfft(mono * self._win))
                    vals = [float(fft[i].mean()) if i.any() else 0.0 for i in self._idx]
                    vals = [math.log1p(v * 8) for v in vals]
                    self._spec_max = max(max(vals), self._spec_max * 0.995)
                    self._spec_fail = 0
                    return [min(1.0, v / max(self._spec_max, 1e-6)) for v in vals]
                except Exception:
                    self._spec_fail = getattr(self, "_spec_fail", 0) + 1
                    if self._spec_fail > 30:
                        log.info("Остров: спектр нестабилен, перехожу на уровень громкости")
                        self._close_spectrum()
                        self.s["spectrum"] = False
        elif self._rec is not None:
            self._close_spectrum()
        try:
            peak = float(meter.GetPeakValue()) if meter else 0.0
        except Exception:
            peak = 0.0
        t = time.time()
        return [max(0.0, min(1.0, peak * (0.55 + 0.45 * math.sin(t * (3 + i * 0.7) + i))))
                for i in range(NBANDS)]

    def stop(self):
        self._stop = True


# ---------------- панель настроек ----------------
class SettingsPanel(QWidget):
    def __init__(self, island):
        super().__init__(None)
        self.island = island
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._rows = {}
        self._build()
        self._restyle()
        self.setFixedSize(400, 620)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        self.card = QFrame()
        self.card.setObjectName("card")
        outer = QVBoxLayout(self.card)
        outer.setContentsMargins(16, 14, 16, 16)
        head = QHBoxLayout()
        self.title = QLabel("НАСТРОЙКА ОСТРОВА")
        self.title.setObjectName("title")
        close = QPushButton("✕")
        close.setObjectName("close")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.hide)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(close)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("inner")
        self.form = QVBoxLayout(inner)
        self.form.setSpacing(9)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        root.addWidget(self.card)

        self._section("ВНЕШНИЙ ВИД")
        self._combo("theme", "Тема", list(THEME_NAMES.items()))
        self._check("dynamic_palette", "Палитра из обложки")
        self._slider("scale", "Масштаб", 80, 150, 100, "%")
        self._slider("opacity", "Прозрачность", 45, 100, 100, "%")
        self._slider("glow", "Свечение", 0, 180, 100, "%")
        self._check("reactive_glow", "Свечение под бас")
        self._check("blur", "Acrylic-размытие")
        self._slider("anim_speed", "Скорость анимации", 40, 220, 100, "%")

        self._section("ТЕКСТ ПЕСНИ")
        self._check("lyrics", "Искать текст в сети (LRCLIB)")
        self._check("lyrics_in_compact", "Строка текста в капсуле")
        self._check("lyrics_glow", "Подсветка текущей строки")

        self._section("ЭКВАЛАЙЗЕР")
        self._check("eq", "Показывать эквалайзер")
        self._check("spectrum", "Честный спектр (soundcard)")
        self._check("peak_hold", "Пики над полосами")
        self._slider("eq_bars", "Полос", 3, 12, 1, "")
        self._slider("eq_sensitivity", "Чувствительность", 40, 250, 100, "%")

        self._section("МУЗЫКА")
        self._check("show_art", "Обложки альбомов")
        self._check("online_covers", "Качать обложки из сети")
        self._check("compact_play", "Кнопка паузы в капсуле")

        self._section("ПОВЕДЕНИЕ")
        self._combo("position", "Позиция", [("top", "Сверху"), ("bottom", "Снизу")])
        self._check("idle_clock", "Часы, когда ничего не играет")
        self._check("quick_actions", "Быстрые команды Капри")
        self._check("auto_collapse", "Авто-сворачивание")
        self._check("auto_hide_fullscreen", "Прятать в играх и видео")
        self._check("enabled", "Остров включён")

        btns = QHBoxLayout()
        for label, fn in (("Сброс", self._reset), ("Тема наугад", self._random_theme),
                          ("На место", self._reset_pos), ("Экспорт", self._export)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            btns.addWidget(b)
        self.form.addLayout(btns)
        self.form.addStretch(1)

    def _section(self, text):
        lab = QLabel(text)
        lab.setObjectName("section")
        self.form.addWidget(lab)

    def _combo(self, key, label, items):
        row = QVBoxLayout()
        lab = QLabel(label); lab.setObjectName("lab")
        cb = QComboBox()
        for val, name in items:
            cb.addItem(name, val)
        cb.currentIndexChanged.connect(lambda _=0, k=key, c=cb: self.island.apply_setting(k, c.currentData()))
        row.addWidget(lab); row.addWidget(cb)
        self.form.addLayout(row)
        self._rows[key] = cb

    def _slider(self, key, label, lo, hi, mult, suffix):
        row = QVBoxLayout()
        top = QHBoxLayout()
        lab = QLabel(label); lab.setObjectName("lab")
        val = QLabel(""); val.setObjectName("val")
        top.addWidget(lab); top.addStretch(1); top.addWidget(val)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.valueChanged.connect(lambda v, k=key, m=mult, s=suffix, l=val: (
            l.setText(f"{v}{s}"), self.island.apply_setting(k, v / m if m != 1 else v)))
        row.addLayout(top); row.addWidget(sl)
        self.form.addLayout(row)
        self._rows[key] = (sl, val, mult, suffix)

    def _check(self, key, label):
        ch = QCheckBox(label)
        ch.toggled.connect(lambda v, k=key: self.island.apply_setting(k, bool(v)))
        self.form.addWidget(ch)
        self._rows[key] = ch

    def sync(self):
        s = self.island.s
        for key, w in self._rows.items():
            if isinstance(w, QComboBox):
                w.blockSignals(True); w.setCurrentIndex(max(0, w.findData(s.get(key)))); w.blockSignals(False)
            elif isinstance(w, QCheckBox):
                w.blockSignals(True); w.setChecked(bool(s.get(key))); w.blockSignals(False)
            else:
                sl, val, mult, suffix = w
                sl.blockSignals(True)
                v = s.get(key, 0)
                iv = int(round(v * mult)) if mult != 1 else int(v)
                sl.setValue(iv); val.setText(f"{iv}{suffix}")
                sl.blockSignals(False)

    def _reset(self):
        self.island.s = dict(DEFAULTS)
        _save_settings(self.island.base, self.island.s)
        self.island._after_settings()
        self.sync(); self._restyle()

    def _random_theme(self):
        import random
        self.island.apply_setting("theme", random.choice(list(THEMES)))
        self.sync(); self._restyle()

    def _reset_pos(self):
        self.island.apply_setting("use_custom_pos", False)

    def _export(self):
        p = os.path.join(self.island.base, "island_settings_export.json")
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.island.s, f, ensure_ascii=False, indent=2)
            log.info("Остров: настройки выгружены в %s", p)
            os.startfile(p)
        except Exception:
            log.exception("Остров: экспорт не удался")

    def _restyle(self):
        t = THEMES.get(self.island.s.get("theme", "cyber"), THEMES["cyber"])
        a = "rgb(%d,%d,%d)" % t["a"]; b = "rgb(%d,%d,%d)" % t["b"]; c = "rgb(%d,%d,%d)" % t["c"]
        bg = "rgb(%d,%d,%d)" % t["bg"]; surf = "rgb(%d,%d,%d)" % t["surf"]
        text = "rgb(%d,%d,%d)" % t["text"]; muted = "rgb(%d,%d,%d)" % t["muted"]
        self.setStyleSheet(f"""
            QFrame#card {{ background:{bg}; border:1px solid {b}; border-radius:16px; }}
            QWidget#inner {{ background:transparent; }}
            QLabel {{ color:{text}; font-family:'Segoe UI'; font-size:12px; background:transparent; }}
            QLabel#title {{ color:{c}; font-weight:bold; letter-spacing:2px; font-size:12px; }}
            QLabel#section {{ color:{a}; font-weight:bold; letter-spacing:2px; font-size:10px;
                padding-top:8px; border-top:1px solid {surf}; margin-top:4px; }}
            QLabel#lab {{ color:{muted}; }}
            QLabel#val {{ color:{c}; font-family:'Consolas'; }}
            QScrollArea {{ background:transparent; border:none; }}
            QScrollBar:vertical {{ background:{surf}; width:10px; border-radius:5px; margin:2px; }}
            QScrollBar::handle:vertical {{ background:{a}; border-radius:5px; min-height:28px; }}
            QScrollBar::handle:vertical:hover {{ background:{b}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:none; }}
            QComboBox {{ background:{surf}; color:{text}; border:1px solid {b}; border-radius:8px; padding:6px 10px; }}
            QComboBox::drop-down {{ border:none; width:22px; }}
            QComboBox QAbstractItemView {{ background:{surf}; color:{text}; selection-background-color:{a};
                border:1px solid {b}; outline:none; }}
            QCheckBox {{ color:{text}; spacing:8px; background:transparent; }}
            QCheckBox::indicator {{ width:18px; height:18px; border-radius:5px; border:1px solid {b}; background:{surf}; }}
            QCheckBox::indicator:checked {{ background:{c}; border:1px solid {c}; }}
            QSlider::groove:horizontal {{ height:6px; background:{surf}; border-radius:3px; }}
            QSlider::sub-page:horizontal {{ background:{a}; border-radius:3px; }}
            QSlider::handle:horizontal {{ width:16px; height:16px; margin:-6px 0; border-radius:8px; background:{c}; }}
            QSlider::handle:horizontal:hover {{ background:{text}; }}
            QPushButton {{ background:{surf}; color:{text}; border:1px solid {b}; border-radius:9px; padding:8px 6px; font-size:11px; }}
            QPushButton:hover {{ border:1px solid {a}; color:{c}; }}
            QPushButton#close {{ border-radius:14px; padding:0; font-size:13px; }}
        """)

    def show_near(self):
        self.sync(); self._restyle(); self.show()
        self.island._place_panel(force=True)
        self.raise_(); self.activateWindow()


# ---------------- остров ----------------
class Island(QWidget):
    notify_signal = Signal(str)
    lyrics_signal = Signal(object)

    def __init__(self, cfg, base):
        super().__init__()
        self.cfg, self.base = cfg, base
        self.s = _load_settings(base)
        self.capri = None
        self.capri_mode = "idle"
        self._mode_until = 0.0
        self.subtitle = ""
        self.track = None
        self._pos_base, self._pos_time = 0.0, time.time()
        self.bands = [0.0] * NBANDS
        self.peaks = [0.0] * NBANDS
        self._bass = 0.0
        self._vol, self._muted = 50, False
        self._vol_drag = False
        self._notify, self._notify_until = "", 0.0
        self._expanded = self._pinned = False
        self._t0 = time.time()
        self._w, self._h = 216.0, 58.0
        self._marquee = self._glitch = 0.0
        self._fullscreen = False
        self._fs_check = 0.0
        self._last_interact = time.time()
        self._hover = None
        self._seeking = False
        self._seek_frac = 0.0
        self._art_pix = self._dyn_a = self._dyn_b = self._art_glow = None
        self._sleep_until = 0.0
        self._drag_start = None
        self._drag_origin = None
        self._dragging = False
        self._rects = {}
        self.lyrics = None
        self._lyrics_on = False
        self._lyr_pos = 0.0
        self._lyr_scroll = 0.0
        self._lyr_lines = []

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                            Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFixedSize(int(self._w), int(self._h))

        self.notify_signal.connect(self._set_notify)
        self.lyrics_signal.connect(self._set_lyrics)
        self.bridge = _MediaBridge(self.s, base)
        self.bridge.bands.connect(self._on_bands)
        self.bridge.track.connect(self._on_track)
        self.bridge.volume.connect(self._on_volume)
        self.bridge.lyrics.connect(self.lyrics_signal.emit)
        self._panel = SettingsPanel(self)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self._place()
        if self.s.get("enabled", True):
            self.show()
        self._apply_blur_now()

    def _theme(self):
        return THEMES.get(self.s.get("theme", "cyber"), THEMES["cyber"])

    def _col(self, name, alpha=255):
        r, g, b = self._theme()[name]
        return QColor(r, g, b, alpha)

    def _d(self, name, alpha=255):
        c = None
        if self.s.get("dynamic_palette", True):
            c = self._dyn_a if name == "a" else self._dyn_b if name == "b" else None
        c = QColor(c) if c else self._col(name)
        c.setAlpha(alpha)
        return c

    def set_mode(self, mode, seconds=0.0):
        if mode != self.capri_mode:
            self._glitch = 0.22
        self.capri_mode = mode or "idle"
        self._mode_until = time.time() + (float(seconds) if seconds and seconds > 0 else 8.0) \
            if self.capri_mode == "listening" else 0.0

    def set_subtitle(self, text):
        self.subtitle = text or ""
        self._marquee = 0.0

    def pulse(self):
        self._glitch = 0.3
        self._last_interact = time.time()

    def notify(self, text):
        self.notify_signal.emit(text)

    def _set_notify(self, text):
        self._notify, self._notify_until = text, time.time() + 8
        self._glitch = 0.35

    def _on_bands(self, bands):
        sens = float(self.s.get("eq_sensitivity", 1.0))
        self.bands = [max(0.0, min(1.0, b * sens)) for b in bands]
        self._bass = self._bass * 0.72 + (sum(self.bands[:3]) / 3) * 0.28
        for i, v in enumerate(self.bands):
            self.peaks[i] = max(v, self.peaks[i] - 0.014)

    def _on_volume(self, vm):
        vol, muted = vm
        if vol is not None and not self._vol_drag:
            self._vol = vol
        self._muted = muted

    def _set_lyrics(self, payload):
        key, data = payload.get("key"), payload.get("data")
        cur = ((self.track or {}).get("title"), (self.track or {}).get("artist"))
        if key == cur:
            self.lyrics = data
            self._lyr_pos = 0.0
            self._lyr_scroll = 0.0

    def _on_track(self, data):
        old = (self.track or {}).get("title")
        self.track = data
        if not data:
            return
        if data.get("position") is not None:
            self._pos_base = float(data["position"])
            self._pos_time = time.time()
        if data.get("title") != old:
            self._art_pix = self._dyn_a = self._dyn_b = self._art_glow = None
            self.lyrics = None
            self._lyr_pos = self._lyr_scroll = 0.0
            self._marquee, self._glitch = 0.0, 0.18
        art = data.get("art")
        if art and self._art_pix is None and self.s.get("show_art", True):
            pm = QPixmap()
            if pm.loadFromData(art):
                self._art_pix = pm
                self._dyn_a, self._dyn_b = palette_from_cover(pm)
                try:
                    self._art_glow = pm.toImage().scaled(1, 1).pixelColor(0, 0)
                except Exception:
                    self._art_glow = None

    def refresh_cover(self):
        key = ((self.track or {}).get("title"), (self.track or {}).get("artist"))
        if not key[0]:
            return
        _ART_MEM.pop(key, None)
        _ART["key"] = None
        self._art_pix = self._dyn_a = self._dyn_b = self._art_glow = None
        try:
            os.remove(_art_disk_path(self.base, key))
        except Exception:
            pass
        self.update()

    def refresh_lyrics(self):
        key = ((self.track or {}).get("title"), (self.track or {}).get("artist"))
        if not key[0]:
            return
        _LYR_MEM.pop(key, None)
        _LYR["key"] = None
        self.lyrics = None
        try:
            os.remove(_cache_path(self.base, "island_lyrics", key, ".json"))
        except Exception:
            pass
        self.notify("Ищу текст заново…")
        self.update()

    def toggle_lyrics(self):
        if not self.s.get("lyrics", True):
            self.notify("Текст песен выключен в настройках.")
            return
        self._lyrics_on = not self._lyrics_on
        if self._lyrics_on:
            self._expanded = True
            self._lyr_scroll = 0.0
            if not self.lyrics:
                self.notify("Ищу текст песни…")
        self._glitch = 0.25
        self.update()

    def _lyric_index(self):
        sy = (self.lyrics or {}).get("synced") or []
        if not sy:
            return -1
        pos = self._display_pos()
        if pos is None:
            return 0
        idx = 0
        for i, (t, _) in enumerate(sy):
            if t <= pos + 0.2:
                idx = i
            else:
                break
        return idx

    def _current_line(self):
        sy = (self.lyrics or {}).get("synced") or []
        i = self._lyric_index()
        if 0 <= i < len(sy):
            return sy[i][1]
        return ""

    def _display_pos(self):
        if not self.track or self.track.get("position") is None:
            return None
        dur = self.track.get("duration")
        if self._seeking:
            return self._seek_frac * (dur or 0)
        pos = self._pos_base
        if self.track.get("playing"):
            pos += time.time() - self._pos_time
        return min(pos, dur) if dur else pos

    def _display_mode(self):
        if self._lyrics_on:
            return "lyrics"
        if self._expanded:
            return "expanded"
        if self._notify and time.time() < self._notify_until:
            return "compact"
        if self.capri_mode in ("listening", "thinking", "speaking"):
            return "compact"
        if self.track:
            return "compact"
        return "minimal"

    def _target_size(self):
        sc = float(self.s.get("scale", 1.0))
        mode = self._display_mode()
        if mode == "lyrics":
            return 560 * sc, 392 * sc
        if mode == "expanded":
            return 560 * sc, 252 * sc
        if mode == "compact":
            return 460 * sc, 70 * sc
        return 216 * sc, 58 * sc

    def _place(self):
        if self._dragging:
            return
        scr = QApplication.primaryScreen().availableGeometry()
        w, h = int(self._w), int(self._h)
        if self.s.get("use_custom_pos") and self.s.get("custom_pos"):
            x, y = int(self.s["custom_pos"][0]), int(self.s["custom_pos"][1])
            x = max(scr.left() - w + 90, min(x, scr.right() - 90))
            y = max(scr.top(), min(y, scr.bottom() - 40))
        else:
            x = scr.x() + (scr.width() - w) // 2
            y = scr.y() + 10 if self.s.get("position", "top") == "top" else scr.y() + scr.height() - h - 12
        self.setGeometry(x, y, w, h)
        self._place_panel()

    def _place_panel(self, force=False):
        if not force and not self._panel.isVisible():
            return
        scr = QApplication.primaryScreen().availableGeometry()
        pw, ph = self._panel.width(), self._panel.height()
        x = self.x() + (self.width() - pw) // 2
        y = self.y() + self.height() + 10
        if y + ph > scr.bottom():
            y = max(scr.top() + 10, self.y() - ph - 10)
        x = max(scr.left() + 8, min(x, scr.right() - pw - 8))
        y = max(scr.top() + 8, min(y, scr.bottom() - ph - 8))
        self._panel.move(x, y)

    def _tick(self):
        now = time.time()
        speed = float(self.s.get("anim_speed", 1.0))
        self._glitch = max(0.0, self._glitch - 0.033 * speed)
        if self.capri_mode == "listening" and self._mode_until and now > self._mode_until:
            self.capri_mode, self._mode_until, self.subtitle = "idle", 0.0, ""
        if self._sleep_until and now >= self._sleep_until:
            self._sleep_until = 0.0
            send_control("pause")
            self.notify("Таймер сна: музыка на паузе.")
        if now - self._fs_check > 1.0:
            self._fs_check = now
            self._fullscreen = _fullscreen_active() if self.s.get("auto_hide_fullscreen", True) else False
        if self._expanded and self.s.get("auto_collapse", True) and not self._pinned and \
                not self._lyrics_on and now - self._last_interact > 14 and not self._panel.isVisible():
            self._expanded = False
        idx = self._lyric_index()
        if idx >= 0:
            self._lyr_pos += (idx - self._lyr_pos) * min(1.0, 0.16 * speed)
        tw, th = self._target_size()
        k = min(1.0, 0.22 * speed)
        self._w += (tw - self._w) * k
        self._h += (th - self._h) * k
        if abs(tw - self._w) < 0.5:
            self._w = tw
        if abs(th - self._h) < 0.5:
            self._h = th
        self.setFixedSize(int(self._w), int(self._h))
        self._place()
        want = self.s.get("enabled", True) and not self._fullscreen
        if self.isVisible() != want:
            self.setVisible(want)
            if not want:
                self._panel.hide()
        if self.capri_mode == "speaking" or self.track:
            self._marquee += 0.9 * speed
        self.update()

    def apply_setting(self, key, value):
        self.s[key] = value
        _save_settings(self.base, self.s)
        self._after_settings()
        if key == "theme":
            self._panel._restyle()
        if key == "lyrics" and not value:
            self._lyrics_on = False
        self.update()

    def _after_settings(self):
        self._apply_blur_now()
        self.setVisible(bool(self.s.get("enabled", True)) and not self._fullscreen)
        self._place()

    def _apply_blur_now(self):
        try:
            _apply_blur(int(self.winId()), bool(self.s.get("blur", False)))
        except Exception:
            pass

    def _capsule(self, w, h, radius):
        p = QPainterPath()
        p.addRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), radius, radius)
        return p

    def _status_color(self):
        return {"listening": self._col("a"), "thinking": self._col("b"),
                "speaking": self._col("info")}.get(self.capri_mode, self._col("c"))

    def _status_label(self):
        return {"listening": "СЛУШАЮ", "thinking": "ДУМАЮ", "speaking": "ГОВОРЮ"}.get(self.capri_mode, "КАПРИ")

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(float(self.s.get("opacity", 0.97)))
        mode = self._display_mode()
        radius = h / 2 if mode in ("compact", "minimal") else 26
        path = self._capsule(w, h, radius)

        glow = self._art_glow if (self._art_glow and mode != "minimal") else self._status_color()
        gi = float(self.s.get("glow", 1.0))
        if self.s.get("reactive_glow", True):
            gi *= 1.0 + self._bass * 0.9
        for i, alpha in ((7, int(22 * gi)), (4, int(38 * gi)), (2, int(52 * gi))):
            p.setPen(QPen(QColor(glow.red(), glow.green(), glow.blue(), max(0, min(255, alpha))), i))
            p.drawPath(self._capsule(w, h, radius))

        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, self._col("surf", 244))
        grad.setColorAt(1, self._col("bg", 250))
        p.fillPath(path, QBrush(grad))

        if self._art_glow and mode != "minimal":
            g = QRadialGradient(QPointF(w * 0.12, h * 0.35), w * 0.6)
            g.setColorAt(0, QColor(self._art_glow.red(), self._art_glow.green(), self._art_glow.blue(), 46))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.save(); p.setClipPath(path); p.fillPath(path, QBrush(g)); p.restore()

        border = QLinearGradient(0, 0, w, 0)
        border.setColorAt(0, self._d("a")); border.setColorAt(0.5, self._d("b"))
        border.setColorAt(1, self._col("c"))
        p.setPen(QPen(QBrush(border), 1.6))
        p.drawPath(path)

        if self._glitch > 0:
            p.save(); p.setClipPath(path)
            for k in range(3):
                y = (self._glitch * 900 + k * 37) % h
                p.fillRect(QRectF(0, y, w, 2), self._col("info", 60))
            p.restore()

        self._rects = {}
        self._lyr_lines = []
        if mode == "lyrics":
            self._paint_lyrics_mode(p, w, h)
        elif mode == "expanded":
            self._paint_expanded(p, w, h)
        elif mode == "compact":
            self._paint_compact(p, w, h)
        else:
            self._paint_minimal(p, w, h)
        p.end()

    def _logo(self, p, x, y, r=13):
        p.setPen(QPen(self._col("b"), 2)); p.setBrush(self._col("a"))
        p.drawEllipse(QPointF(x, y), r, r)
        p.setPen(QPen(self._col("c"), 2)); p.setBrush(self._col("bg"))
        p.drawEllipse(QRectF(x - r * 0.62, y - r * 0.34, r * 1.24, r * 0.68))
        p.setBrush(self._col("c")); p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(x, y), r * 0.18, r * 0.18)

    def _eq(self, p, x, y, width, height, bars=None):
        if not self.s.get("eq", True):
            return
        n = int(bars or self.s.get("eq_bars", 9))
        gap = 2.0
        bw = max(2.0, (width - gap * (n - 1)) / n)
        for i in range(n):
            idx = min(NBANDS - 1, int(i / max(1, n - 1) * (NBANDS - 1)))
            v = self.bands[idx]
            bh = max(2.5, height * (0.10 + 0.90 * v))
            bx = x + i * (bw + gap)
            col = QLinearGradient(bx, y + height - bh, bx, y + height)
            col.setColorAt(0, self._col("info"))
            col.setColorAt(0.55, self._d("b"))
            col.setColorAt(1, self._d("a"))
            p.setPen(Qt.NoPen); p.setBrush(QBrush(col))
            p.drawRoundedRect(QRectF(bx, y + height - bh, bw, bh), bw / 2, bw / 2)
            if self.s.get("peak_hold", True):
                pk = self.peaks[idx]
                py = y + height - max(2.5, height * (0.10 + 0.90 * pk)) - 2.5
                p.setBrush(QColor(255, 255, 255, 150))
                p.drawRoundedRect(QRectF(bx, py, bw, 1.8), 0.9, 0.9)

    def _dot(self, p, x, y):
        col = self._status_color()
        pulse = 1.0 + (0.25 * math.sin((time.time() - self._t0) * 6) if self.capri_mode == "listening" else 0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 60))
        p.drawEllipse(QPointF(x, y), 9 * pulse, 9 * pulse)
        p.setBrush(col)
        p.drawEllipse(QPointF(x, y), 4.5, 4.5)

    def _cover(self, p, rect, small=False):
        p.save()
        rad = 8 if small else 12
        path = QPainterPath(); path.addRoundedRect(rect, rad, rad)
        p.setClipPath(path)
        if self._art_pix and self.s.get("show_art", True):
            p.drawPixmap(rect.toRect(), self._art_pix.scaled(rect.size().toSize(),
                         Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            title = (self.track or {}).get("title") or "K"
            hue = int(hashlib.md5(title.encode("utf-8", "ignore")).hexdigest(), 16) % 360
            g = QRadialGradient(rect.center(), rect.width())
            g.setColorAt(0, QColor.fromHsv(hue, 200, 230)); g.setColorAt(1, self._col("b"))
            p.fillRect(rect, QBrush(g))
            p.setPen(QPen(QColor(255, 255, 255, 40), 2)); p.setBrush(Qt.NoBrush)
            cx, cy = rect.center().x(), rect.center().y()
            rr = rect.width() * 0.30
            p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setBrush(self._col("bg", 200)); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), rr * 0.28, rr * 0.28)
            p.setPen(QPen(self._col("text")))
            p.setFont(QFont("Segoe UI", max(9, int(rect.width() * 0.15)), QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, title[:1].upper())
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 30), 1)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, rad, rad)

    def _marquee_text(self, p, rect, text, font, color=None):
        color = color or self._col("text")
        p.setFont(font)
        tw = p.fontMetrics().horizontalAdvance(text)
        p.save(); p.setClipRect(rect); p.setPen(QPen(color))
        if tw <= rect.width():
            p.drawText(QRectF(rect.x(), rect.y(), rect.width(), rect.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, text)
        else:
            span = tw + 60
            off = self._marquee % span
            p.drawText(QPointF(rect.x() - off, rect.y() + rect.height() * 0.72), text)
            p.drawText(QPointF(rect.x() - off + span, rect.y() + rect.height() * 0.72), text)
        p.restore()

    def _paint_minimal(self, p, w, h):
        self._logo(p, 28, h / 2, 12)
        if self.s.get("idle_clock", True) and self.capri_mode == "idle" and not self.track:
            now = datetime.datetime.now()
            p.setFont(QFont("Consolas", 15, QFont.Bold))
            p.setPen(QPen(self._col("text")))
            p.drawText(QRectF(50, 6, 84, 26), Qt.AlignVCenter | Qt.AlignLeft, now.strftime("%H:%M"))
            p.setFont(QFont("Segoe UI", 7, QFont.Bold))
            p.setPen(QPen(self._col("muted")))
            p.drawText(QRectF(51, 32, 84, 14), Qt.AlignVCenter | Qt.AlignLeft,
                       f"{WEEKDAYS[now.weekday()]} {now:%d.%m}")
        else:
            p.setFont(QFont("Segoe UI", 10, QFont.Bold))
            p.setPen(QPen(self._status_color()))
            p.drawText(QRectF(50, 0, w - 122, h), Qt.AlignVCenter | Qt.AlignLeft, self._status_label())
        self._eq(p, w - 80, h / 2 - 12, 44, 24, bars=7)
        self._dot(p, w - 20, h / 2)

    def _paint_compact(self, p, w, h):
        self._cover(p, QRectF(12, h / 2 - 24, 44, 44), small=True)
        show_play = self.s.get("compact_play", True) and self.track and self.capri_mode == "idle"
        right = 130 if show_play else 100
        tx, tw = 66, w - 66 - right
        lyric = self._current_line() if self.s.get("lyrics_in_compact", True) else ""
        if self._notify and time.time() < self._notify_until:
            main, sub = self._notify, "УВЕДОМЛЕНИЕ"
        elif self.capri_mode == "listening":
            main, sub = "Слушаю тебя…", "КАПРИ"
        elif self.capri_mode == "thinking":
            main, sub = (self.subtitle or "Думаю…"), "ОБРАБОТКА"
        elif self.capri_mode == "speaking":
            main, sub = (self.subtitle or "…"), "КАПРИ ГОВОРИТ"
        elif lyric:
            main, sub = lyric, (self.track or {}).get("title") or ""
        else:
            main = (self.track or {}).get("title") or "Капри"
            sub = (self.track or {}).get("artist") or (self.track or {}).get("source") or ""
            if self.track and self.track.get("playing") is False:
                sub = "пауза · " + sub
        self._marquee_text(p, QRectF(tx, 9, tw, 24), main, QFont("Segoe UI", 10, QFont.Bold))
        p.setFont(QFont("Segoe UI", 8)); p.setPen(QPen(self._col("muted")))
        p.drawText(QRectF(tx, 33, tw, 18), Qt.AlignVCenter | Qt.AlignLeft, sub[:52])
        if show_play:
            r = QRectF(w - 120, h / 2 - 15, 30, 30)
            self._rects["play"] = r
            self._round_btn(p, r, "play")
        self._eq(p, w - 84, h / 2 - 13, 44, 26, bars=7)
        self._dot(p, w - 22, h / 2)
        pos, dur = self._display_pos(), (self.track or {}).get("duration")
        if dur and pos is not None and self.capri_mode == "idle":
            frac = max(0.0, min(1.0, pos / dur))
            p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 22))
            p.drawRoundedRect(QRectF(20, h - 9, w - 40, 2.5), 1.2, 1.2)
            g = QLinearGradient(20, 0, w - 20, 0)
            g.setColorAt(0, self._d("a")); g.setColorAt(1, self._d("b"))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(20, h - 9, (w - 40) * frac, 2.5), 1.2, 1.2)

    def _round_btn(self, p, rect, kind):
        hov = self._hover == kind
        p.setPen(QPen(self._d("a") if hov else QColor(255, 255, 255, 34), 1))
        p.setBrush(QColor(255, 255, 255, 30 if hov else 16))
        p.drawEllipse(rect)
        p.setPen(Qt.NoPen); p.setBrush(self._col("text"))
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        if kind == "prev":
            p.drawRect(QRectF(x + w * 0.24, y + h * 0.30, 2.4, h * 0.40))
            path = QPainterPath()
            path.moveTo(x + w * 0.70, y + h * 0.28); path.lineTo(x + w * 0.38, y + h * 0.5)
            path.lineTo(x + w * 0.70, y + h * 0.72); path.closeSubpath(); p.drawPath(path)
        elif kind == "next":
            p.drawRect(QRectF(x + w * 0.72, y + h * 0.30, 2.4, h * 0.40))
            path = QPainterPath()
            path.moveTo(x + w * 0.30, y + h * 0.28); path.lineTo(x + w * 0.62, y + h * 0.5)
            path.lineTo(x + w * 0.30, y + h * 0.72); path.closeSubpath(); p.drawPath(path)
        elif kind == "play":
            if bool((self.track or {}).get("playing")):
                p.drawRoundedRect(QRectF(x + w * 0.34, y + h * 0.30, w * 0.10, h * 0.40), 1.4, 1.4)
                p.drawRoundedRect(QRectF(x + w * 0.56, y + h * 0.30, w * 0.10, h * 0.40), 1.4, 1.4)
            else:
                path = QPainterPath()
                path.moveTo(x + w * 0.40, y + h * 0.30); path.lineTo(x + w * 0.67, y + h * 0.5)
                path.lineTo(x + w * 0.40, y + h * 0.70); path.closeSubpath(); p.drawPath(path)
        elif kind == "pin":
            p.setBrush(self._col("c") if self._pinned else self._col("muted"))
            p.drawEllipse(QPointF(x + w * 0.5, y + h * 0.42), w * 0.13, w * 0.13)
            p.drawRect(QRectF(x + w * 0.47, y + h * 0.42, w * 0.06, h * 0.30))
        elif kind == "gear":
            p.setBrush(self._col("c") if hov else self._col("muted"))
            p.drawEllipse(QPointF(x + w * 0.5, y + h * 0.5), w * 0.17, w * 0.17)
            p.setBrush(self._col("bg"))
            p.drawEllipse(QPointF(x + w * 0.5, y + h * 0.5), w * 0.07, w * 0.07)

    def _sq_btn(self, p, rect, kind, active=False):
        hov = self._hover == kind
        p.setPen(QPen(self._d("a") if (hov or active) else QColor(255, 255, 255, 30), 1))
        p.setBrush(self._d("a", 40) if active else QColor(255, 255, 255, 28 if hov else 14))
        p.drawRoundedRect(rect, 8, 8)
        col = self._col("text") if active else (self._col("c") if hov else self._col("muted"))
        p.setPen(Qt.NoPen); p.setBrush(col)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        if kind == "shot":
            p.drawRoundedRect(QRectF(x + w * 0.24, y + h * 0.34, w * 0.52, h * 0.36), 2, 2)
            p.setBrush(self._col("bg"))
            p.drawEllipse(QPointF(x + w * 0.5, y + h * 0.52), w * 0.11, w * 0.11)
        elif kind == "timer":
            p.setPen(QPen(col, 1.6)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(x + w * 0.26, y + h * 0.26, w * 0.48, h * 0.48))
            p.drawLine(QPointF(x + w * 0.5, y + h * 0.5), QPointF(x + w * 0.5, y + h * 0.34))
            p.drawLine(QPointF(x + w * 0.5, y + h * 0.5), QPointF(x + w * 0.62, y + h * 0.5))
        elif kind == "mic":
            p.drawRoundedRect(QRectF(x + w * 0.42, y + h * 0.24, w * 0.16, h * 0.32), 3, 3)
            p.setPen(QPen(col, 1.6)); p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(x + w * 0.32, y + h * 0.34, w * 0.36, h * 0.34), 180 * 16, 180 * 16)
            p.drawLine(QPointF(x + w * 0.5, y + h * 0.68), QPointF(x + w * 0.5, y + h * 0.78))
        elif kind == "lyrics":
            for i, frac in enumerate((0.56, 0.42, 0.50)):
                p.drawRoundedRect(QRectF(x + w * 0.24, y + h * (0.30 + i * 0.16),
                                         w * frac, h * 0.075), 1.2, 1.2)

    def _paint_bottom(self, p, w, k, dy):
        pos, dur = self._display_pos(), (self.track or {}).get("duration")
        p.setFont(QFont("Consolas", max(7, int(8 * k))))
        p.setPen(QPen(self._col("muted")))
        p.drawText(QRectF(24 * k, (126 + dy) * k, 140 * k, 16 * k), Qt.AlignVCenter | Qt.AlignLeft, _fmt_time(pos))
        p.drawText(QRectF(w - 164 * k, (126 + dy) * k, 140 * k, 16 * k), Qt.AlignVCenter | Qt.AlignRight, _fmt_time(dur))

        pr = QRectF(24 * k, (148 + dy) * k, w - 48 * k, 9 * k)
        self._rects["progress"] = pr
        frac = max(0.0, min(1.0, pos / dur)) if (dur and pos is not None) else 0.0
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(pr, 4.5 * k, 4.5 * k)
        fill = QRectF(pr.x(), pr.y(), pr.width() * frac, pr.height())
        g = QLinearGradient(fill.x(), 0, max(fill.right(), fill.x() + 1), 0)
        g.setColorAt(0, self._d("a")); g.setColorAt(1, self._d("b"))
        p.setBrush(QBrush(g)); p.drawRoundedRect(fill, 4.5 * k, 4.5 * k)
        p.setBrush(self._col("text"))
        p.drawEllipse(QPointF(fill.right(), pr.center().y()), 5 * k, 5 * k)

        cy = (182 + dy) * k
        self._rects["prev"] = QRectF(w / 2 - 80 * k, cy - 16 * k, 34 * k, 32 * k)
        self._rects["play"] = QRectF(w / 2 - 22 * k, cy - 20 * k, 44 * k, 40 * k)
        self._rects["next"] = QRectF(w / 2 + 46 * k, cy - 16 * k, 34 * k, 32 * k)
        self._round_btn(p, self._rects["prev"], "prev")
        self._round_btn(p, self._rects["next"], "next")
        r = self._rects["play"]
        pg = QLinearGradient(r.topLeft(), r.bottomRight())
        pg.setColorAt(0, self._col("c")); pg.setColorAt(1, self._col("info"))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(pg)); p.drawEllipse(r)
        p.setBrush(self._col("bg"))
        if bool((self.track or {}).get("playing")):
            p.drawRoundedRect(QRectF(r.x() + r.width() * 0.34, r.y() + r.height() * 0.30, r.width() * 0.10, r.height() * 0.40), 1.5, 1.5)
            p.drawRoundedRect(QRectF(r.x() + r.width() * 0.56, r.y() + r.height() * 0.30, r.width() * 0.10, r.height() * 0.40), 1.5, 1.5)
        else:
            path = QPainterPath()
            path.moveTo(r.x() + r.width() * 0.40, r.y() + r.height() * 0.30)
            path.lineTo(r.x() + r.width() * 0.67, r.y() + r.height() * 0.5)
            path.lineTo(r.x() + r.width() * 0.40, r.y() + r.height() * 0.70)
            path.closeSubpath(); p.drawPath(path)

        by = (214 + dy) * k
        vr = QRectF(24 * k, by, 20 * k, 28 * k)
        self._rects["mute"] = vr
        p.setPen(Qt.NoPen)
        p.setBrush(self._col("c") if self._hover == "mute" else self._col("muted"))
        vx, vy = vr.x(), vr.center().y()
        p.drawRect(QRectF(vx + 2 * k, vy - 3 * k, 4 * k, 6 * k))
        tri = QPainterPath()
        tri.moveTo(vx + 6 * k, vy - 3 * k); tri.lineTo(vx + 11 * k, vy - 7 * k)
        tri.lineTo(vx + 11 * k, vy + 7 * k); tri.lineTo(vx + 6 * k, vy + 3 * k)
        tri.closeSubpath(); p.drawPath(tri)
        p.setPen(QPen(self._col("a") if self._muted else self._col("c"), 1.5))
        p.setBrush(Qt.NoBrush)
        if self._muted:
            p.drawLine(QPointF(vx + 14 * k, vy - 5 * k), QPointF(vx + 19 * k, vy + 5 * k))
            p.drawLine(QPointF(vx + 19 * k, vy - 5 * k), QPointF(vx + 14 * k, vy + 5 * k))
        else:
            p.drawArc(QRectF(vx + 11 * k, vy - 6 * k, 10 * k, 12 * k), -60 * 16, 120 * 16)

        vs = QRectF(50 * k, by + 10 * k, 148 * k, 6 * k)
        self._rects["volume"] = QRectF(vs.x(), by, vs.width(), 28 * k)
        vf = 0.0 if self._muted else max(0.0, min(1.0, self._vol / 100))
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(vs, 3 * k, 3 * k)
        vfill = QRectF(vs.x(), vs.y(), vs.width() * vf, vs.height())
        vg = QLinearGradient(vfill.x(), 0, max(vfill.right(), vfill.x() + 1), 0)
        vg.setColorAt(0, self._col("c")); vg.setColorAt(1, self._col("info"))
        p.setBrush(QBrush(vg)); p.drawRoundedRect(vfill, 3 * k, 3 * k)
        p.setBrush(self._col("text"))
        p.drawEllipse(QPointF(vfill.right(), vs.center().y()), 4.5 * k, 4.5 * k)
        p.setFont(QFont("Consolas", max(7, int(7 * k))))
        p.setPen(QPen(self._col("muted")))
        p.drawText(QRectF(50 * k, by - 8 * k, 148 * k, 14 * k), Qt.AlignVCenter | Qt.AlignLeft,
                   "ЗВУК ВЫКЛ" if self._muted else f"ГРОМКОСТЬ {self._vol}%")

        if self.s.get("quick_actions", True):
            for i, kind in enumerate(("shot", "timer", "mic", "lyrics")):
                r = QRectF((214 + i * 34) * k, by, 28 * k, 28 * k)
                self._rects[kind] = r
                self._sq_btn(p, r, kind, active=(kind == "lyrics" and self._lyrics_on))

        sr = QRectF(w - 150 * k, by, 126 * k, 28 * k)
        self._rects["sleep"] = sr
        left = self._sleep_until - time.time()
        active = left > 0
        p.setPen(QPen(self._col("a") if active else QColor(255, 255, 255, 30), 1))
        p.setBrush(self._col("a", 34) if active else QColor(255, 255, 255, 14))
        p.drawRoundedRect(sr, 9 * k, 9 * k)
        p.setPen(QPen(self._col("text") if active else self._col("muted")))
        p.setFont(QFont("Segoe UI", max(7, int(8 * k)), QFont.Bold))
        p.drawText(sr, Qt.AlignCenter,
                   f"СОН ЧЕРЕЗ {int(left // 60)}:{int(left % 60):02d}" if active else "ТАЙМЕР СНА")

    def _paint_expanded(self, p, w, h):
        k = w / 560.0
        self._cover(p, QRectF(18 * k, 16 * k, 104 * k, 104 * k))
        tx, tw = 138 * k, w - 138 * k - 80 * k
        title = (self.track or {}).get("title") or "Капри на связи"
        artist = (self.track or {}).get("artist") or (self.track or {}).get("source") or "плеер не найден"
        self._marquee_text(p, QRectF(tx, 18 * k, tw, 26 * k), title,
                           QFont("Segoe UI", max(9, int(12 * k)), QFont.Bold))
        p.setFont(QFont("Segoe UI", max(8, int(9 * k))))
        p.setPen(QPen(self._col("muted")))
        p.drawText(QRectF(tx, 46 * k, tw, 20 * k), Qt.AlignVCenter | Qt.AlignLeft, artist[:60])
        source = (self.track or {}).get("source") or "ПЛЕЕР"
        p.setFont(QFont("Segoe UI", max(7, int(7 * k)), QFont.Bold))
        badge = QRectF(tx, 72 * k, min(126 * k, tw), 20 * k)
        p.setPen(QPen(self._col("c", 120), 1)); p.setBrush(self._col("c", 26))
        p.drawRoundedRect(badge, 9 * k, 9 * k)
        p.setPen(QPen(self._col("c")))
        p.drawText(badge, Qt.AlignCenter, source[:18].upper())
        self._eq(p, tx, 96 * k, w - tx - 24 * k, 22 * k, bars=12)
        self._rects["pin"] = QRectF(w - 64 * k, 16 * k, 24 * k, 24 * k)
        self._rects["gear"] = QRectF(w - 34 * k, 16 * k, 24 * k, 24 * k)
        self._round_btn(p, self._rects["pin"], "pin")
        self._round_btn(p, self._rects["gear"], "gear")
        self._paint_bottom(p, w, k, 0)

    def _paint_lyrics_mode(self, p, w, h):
        k = w / 560.0
        self._cover(p, QRectF(18 * k, 12 * k, 56 * k, 56 * k), small=True)
        tx, tw = 86 * k, w - 86 * k - 110 * k
        title = (self.track or {}).get("title") or "Капри на связи"
        artist = (self.track or {}).get("artist") or ""
        self._marquee_text(p, QRectF(tx, 14 * k, tw, 24 * k), title,
                           QFont("Segoe UI", max(9, int(11 * k)), QFont.Bold))
        p.setFont(QFont("Segoe UI", max(8, int(8 * k))))
        p.setPen(QPen(self._col("muted")))
        p.drawText(QRectF(tx, 40 * k, tw, 18 * k), Qt.AlignVCenter | Qt.AlignLeft, artist[:56])

        self._rects["pin"] = QRectF(w - 94 * k, 14 * k, 24 * k, 24 * k)
        self._rects["gear"] = QRectF(w - 64 * k, 14 * k, 24 * k, 24 * k)
        self._rects["lyrics"] = QRectF(w - 34 * k, 14 * k, 24 * k, 24 * k)
        self._round_btn(p, self._rects["pin"], "pin")
        self._round_btn(p, self._rects["gear"], "gear")
        self._sq_btn(p, self._rects["lyrics"], "lyrics", active=True)

        area = QRectF(22 * k, 76 * k, w - 44 * k, 182 * k)
        p.save()
        p.setClipRect(area)
        synced = (self.lyrics or {}).get("synced") or []
        plain = (self.lyrics or {}).get("plain") or []
        cx = area.center().x()
        if synced:
            step = 36 * k
            center = area.center().y()
            idx = self._lyric_index()
            for i in range(max(0, idx - 3), min(len(synced), idx + 4)):
                y = center + (i - self._lyr_pos) * step + self._lyr_scroll
                if y < area.top() - step or y > area.bottom() + step:
                    continue
                dist = abs(i - self._lyr_pos)
                cur = (i == idx)
                alpha = 255 if cur else max(35, int(190 - dist * 70))
                size = int((13 if cur else 10) * k)
                p.setFont(QFont("Segoe UI", max(8, size), QFont.Bold if cur else QFont.Normal))
                text = synced[i][1]
                rect = QRectF(area.x(), y - step / 2, area.width(), step)
                if cur and self.s.get("lyrics_glow", True):
                    g = QRadialGradient(QPointF(cx, y), area.width() * 0.5)
                    c = self._d("a")
                    g.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 40))
                    g.setColorAt(1, QColor(0, 0, 0, 0))
                    p.setPen(Qt.NoPen); p.setBrush(QBrush(g))
                    p.drawRect(rect)
                p.setPen(QPen(self._d("a", alpha) if cur else self._col("text", alpha)))
                p.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, text)
                self._lyr_lines.append((synced[i][0], QRectF(rect)))
            fade_top = QLinearGradient(0, area.top(), 0, area.top() + 34 * k)
            fade_top.setColorAt(0, self._col("bg", 235)); fade_top.setColorAt(1, self._col("bg", 0))
            p.fillRect(QRectF(area.x(), area.top(), area.width(), 34 * k), QBrush(fade_top))
            fade_bot = QLinearGradient(0, area.bottom() - 34 * k, 0, area.bottom())
            fade_bot.setColorAt(0, self._col("bg", 0)); fade_bot.setColorAt(1, self._col("bg", 235))
            p.fillRect(QRectF(area.x(), area.bottom() - 34 * k, area.width(), 34 * k), QBrush(fade_bot))
        elif plain:
            p.setFont(QFont("Segoe UI", max(8, int(10 * k))))
            p.setPen(QPen(self._col("text", 210)))
            step = 22 * k
            total = len(plain) * step
            pos, dur = self._display_pos(), (self.track or {}).get("duration")
            auto = (pos / dur) * max(0.0, total - area.height()) if (dur and pos is not None) else 0.0
            off = max(0.0, min(auto + self._lyr_scroll, max(0.0, total - area.height() + 40 * k)))
            for i, line in enumerate(plain):
                y = area.top() + i * step - off
                if area.top() - step < y < area.bottom():
                    p.drawText(QRectF(area.x(), y, area.width(), step),
                               Qt.AlignHCenter | Qt.AlignVCenter, line)
        else:
            p.setFont(QFont("Segoe UI", max(9, int(11 * k))))
            p.setPen(QPen(self._col("muted")))
            msg = "Ищу текст песни…" if self.s.get("lyrics", True) else "Текст песен выключен в настройках"
            if (self.lyrics or {}).get("instrumental"):
                msg = "Инструментал — слов нет"
            elif self.lyrics is None and self.track:
                msg = "Ищу текст песни…"
            elif self.lyrics is not None and not synced and not plain:
                msg = "Текст не найден"
            p.drawText(area, Qt.AlignCenter, msg)
        p.restore()

        p.setFont(QFont("Segoe UI", max(7, int(7 * k)), QFont.Bold))
        p.setPen(QPen(self._col("muted")))
        src = (self.lyrics or {}).get("source")
        hint = "клик по строке — перемотка" if synced else ("колесо — листать" if plain else "")
        p.drawText(QRectF(24 * k, 258 * k, w - 48 * k, 14 * k), Qt.AlignVCenter | Qt.AlignLeft,
                   (src or "").upper())
        p.drawText(QRectF(24 * k, 258 * k, w - 48 * k, 14 * k), Qt.AlignVCenter | Qt.AlignRight, hint)
        self._paint_bottom(p, w, k, 140)

    def _hit(self, pos):
        for kind in ("play", "prev", "next", "pin", "gear", "mute", "shot", "timer", "mic", "lyrics", "sleep"):
            r = self._rects.get(kind)
            if r and r.contains(pos):
                return kind
        for kind in ("progress", "volume"):
            r = self._rects.get(kind)
            if r and r.adjusted(-4, -10, 4, 10).contains(pos):
                return kind
        return None

    def _quick(self, cmd):
        if self.capri:
            self.capri.text_command(cmd)

    def mousePressEvent(self, e):
        self._last_interact = time.time()
        if e.button() == Qt.RightButton:
            self._menu(e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        pos, kind = e.position(), self._hit(e.position())
        if kind is None and self._display_mode() == "lyrics":
            for t, rect in self._lyr_lines:
                if rect.contains(pos):
                    send_control("seek", t)
                    self._pos_base, self._pos_time = t, time.time()
                    self._lyr_scroll = 0.0
                    self.pulse()
                    return
        if kind == "play":
            send_control("play"); self.pulse(); return
        if kind == "prev":
            send_control("prev"); self.pulse(); return
        if kind == "next":
            send_control("next"); self.pulse(); return
        if kind == "pin":
            self._pinned = not self._pinned; self.update(); return
        if kind == "gear":
            self._panel.show_near(); return
        if kind == "mute":
            volume_mute(); return
        if kind == "shot":
            self._quick("сделай скриншот"); self.pulse(); return
        if kind == "timer":
            self._quick("таймер на 5 минут"); self.pulse(); return
        if kind == "mic":
            if self.capri:
                self.capri.call_from_click()
            return
        if kind == "lyrics":
            self.toggle_lyrics(); return
        if kind == "sleep":
            steps = [0, 15, 30, 60]
            cur = 0 if not self._sleep_until else next(
                (m for m in steps if abs((self._sleep_until - time.time()) / 60 - m) < 3), 0)
            nxt = steps[(steps.index(cur) + 1) % len(steps)]
            self._sleep_until = time.time() + nxt * 60 if nxt else 0.0
            self.notify(f"Таймер сна: {nxt} мин." if nxt else "Таймер сна выключен.")
            return
        if kind == "progress":
            dur = (self.track or {}).get("duration")
            if dur:
                self._seeking = True
                r = self._rects["progress"]
                self._seek_frac = max(0.0, min(1.0, (pos.x() - r.x()) / r.width()))
                self.update()
            return
        if kind == "volume":
            self._vol_drag = True
            r = self._rects["volume"]
            self._vol = int(max(0, min(1, (pos.x() - r.x()) / r.width())) * 100)
            volume_set(self._vol)
            self.update()
            return
        self._drag_start = e.globalPosition()
        self._drag_origin = self.pos()
        self._dragging = False

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._seeking:
            r = self._rects["progress"]
            self._seek_frac = max(0.0, min(1.0, (pos.x() - r.x()) / r.width()))
            self.update(); return
        if self._vol_drag:
            r = self._rects["volume"]
            self._vol = int(max(0, min(1, (pos.x() - r.x()) / r.width())) * 100)
            volume_set(self._vol)
            self.update(); return
        if self._drag_start is not None:
            d = e.globalPosition() - self._drag_start
            if not self._dragging and (abs(d.x()) + abs(d.y())) > 6:
                self._dragging = True
                self.s["use_custom_pos"] = True
            if self._dragging:
                self.move(int(self._drag_origin.x() + d.x()), int(self._drag_origin.y() + d.y()))
                self._place_panel()
            return
        kind = self._hit(pos)
        if kind is None and self._display_mode() == "lyrics":
            for _t, rect in self._lyr_lines:
                if rect.contains(pos):
                    kind = "line"
                    break
        if kind != self._hover:
            self._hover = kind
            self.setCursor(Qt.PointingHandCursor if kind else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._seeking:
            self._seeking = False
            dur = (self.track or {}).get("duration")
            if dur:
                target = self._seek_frac * dur
                send_control("seek", target)
                self._pos_base, self._pos_time = target, time.time()
            self.update(); return
        if self._vol_drag:
            self._vol_drag = False
            return
        if self._drag_start is not None:
            if self._dragging:
                self.s["custom_pos"] = [self.x(), self.y()]
                _save_settings(self.base, self.s)
            else:
                if self._lyrics_on:
                    self._lyrics_on = False
                else:
                    self._expanded = not self._expanded
                self._glitch = 0.25
            self._drag_start = None
            self._dragging = False
            self.update()

    def wheelEvent(self, e):
        if self._display_mode() == "lyrics":
            self._lyr_scroll += 28 if e.angleDelta().y() > 0 else -28
            self._lyr_scroll = max(-260, min(260, self._lyr_scroll))
            self._last_interact = time.time()
            self.update()

    def mouseDoubleClickEvent(self, e):
        if self._hit(e.position()) is None:
            self._pinned = not self._pinned
            self.notify("Остров закреплён." if self._pinned else "Остров снова сворачивается сам.")

    def _menu(self, global_pos):
        m = QMenu(self)
        t = self._theme()
        m.setStyleSheet(
            "QMenu{background:rgb(%d,%d,%d);color:rgb(%d,%d,%d);border:1px solid rgb(%d,%d,%d);padding:6px}"
            "QMenu::item{padding:6px 18px;border-radius:6px}"
            "QMenu::item:selected{background:rgb(%d,%d,%d)}"
            "QMenu::separator{height:1px;background:rgb(%d,%d,%d);margin:5px 8px}"
            % (t["bg"] + t["text"] + t["b"] + t["a"] + t["muted"]))

        def add(label, fn, check=None):
            a = QAction(label, m)
            if check is not None:
                a.setCheckable(True); a.setChecked(bool(check))
            a.triggered.connect(lambda _=False, f=fn: f())
            m.addAction(a)

        add("Развернуть" if not self._expanded else "Свернуть",
            lambda: setattr(self, "_expanded", not self._expanded))
        add("Текст песни", self.toggle_lyrics, self._lyrics_on)
        add("Обновить текст", self.refresh_lyrics)
        add("Настройки…", self._panel.show_near)
        add("Обновить обложку", self.refresh_cover)
        m.addSeparator()
        add("Звук выкл", volume_mute, self._muted)
        add("Таймер сна 30 мин", lambda: setattr(self, "_sleep_until", time.time() + 1800),
            self._sleep_until > 0)
        m.addSeparator()
        add("Эквалайзер", lambda: self.apply_setting("eq", not self.s.get("eq", True)), self.s.get("eq", True))
        add("Палитра из обложки", lambda: self.apply_setting("dynamic_palette",
            not self.s.get("dynamic_palette", True)), self.s.get("dynamic_palette", True))
        add("Закрепить", lambda: setattr(self, "_pinned", not self._pinned), self._pinned)
        add("Вернуть на место", lambda: self.apply_setting("use_custom_pos", False),
            self.s.get("use_custom_pos", False))
        m.addSeparator()
        add("Скрыть остров (вернуть из трея)", lambda: self.apply_setting("enabled", False))
        m.exec(global_pos)

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_blur_now()


def attach_island(capri, cfg, base):
    island = Island(cfg, base)
    island.capri = capri
    capri.mode.connect(island.set_mode)
    capri.subtitle.connect(island.set_subtitle)
    capri.summon.connect(island.pulse)
    if getattr(capri, "sched", None):
        old_fire = capri.sched.on_fire
        def fire(text):
            try:
                island.notify(text)
            except Exception:
                pass
            old_fire(text)
        capri.sched.on_fire = fire
    log.info("Капри-остров v11 запущен (текст песен включён)")
    return island