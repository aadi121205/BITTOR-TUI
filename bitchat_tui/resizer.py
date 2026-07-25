"""A draggable divider between two panels, for click-drag panel resizing."""

from __future__ import annotations

from typing import Callable, Literal

from textual import events
from textual.widget import Widget


class ResizeHandle(Widget):
    DEFAULT_CSS = """
    ResizeHandle {
        background: $accent-darken-1;
    }
    ResizeHandle.-vertical {
        width: 1;
        height: 1fr;
    }
    ResizeHandle.-horizontal {
        height: 1;
        width: 1fr;
    }
    ResizeHandle:hover {
        background: $accent;
    }
    ResizeHandle.-dragging {
        background: $warning;
    }
    """

    def __init__(self, orientation: Literal["vertical", "horizontal"], on_drag: Callable[[int], None], **kwargs):
        """orientation: "vertical" = a left/right divider you drag horizontally to
        resize widths; "horizontal" = a top/bottom divider you drag vertically to
        resize heights. on_drag receives the signed delta (in cells) since the last
        callback -- positive means the mouse moved right/down.
        """
        super().__init__(**kwargs)
        self.orientation = orientation
        self.add_class("-vertical" if orientation == "vertical" else "-horizontal")
        self._on_drag = on_drag
        self._dragging = False
        self._last_pos = 0

    def render(self) -> str:
        # Widget.render() defaults to showing the widget's own id/classes as text when
        # there's no explicit content -- this is just a plain colored bar, not a label.
        return ""

    def _axis_pos(self, event: events.MouseEvent) -> int:
        return int(event.screen_x if self.orientation == "vertical" else event.screen_y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragging = True
        self.add_class("-dragging")
        self._last_pos = self._axis_pos(event)
        self.capture_mouse()
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self.release_mouse()
        event.stop()

    def on_mouse_release(self, _event: events.MouseRelease) -> None:
        self._dragging = False
        self.remove_class("-dragging")

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        pos = self._axis_pos(event)
        delta = pos - self._last_pos
        self._last_pos = pos
        if delta:
            self._on_drag(delta)
        event.stop()
