"""
节奏/时值解析模块

PDF 中的时值编码方式（通过探查确认）：
  - 小节线：宽 ~0.7pt、高 ~31.9pt 的黑色填充矩形，x 坐标对齐弦线组
  - 符干（stem）：细竖线（linewidth=0.51），位于弦线组下方
      高度 ~11pt  → 八分音符 (1/8)，1条符梁
      高度 ~18pt  → 十六分音符 (1/16)，2条符梁
      高度 ~24pt  → 附点八分音符 or 特殊情况
      高度 ~37pt  → 三十二分音符 (1/32)，3条符梁
      高度 ~43pt  → 六十四分音符 (1/64)，4条符梁
  - 符梁（beam）：宽 15~23pt、高 ~2.1pt 的黑色填充矩形，位于弦线组下方
      每个符干下方的符梁数量决定时值细分
  - 附点：斜线段（[D] 类型），出现在符干旁边
  - 小节编号：size=6 的 TimesNewRoman 数字，位于弦线组上方

时值判断逻辑（优先用符梁数量，符干高度作为辅助）：
  0条符梁 + 符干高度 ~11pt → 四分音符 (1/4)
  1条符梁                   → 八分音符 (1/8)
  2条符梁                   → 十六分音符 (1/16)
  3条符梁                   → 三十二分音符 (1/32)
  4条符梁                   → 六十四分音符 (1/64)
  附点修饰：时值 × 1.5
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
    0: 1.0,     # 四分音符（无符梁）
    1: 0.5,     # 八分音符
    2: 0.25,    # 十六分音符
    3: 0.125,   # 三十二分音符
    4: 0.0625,  # 六十四分音符
}


# ─────────────────────────────────────────────
# 小节线识别
# ─────────────────────────────────────────────
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

        # 小节线必须是黑色（或接近黑色）填充
        if not _is_black(fill):
            continue

        rect_top = r['top']
        for g_idx, sg in enumerate(string_groups):
            gap = _sg_gap(sg)
            group_height = sg[-1] - sg[0]
            margin = gap * 1.0

            # 高度：至少覆盖弦线组高度的 60%
            if h < group_height * 0.6:
                continue
            # 宽度：极窄，不超过 1 个弦间距
            if w > gap:
                continue
            # 位置：矩形顶部在弦线组范围内（含上下各 1 个间距的余量）
            if (sg[0] - margin) <= rect_top <= (sg[-1] + margin):
                barlines_by_group[g_idx].append(r['x0'])
                break

    # 排序，并合并双小节线（间距 < 1 个弦间距的两条线视为同一条）
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
    识别符干（细竖线，位于弦线组下方）。
    所有尺寸阈值基于弦线组间距动态计算。
    返回：dict，key=group_idx，value=[(x, stem_bottom, stem_height), ...]
    """
    stems_by_group = defaultdict(list)

    # 预先计算各组参数
    group_params = [(sg, _sg_gap(sg)) for sg in string_groups]

    for l in page.lines:
        # 竖线：x0 ≈ x1（允许极小偏差）
        if abs(l['x0'] - l['x1']) > 0.8:
            continue

        page_height = page.height
        top = page_height - l['y1']
        bottom = page_height - l['y0']
        h = bottom - top
        if h <= 0:
            continue

        x = l['x0']

        for g_idx, (sg, gap) in enumerate(group_params):
            group_bottom = sg[-1]
            # 符干高度：0.5~8 个弦间距（覆盖四分到六十四分音符）
            if not (gap * 0.5 < h < gap * 8.0):
                continue
            # 符干位置：top 在弦线组范围内或稍下方（1 个间距余量）
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
    所有尺寸阈值基于弦线组间距动态计算。
    返回：dict，key=group_idx，value=[(x0, x1, top), ...]
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
            # 符梁在弦线组下方，范围：0~8 个弦间距
            if not (group_bottom < beam_top < group_bottom + gap * 8.0):
                continue
            # 符梁宽度：至少 1 个弦间距（排除小点/装饰）
            if w < gap:
                continue
            # 符梁高度：0.1~0.8 个弦间距（薄矩形）
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
# 附点识别
# ─────────────────────────────────────────────
def find_dots(page, string_groups):
    """
    识别附点（短斜线段，出现在符干旁边）。
    所有尺寸阈值基于弦线组间距动态计算。
    返回：dict，key=group_idx，value=[x 坐标列表]
    """
    dots_by_group = defaultdict(list)
    page_height = page.height
    group_params = [(sg, _sg_gap(sg)) for sg in string_groups]

    for l in page.lines:
        dx = abs(l['x1'] - l['x0'])
        dy = abs(l['y1'] - l['y0'])
        length = (dx**2 + dy**2) ** 0.5

        # 附点是短斜线：两个方向都有分量（非纯竖/纯横）
        if dx < 0.3 or dy < 0.3:
            continue

        top = page_height - max(l['y0'], l['y1'])
        x = (l['x0'] + l['x1']) / 2

        for g_idx, (sg, gap) in enumerate(group_params):
            # 附点长度：0.2~1.5 个弦间距
            if not (gap * 0.2 < length < gap * 1.5):
                continue
            # 位置：弦线组范围内或稍下方（2 个间距余量）
            if sg[0] - gap <= top <= sg[-1] + gap * 2.0:
                dots_by_group[g_idx].append(x)
                break

    return dots_by_group


