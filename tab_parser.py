"""
PDF 吉他六线谱解析器 v3
默认输出：MusicXML (.musicxml)
调试输出：ASCII Tab (.txt)
"""

import pdfplumber
from collections import defaultdict
from rhythm_parser import parse_rhythm_page, _sg_gap
from musicxml_writer import build_measures, build_musicxml, write_musicxml
from technique_parser import collect_techniques, collect_text_directions, _find_slide_lines


# ─────────────────────────────────────────────
# 时值缩写（ASCII Tab 调试用）
# ─────────────────────────────────────────────
DURATION_SHORT = {
    'whole':    'w ', 'half.':    'h.', 'half':     'h ',
    'quarter.': 'q.', 'quarter':  'q ', 'eighth.':  'e.',
    'eighth':   'e ', '16th.':    's.', '16th':     's ',
    '32nd.':    't.', '32nd':     't ', '64th':     'x ',
}


# ─────────────────────────────────────────────
# 弦线识别
# ─────────────────────────────────────────────
def find_string_lines(lines, page_height, tolerance=1.0):
    """
    从页面线条中识别六线谱的弦线组。
    返回：list of list，每个子列表是一组6条弦的 top 坐标（从高弦到低弦）
    """
    h_lines = [l for l in lines if abs(l['y0'] - l['y1']) < tolerance]

    y_groups = []
    for l in sorted(h_lines, key=lambda x: x['y0']):
        y = l['y0']
        merged = False
        for g in y_groups:
            if abs(g['y'] - y) < tolerance * 2:
                g['lines'].append(l)
                g['y'] = sum(ll['y0'] for ll in g['lines']) / len(g['lines'])
                merged = True
                break
        if not merged:
            y_groups.append({'y': y, 'lines': [l]})

    y_tops = sorted([page_height - g['y'] for g in y_groups])

    string_groups = []
    i = 0
    while i <= len(y_tops) - 6:
        group = y_tops[i:i+6]
        gaps = [group[j+1] - group[j] for j in range(5)]
        avg_gap = sum(gaps) / 5
        if 3.0 <= avg_gap <= 12.0 and all(abs(g - avg_gap) < 2.5 for g in gaps):
            string_groups.append(group)
            i += 6
        else:
            i += 1

    return string_groups


# ─────────────────────────────────────────────
# 音符收集
# ─────────────────────────────────────────────
def assign_char_to_string(char_top, string_group, tolerance=4.0):
    best_idx, best_dist = None, float('inf')
    for idx, string_top in enumerate(string_group):
        dist = abs(char_top - string_top)
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx if best_dist <= tolerance else None


def _fret_font_size_for_group(page, string_group):
    """
    推断该弦线组对应的品位数字字号。
    品位数字是弦线组范围内数量最多的 Arial 数字字符，取其众数字号。
    """
    from collections import Counter
    group_top = string_group[0]
    group_bottom = string_group[-1]
    margin = (group_bottom - group_top) / 5.0 * 2.0  # 2 个弦间距余量
    sizes = Counter()
    for c in page.chars:
        if (c['text'].isdigit()
                and 'Arial' in c.get('fontname', '')
                and group_top - margin <= c['top'] <= group_bottom + margin):
            sizes[round(c['size'], 1)] += 1
    if not sizes:
        return 8.0
    return sizes.most_common(1)[0][0]


def is_fret_number(char_obj, fret_size=None):
    """
    判断字符是否为品位数字。
    条件：Arial 字体 + 数字 + 字号接近品位数字众数字号（排除连音组分母等小字）。
    """
    if not (char_obj['text'].isdigit() and 'Arial' in char_obj.get('fontname', '')):
        return False
    if fret_size is not None:
        # 字号必须在品位字号的 75%~120% 范围内（排除 size=5 的连音组分母）
        if char_obj['size'] < fret_size * 0.75:
            return False
    return True


def is_slide_start_fret(char_obj, fret_size, string_group, slide_lines):
    """
    判断小字号数字是否是滑弦起始音符（而非连音组分母）。

    滑弦起始音符特征：
      - Arial 字体，数字
      - size 在 fret_size * 0.5 ~ fret_size * 0.85 之间（比正常小）
      - 在某条滑弦斜线左端附近（x 差 < gap * 1.5）
      - top 对应该斜线的 string_idx 所在弦线（误差 < gap * 0.6）

    参数：
      slide_lines: list of {'x0', 'x1', 'top', 'string_idx'}，本行的滑弦斜线
    """
    if not (char_obj['text'].isdigit() and 'Arial' in char_obj.get('fontname', '')):
        return False
    if char_obj['size'] >= fret_size * 0.85:
        return False  # 正常字号，不是起始音符
    if char_obj['size'] < fret_size * 0.5:
        return False  # 太小
    if not slide_lines:
        return False

    gap = string_group[-1] - string_group[0]
    gap /= max(len(string_group) - 1, 1)  # 弦间距

    cx = char_obj['x0']
    ctop = char_obj['top']
    for sl in slide_lines:
        # x 在斜线左端附近（起始音符在斜线左侧）
        if abs(cx - sl['x0']) > gap * 1.5:
            continue
        # top 对应斜线所在弦线
        target_top = string_group[sl['string_idx']]
        if abs(ctop - target_top) > gap * 0.6:
            continue
        return True
    return False


