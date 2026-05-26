"""
MusicXML 序列化模块

将解析好的六线谱数据写成合规的 MusicXML 文件。
目标软件：MuseScore 4、TuxGuitar、Guitar Pro 8、Sibelius

MusicXML 关键结构：
  - <clef><sign>TAB</sign></clef>  → 六线谱谱号
  - <staff-details>                → 定义6条弦及调弦
  - <note><technical><string><fret> → 弦号+品位
  - <chord/>                       → 同一时间位置的和弦音
  - divisions=16                   → 每四分音符=16个 division（支持到64分音符）
"""

from xml.etree.ElementTree import Element, SubElement, ElementTree, indent
import xml.etree.ElementTree as ET


# ─────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────

# 标准调弦（从高弦到低弦：e B G D A E）
# MusicXML string 编号：1=最高音弦(e4), 6=最低音弦(E2)
STANDARD_TUNING = [
    (1, 'E', 4),   # 第1弦 e4
    (2, 'B', 3),   # 第2弦 B3
    (3, 'G', 3),   # 第3弦 G3
    (4, 'D', 3),   # 第4弦 D3
    (5, 'A', 2),   # 第5弦 A2
    (6, 'E', 2),   # 第6弦 E2
]

# divisions：每四分音符的最小单位数
# 16 可以精确表示到 64 分音符（16/16=1, 16/2=8分, 16/4=16分, 16/8=32分, 16/16=64分）
DIVISIONS = 16

# duration_name → (MusicXML type, divisions数, 是否附点)
DURATION_TO_XML = {
    'whole':    ('whole',   64, False),
    'half.':    ('half',    48, True),
    'half':     ('half',    32, False),
    'quarter.': ('quarter', 24, True),
    'quarter':  ('quarter', 16, False),
    'eighth.':  ('eighth',  12, True),
    'eighth':   ('eighth',   8, False),
    '16th.':    ('16th',     6, True),
    '16th':     ('16th',     4, False),
    '32nd.':    ('32nd',     3, True),
    '32nd':     ('32nd',     2, False),
    '64th':     ('64th',     1, False),
}

# 品位数字 + 弦号 → MIDI 音高（用于 <pitch> 元素，让软件能播放）
# 第 n 弦空弦 MIDI 音高
STRING_OPEN_MIDI = {
    1: 64,  # e4
    2: 59,  # B3
    3: 55,  # G3
    4: 50,  # D3
    5: 45,  # A2
    6: 40,  # E2
}

MIDI_TO_PITCH = {
    # octave 2
    40: ('E', 0, 2), 41: ('F', 0, 2), 42: ('F', 1, 2), 43: ('G', 0, 2),
    44: ('G', 1, 2), 45: ('A', 0, 2), 46: ('A', 1, 2), 47: ('B', 0, 2),
    # octave 3
    48: ('C', 0, 3), 49: ('C', 1, 3), 50: ('D', 0, 3), 51: ('D', 1, 3),
    52: ('E', 0, 3), 53: ('F', 0, 3), 54: ('F', 1, 3), 55: ('G', 0, 3),
    56: ('G', 1, 3), 57: ('A', 0, 3), 58: ('A', 1, 3), 59: ('B', 0, 3),
    # octave 4
    60: ('C', 0, 4), 61: ('C', 1, 4), 62: ('D', 0, 4), 63: ('D', 1, 4),
    64: ('E', 0, 4), 65: ('F', 0, 4), 66: ('F', 1, 4), 67: ('G', 0, 4),
    68: ('G', 1, 4), 69: ('A', 0, 4), 70: ('A', 1, 4), 71: ('B', 0, 4),
    # octave 5
    72: ('C', 0, 5), 73: ('C', 1, 5), 74: ('D', 0, 5), 75: ('D', 1, 5),
    76: ('E', 0, 5), 77: ('F', 0, 5), 78: ('F', 1, 5), 79: ('G', 0, 5),
    80: ('G', 1, 5), 81: ('A', 0, 5), 82: ('A', 1, 5), 83: ('B', 0, 5),
    84: ('C', 0, 6),
}


# ─────────────────────────────────────────────
# 数据结构转换
# ─────────────────────────────────────────────

