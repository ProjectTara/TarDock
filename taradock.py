"""TaraDock - dock ala macOS untuk Windows.

Menampilkan app yang di-pin di taskbar Windows sebagai dock di bawah tengah
layar: magnifikasi saat hover, titik penanda app yang sedang berjalan,
klik untuk buka / pindah / minimize jendela.

Keamanan:
- Tidak ada koneksi jaringan. "Cek update" hanya membuka halaman rilis di browser.
- Hanya menjalankan shortcut dari folder pin taskbar milik user sendiri,
  lewat os.startfile (tanpa shell, tanpa argumen tambahan).
- config.json divalidasi ketat; file rusak diganti default.
- Perubahan sistem (auto-hide taskbar, autostart) hanya jika user
  mengaktifkannya, dan dikembalikan saat dimatikan / keluar.
"""
import ctypes
import json
import math
import os
import sys
import tempfile
import time

from PySide6.QtCore import (QFileInfo, QFileSystemWatcher, QLockFile, QPointF,
                            QRectF, Qt, QTimer, QUrl)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor,
                           QDesktopServices, QFont, QFontMetrics, QIcon,
                           QImage, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QFileIconProvider, QMenu,
                               QMessageBox, QSystemTrayIcon, QWidget)

__version__ = "1.0.0"
APP_NAME = "TaraDock"
RELEASES_URL = "https://github.com/ProjectTara/TaraDock/releases"
IS_WIN = sys.platform == "win32"

# ----------------------------------------------------------------- config

ICON_SIZES = (40, 48, 56, 64, 72)
MAG_SCALES = (1.0, 1.3, 1.6, 2.0)
DEFAULTS = {
    "icon_size": 48,
    "magnify": 1.6,           # 1.0 = mati
    "autohide": False,
    "reserve_space": True,    # jendela maximize berhenti di atas dock
    "hide_taskbar": False,    # auto-hide taskbar Windows
    "show_running": True,     # tampilkan app berjalan yang tidak di-pin
    "autostart": False,
    "order": [],              # urutan nama file .lnk
    "hidden": [],             # .lnk yang disembunyikan dari dock
    "taskbar_prev_autohide": None,  # status asli taskbar, untuk dipulihkan
}
MAX_CONFIG_BYTES = 256 * 1024


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _name_list(value):
    """Daftar nama file saja (tanpa path), maks 500, maks 260 karakter."""
    out = []
    if not isinstance(value, list):
        return out
    for v in value[:500]:
        if (isinstance(v, str) and 0 < len(v) <= 260
                and os.path.basename(v) == v and "/" not in v and "\\" not in v
                and v not in (".", "..") and v not in out):
            out.append(v)
    return out


def sanitize(raw):
    cfg = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return cfg
    v = raw.get("icon_size")
    if isinstance(v, int) and not isinstance(v, bool):
        cfg["icon_size"] = min(ICON_SIZES, key=lambda s: abs(s - v))
    v = raw.get("magnify")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        cfg["magnify"] = min(MAG_SCALES, key=lambda s: abs(s - v))
    for k in ("autohide", "reserve_space", "hide_taskbar", "show_running", "autostart"):
        if isinstance(raw.get(k), bool):
            cfg[k] = raw[k]
    cfg["order"] = _name_list(raw.get("order"))
    cfg["hidden"] = _name_list(raw.get("hidden"))
    v = raw.get("taskbar_prev_autohide")
    cfg["taskbar_prev_autohide"] = v if isinstance(v, bool) else None
    return cfg


def load_config(directory=None):
    path = os.path.join(directory or config_dir(), "config.json")
    try:
        if os.path.getsize(path) > MAX_CONFIG_BYTES:
            raise ValueError("config terlalu besar")
        with open(path, "r", encoding="utf-8") as f:
            return sanitize(json.load(f))
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError):
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        return dict(DEFAULTS)


