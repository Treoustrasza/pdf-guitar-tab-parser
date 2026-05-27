"""
节奏/时值解析模块

PDF 中的时值编码方式（通过探查确认）：
  - 小节线：宽 ~0.7pt、高 ~31.9pt 的黑色填充矩形，x 坐标对齐弦线组
  - 符干（stem）：细竖线（linewidth=0.51），位于弦线组下方
  - 符梁（beam）：宽 15~23pt、高 ~2.1pt 的黑色填充矩形，位于弦线组下方
      每个符干下方的符梁数量决定时值细分（连组音符）
  - 旗（flag）：符干末端的弯钩曲线，w~5pt、h~13.7pt、pts=12
      单个音符（不在符梁组内）的八分/十六分标志
  - 附点（dot）：小圆点曲线，w≈h≈1.7pt、pts=6，位于符干旁边弦线组下方
  - 小节编号：size=6 的 TimesNewRoman 数字，位于弦线组上方

时值判断逻辑（优先级：符梁 > 旗 > 符干高度）：
  有符梁覆盖：
    1条符梁 → 八分音符 (1/8)
    2条符梁 → 十六分音符 (1/16)
    3条符梁 → 三十二分音符 (1/32)
    4条符梁 → 六十四分音符 (1/64)
  无符梁，有旗（flag）：
    1个旗   → 八分音符 (1/8)
    2个旗   → 十六分音符 (1/16)
  无符梁，无旗：
    h/gap ~1.75 → 四分音符 (1/4)
    h/gap ~2.8  → 附点四分音符（通常伴随附点圆点）
    h/gap ~3.8  → 附点四分音符（另一种写法）
    h/gap ~4.8  → 二分音符 (1/2)
    h/gap ~5.0  → 小节线（排除）
  附点修饰：时值 × 1.5（由附点圆点触发，或由斜线附点触发）
"""

import pdfplumber
from collections import defaultdict


# ─────────────────────────────────────────────
# 时值常量
# ─────────────────────────────────────────────
DURATION_NAMES = {
    4:    'whole',       # 全音符
    3:    'half.',       # 附点二分音符
    2:    'half',        # 二分音符
    1.5:  'quarter.',    # 附点四分音符
    1:    'quarter',     # 四分音符
    0.75: 'eighth.',     # 附点八分音符
    0.5:  'eighth',      # 八分音符
    0.375:'16th.',       # 附点十六分音符
    0.25: '16th',        # 十六分音符
    0.1875:'32nd.',      # 附点三十二分音符
    0.125: '32nd',       # 三十二分音符
    0.0625: '64th',      # 六十四分音符
}

BEAM_COUNT_TO_DURATION = {
    1: 0.5,     # 八分音符
    2: 0.25,    # 十六分音符
    3: 0.125,   # 三十二分音符
    4: 0.0625,  # 六十四分音符
}

# 无符梁时，按 h/gap 比值判断时值
# (min_ratio, max_ratio) → duration_beats
_NO_BEAM_RATIO_TABLE = [
    (0.5,  1.4,  None),   # 太短，忽略（可能是噪声）
    (1.4,  2.3,  1.0),    # 四分音符  h/gap ~1.75
    (2.3,  3.3,  0.5),    # 八分音符（有旗）或附点四分（有附点）h/gap ~2.8~3.0
    (3.3,  4.3,  1.5),    # 附点四分音符  h/gap ~3.8
    (4.3,  5.5,  2.0),    # 二分音符  h/gap ~4.8
    (5.5,  7.0,  None),   # 小节线高度，排除
]


def _sg_gap(sg):
    """计算弦线组的平均弦间距"""
    if len(sg) < 2:
        return 6.0
    return (sg[-1] - sg[0]) / (len(sg) - 1)


def _is_black(color):
    """
    判断颜色是否为黑色（或接近黑色）。
    兼容多种颜色格式：
      - (r, g, b) 元组，值域 0~1
      - 单个灰度值（float），0=黑
      - None（无填充）→ False
    """
    if color is None:
        return False
    if isinstance(color, (int, float)):
        return color < 0.1
    if isinstance(color, (tuple, list)):
        if len(color) == 1:
            return color[0] < 0.1
        if len(color) == 3:
            r, g, b = color
            return r < 0.1 and g < 0.1 and b < 0.1
        if len(color) == 4:  # CMYK
            c, m, y, k = color
            return k > 0.9
    return False