def _merge_digits(digit_list, x_gap=3.0):
    if not digit_list:
        return []
    sorted_digits = sorted(digit_list, key=lambda x: x[0])
    merged = []
    cx, ct, cs = sorted_digits[0]
    for x, text, size in sorted_digits[1:]:
        char_width = size * 0.6
        if x - (cx + len(ct) * char_width) < x_gap and abs(size - cs) < 0.5:
            ct += text
        else:
            merged.append((cx, ct))
            cx, ct, cs = x, text, size
    merged.append((cx, ct))
    return merged


def collect_notes(page, string_groups, slide_lines_by_group=None):
    """
    收集各弦线组的品位数字。
    每个弦线组单独推断品位字号，过滤掉连音组分母（size 明显偏小）。

    参数：
      slide_lines_by_group: list of list，每行的滑弦斜线列表（来自 _find_slide_lines）。
        传入后，小字号数字中属于滑弦起始音符的也会被收集（其余小字号仍被过滤）。
    """
    # 预先为每个弦线组推断品位字号
    fret_sizes = [_fret_font_size_for_group(page, sg) for sg in string_groups]

    group_notes = [defaultdict(list) for _ in string_groups]
    for c in page.chars:
        char_top = c['top']
        for g_idx, sg in enumerate(string_groups):
            if sg[0] - 8 <= char_top <= sg[-1] + 8:
                fret_size = fret_sizes[g_idx]
                if is_fret_number(c, fret_size):
                    # 正常字号品位数字
                    pass
                elif (slide_lines_by_group is not None
                      and is_slide_start_fret(c, fret_size, sg,
                                              slide_lines_by_group[g_idx])):
                    # 小字号滑弦起始音符
                    pass
                else:
                    break  # 既不是品位数字也不是滑弦起始音符，跳过
                s_idx = assign_char_to_string(char_top, sg)
                if s_idx is not None:
                    group_notes[g_idx][s_idx].append((c['x0'], c['text'], c['size']))
                break

    result = []
    for g_idx in range(len(string_groups)):
        merged = {}
        for s_idx in range(6):
            merged[s_idx] = _merge_digits(group_notes[g_idx].get(s_idx, []))
        result.append(merged)
    return result


# ─────────────────────────────────────────────
# 位置聚类
# ─────────────────────────────────────────────
def build_positions(merged_notes, cluster_gap=5.0):
    all_x = set()
    for s_idx in range(6):
        for x, _ in merged_notes[s_idx]:
            all_x.add(round(x, 1))

    if not all_x:
        return [], []

    sorted_x = sorted(all_x)
    x_clusters = []
    for x in sorted_x:
        if x_clusters and x - x_clusters[-1][-1] < cluster_gap:
            x_clusters[-1].append(x)
        else:
            x_clusters.append([x])

    cluster_centers = [sum(c) / len(c) for c in x_clusters]
    positions = [dict() for _ in cluster_centers]

    for s_idx in range(6):
        for x, text in merged_notes[s_idx]:
            best_ci = min(range(len(cluster_centers)),
                          key=lambda i: abs(cluster_centers[i] - x))
            positions[best_ci][s_idx] = text

    return cluster_centers, positions


# ─────────────────────────────────────────────
# 时值匹配
# ─────────────────────────────────────────────
def assign_duration_to_positions(cluster_centers, durations, x_tolerance=8.0):
    result = [None] * len(cluster_centers)
    for d in durations:
        dx = d['x']
        best_ci, best_dist = None, float('inf')
        for ci, cx in enumerate(cluster_centers):
            dist = abs(cx - dx)
            if dist < best_dist and dist < x_tolerance:
                best_dist = dist
                best_ci = ci
        if best_ci is not None and result[best_ci] is None:
            result[best_ci] = d['duration_name']
    return result