def save_config(cfg, directory=None):
    directory = directory or config_dir()
    path = os.path.join(directory, "config.json")
    data = json.dumps(sanitize(cfg), indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ------------------------------------------------------------ Windows API

class Win:
    """Pembungkus Win32. Di luar Windows semua fungsi jadi no-op."""
    ok = False

    def __init__(self):
        if not IS_WIN:
            return
        from ctypes import wintypes as W
        self.W = W
        u = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        k = self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        s = self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        d = self.dwmapi = ctypes.WinDLL("dwmapi")
        H, LP = W.HWND, ctypes.c_ssize_t

        class RECT(ctypes.Structure):
            _fields_ = [("left", W.LONG), ("top", W.LONG),
                        ("right", W.LONG), ("bottom", W.LONG)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", W.DWORD)]

        class APPBARDATA(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("hWnd", H),
                        ("uCallbackMessage", W.UINT), ("uEdge", W.UINT),
                        ("rc", RECT), ("lParam", LP)]

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", H), ("message", W.UINT), ("wParam", ctypes.c_size_t),
                        ("lParam", LP), ("time", W.DWORD), ("pt", W.POINT)]

        self.RECT, self.MONITORINFO, self.APPBARDATA, self.MSG = RECT, MONITORINFO, APPBARDATA, MSG
        self.ENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, H, LP)

        def sig(fn, res, *args):
            fn.restype, fn.argtypes = res, list(args)

        sig(u.EnumWindows, W.BOOL, self.ENUMPROC, LP)
        sig(u.EnumChildWindows, W.BOOL, H, self.ENUMPROC, LP)
        sig(u.IsWindowVisible, W.BOOL, H)
        sig(u.IsWindow, W.BOOL, H)
        sig(u.IsIconic, W.BOOL, H)
        sig(u.IsZoomed, W.BOOL, H)
        sig(u.GetWindow, H, H, W.UINT)
        sig(u.GetWindowLongPtrW, LP, H, ctypes.c_int)
        sig(u.GetWindowTextLengthW, ctypes.c_int, H)
        sig(u.GetWindowTextW, ctypes.c_int, H, W.LPWSTR, ctypes.c_int)
        sig(u.GetClassNameW, ctypes.c_int, H, W.LPWSTR, ctypes.c_int)
        sig(u.GetWindowThreadProcessId, W.DWORD, H, ctypes.POINTER(W.DWORD))
        sig(u.GetForegroundWindow, H)
        sig(u.SetForegroundWindow, W.BOOL, H)
        sig(u.BringWindowToTop, W.BOOL, H)
        sig(u.ShowWindow, W.BOOL, H, ctypes.c_int)
        sig(u.PostMessageW, W.BOOL, H, W.UINT, ctypes.c_size_t, LP)
        sig(u.GetWindowRect, W.BOOL, H, ctypes.POINTER(RECT))
        sig(u.GetShellWindow, H)
        sig(u.GetDesktopWindow, H)
        sig(u.FindWindowW, H, W.LPCWSTR, W.LPCWSTR)
        sig(u.MonitorFromWindow, W.HANDLE, H, W.DWORD)
        sig(u.MonitorFromPoint, W.HANDLE, W.POINT, W.DWORD)
        sig(u.GetMonitorInfoW, W.BOOL, W.HANDLE, ctypes.POINTER(MONITORINFO))
        sig(u.RegisterWindowMessageW, W.UINT, W.LPCWSTR)
        sig(u.AttachThreadInput, W.BOOL, W.DWORD, W.DWORD, W.BOOL)
        sig(k.GetCurrentThreadId, W.DWORD)
        sig(k.OpenProcess, W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
        sig(k.CloseHandle, W.BOOL, W.HANDLE)
        sig(k.QueryFullProcessImageNameW, W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR,
            ctypes.POINTER(W.DWORD))
        sig(s.SHAppBarMessage, ctypes.c_size_t, W.DWORD, ctypes.POINTER(APPBARDATA))
        sig(d.DwmGetWindowAttribute, ctypes.c_long, H, W.DWORD, ctypes.c_void_p, W.DWORD)
        self.ok = True

    # --- jendela & proses
    def proc_path(self, pid):
        if not self.ok or not pid:
            return ""
        h = self.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = self.W.DWORD(1024)
            if self.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return buf.value
            return ""
        finally:
            self.kernel32.CloseHandle(h)

    def window_pid(self, hwnd):
        pid = self.W.DWORD(0)
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def class_name(self, hwnd):
        buf = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def title(self, hwnd):
        n = self.user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(min(n, 512) + 1)
        self.user32.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value

    def cloaked(self, hwnd):
        v = ctypes.c_int(0)
        if self.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(v), ctypes.sizeof(v)) == 0:
            return v.value != 0
        return False

    def _uwp_path(self, hwnd, host_pid):
        """ApplicationFrameHost membungkus app UWP; cari proses aslinya."""
        found = []

        def cb(child, _):
            pid = self.window_pid(child)
            if pid and pid != host_pid:
                found.append(pid)
                return False
            return True

        self.user32.EnumChildWindows(hwnd, self.ENUMPROC(cb), 0)
        return self.proc_path(found[0]) if found else ""

    SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

    def app_windows(self, own_pid):
        """[(hwnd, exe_path, title)] jendela yang biasa muncul di taskbar, urut z-order."""
        if not self.ok:
            return []
        out = []
        u = self.user32

        def cb(hwnd, _):
            try:
                if not hwnd or not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, 4):  # GW_OWNER
                    return True
                ex = u.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
                if ex & 0x80 and not ex & 0x40000:  # TOOLWINDOW tanpa APPWINDOW
                    return True
                if ex & 0x08000000 and not ex & 0x40000:  # NOACTIVATE
                    return True
                title = self.title(hwnd)
                if not title or self.cloaked(hwnd) or self.class_name(hwnd) in self.SKIP_CLASSES:
                    return True
                pid = self.window_pid(hwnd)
                if pid == own_pid:
                    return True
                path = self.proc_path(pid)
                if path.lower().endswith("\\applicationframehost.exe"):
                    path = self._uwp_path(hwnd, pid) or path
                out.append((int(hwnd), path, title))
            except Exception:
                pass
            return True

        u.EnumWindows(self.ENUMPROC(cb), 0)
        return out

    def foreground(self):
        return int(self.user32.GetForegroundWindow() or 0) if self.ok else 0

    def activate(self, hwnd):
        if not self.ok or not self.user32.IsWindow(hwnd):
            return
        u = self.user32
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        if u.SetForegroundWindow(hwnd) and self.foreground() == hwnd:
            return
        # Cadangan standar: tempel input thread ke thread foreground sebentar.
        fg = u.GetForegroundWindow()
        fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
        me = self.kernel32.GetCurrentThreadId()
        attached = bool(fg_tid and fg_tid != me and u.AttachThreadInput(me, fg_tid, True))
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u.AttachThreadInput(me, fg_tid, False)

    def minimize(self, hwnd):
        if self.ok and self.user32.IsWindow(hwnd):
            self.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE

    def close(self, hwnd):
        if self.ok and self.user32.IsWindow(hwnd):
            self.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    # --- monitor
    def primary_monitor(self):
        """(rcMonitor, handle) monitor utama dalam piksel fisik."""
        if not self.ok:
            return None, None
        hmon = self.user32.MonitorFromPoint(self.W.POINT(0, 0), 1)  # DEFAULTTOPRIMARY
        mi = self.MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if not hmon or not self.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None, None
        return mi.rcMonitor, hmon

    def fullscreen_on_primary(self):
        """True jika jendela aktif menutupi penuh monitor utama (game, video F11)."""
        if not self.ok:
            return False
        u = self.user32
        fg = u.GetForegroundWindow()
        if not fg or fg in (u.GetShellWindow(), u.GetDesktopWindow()):
            return False
        if self.class_name(fg) in self.SKIP_CLASSES:
            return False
        mon, hmon = self.primary_monitor()
        if mon is None or u.MonitorFromWindow(fg, 2) != hmon:
            return False
        r = self.RECT()
        if not u.GetWindowRect(fg, ctypes.byref(r)):
            return False
        covers = (r.left <= mon.left and r.top <= mon.top
                  and r.right >= mon.right and r.bottom >= mon.bottom)
        if not covers:
            return False
        style = u.GetWindowLongPtrW(fg, -16)  # GWL_STYLE
        has_caption = (style & 0x00C00000) == 0x00C00000
        # Maximize biasa (taskbar auto-hide) masih punya caption -> bukan fullscreen.
        return not (u.IsZoomed(fg) and has_caption)

    # --- appbar (reservasi ruang) & taskbar
    def _abd(self, hwnd=0):
        abd = self.APPBARDATA()
        abd.cbSize = ctypes.sizeof(abd)
        abd.hWnd = hwnd or None
        return abd

    def appbar_new(self, hwnd, cb_msg):
        if not self.ok:
            return False
        abd = self._abd(hwnd)
        abd.uCallbackMessage = cb_msg
        return bool(self.shell32.SHAppBarMessage(0, ctypes.byref(abd)))  # ABM_NEW

    def appbar_set_bottom(self, hwnd, height_px):
        """Reservasi strip bawah monitor utama. Return (top, bottom) fisik atau None."""
        mon, _ = self.primary_monitor()
        if mon is None:
            return None
        abd = self._abd(hwnd)
        abd.uEdge = 3  # ABE_BOTTOM
        abd.rc.left, abd.rc.right = mon.left, mon.right
        abd.rc.bottom, abd.rc.top = mon.bottom, mon.bottom - height_px
        self.shell32.SHAppBarMessage(2, ctypes.byref(abd))  # ABM_QUERYPOS
        abd.rc.top = abd.rc.bottom - height_px
        self.shell32.SHAppBarMessage(3, ctypes.byref(abd))  # ABM_SETPOS
        return abd.rc.top, abd.rc.bottom, mon.top

    def appbar_remove(self, hwnd):
        if self.ok:
            abd = self._abd(hwnd)
            self.shell32.SHAppBarMessage(1, ctypes.byref(abd))  # ABM_REMOVE

    def taskbar_autohide(self):
        if not self.ok:
            return None
        tray = self.user32.FindWindowW("Shell_TrayWnd", None)
        if not tray:
            return None
        abd = self._abd(tray)
        return bool(self.shell32.SHAppBarMessage(4, ctypes.byref(abd)) & 1)  # ABM_GETSTATE

    def set_taskbar_autohide(self, on):
        if not self.ok:
            return
        tray = self.user32.FindWindowW("Shell_TrayWnd", None)
        if not tray:
            return
        abd = self._abd(tray)
        state = int(self.shell32.SHAppBarMessage(4, ctypes.byref(abd)))
        abd.lParam = (state | 1) if on else (state & ~1)
        self.shell32.SHAppBarMessage(10, ctypes.byref(abd))  # ABM_SETSTATE

    def register_message(self, name):
        return int(self.user32.RegisterWindowMessageW(name)) if self.ok else 0


