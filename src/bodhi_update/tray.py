"""Integrated tray indicator for the Update Manager.

Owns exactly one tray icon per application instance.  All menu actions
lazily create the UpdateManagerWindow on first use via the application's
_get_or_create_window() helper — the window is never pre-created in tray
mode, so GTK cannot implicitly show it at startup.

Indicator backend priority:
  1. AppIndicator3 (Ayatana) — preferred on modern Ubuntu/Debian DE stacks
  2. Gtk.StatusIcon          — universal GTK3 fallback
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

if TYPE_CHECKING:
    from bodhi_update.app import UpdateManagerApplication

# Try to import AppIndicator3 (Ayatana or classic libappindicator).
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
# Tray implementation
# ---------------------------------------------------------------------------

class TrayIcon:
    """System-tray icon whose actions operate on the application's window.

    Receives the *application* (not the window) so it can lazily create the
    window on demand instead of requiring it to exist at construction time.

    Call :meth:`destroy` to remove the icon when the application exits.
    """

    def __init__(self, app: "UpdateManagerApplication") -> None:
        self._app = app
        self._indicator = None  # AppIndicator3 handle if available
        self._status_icon = None  # Gtk.StatusIcon handle if falling back

        menu = self._build_menu()

        if _AppIndicator is not None:
            self._indicator = _AppIndicator.Indicator.new(
                "bodhi-update-manager",
                "bodhi-update-manager",
                _AppIndicator.IndicatorCategory.APPLICATION_STATUS,
            )
            self._indicator.set_status(_AppIndicator.IndicatorStatus.ACTIVE)
            self._indicator.set_menu(menu)
        else:
            icon = Gtk.StatusIcon()
            icon.set_from_icon_name("bodhi-update-manager")
            icon.set_tooltip_text("Update Manager")
            icon.set_visible(True)
            icon.connect("activate", lambda _: self._show_window())
            icon.connect("popup-menu", self._on_status_icon_popup)
            self._status_icon = icon
            self._menu = menu  # keep alive

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
    # Lifecycle
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        """Remove the tray icon."""
        if self._status_icon is not None:
            self._status_icon.set_visible(False)
            self._status_icon = None
        if self._indicator is not None:
            self._indicator.set_status(_AppIndicator.IndicatorStatus.PASSIVE)
            self._indicator = None
