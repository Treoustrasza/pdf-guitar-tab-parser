"""
PDF 吉他六线谱解析器 v3
默认输出：MusicXML (.musicxml)
调试输出：ASCII Tab (.txt)
"""

import pdfplumber
from collections import defaultdict
from rhythm_parser import parse_rhythm_page
from musicxml_writer import build_measures, build_musicxml, write_musicxml


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


def is_fret_number(char_obj):
    return char_obj['text'].isdigit() and 'Arial' in char_obj.get('fontname', '')


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


def collect_notes(page, string_groups):
    group_notes = [defaultdict(list) for _ in string_groups]
    for c in page.chars:
        if not is_fret_number(c):
            continue
        char_top = c['top']
        for g_idx, sg in enumerate(string_groups):
            if sg[0] - 8 <= char_top <= sg[-1] + 8:
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
                     barline_xs, barline_tolerance=8.0):
    STRING_NAMES = ['e', 'B', 'G', 'D', 'A', 'E']

    col_widths = []
    for ci, pos in enumerate(positions):
        max_fret_w = max((len(pos.get(s, '-')) for s in range(6)), default=1)
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

    dur_line = '  '
    tab_lines = [STRING_NAMES[s] + '|' for s in range(6)]

    for ci, pos in enumerate(positions):
        w = col_widths[ci]
        dur_name = duration_names[ci]
        dur_short = DURATION_SHORT.get(dur_name, '??') if dur_name else '--'
        dur_line += dur_short.ljust(w)
        for s_idx in range(6):
            tab_lines[s_idx] += pos.get(s_idx, '-').ljust(w, '-')
        if barline_after[ci]:
            dur_line += '| '
            for s_idx in range(6):
                tab_lines[s_idx] += '|'

    dur_line += '|'
    for s_idx in range(6):
        tab_lines[s_idx] += '|'

    return dur_line, tab_lines


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

    group_notes = collect_notes(page, string_groups)
    barlines_by_group, durations_by_group = parse_rhythm_page(page, string_groups)

    result_rows = []

    for g_idx, sg in enumerate(string_groups):
        merged_notes = group_notes[g_idx]
        cluster_centers, positions = build_positions(merged_notes)
        if not cluster_centers:
            continue

        durations = durations_by_group.get(g_idx, [])
        duration_names = assign_duration_to_positions(cluster_centers, durations)
        barline_xs = barlines_by_group.get(g_idx, [])

        # 结构化小节数据（MusicXML 用）
        measures = build_measures(positions, cluster_centers, duration_names, barline_xs)

        # ASCII Tab（调试用）
        dur_line, tab_lines = render_ascii_tab(
            positions, cluster_centers, duration_names, barline_xs
        )

        result_rows.append({
            'measures': measures,
            'ascii_dur_line': dur_line,
            'ascii_tab_lines': tab_lines,
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
    tree = build_musicxml(all_measures_by_row, title=title, tempo=tempo)
    write_musicxml(tree, output_path)
    print(f'\n已输出 MusicXML: {output_path}')

    # ── 可选：输出 ASCII Tab ──
    if also_save_ascii:
        ascii_path = os.path.splitext(output_path)[0] + '_debug.txt'
        lines_out = [title, 'Standard tuning',
                     '时值: w=全音符 h=二分 q=四分 e=八分 s=十六分 t=三十二分  .=附点', '']
        for i, row in enumerate(all_rows):
            lines_out.append(f'--- 第 {i+1} 行 ---')
            lines_out.append(row['ascii_dur_line'])
            lines_out.extend(row['ascii_tab_lines'])
            lines_out.append('')
        with open(ascii_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines_out))
        print(f'已输出 ASCII Tab: {ascii_path}')

    return output_path


if __name__ == '__main__':
    pdf_path = r'C:\Users\王诗语\Documents\Tencent Files\931865382\FileRecv\MobileFile\Immature（指弹改编）.pdf'
    convert_pdf_to_musicxml(
        pdf_path,
        output_path=r'D:\working_dir\tab_demo\Immature.musicxml',
        title='Immature（指弹改编）',
        tempo=100,
        also_save_ascii=True,
    )
