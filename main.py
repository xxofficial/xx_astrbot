from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import urllib.request
import json
import io
import os
import asyncio
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.api.star import StarTools
import tempfile
from PIL import Image as PILImage, ImageDraw, ImageFont, ImageFilter

@register("xx_bot", "XX", "自用插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._hero_cache = None  # hero_id -> {name, localized_name}
        self._hero_img_dir = StarTools.get_data_dir("xx_bot") / "heroes"

    async def initialize(self):
        """插件初始化：预加载英雄数据和头像缓存"""
        os.makedirs(self._hero_img_dir, exist_ok=True)
        logger.info("正在初始化英雄数据缓存...")
        await asyncio.to_thread(self._fetch_heroes)
        if self._hero_cache:
            logger.info(f"英雄数据加载完成，共 {len(self._hero_cache)} 个英雄，开始预加载头像...")
            await asyncio.to_thread(self._preload_hero_images)
            cached_count = len([f for f in os.listdir(self._hero_img_dir) if f.endswith('.png')])
            logger.info(f"英雄头像预加载完成，共缓存 {cached_count} 张头像")
        else:
            logger.warning("英雄数据加载失败，头像将在运行时按需加载")

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

    def _load_hero_image(self, hero_name: str) -> PILImage.Image | None:
        """从本地文件缓存加载英雄头像"""
        local_path = os.path.join(self._hero_img_dir, f"{hero_name}.png")
        if not os.path.exists(local_path):
            # 本地不存在，尝试下载
            if not self._download_hero_image(hero_name):
                return None
        try:
            return PILImage.open(local_path).convert("RGBA")
        except Exception as e:
            logger.error(f"加载英雄头像失败 ({hero_name}): {e}")
            return None

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """获取中文字体（Linux 服务器环境）"""
        font_paths = [
            # Linux 常见中文字体 (apt install fonts-noto-cjk-extra)
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            # Windows 备用
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    continue

        # 如果系统没有中文字体，尝试从国内可访问的镜像下载
        cache_font = os.path.join(tempfile.gettempdir(), "NotoSansSC-Regular.ttf")
        if os.path.exists(cache_font):
            try:
                return ImageFont.truetype(cache_font, size)
            except Exception:
                pass

        font_urls = [
            "https://cdn.npmmirror.com/packages/@aspect-build/noto-sans-sc/0.0.0/noto-sans-sc-0.0.0.tgz",
            "https://raw.gitmirror.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf",
            "https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf",
        ]
        for font_url in font_urls:
            try:
                logger.info(f"系统未找到中文字体，正在下载: {font_url}")
                req = urllib.request.Request(font_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    with open(cache_font, 'wb') as f:
                        f.write(resp.read())
                return ImageFont.truetype(cache_font, size)
            except Exception as e:
                logger.warning(f"下载字体失败({font_url}): {e}")
                continue

        logger.error("所有字体源均失败，建议在服务器执行: apt install -y fonts-noto-cjk-extra")
        return ImageFont.load_default()

    def _render_matches_image(self, matches: list, steamid: str, heroes: dict) -> PILImage.Image:
        """渲染对局结果图片"""
        # ========== 布局常量 ==========
        ROW_HEIGHT = 100
        CARD_WIDTH = 700
        PADDING = 16
        HERO_IMG_SIZE = 64
        CARD_RADIUS = 12
        ROW_GAP = 8

        # ========== 字体 ==========
        font_title = self._get_font(20)
        font_big = self._get_font(32)
        font_mid = self._get_font(16)
        font_small = self._get_font(13)

        # ========== 颜色 ==========
        BG_COLOR = (30, 30, 40, 255)
        CARD_WIN = (35, 60, 40, 240)
        CARD_LOSE = (65, 35, 35, 240)
        WIN_BADGE = (60, 170, 90)
        LOSE_BADGE = (200, 60, 60)
        TEXT_WHITE = (240, 240, 240)
        TEXT_GRAY = (160, 160, 170)
        TEXT_GOLD = (255, 215, 80)
        KILL_COLOR = (100, 220, 120)
        DEATH_COLOR = (240, 80, 80)
        ASSIST_COLOR = (120, 180, 255)

        # ========== 计算画布大小 ==========
        title_height = 50
        total_height = title_height + len(matches) * (ROW_HEIGHT + ROW_GAP) + PADDING
        img = PILImage.new("RGBA", (CARD_WIDTH, total_height), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # ========== 标题 ==========
        title_text = f"SteamID {steamid} 的最近 {len(matches)} 场对局"
        draw.text((PADDING, 14), title_text, fill=TEXT_WHITE, font=font_title)

        # ========== 渲染每行对局 ==========
        for i, m in enumerate(matches):
            y_start = title_height + i * (ROW_HEIGHT + ROW_GAP)

            hero_id = m.get('hero_id', 0)
            k = m.get('kills', 0)
            d = m.get('deaths', 0)
            a = m.get('assists', 0)
            duration = m.get('duration', 0)
            lobby_type = m.get('lobby_type', 0)
            player_slot = m.get('player_slot', 0)
            radiant_win = m.get('radiant_win', True)
            hero_level = m.get('hero_level', 0) if 'hero_level' in m else None
            gpm = m.get('gold_per_min', 0)
            xpm = m.get('xp_per_min', 0)

            is_win = (player_slot < 128) == radiant_win
            kda_score = round((k + a) / max(d, 1), 1)
            duration_str = f"{duration // 60}:{duration % 60:02d}"
            lobby_str = "天梯模式"

            # ---------- 卡片背景 ----------
            card_rect = [PADDING, y_start, CARD_WIDTH - PADDING, y_start + ROW_HEIGHT]
            card_color = CARD_WIN if is_win else CARD_LOSE
            draw.rounded_rectangle(card_rect, radius=CARD_RADIUS, fill=card_color)

            # ---------- 胜负标记 ----------
            badge_w = 50
            badge_rect = [PADDING, y_start, PADDING + badge_w, y_start + ROW_HEIGHT]
            badge_color = WIN_BADGE if is_win else LOSE_BADGE
            # 画左侧圆角矩形色块
            draw.rounded_rectangle(badge_rect, radius=CARD_RADIUS, fill=badge_color)
            # 右侧覆盖一个直角矩形，让右边不圆角
            draw.rectangle([PADDING + badge_w - CARD_RADIUS, y_start, PADDING + badge_w, y_start + ROW_HEIGHT], fill=badge_color)

            win_text = "胜利" if is_win else "失败"
            bbox = draw.textbbox((0, 0), win_text, font=font_small)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                (PADDING + (badge_w - tw) // 2, y_start + (ROW_HEIGHT - th) // 2),
                win_text, fill=TEXT_WHITE, font=font_small
            )

            # ---------- 英雄头像 ----------
            hero_x = PADDING + badge_w + 12
            hero_y = y_start + (ROW_HEIGHT - HERO_IMG_SIZE) // 2
            hero_info = heroes.get(hero_id)

            if hero_info:
                hero_img = self._load_hero_image(hero_info['name'])
                if hero_img:
                    # 裁剪为更方正的比例
                    w, h = hero_img.size
                    if w > h:
                        left = (w - h) // 2
                        hero_img = hero_img.crop((left, 0, left + h, h))
                    hero_img = hero_img.resize((HERO_IMG_SIZE, HERO_IMG_SIZE), PILImage.LANCZOS)
                    # 粘贴到画布
                    img.paste(hero_img, (hero_x, hero_y), hero_img)

            # 英雄等级（左下角小数字）
            if hero_level:
                level_text = str(hero_level)
                lbbox = draw.textbbox((0, 0), level_text, font=font_small)
                lw = lbbox[2] - lbbox[0]
                lh = lbbox[3] - lbbox[1]
                lx = hero_x + HERO_IMG_SIZE - lw - 2
                ly = hero_y + HERO_IMG_SIZE - lh - 4
                # 背景圆
                draw.ellipse([lx - 4, ly - 2, lx + lw + 4, ly + lh + 4], fill=(0, 0, 0, 200))
                draw.text((lx, ly), level_text, fill=TEXT_GOLD, font=font_small)

            # ---------- KDA 分数（大字） ----------
            kda_x = hero_x + HERO_IMG_SIZE + 20
            kda_text = str(kda_score)
            draw.text((kda_x, y_start + 18), kda_text, fill=TEXT_WHITE, font=font_big)

            # K / D / A 细节
            kda_detail = f"{k} / {d} / {a}"
            draw.text((kda_x, y_start + 58), kda_detail, fill=TEXT_GRAY, font=font_small)

            # ---------- 英雄名 ----------
            if hero_info:
                hero_name_display = hero_info['localized_name']
            else:
                hero_name_display = f"Hero {hero_id}"
            name_x = kda_x + 120
            draw.text((name_x, y_start + 18), hero_name_display, fill=TEXT_WHITE, font=font_mid)

            # ---------- 游戏模式和时长 ----------
            draw.text((name_x, y_start + 42), lobby_str, fill=TEXT_GRAY, font=font_small)
            draw.text((name_x, y_start + 62), duration_str, fill=TEXT_GRAY, font=font_small)

            # ---------- GPM / XPM ----------
            stat_x = CARD_WIDTH - PADDING - 120
            draw.text((stat_x, y_start + 22), f"GPM {gpm}", fill=TEXT_GOLD, font=font_small)
            draw.text((stat_x, y_start + 42), f"XPM {xpm}", fill=ASSIST_COLOR, font=font_small)

        return img.convert("RGB")

    @filter.llm_tool(name="get_player_recent_matches")
    async def get_player_recent_matches(self, event: AstrMessageEvent, steamid: str, count: int = 1) -> MessageEventResult:
        '''获取指定steamid的玩家最近几盘DOTA2对局数据。
        
        Args:
            steamid(string): 玩家的Steam32 ID
            count(int): 要查询的最近对局盘数，默认1盘
        '''
        # 如果输入的是Steam64 ID，转换为Steam32 ID
        STEAM64_BASE = 76561197960265728
        try:
            steam_id_int = int(steamid)
            if steam_id_int >= STEAM64_BASE:
                steam_id_int -= STEAM64_BASE
            steamid = str(steam_id_int)
        except ValueError:
            yield event.plain_result(f"无效的Steam ID: {steamid}")
            return

        count = min(count, 20)
        url = f"https://api.opendota.com/api/players/{steamid}/matches?lobby_type=7&limit={count}"
        try:
            # 使用 asyncio.to_thread 避免阻塞事件循环
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

            # 渲染图片
            result_img = await asyncio.to_thread(self._render_matches_image, matches, steamid, heroes)

            # 保存到临时文件
            tmp_path = os.path.join(tempfile.gettempdir(), f"dota2_matches_{steamid}.png")
            result_img.save(tmp_path, "PNG")

            # 发送图片
            yield event.image_result(tmp_path)

        except Exception as e:
            logger.error(f"请求OpenDota API时发生错误: {str(e)}")
            yield event.plain_result(f"请求OpenDota API时发生错误: {str(e)}")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
