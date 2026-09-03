"""Safe view-model helpers for the Bilibili HTML notification card."""

from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_TEMPLATE_PATH = Path(__file__).with_name("templates") / "bilibili_card.html"
_BILIBILI_IMAGE_HOSTS = (
    "bilibili.com",
    "hdslb.com",
    "biliimg.com",
    "bstarstatic.com",
)


def _escaped(value: Any) -> str:
    return escape(str(value or "").strip(), quote=True)


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}……"


def _bilibili_image_url(value: Any) -> str:
    """Only pass Bilibili-owned image hosts into renderer attributes."""

    url = str(value or "").strip()
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    belongs_to_bilibili = any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in _BILIBILI_IMAGE_HOSTS
    )
    if parsed.scheme not in {"http", "https"} or not belongs_to_bilibili:
        return ""
    return _escaped(url)


def _gallery_class(image_count: int) -> str:
    if image_count <= 1:
        return "one"
    if image_count == 2:
        return "two"
    if image_count == 3:
        return "three"
    if image_count == 4:
        return "four"
    return "many"


@lru_cache(maxsize=1)
def load_bili_card_template() -> str:
    """Load the bundled template once for the lifetime of the plugin."""

    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def trim_transparent_padding(value: Any) -> str:
    """Crop transparent screenshot margins while preserving visible shadows.

    AstrBot's full-page renderer never produces an image shorter than its
    viewport, so compact cards can have a large transparent area underneath.
    Pillow is already provided by AstrBot; if it or a usable local PNG is not
    available, returning the original value keeps rendering non-fatal.
    """

    rendered = str(value or "").strip()
    if not rendered or rendered.startswith(("http://", "https://")):
        return rendered

    path = Path(rendered)
    if not path.is_file():
        return rendered

    try:
        from PIL import Image

        with Image.open(path) as source:
            if source.format != "PNG":
                return rendered
            rgba = source.convert("RGBA")
            bounds = rgba.getchannel("A").getbbox()
            if bounds is None or bounds == (0, 0, *rgba.size):
                rgba.close()
                return rendered
            cropped = rgba.crop(bounds)
            rgba.close()

        try:
            cropped.save(path, format="PNG")
        finally:
            cropped.close()
    except (ImportError, OSError, ValueError):
        return rendered

    return rendered


def build_bili_card_context(update: Any, published: str) -> dict[str, Any]:
    """Build an escaped, renderer-ready context from a normalized update."""

    is_video = str(getattr(update, "kind", "")) == "video"
    raw_images = getattr(update, "images", ()) or ()
    images = [
        url for value in raw_images if (url := _bilibili_image_url(value))
    ]
    images = images[:1] if is_video else images[:9]

    author = str(getattr(update, "author", "") or "B站用户").strip()
    initial = next((char for char in author if char.isalnum()), "B")
    title = str(getattr(update, "title", "") or "").strip()
    text = str(getattr(update, "text", "") or "").strip()
    if is_video:
        heading = title or "新视频投稿"
        content = _truncate(text, 260)
        kind_label = "视频投稿"
        eyebrow = "NEW VIDEO"
    else:
        heading = title or "账号动态"
        content = _truncate(text or heading, 680)
        kind_label = "动态更新"
        eyebrow = "NEW POST"

    target_url = str(getattr(update, "url", "") or "").strip()
    display_url = target_url.removeprefix("https://").removeprefix("http://")
    if len(display_url) > 58:
        display_url = f"{display_url[:57]}…"

    return {
        "is_video": is_video,
        "author": _escaped(author),
        "author_initial": _escaped(initial.upper()),
        "avatar": _bilibili_image_url(getattr(update, "avatar", "")),
        "uid": _escaped(getattr(update, "uid", "")),
        "heading": _escaped(heading),
        "content": _escaped(content),
        "kind_label": _escaped(kind_label),
        "eyebrow": eyebrow,
        "published": _escaped(published),
        "content_key": _escaped(getattr(update, "key", "")),
        "display_url": _escaped(display_url),
        "images": images,
        "cover": images[0] if images else "",
        "image_count": len(images),
        "gallery_class": _gallery_class(len(images)),
    }
