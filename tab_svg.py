"""
tab_svg.py
将解析好的六线谱数据渲染为 SVG 字符串。

布局：
  每行六线谱 = 一个 <g> 块
  弦间距 STRING_GAP，列间距由内容宽度决定
  小节线用竖线表示，时值标注在谱线上方
"""

STRING_GAP   = 14      # 弦间距 px
LINE_H       = STRING_GAP * 5   # 六线谱总高度
COL_MIN      = 18      # 最小列宽
COL_PAD      = 6       # 列左右留白
FONT_FRET    = 11      # 品位数字字号
FONT_DUR     = 9       # 时值字号
FONT_STR     = 10      # 弦名字号
ROW_GAP      = 48      # 行间距（谱线底部到下一行顶部）
MARGIN_LEFT  = 32      # 左边距（弦名区域）
MARGIN_TOP   = 24      # 顶部留白
MARGIN_RIGHT = 16
MARGIN_BOT   = 16

# 时值缩写
DUR_SHORT = {
    'whole':    'w', 'half.':    'h.', 'half':     'h',
    'quarter.': 'q.','quarter':  'q',  'eighth.':  'e.',
    'eighth':   'e', '16th.':    's.', '16th':     's',
    '32nd.':    't.','32nd':     't',  '64th':     'x',
}

STRING_NAMES = ['e', 'B', 'G', 'D', 'A', 'E']


def _col_width(pos):
    """计算一个位置（beat）所需的列宽"""
    max_fret_w = max(
        (len(pos.get(s, '')) for s in range(6) if pos.get(s)),
        default=1
    )
    return max(max_fret_w * 7 + COL_PAD * 2, COL_MIN)


def _build_row_svg(row_data, row_top, total_width):
    """
    渲染单行六线谱，返回 SVG <g> 元素字符串。
    row_data: {'measures': [...], 'ascii_dur_line': ..., 'ascii_tab_lines': ...}
    row_top: 该行在整体 SVG 中的 y 起点
    """
    parts = []
    measures = row_data['measures']

    # 展平所有 beat，同时记录小节线位置
    beats = []
    barline_after = []
    for m_idx, measure in enumerate(measures):
        for b_idx, beat in enumerate(measure):
            beats.append(beat)
            is_last_in_measure = (b_idx == len(measure) - 1)
            barline_after.append(is_last_in_measure and m_idx < len(measures) - 1)

    if not beats:
        return ''

    # 计算每列 x 坐标
    col_widths = [_col_width(b['notes']) for b in beats]
    col_xs = []
    x = MARGIN_LEFT
    for w in col_widths:
        col_xs.append(x)
        x += w
    row_width = x + MARGIN_RIGHT

    # ── 弦名 ──
    for s_idx in range(6):
        sy = row_top + s_idx * STRING_GAP
        parts.append(
            f'<text x="{MARGIN_LEFT - 8}" y="{sy + 4}" '
            f'text-anchor="end" font-size="{FONT_STR}" '
            f'fill="#7a6a5a" font-family="Special Elite, serif">'
            f'{STRING_NAMES[s_idx]}</text>'
        )

    # ── 六条弦线 ──
    line_end = min(row_width, total_width - MARGIN_RIGHT)
    for s_idx in range(6):
        sy = row_top + s_idx * STRING_GAP
        parts.append(
            f'<line x1="{MARGIN_LEFT}" y1="{sy}" '
            f'x2="{line_end}" y2="{sy}" '
            f'stroke="#b8a898" stroke-width="0.8"/>'
        )

    # ── 起始竖线 ──
    parts.append(
        f'<line x1="{MARGIN_LEFT}" y1="{row_top}" '
        f'x2="{MARGIN_LEFT}" y2="{row_top + LINE_H}" '
        f'stroke="#7a6a5a" stroke-width="1.2"/>'
    )

    # ── 每个 beat ──
    for ci, beat in enumerate(beats):
        cx   = col_xs[ci]
        cw   = col_widths[ci]
        mid  = cx + cw / 2

        # 时值标注（谱线上方）
        dur_name = beat.get('duration_name')
        if dur_name:
            short = DUR_SHORT.get(dur_name, '?')
            parts.append(
                f'<text x="{mid}" y="{row_top - 5}" '
                f'text-anchor="middle" font-size="{FONT_DUR}" '
                f'fill="#9b8ec4" font-family="Special Elite, serif" '
                f'opacity="0.85">{short}</text>'
            )

        # 品位数字
        notes = beat.get('notes', {})
        for s_idx in range(6):
            fret = notes.get(s_idx)
            if fret is None:
                continue
            sy = row_top + s_idx * STRING_GAP
            # 白色底（遮住弦线）
            fw = len(str(fret)) * 7 + 4
            parts.append(
                f'<rect x="{mid - fw/2}" y="{sy - 7}" '
                f'width="{fw}" height="13" fill="#f0ebe2"/>'
            )
            parts.append(
                f'<text x="{mid}" y="{sy + 4}" '
                f'text-anchor="middle" font-size="{FONT_FRET}" '
                f'fill="#3d3028" font-family="Special Elite, serif" '
                f'font-weight="400">{fret}</text>'
            )

        # 小节线
        if barline_after[ci]:
            bx = cx + cw - 1
            parts.append(
                f'<line x1="{bx}" y1="{row_top}" '
                f'x2="{bx}" y2="{row_top + LINE_H}" '
                f'stroke="#7a6a5a" stroke-width="1.2"/>'
            )

    # ── 结尾竖线 ──
    end_x = col_xs[-1] + col_widths[-1]
    parts.append(
        f'<line x1="{end_x}" y1="{row_top}" '
        f'x2="{end_x}" y2="{row_top + LINE_H}" '
        f'stroke="#7a6a5a" stroke-width="1.2"/>'
    )

    return '\n'.join(parts), end_x


