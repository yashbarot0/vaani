"""Floating pill (bottom-center, never takes focus) and tray menu. GTK3 on the X11 backend."""
import math
import time
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
from gi.repository import AppIndicator3, Gdk, GLib, Gtk  # noqa: E402

SIZES = {"loading": (128, 30), "idle": (104, 30), "recording": (176, 34), "busy": (150, 34),
         "done": (116, 30), "error": (120, 34)}
BORDER = {"recording": (0.94, 0.27, 0.27), "busy": (0.55, 0.36, 0.96), "done": (0.06, 0.73, 0.51),
          "error": (0.96, 0.62, 0.04)}
CAPTION_X, CAPTION_MAX = 132, 560  # live caption starts after the timer; pill grows up to 560 px
TRAY_ICONS = {"loading": "content-loading-symbolic", "idle": "audio-input-microphone-symbolic",
              "recording": "media-record-symbolic", "busy": "content-loading-symbolic",
              "done": "audio-input-microphone-symbolic", "error": "dialog-warning-symbolic"}


def _label_width(label, bold=True):
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    cr.select_font_face("Sans", 0, int(bold))
    cr.set_font_size(12)
    return cr.text_extents(label).x_advance


def _tail(text, max_width):
    """The newest words of text that fit in max_width, with a leading ellipsis if cut."""
    if _label_width(text, False) <= max_width:
        return text
    words = text.split()
    while words and _label_width("…" + " ".join(words), False) > max_width:
        words.pop(0)
    return "…" + " ".join(words)


