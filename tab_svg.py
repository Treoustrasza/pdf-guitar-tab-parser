"""
tab_svg.py
将解析好的六线谱数据渲染为 SVG 字符串。

时值标注：在谱线上方绘制标准音符图形
  全音符   —— 空心椭圆，无符干
  二分音符 —— 空心椭圆 + 符干
  四分音符 —— 实心椭圆 + 符干
  八分音符 —— 实心椭圆 + 符干 + 1条符尾
  十六分   —— 实心椭圆 + 符干 + 2条符尾
  三十二分 —— 实心椭圆 + 符干 + 3条符尾
  附点     —— 符头右侧加实心小圆点
"""

STRING_GAP   = 14      # 弦间距 px
LINE_H       = STRING_GAP * 5   # 六线谱总高度
COL_MIN      = 22      # 最小列宽（稍宽，给音符图形留空间）
COL_PAD      = 8       # 列左右留白
FONT_FRET    = 11      # 品位数字字号
FONT_STR     = 10      # 弦名字号
ROW_GAP      = 56      # 行间距（谱线底部到下一行顶部，加高给音符图形）
NOTE_AREA_H  = 28      # 谱线上方音符区域高度
MARGIN_LEFT  = 32      # 左边距（弦名区域）
MARGIN_TOP   = 24      # 顶部留白
MARGIN_RIGHT = 16
MARGIN_BOT   = 16

STRING_NAMES = ['e', 'B', 'G', 'D', 'A', 'E']

# 音符颜色（梦核薰衣草紫）
NOTE_COLOR   = '#9b8ec4'
NOTE_STROKE  = '#7a6a9a'


# ─────────────────────────────────────────────
# 音符图形绘制
# ─────────────────────────────────────────────

# duration_name → (符头是否实心, 符干长度, 符尾数量, 是否附点)
NOTE_SHAPE = {
    'whole':    (False, 0,  0, False),
    'half':     (False, 14, 0, False),
    'half.':    (False, 14, 0, True),
    'quarter':  (True,  14, 0, False),
    'quarter.': (True,  14, 0, True),
    'eighth':   (True,  14, 1, False),
    'eighth.':  (True,  14, 1, True),
    '16th':     (True,  14, 2, False),
    '16th.':    (True,  14, 2, True),
    '32nd':     (True,  14, 3, False),
    '32nd.':    (True,  14, 3, True),
    '64th':     (True,  14, 4, False),
}

# 符头椭圆尺寸
HEAD_RX = 4.0   # 水平半径
HEAD_RY = 2.8   # 垂直半径（稍扁，像真实音符）
HEAD_TILT = -20  # 倾斜角度（度）


def _draw_note(cx, note_y, dur_name):
    """
    在 (cx, note_y) 处绘制一个音符图形，返回 SVG 片段列表。
    note_y 是符头中心的 y 坐标。
    符干向上生长（y 减小方向）。
    """
    shape = NOTE_SHAPE.get(dur_name)
    if shape is None:
        return []

    filled, stem_len, tails, dotted = shape
    parts = []

    # ── 符头 ──
    fill   = NOTE_COLOR if filled else 'none'
    stroke = NOTE_STROKE
    sw     = 1.2 if not filled else 0

    parts.append(
        f'<ellipse cx="{cx:.1f}" cy="{note_y:.1f}" '
        f'rx="{HEAD_RX}" ry="{HEAD_RY}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
        f'transform="rotate({HEAD_TILT},{cx:.1f},{note_y:.1f})"/>'
    )

    # ── 附点 ──
    if dotted:
        dot_x = cx + HEAD_RX + 3.5
        dot_y = note_y - 1.5
        parts.append(
            f'<circle cx="{dot_x:.1f}" cy="{dot_y:.1f}" r="1.5" '
            f'fill="{NOTE_COLOR}"/>'
        )

    # ── 符干 ──
    if stem_len > 0:
        stem_x  = cx + HEAD_RX - 0.5   # 符干贴符头右侧
        stem_y1 = note_y - 1            # 符干底（符头顶部附近）
        stem_y2 = note_y - stem_len     # 符干顶
        parts.append(
            f'<line x1="{stem_x:.1f}" y1="{stem_y1:.1f}" '
            f'x2="{stem_x:.1f}" y2="{stem_y2:.1f}" '
            f'stroke="{NOTE_STROKE}" stroke-width="1.1"/>'
        )

        # ── 符尾（旗帜）──
        # 每条符尾是从符干顶向右下方的弧线
        for i in range(tails):
            ty  = stem_y2 + i * 4       # 每条符尾间距 4px
            tx1 = stem_x
            ty1 = ty
            # 贝塞尔控制点：向右下弯
            tcx = stem_x + 7
            tcy = ty + 5
            tx2 = stem_x + 5
            ty2 = ty + 7
            parts.append(
                f'<path d="M{tx1:.1f},{ty1:.1f} Q{tcx:.1f},{tcy:.1f} {tx2:.1f},{ty2:.1f}" '
                f'stroke="{NOTE_STROKE}" stroke-width="1.1" fill="none" '
                f'stroke-linecap="round"/>'
            )

    return parts


