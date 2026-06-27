from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
import astrbot.api.message_components as Comp
import urllib.request
import urllib.parse
import json
import os
import asyncio
import glob
import hashlib
import time
from datetime import datetime
from .render import render_matches_card, ensure_fonts


BILI_TARGET_UID = 10082742
BILI_POLL_INTERVAL_SECONDS = 300
BILI_BUILTIN_SUBSCRIBERS = ["aiocqhttp:GroupMessage:617903838"]
BILI_ARCHIVE_URL = "https://api.bilibili.com/x/space/wbi/arc/search"
BILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
BILI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": f"https://space.bilibili.com/{BILI_TARGET_UID}/video",
    "Origin": "https://space.bilibili.com",
}


@register("xx_bot", "XX", "自用插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._hero_cache = None  # hero_id -> {name, localized_name}
        self._hero_img_dir = StarTools.get_data_dir("astrbot_plugin_xx_bot")
        self._bindings_file = os.path.join(self._hero_img_dir, "qq_steam_bindings.json")
        self._bindings = {}  # qq_id(str) -> steam32_id(str)
        self._bili_state_file = os.path.join(self._hero_img_dir, "bili_video_subscribe.json")
        self._bili_state = {}
        self._bili_poll_task = None

    async def initialize(self):
        """插件初始化：预加载英雄数据和头像缓存"""
        os.makedirs(self._hero_img_dir, exist_ok=True)
        # 清理之前可能遗留的临时卡片图片
        for old_img in glob.glob(os.path.join(self._hero_img_dir, "matches_card_*.png")):
            try:
                os.remove(old_img)
            except Exception:
                pass

        # DOTA/Steam 功能暂时停用，避免插件启动时加载旧功能状态。
        # self._bindings = self._load_bindings()
        self._bili_state = self._load_bili_state()
        self._bili_poll_task = asyncio.create_task(self._bili_video_poll_loop())
        logger.info("B站视频更新订阅任务已启动")
        # logger.info(f"QQ-Steam 绑定数据已加载，共 {len(self._bindings)} 条记录")
        # logger.info("正在预下载字体文件...")
        # await asyncio.to_thread(ensure_fonts, self._hero_img_dir)
        # logger.info("正在初始化英雄数据缓存...")
        # await asyncio.to_thread(self._fetch_heroes)
        # if self._hero_cache:
        #     logger.info(f"英雄数据加载完成，共 {len(self._hero_cache)} 个英雄，开始预加载头像...")
        #     await asyncio.to_thread(self._preload_hero_images)
        #     cached_count = len([f for f in os.listdir(self._hero_img_dir) if f.endswith('.png')])
        #     logger.info(f"英雄头像预加载完成，共缓存 {cached_count} 张头像")
        # else:
        #     logger.warning("英雄数据加载失败，头像将在运行时按需加载")

    def _fetch_heroes(self):
        """获取英雄数据并缓存"""
        if self._hero_cache is not None:
            return self._hero_cache
        try:
            req = urllib.request.Request(
                "https://api.opendota.com/api/heroes",
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                heroes = json.loads(response.read().decode())
            self._hero_cache = {}
            for h in heroes:
                short_name = h['name'].replace('npc_dota_hero_', '')
                self._hero_cache[h['id']] = {
                    'name': short_name,
                    'localized_name': h['localized_name']
                }
            return self._hero_cache
        except Exception as e:
            logger.error(f"获取英雄数据失败: {e}")
            return {}

    def _preload_hero_images(self):
        """预下载所有英雄头像到本地文件缓存（跳过已存在的）"""
        for hero_id, info in self._hero_cache.items():
            hero_name = info['name']
            local_path = os.path.join(self._hero_img_dir, f"{hero_name}.png")
            if not os.path.exists(local_path):
                self._download_hero_image(hero_name)

    def _download_hero_image(self, hero_name: str) -> bool:
        """从 Steam CDN 下载英雄头像到本地文件"""
        url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{hero_name}.png"
        local_path = os.path.join(self._hero_img_dir, f"{hero_name}.png")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                with open(local_path, 'wb') as f:
                    f.write(response.read())
            return True
        except Exception as e:
            logger.error(f"下载英雄头像失败 ({hero_name}): {e}")
            return False

    # ====== B站视频更新订阅 ======

    def _default_bili_state(self) -> dict:
        return {
            "subscribers": list(BILI_BUILTIN_SUBSCRIBERS),
            "last_video": {},
            "wbi_keys": {},
        }

    def _load_bili_state(self) -> dict:
        """从 JSON 文件加载 B 站订阅状态"""
        if os.path.exists(self._bili_state_file):
            try:
                with open(self._bili_state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                state = self._default_bili_state()
                state.update(data if isinstance(data, dict) else {})
                state["subscribers"] = list(dict.fromkeys(
                    [*BILI_BUILTIN_SUBSCRIBERS, *state.get("subscribers", [])]
                ))
                return state
            except Exception as e:
                logger.error(f"加载B站订阅状态失败: {e}")
        return self._default_bili_state()

    def _save_bili_state(self):
        """保存 B 站订阅状态到 JSON 文件"""
        try:
            with open(self._bili_state_file, 'w', encoding='utf-8') as f:
                json.dump(self._bili_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存B站订阅状态失败: {e}")

    def _http_get_json(self, url: str, headers=None) -> dict:
        req = urllib.request.Request(url, headers=headers or BILI_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    def _get_bili_wbi_keys(self) -> tuple[str, str]:
        cached = self._bili_state.get("wbi_keys") or {}
        if (
            cached.get("img_key")
            and cached.get("sub_key")
            and int(time.time()) - int(cached.get("updated_at", 0)) < 12 * 60 * 60
        ):
            return cached["img_key"], cached["sub_key"]

        data = self._http_get_json(BILI_NAV_URL, BILI_HEADERS)
        wbi_img = (data.get("data") or {}).get("wbi_img") or {}
        img_url = wbi_img.get("img_url", "")
        sub_url = wbi_img.get("sub_url", "")
        if not img_url or not sub_url:
            raise RuntimeError(f"获取B站WBI key失败: {data}")

        img_key = os.path.basename(img_url).split(".")[0]
        sub_key = os.path.basename(sub_url).split(".")[0]
        self._bili_state["wbi_keys"] = {
            "img_key": img_key,
            "sub_key": sub_key,
            "updated_at": int(time.time()),
        }
        self._save_bili_state()
        return img_key, sub_key

    def _sign_bili_wbi_params(self, params: dict) -> dict:
        img_key, sub_key = self._get_bili_wbi_keys()
        raw_key = img_key + sub_key
        mixin_key = "".join(raw_key[i] for i in BILI_MIXIN_KEY_ENC_TAB)[:32]

        signed = {k: str(v) for k, v in params.items()}
        signed["wts"] = str(int(time.time()))
        sorted_params = dict(sorted(signed.items()))
        clean_params = {
            k: "".join(ch for ch in v if ch not in "!'()*")
            for k, v in sorted_params.items()
        }
        query = urllib.parse.urlencode(clean_params)
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return signed

    def _fetch_bili_latest_videos(self, limit: int = 5) -> list:
        params = self._sign_bili_wbi_params({
            "mid": BILI_TARGET_UID,
            "order": "pubdate",
            "ps": max(1, min(limit, 30)),
            "pn": 1,
            "web_location": 1550101,
        })
        url = f"{BILI_ARCHIVE_URL}?{urllib.parse.urlencode(params)}"
        data = self._http_get_json(url, BILI_HEADERS)
        if data.get("code") != 0:
            # WBI key 偶尔会失效，清掉缓存后让下一轮重新获取。
            self._bili_state["wbi_keys"] = {}
            self._save_bili_state()
            raise RuntimeError(f"B站投稿列表获取失败: {data}")

        vlist = (((data.get("data") or {}).get("list") or {}).get("vlist") or [])
        return sorted(vlist, key=lambda item: int(item.get("created") or 0), reverse=True)

    def _format_bili_video_message(self, video: dict) -> str:
        bvid = video.get("bvid") or ""
        title = video.get("title") or "未命名视频"
        author = video.get("author") or f"UID {BILI_TARGET_UID}"
        created = int(video.get("created") or 0)
        pub_time = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else "未知"
        link = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        return (
            f"B站视频更新\n"
            f"UP：{author}\n"
            f"标题：{title}\n"
            f"发布时间：{pub_time}\n"
            f"{link}"
        )

    def _normalize_bili_cover_url(self, video: dict) -> str:
        cover_url = video.get("pic") or video.get("cover") or ""
        if cover_url.startswith("//"):
            cover_url = f"https:{cover_url}"
        elif cover_url.startswith("http://"):
            cover_url = f"https://{cover_url[len('http://'):]}"
        return cover_url

    def _is_new_bili_video(self, video: dict, last_video: dict) -> bool:
        if not video or not last_video:
            return False
        created = int(video.get("created") or 0)
        last_created = int(last_video.get("created") or 0)
        bvid = video.get("bvid")
        last_bvid = last_video.get("bvid")
        return created > last_created or (created == last_created and bvid != last_bvid)

    async def _send_bili_update(self, video: dict):
        subscribers = self._bili_state.get("subscribers") or []
        if not subscribers:
            return

        message = self._format_bili_video_message(video)
        cover_url = self._normalize_bili_cover_url(video)
        for umo in list(subscribers):
            try:
                chain = MessageChain([Comp.Plain(message)])
                if cover_url:
                    chain.append(Comp.Image.fromURL(cover_url))
                await self.context.send_message(umo, chain)
            except Exception as e:
                logger.error(f"发送B站更新到 {umo} 失败: {e}")

    async def _check_bili_video_update(self, notify: bool = True):
        videos = await asyncio.to_thread(self._fetch_bili_latest_videos, 5)
        if not videos:
            logger.warning(f"B站 UID {BILI_TARGET_UID} 暂无投稿视频")
            return

        latest = videos[0]
        last_video = self._bili_state.get("last_video") or {}
        if not last_video:
            self._bili_state["last_video"] = {
                "bvid": latest.get("bvid"),
                "created": int(latest.get("created") or 0),
                "title": latest.get("title"),
            }
            self._save_bili_state()
            logger.info(f"B站订阅已设置基线视频: {latest.get('bvid')} {latest.get('title')}")
            return

        new_videos = [video for video in videos if self._is_new_bili_video(video, last_video)]
        if not new_videos:
            return

        for video in reversed(new_videos):
            if notify:
                await self._send_bili_update(video)

        self._bili_state["last_video"] = {
            "bvid": latest.get("bvid"),
            "created": int(latest.get("created") or 0),
            "title": latest.get("title"),
        }
        self._save_bili_state()

    async def _bili_video_poll_loop(self):
        await asyncio.sleep(5)
        while True:
            try:
                if self._bili_state.get("subscribers"):
                    await self._check_bili_video_update(notify=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"B站视频更新检查失败: {e}")
            await asyncio.sleep(BILI_POLL_INTERVAL_SECONDS)

    @filter.command("订阅b站更新")
    async def subscribe_bili_video_update(self, event: AstrMessageEvent):
        """订阅 UID 10082742 的 B 站视频更新，更新会推送到当前会话。"""
        umo = event.unified_msg_origin
        subscribers = self._bili_state.setdefault("subscribers", [])
        if umo not in subscribers:
            subscribers.append(umo)
            self._save_bili_state()

        try:
            await self._check_bili_video_update(notify=False)
            latest = self._bili_state.get("last_video") or {}
            title = latest.get("title") or "暂无基线视频"
            yield event.plain_result(f"已订阅 UID {BILI_TARGET_UID} 的B站视频更新。当前基线：{title}")
        except Exception as e:
            logger.error(f"订阅B站更新时初始化基线失败: {e}")
            yield event.plain_result(f"已订阅 UID {BILI_TARGET_UID} 的B站视频更新，但初始化基线失败：{e}")

    @filter.command("取消订阅b站更新")
    async def unsubscribe_bili_video_update(self, event: AstrMessageEvent):
        """取消当前会话的 B 站视频更新订阅。"""
        umo = event.unified_msg_origin
        if umo in BILI_BUILTIN_SUBSCRIBERS:
            yield event.plain_result(f"QQ群 617903838 是内置订阅目标，不能通过命令取消。")
            return

        subscribers = self._bili_state.setdefault("subscribers", [])
        if umo in subscribers:
            subscribers.remove(umo)
            self._save_bili_state()
            yield event.plain_result(f"已取消订阅 UID {BILI_TARGET_UID} 的B站视频更新。")
        else:
            yield event.plain_result("当前会话还没有订阅B站视频更新。")

    @filter.command("查看b站订阅")
    async def show_bili_video_subscription(self, event: AstrMessageEvent):
        """查看当前 B 站视频更新订阅状态。"""
        umo = event.unified_msg_origin
        subscribers = self._bili_state.get("subscribers") or []
        latest = self._bili_state.get("last_video") or {}
        status = "已订阅" if umo in subscribers else "未订阅"
        latest_title = latest.get("title") or "暂无"
        yield event.plain_result(
            f"当前会话：{status}\n"
            f"订阅UID：{BILI_TARGET_UID}\n"
            f"订阅会话数：{len(subscribers)}\n"
            f"当前基线：{latest_title}"
        )


    # ====== QQ-Steam 绑定 ======

    def _load_bindings(self) -> dict:
        """从 JSON 文件加载 QQ→Steam 绑定"""
        if os.path.exists(self._bindings_file):
            try:
                with open(self._bindings_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载绑定数据失败: {e}")
        return {}

    def _save_bindings(self):
        """保存 QQ→Steam 绑定到 JSON 文件"""
        try:
            with open(self._bindings_file, 'w', encoding='utf-8') as f:
                json.dump(self._bindings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存绑定数据失败: {e}")

    def _normalize_steam_id(self, steamid: str) -> str:
        """将 Steam64 ID 转换为 Steam32 ID"""
        STEAM64_BASE = 76561197960265728
        steam_id_int = int(steamid)
        if steam_id_int >= STEAM64_BASE:
            steam_id_int -= STEAM64_BASE
        return str(steam_id_int)

    def _resolve_steamid(self, event: AstrMessageEvent, steamid: str) -> str:
        """解析 Steam ID：优先使用传入值，否则尝试从 QQ 映射获取"""
        if steamid:
            return steamid
        # 检查消息中是否有 @某人
        msg_chain = event.message_obj.message
        for comp in msg_chain:
            if hasattr(comp, 'qq') and str(comp.qq) != str(event.message_obj.self_id):
                target_qq = str(comp.qq)
                if target_qq in self._bindings:
                    return self._bindings[target_qq]
                return ""  # 被@的人未绑定
        # 使用发送者自己的 QQ
        sender_qq = str(event.get_sender_id())
        return self._bindings.get(sender_qq, "")

    # @filter.llm_tool(name="bind_steam_id")
    async def bind_steam_id(self, event: AstrMessageEvent, steamid: str) -> MessageEventResult:
        '''记录用户的DOTA2 Steam ID。只需提供Steam ID即可完成绑定，无需其他信息。绑定后用户查询对局数据时将自动使用该Steam ID。

        Args:
            steamid(string): 用户提供的Steam ID（支持Steam32或Steam64格式）
        '''
        try:
            normalized = self._normalize_steam_id(steamid)
        except (ValueError, TypeError):
            yield event.plain_result(f"无效的 Steam ID: {steamid}")
            return

        sender_qq = str(event.get_sender_id())
        self._bindings[sender_qq] = normalized
        self._save_bindings()
        logger.info(f"QQ {sender_qq} 绑定 Steam ID {normalized}")
        yield event.plain_result(f"绑定成功！QQ {sender_qq} → Steam ID {normalized}")

    def _prepare_match_data(self, matches: list, heroes: dict) -> list:
        """预处理对局数据"""
        result = []
        for m in matches:
            hero_id = m.get('hero_id', 0)
            k = m.get('kills', 0)
            d = m.get('deaths', 0)
            a = m.get('assists', 0)
            duration = m.get('duration', 0)
            player_slot = m.get('player_slot', 0)
            radiant_win = m.get('radiant_win', True)

            is_win = (player_slot < 128) == radiant_win
            kda_score = round((k + a) / max(d, 1), 1)

            hero_info = heroes.get(hero_id)
            hero_name = hero_info['localized_name'] if hero_info else f"Hero {hero_id}"
            hero_img_path = os.path.join(self._hero_img_dir, f"{hero_info['name']}.png") if hero_info else ""

            result.append({
                'is_win': is_win,
                'hero_img_path': hero_img_path,
                'hero_name': hero_name,
                'kda_score': kda_score,
                'kills': k,
                'deaths': d,
                'assists': a,
                'lobby_str': "天梯模式",
                'duration_str': f"{duration // 60}:{duration % 60:02d}",
            })
        return result

    # @filter.llm_tool(name="get_player_recent_matches")
    async def get_player_recent_matches(self, event: AstrMessageEvent, steamid: str = "", count: int = 1) -> MessageEventResult:
        '''获取指定玩家最近几盘DOTA2对局数据。如果用户没有提供steamid，会自动根据发送消息的QQ号或被@的人的QQ号查找已绑定的Steam ID。

        Args:
            steamid(string): 玩家的Steam ID（可选，未提供时自动从QQ绑定中查找）
            count(int): 要查询的最近对局盘数，默认1盘
        '''
        # 解析 Steam ID：传入值 > @某人的绑定 > 发送者自己的绑定
        steamid = self._resolve_steamid(event, steamid)
        if not steamid:
            yield event.plain_result("未找到绑定的 Steam ID。请先使用绑定功能将你的QQ号与Steam ID关联。")
            return

        # Steam64 → Steam32 转换
        try:
            steamid = self._normalize_steam_id(steamid)
        except (ValueError, TypeError):
            yield event.plain_result(f"无效的Steam ID: {steamid}")
            return

        count = min(count, 20)
        url = f"https://api.opendota.com/api/players/{steamid}/matches?lobby_type=7&limit={count}"
        try:
            def _fetch_data():
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=15) as response:
                    return json.loads(response.read().decode())

            data = await asyncio.to_thread(_fetch_data)

            if not isinstance(data, list):
                yield event.plain_result(f"获取失败，返回值: {data}")
                return

            if len(data) == 0:
                yield event.plain_result("未找到该玩家的天梯对局记录。可能未公开比赛数据。")
                return

            matches = data

            # 使用初始化时已缓存的英雄数据
            heroes = self._hero_cache or {}

            # 预处理对局数据
            match_data = await asyncio.to_thread(self._prepare_match_data, matches, heroes)

            # 使用 Pillow 高清渲染
            img_path = await asyncio.to_thread(
                render_matches_card, steamid, match_data,
                self._hero_img_dir, self._hero_img_dir
            )

            yield event.image_result(img_path)

            # 启动后台任务：延迟 60 秒后删除生成的图片文件
            async def _delayed_delete_image(path: str, delay: int = 60):
                await asyncio.sleep(delay)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        logger.info(f"已清理临时图片: {path}")
                except Exception as e:
                    logger.error(f"清理临时图片失败: {e}")

            asyncio.create_task(_delayed_delete_image(img_path))

        except Exception as e:
            logger.error(f"请求OpenDota API时发生错误: {str(e)}")
            yield event.plain_result(f"请求OpenDota API时发生错误: {str(e)}")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        if self._bili_poll_task:
            self._bili_poll_task.cancel()
            try:
                await self._bili_poll_task
            except asyncio.CancelledError:
                pass
