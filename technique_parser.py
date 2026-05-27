"""
吉他技法符号解析模块

识别策略：不依赖固定字号/字体名/线宽，改用以下自适应方法：

  H / P（击弦/勾弦字母标记）
    - 文字内容为 'H' 或 'P'（大写）
    - 字号比品位数字小（品位数字通常是页面最大的 Arial 字体）
    - 位于弦线组上方或下方的"标注区"内（不在弦线之间）

  sl. / s（滑弦文字标记）
    - 文字内容为 's' 开头，后跟 'l'（可选 '.'），同行紧邻
    - 字号比品位数字小
    - 位于弦线组标注区内

  装饰音弧线（grace note）
    - 曲线，x_span 和 y_span 都很小（相对于弦线组间距）
    - 宽高比接近 1:2~1:3（细长弧）
    - 位于弦线组上方标注区

  勾弦/击弦连接弧线（slur/tie）
    - 曲线，x_span 比装饰音大（跨越两个音符之间）
    - y_span 相对较大
    - 位于弦线组上方或下方

所有尺寸阈值均以"弦线组间距（string_gap）"为基准动态计算，
而非写死的 pt 数值。
"""

from collections import defaultdict


# ─────────────────────────────────────────────
# 辅助：弦线组几何参数
# ─────────────────────────────────────────────
def _group_metrics(string_group):
    """
    从弦线组坐标计算几何参数。
    返回：
      group_top    最高弦 top 坐标
      group_bottom 最低弦 top 坐标
      string_gap   相邻弦间距（平均值）
      group_height 整组高度
    """
    group_top = string_group[0]
    group_bottom = string_group[-1]
    group_height = group_bottom - group_top
    string_gap = group_height / 5.0 if group_height > 0 else 6.0
    return group_top, group_bottom, string_gap, group_height


def _in_annotation_zone(top, group_top, group_bottom, string_gap):
    """
    判断 top 坐标是否在弦线组的"标注区"内。
    标注区 = 弦线组上方 4×gap 到 弦线组下方 2×gap 之间。
    （技法符号通常写在弦线组正上方或正下方，不会太远）
    """
    above_margin = string_gap * 4.0
    below_margin = string_gap * 2.0
    return (group_top - above_margin) <= top <= (group_bottom + below_margin)


# ─────────────────────────────────────────────
# 辅助：找最近的 cluster
# ─────────────────────────────────────────────
def _nearest_cluster(x, cluster_centers, x_tolerance):
    """返回距离 x 最近的 cluster 索引，超出 tolerance 返回 None"""
    best_ci, best_dist = None, float('inf')
    for ci, cx in enumerate(cluster_centers):
        dist = abs(cx - x)
        if dist < best_dist and dist < x_tolerance:
            best_dist = dist
            best_ci = ci
    return best_ci


# ─────────────────────────────────────────────
# 辅助：推断品位数字的字号（作为"大字号"基准）
# ─────────────────────────────────────────────
def _fret_font_size(page):
    """
    推断页面中品位数字的字号。
    品位数字是页面中数量最多的数字字符，取其众数字号。
    """
    from collections import Counter
    sizes = Counter()
    for c in page.chars:
        if c['text'].isdigit():
            sizes[round(c['size'], 1)] += 1
    if not sizes:
        return 8.0  # 默认回退值
    return sizes.most_common(1)[0][0]


# ─────────────────────────────────────────────
# 击弦识别（字符 'H'）
# ─────────────────────────────────────────────
def find_hammer_ons(page, string_group, cluster_centers, fret_size=None):
    """
    识别击弦标记（字符 'H'）。

    判断条件（不依赖字体名/固定字号）：
      1. 文字内容为大写 'H'
      2. 字号 < 品位数字字号（技法标注字号通常更小）
      3. 位于弦线组标注区内
    """
    if fret_size is None:
        fret_size = _fret_font_size(page)

    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    x_tolerance = string_gap * 2.0
    result = set()

    for c in page.chars:
        if c['text'] != 'H':
            continue
        # 字号应小于品位数字（技法标注比品位数字小）
        if c['size'] >= fret_size:
            continue
        # 字号不能太小（排除页码、水印等）
        if c['size'] < fret_size * 0.3:
            continue
        if not _in_annotation_zone(c['top'], group_top, group_bottom, string_gap):
            continue
        ci = _nearest_cluster(c['x0'], cluster_centers, x_tolerance)
        if ci is not None:
            result.add(ci)
    return result


