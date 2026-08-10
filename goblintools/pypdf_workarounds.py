"""
Runtime fixes for pypdf text extraction.

Goals:
- Resolve font metrics stored as IndirectObject (space/character widths).
- Relax array-based stream output cap when the attribute exists.
- Stay compatible across pypdf generations:

  * **Legacy** (e.g. 6.12.x): ``CUSTOM_RTL_MIN`` is ``int``, Font has ``text_width``.
    Apply the historical ``get_display_str`` / ``get_text_operands`` / ``_handle_*``
    patches (needed for some editais on that API).

  * **Modern** (e.g. 6.15+): RTL helpers use ``str`` and Font has ``get_text_width``.
    Do **not** replace ``get_display_str`` with the legacy copy — that causes
    ``TypeError: 'in <string>' requires string as left operand, not int`` on every
    page (seen in production with goblintools 0.9.0 + pypdf 6.15).

Apply once via :func:`apply_pypdf_extraction_workarounds`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Union

_applied = False


def _as_float(value: Any, fallback: float = 500.0) -> float:
    try:
        from pypdf.generic import IndirectObject
    except ImportError:  # pragma: no cover
        return float(value)
    if isinstance(value, IndirectObject):
        value = value.get_object()
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _uses_legacy_rtl_api() -> bool:
    """True when pypdf still exposes int-based CUSTOM_RTL_* (pre-6.15 style)."""
    import pypdf._text_extraction as te

    return isinstance(getattr(te, "CUSTOM_RTL_MIN", None), int)


def _cmap_to_str(font: Any, ch: Any) -> str:
    """Resolve a character through the font cmap to a str (never int/bytes)."""
    mapped = font.character_map.get(ch, ch) if getattr(font, "character_map", None) else ch
    if isinstance(mapped, int):
        try:
            if 0 <= mapped <= 0x10FFFF:
                return chr(mapped)
        except (ValueError, OverflowError):
            return ""
        return ""
    if isinstance(mapped, bytes):
        try:
            return mapped.decode("latin-1", "replace")
        except Exception:
            return ""
    if mapped is None:
        return ""
    if not isinstance(mapped, str):
        return str(mapped)
    return mapped


def apply_pypdf_extraction_workarounds(*, force: bool = False) -> None:
    """Idempotent monkey-patches for pypdf; safe to call multiple times."""
    global _applied
    if _applied and not force:
        return

    import pypdf.filters as pypdf_filters

    # Not every pypdf release exposes this on pypdf.filters (AttributeError-safe).
    _stream_cap = getattr(
        pypdf_filters, "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH", None
    )
    if _stream_cap is not None:
        pypdf_filters.MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH = max(
            int(_stream_cap), 120_000_000
        )

    if _uses_legacy_rtl_api():
        _apply_legacy_text_extraction_patches()
    else:
        _apply_modern_font_width_patches()

    _applied = True


def _apply_modern_font_width_patches() -> None:
    """Minimal patches for pypdf 6.15+ (keep stock get_display_str)."""
    from pypdf._font import Font

    if hasattr(Font, "get_text_width"):
        _orig_get_text_width = Font.get_text_width

        def get_text_width(self: Any, text: str = "") -> float:
            if not isinstance(text, str):
                text = str(text) if text is not None else ""
            # Ensure IndirectObject space_width does not break arithmetic upstream.
            try:
                sw = self.space_width
                if type(sw).__name__ == "IndirectObject" or not isinstance(
                    sw, (int, float)
                ):
                    object.__setattr__(self, "space_width", _as_float(sw, 250.0))
            except Exception:
                pass
            return _orig_get_text_width(self, text)

        Font.get_text_width = get_text_width  # type: ignore[method-assign]


def _apply_legacy_text_extraction_patches() -> None:
    """Historical patches for pypdf ≤ ~6.12 (int CUSTOM_RTL_* + Font.text_width)."""
    import pypdf._font as pypdf_font
    import pypdf._text_extraction._text_extractor as te
    from pypdf._font import Font, FontDescriptor
    from pypdf.generic import DictionaryObject, TextStringObject, encode_pdfdocencoding
    import pypdf._text_extraction as pypdf_te

    def text_width(self: Any, text: str = "") -> float:
        if not isinstance(text, str):
            text = str(text) if text is not None else ""
        return sum(
            _as_float(self.character_widths.get(ch, self.character_widths["default"]))
            for ch in text
        )

    if hasattr(Font, "text_width"):
        pypdf_font.Font.text_width = text_width  # type: ignore[method-assign]

    def get_text_operands(
        operands: list[Union[str, TextStringObject]],
        cm_matrix: list[float],
        tm_matrix: list[float],
        font: Font,
        orientations: tuple[int, ...],
    ) -> tuple[str, bool]:
        from pypdf._text_extraction import mult, orient

        t: str = ""
        is_str_operands = False
        m = mult(tm_matrix, cm_matrix)
        orientation = orient(m)
        if orientation in orientations and len(operands) > 0:
            if isinstance(operands[0], str):
                t = operands[0]
                is_str_operands = True
            else:
                t = ""
                tt: bytes = (
                    encode_pdfdocencoding(operands[0])
                    if isinstance(operands[0], str)
                    else operands[0]
                )
                enc = font.encoding
                if isinstance(enc, str):
                    try:
                        t = tt.decode(enc, "surrogatepass")
                    except Exception:
                        t = tt.decode(
                            "utf-16-be" if enc == "charmap" else "charmap",
                            "surrogatepass",
                        )
                elif isinstance(enc, dict):
                    t = "".join(
                        enc[x] if x in enc else bytes((x,)).decode() for x in tt
                    )
                else:
                    try:
                        t = tt.decode("latin-1", "surrogatepass")
                    except Exception:
                        t = tt.decode("charmap", "replace")
        return (t, is_str_operands)

    pypdf_te.get_text_operands = get_text_operands  # type: ignore[assignment]

    def get_display_str(
        text: str,
        cm_matrix: list[float],
        tm_matrix: list[float],
        font_resource: Optional[DictionaryObject],
        font: Font,
        text_operands: str,
        font_size: float,
        rtl_dir: bool,
        visitor_text: Optional[Callable[[Any, Any, Any, Any, Any], None]],
    ) -> tuple[str, bool, float]:
        from pypdf._text_extraction import (
            CUSTOM_RTL_MAX,
            CUSTOM_RTL_MIN,
            CUSTOM_RTL_SPECIAL_CHARS,
        )

        if not isinstance(text_operands, str):
            if isinstance(text_operands, (bytes, bytearray)):
                text_operands = bytes(text_operands).decode("latin-1", "replace")
            else:
                text_operands = str(text_operands)

        widths: float = 0.0
        rtl_specials = CUSTOM_RTL_SPECIAL_CHARS
        for x in [_cmap_to_str(font, ch) for ch in text_operands]:
            if not x:
                continue
            if len(x) == 1:
                xx = ord(x)
            else:
                xx = 1
            if isinstance(rtl_specials, str):
                rtl_hit = (0 <= xx <= 0x10FFFF) and (chr(xx) in rtl_specials)
            else:
                try:
                    rtl_hit = xx in rtl_specials
                except TypeError:
                    rtl_hit = False
            if (
                (xx <= 0x2F)
                or 0x3A <= xx <= 0x40
                or 0x2000 <= xx <= 0x206F
                or 0x20A0 <= xx <= 0x21FF
                or rtl_hit
            ):
                text = x + text if rtl_dir else text + x
            elif (
                0x0590 <= xx <= 0x08FF
                or 0xFB1D <= xx <= 0xFDFF
                or 0xFE70 <= xx <= 0xFEFF
                or CUSTOM_RTL_MIN <= xx <= CUSTOM_RTL_MAX
            ):
                if not rtl_dir:
                    rtl_dir = True
                    if visitor_text is not None:
                        visitor_text(
                            text, cm_matrix, tm_matrix, font_resource, font_size
                        )
                    text = ""
                text = x + text
            else:
                if rtl_dir:
                    rtl_dir = False
                    if visitor_text is not None:
                        visitor_text(
                            text, cm_matrix, tm_matrix, font_resource, font_size
                        )
                    text = ""
                text = text + x
            sw = _as_float(font.space_width, 250.0)
            widths += sw if x == " " else font.text_width(x)
        return text, rtl_dir, widths

    pypdf_te.get_display_str = get_display_str  # type: ignore[assignment]

    def _handle_tf(self: Any, operands: list[Any]) -> None:
        if self.text != "":
            self.output += self.text
            if self.visitor_text is not None:
                self.visitor_text(
                    self.text,
                    self.memo_cm,
                    self.memo_tm,
                    self.font_resource,
                    self.font_size,
                )
        self.text = ""
        self.memo_cm = self.cm_matrix.copy()
        self.memo_tm = self.tm_matrix.copy()
        try:
            self.font_resource = self.font_resources[operands[0]]
            self.font = self.fonts[operands[0]]
        except KeyError:
            self.font_resource = None
            font_descriptor = FontDescriptor()
            self.font = Font(
                "Unknown",
                space_width=250,
                encoding=dict.fromkeys(range(256), "\ufffd"),
                font_descriptor=font_descriptor,
                character_map={},
            )

        self._space_width = _as_float(self.font.space_width, 250.0) / 2
        try:
            self.font_size = float(operands[1])
        except Exception:
            pass

    te.TextExtraction._handle_tf = _handle_tf  # type: ignore[method-assign]

    def _handle_tj(
        self: Any,
        text: str,
        operands: list[Union[str, TextStringObject]],
        cm_matrix: list[float],
        tm_matrix: list[float],
        font_resource: Optional[DictionaryObject],
        font: Font,
        orientations: tuple[int, ...],
        font_size: float,
        rtl_dir: bool,
        visitor_text: Optional[Callable[[Any, Any, Any, Any, Any], None]],
        actual_str_size: dict[str, float],
    ) -> tuple[str, bool, dict[str, float]]:
        from pypdf._text_extraction import get_display_str, get_text_operands

        text_operands, is_str_operands = get_text_operands(
            operands, cm_matrix, tm_matrix, font, orientations
        )
        if is_str_operands:
            text += text_operands
            sw = _as_float(font.space_width, 250.0)
            font_widths = sum(
                sw if x == " " else font.text_width(x) for x in text_operands
            )
        else:
            text, rtl_dir, font_widths = get_display_str(
                text,
                cm_matrix,
                tm_matrix,
                font_resource,
                font,
                text_operands,
                font_size,
                rtl_dir,
                visitor_text,
            )
        actual_str_size["str_widths"] += font_widths * font_size
        actual_str_size["str_height"] = font_size
        return text, rtl_dir, actual_str_size

    te.TextExtraction._handle_tj = _handle_tj  # type: ignore[method-assign]