WIN = Win()

# ------------------------------------------------------------ pins & items


def pinned_dir():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Internet Explorer",
                        "Quick Launch", "User Pinned", "TaskBar")


def taskband_positions(names):
    """Perkiraan urutan pin taskbar dari registry (Taskband\\Favorites)."""
    if not IS_WIN:
        return {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband") as key:
            blob, _ = winreg.QueryValueEx(key, "Favorites")
    except OSError:
        return {}
    if not isinstance(blob, bytes):
        return {}
    return order_from_blob(blob, names)


def order_from_blob(blob, names):
    pos = {}
    low = blob.lower()
    for n in names:
        for enc in ("utf-16-le", "mbcs" if IS_WIN else "latin-1"):
            try:
                i = low.find(n.lower().encode(enc))
            except (UnicodeEncodeError, LookupError):
                continue
            if i >= 0:
                pos[n] = i
                break
    return pos


def sort_pins(files, order, reg_pos):
    order_low = [o.lower() for o in order]

    def key(f):
        lf = f.lower()
        if lf in order_low:
            return (0, order_low.index(lf), "")
        if f in reg_pos:
            return (1, reg_pos[f], "")
        return (2, 0, lf)

    return sorted(files, key=key)


class Item:
    __slots__ = ("key", "name", "lnk", "target", "pinned", "hwnds", "icon")

    def __init__(self, key, name, lnk, target, pinned):
        self.key, self.name, self.lnk, self.target, self.pinned = key, name, lnk, target, pinned
        self.hwnds = []   # [(hwnd, title)] urut z-order
        self.icon = None


def norm(path):
    return os.path.normcase(os.path.normpath(path)) if path else ""


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{w if os.path.exists(w) else exe}" "{os.path.abspath(__file__)}"'


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    run = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass


def app_icon():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    ico = os.path.join(base, "assets", "taradock.ico")
    if os.path.exists(ico):
        return QIcon(ico)
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(30, 30, 34, 230))
    p.setPen(QPen(QColor(255, 255, 255, 200), 2))
    p.drawRoundedRect(QRectF(4, 30, 56, 26), 9, 9)
    for i, c in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        p.setBrush(QColor(*c))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(10 + i * 16, 35, 13, 13), 3, 3)
    p.end()
    return QIcon(pm)