# ─────────────────────────────────────────────
# 勾弦识别（字符 'P' 或 'p'）
# ─────────────────────────────────────────────
def find_hammer_pull_chars(page, string_group, cluster_centers, fret_size=None):
    """
    识别勾弦字母标记（字符 'P' 或 'p'）。
    部分软件用字母 P 标注 pull-off，与弧线方式互补。

    判断条件同击弦 H。
    """
    if fret_size is None:
        fret_size = _fret_font_size(page)

    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    x_tolerance = string_gap * 2.0
    result = set()

    for c in page.chars:
        if c['text'] not in ('P', 'p'):
            continue
        if c['size'] >= fret_size:
            continue
        if c['size'] < fret_size * 0.3:
            continue
        if not _in_annotation_zone(c['top'], group_top, group_bottom, string_gap):
            continue
        ci = _nearest_cluster(c['x0'], cluster_centers, x_tolerance)
        if ci is not None:
            result.add(ci)
    return result


# ─────────────────────────────────────────────
# 滑弦识别（文字 's' 开头，后跟 'l'）
# ─────────────────────────────────────────────
def find_slides(page, string_group, cluster_centers, fret_size=None):
    """
    识别滑弦标记（文字序列以 's' 开头，后跟 'l'，可选 '.'）。

    判断条件（不依赖字体名/固定字号）：
      1. 字符 's'，字号 < 品位数字字号
      2. 同行（top 坐标相近）紧邻右侧有字符 'l'
      3. 位于弦线组标注区内
    """
    if fret_size is None:
        fret_size = _fret_font_size(page)

    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    x_tolerance = string_gap * 2.5

    # 预先按 top 分组，加速查找
    result = set()
    small_chars = [
        c for c in page.chars
        if c['size'] < fret_size and c['size'] >= fret_size * 0.3
        and _in_annotation_zone(c['top'], group_top, group_bottom, string_gap)
    ]

    for s_char in small_chars:
        if s_char['text'] != 's':
            continue
        # 在同行（top 差 < 字号的一半）、右侧紧邻（x 差 < 字号 * 2）找 'l'
        top_tol = s_char['size'] * 0.6
        x_reach = s_char['size'] * 2.5
        nearby = [
            c for c in small_chars
            if (abs(c['top'] - s_char['top']) < top_tol
                and c['x0'] > s_char['x0']
                and c['x0'] < s_char['x0'] + x_reach)
        ]
        texts = ''.join(c['text'] for c in sorted(nearby, key=lambda x: x['x0']))
        if texts.startswith('l'):
            ci = _nearest_cluster(s_char['x0'], cluster_centers, x_tolerance)
            if ci is not None:
                result.add(ci)
    return result