# ─────────────────────────────────────────────
# 小节线识别
# ─────────────────────────────────────────────
def find_barlines(page, string_groups):
    """
    从页面矩形中识别小节线。
    小节线特征：极窄、高度接近弦线组高度、黑色填充。
    所有尺寸阈值基于弦线组间距动态计算。
    双小节线（两条紧挨着的线）合并为一条。
    返回：dict，key=group_idx，value=该行的小节线 x 坐标列表（已排序）
    """
    barlines_by_group = defaultdict(list)

    for r in page.rects:
        w = r['x1'] - r['x0']
        h = r['bottom'] - r['top']
        fill = r.get('non_stroking_color')

        if not _is_black(fill):
            continue

        rect_top = r['top']
        for g_idx, sg in enumerate(string_groups):
            gap = _sg_gap(sg)
            group_height = sg[-1] - sg[0]
            margin = gap * 1.0

            if h < group_height * 0.6:
                continue
            if w > gap:
                continue
            if (sg[0] - margin) <= rect_top <= (sg[-1] + margin):
                barlines_by_group[g_idx].append(r['x0'])
                break

    # 排序，并合并双小节线
    for g_idx in barlines_by_group:
        sg = string_groups[g_idx]
        gap = _sg_gap(sg)
        merge_dist = gap * 1.0
        xs = sorted(barlines_by_group[g_idx])
        merged = []
        for x in xs:
            if merged and x - merged[-1] < merge_dist:
                merged[-1] = (merged[-1] + x) / 2
            else:
                merged.append(x)
        barlines_by_group[g_idx] = merged

    return barlines_by_group


# ─────────────────────────────────────────────
# 符干识别
# ─────────────────────────────────────────────
def find_stems(page, string_groups):
    """
    识别符干（细竖线）。
    返回：dict，key=group_idx，value=[{x, top, bottom, height}, ...]
    """
    stems_by_group = defaultdict(list)
    group_params = [(sg, _sg_gap(sg)) for sg in string_groups]

    for l in page.lines:
        if abs(l['x0'] - l['x1']) > 0.8:
            continue

        page_height = page.height
        top = page_height - l['y1']
        bottom = page_height - l['y0']
        h = bottom - top
        if h <= 0:
            continue

        x = l['x0']

        # 排除行首谱号区域（x < 50pt 通常是高音谱号/拍号，不是符干）
        if x < 50.0:
            continue

        for g_idx, (sg, gap) in enumerate(group_params):
            group_bottom = sg[-1]
            if not (gap * 0.5 < h < gap * 8.0):
                continue
            if sg[0] - gap <= top <= group_bottom + gap:
                stems_by_group[g_idx].append({
                    'x': x,
                    'top': top,
                    'bottom': bottom,
                    'height': h,
                })
                break

    return stems_by_group


# ─────────────────────────────────────────────
# 符梁识别
# ─────────────────────────────────────────────
def find_beams(page, string_groups):
    """
    识别符梁（宽矩形，位于弦线组下方）。
    返回：dict，key=group_idx，value=[{x0, x1, top}, ...]
    """
    beams_by_group = defaultdict(list)
    group_params = [(sg, _sg_gap(sg)) for sg in string_groups]

    for r in page.rects:
        w = r['x1'] - r['x0']
        h = r['bottom'] - r['top']
        fill = r.get('non_stroking_color')

        if not _is_black(fill):
            continue

        beam_top = r['top']

        for g_idx, (sg, gap) in enumerate(group_params):
            group_bottom = sg[-1]
            if not (group_bottom < beam_top < group_bottom + gap * 8.0):
                continue
            if w < gap:
                continue
            if not (gap * 0.1 < h < gap * 0.8):
                continue
            beams_by_group[g_idx].append({
                'x0': r['x0'],
                'x1': r['x1'],
                'top': beam_top,
            })
            break

    return beams_by_group


# ─────────────────────────────────────────────
# 旗（flag）识别
# ─────────────────────────────────────────────
def find_flags(page, string_groups):
    """
    识别符干末端的旗（flag）曲线。
    旗特征：w~5pt、h~13.7pt（约2个弦间距）、pts=12 的曲线，
    紧贴符干末端（x0 ≈ 符干 x，bottom ≈ 符干 bottom）。
    单个旗 → 八分音符；两个旗 → 十六分音符。
    返回：dict，key=group_idx，value=[{x, flag_count}, ...]
             x 为对应符干的 x 坐标
    """
    flags_by_group = defaultdict(list)
    group_params = [(sg, _sg_gap(sg)) for sg in string_groups]

    for cv in page.curves:
        w = cv['x1'] - cv['x0']
        h = cv['bottom'] - cv['top']
        pts = len(cv['pts'])

        # 旗的特征：pts=12，宽度 2~8pt，高度 8~20pt
        if pts != 12:
            continue
        if not (2.0 < w < 10.0):
            continue
        if not (6.0 < h < 22.0):
            continue

        cv_top = cv['top']
        cv_x0 = cv['x0']

        for g_idx, (sg, gap) in enumerate(group_params):
            group_bottom = sg[-1]
            # 旗位于弦线组下方（符干末端区域）
            if not (group_bottom - gap < cv_top < group_bottom + gap * 8.0):
                continue
            flags_by_group[g_idx].append({
                'x': cv_x0,
                'top': cv_top,
                'bottom': cv['bottom'],
                'width': w,
                'height': h,
            })
            break

    return flags_by_group


