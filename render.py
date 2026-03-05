"""
Pillow 高清对局卡片渲染模块
用于替代 html_render，以 2x 分辨率直接绘制 PNG 图片。
"""
import os
import urllib.request
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from astrbot.api import logger

# ====== 常量 ======
SCALE = 2  # 2x 高清
CARD_W = 720 * SCALE
CARD_H = 88 * SCALE
CARD_GAP = 8 * SCALE
CARD_RADIUS = 10 * SCALE
PAD = 24 * SCALE
WIN_STRIP_W = 4 * SCALE  # 左侧胜负边线宽度
LABEL_W = 52 * SCALE     # 胜/负标签区域宽度
AVATAR_SIZE = 60 * SCALE
AVATAR_RADIUS = 8 * SCALE
AVATAR_MARGIN = 12 * SCALE

# 颜色
BG_TOP = (26, 26, 46)
BG_BOT = (15, 52, 96)
WIN_BG = (40, 75, 50, 216)
LOSE_BG = (80, 35, 35, 216)
WIN_LABEL_BG = (60, 170, 90, 153)
LOSE_LABEL_BG = (200, 60, 60, 153)
WIN_STRIP_COLOR = (60, 170, 90)
LOSE_STRIP_COLOR = (221, 68, 68)
TEXT_PRIMARY = (240, 240, 240)
TEXT_SECONDARY = (160, 160, 168)
KDA_GREEN = (100, 220, 120)
KDA_RED = (240, 80, 80)
KDA_BLUE = (120, 180, 255)
DIVIDER_COLOR = (255, 255, 255, 26)

# 字体 URL（Noto Sans SC，使用国内 GitHub 代理加速）
FONT_URL = "https://ghfast.top/https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf"
FONT_FILENAME = "NotoSansCJKsc-Bold.otf"
FONT_URL_REGULAR = "https://ghfast.top/https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
FONT_FILENAME_REGULAR = "NotoSansCJKsc-Regular.otf"


def _ensure_font(data_dir: str, filename: str, url: str) -> str:
    """确保字体文件存在于 data_dir 中，不存在则下载"""
    path = os.path.join(data_dir, filename)
    if os.path.exists(path):
        return path
    logger.info(f"首次运行，正在下载字体 {filename} ...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(path, 'wb') as f:
                f.write(resp.read())
        logger.info(f"字体下载完成: {path}")
    except Exception as e:
        logger.error(f"字体下载失败: {e}")
        raise RuntimeError(f"无法下载字体文件 {filename}，请确保网络可访问 GitHub") from e
    return path


def _load_fonts(data_dir: str):
    """加载 Bold 和 Regular 字体，返回各种尺寸的 ImageFont"""
    bold_path = _ensure_font(data_dir, FONT_FILENAME, FONT_URL)
    regular_path = _ensure_font(data_dir, FONT_FILENAME_REGULAR, FONT_URL_REGULAR)

    return {
        'title': ImageFont.truetype(bold_path, 18 * SCALE),
        'win_label': ImageFont.truetype(bold_path, 13 * SCALE),
        'kda_big': ImageFont.truetype(bold_path, 28 * SCALE),
        'kda_detail': ImageFont.truetype(regular_path, 12 * SCALE),
        'hero_name': ImageFont.truetype(regular_path, 15 * SCALE),
        'info': ImageFont.truetype(regular_path, 12 * SCALE),
    }


def _draw_gradient_bg(img: Image.Image):
    """在图片上绘制从 BG_TOP 到 BG_BOT 的线性渐变背景"""
    w, h = img.size
    for y in range(h):
        ratio = y / max(h - 1, 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * ratio)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * ratio)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * ratio)
        ImageDraw.Draw(img).line([(0, y), (w, y)], fill=(r, g, b))


def _round_rect(draw: ImageDraw.ImageDraw, xy, radius, fill):
    """绘制圆角矩形"""
    x0, y0, x1, y1 = xy
    r = radius
    # 四个圆角
    draw.ellipse([x0, y0, x0 + 2*r, y0 + 2*r], fill=fill)
    draw.ellipse([x1 - 2*r, y0, x1, y0 + 2*r], fill=fill)
    draw.ellipse([x0, y1 - 2*r, x0 + 2*r, y1], fill=fill)
    draw.ellipse([x1 - 2*r, y1 - 2*r, x1, y1], fill=fill)
    # 中间填充
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)


def _round_image(img: Image.Image, radius: int) -> Image.Image:
    """给图片加圆角蒙版"""
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.size[0], img.size[1]], radius=radius, fill=255)
    result = img.copy()
    result.putalpha(mask)
    return result


def _text_center_y(draw, text, font, target_cy):
    """计算让文字垂直居中于 target_cy 的 y 坐标"""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    return int(target_cy - text_h / 2)


