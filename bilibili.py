"""Bilibili public-space API helpers.

Only public visitor endpoints are used here.  No account cookies such as
SESSDATA or bili_jct are accepted or stored by this module.
"""

from dataclasses import dataclass
import json
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


BILI_DYNAMIC_SPACE_URL = (
    "https://api.bilibili.com/x/polymer/web-dynamic/desktop/v1/feed/space"
)
BILI_ACCOUNT_CARD_URL = "https://api.bilibili.com/x/web-interface/card"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_FEATURES = (
    "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,"
    "forwardListHidden,decorationCard,commentsNewVersion,"
    "onlyfansAssetsV2,ugcDelete,onlyfansQaCard"
)
_UID_RE = re.compile(
    r"(?:(?:https?://)?space\.bilibili\.com/)?(\d+)(?:[/?#].*)?",
    re.IGNORECASE,
)


class BilibiliApiError(RuntimeError):
    """Raised when a public Bilibili endpoint cannot return usable data."""


@dataclass(frozen=True)
class BiliUpdate:
    """Normalized dynamic/video item used by the subscription worker."""

    key: str
    uid: str
    author: str
    kind: str
    title: str
    text: str
    published_at: int
    url: str
    images: tuple[str, ...]
    raw_type: str
    avatar: str = ""
    is_pinned: bool = False


def normalize_bili_uid(value: Any) -> str:
    """Normalize a numeric UID or a Bilibili space URL to a UID string."""

    raw = str(value or "").strip().rstrip("/")
    match = _UID_RE.fullmatch(raw)
    if not match:
        raise ValueError("UID 必须是正整数，或形如 https://space.bilibili.com/123 的空间链接")

    uid = str(int(match.group(1)))
    if uid == "0":
        raise ValueError("UID 必须大于 0")
    return uid


def should_at_all_subscription_target(value: Any) -> bool:
    """Return whether a subscription target is a group-message UMO.

    The first UMO field is a user-configurable platform instance ID, so it
    must not be compared with an adapter name such as ``aiocqhttp``.
    """

    parts = str(value or "").split(":", 2)
    return (
        len(parts) == 3
        and bool(parts[0].strip())
        and parts[1].casefold() == "groupmessage"
        and bool(parts[2].strip())
    )


def normalize_image_url(value: Any) -> str:
    """Convert protocol-relative and plain-http image URLs to HTTPS."""

    url = str(value or "").strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        return f"https://{url[len('http://') :]}"
    return url


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _find_module(item: dict, name: str) -> dict:
    """Support both the dictionary and desktop-list module layouts."""

    modules = item.get("modules")
    if isinstance(modules, dict):
        return _as_dict(modules.get(name))
    if isinstance(modules, list):
        for module in modules:
            if isinstance(module, dict) and name in module:
                return _as_dict(module.get(name))
    return {}


def _archive_from_item(item: dict) -> dict:
    dynamic = _find_module(item, "module_dynamic")
    archive = dynamic.get("dyn_archive") or dynamic.get("archive")
    if isinstance(archive, dict):
        return archive

    major = _as_dict(dynamic.get("major"))
    archive = major.get("archive")
    return _as_dict(archive)


def _dynamic_text(item: dict) -> str:
    description = _find_module(item, "module_desc")
    text = description.get("text")
    if text:
        return str(text).strip()

    dynamic = _find_module(item, "module_dynamic")
    desc = _as_dict(dynamic.get("desc"))
    text = desc.get("text")
    if text:
        return str(text).strip()

    opus = _as_dict(dynamic.get("dyn_opus"))
    summary = _as_dict(opus.get("summary"))
    text = summary.get("text")
    if text:
        return str(text).strip()

    major = _as_dict(dynamic.get("major"))
    opus = _as_dict(major.get("opus"))
    summary = _as_dict(opus.get("summary"))
    return str(summary.get("text") or "").strip()


def _card_title(item: dict) -> str:
    dynamic = _find_module(item, "module_dynamic")
    major = _as_dict(dynamic.get("major"))
    candidates = [
        dynamic.get("dyn_opus"),
        dynamic.get("dyn_article"),
        dynamic.get("dyn_common"),
        dynamic.get("dyn_ugc_season"),
        major.get("opus"),
        major.get("article"),
        major.get("common"),
        major.get("ugc_season"),
    ]
    for candidate in candidates:
        card = _as_dict(candidate)
        title = card.get("title") or card.get("name")
        if title:
            return str(title).strip()
    return ""