# ─────────────────────────────────────────────
# 曲线分类：装饰音弧线 vs 勾弦/击弦连接弧线
# ─────────────────────────────────────────────
def _classify_curves(page, string_group):
    """
    对页面曲线按几何特征分类，返回：
      grace_curves: 装饰音弧线列表（小弧，x_span < 1×gap，y_span < 3×gap）
      slur_curves:  连接弧线列表（跨音符弧，x_span 在 0.5~6×gap 之间）

    所有尺寸阈值基于 string_gap 动态计算。
    """
    group_top, group_bottom, string_gap, group_height = _group_metrics(string_group)

    grace_curves = []
    slur_curves = []

    for curve in page.curves:
        pts = curve.get('pts', [])
        if len(pts) < 4:
            continue

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_span = max(xs) - min(xs)
        y_span = max(ys) - min(ys)

        # 转换为 top 坐标（pdfplumber y 轴向上，top 向下）
        top = page.height - max(ys)

        if not _in_annotation_zone(top, group_top, group_bottom, string_gap):
            continue

        # 宽高比：y_span / x_span（弧线通常高大于宽）
        aspect = y_span / x_span if x_span > 0.5 else float('inf')

        # ── 装饰音弧线：非常小，宽 < 1.5×gap，高 < 3×gap，高宽比 > 1.5
        if (x_span < string_gap * 1.5
                and y_span < string_gap * 3.0
                and y_span > string_gap * 0.8
                and aspect > 1.2):
            grace_curves.append({
                'x_center': (min(xs) + max(xs)) / 2,
                'x_span': x_span,
                'y_span': y_span,
                'top': top,
            })

        # ── 连接弧线（勾弦/击弦）：跨越两个音符，宽 0.5~8×gap，高 1~6×gap
        elif (string_gap * 0.5 < x_span < string_gap * 8.0
              and string_gap * 1.0 < y_span < string_gap * 6.0):
            slur_curves.append({
                'x_start': min(xs),
                'x_end': max(xs),
                'x_center': (min(xs) + max(xs)) / 2,
                'x_span': x_span,
                'y_span': y_span,
                'top': top,
            })

    return grace_curves, slur_curves


def find_grace_notes(page, string_group, cluster_centers):
    """
    识别装饰音弧线（小弧线，位于音符上方）。
    返回：set of cluster_idx
    """
    _, string_gap, _, _ = (
        string_group[0],
        (string_group[-1] - string_group[0]) / 5.0 if len(string_group) >= 2 else 6.0,
        string_group[-1],
        string_group[-1] - string_group[0],
    )
    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    x_tolerance = string_gap * 2.0

    grace_curves, _ = _classify_curves(page, string_group)
    result = set()
    for curve in grace_curves:
        ci = _nearest_cluster(curve['x_center'], cluster_centers, x_tolerance)
        if ci is not None:
            result.add(ci)
    return result


def find_pull_offs_by_curve(page, string_group, cluster_centers):
    """
    识别勾弦/击弦连接弧线（跨越两个音符的弧线）。
    返回：set of cluster_idx（标记在弧线起始端的音符上）
    """
    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    x_tolerance = string_gap * 2.0

    _, slur_curves = _classify_curves(page, string_group)
    result = set()
    for curve in slur_curves:
        # 标记在弧线起始端（x_start）
        ci = _nearest_cluster(curve['x_start'], cluster_centers, x_tolerance)
        if ci is not None:
            result.add(ci)
    return result


# ─────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────
def collect_techniques(page, string_group, cluster_centers):
    """
    收集一行六线谱中所有技法标记，关联到 cluster 索引。

    返回：dict，key=cluster_idx，value=dict of technique flags
      {
          'hammer_on': bool,
          'pull_off': bool,
          'slide': bool,
          'grace_note': bool,
      }
    """
    fret_size = _fret_font_size(page)

    hammer_ons = find_hammer_ons(page, string_group, cluster_centers, fret_size)
    pull_off_chars = find_hammer_pull_chars(page, string_group, cluster_centers, fret_size)
    slides = find_slides(page, string_group, cluster_centers, fret_size)
    grace_notes = find_grace_notes(page, string_group, cluster_centers)
    pull_off_curves = find_pull_offs_by_curve(page, string_group, cluster_centers)

    # 勾弦：字母 P 或 弧线 均算
    pull_offs = pull_off_chars | pull_off_curves

    all_cis = hammer_ons | pull_offs | slides | grace_notes
    result = {}
    for ci in all_cis:
        result[ci] = {
            'hammer_on': ci in hammer_ons,
            'pull_off': ci in pull_offs,
            'slide': ci in slides,
            'grace_note': ci in grace_notes,
        }
    return result
