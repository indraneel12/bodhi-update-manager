"""Integrated tray indicator for the Update Manager.

Owns exactly one tray icon per application instance.  All menu actions
lazily create the UpdateManagerWindow on first use via the application's
_get_or_create_window() helper — the window is never pre-created in tray
mode, so GTK cannot implicitly show it at startup.

Indicator backend priority:
  1. Gtk.StatusIcon         — preferred on Bodhi/Moksha; badge fully supported
  2. AyatanaAppIndicator3   — fallback on desktops with AppIndicator support
  3. AppIndicator3          — classic libappindicator fallback
"""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

if TYPE_CHECKING:
    from bodhi_update.app import UpdateManagerApplication

# ---------------------------------------------------------------------------
# AppIndicator backend detection
# ---------------------------------------------------------------------------

_AppIndicator = None
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as _AppIndicator  # type: ignore[assignment]
except (ValueError, ImportError):
    pass

if _AppIndicator is None:
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as _AppIndicator  # type: ignore[assignment]
    except (ValueError, ImportError):
        pass


# ---------------------------------------------------------------------------
# Badge-dot helper
# ---------------------------------------------------------------------------

# Severity → (fill RGBA, outline RGBA)
_SEVERITY_COLORS = {
    "high": ((220, 60, 60, 255), (120, 220, 120, 255)),    # red fill   / green ring
    "medium": ((246, 195, 66, 255), (120, 220, 120, 255)),  # amber fill / green ring
    "low": ((80, 210, 230, 255), (120, 220, 120, 255)),     # cyan fill  / green ring
}

# APT package name prefixes that warrant amber (medium) severity.
# Keep this list intentionally small: core platform plumbing only.
_MEDIUM_PREFIXES = (
    "linux-", "systemd", "libc", "glibc", "dbus", "openssl",
    "gnupg", "apt", "dpkg", "bash", "coreutils", "util-linux",
    "sudo", "moksha", "bodhi-",
)


def _pkg_severity(name: str, category: str, backend: str) -> str:
    """Return 'high', 'medium', or 'low' for a single update item."""
    if category in ("security", "kernel"):
        return "high"
    if backend == "apt" and name.startswith(_MEDIUM_PREFIXES):
        return "medium"
    return "low"


def _read_pref(key: str, default: bool = True) -> bool:
    """Read a single boolean preference from the shared prefs file."""
    try:
        config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        path = os.path.join(config_home, "bodhi-update-manager", "prefs.json")
        with open(path, "r", encoding="utf-8") as f:
            return bool(json.load(f).get(key, default))
    except Exception:  # pylint: disable=broad-except
        return default


def _add_badge_dot(pixbuf: GdkPixbuf.Pixbuf, severity: str = "medium") -> GdkPixbuf.Pixbuf:
    """Draw a small status dot in the top-right corner. Color reflects severity."""
    fill, outline = _SEVERITY_COLORS.get(severity, _SEVERITY_COLORS["medium"])

    width = pixbuf.get_width()
    height = pixbuf.get_height()

    pixels = bytearray(pixbuf.get_pixels())
    rowstride = pixbuf.get_rowstride()
    n_channels = pixbuf.get_n_channels()

    radius = max(2, width // 12)
    cx = width - radius - 1
    cy = radius + 1

    outline_r2_outer = (radius + 1) * (radius + 1)
    outline_r2_inner = radius * radius

    for y in range(height):
        for x in range(width):
            dx = x - cx
            dy = y - cy
            dist2 = dx * dx + dy * dy

            if dist2 > outline_r2_outer:
                continue

            p = y * rowstride + x * n_channels
            color = outline if dist2 > outline_r2_inner else fill
            pixels[p] = color[0]
            pixels[p + 1] = color[1]
            pixels[p + 2] = color[2]
            if n_channels == 4:
                pixels[p + 3] = color[3]

    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(pixels)),
        pixbuf.get_colorspace(),
        pixbuf.get_has_alpha(),
        pixbuf.get_bits_per_sample(),
        width,
        height,
        rowstride,
    )


# ---------------------------------------------------------------------------
# Tray implementation
# ---------------------------------------------------------------------------