# ─────────────────────────────────────────────
# 附点识别（圆点曲线 + 斜线）
# ─────────────────────────────────────────────
def find_dots(page, string_groups):
    """
    识别附点。
    附点有两种形式：
    1. 圆点曲线：w≈h≈1.7pt、pts=6，位于符干旁边弦线组下方
    2. 斜线段（[D] 类型）：两个方向都有分量的短线
    返回：dict，key=group_idx，value=[x 坐标列表]
    """
    dots_by_group = defaultdict(list)
    page_height = page.height
    group_params = [(sg, _sg_gap(sg)) for sg in string_groups]

    # 1. 圆点曲线附点
    for cv in page.curves:
        w = cv['x1'] - cv['x0']
        h = cv['bottom'] - cv['top']
        pts = len(cv['pts'])

        # 圆点特征：pts=6，w≈h≈1.7pt（约 0.25 个弦间距）
        if pts != 6:
            continue
        if not (0.8 < w < 3.5 and 0.8 < h < 3.5):
            continue

        cv_top = cv['top']
        x = (cv['x0'] + cv['x1']) / 2

        for g_idx, (sg, gap) in enumerate(group_params):
            group_bottom = sg[-1]
            # 附点位于弦线组下方（符干末端附近）
            if group_bottom - gap * 0.5 <= cv_top <= group_bottom + gap * 4.0:
                dots_by_group[g_idx].append(x)
                break

    # 2. 斜线段附点（兼容旧格式）
    for l in page.lines:
        dx = abs(l['x1'] - l['x0'])
        dy = abs(l['y1'] - l['y0'])
        length = (dx**2 + dy**2) ** 0.5

        if dx < 0.3 or dy < 0.3:
            continue

        top = page_height - max(l['y0'], l['y1'])
        x = (l['x0'] + l['x1']) / 2

        for g_idx, (sg, gap) in enumerate(group_params):
            if not (gap * 0.2 < length < gap * 1.5):
                continue
            if sg[0] - gap <= top <= sg[-1] + gap * 2.0:
                dots_by_group[g_idx].append(x)
                break

    return dots_by_group


# ─────────────────────────────────────────────
# 时值推断：给每个符干分配时值
# ─────────────────────────────────────────────
def assign_durations(stems, beams, flags, dots, gap, x_tolerance=6.0):
    """
    给每个符干分配时值。

    优先级：
    1. 有符梁覆盖 → 按符梁数量查表
    2. 无符梁，有旗（flag）→ 八分/十六分
    3. 无符梁，无旗 → 按 h/gap 比值判断（四分/附点四分/二分等）
    4. 附点圆点 → 时值 × 1.5

    返回：[{x, duration, beam_count, flag_count, dotted, duration_name}, ...]
    """
    result = []
    dot_xs = list(dots)

    # ── 合并同一 x 位置的重复符干（和弦音：多根弦共用一个时值）──
    # 同一拍可能有多个符干（高度不同），取最长的那个作为代表
    merged_stems = {}
    for stem in stems:
        sx = stem['x']
        # 找已有的最近符干（x 差 < x_tolerance）
        found_key = None
        for key in merged_stems:
            if abs(key - sx) < x_tolerance:
                found_key = key
                break
        if found_key is None:
            merged_stems[sx] = stem
        else:
            # 保留高度更大的符干
            if stem['height'] > merged_stems[found_key]['height']:
                merged_stems[found_key] = stem
    stems = sorted(merged_stems.values(), key=lambda s: s['x'])

    for stem in stems:
        sx = stem['x']
        sh = stem['height']
        ratio = sh / gap if gap > 0 else 0

        # ── 1. 统计覆盖该符干的符梁数量 ──
        beam_count = sum(
            1 for beam in beams
            if beam['x0'] - x_tolerance <= sx <= beam['x1'] + x_tolerance
        )

        # ── 2. 统计该符干旁边的旗数量 ──
        flag_count = sum(
            1 for flag in flags
            if abs(flag['x'] - sx) < x_tolerance
        )

        # ── 3. 判断基础时值 ──
        if beam_count > 0:
            # 有符梁：按符梁数量
            duration = BEAM_COUNT_TO_DURATION.get(beam_count, 0.5)
        elif flag_count > 0:
            # 有旗：1旗=八分，2旗=十六分
            if flag_count >= 2:
                duration = 0.25
            else:
                duration = 0.5
        else:
            # 无符梁无旗：按 h/gap 比值
            duration = _duration_from_ratio(ratio)

        # ── 4. 检查附点 ──
        # 附点匹配范围：符干 x 右侧 gap*1.5 以内（约 9.6pt）
        # 圆点附点通常紧贴符干右侧（约 2~4pt），斜线附点稍远但不超过 1.5 个弦间距
        dot_range = gap * 1.5
        dotted = any(0 <= dx - sx < dot_range for dx in dot_xs)

        # h/gap ~2.8 且无旗无符梁时，可能是附点四分（附点圆点在旁边）
        # 如果 ratio 在 2.3~3.3 且有附点，确认为附点四分
        if beam_count == 0 and flag_count == 0 and 2.3 < ratio < 3.3:
            if dotted:
                duration = 1.5  # 附点四分
            else:
                # ratio ~2.8 无附点：可能是八分音符（旗被漏识别），保守判为四分
                duration = 1.0

        if dotted and duration not in (1.5, 0.75, 0.375, 0.1875, 3.0):
            duration = round(duration * 1.5, 6)

        duration = round(duration, 6)

        result.append({
            'x': sx,
            'duration': duration,
            'beam_count': beam_count,
            'flag_count': flag_count,
            'dotted': dotted,
            'ratio': round(ratio, 2),
            'duration_name': DURATION_NAMES.get(duration, f'?{duration:.4f}'),
        })

    result.sort(key=lambda n: n['x'])
    return result