def build_measures(positions, cluster_centers, duration_names, barline_xs,
                   barline_tolerance=12.0):
    """
    将 positions/duration_names/barline_xs 组织成小节列表。

    返回：measures = [
        [  # 小节1
            {  # 一个时间位置（beat）
                'notes': {string_idx(0-5): fret_str},
                'duration_name': 'eighth' | None,
            },
            ...
        ],
        ...
    ]
    """
    # 判断每个 cluster 之后是否有小节线
    barline_after = [False] * len(cluster_centers)
    for bx in sorted(barline_xs):
        best_ci = None
        best_dist = float('inf')
        for ci, cx in enumerate(cluster_centers):
            if cx <= bx + barline_tolerance:
                dist = bx - cx
                if 0 <= dist < best_dist:
                    best_dist = dist
                    best_ci = ci
        if best_ci is not None and best_dist < 35:
            barline_after[best_ci] = True

    measures = []
    current_measure = []

    for ci, pos in enumerate(positions):
        beat = {
            'notes': {s: fret for s, fret in pos.items()},
            'duration_name': duration_names[ci],
        }
        current_measure.append(beat)
        if barline_after[ci]:
            measures.append(current_measure)
            current_measure = []

    if current_measure:
        measures.append(current_measure)

    return measures


# ─────────────────────────────────────────────
# XML 构建辅助
# ─────────────────────────────────────────────

def _pitch_element(parent, string_num, fret_num):
    """在 parent 下添加 <pitch> 元素（根据弦号+品位计算音高）"""
    midi = STRING_OPEN_MIDI.get(string_num, 64) + fret_num
    pitch_info = MIDI_TO_PITCH.get(midi)
    if pitch_info is None:
        # 超出范围时用 C5 占位
        pitch_info = ('C', 0, 5)
    step, alter, octave = pitch_info
    pitch = SubElement(parent, 'pitch')
    SubElement(pitch, 'step').text = step
    if alter:
        SubElement(pitch, 'alter').text = str(alter)
    SubElement(pitch, 'octave').text = str(octave)


def _note_element(measure_el, string_num, fret_num, duration_name,
                  is_chord=False, is_rest=False):
    """
    在 measure_el 下添加一个 <note> 元素。
    string_num: 1-6（MusicXML 弦号，1=最高音弦）
    fret_num: 品位整数
    duration_name: 'eighth', 'quarter' 等
    is_chord: True 表示与前一个音符同时发声（和弦）
    is_rest: True 表示休止符
    """
    xml_type, divs, dotted = DURATION_TO_XML.get(
        duration_name, ('quarter', 16, False)
    )

    note = SubElement(measure_el, 'note')

    if is_chord:
        SubElement(note, 'chord')

    if is_rest:
        SubElement(note, 'rest')
    else:
        _pitch_element(note, string_num, fret_num)

    SubElement(note, 'duration').text = str(divs)
    SubElement(note, 'type').text = xml_type
    if dotted:
        SubElement(note, 'dot')

    if not is_rest:
        notations = SubElement(note, 'notations')
        technical = SubElement(notations, 'technical')
        SubElement(technical, 'string').text = str(string_num)
        SubElement(technical, 'fret').text = str(fret_num)

    return note


def _rest_element(measure_el, duration_name):
    """添加休止符"""
    _note_element(measure_el, 1, 0, duration_name, is_rest=True)


# ─────────────────────────────────────────────
# 主序列化函数
# ─────────────────────────────────────────────