class TrayIcon:
    """System-tray icon whose actions operate on the application's window.

    Receives the *application* (not the window) so it can lazily create the
    window on demand instead of requiring it to exist at construction time.

    Call :meth:`destroy` to remove the icon when the application exits.
    """

    _ICON_NAME = "bodhi-update-manager"
    _ICON_SIZE = 22  # px — standard system-tray icon size

    # Background poll interval (seconds).
    _POLL_INTERVAL = 15 * 60  # 15 minutes
    _INITIAL_DELAY = 5        # seconds after startup before first check

    def __init__(self, app: "UpdateManagerApplication") -> None:
        self._app = app
        self._status_icon = None  # Gtk.StatusIcon handle (preferred)
        self._indicator = None    # AppIndicator3 handle (fallback)
        self._poll_source_id: int | None = None

        menu = self._build_menu()

        # Prefer Gtk.StatusIcon: it works on Moksha/Bodhi and supports the
        # pixbuf badge.  Fall back to AppIndicator on desktops that need it.
        try:
            icon = Gtk.StatusIcon()
            icon.set_from_icon_name(self._ICON_NAME)
            icon.set_tooltip_text("Update Manager")
            icon.set_visible(True)
            icon.connect("activate", lambda _: self._show_window())
            icon.connect("popup-menu", self._on_status_icon_popup)
            self._status_icon = icon
            self._menu = menu  # keep menu alive as long as the icon is alive
        except Exception:  # pylint: disable=broad-except
            # StatusIcon unavailable (e.g. Wayland-only compositor); try AppIndicator.
            if _AppIndicator is not None:
                self._indicator = _AppIndicator.Indicator.new(
                    self._ICON_NAME,
                    self._ICON_NAME,
                    _AppIndicator.IndicatorCategory.APPLICATION_STATUS,
                )
                self._indicator.set_status(_AppIndicator.IndicatorStatus.ACTIVE)
                self._indicator.set_menu(menu)

        # Schedule an initial badge check shortly after startup, then periodically.
        GLib.timeout_add_seconds(self._INITIAL_DELAY, self._on_poll_timer)

    # ------------------------------------------------------------------
    # Menu construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        show_item = Gtk.MenuItem(label="Show / Hide")
        show_item.connect("activate", lambda _: self._toggle_window())
        menu.append(show_item)

        refresh_item = Gtk.MenuItem(label="Check for Updates")
        refresh_item.connect("activate", lambda _: self._check_updates())
        menu.append(refresh_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: self._quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _show_window(self) -> None:
        """Lazily create the window (if needed) and make it visible."""
        win = self._app._get_or_create_window()
        win.show_all()
        win.present()

    def _toggle_window(self) -> None:
        win = self._app._get_or_create_window()
        if win.get_visible():
            win.hide()
        else:
            win.show_all()
            win.present()

    def _check_updates(self) -> None:
        win = self._app._get_or_create_window()
        if not win.get_visible():
            win.show_all()
            win.present()
        win.on_check_updates(None)

    def _quit(self) -> None:
        if self._app._held_for_tray:
            self._app._held_for_tray = False
            self._app.release()
        self._app.quit()

    # ------------------------------------------------------------------
    # StatusIcon popup helper
    # ------------------------------------------------------------------

    def _on_status_icon_popup(
        self, status_icon: Gtk.StatusIcon, button: int, time: int
    ) -> None:
        self._menu.popup(
            None,
            None,
            Gtk.StatusIcon.position_menu,
            status_icon,
            button,
            time,
        )

    # ------------------------------------------------------------------
    # Background update-count polling
    # ------------------------------------------------------------------

    def _on_poll_timer(self) -> bool:
        """GLib timer callback: start a daemon thread to query cached updates."""
        if _read_pref("show_notifications"):
            threading.Thread(target=self._poll_worker, daemon=True).start()
        # Re-arm after _POLL_INTERVAL; use a one-shot source that reschedules itself.
        self._poll_source_id = GLib.timeout_add_seconds(
            self._POLL_INTERVAL, self._on_poll_timer
        )
        return False  # remove the current one-shot source

    def _poll_worker(self) -> None:
        """Read cached update state from all backends (no refresh/privilege tool).

        Runs on a daemon thread; posts badge update back to the main loop.
        """
        try:
            from bodhi_update.backends import get_registry, initialize_registry  # noqa: PLC0415
            initialize_registry()  # idempotent
            count = 0
            severity = "low"
            for backend in get_registry().get_all_backends():
                try:
                    updates, _ = backend.get_updates()
                    for u in updates:
                        if getattr(u, "held", False):
                            continue
                        count += 1
                        s = _pkg_severity(
                            getattr(u, "name", "") or "",
                            getattr(u, "category", "") or "",
                            getattr(u, "backend", "") or "",
                        )
                        if s == "high":
                            severity = "high"
                        elif s == "medium" and severity != "high":
                            severity = "medium"
                except Exception:  # pylint: disable=broad-except
                    pass
            GLib.idle_add(self.set_update_count, count, severity)
        except Exception:  # pylint: disable=broad-except
            pass  # Never crash the tray over a background check.

    # ------------------------------------------------------------------
    # Badge update
    # ------------------------------------------------------------------

    def set_update_count(self, count: int, severity: str = "medium") -> None:
        """Apply (count > 0) or clear (count == 0) the severity-colored badge dot.

        Badge is only applied on the Gtk.StatusIcon path via set_from_pixbuf().
        The AppIndicator path does not support pixbuf injection and gracefully
        keeps the plain icon name instead — no temp files, no crash.
        """
        if self._status_icon is None and self._indicator is None:
            return

        if count == 0:
            tooltip = "Update Manager"
        elif severity == "high":
            tooltip = "Update Manager - Security updates available"
        elif severity == "medium":
            tooltip = "Update Manager - Important updates available"
        else:
            tooltip = "Update Manager - Updates available"

        try:
            if self._status_icon is not None:
                theme = Gtk.IconTheme.get_default()
                pixbuf = theme.load_icon(self._ICON_NAME, self._ICON_SIZE, 0)
                if count > 0 and _read_pref("show_notifications"):
                    pixbuf = _add_badge_dot(pixbuf, severity)
                self._status_icon.set_from_pixbuf(pixbuf)
                self._status_icon.set_tooltip_text(tooltip)

            # AppIndicator: pixbuf badge not supported without temp files.
            # Just ensure the icon name is set correctly; badge is degraded.
            if self._indicator is not None:
                self._indicator.set_icon_full(self._ICON_NAME, tooltip)
        except Exception:  # pylint: disable=broad-except
            pass  # Never crash the tray over a cosmetic update.

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        """Remove the tray icon and stop background polling."""
        if self._poll_source_id is not None:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = None
        if self._status_icon is not None:
            self._status_icon.set_visible(False)
            self._status_icon = None
        if self._indicator is not None:
            self._indicator.set_status(_AppIndicator.IndicatorStatus.PASSIVE)
            self._indicator = None
