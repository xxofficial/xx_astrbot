from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
import urllib.request
import json
import os
import asyncio
from .render import render_matches_card



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

            # 预处理对局数据
            match_data = await asyncio.to_thread(self._prepare_match_data, matches, heroes)

            # 使用 Pillow 高清渲染
            img_path = await asyncio.to_thread(
                render_matches_card, steamid, match_data,
                self._hero_img_dir, self._hero_img_dir
            )

            yield event.image_result(img_path)

        except Exception as e:
            logger.error(f"请求OpenDota API时发生错误: {str(e)}")
            yield event.plain_result(f"请求OpenDota API时发生错误: {str(e)}")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
