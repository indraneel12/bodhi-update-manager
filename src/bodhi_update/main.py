"""Entry point for the Bodhi Update Manager."""

import os


def _sanitize_gtk_modules() -> None:
    """Remove the broken xapp GTK module from the environment if present.

    Some Bodhi setups export GTK3_MODULES=xapp-gtk3-module even when the
    module is not installed, causing GTK warnings on startup.
    """
    val = os.environ.get("GTK3_MODULES")
    if not val:
        return

    parts = [m for m in val.split(":") if m and m != "xapp-gtk3-module"]
    if parts:
        os.environ["GTK3_MODULES"] = ":".join(parts)
    else:
        os.environ.pop("GTK3_MODULES", None)


_sanitize_gtk_modules()

from bodhi_update.app import main  # noqa: E402


if __name__ == "__main__":
    from gi.repository import Gtk
    Gtk.Window.set_default_icon_name("bodhi-update-manager")
    main()