def _collect_images(item: dict, archive: dict) -> tuple[str, ...]:
    images: list[str] = []

    def add(value: Any) -> None:
        url = normalize_image_url(value)
        if url and url not in images:
            images.append(url)

    add(archive.get("cover"))
    dynamic = _find_module(item, "module_dynamic")
    major = _as_dict(dynamic.get("major"))

    draw_cards = [dynamic.get("dyn_draw"), major.get("draw")]
    for draw_value in draw_cards:
        draw = _as_dict(draw_value)
        for picture in draw.get("items") or []:
            if isinstance(picture, dict):
                add(picture.get("src") or picture.get("url"))

    opus_cards = [dynamic.get("dyn_opus"), major.get("opus")]
    for opus_value in opus_cards:
        opus = _as_dict(opus_value)
        for picture in opus.get("pics") or []:
            if isinstance(picture, dict):
                add(picture.get("url") or picture.get("src"))

    article_cards = [dynamic.get("dyn_article"), major.get("article")]
    for article_value in article_cards:
        article = _as_dict(article_value)
        for cover in article.get("covers") or []:
            add(cover)
        add(article.get("cover"))

    common_cards = [dynamic.get("dyn_common"), major.get("common")]
    for common_value in common_cards:
        add(_as_dict(common_value).get("cover"))

    return tuple(images[:9])


_DYNAMIC_TYPE_LABELS = {
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_WORD": "文字动态",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏动态",
    "DYNAMIC_TYPE_MUSIC": "音频动态",
    "DYNAMIC_TYPE_PGC": "番剧动态",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播动态",
    "DYNAMIC_TYPE_UGC_SEASON": "合集动态",
    "DYNAMIC_TYPE_COMMON_SQUARE": "卡片动态",
    "DYNAMIC_TYPE_COURSES_SEASON": "课程动态",
}


def parse_space_item(item: Any, fallback_uid: str) -> BiliUpdate | None:
    """Normalize one space-feed item; unsupported/invalid items are skipped."""

    if not isinstance(item, dict) or item.get("visible") is False:
        return None

    dynamic_id = str(item.get("id_str") or item.get("id") or "").strip()
    raw_type = str(item.get("type") or "DYNAMIC_TYPE_UNKNOWN")
    if not dynamic_id:
        return None

    author_module = _find_module(item, "module_author")
    author_user = _as_dict(author_module.get("user"))
    uid = str(
        author_user.get("mid")
        or author_module.get("mid")
        or fallback_uid
    )
    author = str(
        author_user.get("name")
        or author_module.get("name")
        or f"UID {fallback_uid}"
    ).strip()
    avatar = normalize_image_url(
        author_user.get("face") or author_module.get("face")
    )
    tag_module = _find_module(item, "module_tag")
    is_pinned = str(tag_module.get("text") or "").strip() == "置顶"
    published_at = _as_int(author_module.get("pub_ts"))

    archive = _archive_from_item(item)
    is_video = raw_type == "DYNAMIC_TYPE_AV"
    kind = "video" if is_video else "dynamic"
    text = _dynamic_text(item)
    title = ""
    key = dynamic_id
    url = f"https://t.bilibili.com/{dynamic_id}"

    if is_video:
        bvid = str(archive.get("bvid") or "").strip()
        key = bvid or dynamic_id
        title = str(archive.get("title") or "未命名视频").strip()
        text = text or str(archive.get("desc") or "").strip()
        if bvid:
            url = f"https://www.bilibili.com/video/{bvid}"
    else:
        title = _card_title(item) or _DYNAMIC_TYPE_LABELS.get(raw_type, "账号动态")

    return BiliUpdate(
        key=key,
        uid=uid,
        author=author,
        kind=kind,
        title=title,
        text=text,
        published_at=published_at,
        url=url,
        images=_collect_images(item, archive),
        raw_type=raw_type,
        avatar=avatar,
        is_pinned=is_pinned,
    )


def select_latest_update(
    updates: list[BiliUpdate], kinds: set[str] | frozenset[str]
) -> BiliUpdate | None:
    """Select the newest matching item by publish time, not feed position.

    Space feeds can put an older pinned dynamic before newer items.  Keeping
    the first item on equal timestamps preserves the API order as a fallback.
    """

    latest = None
    for update in updates:
        if update.kind not in kinds:
            continue
        if latest is None or update.published_at > latest.published_at:
            latest = update
    return latest


