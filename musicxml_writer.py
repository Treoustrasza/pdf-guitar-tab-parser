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
                   barline_tolerance=12.0, techniques=None):
    """
    将 positions/duration_names/barline_xs 组织成小节列表。

    返回：measures = [
        [  # 小节1
            {  # 一个时间位置（beat）
                'notes': {string_idx(0-5): fret_str},
                'duration_name': 'eighth' | None,
                'techniques': {'hammer_on': bool, 'pull_off': bool, ...} | None,
            },
            ...
        ],
        ...
    ]
    """
    if techniques is None:
        techniques = {}
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
        is_tied = pos.get('_tied', False)
        beat = {
            'notes': {s: fret for s, fret in pos.items() if s != '_tied'},
            'duration_name': duration_names[ci],
            'techniques': techniques.get(ci),
            'tied': is_tied,  # 延音音符标记（只有符干无品位数字）
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
                  is_chord=False, is_rest=False, techniques=None, is_first_note=True,
                  grace_hammer_stop_strings=None):
    """
    在 measure_el 下添加一个 <note> 元素。
    string_num: 1-6（MusicXML 弦号，1=最高音弦）
    fret_num: 品位整数
    duration_name: 'eighth', 'quarter' 等
    is_chord: True 表示与前一个音符同时发声（和弦）
    is_rest: True 表示休止符
    techniques: dict，包含 hammer_on/pull_off/slide/grace_note 标志
    is_first_note: True 表示该 beat 的第一个音符（写全部技法），
                   False 表示和弦音符（只写 slide，其他技法不重复）
    grace_hammer_stop_strings: set of 0-based 弦索引，表示该音符是 grace note 击弦的目标音符
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

    # <time-modification>：连音组时值修正（只写在第一个音符上）
    tuplet_info = (techniques.get('tuplet') if techniques else None) if is_first_note else None
    if tuplet_info:
        tm = SubElement(note, 'time-modification')
        SubElement(tm, 'actual-notes').text = str(tuplet_info['actual'])
        SubElement(tm, 'normal-notes').text = str(tuplet_info['normal'])

    # <tie> 元素（音符级别，影响播放）——和弦每个音符都需要写，否则 alphaTab 只隐藏第一根弦的品位数字
    tie_type = (techniques.get('tie') if techniques else None)
    if tie_type and not is_rest:
        if tie_type in ('start', 'start_stop'):
            SubElement(note, 'tie', type='start')
        if tie_type in ('stop', 'start_stop'):
            SubElement(note, 'tie', type='stop')

    if not is_rest:
        notations = SubElement(note, 'notations')

        # <tied> 元素（notations 级别，影响显示）——同上，每个音符都写
        # 例外：grace hammer 目标音符若同时是 tie start，不写 <tied type="start">，
        # 避免 alphaTab 在 hammer-on 弧线之外再渲染一条 tie 弧线（导致双重弧线）
        _is_grace_hammer_stop = bool(grace_hammer_stop_strings and (string_num - 1) in grace_hammer_stop_strings)
        if tie_type:
            if tie_type in ('stop', 'start_stop'):
                SubElement(notations, 'tied', type='stop')
            if tie_type in ('start', 'start_stop') and not _is_grace_hammer_stop:
                SubElement(notations, 'tied', type='start')

        # <slide> 元素（notations 直接子元素，alphaTab/_parseSlide 在此层级读取）
        # MusicXML 规范：<slide> 是 <notations> 的直接子元素，不在 <technical> 里
        # slide_start/slide_stop 现在是 int（弦索引 0-based）或 None
        # 只对匹配弦的音符写 <slide>，所有音符（包括和弦）都参与判断
        if techniques:
            note_string_idx = string_num - 1  # 1-based 转 0-based
            slide_start_val = techniques.get('slide_start')
            slide_stop_val  = techniques.get('slide_stop')
            slur_start_num  = techniques.get('slur_start_num')  # int slur number 或 None
            slur_stop_num   = techniques.get('slur_stop_num')   # int slur number 或 None
            # 只有明确的 int 弦索引才写，None 表示未能确定弦号，跳过
            if isinstance(slide_start_val, int) and note_string_idx == slide_start_val:
                slide_el = SubElement(notations, 'slide', number='1', type='start')
                slide_el.set('line-type', 'solid')
                # 同时写 <slur type="start"> 连音线（像原 PDF 那样用弧线连接两个音符）
                if slur_start_num is not None:
                    SubElement(notations, 'slur',
                               number=str(slur_start_num), type='start', placement='above')
            if isinstance(slide_stop_val, int) and note_string_idx == slide_stop_val:
                slide_el = SubElement(notations, 'slide', number='1', type='stop')
                slide_el.set('line-type', 'solid')
                # 同时写 <slur type="stop"> 连音线
                if slur_stop_num is not None:
                    SubElement(notations, 'slur',
                               number=str(slur_stop_num), type='stop')

        # <tuplet> 元素（连音组显示标记，仅在起始音符上写 start）
        if tuplet_info:
            SubElement(notations, 'tuplet', type='start', number='1')

        # grace note 击弦的目标音符：写 <slur type="stop">
        # alphaTab 通过 slur 弧线自动渲染 H 标记（升序弦显示 H，降序弦显示 P）
        note_string_idx = string_num - 1  # 1-based 转 0-based
        if grace_hammer_stop_strings and note_string_idx in grace_hammer_stop_strings:
            slur_num_stop = grace_hammer_stop_strings[note_string_idx]
            SubElement(notations, 'slur', number=str(slur_num_stop), type='stop')

        technical = SubElement(notations, 'technical')
        SubElement(technical, 'string').text = str(string_num)
        SubElement(technical, 'fret').text = str(fret_num)

        # 技法标注（hammer-on / pull-off 在 <technical> 里）
        # 注意：CDN 版 alphaTab 看到任何 <hammer-on> 标签（不管 type="start/stop"）
        # 都会设置 isHammerPullOrigin=true，导致目标音符再次触发击弦链。
        # 因此 grace hammer 目标音符只靠 <slur stop> 渲染弧线，不写 <hammer-on stop>。
        if grace_hammer_stop_strings and note_string_idx in grace_hammer_stop_strings:
            pass  # slur stop 已在上方写入，不写 hammer-on stop
        elif techniques:
            hammer_start_val = techniques.get('hammer_start')
            hammer_stop_val  = techniques.get('hammer_stop')
            # hammer_start：新格式（int 弦索引）
            if isinstance(hammer_start_val, int) and note_string_idx == hammer_start_val:
                SubElement(technical, 'hammer-on', number='1', type='start').text = 'H'
            elif hammer_stop_val is None and techniques.get('hammer_on') and is_first_note:
                # 兼容旧格式：只有 hammer_on=True 但无 hammer_start/stop 时，写 start
                SubElement(technical, 'hammer-on', number='1', type='start').text = 'H'
            if isinstance(hammer_stop_val, int) and note_string_idx == hammer_stop_val:
                SubElement(technical, 'hammer-on', number='1', type='stop').text = 'H'
            # pull-off：同理
            pull_off_val = techniques.get('pull_off')
            if pull_off_val and is_first_note:
                SubElement(technical, 'pull-off', number='1', type='start').text = 'P'

    return note


def _rest_element(measure_el, duration_name):
    """添加休止符"""
    _note_element(measure_el, 1, 0, duration_name, is_rest=True)


# ─────────────────────────────────────────────
# 主序列化函数
# ─────────────────────────────────────────────

def build_musicxml(all_measures_by_row, title='Guitar Tab', tempo=100,
                   text_directions=None):
    """
    将所有行的小节数据构建成完整的 MusicXML ElementTree。

    all_measures_by_row: [
        [measure1, measure2, ...],   # 第1行
        [measure1, measure2, ...],   # 第2行
        ...
    ]
    每个 measure 是 beat 列表，每个 beat 是 {'notes': {s_idx: fret_str}, 'duration_name': ...}
    text_directions: list of {'measure_idx': int, 'text': str, 'placement': 'above'|'below'}
                     在指定小节前插入文字方向标记（如 let ring）
    """
    if text_directions is None:
        text_directions = []
    # 展平所有小节（所有行连续）
    all_measures = []
    for row in all_measures_by_row:
        all_measures.extend(row)

    # ── 预处理：为每对 slide_start/stop 分配 slur number（1-6 循环）──
    # 遍历所有 beats，找到 slide_start，分配 slur number；
    # 再找对应的 slide_stop（同弦索引的下一个 stop），分配同一 number。
    # 使用简单的"先来先服务"策略：遇到 start 分配 number，遇到 stop 释放 number。
    # 用 list 包装计数器，以便在嵌套循环（grace note 击弦）中也能修改
    _slur_counter = [0]        # 全局递增，取模 6 得到 1-6
    _active_slurs = {}         # string_idx -> slur_number（已开始但未结束的 slur）
    for measure_beats in all_measures:
        for beat in measure_beats:
            techs = beat.get('techniques')
            if not techs:
                continue
            slide_start_val = techs.get('slide_start')
            slide_stop_val  = techs.get('slide_stop')
            if isinstance(slide_start_val, int):
                _slur_counter[0] += 1
                slur_num = (_slur_counter[0] - 1) % 6 + 1  # 1-6
                techs['slur_start_num'] = slur_num
                _active_slurs[slide_start_val] = slur_num
            if isinstance(slide_stop_val, int):
                slur_num = _active_slurs.pop(slide_stop_val, None)
                if slur_num is not None:
                    techs['slur_stop_num'] = slur_num

    # ── 根元素 ──
    score = Element('score-partwise', version='4.0')

    # ── 页面布局（A4 横向，宽边距，让谱子主体更宽）──
    # MusicXML tenths 单位：scaling 定义 1 staff-space = N tenths，
    # 这里用 40 tenths = 7mm（标准六线谱间距），则 1 tenth = 0.175mm
    # A4 横向：297mm × 210mm → 297/0.175 ≈ 1697 tenths × 210/0.175 ≈ 1200 tenths
    defaults = SubElement(score, 'defaults')
    scaling = SubElement(defaults, 'scaling')
    SubElement(scaling, 'millimeters').text = '7'
    SubElement(scaling, 'tenths').text = '40'
    page_layout = SubElement(defaults, 'page-layout')
    SubElement(page_layout, 'page-height').text = '1697'   # 297mm
    SubElement(page_layout, 'page-width').text = '1200'    # 210mm（A4 横向高度作为宽度）
    page_margins = SubElement(page_layout, 'page-margins', type='both')
    SubElement(page_margins, 'left-margin').text = '57'    # 10mm
    SubElement(page_margins, 'right-margin').text = '57'   # 10mm
    SubElement(page_margins, 'top-margin').text = '57'     # 10mm
    SubElement(page_margins, 'bottom-margin').text = '57'  # 10mm
    system_layout = SubElement(defaults, 'system-layout')
    system_margins = SubElement(system_layout, 'system-margins')
    SubElement(system_margins, 'left-margin').text = '0'
    SubElement(system_margins, 'right-margin').text = '0'
    SubElement(system_layout, 'system-distance').text = '150'

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

            # 调号（C 大调，无升降号）
            key_el = SubElement(attrs, 'key')
            SubElement(key_el, 'fifths').text = '0'

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

        # ── 文字方向标记（let ring 等）──
        for td in text_directions:
            if td.get('measure_idx') == m_idx:
                dir_el = SubElement(measure_el, 'direction',
                                    placement=td.get('placement', 'above'))
                dt_el = SubElement(dir_el, 'direction-type')
                SubElement(dt_el, 'words').text = td['text']

        # ── 写音符 ──
        # 注：sl. 文字标记在写完对应音符后紧接插入，使其定位到滑弦音符正上方
        if not measure_beats:
            # 空小节：填一个全音符休止符
            _rest_element(measure_el, 'whole')
            continue

        # 预处理 tied 标记：给延音音符的前一个 beat 加 tie_start，给延音音符自身加 tie_stop
        for bi, beat in enumerate(measure_beats):
            if beat.get('tied'):
                beat['_tie_type'] = 'stop'
                if bi > 0:
                    prev = measure_beats[bi - 1]
                    prev_tie = prev.get('_tie_type')
                    if prev_tie == 'stop':
                        prev['_tie_type'] = 'start_stop'
                    else:
                        prev['_tie_type'] = 'start'

        for beat in measure_beats:
            notes_dict = beat['notes']   # {s_idx(0-5): fret_str}
            beat_techniques = beat.get('techniques') or {}

            # ── tied beat（孤立符干延音）：保留符干，不显示品位数字 ──
            # alphaTab 看到 <tie type="stop"> 时会画符干和连音线弧，但不显示品位数字
            if beat.get('tied'):
                dur_name = beat['duration_name'] or 'eighth'
                xml_type, divs, dotted = DURATION_TO_XML.get(dur_name, ('quarter', 16, False))
                tie_pairs = []
                for s_idx in sorted(notes_dict.keys()):
                    if s_idx == '_tied':
                        continue
                    fret_str = notes_dict[s_idx]
                    try:
                        fret_num = int(fret_str)
                    except (ValueError, TypeError):
                        continue
                    tie_pairs.append((s_idx + 1, fret_num))  # xml_string, fret_num
                if not tie_pairs:
                    continue
                for i, (xml_string, fret_num) in enumerate(tie_pairs):
                    note = SubElement(measure_el, 'note')
                    if i > 0:
                        SubElement(note, 'chord')
                    _pitch_element(note, xml_string, fret_num)
                    SubElement(note, 'duration').text = str(divs)
                    SubElement(note, 'type').text = xml_type
                    if dotted:
                        SubElement(note, 'dot')
                    # tie stop：播放器延音，同时告知渲染器不显示品位数字
                    SubElement(note, 'tie', type='stop')
                    notations = SubElement(note, 'notations')
                    SubElement(notations, 'tied', type='stop')
                    # 保留 <technical><string><fret>，alphaTab 需要它来定位符干位置
                    technical = SubElement(notations, 'technical')
                    SubElement(technical, 'string').text = str(xml_string)
                    SubElement(technical, 'fret').text = str(fret_num)
                continue  # 跳过后面的普通音符写入

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

            # ── 滑弦起始音符：duration_name=None 且有 slide_start → 作为 grace note 写入 ──
            slide_start_val = beat_techniques.get('slide_start')
            is_slide_grace = (beat['duration_name'] is None
                              and isinstance(slide_start_val, int))
            if is_slide_grace:
                # sl. 写在 grace note 之前，alphaTab 会将其对齐到起始音符位置
                # default-y 正值推到六线谱上方，避免与谱线重叠
                sl_dir = SubElement(measure_el, 'direction', placement='above')
                sl_dt = SubElement(sl_dir, 'direction-type')
                SubElement(sl_dt, 'words', **{'default-y': '60'}).text = 'sl.'
                for gi, (xml_string, fret_num) in enumerate(string_fret_pairs):
                    grace_note = SubElement(measure_el, 'note')
                    if gi > 0:
                        SubElement(grace_note, 'chord')
                    SubElement(grace_note, 'grace')
                    _pitch_element(grace_note, xml_string, fret_num)
                    SubElement(grace_note, 'duration').text = '4'  # 16th
                    SubElement(grace_note, 'type').text = '16th'
                    notations_g = SubElement(grace_note, 'notations')
                    # 只对匹配弦写 slide start
                    note_s_idx = xml_string - 1  # 1-based → 0-based
                    if note_s_idx == slide_start_val:
                        slide_el = SubElement(notations_g, 'slide', number='1', type='start')
                        slide_el.set('line-type', 'solid')
                        # grace note 的 slur start
                        slur_num_g = beat_techniques.get('slur_start_num')
                        if slur_num_g is not None:
                            SubElement(notations_g, 'slur',
                                       number=str(slur_num_g), type='start', placement='above')
                    technical_g = SubElement(notations_g, 'technical')
                    SubElement(technical_g, 'string').text = str(xml_string)
                    SubElement(technical_g, 'fret').text = str(fret_num)
                continue  # 跳过后面的普通音符写入

            dur_name = beat['duration_name'] or 'eighth'  # 无时值时默认八分

            # 装饰音前置音符（grace notes，来自 grace_frets）
            grace_frets = beat_techniques.get('grace_frets') if beat_techniques else None
            # 记录哪些弦的 grace note 构成击弦（grace fret < 正常音符 fret）
            # 值为分配的 slur number（1-6），用于在正常音符上写 slur stop
            grace_hammer_stop_strings = {}  # {0-based 弦索引: slur_number}
            if grace_frets:
                # 建立正常音符的弦→品位映射（0-based 弦索引）
                normal_fret_by_string = {
                    xml_s - 1: fn for xml_s, fn in string_fret_pairs
                }
                # 按弦排序写装饰音音符
                grace_pairs = sorted(grace_frets, key=lambda e: e['string'])
                for gi, entry in enumerate(grace_pairs):
                    s_idx_g = entry['string']          # 0-based
                    xml_string_g = s_idx_g + 1         # 1-based
                    fret_g = entry['fret']
                    grace_note = SubElement(measure_el, 'note')
                    if gi > 0:
                        SubElement(grace_note, 'chord')
                    SubElement(grace_note, 'grace')  # 装饰音标记
                    _pitch_element(grace_note, xml_string_g, fret_g)
                    SubElement(grace_note, 'duration').text = '4'  # 16th
                    SubElement(grace_note, 'type').text = '16th'
                    notations_g = SubElement(grace_note, 'notations')
                    technical_g = SubElement(notations_g, 'technical')
                    SubElement(technical_g, 'string').text = str(xml_string_g)
                    SubElement(technical_g, 'fret').text = str(fret_g)
                    # 判断是否构成击弦：grace fret < 同弦正常音符 fret
                    normal_fret = normal_fret_by_string.get(s_idx_g)
                    if normal_fret is not None and fret_g < normal_fret:
                        # grace note 是击弦起始：
                        # 1. 写 hammer-on start（让 alphaTab 知道这是击弦）
                        SubElement(technical_g, 'hammer-on',
                                   number='1', type='start').text = 'H'
                        # 2. 写 slur start（alphaTab 通过 slur 弧线渲染 H 标记）
                        _slur_counter[0] += 1
                        slur_num = (_slur_counter[0] - 1) % 6 + 1
                        SubElement(notations_g, 'slur',
                                   number=str(slur_num), type='start', placement='above')
                        grace_hammer_stop_strings[s_idx_g] = slur_num

            # 合并 tie 标记到 techniques
            tie_type = beat.get('_tie_type')
            if tie_type:
                beat_techniques = dict(beat_techniques) if beat_techniques else {}
                beat_techniques['tie'] = tie_type

            # 所有音符都传入 techniques，以便每个音符自己判断是否需要写 <slide>
            # 其他技法（hammer_on/pull_off/tie/tuplet）只写在第一个音符上（is_first_note）
            # grace_hammer_stop_strings：grace note 击弦目标弦集合，传给每个音符自行判断
            for i, (xml_string, fret_num) in enumerate(string_fret_pairs):
                _note_element(
                    measure_el, xml_string, fret_num, dur_name,
                    is_chord=(i > 0),
                    techniques=beat_techniques,
                    is_first_note=(i == 0),
                    grace_hammer_stop_strings=grace_hammer_stop_strings
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