# ─────────────────────────────────────────────
# ASCII Tab 渲染（调试用）
# ─────────────────────────────────────────────
def render_ascii_tab(positions, cluster_centers, duration_names,
                     barline_xs, barline_tolerance=8.0, techniques=None):
    STRING_NAMES = ['e', 'B', 'G', 'D', 'A', 'E']
    if techniques is None:
        techniques = {}

    col_widths = []
    for ci, pos in enumerate(positions):
        tech = techniques.get(ci, {})
        slide_suffix = '/' if tech.get('slide_start') else ''
        is_tied = pos.get('_tied', False)
        # tied 列不显示品位数字，宽度按 1 算
        if is_tied:
            max_fret_w = 1
        else:
            max_fret_w = max((len(pos.get(s, '-')) + len(slide_suffix) for s in range(6)), default=1)
        dur_name = duration_names[ci]
        dur_short = DURATION_SHORT.get(dur_name, '? ') if dur_name else '  '
        col_widths.append(max(max_fret_w, len(dur_short.rstrip())) + 1)

    barline_after = [False] * len(cluster_centers)
    for bx in barline_xs:
        best_ci, best_dist = None, float('inf')
        for ci, cx in enumerate(cluster_centers):
            if cx <= bx + barline_tolerance:
                dist = bx - cx
                if 0 <= dist < best_dist:
                    best_dist = dist
                    best_ci = ci
        if best_ci is not None and best_dist < 30:
            barline_after[best_ci] = True

    # 标注行：在滑弦起始位置写 sl.
    annotation_line = '  '
    dur_line = '  '
    tab_lines = [STRING_NAMES[s] + '|' for s in range(6)]

    for ci, pos in enumerate(positions):
        w = col_widths[ci]
        dur_name = duration_names[ci]
        dur_short = DURATION_SHORT.get(dur_name, '??') if dur_name else '--'
        dur_line += dur_short.ljust(w)
        tech = techniques.get(ci, {})
        slide_suffix = '/' if tech.get('slide_start') else ''
        # 标注行：滑弦起始处写 sl.，其余留空
        if tech.get('slide_start'):
            annotation_line += 'sl.'.ljust(w)
        else:
            annotation_line += ' ' * w
        is_tied = pos.get('_tied', False)
        for s_idx in range(6):
            # tied（孤立符干延音）：不显示品位数字，只画横线
            if is_tied:
                cell = ''
            else:
                fret_str = pos.get(s_idx, '')
                cell = (fret_str + slide_suffix) if fret_str else ''
            tab_lines[s_idx] += cell.ljust(w, '-')
        if barline_after[ci]:
            annotation_line += '  '
            dur_line += '| '
            for s_idx in range(6):
                tab_lines[s_idx] += '|'

    annotation_line = annotation_line.rstrip()
    dur_line += '|'
    for s_idx in range(6):
        tab_lines[s_idx] += '|'

    return annotation_line, dur_line, tab_lines