# ------------------------------------------------------------------- dock


def bump(t):
    return (math.cos(math.pi * t) + 1) / 2 if t < 1 else 0.0


class Dock(QWidget):
    MARGIN = 6       # jarak dock dari bawah layar / taskbar
    LABEL_H = 34

    def __init__(self, cfg, cfg_dir=None, use_system=True):
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                 | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle(APP_NAME)
        self.cfg, self.cfg_dir, self.use_system = cfg, cfg_dir, use_system
        self.items, self.scales = [], []
        self.base_centers = []
        self.mouse = None
        self.hover = -1
        self.reveal = 1.0          # 0 = tersembunyi (auto-hide), 1 = tampil
        self.reveal_target = 1.0
        self.bounce = {}           # key -> waktu mulai
        self.fs_hidden = False
        self.appbar = False
        self.last_setpos = 0.0
        self.tray_menu = None
        if cfg["autohide"]:
            self.reveal = self.reveal_target = 0.0
        self.provider = QFileIconProvider()
        self.icon_cache = {}
        self.cb_msg = WIN.register_message("TaraDockAppBar") if use_system else 0
        self.tb_created = WIN.register_message("TaskbarCreated") if use_system else 0

        self.anim = QTimer(self, interval=16, timeout=self._step)
        self.hide_delay = QTimer(self, singleShot=True, interval=700,
                                 timeout=lambda: self._set_reveal(0.0))
        self.place_later = QTimer(self, singleShot=True, interval=250, timeout=self.place)
        self.reload_later = QTimer(self, singleShot=True, interval=600, timeout=self.reload)
        if use_system:
            self.poll = QTimer(self, interval=1500, timeout=self.refresh_running)
            self.fs_poll = QTimer(self, interval=700, timeout=self.check_fullscreen)
            self.poll.start()
            self.fs_poll.start()
            self.watcher = QFileSystemWatcher(self)
            if os.path.isdir(pinned_dir()):
                self.watcher.addPath(pinned_dir())
            self.watcher.directoryChanged.connect(lambda _: self.reload_later.start())

    # ---------------------------------------------------------- geometri
    @property
    def I(self):
        return self.cfg["icon_size"]

    @property
    def S(self):
        return self.cfg["magnify"]

    def metrics(self):
        I = self.I
        gap = round(I * 0.22)
        pad = round(I * 0.24)
        bar_h = I + 2 * pad
        return I, gap, pad, bar_h

    def sep_index(self):
        """Index item pertama yang tidak di-pin (untuk garis pemisah), atau -1."""
        for i, it in enumerate(self.items):
            if not it.pinned:
                return i if i > 0 else -1
        return -1

    def window_size(self):
        I, gap, pad, bar_h = self.metrics()
        n = max(len(self.items), 1)
        base_w = n * I + (n - 1) * gap + 2 * pad + (gap * 2 if self.sep_index() > 0 else 0)
        extra = math.ceil((self.S - 1) * I * 3.2)
        w = base_w + extra + 2 * I
        h = self.LABEL_H + math.ceil(I * self.S) + 2 * pad + 8
        return int(w), int(h)

    def layout(self, scales=None):
        """List QRectF ikon + QRectF latar dock untuk skala tertentu."""
        I, gap, pad, bar_h = self.metrics()
        scales = scales if scales is not None else self.scales
        W, H = self.width(), self.height()
        sep = self.sep_index()
        widths = [I * s for s in scales]
        total = sum(widths) + gap * max(len(widths) - 1, 0) + 2 * pad
        if sep > 0:
            total += gap * 2
        x = (W - total) / 2 + pad
        bottom = H - pad
        rects = []
        for i, w in enumerate(widths):
            if i == sep:
                x += gap * 2
            rects.append(QRectF(x, bottom - w, w, w))
            x += w + gap
        bg = QRectF((W - total) / 2, H - bar_h, total, bar_h)
        return rects, bg

    def _recompute_base(self):
        rects, _ = self.layout([1.0] * len(self.items))
        self.base_centers = [r.center().x() for r in rects]

    def target_scales(self):
        n = len(self.items)
        if self.mouse is None or self.S <= 1.0 or self.reveal < 0.99:
            return [1.0] * n
        I, gap, _, _ = self.metrics()
        radius = (I + gap) * 2.3
        mx = self.mouse.x()
        return [1 + (self.S - 1) * bump(abs(mx - c) / radius) for c in self.base_centers]

    def hit(self, pos):
        if self.reveal < 0.99:
            return -1
        rects, bg = self.layout()
        I, gap, _, _ = self.metrics()
        if pos.y() < min((r.top() for r in rects), default=bg.top()) - 4 or pos.y() > self.height():
            return -1
        for i, r in enumerate(rects):
            if r.left() - gap / 2 <= pos.x() <= r.right() + gap / 2:
                return i
        return -1

    # ------------------------------------------------------- penempatan
    def apply_size(self):
        w, h = self.window_size()
        self.setFixedSize(w, h)
        self._recompute_base()
        self.scales = self.target_scales()

    def place(self):
        self.apply_size()
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        geo, avail = scr.geometry(), scr.availableGeometry()
        dpr = scr.devicePixelRatio() or 1.0
        _, _, _, bar_h = self.metrics()
        bottom = None
        want_bar = (self.use_system and WIN.ok and self.cfg["reserve_space"]
                    and not self.cfg["autohide"])
        hwnd = int(self.winId())
        if want_bar:
            if not self.appbar:
                self.appbar = WIN.appbar_new(hwnd, self.cb_msg)
            if self.appbar:
                res = WIN.appbar_set_bottom(hwnd, math.ceil((bar_h + 2 * self.MARGIN) * dpr))
                self.last_setpos = time.monotonic()
                if res:
                    _, phys_bottom, mon_top = res
                    bottom = geo.top() + (phys_bottom - mon_top) / dpr - self.MARGIN
        elif self.appbar:
            WIN.appbar_remove(hwnd)
            self.appbar = False
        if bottom is None:
            bottom = (geo.bottom() + 1) if self.cfg["autohide"] else (avail.bottom() + 1 - self.MARGIN)
        x = geo.left() + (geo.width() - self.width()) / 2
        self.move(round(x), round(bottom - self.height()))
        self.update()

    def shutdown(self):
        if self.appbar:
            WIN.appbar_remove(int(self.winId()))
            self.appbar = False

    # --------------------------------------------------------- data
    def icon_for(self, path):
        if path in self.icon_cache:
            return self.icon_cache[path]
        icon = self.provider.icon(QFileInfo(path)) if path and os.path.exists(path) else QIcon()
        if icon.isNull():
            icon = self.provider.icon(QFileIconProvider.IconType.File)
        pm = icon.pixmap(256, 256)
        self.icon_cache[path] = pm
        return pm

    def reload(self):
        d = pinned_dir()
        try:
            files = [f for f in os.listdir(d) if f.lower().endswith(".lnk")
                     and os.path.isfile(os.path.join(d, f))]
        except OSError:
            files = []
        hidden = {h.lower() for h in self.cfg["hidden"]}
        files = [f for f in files if f.lower() not in hidden]
        files = sort_pins(files, self.cfg["order"], taskband_positions(files))
        pins = []
        for f in files:
            lnk = os.path.join(d, f)
            info = QFileInfo(lnk)
            target = info.symLinkTarget() if (info.isShortcut() or info.isSymLink()) else ""
            it = Item("pin:" + f.lower(), os.path.splitext(f)[0], lnk, target, True)
            it.icon = self.icon_for(target if target and os.path.exists(target) else lnk)
            pins.append(it)
        self.items = pins
        self.refresh_running(force_layout=True)

    def refresh_running(self, force_layout=False):
        wins = WIN.app_windows(os.getpid()) if self.use_system else []
        groups = {}
        for hwnd, path, title in wins:
            groups.setdefault(norm(path) or f"hwnd:{hwnd}", []).append((hwnd, path, title))
        pins = [it for it in self.items if it.pinned]
        for it in pins:
            hit = groups.pop(norm(it.target), []) if it.target else []
            it.hwnds = [(h, t) for h, _, t in hit]
        running = []
        if self.cfg["show_running"]:
            old = {it.key: it for it in self.items if not it.pinned}
            for key, lst in groups.items():
                it = old.get("run:" + key)
                if it is None:
                    path = lst[0][1]
                    it = Item("run:" + key, lst[0][2][:80], "", path, False)
                    it.icon = self.icon_for(path)
                it.name = lst[0][2][:80]
                it.hwnds = [(h, t) for h, _, t in lst]
                running.append(it)
            order = [it.key for it in self.items if not it.pinned]
            running.sort(key=lambda it: order.index(it.key) if it.key in order else len(order))
        new = pins + running
        changed = force_layout or [i.key for i in new] != [i.key for i in self.items]
        self.items = new
        if changed:
            self.mouse = None
            self.hover = -1
            self.place()
            self.scales = [1.0] * len(self.items)
        self.update()

    # ------------------------------------------------------- aksi
    def launch(self, it):
        path = it.lnk or it.target
        if not path or not os.path.exists(path):
            QMessageBox.warning(None, APP_NAME, f"File tidak ditemukan:\n{path}")
            return
        self.bounce[it.key] = time.monotonic()
        self.anim.start()
        try:
            os.startfile(path)  # noqa: tanpa shell, tanpa argumen
        except OSError as e:
            QMessageBox.warning(None, APP_NAME, f"Gagal membuka {it.name}:\n{e}")

    def click(self, it):
        self.refresh_running()
        it = next((x for x in self.items if x.key == it.key), it)
        hw = [h for h, _ in it.hwnds]
        if not hw:
            self.launch(it)
            return
        fg = WIN.foreground()
        if fg in hw:
            if len(hw) > 1:  # siklus antar jendela app yang sama
                WIN.activate(hw[(hw.index(fg) + 1) % len(hw)])
            else:
                WIN.minimize(fg)
        else:
            WIN.activate(hw[0])

    def move_item(self, it, delta):
        pins = [x for x in self.items if x.pinned]
        if it not in pins:
            return
        i = pins.index(it)
        j = max(0, min(len(pins) - 1, i + delta))
        if i == j:
            return
        pins.insert(j, pins.pop(i))
        self.cfg["order"] = [os.path.basename(x.lnk) for x in pins]
        self.save()
        self.items = pins + [x for x in self.items if not x.pinned]
        self._recompute_base()
        self.update()

    def hide_item(self, it):
        name = os.path.basename(it.lnk)
        if name and name not in self.cfg["hidden"]:
            self.cfg["hidden"].append(name)
            self.save()
            self.reload()

    def save(self):
        save_config(self.cfg, self.cfg_dir)

    def check_fullscreen(self):
        fs = WIN.fullscreen_on_primary()
        if fs != self.fs_hidden:
            self.fs_hidden = fs
            self.setVisible(not fs)

    # ------------------------------------------------------- event
    def nativeEvent(self, event_type, message):
        if IS_WIN and WIN.ok and self.use_system:
            try:
                msg = WIN.MSG.from_address(int(message))
                if self.cb_msg and msg.message == self.cb_msg:
                    if msg.wParam in (0, 1):     # STATECHANGE / POSCHANGED
                        # Abaikan notifikasi akibat SETPOS kita sendiri (cegah loop).
                        if time.monotonic() - self.last_setpos > 0.5:
                            self.place_later.start()
                    elif msg.wParam == 2:        # FULLSCREENAPP
                        QTimer.singleShot(50, self.check_fullscreen)
                elif self.tb_created and msg.message == self.tb_created:
                    # Explorer restart: daftar ulang appbar & status taskbar.
                    self.appbar = False
                    if self.cfg["hide_taskbar"]:
                        WIN.set_taskbar_autohide(True)
                    self.place_later.start()
            except Exception:
                pass
        return False, 0

    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        self.hover = self.hit(self.mouse)
        self.anim.start()

    def enterEvent(self, e):
        self.hide_delay.stop()
        if self.cfg["autohide"]:
            self._set_reveal(1.0)

    def leaveEvent(self, e):
        self.mouse = None
        self.hover = -1
        self.anim.start()
        if self.cfg["autohide"]:
            self.hide_delay.start()

    def mouseReleaseEvent(self, e):
        idx = self.hit(e.position())
        if idx < 0:
            if e.button() == Qt.RightButton and self.reveal > 0.99:
                self.parent_menu().exec(QCursor.pos())
            return
        it = self.items[idx]
        if e.button() == Qt.LeftButton:
            if e.modifiers() & Qt.ShiftModifier:
                self.launch(it)
            else:
                self.click(it)
        elif e.button() == Qt.MiddleButton:
            self.launch(it)
        elif e.button() == Qt.RightButton:
            self.item_menu(it).exec(QCursor.pos())

    def parent_menu(self):
        return self.tray_menu or QMenu()

    def item_menu(self, it):
        m = QMenu()
        m.addAction("Buka" if not it.hwnds else "Tampilkan", lambda: self.click(it))
        if it.hwnds:
            m.addAction("Jendela baru", lambda: self.launch(it))
            m.addAction(f"Tutup ({len(it.hwnds)} jendela)",
                        lambda: [WIN.close(h) for h, _ in it.hwnds])
        if it.pinned:
            m.addSeparator()
            m.addAction("Geser ke kiri", lambda: self.move_item(it, -1))
            m.addAction("Geser ke kanan", lambda: self.move_item(it, 1))
            m.addAction("Sembunyikan dari dock", lambda: self.hide_item(it))
        path = it.target if it.target and os.path.exists(it.target) else it.lnk
        if path and os.path.exists(path) and '"' not in path:
            import subprocess
            m.addAction("Buka lokasi file",
                        lambda: subprocess.Popen(f'explorer.exe /select,"{path}"'))
        return m

    def _set_reveal(self, v):
        self.reveal_target = v
        self.anim.start()

    def _step(self):
        targets = self.target_scales()
        if len(self.scales) != len(targets):
            self.scales = list(targets)
        moving = False
        for i, t in enumerate(targets):
            d = t - self.scales[i]
            if abs(d) > 0.002:
                self.scales[i] += d * 0.35
                moving = True
            else:
                self.scales[i] = t
        d = self.reveal_target - self.reveal
        if abs(d) > 0.01:
            self.reveal += d * 0.3
            moving = True
        else:
            self.reveal = self.reveal_target
        now = time.monotonic()
        self.bounce = {k: t0 for k, t0 in self.bounce.items() if now - t0 < 1.2}
        if self.bounce:
            moving = True
        self.update()
        if not moving:
            self.anim.stop()

    # ------------------------------------------------------- gambar
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        I, gap, pad, bar_h = self.metrics()
        H = self.height()
        if self.cfg["autohide"] and self.reveal < 1.0:
            # Strip tak terlihat (alpha 1) di tepi bawah untuk memicu munculnya dock.
            _, bg = self.layout([1.0] * len(self.items))
            p.fillRect(QRectF(bg.left(), H - 4, bg.width(), 4), QColor(0, 0, 0, 1))
            p.translate(0, (1 - self.reveal) * (bar_h + self.MARGIN + 2))
            if self.reveal < 0.02:
                p.end()
                return
        rects, bg = self.layout()
        if self.mouse is not None and rects:
            # Area hover tak terlihat agar celah antar ikon tidak memutus hover.
            top = min(r.top() for r in rects) - 2
            p.fillRect(QRectF(bg.left(), top, bg.width(), H - top), QColor(0, 0, 0, 1))
        path = QPainterPath()
        path.addRoundedRect(bg.adjusted(0.5, 0.5, -0.5, -0.5), bar_h * 0.3, bar_h * 0.3)
        p.fillPath(path, QColor(28, 28, 32, 170))
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.drawPath(path)
        sep = self.sep_index()
        if sep > 0 and sep < len(rects):
            x = rects[sep].left() - gap * 1.5
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            p.drawLine(QPointF(x, bg.top() + pad * 0.6), QPointF(x, bg.bottom() - pad * 0.6))
        now = time.monotonic()
        for i, (r, it) in enumerate(zip(rects, self.items)):
            dy = 0.0
            t0 = self.bounce.get(it.key)
            if t0 is not None:
                t = (now - t0) / 0.6
                dy = -abs(math.sin(t * math.pi)) * I * 0.35 * max(0.0, 1 - (now - t0) / 1.2)
            if it.icon is not None and not it.icon.isNull():
                p.drawPixmap(r.translated(0, dy).toRect(), it.icon)
            if it.hwnds:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(235, 235, 240, 230))
                dot = max(3.0, I / 12)
                p.drawEllipse(QPointF(r.center().x(), H - pad / 2), dot / 2, dot / 2)
        if 0 <= self.hover < len(rects):
            self._label(p, rects[self.hover], self.items[self.hover].name)
        p.end()

    def _label(self, p, r, text):
        f = QFont(self.font())
        f.setPointSizeF(10)
        p.setFont(f)
        fm = QFontMetrics(f)
        text = fm.elidedText(text, Qt.ElideRight, 320)
        w, h = fm.horizontalAdvance(text) + 20, fm.height() + 8
        x = max(2, min(self.width() - w - 2, r.center().x() - w / 2))
        y = max(2, r.top() - h - 8)
        box = QRectF(x, y, w, h)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.setBrush(QColor(28, 28, 32, 225))
        p.drawRoundedRect(box, 7, 7)
        p.setPen(QColor(245, 245, 247))
        p.drawText(box, Qt.AlignCenter, text)