def _rounded(cr, w, h):
    r = h / 2
    cr.new_sub_path()
    cr.arc(w - r, r, r - 1, -math.pi / 2, math.pi / 2)
    cr.arc(r, r, r - 1, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()


class Pill(Gtk.Window):
    def __init__(self, on_left, on_right, levels, mode):
        # POPUP = override-redirect: the window manager never manages or focuses it.
        super().__init__(type=Gtk.WindowType.POPUP)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", lambda _w, e: on_left() if e.button == 1 else on_right())
        self.levels, self.mode, self.show_idle = levels, mode, True
        self.state, self.label, self.since = "loading", "Loading…", time.monotonic()
        self.caption = ""
        GLib.timeout_add(50, self._tick)
        self.set_state("loading", "Loading…")

    def set_state(self, state, label=""):
        self.state, self.label, self.since, self.caption = state, label, time.monotonic(), ""
        self._layout()

    def set_caption(self, caption):
        """Live preview while recording; does not reset the timer."""
        self.caption = caption
        self._layout()

    def _layout(self):
        state, label = self.state, self.label
        w, h = SIZES[state]
        if label:
            w = max(w, int(_label_width(label)) + (50 if state == "done" else 32))
        if state == "recording" and self.caption:
            w = min(CAPTION_MAX, max(w, CAPTION_X + int(_label_width(self.caption, False)) + 18))
        self.resize(w, h)
        display = Gdk.Display.get_default()
        geo = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
        self.move(geo.x + (geo.width - w) // 2, geo.y + geo.height - h - 24)
        if state == "idle" and not self.show_idle:
            self.hide()
        else:
            self.show_all()
        self.queue_draw()

    def _tick(self):
        if self.state in ("recording", "busy", "loading"):
            self.queue_draw()
        return True

    def _draw(self, _w, cr):
        w, h = self.get_size()
        t = time.monotonic() - self.since
        cr.set_operator(0)  # CLEAR, so corners stay transparent
        cr.paint()
        cr.set_operator(2)  # OVER
        _rounded(cr, w, h)
        cr.set_source_rgba(0.086, 0.086, 0.118, 0.92)
        cr.fill_preserve()
        if self.state in BORDER:
            alpha = 0.6 + 0.4 * math.sin(t * 5) if self.state == "busy" else 1.0
            cr.set_source_rgba(*BORDER[self.state], alpha)
            cr.set_line_width(2)
        else:
            cr.set_source_rgba(1, 1, 1, 0.15)
            cr.set_line_width(1)
        cr.stroke()
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(12)
        if self.state == "idle":
            self._mic(cr, 20, h / 2)
            self._text(cr, 38, h, "Flow" if self.mode == "flow" else "Raw", (0.8, 0.8, 0.9))
        elif self.state == "recording":
            cr.set_source_rgba(0.94, 0.27, 0.27, 0.6 + 0.4 * math.sin(t * 6))
            cr.arc(18, h / 2, 5, 0, 2 * math.pi)
            cr.fill()
            for i, lv in enumerate(self.levels()):
                bh = max(3, min(h - 12, (lv / 0.15) * (h - 12)))
                cr.set_source_rgb(1, 1, 1)
                cr.rectangle(34 + i * 9, (h - bh) / 2, 5, bh)
                cr.fill()
            self._text(cr, 92, h, f"{int(t) // 60}:{int(t) % 60:02d}", (1, 1, 1))
            if self.caption:
                cr.select_font_face("Sans", 0, 0)
                self._text(cr, CAPTION_X, h, _tail(self.caption, w - CAPTION_X - 18), (0.85, 0.85, 0.92))
        elif self.state == "done":
            cr.set_source_rgb(0.4, 0.95, 0.7)
            cr.set_line_width(2.2)
            cr.move_to(16, h / 2)
            cr.line_to(20, h / 2 + 4)
            cr.line_to(28, h / 2 - 5)
            cr.stroke()
            self._text(cr, 36, h, self.label, (0.4, 0.95, 0.7))
        else:
            color = (1, 0.8, 0.4) if self.state == "error" else (0.9, 0.9, 1)
            self._text(cr, 16, h, self.label, color)

    @staticmethod
    def _mic(cr, x, y):
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(1.6)
        cr.rectangle(x - 3, y - 8, 6, 10)
        cr.fill()
        cr.arc(x, y - 1, 6, 0, math.pi)
        cr.stroke()
        cr.move_to(x, y + 5)
        cr.line_to(x, y + 8)
        cr.stroke()

    @staticmethod
    def _text(cr, x, h, s, rgb):
        cr.set_source_rgb(*rgb)
        cr.move_to(x, h / 2 + 4.5)
        cr.show_text(s)


class Tray:
    def __init__(self, d):
        self.ind = AppIndicator3.Indicator.new("vaani", TRAY_ICONS["loading"],
                                               AppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.ind.set_title("Vaani")
        menu = Gtk.Menu()

        def item(label, cb, check=None):
            it = Gtk.CheckMenuItem(label=label) if check is not None else Gtk.MenuItem(label=label)
            if check is not None:
                it.set_active(check)
                it.connect("toggled", lambda i: cb(i.get_active()))
            else:
                it.connect("activate", lambda _i: cb())
            menu.append(it)
            return it

        self.toggle = item("Start dictation  (Super+Alt+D)", d.toggle)
        item("Cancel", d.cancel)
        menu.append(Gtk.SeparatorMenuItem())
        self.flow = item("Flow mode (AI cleanup)", lambda on: d.set_mode("flow" if on else "raw"),
                         d.mode == "flow")
        item("Show pill when idle", d.set_show_idle, True)
        menu.append(Gtk.SeparatorMenuItem())
        self.last = item("Last transcript: none", d.copy_last)
        self.last.set_sensitive(False)
        item("Quit", d.quit)
        menu.show_all()
        self.ind.set_menu(menu)
        self.ind.set_secondary_activate_target(self.toggle)  # middle-click the icon toggles

    def set_state(self, state):
        self.ind.set_icon_full(TRAY_ICONS[state], state)
        self.toggle.set_label("Stop dictation  (Super+Alt+D)" if state == "recording"
                              else "Start dictation  (Super+Alt+D)")

    def set_last(self, raw):
        short = raw if len(raw) <= 40 else raw[:39] + "…"
        self.last.set_label(f"Copy raw: {short}")
        self.last.set_sensitive(True)