class BilibiliPublicClient:
    """Small client for public visitor APIs, deliberately without Cookie headers."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def _headers(self, uid: str, page: str) -> dict[str, str]:
        return {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"https://space.bilibili.com/{uid}/{page}",
            "Origin": "https://space.bilibili.com",
        }

    def _get_json(self, url: str, headers: dict[str, str]) -> dict:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                detail = ""
            suffix = f"：{detail}" if detail else ""
            raise BilibiliApiError(f"B站接口 HTTP {exc.code}{suffix}") from exc
        except urllib.error.URLError as exc:
            raise BilibiliApiError(f"连接 B站接口失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise BilibiliApiError("连接 B站接口超时") from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BilibiliApiError("B站接口返回了无法解析的数据") from exc
        if not isinstance(data, dict):
            raise BilibiliApiError("B站接口返回格式异常")
        return data

    def _fetch_space_page(
        self, normalized_uid: str, offset: str = ""
    ) -> tuple[list[BiliUpdate], str, bool]:
        params_data = {
            "host_mid": normalized_uid,
            "timezone_offset": -480,
            "platform": "web",
            "features": _FEATURES,
        }
        if offset:
            params_data["offset"] = offset
        params = urllib.parse.urlencode(params_data)
        url = f"{BILI_DYNAMIC_SPACE_URL}?{params}"
        response = self._get_json(url, self._headers(normalized_uid, "dynamic"))
        if response.get("code") != 0:
            raise BilibiliApiError(
                f"获取 UID {normalized_uid} 的公开动态失败："
                f"{response.get('message') or response.get('code')}"
            )

        response_data = _as_dict(response.get("data"))
        raw_items = response_data.get("items") or []
        updates = [
            update
            for update in (
                parse_space_item(item, normalized_uid) for item in raw_items
            )
            if update is not None
        ]
        updates.sort(key=lambda update: update.published_at, reverse=True)
        next_offset = str(response_data.get("offset") or "").strip()
        return updates, next_offset, bool(response_data.get("has_more"))

    def fetch_space_updates(self, uid: Any) -> list[BiliUpdate]:
        """Fetch the first public space-feed page and split dynamics/videos."""

        normalized_uid = normalize_bili_uid(uid)
        updates, _, _ = self._fetch_space_page(normalized_uid)
        if not updates:
            # The visitor endpoint occasionally returns a transient empty page
            # with code=0. One immediate retry avoids corrupting a new baseline.
            updates, _, _ = self._fetch_space_page(normalized_uid)
        return updates

    def fetch_latest_update(
        self,
        uid: Any,
        kinds: set[str] | frozenset[str],
        max_pages: int = 5,
    ) -> BiliUpdate | None:
        """Find the latest requested kind, paging past pins or dense feeds."""

        normalized_uid = normalize_bili_uid(uid)
        collected: list[BiliUpdate] = []
        offset = ""
        seen_offsets: set[str] = set()
        page_limit = max(1, min(int(max_pages), 10))

        for page_index in range(page_limit):
            page, next_offset, has_more = self._fetch_space_page(
                normalized_uid, offset
            )
            if page_index == 0 and not page:
                page, next_offset, has_more = self._fetch_space_page(
                    normalized_uid, offset
                )
            collected.extend(page)

            # Once a regular (non-pinned) matching item is found, later pages
            # are older. Include pins in the timestamp comparison, however.
            if any(
                update.kind in kinds and not update.is_pinned
                for update in page
            ):
                break
            if (
                not has_more
                or not next_offset
                or next_offset in seen_offsets
            ):
                break
            seen_offsets.add(next_offset)
            offset = next_offset

        return select_latest_update(collected, kinds)

    def fetch_account_name(self, uid: Any) -> str:
        """Resolve a public account name without login credentials."""

        normalized_uid = normalize_bili_uid(uid)
        params = urllib.parse.urlencode({"mid": normalized_uid})
        url = f"{BILI_ACCOUNT_CARD_URL}?{params}"
        response = self._get_json(url, self._headers(normalized_uid, ""))
        if response.get("code") != 0:
            raise BilibiliApiError(
                f"查询 UID {normalized_uid} 失败："
                f"{response.get('message') or response.get('code')}"
            )

        card = _as_dict(_as_dict(response.get("data")).get("card"))
        name = str(card.get("name") or "").strip()
        if not name:
            raise BilibiliApiError(f"没有找到 UID {normalized_uid} 对应的公开账号")
        return name
