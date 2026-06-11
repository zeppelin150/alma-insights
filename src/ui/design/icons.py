"""QtSvg icon registry — drawn vector glyphs, no emoji / symbol fonts.

Glyphs are 24x24 stroke-based paths (Feather-style) rendered to a 2x
pixmap with the requested color baked in (QSS cannot recolor QIcons).
QtSvg ships with PySide6-Essentials, so this adds no dependency.
"""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from src.ui.design import tokens

_GLYPHS = {
    "home": '<path d="M3 9.5 12 3l9 6.5"/><path d="M5 8.5V21h14V8.5"/>'
            '<path d="M9 21v-6h6v6"/>',
    "chat": '<path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.4 8.5 8.5 0 0 1-3.9-.9'
            'L3 20l1-5.1a8.4 8.4 0 0 1-.4-3.4 8.5 8.5 0 0 1 8.4-8.5h.5'
            'a8.5 8.5 0 0 1 8.5 8.5z"/>',
    "antenna": '<circle cx="12" cy="12" r="2"/>'
               '<path d="M16.2 7.8a6 6 0 0 1 0 8.4"/>'
               '<path d="M7.8 16.2a6 6 0 0 1 0-8.4"/>'
               '<path d="M19 5a10 10 0 0 1 0 14"/>'
               '<path d="M5 19A10 10 0 0 1 5 5"/>',
    "book": '<path d="M2 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H2z"/>'
            '<path d="M22 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z"/>',
    "bars": '<line x1="6" y1="20" x2="6" y2="10"/>'
            '<line x1="12" y1="20" x2="12" y2="4"/>'
            '<line x1="18" y1="20" x2="18" y2="14"/>',
    "trend": '<polyline points="3 17 9 11 13 15 21 7"/>'
             '<polyline points="15 7 21 7 21 13"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 '
             '1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'
             '<line x1="12" y1="9" x2="12" y2="13"/>'
             '<line x1="12" y1="17" x2="12.01" y2="17"/>',
    "doc": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 '
           '2-2V8z"/><polyline points="14 2 14 8 20 8"/>'
           '<line x1="8" y1="13" x2="16" y2="13"/>'
           '<line x1="8" y1="17" x2="13" y2="17"/>',
    "repeat": '<polyline points="17 1 21 5 17 9"/>'
              '<path d="M3 11V9a4 4 0 0 1 4-4h14"/>'
              '<polyline points="7 23 3 19 7 15"/>'
              '<path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/>'
                '<path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>'
                '<path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/>'
               '<line x1="4" y1="10" x2="4" y2="3"/>'
               '<line x1="12" y1="21" x2="12" y2="12"/>'
               '<line x1="12" y1="8" x2="12" y2="3"/>'
               '<line x1="20" y1="21" x2="20" y2="16"/>'
               '<line x1="20" y1="12" x2="20" y2="3"/>'
               '<line x1="1" y1="14" x2="7" y2="14"/>'
               '<line x1="9" y1="8" x2="15" y2="8"/>'
               '<line x1="17" y1="16" x2="23" y2="16"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/>'
                '<line x1="16" y1="2" x2="16" y2="6"/>'
                '<line x1="8" y1="2" x2="8" y2="6"/>'
                '<line x1="3" y1="10" x2="21" y2="10"/>',
    "tasks": '<polyline points="9 11 12 14 22 4"/>'
             '<path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 '
             '2-2h11"/>',
    "pen": '<path d="M12 20h9"/>'
           '<path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "pie": '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/>'
           '<path d="M22 12A10 10 0 0 0 12 2v10z"/>',
    "refresh": '<polyline points="23 4 23 10 17 10"/>'
               '<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<polyline points="7 10 12 15 17 10"/>'
                '<line x1="12" y1="15" x2="12" y2="3"/>',
    "type": '<polyline points="4 7 4 4 20 4 20 7"/>'
            '<line x1="9" y1="20" x2="15" y2="20"/>'
            '<line x1="12" y1="4" x2="12" y2="20"/>',
    "chevron-left": '<polyline points="15 18 9 12 15 6"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

_CACHE: dict = {}


def glyph_names():
    return tuple(_GLYPHS)


def icon(name: str, size: int = 18, color: str | None = None) -> QIcon:
    """Render a registered glyph to a QIcon (2x pixmap for HiDPI).

    Unknown names return a null QIcon so callers degrade gracefully.
    """
    body = _GLYPHS.get(name)
    if body is None:
        return QIcon()
    color = color or tokens.ALMA_TEXT_DARK
    key = (name, size, color)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    svg = _TEMPLATE.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(2.0)
    result = QIcon(pm)
    _CACHE[key] = result
    return result
