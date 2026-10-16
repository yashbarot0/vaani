"""Floating pill (bottom-center), Bob button (bottom-right), Bob's card and the tray menu.
GTK3 on the X11 backend; every window is a POPUP, so none of them ever takes keyboard focus."""
import math
import time
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import AppIndicator3, Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from . import config  # noqa: E402

SIZES = {"loading": (128, 30), "idle": (104, 30), "recording": (176, 34), "busy": (150, 34),
         "done": (116, 30), "error": (120, 34)}
BORDER = {"recording": (0.94, 0.27, 0.27), "busy": (0.55, 0.36, 0.96), "done": (0.06, 0.73, 0.51),
          "error": (0.96, 0.62, 0.04)}
BOB_ACCENT = (0.33, 0.42, 1.0)  # Bob's helmet blue: marks everything that goes online
AMBER = (0.96, 0.62, 0.04)
BG = (0.086, 0.086, 0.118)
MARGIN = 24
CAPTION_X, CAPTION_MAX = 132, 560  # live caption starts after the timer; pill grows up to 560 px
TRAY_ICONS = {"loading": "content-loading-symbolic", "idle": "audio-input-microphone-symbolic",
              "recording": "media-record-symbolic", "busy": "content-loading-symbolic",
              "done": "audio-input-microphone-symbolic", "error": "dialog-warning-symbolic"}


def _monitor():
    display = Gdk.Display.get_default()
    return (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()


def _popup(window):
    """Make a window an unfocusable, translucent overlay."""
    visual = window.get_screen().get_rgba_visual()
    if visual:
        window.set_visual(visual)
    window.set_app_paintable(True)
    window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)


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


# ---- Bob's face ------------------------------------------------------------------------
_bob_pixbufs = {}
# Feature positions, in the units of each drawing: the 170 px cropped PNG, or the 22-unit fallback robot.
PNG_FACE = {"unit": 170, "skin": 0.957, "eyes": [(60, 123), (111, 123)], "eye_r": 14.5,
            "mouth": (86, 148), "mouth_box": (66, 139, 40, 18), "mouth_w": 12, "line": 3.5}
DRAWN_FACE = {"unit": 22, "skin": 0.95, "eyes": [(8.5, 14.5), (13.5, 14.5)], "eye_r": 1.8,
              "mouth": (11, 18.2), "mouth_box": (8, 16.6, 6, 3), "mouth_w": 2.4, "line": 0.6}


def _pixbuf(size):
    if size not in _bob_pixbufs:
        try:
            _bob_pixbufs[size] = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(config.BOB_ICON), size, size, True)
        except GLib.Error:
            _bob_pixbufs[size] = None
    return _bob_pixbufs[size]


