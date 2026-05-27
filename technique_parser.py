"""
吉他技法符号解析模块

识别策略：不依赖固定字号/字体名/线宽，改用以下自适应方法：

  H / P（击弦/勾弦字母标记）
    - 文字内容为 'H' 或 'P'（大写）
    - 字号比品位数字小（品位数字通常是页面最大的 Arial 字体）
    - 位于弦线组上方或下方的"标注区"内（不在弦线之间）

  sl.（滑弦文字标记）
    - 文字内容为 's' 开头，后跟 'l'（可选 '.'），同行紧邻
    - 字号比品位数字小
    - 位于弦线组标注区内
    - 用 '.' 字符的 x 坐标（最右端）定位，关联到右侧最近的 cluster

  装饰音弧线（grace note arc）
    - 曲线，x_span 和 y_span 都很小（相对于弦线组间距）
    - 宽高比接近 1:2~1:3（细长弧）
    - 必须在弦线组上方（top < group_top）

  装饰音品位数字（grace note fret）
    - size=5 的 Arial 数字，比品位数字（size=7）小
    - 位于弦线组标注区内，x 坐标比对应正式音符偏左约 1 个 gap
    - 关联到右侧最近的 cluster（正式音符）
    - 返回 {cluster_idx: {string_idx: fret_str}} 的映射

  连音线（tie）
    - pts=4, y_span≈0 的水平短线段群，拼成虚线
    - 起始端：连音线 x_min 左侧最近的 cluster（连音线从音符右边开始画）
    - 终止端：连音线 x_max 右侧最近的 cluster（连音线到音符左边结束）
    - 容差放宽到 4×gap，覆盖连音线端点与音符之间的间距

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


def _cluster_left_of(x, cluster_centers, x_tolerance):
    """
    找 x 左侧（cx <= x + margin）最近的 cluster。
    用于连音线起始端：连音线从音符右边开始，x_min 在音符右侧。
    """
    best_ci, best_dist = None, float('inf')
    for ci, cx in enumerate(cluster_centers):
        if cx <= x + x_tolerance:
            dist = x - cx
            if dist >= 0 and dist < best_dist and dist < x_tolerance:
                best_dist = dist
                best_ci = ci
    return best_ci


def _cluster_right_of(x, cluster_centers, x_tolerance):
    """
    找 x 右侧（cx >= x - margin）最近的 cluster。
    用于连音线终止端：连音线到音符左边结束，x_max 在音符左侧。
    """
    best_ci, best_dist = None, float('inf')
    for ci, cx in enumerate(cluster_centers):
        if cx >= x - x_tolerance:
            dist = cx - x
            if dist >= 0 and dist < best_dist and dist < x_tolerance:
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
# 滑弦识别（文字 'sl.' + 斜线 + 小字号起始音符）
# ─────────────────────────────────────────────
def _find_slide_lines(page, string_group):
    """
    找页面中属于本弦线组的滑弦斜线。

    Guitar Pro 导出的 TAB 滑弦线特征：
      - 是一条斜线（dx > 0 且 dy > 0）
      - 宽度（width）≈ dx（线宽等于水平跨度，说明是斜线而非水平线）
      - 位于弦线组范围内（top 在弦线组 top±2×gap 之间）
      - x_span 很小（约 2~5pt），是短斜线

    返回：list of {'x0', 'x1', 'top', 'string_idx'}
      top 是斜线的 top 坐标，string_idx 是最近弦线的索引（0-based）
    """
    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    # 滑弦线在弦线组范围内（允许少量超出）
    zone_top    = group_top    - string_gap * 0.5
    zone_bottom = group_bottom + string_gap * 0.5

    result = []
    for line in page.lines:
        x0, x1 = line['x0'], line['x1']
        y0, y1 = line['y0'], line['y1']
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        # 斜线：x 和 y 都有变化
        if dx < 0.5 or dy < 0.5:
            continue
        # 宽度约等于 dx（斜线的 width 属性 = 线段长度）
        w = line.get('width', 0)
        if w < dx * 0.5:
            continue
        # pdfplumber 提供的 top 字段（从页面顶部向下）
        # 斜线的 top 取两端中较小的（更靠近页面顶部的那端）
        top = line.get('top', page.height - max(y0, y1))
        if not (zone_top <= top <= zone_bottom):
            continue
        # 找最近的弦线
        best_si, best_dist = None, float('inf')
        for si, sy in enumerate(string_group):
            d = abs(sy - top)
            if d < best_dist:
                best_dist = d
                best_si = si
        if best_si is None or best_dist > string_gap * 1.0:
            continue
        result.append({
            'x0': min(x0, x1),
            'x1': max(x0, x1),
            'top': top,
            'string_idx': best_si,
        })
    return result



def find_slides(page, string_group, cluster_centers, positions=None, fret_size=None):
    """
    识别滑弦标记，基于两个视觉特征精确定位：
      1. 'sl.' 文字（TimesNewRoman size≈6，在弦线组上方标注区）
      2. 滑弦斜线（短斜线，dx≈2~5pt，位于弦线上，top 对应具体弦线）

    定位策略：
      - 找 'sl.' 标记，在其附近（x 范围内）找斜线
      - 斜线的 top 坐标对应哪根弦线 → 就是滑弦所在弦（string_idx）
      - 起始音符：斜线左端 x 左侧最近的 cluster（collect_notes 已收集小字号起始音符）
      - 目标音符：斜线右端 x 右侧最近的 cluster
      - 若找不到斜线，回退到用 start/stop cluster 的公共弦确定弦号

    返回：
      slide_starts: {ci_start: string_idx}  起始音符 cluster → 弦索引
      slide_stops:  {ci_stop:  string_idx}  目标音符 cluster → 弦索引
    """
    if fret_size is None:
        fret_size = _fret_font_size(page)

    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    x_tolerance = string_gap * 4.0

    slide_starts = {}  # ci -> string_idx
    slide_stops  = {}  # ci -> string_idx

    # 预先找本行所有滑弦斜线
    slide_lines = _find_slide_lines(page, string_group)

    # 收集标注区内的 sl. 候选字符
    # sl. 使用斜体 TimesNewRoman（fontname 含 '-1-' 或 'Italic'），
    # 而 let ring 等文字方向标记使用正体（fontname 含 '-0-'），需排除
    def _is_italic_font(c):
        fn = c.get('fontname', '')
        return '-1-' in fn or 'Italic' in fn or 'italic' in fn

    small_chars = [
        c for c in page.chars
        if c['size'] < fret_size and c['size'] >= fret_size * 0.3
        and _in_annotation_zone(c['top'], group_top, group_bottom, string_gap)
        and _is_italic_font(c)
    ]

    for s_char in small_chars:
        if s_char['text'] != 's':
            continue
        top_tol = s_char['size'] * 0.6
        x_reach = s_char['size'] * 3.0
        nearby = [
            c for c in small_chars
            if (abs(c['top'] - s_char['top']) < top_tol
                and c['x0'] > s_char['x0']
                and c['x0'] < s_char['x0'] + x_reach)
        ]
        nearby_sorted = sorted(nearby, key=lambda x: x['x0'])
        texts = ''.join(c['text'] for c in nearby_sorted)
        if not texts.startswith('l'):
            continue

        sl_x     = s_char['x0']
        anchor_x = nearby_sorted[-1]['x0']  # '.' 或 'l' 的最右端 x

        # ── 找 sl. 附近的滑弦斜线，从斜线确定弦号 ──
        nearby_slines = [
            sl for sl in slide_lines
            if sl['x0'] >= sl_x - string_gap * 2
            and sl['x0'] <= anchor_x + string_gap * 2
        ]

        string_idx = None
        sl_line_x0 = None
        sl_line_x1 = None

        if nearby_slines:
            best_sl = min(nearby_slines,
                          key=lambda sl: abs((sl['x0'] + sl['x1']) / 2 - sl_x))
            string_idx = best_sl['string_idx']
            sl_line_x0 = best_sl['x0']
            sl_line_x1 = best_sl['x1']

        # ── 确定起始 cluster（斜线左端左侧最近的 cluster）──
        # collect_notes 已经收集了小字号起始音符，所以这里能找到对应 cluster
        start_anchor = sl_line_x0 if sl_line_x0 is not None else sl_x
        ci_start = _cluster_left_of(start_anchor, cluster_centers, x_tolerance)

        # ── 确定目标 cluster（斜线右端右侧最近的 cluster）──
        stop_anchor = sl_line_x1 if sl_line_x1 is not None else anchor_x
        ci_stop = _cluster_right_of(stop_anchor, cluster_centers, x_tolerance)

        if ci_start is None or ci_stop is None:
            continue

        # ── 回退：若斜线未找到，用 start/stop cluster 的公共弦 ──
        if string_idx is None and positions is not None:
            start_strings = set(k for k in positions[ci_start].keys()
                                if k != '_tied')
            stop_strings  = set(k for k in positions[ci_stop].keys()
                                if k != '_tied')
            common = start_strings & stop_strings
            if common:
                string_idx = min(common)
            elif start_strings:
                string_idx = min(start_strings)
            elif stop_strings:
                string_idx = min(stop_strings)

        slide_starts[ci_start] = string_idx
        slide_stops[ci_stop]   = string_idx

    return slide_starts, slide_stops


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
        n_pts = len(pts)
        if n_pts < 4:
            continue

        # 排除旗（flag）曲线：pts=12，是节奏符号不是技法弧线
        if n_pts == 12:
            continue
        # 排除连音线段：pts=4 的短曲线（连音线由多段小弧拼成）
        # 条件：y_span 极小（近水平）或 x_span 极小（< 0.4×gap，太窄不是装饰音弧）
        if n_pts == 4:
            ys_check = [p[1] for p in pts]
            xs_check = [p[0] for p in pts]
            y_span_check = max(ys_check) - min(ys_check)
            x_span_check = max(xs_check) - min(xs_check)
            if y_span_check < 1.0 or x_span_check < string_gap * 0.4:
                continue

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_span = max(xs) - min(xs)
        y_span = max(ys) - min(ys)
        x_min = min(xs)

        # 排除行首谱号区域（x < 50pt）
        if x_min < 50.0:
            continue

        # 转换为 top 坐标（pdfplumber y 轴向上，top 向下）
        top = page.height - max(ys)

        if not _in_annotation_zone(top, group_top, group_bottom, string_gap):
            continue

        # 宽高比：y_span / x_span（弧线通常高大于宽）
        aspect = y_span / x_span if x_span > 0.5 else float('inf')

        # ── 装饰音弧线：非常小，宽 < 1.5×gap，高 < 3×gap，高宽比 > 1.2
        # pts 范围：装饰音弧线通常是 5~13 个控制点的贝塞尔曲线
        # 排除颤音波浪线段（pts=15/16，多段堆叠拼成波浪）
        # 装饰音弧线必须在弦线组上方（top < group_top），不能在弦线组内部或下方
        if (x_span < string_gap * 1.5
                and y_span < string_gap * 3.0
                and y_span > string_gap * 0.8
                and aspect > 1.2
                and 5 <= n_pts <= 13
                and top < group_top):
            grace_curves.append({
                'x_center': (min(xs) + max(xs)) / 2,
                'x_span': x_span,
                'y_span': y_span,
                'top': top,
            })

        # ── 连接弧线（勾弦/击弦）：跨越两个音符，宽 0.5~8×gap，高 1~6×gap
        # pts 范围：连接弧线通常是 7~13 个控制点，排除颤音波浪线端点段（pts=14/15）
        elif (string_gap * 0.5 < x_span < string_gap * 8.0
              and string_gap * 1.0 < y_span < string_gap * 6.0
              and 5 <= n_pts <= 13):
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
# 连音线识别（Tie / Slur）
# ─────────────────────────────────────────────
def find_ties(page, string_group, cluster_centers):
    """暂时禁用，避免误识别。"""
    return {}


def _find_ties_disabled(page, string_group, cluster_centers):
    """
    识别连音线（水平短线段群，位于弦线组上方或下方）。

    Guitar Pro 导出的 TAB 连音线由多段水平短线段（pts=4, y_span≈0）拼成虚线。
    连音线的画法：从起始音符右边开始，到终止音符左边结束。
    因此：
      - 起始端（x_min）在起始音符的右侧，用 _cluster_left_of 找左侧最近的 cluster
      - 终止端（x_max）在终止音符的左侧，用 _cluster_right_of 找右侧最近的 cluster

    容差放宽到 4×gap，覆盖连音线端点与音符之间的间距（实测约 13~20pt ≈ 2~3×gap）。

    返回 dict：{cluster_idx: 'start' | 'stop' | 'start_stop'}
    """
    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    # 连音线端点到 cluster 的距离可达 3~4×gap，放宽容差
    x_tolerance = string_gap * 4.0

    # 标注区：弦线组上方 5×gap 到 弦线组下方 3×gap
    zone_top = group_top - string_gap * 5.0
    zone_bottom = group_bottom + string_gap * 3.0

    # 收集水平短线段
    flat_segs = []
    for curve in page.curves:
        pts = curve.get('pts', [])
        if len(pts) != 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        y_span = max(ys) - min(ys)
        x_span = max(xs) - min(xs)
        top = page.height - max(ys)

        # 必须是水平线段（y_span 极小）且有一定宽度
        if y_span > 0.5:
            continue
        if x_span < string_gap * 0.5:
            continue
        if not (zone_top <= top <= zone_bottom):
            continue

        flat_segs.append({
            'x0': min(xs),
            'x1': max(xs),
            'top': top,
        })

    if not flat_segs:
        return {}

    # ── 分组策略：先按 top 分组，再在同一 top 组内按 x 连续性拆分 ──
    # 同一条连音线的多段：top 相近 AND x 坐标连续（相邻段间距 < 3×gap）
    # 不同位置的独立短弧：top 相近但 x 不连续，必须拆开独立处理

    # Step 1：按 top 粗分组
    top_groups = defaultdict(list)
    for seg in sorted(flat_segs, key=lambda s: s['top']):
        placed = False
        for key in list(top_groups.keys()):
            if abs(key - seg['top']) < string_gap * 0.5:
                top_groups[key].append(seg)
                placed = True
                break
        if not placed:
            top_groups[seg['top']].append(seg)

    # Step 2：在每个 top 组内，按 x 连续性再拆分成独立的连音线段
    tie_chains = []  # 每个元素是一组 x 连续的线段列表
    for top_key, segs in top_groups.items():
        segs_sorted = sorted(segs, key=lambda s: s['x0'])
        chain = [segs_sorted[0]]
        for seg in segs_sorted[1:]:
            # 与当前链末尾的间距
            gap_x = seg['x0'] - chain[-1]['x1']
            if gap_x <= string_gap * 3.0:  # 连续：合并到同一链
                chain.append(seg)
            else:  # 不连续：结束当前链，开新链
                tie_chains.append(chain)
                chain = [seg]
        tie_chains.append(chain)

    result = {}  # cluster_idx -> 'start' | 'stop' | 'start_stop'

    for chain in tie_chains:
        x_min = min(s['x0'] for s in chain)
        x_max = max(s['x1'] for s in chain)
        x_span_total = x_max - x_min

        # 连音线至少要跨越 1 个弦间距
        if x_span_total < string_gap:
            continue

        # 起始端：x_min 左侧最近的 cluster（连音线从音符右边开始）
        ci_start = _cluster_left_of(x_min, cluster_centers, x_tolerance)
        # 终止端：x_max 右侧最近的 cluster（连音线到音符左边结束）
        ci_stop = _cluster_right_of(x_max, cluster_centers, x_tolerance)

        if ci_start is not None:
            if ci_start in result:
                if result[ci_start] == 'stop':
                    result[ci_start] = 'start_stop'
            else:
                result[ci_start] = 'start'

        if ci_stop is not None and ci_stop != ci_start:
            if ci_stop in result:
                if result[ci_stop] == 'start':
                    result[ci_stop] = 'start_stop'
            else:
                result[ci_stop] = 'stop'

    return result


# ─────────────────────────────────────────────
# 装饰音品位数字识别（Grace Note Fret）
# ─────────────────────────────────────────────
def find_grace_frets(page, string_group, cluster_centers, fret_size=None):
    """
    识别装饰音的品位数字（比正常品位数字更小的 Arial 数字）。

    Guitar Pro 导出的 TAB 装饰音品位数字特征：
      - size=5（比正常品位数字 size=7 小），Arial 字体
      - 位于弦线组标注区内，x 坐标比对应正式音符偏左约 1 个 gap
      - 上下两个数字对应两根弦（或单个数字对应一根弦）
      - 关联到右侧最近的 cluster（正式音符）

    返回：dict，key=cluster_idx，value=dict {string_idx: fret_str}
      表示该 cluster 左侧有装饰音，装饰音的品位信息按弦索引存储。
    """
    if fret_size is None:
        fret_size = _fret_font_size(page)

    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)

    # 装饰音数字字号范围：比品位数字小（< 85%），但不能太小（> 30%）
    grace_size_max = fret_size * 0.85
    grace_size_min = fret_size * 0.30

    # 标注区
    zone_top = group_top - string_gap * 4.0
    zone_bottom = group_bottom + string_gap * 2.0

    # 容差：装饰音数字 x 坐标比对应 cluster 偏左约 1~2 个 gap
    x_tolerance = string_gap * 3.0

    # 收集候选装饰音数字
    candidates = []
    for c in page.chars:
        if not c['text'].isdigit():
            continue
        if 'Arial' not in c.get('fontname', ''):
            continue
        if not (grace_size_min <= c['size'] <= grace_size_max):
            continue
        if not (zone_top <= c['top'] <= zone_bottom):
            continue
        candidates.append(c)

    if not candidates:
        return {}

    # 将候选数字关联到右侧最近的 cluster
    # 同时需要确定该数字对应哪根弦（通过 top 坐标与弦线 top 的距离）
    result = {}  # cluster_idx -> {string_idx: fret_str}

    for c in candidates:
        # 找右侧最近的 cluster
        ci = _cluster_right_of(c['x0'], cluster_centers, x_tolerance)
        if ci is None:
            continue

        # 确定对应的弦索引（找 top 最近的弦）
        best_si, best_dist = None, float('inf')
        for si, string_top in enumerate(string_group):
            dist = abs(c['top'] - string_top)
            if dist < best_dist:
                best_dist = dist
                best_si = si

        # 弦匹配容差：2 个弦间距
        if best_dist > string_gap * 2.0:
            continue

        if ci not in result:
            result[ci] = {}
        result[ci][best_si] = c['text']

    return result


# ─────────────────────────────────────────────
# 连音组识别（Tuplet）
# ─────────────────────────────────────────────
def find_tuplets(page, string_group, cluster_centers):
    """
    识别连音组标记（三连音、五连音等）。

    Guitar Pro 导出的 TAB 连音组标记由两层数字组成：
      - 上层（size≈7, Arial）：连音组内的音符数（如 3、5）
      - 下层（size≈5, Arial）：连音组的分母（如 2、4），紧贴在上层数字下方

    识别策略：
      1. 找到弦线组上方标注区内的 Arial 小字（size < 品位字号 * 0.75）
      2. 按 x 坐标聚类，找到上下配对的数字
      3. 上层数字（较大）= actual_notes，下层数字（较小）= normal_notes
      4. 找到该 x 坐标附近的 cluster，标记连音组起始

    返回：dict，key=cluster_idx，value={'actual': int, 'normal': int}
      actual: 连音组内实际音符数（如三连音=3）
      normal: 正常时值内的音符数（如三连音=2）
    """
    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)
    fret_size = _fret_font_size(page)

    # 连音组数字的字号阈值：比品位数字小（< 75%），但不能太小（> 30%）
    tuplet_size_max = fret_size * 0.75
    tuplet_size_min = fret_size * 0.30

    # 标注区：弦线组上方 6×gap 到 弦线组上方 1×gap（不含弦线组边缘）
    # 收紧 zone_bottom：小节编号紧贴 group_top 上方，连音组数字在更上方
    zone_top = group_top - string_gap * 6.0
    zone_bottom = group_top - string_gap * 0.3  # 比原来更严格，排除紧贴弦线的小节编号

    # 收集候选字符
    candidates = []
    for c in page.chars:
        if not (c['text'].isdigit() and 'Arial' in c.get('fontname', '')):
            continue
        if not (tuplet_size_min <= c['size'] <= tuplet_size_max):
            continue
        if not (zone_top <= c['top'] <= zone_bottom):
            continue
        candidates.append(c)

    if not candidates:
        return {}

    # 按 x 坐标聚类（同一连音组的上下两个数字 x 坐标相近）
    x_tolerance = string_gap * 1.5
    x_groups = defaultdict(list)
    for c in sorted(candidates, key=lambda x: x['x0']):
        placed = False
        for key in list(x_groups.keys()):
            if abs(key - c['x0']) < x_tolerance:
                x_groups[key].append(c)
                placed = True
                break
        if not placed:
            x_groups[c['x0']].append(c)

    result = {}
    for x_key, chars in x_groups.items():
        if not chars:
            continue

        # 按字号分组：较大的是 actual_notes，较小的是 normal_notes
        chars_sorted_by_size = sorted(chars, key=lambda c: c['size'], reverse=True)
        larger = chars_sorted_by_size[0]
        smaller = chars_sorted_by_size[-1] if len(chars_sorted_by_size) > 1 else None

        try:
            actual = int(larger['text'])
        except ValueError:
            continue

        # 连音组必须有上下两个数字配对（actual + normal）
        # 单独一个数字（如小节编号）不算连音组，直接跳过
        normal = None
        if smaller and smaller['size'] < larger['size'] * 0.85:
            try:
                normal = int(smaller['text'])
            except ValueError:
                pass

        if normal is None:
            # 没有配对的 normal 数字 → 不是连音组，跳过
            continue

        # 找最近的 cluster
        ci = _nearest_cluster(x_key, cluster_centers, x_tolerance * 2)
        if ci is not None:
            result[ci] = {'actual': actual, 'normal': normal}

    return result


# ─────────────────────────────────────────────
# 装饰音品位数字识别（Grace Note Fret）
# ─────────────────────────────────────────────
def find_grace_note_frets(page, string_group, cluster_centers, fret_size=None):
    """
    识别装饰音品位数字（比正常品位数字更小的 Arial 数字）。

    Guitar Pro 导出的装饰音在六线谱上表现为：
      - 一个比正常品位数字更小的数字（size ≈ fret_size * 0.7）
      - 位于正常音符的左侧，x 坐标比对应 cluster 偏左约 1~2×gap
      - 可能有多个（不同弦上各有一个小数字，x 坐标相同）

    识别策略：
      1. 找 size 在 [fret_size*0.5, fret_size*0.85) 之间的 Arial 数字
      2. 位于弦线组范围内（top 在弦线组 top±gap 之间）
      3. 关联到右侧最近的 cluster（装饰音在正式音符左边）

    返回：dict，key=cluster_idx，value=list of {'string': int, 'fret': int}
      表示该 cluster 左侧有装饰音，装饰音品位为 fret，弦号为 string
    """
    if fret_size is None:
        fret_size = _fret_font_size(page)

    group_top, group_bottom, string_gap, _ = _group_metrics(string_group)

    # 装饰音数字字号范围：比品位数字小，但不能太小
    grace_size_min = fret_size * 0.50
    grace_size_max = fret_size * 0.85

    # 装饰音数字位于弦线组范围内（top 在 group_top-gap ~ group_bottom+gap 之间）
    zone_top = group_top - string_gap
    zone_bottom = group_bottom + string_gap

    # 收集候选字符
    candidates = []
    for c in page.chars:
        if not c['text'].isdigit():
            continue
        if 'Arial' not in c.get('fontname', ''):
            continue
        if not (grace_size_min <= c['size'] < grace_size_max):
            continue
        if not (zone_top <= c['top'] <= zone_bottom):
            continue
        candidates.append(c)

    if not candidates:
        return {}

    # 按 x 坐标聚类（同一装饰音位置的多个数字 x 坐标相近）
    x_tol = string_gap * 1.0
    x_groups = defaultdict(list)
    for c in sorted(candidates, key=lambda x: x['x0']):
        placed = False
        for key in list(x_groups.keys()):
            if abs(key - c['x0']) < x_tol:
                x_groups[key].append(c)
                placed = True
                break
        if not placed:
            x_groups[c['x0']].append(c)

    result = {}
    for x_key, chars in x_groups.items():
        # 找右侧最近的 cluster（装饰音在正式音符左边）
        # 容差放宽到 3×gap，因为装饰音数字偏左约 1~2×gap
        right_ci = None
        right_d = float('inf')
        for ci, cx in enumerate(cluster_centers):
            d = cx - x_key
            if d >= -string_gap * 0.5 and d < right_d:  # cx 在 x_key 右侧（允许少量重叠）
                right_d = d
                right_ci = ci

        if right_ci is None or right_d > string_gap * 3.0:
            continue

        # 解析每个字符对应的弦号
        fret_entries = []
        for c in chars:
            # 找最近的弦
            best_s, best_sd = None, float('inf')
            for s_idx, s_top in enumerate(string_group):
                sd = abs(c['top'] - s_top)
                if sd < best_sd:
                    best_sd = sd
                    best_s = s_idx
            if best_s is not None and best_sd <= string_gap * 1.5:
                try:
                    fret_entries.append({'string': best_s, 'fret': int(c['text'])})
                except ValueError:
                    pass

        if fret_entries:
            result[right_ci] = fret_entries

    return result


# ─────────────────────────────────────────────
# 文字方向标记识别（let ring / let ring throughout 等）
# ─────────────────────────────────────────────

# 需要识别的技法文字关键词（小写匹配）
_TEXT_DIRECTION_KEYWORDS = [
    'let ring throughout',
    'let ring',
    'harm.',
    'harmonics',
    'vibrato',
    'vib.',
    'palm mute',
    'p.m.',
    'tap',
    'tapping',
]


def collect_text_directions(page, string_groups):
    """
    识别页面中弦线组上方/下方的文字技法标记（如 let ring throughout）。

    策略：
      - 收集 Times/Roman 字体、字号合理的字符
      - 按行（top 坐标相近）拼合成单词
      - 匹配关键词列表
      - 返回每条标记所属的弦线组索引

    返回：list of {
        'text': str,       完整文字内容（如 'let ring throughout'）
        'x': float,        文字起始 x 坐标
        'top': float,      文字 top 坐标
        'group_idx': int,  所属弦线组索引
    }
    """
    from collections import defaultdict

    fret_size = _fret_font_size(page)

    # 收集候选字符：Times/Roman 字体，字号合理
    # sl. 字符是 size≈6（< fret_size），let ring 等是 size≈8（≥ fret_size），
    # 用字号下限 fret_size * 0.95 排除 sl. 等小字号技法标记
    candidates = []
    for c in page.chars:
        font = c.get('fontname', '')
        if not ('Times' in font or 'Roman' in font):
            continue
        if c['size'] < fret_size * 0.95 or c['size'] > fret_size * 1.5:
            continue
        candidates.append(c)

    if not candidates:
        return []

    # 按 top 分组（同一行的字符 top 差 < 3pt）
    lines_map = defaultdict(list)
    for c in sorted(candidates, key=lambda x: (round(x['top']), x['x0'])):
        placed = False
        for key in list(lines_map.keys()):
            if abs(key - c['top']) < 3.0:
                lines_map[key].append(c)
                placed = True
                break
        if not placed:
            lines_map[c['top']].append(c)

    results = []
    for top_key in sorted(lines_map.keys()):
        chars = sorted(lines_map[top_key], key=lambda x: x['x0'])
        # 拼合文字（相邻字符 x 间距 < 字号 * 1.5 视为连续）
        buf = ''
        buf_x0 = chars[0]['x0']
        prev_x1 = None
        segments = []
        for c in chars:
            if prev_x1 is not None and c['x0'] - prev_x1 > c['size'] * 1.5:
                if buf.strip():
                    segments.append((buf_x0, buf.strip()))
                buf = c['text']
                buf_x0 = c['x0']
            else:
                buf += c['text']
            prev_x1 = c.get('x1', c['x0'] + c['size'] * 0.6)
        if buf.strip():
            segments.append((buf_x0, buf.strip()))

        full_text = ' '.join(s for _, s in segments)
        full_text_lower = full_text.lower()

        # 匹配关键词（优先匹配更长的关键词）
        matched_kw = None
        for kw in _TEXT_DIRECTION_KEYWORDS:
            if kw in full_text_lower:
                matched_kw = kw
                break
        if matched_kw is None:
            continue

        # 找所属弦线组（文字在哪个弦线组的标注区内）
        group_idx = None
        for g_idx, sg in enumerate(string_groups):
            gap = (sg[-1] - sg[0]) / 5.0
            if (sg[0] - gap * 5.0) <= top_key <= (sg[-1] + gap * 2.0):
                group_idx = g_idx
                break

        if group_idx is None:
            continue

        results.append({
            'text': full_text,
            'x': segments[0][0] if segments else 0,
            'top': top_key,
            'group_idx': group_idx,
        })

    return results


# ─────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────
def collect_techniques(page, string_group, cluster_centers, positions=None):
    """
    收集一行六线谱中所有技法标记，关联到 cluster 索引。

    参数：
      positions: list of dict {string_idx: fret_str}，与 cluster_centers 对应。
                 传入后可精确定位滑弦属于哪根弦。

    返回：dict，key=cluster_idx，value=dict of technique flags
      {
          'hammer_on':    bool,
          'pull_off':     bool,
          'slide_start':  int | None,  # 滑弦起始音符所在弦索引（0-based），None 表示未知
          'slide_stop':   int | None,  # 滑弦目标音符所在弦索引（0-based），None 表示未知
          'grace_note':   bool,
          'grace_frets':  list of {'string': int, 'fret': int} | None,
          'tie':          str | None,
          'tuplet':       dict | None,
      }
    """
    fret_size = _fret_font_size(page)

    hammer_ons = find_hammer_ons(page, string_group, cluster_centers, fret_size)
    pull_off_chars = find_hammer_pull_chars(page, string_group, cluster_centers, fret_size)
    # find_slides 返回两个 dict：{ci: string_idx}
    # 其中 string_idx 是滑弦所在弦（0-based），None 表示未能确定
    slide_starts, slide_stops = find_slides(
        page, string_group, cluster_centers,
        positions=positions, fret_size=fret_size
    )
    grace_notes = find_grace_notes(page, string_group, cluster_centers)
    grace_frets_raw = find_grace_note_frets(page, string_group, cluster_centers, fret_size)
    # 滑弦起始音符（小字号）会被 find_grace_note_frets 误识别为装饰音，需过滤掉
    grace_frets = {ci: v for ci, v in grace_frets_raw.items() if ci not in slide_starts}
    pull_off_curves = find_pull_offs_by_curve(page, string_group, cluster_centers)
    ties = find_ties(page, string_group, cluster_centers)
    tuplets = find_tuplets(page, string_group, cluster_centers)

    # 勾弦：字母 P 或 弧线 均算
    pull_offs = pull_off_chars | pull_off_curves

    all_cis = (hammer_ons | pull_offs | set(slide_starts.keys()) | set(slide_stops.keys())
               | grace_notes | set(grace_frets.keys()) | set(ties.keys()) | set(tuplets.keys()))
    result = {}
    for ci in all_cis:
        result[ci] = {
            'hammer_on':   ci in hammer_ons,
            'pull_off':    ci in pull_offs,
            'slide_start': slide_starts.get(ci),   # int | None
            'slide_stop':  slide_stops.get(ci),    # int | None
            'grace_note':  ci in grace_notes,
            'grace_frets': grace_frets.get(ci),
            'tie':         ties.get(ci),
            'tuplet':      tuplets.get(ci),
        }
    return result