def _draw_match_card(overlay: Image.Image, draw_overlay: ImageDraw.ImageDraw,
                     x0: int, y0: int, match: dict, fonts: dict, img_dir: str):
    """在 overlay（RGBA）上绘制一张对局卡片"""
    is_win = match['is_win']
    x1 = x0 + CARD_W - 2 * PAD
    y1 = y0 + CARD_H
    cy = y0 + CARD_H // 2  # 卡片垂直中心

    # — 卡片背景（圆角）—
    card_bg = WIN_BG if is_win else LOSE_BG
    draw_overlay.rounded_rectangle([x0, y0, x1, y1], radius=CARD_RADIUS, fill=card_bg)

    # — 左侧胜负边线（细条，直接用矩形）—
    strip_color = WIN_STRIP_COLOR if is_win else LOSE_STRIP_COLOR
    draw_overlay.rectangle([x0, y0, x0 + WIN_STRIP_W, y1], fill=(*strip_color, 255))

    # — 胜/负标签 —
    label_x0 = x0 + WIN_STRIP_W
    label_x1 = label_x0 + LABEL_W
    label_bg = WIN_LABEL_BG if is_win else LOSE_LABEL_BG
    draw_overlay.rectangle([label_x0, y0, label_x1, y1], fill=label_bg)
    label_text = "胜利" if is_win else "失败"
    lbox = draw_overlay.textbbox((0, 0), label_text, font=fonts['win_label'])
    lw = lbox[2] - lbox[0]
    lx = label_x0 + (LABEL_W - lw) // 2
    ly = _text_center_y(draw_overlay, label_text, fonts['win_label'], cy)
    draw_overlay.text((lx, ly), label_text, fill=(255, 255, 255), font=fonts['win_label'])

    # — 英雄头像 —
    avatar_x = label_x1 + AVATAR_MARGIN
    avatar_y = cy - AVATAR_SIZE // 2
    hero_img_path = match.get('hero_img_path', '')
    if hero_img_path and os.path.exists(hero_img_path):
        try:
            avatar = Image.open(hero_img_path).convert('RGBA')
            avatar = avatar.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
            avatar = _round_image(avatar, AVATAR_RADIUS)
            overlay.paste(avatar, (avatar_x, avatar_y), avatar)
        except Exception:
            # 头像加载失败，绘制灰色占位
            _round_rect(draw_overlay, (avatar_x, avatar_y,
                        avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE),
                        AVATAR_RADIUS, fill=(51, 51, 51, 255))
    else:
        _round_rect(draw_overlay, (avatar_x, avatar_y,
                    avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE),
                    AVATAR_RADIUS, fill=(51, 51, 51, 255))

    # — KDA 分数 —
    kda_cx = avatar_x + AVATAR_SIZE + AVATAR_MARGIN + 45 * SCALE
    kda_text = str(match['kda_score'])
    kbox = draw_overlay.textbbox((0, 0), kda_text, font=fonts['kda_big'])
    kw = kbox[2] - kbox[0]
    kx = kda_cx - kw // 2
    ky = cy - 20 * SCALE
    draw_overlay.text((kx, ky), kda_text, fill=TEXT_PRIMARY, font=fonts['kda_big'])

    # KDA 详情（K / D / A）
    k_str = str(match['kills'])
    d_str = str(match['deaths'])
    a_str = str(match['assists'])
    sep = " / "
    detail_y = cy + 12 * SCALE

    # 计算整体宽度以居中
    parts = [
        (k_str, KDA_GREEN), (sep, TEXT_SECONDARY),
        (d_str, KDA_RED), (sep, TEXT_SECONDARY),
        (a_str, KDA_BLUE),
    ]
    total_w = sum(draw_overlay.textbbox((0, 0), t, font=fonts['kda_detail'])[2] for t, _ in parts)
    dx = kda_cx - total_w // 2
    for text, color in parts:
        draw_overlay.text((dx, detail_y), text, fill=color, font=fonts['kda_detail'])
        dx += draw_overlay.textbbox((0, 0), text, font=fonts['kda_detail'])[2]

    # — 英雄名 + 模式 + 时长 —
    info_x = kda_cx + 65 * SCALE
    hero_name = match['hero_name']
    draw_overlay.text((info_x, cy - 24 * SCALE), hero_name,
                      fill=TEXT_PRIMARY, font=fonts['hero_name'])
    draw_overlay.text((info_x, cy - 2 * SCALE), match['lobby_str'],
                      fill=TEXT_SECONDARY, font=fonts['info'])
    draw_overlay.text((info_x, cy + 14 * SCALE), match['duration_str'],
                      fill=TEXT_SECONDARY, font=fonts['info'])


def render_matches_card(steamid: str, match_data: list, data_dir: str, img_dir: str) -> str:
    """
    渲染对局卡片图片。

    Args:
        steamid: 玩家 Steam32 ID
        match_data: 预处理后的对局数据列表
        data_dir: 插件数据目录（存放字体、输出图片）
        img_dir: 英雄头像目录

    Returns:
        输出图片的绝对路径
    """
    fonts = _load_fonts(data_dir)
    count = len(match_data)

    # 计算画布高度
    title_h = 50 * SCALE
    content_h = count * CARD_H + max(0, count - 1) * CARD_GAP
    total_h = PAD + title_h + content_h + PAD

    # 创建背景
    img = Image.new('RGB', (CARD_W, total_h))
    _draw_gradient_bg(img)

    # 创建 RGBA 覆盖层（用于半透明卡片）
    overlay = Image.new('RGBA', (CARD_W, total_h), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    # — 标题 —
    title = f"SteamID {steamid} 的最近 {count} 场天梯对局"
    draw_overlay.text((PAD, PAD), title, fill=(224, 224, 224, 255), font=fonts['title'])

    # 分割线
    divider_y = PAD + 36 * SCALE
    draw_overlay.line([(PAD, divider_y), (CARD_W - PAD, divider_y)], fill=DIVIDER_COLOR, width=SCALE)

    # — 逐行绘制卡片 —
    card_y = PAD + title_h
    for m in match_data:
        _draw_match_card(overlay, draw_overlay, PAD, card_y, m, fonts, img_dir)
        card_y += CARD_H + CARD_GAP

    # 合成到背景
    img = img.convert('RGBA')
    img = Image.alpha_composite(img, overlay)
    img = img.convert('RGB')

    # 保存输出
    out_path = os.path.join(data_dir, "matches_card.png")
    img.save(out_path, "PNG", quality=100)
    return out_path
