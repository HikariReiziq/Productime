"""Light and dark theme switching through Adw.StyleManager and token CSS providers."""

from collections.abc import Callable

from gi.repository import Adw, Gdk, Gtk

from ..paths import icons_dir, styles_dir
from .tokens import hex_to_rgb, load_tokens

THEMES = ("light", "dark")


class ThemeManager:
    def __init__(self, repo):
        self._repo = repo
        self._tokens = {name: load_tokens(styles_dir() / f"{name}.css") for name in THEMES}
        self._providers: dict[str, Gtk.CssProvider] = {}
        self._base: Gtk.CssProvider | None = None
        self._display: Gdk.Display | None = None
        self._style: Adw.StyleManager | None = None
        self._style_handler = 0
        self._active: str | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def dark(self) -> bool:
        return bool(self._style and self._style.get_dark())

    @property
    def name(self) -> str:
        return "dark" if self.dark else "light"

    def install(self, display: Gdk.Display) -> None:
        self._display = display
        self._style = Adw.StyleManager.get_default()
        self._base = Gtk.CssProvider()
        self._base.load_from_path(str(styles_dir() / "base.css"))
        for name in THEMES:
            provider = Gtk.CssProvider()
            provider.load_from_path(str(styles_dir() / f"{name}.css"))
            self._providers[name] = provider
        Gtk.StyleContext.add_provider_for_display(
            display, self._base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Gtk.IconTheme.get_for_display(display).add_search_path(str(icons_dir()))
        self.apply_saved_preference()
        self._style_handler = self._style.connect("notify::dark", lambda *_: self._apply())
        self._apply()

    def apply_saved_preference(self) -> None:
        """Follow the saved choice, or the desktop setting when the user never chose."""
        saved = self._repo.get_pref("theme", "")
        scheme = {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(saved, Adw.ColorScheme.DEFAULT)
        self._style.set_color_scheme(scheme)

    def uninstall(self) -> None:
        if self._display is None:
            return
        if self._style_handler:
            self._style.disconnect(self._style_handler)
            self._style_handler = 0
        for provider in (self._base, self._providers.get(self._active or "")):
            if provider is not None:
                Gtk.StyleContext.remove_provider_for_display(self._display, provider)
        self._active = None
        self._display = None
        self._listeners.clear()

    def set_dark(self, dark: bool) -> None:
        self._repo.set_pref("theme", "dark" if dark else "light")
        self._style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
        self._apply()

    def _apply(self) -> None:
        name = self.name
        if name == self._active or self._display is None:
            return
        if self._active:
            Gtk.StyleContext.remove_provider_for_display(self._display, self._providers[self._active])
        Gtk.StyleContext.add_provider_for_display(
            self._display, self._providers[name], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._active = name
        for listener in list(self._listeners):
            listener()

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def rgb(self, token: str) -> tuple[float, float, float]:
        """Token color for Cairo drawing, from the same CSS file GTK is using."""
        return hex_to_rgb(self._tokens[self.name][token])