# ------------------------------------------------------------------- tray


class Controller:
    def __init__(self, app):
        self.app = app
        self.cfg = load_config()
        self.dock = Dock(self.cfg)
        self.restore_taskbar_if_needed()
        self.tray = QSystemTrayIcon(app_icon(), app)
        self.tray.setToolTip(f"{APP_NAME} {__version__}")
        self.menu = self.build_menu()
        self.tray.setContextMenu(self.menu)
        self.dock.tray_menu = self.menu
        self.tray.show()
        app.aboutToQuit.connect(self.on_quit)
        scr = app.primaryScreen()
        app.primaryScreenChanged.connect(lambda _: self.dock.place_later.start())
        if scr:
            scr.geometryChanged.connect(lambda _: self.dock.place_later.start())
            scr.availableGeometryChanged.connect(lambda _: self.dock.place_later.start())
        self.dock.reload()
        self.dock.show()
        self.dock.place()
        if self.cfg["hide_taskbar"]:
            self.apply_taskbar(True)

    def restore_taskbar_if_needed(self):
        """Pulihkan taskbar jika sesi sebelumnya crash saat taskbar disembunyikan."""
        if not self.cfg["hide_taskbar"] and self.cfg["taskbar_prev_autohide"] is not None:
            WIN.set_taskbar_autohide(self.cfg["taskbar_prev_autohide"])
            self.cfg["taskbar_prev_autohide"] = None
            save_config(self.cfg)

    def apply_taskbar(self, hide):
        if hide:
            if self.cfg["taskbar_prev_autohide"] is None:
                prev = WIN.taskbar_autohide()
                self.cfg["taskbar_prev_autohide"] = bool(prev) if prev is not None else False
                save_config(self.cfg)
            WIN.set_taskbar_autohide(True)
        elif self.cfg["taskbar_prev_autohide"] is not None:
            WIN.set_taskbar_autohide(self.cfg["taskbar_prev_autohide"])
            self.cfg["taskbar_prev_autohide"] = None
            save_config(self.cfg)
        self.dock.place_later.start()

    def toggle(self, key, value):
        self.cfg[key] = value
        if key == "hide_taskbar":
            self.apply_taskbar(value)
        elif key == "autostart":
            set_autostart(value)
        elif key == "autohide":
            self.dock.reveal_target = self.dock.reveal = (0.0 if value else 1.0)
        save_config(self.cfg)
        if key == "show_running":
            self.dock.refresh_running(force_layout=True)
        else:
            self.dock.place()
        self.dock.update()

    def build_menu(self):
        m = QMenu()
        m.addAction(f"{APP_NAME} {__version__}").setEnabled(False)
        m.addSeparator()

        size = m.addMenu("Ukuran ikon")
        g = QActionGroup(size)
        for s in ICON_SIZES:
            a = QAction(f"{s} px", size, checkable=True, checked=self.cfg["icon_size"] == s)
            a.triggered.connect(lambda _=False, s=s: self.toggle("icon_size", s))
            g.addAction(a)
            size.addAction(a)
        mag = m.addMenu("Magnifikasi")
        g2 = QActionGroup(mag)
        for s in MAG_SCALES:
            label = "Mati" if s == 1.0 else f"{s:.1f}x"
            a = QAction(label, mag, checkable=True, checked=self.cfg["magnify"] == s)
            a.triggered.connect(lambda _=False, s=s: self.toggle("magnify", s))
            g2.addAction(a)
            mag.addAction(a)

        def check(text, key):
            a = QAction(text, m, checkable=True, checked=self.cfg[key])
            a.toggled.connect(lambda v, k=key: self.toggle(k, v))
            m.addAction(a)

        m.addSeparator()
        check("Sembunyikan dock otomatis", "autohide")
        check("Sisakan ruang untuk dock", "reserve_space")
        check("Tampilkan app berjalan (tidak di-pin)", "show_running")
        check("Sembunyikan taskbar Windows (auto-hide)", "hide_taskbar")
        check("Jalankan saat Windows mulai", "autostart")
        m.addSeparator()
        m.addAction("Muat ulang pin", self.dock.reload)
        self.unhide = m.addAction("Tampilkan lagi item tersembunyi", self.unhide_all)
        m.aboutToShow.connect(lambda: self.unhide.setEnabled(bool(self.cfg["hidden"])))
        m.addAction("Cek update (buka halaman rilis)",
                    lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        m.addSeparator()
        m.addAction("Keluar", self.app.quit)
        return m

    def unhide_all(self):
        self.cfg["hidden"] = []
        save_config(self.cfg)
        self.dock.reload()

    def on_quit(self):
        self.dock.shutdown()
        if self.cfg["taskbar_prev_autohide"] is not None:
            WIN.set_taskbar_autohide(self.cfg["taskbar_prev_autohide"])
            # Simpan status agar dipasang lagi saat start berikutnya.
            self.cfg["taskbar_prev_autohide"] = None
            save_config(self.cfg)


# --------------------------------------------------------------- selftest


def selftest(report_path):
    """Dijalankan CI (Windows) sebelum & sesudah build. Exit code 0 = lulus."""
    import random
    results, ok = [], True

    def check(name, fn):
        nonlocal ok
        try:
            detail = fn()
            results.append({"test": name, "ok": True, "detail": detail})
        except Exception as e:  # pragma: no cover
            ok = False
            results.append({"test": name, "ok": False, "detail": f"{type(e).__name__}: {e}"})

    def t_sanitize():
        rnd = random.Random(7)
        junk = [None, 1, "x", [], {}, True, 1e309, -5, 999, [1, "a/b", "..", "ok.lnk"] * 200,
                {"nested": {}}, "A" * 500, float("nan")]
        for _ in range(3000):
            raw = {k: rnd.choice(junk) for k in DEFAULTS if rnd.random() < 0.8}
            c = sanitize(raw)
            assert set(c) == set(DEFAULTS)
            assert c["icon_size"] in ICON_SIZES and c["magnify"] in MAG_SCALES
            assert all(isinstance(x, str) and "/" not in x and "\\" not in x for x in c["order"])
            assert len(c["order"]) <= 500
        return "3000 config acak OK"

    def t_config_io():
        with tempfile.TemporaryDirectory() as d:
            c = dict(DEFAULTS, icon_size=64, order=["Chrome.lnk"])
            save_config(c, d)
            assert load_config(d)["icon_size"] == 64
            with open(os.path.join(d, "config.json"), "w") as f:
                f.write("{rusak")
            assert load_config(d) == DEFAULTS
            assert os.path.exists(os.path.join(d, "config.json.bad"))
        return "simpan / muat / file rusak OK"

    def t_layout():
        app = QApplication.instance()
        assert app is not None
        cfg = dict(DEFAULTS)
        d = Dock(cfg, use_system=False)
        pm = QPixmap(64, 64)
        pm.fill(QColor(80, 140, 255))
        checked = 0
        for n_pin, n_run in ((0, 0), (1, 0), (5, 3), (14, 6), (30, 0)):
            d.items = [Item(f"pin:{i}", f"App {i}", "", "", True) for i in range(n_pin)]
            d.items += [Item(f"run:{i}", f"Run {i}", "", "", False) for i in range(n_run)]
            for it in d.items:
                it.icon = pm
            for size in ICON_SIZES:
                for mag in MAG_SCALES:
                    cfg["icon_size"], cfg["magnify"] = size, mag
                    d.apply_size()
                    W, H = d.width(), d.height()
                    for mx in range(-50, W + 50, 7):
                        d.mouse = QPointF(mx, H - 10)
                        rects, bg = d.layout(d.target_scales())
                        for a, b in zip(rects, rects[1:]):
                            assert a.right() <= b.left() + 1e-6, "ikon tumpang tindih"
                        for r in rects:
                            assert r.left() >= -1e-6 and r.right() <= W + 1e-6, "keluar jendela"
                            assert r.top() >= d.LABEL_H - 1, "menabrak area label"
                        assert bg.left() >= -1e-6 and bg.right() <= W + 1e-6
                        checked += 1
        img = QImage(d.size(), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        d.render(img)
        return f"{checked} posisi kursor OK"

    def t_blob():
        names = ["Brave.lnk", "Telegram.lnk", "File Explorer.lnk", "Zed.lnk"]
        blob = b"\x00" * 10 + "telegram.lnk".encode("utf-16-le") + b"\x01" * 5 \
            + "Brave.lnk".encode("utf-16-le") + "FILE EXPLORER.LNK".encode("utf-16-le")
        pos = order_from_blob(blob, names)
        out = sort_pins(names, [], pos)
        assert out == ["Telegram.lnk", "Brave.lnk", "File Explorer.lnk", "Zed.lnk"], out
        out = sort_pins(names, ["Zed.lnk"], pos)
        assert out[0] == "Zed.lnk", out
        return "urutan pin OK"

    def t_win():
        if not IS_WIN:
            return "dilewati (bukan Windows)"
        assert WIN.ok
        assert ctypes.sizeof(WIN.APPBARDATA) == (48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 36)
        assert ctypes.sizeof(WIN.MONITORINFO) == 40
        wins = WIN.app_windows(os.getpid())
        assert isinstance(wins, list)
        WIN.fullscreen_on_primary()
        WIN.foreground()
        state = WIN.taskbar_autohide()
        files = os.listdir(pinned_dir()) if os.path.isdir(pinned_dir()) else []
        taskband_positions(files)
        assert autostart_command().startswith('"')
        return f"{len(wins)} jendela, taskbar autohide={state}, {len(files)} pin"

    app = QApplication.instance() or QApplication(sys.argv[:1])
    for name, fn in (("sanitize", t_sanitize), ("config_io", t_config_io),
                     ("layout", t_layout), ("pin_order", t_blob), ("win32", t_win)):
        check(name, fn)
    del app
    report = {"version": __version__, "ok": ok, "results": results}
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


# ------------------------------------------------------------------- main


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2])
    if len(sys.argv) >= 2 and sys.argv[1] == "--version":
        print(__version__)
        return 0
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    lock = QLockFile(os.path.join(tempfile.gettempdir(), f"{APP_NAME}.lock"))
    if not lock.tryLock(200):
        return 0  # sudah berjalan
    app.setWindowIcon(app_icon())
    app._controller = Controller(app)  # simpan referensi agar tidak di-GC
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