def build_musicxml(all_measures_by_row, title='Guitar Tab', tempo=100):
    """
    将所有行的小节数据构建成完整的 MusicXML ElementTree。

    all_measures_by_row: [
        [measure1, measure2, ...],   # 第1行
        [measure1, measure2, ...],   # 第2行
        ...
    ]
    每个 measure 是 beat 列表，每个 beat 是 {'notes': {s_idx: fret_str}, 'duration_name': ...}
    """
    # 展平所有小节（所有行连续）
    all_measures = []
    for row in all_measures_by_row:
        all_measures.extend(row)

    # ── 根元素 ──
    score = Element('score-partwise', version='4.0')

    # ── 标题 ──
    work = SubElement(score, 'work')
    SubElement(work, 'work-title').text = title

    # ── part-list ──
    part_list = SubElement(score, 'part-list')
    score_part = SubElement(part_list, 'score-part', id='P1')
    SubElement(score_part, 'part-name').text = 'Guitar'
    score_instrument = SubElement(score_part, 'score-instrument', id='P1-I1')
    SubElement(score_instrument, 'instrument-name').text = 'Classical Guitar'
    midi_instrument = SubElement(score_part, 'midi-instrument', id='P1-I1')
    SubElement(midi_instrument, 'midi-channel').text = '1'
    SubElement(midi_instrument, 'midi-program').text = '25'  # Acoustic Guitar (nylon)
    SubElement(midi_instrument, 'volume').text = '78.7402'
    SubElement(midi_instrument, 'pan').text = '0'

    # ── part ──
    part = SubElement(score, 'part', id='P1')

    for m_idx, measure_beats in enumerate(all_measures):
        measure_el = SubElement(part, 'measure', number=str(m_idx + 1))

        # ── 第一小节：写 attributes + direction ──
        if m_idx == 0:
            attrs = SubElement(measure_el, 'attributes')
            SubElement(attrs, 'divisions').text = str(DIVISIONS)

            # 拍号（4/4）
            time_el = SubElement(attrs, 'time')
            SubElement(time_el, 'beats').text = '4'
            SubElement(time_el, 'beat-type').text = '4'

            # 谱号：TAB
            clef = SubElement(attrs, 'clef')
            SubElement(clef, 'sign').text = 'TAB'

            # 弦定义
            staff_details = SubElement(attrs, 'staff-details')
            SubElement(staff_details, 'staff-lines').text = '6'
            for line_num, step, octave in STANDARD_TUNING:
                tuning = SubElement(staff_details, 'staff-tuning', line=str(line_num))
                SubElement(tuning, 'tuning-step').text = step
                SubElement(tuning, 'tuning-octave').text = str(octave)

            # 速度标记
            direction = SubElement(measure_el, 'direction', placement='above')
            direction_type = SubElement(direction, 'direction-type')
            metronome = SubElement(direction_type, 'metronome', parentheses='no')
            SubElement(metronome, 'beat-unit').text = 'quarter'
            SubElement(metronome, 'per-minute').text = str(tempo)
            SubElement(direction, 'sound', tempo=str(tempo))

        # ── 写音符 ──
        if not measure_beats:
            # 空小节：填一个全音符休止符
            _rest_element(measure_el, 'whole')
            continue

        for beat in measure_beats:
            notes_dict = beat['notes']   # {s_idx(0-5): fret_str}
            dur_name = beat['duration_name'] or 'eighth'  # 无时值时默认八分

            # 按弦排序（s_idx 0=e, 5=E → xml string 1=e, 6=E）
            string_fret_pairs = []
            for s_idx in sorted(notes_dict.keys()):
                fret_str = notes_dict[s_idx]
                try:
                    fret_num = int(fret_str)
                except ValueError:
                    continue
                xml_string = s_idx + 1  # 0-based → 1-based
                string_fret_pairs.append((xml_string, fret_num))

            if not string_fret_pairs:
                continue

            # 第一个音符正常写，其余加 <chord/>
            for i, (xml_string, fret_num) in enumerate(string_fret_pairs):
                _note_element(
                    measure_el, xml_string, fret_num, dur_name,
                    is_chord=(i > 0)
                )

    return ElementTree(score)


# ─────────────────────────────────────────────
# 写文件
# ─────────────────────────────────────────────

def write_musicxml(tree, output_path):
    """将 ElementTree 写成带 XML 声明和 DOCTYPE 的 .musicxml 文件"""
    # 格式化缩进（Python 3.9+）
    try:
        indent(tree.getroot(), space='  ')
    except AttributeError:
        pass  # Python < 3.9 跳过缩进

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE score-partwise PUBLIC\n')
        f.write('  "-//Recordare//DTD MusicXML 4.0 Partwise//EN"\n')
        f.write('  "http://www.musicxml.org/dtds/partwise.dtd">\n')
        ET.ElementTree.write(tree, f, encoding='unicode', xml_declaration=False)