# ─────────────────────────────────────────────
# 单页解析
# ─────────────────────────────────────────────
def parse_tab_page(page):
    """
    解析单页，返回每行的数据。
    每个元素：{
        'measures':        小节列表（供 MusicXML 用）,
        'ascii_dur_line':  时值行字符串（调试用）,
        'ascii_tab_lines': 六行 Tab 字符串列表（调试用）,
    }
    """
    string_groups = find_string_lines(page.lines, page.height)
    if not string_groups:
        return []

    # 预先找每行的滑弦斜线，供 collect_notes 识别小字号起始音符
    slide_lines_by_group = [_find_slide_lines(page, sg) for sg in string_groups]

    group_notes = collect_notes(page, string_groups,
                                slide_lines_by_group=slide_lines_by_group)
    barlines_by_group, durations_by_group = parse_rhythm_page(page, string_groups)

    # 文字技法标记（let ring 等），按弦线组分组
    text_dirs_all = collect_text_directions(page, string_groups)
    text_dirs_by_group = defaultdict(list)
    for td in text_dirs_all:
        text_dirs_by_group[td['group_idx']].append(td)

    result_rows = []

    for g_idx, sg in enumerate(string_groups):
        merged_notes = group_notes[g_idx]
        cluster_centers, positions = build_positions(merged_notes)
        if not cluster_centers:
            continue

        durations = durations_by_group.get(g_idx, [])
        duration_names = assign_duration_to_positions(cluster_centers, durations)
        barline_xs = barlines_by_group.get(g_idx, [])

        # ── 插入孤立符干（只有符干无品位数字的延音音符）──
        # 这类音符在 PDF 里只有符干，没有品位数字，是前一个音符的延音。
        # 找出有时值但无对应 cluster 的符干，按 x 坐标插入到 cluster 序列中。
        gap = _sg_gap(sg)
        x_tol = gap * 1.2
        orphan_durations = []
        for d in durations:
            if not any(abs(cx - d['x']) < x_tol for cx in cluster_centers):
                orphan_durations.append(d)

        if orphan_durations:
            # 将孤立符干插入 cluster_centers / positions / duration_names
            for d in orphan_durations:
                ox = d['x']
                # 找插入位置（按 x 排序）
                insert_idx = next(
                    (i for i, cx in enumerate(cluster_centers) if cx > ox),
                    len(cluster_centers)
                )
                # 继承前一个 cluster 的品位（延音 = 同音延续）
                prev_pos = positions[insert_idx - 1] if insert_idx > 0 else {}
                cluster_centers.insert(insert_idx, ox)
                positions.insert(insert_idx, dict(prev_pos))  # 复制前一个音符的品位
                duration_names.insert(insert_idx, d['duration_name'])
                # 标记为延音（后续 build_measures 会用到）
                # 用特殊 key '_tied' 标记
                positions[insert_idx]['_tied'] = True

        # 技法符号（击弦/勾弦/滑弦/装饰音）
        # 传入 positions 以便 find_slides 能通过公共弦确定滑弦属于哪根弦
        techniques = collect_techniques(page, sg, cluster_centers, positions=positions)

        # 结构化小节数据（MusicXML 用）
        measures = build_measures(positions, cluster_centers, duration_names, barline_xs,
                                  techniques=techniques)

        # ASCII Tab（调试用）
        annotation_line, dur_line, tab_lines = render_ascii_tab(
            positions, cluster_centers, duration_names, barline_xs,
            techniques=techniques
        )

        result_rows.append({
            'measures': measures,
            'ascii_annotation_line': annotation_line,
            'ascii_dur_line': dur_line,
            'ascii_tab_lines': tab_lines,
            'techniques': techniques,
            'text_dirs': text_dirs_by_group.get(g_idx, []),
        })

    return result_rows


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────
def convert_pdf_to_musicxml(pdf_path, output_path=None, title=None, tempo=100,
                             also_save_ascii=False):
    """
    将 PDF 六线谱转换为 MusicXML 文件。

    参数：
      pdf_path:       输入 PDF 路径
      output_path:    输出 .musicxml 路径（默认与 PDF 同名同目录）
      title:          乐曲标题（默认从文件名提取）
      tempo:          速度（BPM，默认 100）
      also_save_ascii: 是否同时保存 ASCII Tab 调试文件
    """
    import os

    if output_path is None:
        base = os.path.splitext(pdf_path)[0]
        output_path = base + '.musicxml'

    if title is None:
        title = os.path.splitext(os.path.basename(pdf_path))[0]

    print(f'正在解析: {pdf_path}')

    all_rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            rows = parse_tab_page(page)
            all_rows.extend(rows)
            print(f'  第 {page_num + 1} 页：{len(rows)} 行六线谱')

    # ── 输出 MusicXML ──
    all_measures_by_row = [row['measures'] for row in all_rows]

    # 把文字标记映射到全局小节编号
    # 每行的文字标记放在该行第一个小节（measure_idx = 该行累计小节数起点）
    text_directions = []
    global_measure_offset = 0
    for row in all_rows:
        row_text_dirs = row.get('text_dirs', [])
        if row_text_dirs:
            # 该行有文字标记，放在该行第一个小节
            for td in row_text_dirs:
                text_directions.append({
                    'measure_idx': global_measure_offset,
                    'text': td['text'],
                    'placement': 'above',
                })
        global_measure_offset += len(row['measures'])

    tree = build_musicxml(all_measures_by_row, title=title, tempo=tempo,
                          text_directions=text_directions)
    write_musicxml(tree, output_path)
    print(f'\n已输出 MusicXML: {output_path}')

    # ── 可选：输出 ASCII Tab ──
    if also_save_ascii:
        ascii_path = os.path.splitext(output_path)[0] + '_debug.txt'
        lines_out = [title, 'Standard tuning',
                     '时值: w=全音符 h=二分 q=四分 e=八分 s=十六分 t=三十二分  .=附点', '']
        for i, row in enumerate(all_rows):
            lines_out.append(f'--- 第 {i+1} 行 ---')
            ann = row.get('ascii_annotation_line', '').rstrip()
            if ann.strip():
                lines_out.append(ann)
            lines_out.append(row['ascii_dur_line'])
            lines_out.extend(row['ascii_tab_lines'])
            lines_out.append('')
        with open(ascii_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines_out))
        print(f'已输出 ASCII Tab: {ascii_path}')

    return output_path


if __name__ == '__main__':
    pdf_path = r'Immature（指弹改编）.pdf'
    convert_pdf_to_musicxml(
        pdf_path,
        output_path=r'D:\working_dir\tab_demo\Immature.musicxml',
        title='Immature（指弹改编）',
        tempo=100,
        also_save_ascii=True,
    )