def render_svg(all_rows, title=''):
    """
    将所有行渲染为一张完整 SVG。
    all_rows: parse_tab_page 返回的行列表（跨页合并后）
    返回 SVG 字符串。
    """
    if not all_rows:
        return '<svg xmlns="http://www.w3.org/2000/svg"><text y="20" fill="#999">无内容</text></svg>'

    # 先算每行宽度，取最大值作为 SVG 宽度
    row_widths = []
    for row in all_rows:
        measures = row['measures']
        beats = [b for m in measures for b in m]
        if not beats:
            row_widths.append(MARGIN_LEFT + MARGIN_RIGHT + 40)
            continue
        w = MARGIN_LEFT + sum(_col_width(b['notes']) for b in beats) + MARGIN_RIGHT
        row_widths.append(w)

    svg_width  = max(row_widths) if row_widths else 400
    row_height = LINE_H + ROW_GAP
    title_h    = 28 if title else 0
    svg_height = MARGIN_TOP + title_h + len(all_rows) * row_height + MARGIN_BOT

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{svg_width}" height="{svg_height}" '
        f'style="background:#f0ebe2; font-family: Special Elite, serif;">',
        # 背景
        f'<rect width="{svg_width}" height="{svg_height}" fill="#f0ebe2"/>',
    ]

    # 标题
    if title:
        parts.append(
            f'<text x="{svg_width/2}" y="{MARGIN_TOP + 16}" '
            f'text-anchor="middle" font-size="15" fill="#3d3028" '
            f'font-family="Special Elite, serif" letter-spacing="2">'
            f'{title}</text>'
        )

    # 每行
    for r_idx, row in enumerate(all_rows):
        row_top = MARGIN_TOP + title_h + r_idx * row_height + 14
        result = _build_row_svg(row, row_top, svg_width)
        if result:
            row_svg, _ = result
            parts.append(row_svg)

    parts.append('</svg>')
    return '\n'.join(parts)