def _duration_from_ratio(ratio):
    """根据 h/gap 比值推断无符梁符干的时值"""
    for min_r, max_r, dur in _NO_BEAM_RATIO_TABLE:
        if min_r <= ratio < max_r:
            if dur is None:
                return 1.0  # 默认四分
            return dur
    return 1.0  # 默认四分


# ─────────────────────────────────────────────
# 主函数：解析单页的节奏信息
# ─────────────────────────────────────────────
def parse_rhythm_page(page, string_groups):
    """
    解析单页的节奏信息。
    返回：
      barlines_by_group: {group_idx: [x, ...]}
      durations_by_group: {group_idx: [{x, duration, dotted, duration_name}, ...]}
    """
    barlines = find_barlines(page, string_groups)
    stems = find_stems(page, string_groups)
    beams = find_beams(page, string_groups)
    flags = find_flags(page, string_groups)
    dots = find_dots(page, string_groups)

    durations_by_group = {}
    for g_idx, sg in enumerate(string_groups):
        gap = _sg_gap(sg)
        group_stems = stems.get(g_idx, [])
        group_beams = beams.get(g_idx, [])
        group_flags = flags.get(g_idx, [])
        group_dots = dots.get(g_idx, [])
        durations_by_group[g_idx] = assign_durations(
            group_stems, group_beams, group_flags, group_dots, gap
        )

    return barlines, durations_by_group


# ─────────────────────────────────────────────
# 调试：打印节奏分析结果
# ─────────────────────────────────────────────
def debug_rhythm(pdf_path, page_idx=0, group_idx=0):
    """打印指定页、指定行的节奏分析结果"""
    from tab_parser import find_string_lines

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_idx]
        string_groups = find_string_lines(page.lines, page.height)

        print(f"第{page_idx+1}页，第{group_idx+1}行六线谱")
        print(f"弦线 top 坐标: {[f'{y:.1f}' for y in string_groups[group_idx]]}")

        barlines, durations_by_group = parse_rhythm_page(page, string_groups)

        print(f"\n小节线 x 坐标: {[f'{x:.1f}' for x in barlines.get(group_idx, [])]}")

        durations = durations_by_group.get(group_idx, [])
        print(f"\n符干时值（共 {len(durations)} 个）:")
        for d in durations:
            print(f"  x={d['x']:.1f}  {d['duration_name']:10s}  "
                  f"(梁={d['beam_count']}, 旗={d['flag_count']}, "
                  f"附点={d['dotted']}, h/gap={d['ratio']:.2f})")


if __name__ == '__main__':
    pdf_path = r'C:\Users\王诗语\Documents\Tencent Files\931865382\FileRecv\MobileFile\Immature（指弹改编）.pdf'
    debug_rhythm(pdf_path, page_idx=0, group_idx=0)
    print()
    debug_rhythm(pdf_path, page_idx=0, group_idx=1)
