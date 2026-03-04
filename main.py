from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
import urllib.request
import json
import os
import asyncio
import base64

# Lobby type 映射

# HTML + Jinja2 模板
MATCHES_TMPL = '''
<div style="
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', 'Microsoft YaHei', sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    padding: 24px;
    min-width: 680px;
    max-width: 720px;
">
    <!-- 标题 -->
    <div style="
        color: #e0e0e0;
        font-size: 18px;
        font-weight: 600;
        margin-bottom: 16px;
        padding-bottom: 12px;
        border-bottom: 1px solid rgba(255,255,255,0.1);
    ">
        SteamID {{ steamid }} 的最近 {{ matches|length }} 场天梯对局
    </div>

    {% for m in matches %}
    <div style="
        display: flex;
        align-items: center;
        background: {{ 'rgba(40, 75, 50, 0.85)' if m.is_win else 'rgba(80, 35, 35, 0.85)' }};
        border-radius: 10px;
        margin-bottom: 8px;
        overflow: hidden;
        height: 88px;
        border-left: 4px solid {{ '#3caa5a' if m.is_win else '#d44' }};
    ">
        <!-- 胜负标记 -->
        <div style="
            width: 52px;
            min-width: 52px;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            background: {{ 'rgba(60, 170, 90, 0.6)' if m.is_win else 'rgba(200, 60, 60, 0.6)' }};
            color: white;
            font-size: 13px;
            font-weight: 700;
        ">{{ '胜利' if m.is_win else '失败' }}</div>

        <!-- 英雄头像 -->
        <div style="
            width: 60px;
            height: 60px;
            margin: 0 12px;
            border-radius: 8px;
            overflow: hidden;
            flex-shrink: 0;
            position: relative;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        ">
            {% if m.hero_img %}
            <img src="{{ m.hero_img }}" style="width: 100%; height: 100%; object-fit: cover;" />
            {% else %}
            <div style="width:100%;height:100%;background:#333;display:flex;align-items:center;justify-content:center;color:#888;font-size:11px;">N/A</div>
            {% endif %}
            {% if m.hero_level %}
            <div style="
                position: absolute;
                bottom: 2px;
                right: 2px;
                background: rgba(0,0,0,0.75);
                color: #ffd750;
                font-size: 11px;
                font-weight: bold;
                border-radius: 50%;
                width: 18px;
                height: 18px;
                display: flex;
                align-items: center;
                justify-content: center;
            ">{{ m.hero_level }}</div>
            {% endif %}
        </div>

        <!-- KDA 分数 -->
        <div style="min-width: 90px; text-align: center;">
            <div style="color: #f0f0f0; font-size: 28px; font-weight: 700; line-height: 1.1;">{{ m.kda_score }}</div>
            <div style="color: #a0a0a8; font-size: 12px; margin-top: 4px;">
                <span style="color: #64dc78;">{{ m.kills }}</span>
                <span style="color: #888;"> / </span>
                <span style="color: #f05050;">{{ m.deaths }}</span>
                <span style="color: #888;"> / </span>
                <span style="color: #78b4ff;">{{ m.assists }}</span>
            </div>
        </div>

        <!-- 英雄名 + 模式 + 时长 -->
        <div style="flex: 1; padding: 0 12px; min-width: 0;">
            <div style="color: #f0f0f0; font-size: 15px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{{ m.hero_name }}</div>
            <div style="color: #a0a0a8; font-size: 12px; margin-top: 4px;">{{ m.lobby_str }}</div>
            <div style="color: #a0a0a8; font-size: 12px; margin-top: 2px;">{{ m.duration_str }}</div>
        </div>
    </div>
    {% endfor %}
</div>
'''

@register("xx_bot", "XX", "自用插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._hero_cache = None  # hero_id -> {name, localized_name}
        self._hero_img_dir = StarTools.get_data_dir("astrbot_plugin_xx_bot")

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

    def _get_hero_img_data_url(self, hero_name: str) -> str:
        """获取英雄头像的 data URL（用于 HTML 内嵌图片）"""
        local_path = os.path.join(self._hero_img_dir, f"{hero_name}.png")
        if not os.path.exists(local_path):
            if not self._download_hero_image(hero_name):
                return ""
        try:
            with open(local_path, 'rb') as f:
                img_data = f.read()
            b64 = base64.b64encode(img_data).decode('ascii')
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            logger.error(f"读取英雄头像失败 ({hero_name}): {e}")
            return ""

    def _prepare_match_data(self, matches: list, heroes: dict) -> list:
        """预处理对局数据，供 Jinja2 模板使用"""
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
            hero_img = self._get_hero_img_data_url(hero_info['name']) if hero_info else ""

            result.append({
                'is_win': is_win,
                'hero_img': hero_img,
                'hero_name': hero_name,
                'hero_level': hero_level,
                'kda_score': kda_score,
                'kills': k,
                'deaths': d,
                'assists': a,
                'lobby_str': "天梯模式",
                'duration_str': f"{duration // 60}:{duration % 60:02d}",
            })
        return result

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

            # 预处理对局数据（含读取头像文件，放到线程中执行）
            match_data = await asyncio.to_thread(self._prepare_match_data, matches, heroes)

            # 使用 AstrBot 官方 HTML 文转图渲染
            url = await self.html_render(
                MATCHES_TMPL,
                {"steamid": steamid, "matches": match_data},
                options={"omit_background": True}
            )

            yield event.image_result(url)

        except Exception as e:
            logger.error(f"请求OpenDota API时发生错误: {str(e)}")
            yield event.plain_result(f"请求OpenDota API时发生错误: {str(e)}")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
