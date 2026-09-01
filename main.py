from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, StarTools, register

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any

from .bilibili import (
    BiliUpdate,
    BilibiliApiError,
    BilibiliPublicClient,
    normalize_bili_uid,
    should_at_all_subscription_target,
)
from .bili_card import build_bili_card_context, load_bili_card_template


BILI_DEFAULT_UID = "10082742"
BILI_POLL_INTERVAL_SECONDS = 300
BILI_MAX_SEEN_IDS = 200
BILI_BUILTIN_SUBSCRIPTIONS = {
    "aiocqhttp:GroupMessage:498908616": {
        BILI_DEFAULT_UID: frozenset({"dynamic", "video"})
    }
}

_TYPE_ALIASES = {
    "全部": frozenset({"dynamic", "video"}),
    "全": frozenset({"dynamic", "video"}),
    "更新": frozenset({"dynamic", "video"}),
    "all": frozenset({"dynamic", "video"}),
    "动态": frozenset({"dynamic"}),
    "dynamic": frozenset({"dynamic"}),
    "视频": frozenset({"video"}),
    "投稿": frozenset({"video"}),
    "video": frozenset({"video"}),
}
_TYPE_ORDER = ("dynamic", "video")
_TYPE_NAMES = {"dynamic": "动态", "video": "视频"}
_BILI_TIMEZONE = timezone(timedelta(hours=8))