# ─────────────────────────────────────────────
# 列宽计算
# ─────────────────────────────────────────────

def _col_width(pos):
    """计算一个位置（beat）所需的列宽"""
    max_fret_w = max(
        (len(pos.get(s, '')) for s in range(6) if pos.get(s)),
        default=1
    )
    return max(max_fret_w * 7 + COL_PAD * 2, COL_MIN)


# ─────────────────────────────────────────────
# 单行渲染
# ─────────────────────────────────────────────

def _build_row_svg(row_data, row_top, total_width):
    """
    渲染单行六线谱，返回 (SVG字符串, 行末x坐标)。
    row_top 是谱线第一弦的 y 坐标（音符区域在其上方）。
    """
    parts = []
    measures = row_data['measures']

    # 展平所有 beat，同时记录小节线位置
    beats = []
    barline_after = []
    for m_idx, measure in enumerate(measures):
        for b_idx, beat in enumerate(measure):
            beats.append(beat)
            is_last = (b_idx == len(measure) - 1)
            barline_after.append(is_last and m_idx < len(measures) - 1)

    if not beats:
        return '', MARGIN_LEFT

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
        cx  = col_xs[ci]
        cw  = col_widths[ci]
        mid = cx + cw / 2

        # 音符图形（谱线上方 NOTE_AREA_H 区域内）
        dur_name = beat.get('duration_name')
        if dur_name:
            # 符头 y：谱线上方约 14px 处
            note_y = row_top - 14
            parts.extend(_draw_note(mid, note_y, dur_name))

        # 品位数字
        notes = beat.get('notes', {})
        for s_idx in range(6):
            fret = notes.get(s_idx)
            if fret is None:
                continue
            sy = row_top + s_idx * STRING_GAP
            fw = len(str(fret)) * 7 + 4
            # 白色底遮住弦线
            parts.append(
                f'<rect x="{mid - fw/2:.1f}" y="{sy - 7}" '
                f'width="{fw}" height="13" fill="#f0ebe2"/>'
            )
            parts.append(
                f'<text x="{mid:.1f}" y="{sy + 4}" '
                f'text-anchor="middle" font-size="{FONT_FRET}" '
                f'fill="#3d3028" font-family="Special Elite, serif">'
                f'{fret}</text>'
            )

        # 小节线
        if barline_after[ci]:
            bx = cx + cw - 1
            parts.append(
                f'<line x1="{bx:.1f}" y1="{row_top}" '
                f'x2="{bx:.1f}" y2="{row_top + LINE_H}" '
                f'stroke="#7a6a5a" stroke-width="1.2"/>'
            )

    # ── 结尾竖线 ──
    end_x = col_xs[-1] + col_widths[-1]
    parts.append(
        f'<line x1="{end_x:.1f}" y1="{row_top}" '
        f'x2="{end_x:.1f}" y2="{row_top + LINE_H}" '
        f'stroke="#7a6a5a" stroke-width="1.2"/>'
    )

    return '\n'.join(parts), end_x


# ─────────────────────────────────────────────
# 整体 SVG 构建
# ─────────────────────────────────────────────

def render_svg(all_rows, title=''):
    """
    将所有行渲染为一张完整 SVG。
    all_rows: parse_tab_page 返回的行列表（跨页合并后）
    返回 SVG 字符串。
    """
    if not all_rows:
        return '<svg xmlns="http://www.w3.org/2000/svg"><text y="20" fill="#999">无内容</text></svg>'

    # 计算每行宽度，取最大值
    row_widths = []
    for row in all_rows:
        beats = [b for m in row['measures'] for b in m]
        if not beats:
            row_widths.append(MARGIN_LEFT + MARGIN_RIGHT + 40)
            continue
        w = MARGIN_LEFT + sum(_col_width(b['notes']) for b in beats) + MARGIN_RIGHT
        row_widths.append(w)

    svg_width  = max(row_widths) if row_widths else 400
    row_height = LINE_H + ROW_GAP   # ROW_GAP 包含音符区域高度
    title_h    = 32 if title else 0
    svg_height = MARGIN_TOP + title_h + len(all_rows) * row_height + MARGIN_BOT

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{svg_width}" height="{svg_height}" '
        f'style="background:#f0ebe2;">',
        f'<rect width="{svg_width}" height="{svg_height}" fill="#f0ebe2"/>',
    ]

    # 标题
    if title:
        parts.append(
            f'<text x="{svg_width/2:.1f}" y="{MARGIN_TOP + 18}" '
            f'text-anchor="middle" font-size="15" fill="#3d3028" '
            f'font-family="Special Elite, serif" letter-spacing="2">'
            f'{title}</text>'
        )

    # 每行：row_top 是第一弦的 y，音符画在 row_top 上方
    for r_idx, row in enumerate(all_rows):
        # NOTE_AREA_H 留给音符，再加一点间距
        row_top = MARGIN_TOP + title_h + r_idx * row_height + NOTE_AREA_H + 4
        result = _build_row_svg(row, row_top, svg_width)
        if result:
            row_svg, _ = result
            parts.append(row_svg)

    parts.append('</svg>')
    return '\n'.join(parts)