# ─────────────────────────────────────────────
# 时值推断：给每个符干分配时值
# ─────────────────────────────────────────────
def assign_durations(stems, beams, dots, x_tolerance=8.0):
    """
    给每个符干分配时值。
    
    策略：
    1. 统计每个符干 x 坐标范围内有多少条符梁覆盖它
    2. 根据符梁数量查表得到时值
    3. 检查附近是否有附点，有则时值 × 1.5
    
    返回：[(x, duration_beats, dotted), ...]
    """
    result = []
    dot_xs = [d for d in dots]
    
    for stem in stems:
        sx = stem['x']
        
        # 统计覆盖该符干的符梁数量
        beam_count = 0
        for beam in beams:
            # 符梁的 x 范围覆盖符干 x
            if beam['x0'] - x_tolerance <= sx <= beam['x1'] + x_tolerance:
                beam_count += 1
        
        # 查表得到时值（以四分音符=1为单位）
        duration = BEAM_COUNT_TO_DURATION.get(beam_count, 1.0)
        
        # 检查附点
        dotted = any(abs(dx - sx) < x_tolerance * 2 for dx in dot_xs)
        if dotted:
            duration *= 1.5
        
        # 四舍五入避免浮点误差
        duration = round(duration, 6)
        
        result.append({
            'x': sx,
            'duration': duration,
            'beam_count': beam_count,
            'dotted': dotted,
            'duration_name': DURATION_NAMES.get(duration, f'?{duration:.4f}'),
        })
    
    result.sort(key=lambda n: n['x'])
    return result


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
    dots = find_dots(page, string_groups)
    
    durations_by_group = {}
    for g_idx, sg in enumerate(string_groups):
        group_stems = stems.get(g_idx, [])
        group_beams = beams.get(g_idx, [])
        group_dots = dots.get(g_idx, [])
        durations_by_group[g_idx] = assign_durations(group_stems, group_beams, group_dots)
    
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
            print(f"  x={d['x']:.1f}  {d['duration_name']}  (梁数={d['beam_count']}, 附点={d['dotted']})")


if __name__ == '__main__':
    pdf_path = r'C:\Users\王诗语\Documents\Tencent Files\931865382\FileRecv\MobileFile\Immature（指弹改编）.pdf'
    debug_rhythm(pdf_path, page_idx=0, group_idx=0)
    print()
    debug_rhythm(pdf_path, page_idx=0, group_idx=1)