def bob_face(cr, x, y, size, mouth=None, blink=False):
    """Bob's head from ~/.config/vaani/bob.png (IBM's mascot is not committed), else a drawn robot.
    mouth: None = the normal smile, 0..1 = open by that much while speaking. blink closes the eyes."""
    pb = _pixbuf(size)
    if pb:
        Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
        cr.paint()
        g = PNG_FACE
    else:
        s = size / 22
        cr.set_source_rgb(*BOB_ACCENT)
        cr.arc(x + 11 * s, y + 10 * s, 9 * s, math.pi, 2 * math.pi)  # helmet
        cr.fill()
        cr.set_source_rgb(0.95, 0.95, 0.97)
        cr.rectangle(x + 4 * s, y + 10 * s, 14 * s, 10 * s)  # face
        cr.fill()
        g = DRAWN_FACE
        if not blink:
            cr.set_source_rgb(0.1, 0.1, 0.14)
            for ex, ey in g["eyes"]:
                cr.arc(x + ex * s, y + ey * s, 1.6 * s, 0, 2 * math.pi)
                cr.fill()
    s = size / g["unit"]
    if blink:
        for ex, ey in g["eyes"]:
            cr.set_source_rgb(g["skin"], g["skin"], g["skin"])
            cr.arc(x + ex * s, y + ey * s, g["eye_r"] * s, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(0.2, 0.2, 0.24)
            cr.set_line_width(g["line"] * s)
            cr.move_to(x + (ex - g["eye_r"] * 0.7) * s, y + ey * s)
            cr.line_to(x + (ex + g["eye_r"] * 0.7) * s, y + ey * s)
            cr.stroke()
    if mouth is not None:
        bx, by, bw, bh = g["mouth_box"]
        cr.set_source_rgb(g["skin"], g["skin"], g["skin"])
        cr.rectangle(x + bx * s, y + by * s, bw * s, bh * s)
        cr.fill()
        mx, my = g["mouth"]
        open_h = (0.15 + 0.85 * max(0.0, min(1.0, mouth))) * g["mouth_w"] * 0.8
        cr.save()
        cr.translate(x + mx * s, y + my * s)
        cr.scale(g["mouth_w"] * s, open_h * s)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.set_source_rgb(0.2, 0.12, 0.16)
        cr.fill()


def _rounded(cr, w, h):
    r = h / 2
    cr.new_sub_path()
    cr.arc(w - r, r, r - 1, -math.pi / 2, math.pi / 2)
    cr.arc(r, r, r - 1, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()


# ---- Dictation pill --------------------------------------------------------------------
class Pill(Gtk.Window):
    def __init__(self, on_left, on_right, levels, mode):
        super().__init__(type=Gtk.WindowType.POPUP)
        _popup(self)
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
        geo = _monitor()
        self.move(geo.x + (geo.width - w) // 2, geo.y + geo.height - h - MARGIN)
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
        cr.set_source_rgba(*BG, 0.92)
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


# ---- Bob button (bottom-right) ---------------------------------------------------------
class BobButton(Gtk.Window):
    """Bob's face in the bottom-right corner. Click to ask; it listens, thinks and talks."""
    SIZE, FACE = 64, 44

    def __init__(self, on_left, on_right, mic_levels, speak_level):
        super().__init__(type=Gtk.WindowType.POPUP)
        _popup(self)
        self.connect("draw", self._draw)
        self.connect("button-press-event", lambda _w, e: on_left() if e.button == 1 else on_right())
        self.mic_levels, self.speak_level = mic_levels, speak_level
        self.state, self.since, self.blink, self.visible_wanted = "idle", time.monotonic(), False, True
        self.resize(self.SIZE, self.SIZE)
        GLib.timeout_add(40, self._tick)
        GLib.timeout_add_seconds(4, self._blink)
        self.set_state("idle")

    def set_state(self, state):
        """idle | recording | busy | speaking | error"""
        self.state, self.since = state, time.monotonic()
        geo = _monitor()
        self.move(geo.x + geo.width - self.SIZE - MARGIN, geo.y + geo.height - self.SIZE - MARGIN)
        if self.visible_wanted or state != "idle":
            self.show_all()
        else:
            self.hide()
        self.queue_draw()

    def _tick(self):
        if self.state != "idle":
            self.queue_draw()
        return True

    def _blink(self):
        if self.state in ("idle", "speaking", "busy"):
            self.blink = True
            self.queue_draw()
            GLib.timeout_add(140, lambda: setattr(self, "blink", False) or self.queue_draw() or False)
        return True

    def _draw(self, _w, cr):
        n, t = self.SIZE, time.monotonic() - self.since
        cr.set_operator(0)
        cr.paint()
        cr.set_operator(2)
        c, r = n / 2, n / 2 - 4
        cr.arc(c, c, r, 0, 2 * math.pi)
        cr.set_source_rgba(*BG, 0.92)
        cr.fill()
        if self.state == "recording":
            cr.set_source_rgb(*BOB_ACCENT)
            cr.set_line_width(2 + min(4.0, max(self.mic_levels() or [0]) * 30))
            cr.arc(c, c, r, 0, 2 * math.pi)
            cr.stroke()
        elif self.state == "busy":
            cr.set_source_rgba(1, 1, 1, 0.12)
            cr.set_line_width(3)
            cr.arc(c, c, r, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgb(*BOB_ACCENT)
            cr.arc(c, c, r, t * 5, t * 5 + 1.6)  # spinner
            cr.stroke()
        elif self.state in ("speaking", "error"):
            cr.set_source_rgba(*(BOB_ACCENT if self.state == "speaking" else AMBER), 0.95)
            cr.set_line_width(2.5)
            cr.arc(c, c, r, 0, 2 * math.pi)
            cr.stroke()
        else:
            cr.set_source_rgba(1, 1, 1, 0.18)
            cr.set_line_width(1.5)
            cr.arc(c, c, r, 0, 2 * math.pi)
            cr.stroke()
        bounce = 1.5 * math.sin(t * 7) if self.state == "busy" else 0
        mouth = min(1.0, self.speak_level() * 7) if self.state == "speaking" else None
        f = self.FACE
        bob_face(cr, c - f / 2, c - f / 2 + 1 + bounce, f, mouth, self.blink)


# ---- Bob's card (above the Bob button) -------------------------------------------------
class Card(Gtk.Window):
    """Your question while you speak, then Bob's answer. Click copies the answer, right-click closes."""
    WIDTH, PAD, ICON, LINES = 520, 14, 26, 10

    def __init__(self, on_copy, on_close):
        super().__init__(type=Gtk.WindowType.POPUP)
        _popup(self)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._click)
        self.on_copy, self.on_close = on_copy, on_close
        self.text, self.footer, self.kind = "", "", "answer"
        self.timer = self.thinking = None

    def _text_layout(self, cr):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(
            "Sans Italic 11" if self.kind == "question" else "Sans 11"))
        layout.set_width((self.WIDTH - 3 * self.PAD - self.ICON) * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_height(-self.LINES)  # at most LINES lines, then an ellipsis; copy gets the full text
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_text(self.text, -1)
        return layout

    def show(self, text, footer, kind):
        """kind: question (your words, while listening and thinking) | answer | error"""
        self._stop_timers()
        self.text, self.footer, self.kind = text, footer, kind
        self._layout()

    def set_text(self, text):
        self.text = text
        self._layout()

    def set_footer(self, footer):
        self.footer = footer
        self.queue_draw()

    def thinking_footer(self, label):
        """Footer with a seconds counter, e.g. 'Bob is thinking… 3s'."""
        self._stop_timers()
        start = time.monotonic()

        def tick():
            self.set_footer(f"{label} {int(time.monotonic() - start)}s")
            return True
        tick()
        self.thinking = GLib.timeout_add(1000, tick)

    def close_later(self, seconds):
        self._stop_timers()
        self.timer = GLib.timeout_add_seconds(max(1, int(seconds)), self.close)

    def close(self):
        self._stop_timers()
        self.hide()
        return False

    def _stop_timers(self):
        for attr in ("timer", "thinking"):
            if getattr(self, attr):
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, None)

    def _layout(self):
        _, ext = self._text_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))).get_pixel_extents()
        w = max(300, min(self.WIDTH, ext.width + 3 * self.PAD + self.ICON + 4))
        h = max(ext.height, self.ICON) + 2 * self.PAD + 22
        geo = _monitor()
        self.resize(w, h)
        self.move(geo.x + geo.width - MARGIN - w, geo.y + geo.height - MARGIN - BobButton.SIZE - 10 - h)
        self.show_all()
        self.queue_draw()

    def _click(self, _w, event):
        if event.button == 1 and self.kind == "answer":
            self.on_copy(self.text)
            self.set_footer("Copied to clipboard")
        elif event.button != 1:
            self.on_close()

    def _draw(self, _w, cr):
        w, h = self.get_size()
        cr.set_operator(0)
        cr.paint()
        cr.set_operator(2)
        r = 14
        cr.new_sub_path()
        cr.arc(w - r - 1, r + 1, r, -math.pi / 2, 0)
        cr.arc(w - r - 1, h - r - 1, r, 0, math.pi / 2)
        cr.arc(r + 1, h - r - 1, r, math.pi / 2, math.pi)
        cr.arc(r + 1, r + 1, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(*BG, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(*(AMBER if self.kind == "error" else BOB_ACCENT), 0.9)
        cr.set_line_width(1.5)
        cr.stroke()
        bob_face(cr, self.PAD, self.PAD - 2, self.ICON)
        x = 2 * self.PAD + self.ICON
        cr.set_source_rgb(*{"question": (0.72, 0.74, 0.85), "error": (1, 0.8, 0.4)}.get(self.kind, (0.93, 0.93, 0.97)))
        cr.move_to(x, self.PAD)
        PangoCairo.show_layout(cr, self._text_layout(cr))
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(10)
        cr.set_source_rgb(0.55, 0.57, 0.68)
        cr.move_to(x, h - self.PAD + 2)
        cr.show_text(self.footer)


# ---- Tray ------------------------------------------------------------------------------
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
        item("Ask Bob  (Super+Alt+B)", lambda: d.toggle("bob"))
        item("Cancel", d.cancel)
        menu.append(Gtk.SeparatorMenuItem())
        self.flow = item("Flow mode (AI cleanup)", lambda on: d.set_mode("flow" if on else "raw"),
                         d.mode == "flow")
        item("Show pill when idle", d.set_show_idle, True)
        item("Show Bob button", d.set_show_bob, d.cfg["show_bob_button"])
        item("Speak Bob's answers", d.set_bob_speak, d.cfg["bob_speak"])
        item("New Bob conversation", d.bob_reset)
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