@register("xx_bot", "XX", "B站账号动态与视频订阅推送", "2.3.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._data_dir = StarTools.get_data_dir("astrbot_plugin_xx_bot")
        self._bili_state_file = os.path.join(
            self._data_dir, "bili_subscriptions.json"
        )
        self._legacy_bili_state_file = os.path.join(
            self._data_dir, "bili_video_subscribe.json"
        )
        self._bili_state: dict[str, Any] = {}
        self._bili_poll_task = None
        self._bili_check_lock = asyncio.Lock()
        self._bili_client = BilibiliPublicClient()

    async def initialize(self):
        """Load subscriptions and start the public-feed polling worker."""

        os.makedirs(self._data_dir, exist_ok=True)
        self._bili_state = self._load_bili_state()
        self._save_bili_state()
        self._bili_poll_task = asyncio.create_task(self._bili_poll_loop())
        logger.info(
            "B站动态/视频订阅任务已启动（公开游客接口，不使用账号 Cookie）"
        )

    def _default_bili_state(self) -> dict[str, Any]:
        return {
            "version": 2,
            "subscriptions": {},
            "accounts": {},
        }

    def _read_json_file(self, path: str) -> dict | None:
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.error(f"加载B站订阅状态失败 ({path}): {exc}")
            return None

    def _normalize_stored_kinds(self, value: Any) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = list(value)
        else:
            return []

        result = []
        for item in values:
            kind = str(item).strip().lower()
            if kind in _TYPE_NAMES and kind not in result:
                result.append(kind)
        return [kind for kind in _TYPE_ORDER if kind in result]

    def _normalize_v2_state(self, raw: dict) -> dict[str, Any]:
        state = self._default_bili_state()
        subscriptions = raw.get("subscriptions")
        if isinstance(subscriptions, dict):
            for raw_umo, raw_targets in subscriptions.items():
                if not isinstance(raw_targets, dict):
                    continue
                targets = {}
                for raw_uid, raw_kinds in raw_targets.items():
                    try:
                        uid = normalize_bili_uid(raw_uid)
                    except ValueError:
                        continue
                    kinds = self._normalize_stored_kinds(raw_kinds)
                    if kinds:
                        targets[uid] = kinds
                if targets:
                    state["subscriptions"][str(raw_umo)] = targets

        accounts = raw.get("accounts")
        if isinstance(accounts, dict):
            for raw_uid, raw_account in accounts.items():
                try:
                    uid = normalize_bili_uid(raw_uid)
                except ValueError:
                    continue
                if not isinstance(raw_account, dict):
                    continue
                state["accounts"][uid] = {
                    "name": str(raw_account.get("name") or "").strip(),
                    "initialized": bool(raw_account.get("initialized")),
                    "seen_dynamic_ids": self._clean_seen_ids(
                        raw_account.get("seen_dynamic_ids")
                    ),
                    "seen_video_ids": self._clean_seen_ids(
                        raw_account.get("seen_video_ids")
                    ),
                    "last_checked_at": self._safe_int(
                        raw_account.get("last_checked_at")
                    ),
                }
        return state

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _migrate_legacy_state(self, raw: dict) -> dict[str, Any]:
        state = self._default_bili_state()
        subscribers = raw.get("subscribers")
        if isinstance(subscribers, list):
            for umo in subscribers:
                if not umo:
                    continue
                state["subscriptions"][str(umo)] = {
                    BILI_DEFAULT_UID: ["dynamic", "video"]
                }
        logger.info("已将旧版固定 UID 视频订阅迁移为动态+视频订阅")
        return state

    def _load_bili_state(self) -> dict[str, Any]:
        state = None
        if os.path.exists(self._bili_state_file):
            raw = self._read_json_file(self._bili_state_file)
            if raw is not None:
                state = self._normalize_v2_state(raw)
        elif os.path.exists(self._legacy_bili_state_file):
            raw = self._read_json_file(self._legacy_bili_state_file)
            if raw is not None:
                state = self._migrate_legacy_state(raw)

        if state is None:
            state = self._default_bili_state()
        self._merge_builtin_subscriptions(state)
        return state

    def _merge_builtin_subscriptions(self, state: dict[str, Any]) -> None:
        subscriptions = state.setdefault("subscriptions", {})
        for umo, targets in BILI_BUILTIN_SUBSCRIPTIONS.items():
            saved_targets = subscriptions.setdefault(umo, {})
            for uid, protected_kinds in targets.items():
                current = set(self._normalize_stored_kinds(saved_targets.get(uid)))
                current.update(protected_kinds)
                saved_targets[uid] = self._ordered_kinds(current)

    def _save_bili_state(self) -> None:
        temp_path = f"{self._bili_state_file}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(
                    self._bili_state,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(temp_path, self._bili_state_file)
        except Exception as exc:
            logger.error(f"保存B站订阅状态失败: {exc}")
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    def _clean_seen_ids(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            key = str(item or "").strip()
            if key and key not in result:
                result.append(key)
            if len(result) >= BILI_MAX_SEEN_IDS:
                break
        return result

    def _ordered_kinds(self, kinds: set[str] | frozenset[str]) -> list[str]:
        return [kind for kind in _TYPE_ORDER if kind in kinds]

    def _parse_kind_argument(self, value: Any) -> set[str]:
        normalized = str(value or "全部").strip().lower()
        kinds = _TYPE_ALIASES.get(normalized)
        if kinds is None:
            raise ValueError("订阅类型只支持：全部、动态、视频")
        return set(kinds)

    def _parse_command_target(
        self, uid_value: Any, kind_value: Any
    ) -> tuple[str, set[str]]:
        raw_uid = str(uid_value or "").strip()
        raw_kind = str(kind_value or "全部").strip()
        if raw_uid.lower() in _TYPE_ALIASES and raw_kind in ("", "全部"):
            raw_kind = raw_uid
            raw_uid = BILI_DEFAULT_UID
        uid = normalize_bili_uid(raw_uid or BILI_DEFAULT_UID)
        return uid, self._parse_kind_argument(raw_kind)

    def _account_state(self, uid: str) -> dict[str, Any]:
        return self._bili_state.setdefault("accounts", {}).setdefault(
            uid,
            {
                "name": "",
                "initialized": False,
                "seen_dynamic_ids": [],
                "seen_video_ids": [],
                "last_checked_at": 0,
            },
        )

    def _subscribed_uids(self) -> list[str]:
        uids = {
            uid
            for targets in self._bili_state.get("subscriptions", {}).values()
            if isinstance(targets, dict)
            for uid, kinds in targets.items()
            if self._normalize_stored_kinds(kinds)
        }
        return sorted(uids, key=int)

    def _recipients(self, uid: str, kind: str) -> list[str]:
        result = []
        for umo, targets in self._bili_state.get("subscriptions", {}).items():
            if not isinstance(targets, dict):
                continue
            if kind in self._normalize_stored_kinds(targets.get(uid)):
                result.append(umo)
        return result

    def _remember_ids(
        self, current: list[str], previous: list[str]
    ) -> list[str]:
        result = []
        for key in [*current, *previous]:
            normalized = str(key or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
            if len(result) >= BILI_MAX_SEEN_IDS:
                break
        return result

    def _format_time(self, timestamp: int) -> str:
        if not timestamp:
            return "未知"
        try:
            return datetime.fromtimestamp(timestamp, _BILI_TIMEZONE).strftime(
                "%Y-%m-%d %H:%M"
            )
        except (OSError, OverflowError, ValueError):
            return "未知"

    def _truncate(self, text: str, limit: int = 500) -> str:
        value = str(text or "").strip()
        return value if len(value) <= limit else f"{value[:limit]}……"

    def _format_bili_message(self, update: BiliUpdate) -> str:
        published = self._format_time(update.published_at)
        if update.kind == "video":
            return (
                "B站视频更新\n"
                f"UP：{update.author}\n"
                f"标题：{update.title}\n"
                f"发布时间：{published}\n"
                f"{update.url}"
            )

        content = self._truncate(update.text) or update.title
        return (
            "B站动态更新\n"
            f"UP：{update.author}\n"
            f"内容：{content}\n"
            f"发布时间：{published}\n"
            f"{update.url}"
        )

    async def _render_bili_card(self, update: BiliUpdate) -> str:
        try:
            rendered = await self.html_render(
                tmpl=load_bili_card_template(),
                data=build_bili_card_context(
                    update, self._format_time(update.published_at)
                ),
                return_url=False,
                options={
                    "full_page": True,
                    "type": "png",
                    "animations": "disabled",
                    "caret": "hide",
                    "scale": "device",
                    "omit_background": True,
                },
            )
            return str(rendered or "").strip()
        except Exception as exc:
            logger.warning(
                f"渲染B站{_TYPE_NAMES[update.kind]}蓝色卡片失败，将回退为普通消息: {exc}"
            )
            return ""

    def _bili_card_component(self, rendered: str):
        if rendered.startswith(("http://", "https://")):
            return Comp.Image.fromURL(rendered)
        return Comp.Image.fromFileSystem(rendered)

    def _bili_update_components(
        self, update: BiliUpdate, rendered: str
    ) -> list[Any]:
        if rendered:
            return [
                self._bili_card_component(rendered),
                Comp.Plain(f"\n{update.url}"),
            ]

        components: list[Any] = [
            Comp.Plain(self._format_bili_message(update))
        ]
        components.extend(
            Comp.Image.fromURL(image_url) for image_url in update.images
        )
        return components

    async def _send_bili_update(self, uid: str, update: BiliUpdate) -> None:
        recipients = self._recipients(uid, update.kind)
        if not recipients:
            return

        rendered = await self._render_bili_card(update)
        for umo in recipients:
            should_at_all = should_at_all_subscription_target(umo)
            try:
                components = self._bili_update_components(update, rendered)
                if should_at_all:
                    components.insert(0, Comp.AtAll())
                chain = MessageChain(components)
                await self.context.send_message(umo, chain)
            except Exception as exc:
                if should_at_all:
                    logger.warning(
                        f"发送B站{_TYPE_NAMES[update.kind]}更新到 {umo} 时"
                        f" @全体成员失败，将重试普通推送: {exc}"
                    )
                    try:
                        fallback_chain = MessageChain(
                            self._bili_update_components(update, rendered)
                        )
                        await self.context.send_message(umo, fallback_chain)
                        continue
                    except Exception as fallback_exc:
                        logger.error(
                            f"发送B站{_TYPE_NAMES[update.kind]}更新到 {umo} "
                            f"失败（@全体成员与普通推送均失败）: "
                            f"{fallback_exc}"
                        )
                        continue
                logger.error(
                    f"发送B站{_TYPE_NAMES[update.kind]}更新到 {umo} 失败: {exc}"
                )

    async def _refresh_bili_account(self, uid: str, notify: bool) -> None:
        updates = await asyncio.to_thread(
            self._bili_client.fetch_space_updates, uid
        )
        account = self._account_state(uid)
        was_initialized = bool(account.get("initialized"))
        previous_by_kind = {
            "dynamic": self._clean_seen_ids(account.get("seen_dynamic_ids")),
            "video": self._clean_seen_ids(account.get("seen_video_ids")),
        }

        if updates:
            account["name"] = updates[0].author

        if was_initialized and notify:
            new_updates = [
                update
                for update in updates
                if update.key not in previous_by_kind[update.kind]
            ]
            for update in reversed(new_updates):
                await self._send_bili_update(uid, update)

        for kind in _TYPE_ORDER:
            current = [
                update.key for update in updates if update.kind == kind
            ]
            state_key = f"seen_{kind}_ids"
            account[state_key] = self._remember_ids(
                current, previous_by_kind[kind]
            )
        account["initialized"] = True
        account["last_checked_at"] = int(datetime.now().timestamp())
        self._save_bili_state()

    async def _bili_poll_loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                for uid in self._subscribed_uids():
                    async with self._bili_check_lock:
                        try:
                            await self._refresh_bili_account(uid, notify=True)
                        except BilibiliApiError as exc:
                            logger.warning(f"检查B站 UID {uid} 更新失败: {exc}")
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.error(f"检查B站 UID {uid} 更新异常: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"B站订阅轮询任务异常: {exc}")
            await asyncio.sleep(BILI_POLL_INTERVAL_SECONDS)

    async def _subscribe(
        self, umo: str, uid: str, requested_kinds: set[str]
    ) -> str:
        async with self._bili_check_lock:
            targets = self._bili_state.setdefault("subscriptions", {}).setdefault(
                umo, {}
            )
            current = set(self._normalize_stored_kinds(targets.get(uid)))
            missing = requested_kinds - current
            account = self._account_state(uid)
            account_was_known = bool(
                account.get("initialized") or account.get("name")
            )
            if not missing:
                name = account.get("name") or f"UID {uid}"
                return f"当前会话已订阅 {name} 的{self._kind_text(requested_kinds)}。"

            refresh_error = None
            try:
                await self._refresh_bili_account(
                    uid, notify=bool(account.get("initialized"))
                )
            except Exception as exc:
                refresh_error = exc

            account = self._account_state(uid)
            if not account.get("name"):
                try:
                    account["name"] = await asyncio.to_thread(
                        self._bili_client.fetch_account_name, uid
                    )
                except Exception as profile_exc:
                    if refresh_error is None:
                        refresh_error = profile_exc

            if (
                refresh_error is not None
                and not account_was_known
                and not account.get("name")
            ):
                if not current:
                    targets.pop(uid, None)
                if not targets:
                    self._bili_state["subscriptions"].pop(umo, None)
                if not self._uid_has_subscribers(uid):
                    self._bili_state.get("accounts", {}).pop(uid, None)
                self._save_bili_state()
                return f"订阅失败：{refresh_error}"

            current.update(requested_kinds)
            targets[uid] = self._ordered_kinds(current)
            self._save_bili_state()

            name = account.get("name") or f"UID {uid}"
            result = f"已订阅 {name}（UID {uid}）的{self._kind_text(requested_kinds)}。"
            if refresh_error is not None:
                result += " 当前基线初始化失败，将在后续轮询中自动重试。"
            return result

    def _protected_kinds(self, umo: str, uid: str) -> set[str]:
        return set(
            BILI_BUILTIN_SUBSCRIPTIONS.get(umo, {}).get(uid, frozenset())
        )

    def _kind_text(self, kinds: set[str] | frozenset[str]) -> str:
        return "和".join(
            _TYPE_NAMES[kind] for kind in _TYPE_ORDER if kind in kinds
        )

    def _uid_has_subscribers(self, uid: str) -> bool:
        return any(
            isinstance(targets, dict)
            and bool(self._normalize_stored_kinds(targets.get(uid)))
            for targets in self._bili_state.get("subscriptions", {}).values()
        )

    async def _unsubscribe(
        self, umo: str, uid: str, requested_kinds: set[str]
    ) -> str:
        async with self._bili_check_lock:
            subscriptions = self._bili_state.setdefault("subscriptions", {})
            targets = subscriptions.get(umo)
            current = set(
                self._normalize_stored_kinds(
                    targets.get(uid) if isinstance(targets, dict) else None
                )
            )
            removable = (
                current & requested_kinds
            ) - self._protected_kinds(umo, uid)
            if not removable:
                if current & requested_kinds:
                    return "这是内置订阅目标，对应订阅不能通过命令取消。"
                return "当前会话没有对应的B站订阅。"

            remaining = current - removable
            account_name = (
                self._bili_state.get("accounts", {}).get(uid, {}).get("name")
            )
            if remaining:
                targets[uid] = self._ordered_kinds(remaining)
            else:
                targets.pop(uid, None)
                if not targets:
                    subscriptions.pop(umo, None)

            self._merge_builtin_subscriptions(self._bili_state)
            if not self._uid_has_subscribers(uid):
                self._bili_state.get("accounts", {}).pop(uid, None)
            self._save_bili_state()
            target_name = account_name or f"UID {uid}"
            return f"已取消 {target_name} 的{self._kind_text(removable)}订阅。"

    async def _latest_bili_result(
        self,
        event: AstrMessageEvent,
        uid_value: Any,
        kind_value: Any,
    ):
        try:
            uid, kinds = self._parse_command_target(uid_value, kind_value)
        except ValueError as exc:
            return event.plain_result(
                f"参数错误：{exc}\n"
                "用法：最新b站 [UID/空间链接] [全部/动态/视频]"
            )

        try:
            update = await asyncio.to_thread(
                self._bili_client.fetch_latest_update, uid, kinds
            )
        except BilibiliApiError as exc:
            return event.plain_result(f"获取B站最新更新失败：{exc}")
        except Exception as exc:
            logger.error(f"获取B站 UID {uid} 最新更新异常: {exc}")
            return event.plain_result("获取B站最新更新失败，请稍后重试。")

        if update is None:
            return event.plain_result(
                f"没有找到 UID {uid} 的公开{self._kind_text(kinds)}。"
            )

        rendered = await self._render_bili_card(update)
        return event.chain_result(
            self._bili_update_components(update, rendered)
        )

    @filter.command("最新b站")
    async def latest_bili(
        self,
        event: AstrMessageEvent,
        uid: str = "",
        content_type: str = "全部",
    ):
        """获取最新一条；格式：最新b站 [UID/空间链接] [全部/动态/视频]。"""

        yield await self._latest_bili_result(event, uid, content_type)

    @filter.command("最新b站动态")
    async def latest_bili_dynamic(
        self, event: AstrMessageEvent, uid: str = ""
    ):
        """获取指定 UID 最新一条公开动态。"""

        yield await self._latest_bili_result(event, uid, "动态")

    @filter.command("最新b站视频")
    async def latest_bili_video(
        self, event: AstrMessageEvent, uid: str = ""
    ):
        """获取指定 UID 最新一条投稿视频。"""

        yield await self._latest_bili_result(event, uid, "视频")

    @filter.command("订阅b站")
    async def subscribe_bili(
        self,
        event: AstrMessageEvent,
        uid: str = "",
        content_type: str = "全部",
    ):
        """订阅指定 UID；格式：订阅b站 [UID/空间链接] [全部/动态/视频]。"""

        try:
            normalized_uid, kinds = self._parse_command_target(uid, content_type)
        except ValueError as exc:
            yield event.plain_result(
                f"参数错误：{exc}\n用法：订阅b站 [UID/空间链接] [全部/动态/视频]"
            )
            return
        result = await self._subscribe(
            event.unified_msg_origin, normalized_uid, kinds
        )
        yield event.plain_result(result)

    @filter.command("取消订阅b站")
    async def unsubscribe_bili(
        self,
        event: AstrMessageEvent,
        uid: str = "",
        content_type: str = "全部",
    ):
        """取消指定 UID；格式：取消订阅b站 [UID/空间链接] [全部/动态/视频]。"""

        try:
            normalized_uid, kinds = self._parse_command_target(uid, content_type)
        except ValueError as exc:
            yield event.plain_result(
                f"参数错误：{exc}\n用法：取消订阅b站 [UID/空间链接] [全部/动态/视频]"
            )
            return
        result = await self._unsubscribe(
            event.unified_msg_origin, normalized_uid, kinds
        )
        yield event.plain_result(result)

    @filter.command("订阅b站更新")
    async def subscribe_bili_legacy(self, event: AstrMessageEvent):
        """兼容旧命令：订阅默认 UID 的动态和视频。"""

        result = await self._subscribe(
            event.unified_msg_origin,
            BILI_DEFAULT_UID,
            {"dynamic", "video"},
        )
        yield event.plain_result(result)

    @filter.command("取消订阅b站更新")
    async def unsubscribe_bili_legacy(self, event: AstrMessageEvent):
        """兼容旧命令：取消默认 UID 的动态和视频。"""

        result = await self._unsubscribe(
            event.unified_msg_origin,
            BILI_DEFAULT_UID,
            {"dynamic", "video"},
        )
        yield event.plain_result(result)

    @filter.command("查看b站订阅")
    async def show_bili_subscriptions(self, event: AstrMessageEvent):
        """查看当前会话的全部 B站账号订阅。"""

        targets = self._bili_state.get("subscriptions", {}).get(
            event.unified_msg_origin, {}
        )
        if not isinstance(targets, dict) or not targets:
            yield event.plain_result("当前会话没有B站订阅。")
            return

        lines = ["当前会话的B站订阅："]
        for uid in sorted(targets, key=int):
            kinds = set(self._normalize_stored_kinds(targets.get(uid)))
            if not kinds:
                continue
            account = self._bili_state.get("accounts", {}).get(uid, {})
            name = account.get("name") or f"UID {uid}"
            lines.append(
                f"- {name}（UID {uid}）：{self._kind_text(kinds)}"
            )
        lines.append(f"检查间隔：{BILI_POLL_INTERVAL_SECONDS // 60} 分钟")
        lines.append("数据来源：B站公开游客接口，不使用账号 Cookie")
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        """Stop the background worker when the plugin is unloaded."""

        if self._bili_poll_task:
            self._bili_poll_task.cancel()
            try:
                await self._bili_poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error(f"停止B站订阅任务时发生异常: {exc}")
