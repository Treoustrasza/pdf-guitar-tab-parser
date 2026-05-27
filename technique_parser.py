"""
吉他技法符号解析模块

支持识别以下技法，并将其关联到最近的 cluster（音符位置）：
  - H  (Hammer-on)   击弦：size=6 TimesNewRoman-0-50 字符 'H'，位于弦线组上方
  - sl. (Slide)      滑弦：size=6 TimesNewRoman-1-50 字符 's'+'l'+'.'，位于弦线组上方
  - 装饰音弧线       Grace note：曲线 pts=12, lw=0.00, y_span≈13.7pt，x_span≈5pt
  - 勾弦弧线         Pull-off：曲线 pts=6, lw=0.68, y_span≈26pt，x_span≈7pt

返回格式：
  techniques = {
      cluster_idx: {
          'hammer_on': True/False,
          'pull_off': True/False,
          'slide': True/False,
          'grace_note': True/False,
      },
      ...
  }
"""

from collections import defaultdict


# ─────────────────────────────────────────────
# 辅助：找最近的 cluster
# ─────────────────────────────────────────────
def _nearest_cluster(x, cluster_centers, x_tolerance=12.0):
    """返回距离 x 最近的 cluster 索引，超出 tolerance 返回 None"""
    best_ci, best_dist = None, float('inf')
    for ci, cx in enumerate(cluster_centers):
        dist = abs(cx - x)
        if dist < best_dist and dist < x_tolerance:
            best_dist = dist
            best_ci = ci
    return best_ci


def _in_group_range(top, string_group, above_margin=20.0, below_margin=10.0):
    """判断 top 坐标是否在弦线组的上方合理范围内"""
    group_top = string_group[0]
    group_bottom = string_group[-1]
    return (group_top - above_margin) <= top <= (group_bottom + below_margin)


# ─────────────────────────────────────────────
# 击弦识别（H）
# ─────────────────────────────────────────────
def find_hammer_ons(page, string_group, cluster_centers, x_tolerance=12.0):
    """
    识别击弦标记（字符 'H'，size=6，TimesNewRoman-0-50）。
    返回：set of cluster_idx
    """
    result = set()
    for c in page.chars:
        if (c['text'] == 'H'
                and abs(c['size'] - 6.0) < 0.5
                and 'TimesNewRoman-0-50' in c.get('fontname', '')):
            if _in_group_range(c['top'], string_group):
                ci = _nearest_cluster(c['x0'], cluster_centers, x_tolerance)
                if ci is not None:
                    result.add(ci)
    return result


# ─────────────────────────────────────────────
# 滑弦识别（sl.）
# ─────────────────────────────────────────────
def find_slides(page, string_group, cluster_centers, x_tolerance=15.0):
    """
    识别滑弦标记（字符序列 's'+'l'+'.'，size=6，TimesNewRoman-1-50）。
    返回：set of cluster_idx（标记在起始音符上）
    """
    result = set()
    # 收集所有候选 's' 字符
    candidates = [
        c for c in page.chars
        if (c['text'] == 's'
            and abs(c['size'] - 6.0) < 0.5
            and 'TimesNewRoman-1-50' in c.get('fontname', ''))
    ]
    for s_char in candidates:
        if not _in_group_range(s_char['top'], string_group):
            continue
        # 检查同行是否紧跟 'l' 和 '.'
        nearby = [
            c for c in page.chars
            if (abs(c['top'] - s_char['top']) < 1.5
                and c['x0'] > s_char['x0']
                and c['x0'] < s_char['x0'] + 12.0
                and 'TimesNewRoman-1-50' in c.get('fontname', ''))
        ]
        texts = ''.join(c['text'] for c in sorted(nearby, key=lambda x: x['x0']))
        if texts.startswith('l'):
            ci = _nearest_cluster(s_char['x0'], cluster_centers, x_tolerance)
            if ci is not None:
                result.add(ci)
    return result


# ─────────────────────────────────────────────
# 装饰音识别（小弧线，pts=12, lw=0.00）
# ─────────────────────────────────────────────
def find_grace_notes(page, string_group, cluster_centers, x_tolerance=12.0):
    """
    识别装饰音弧线（pts=12, lw=0.00, x_span≈5pt, y_span≈13.7pt）。
    返回：set of cluster_idx
    """
    result = set()
    for curve in page.curves:
        pts = curve.get('pts', [])
        if len(pts) != 12:
            continue
        if abs(curve.get('linewidth', -1)) > 0.05:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h_span = max(ys) - min(ys)
        # 装饰音弧线特征：宽 3~10pt，高 10~18pt
        if not (3.0 < w < 10.0 and 10.0 < h_span < 18.0):
            continue
        top = page.height - max(ys)
        if not _in_group_range(top, string_group, above_margin=25.0):
            continue
        cx_center = (min(xs) + max(xs)) / 2
        ci = _nearest_cluster(cx_center, cluster_centers, x_tolerance)
        if ci is not None:
            result.add(ci)
    return result


# ─────────────────────────────────────────────
# 勾弦识别（大弧线，pts=6, lw=0.68）
# ─────────────────────────────────────────────
def find_pull_offs(page, string_group, cluster_centers, x_tolerance=15.0):
    """
    识别勾弦弧线（pts=6, lw≈0.68, x_span≈7pt, y_span≈26pt）。
    返回：set of cluster_idx（标记在起始音符上）
    """
    result = set()
    for curve in page.curves:
        pts = curve.get('pts', [])
        if len(pts) != 6:
            continue
        if abs(curve.get('linewidth', 0) - 0.68) > 0.15:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h_span = max(ys) - min(ys)
        # 勾弦弧线特征：宽 5~15pt，高 20~35pt
        if not (5.0 < w < 15.0 and 20.0 < h_span < 35.0):
            continue
        top = page.height - max(ys)
        if not _in_group_range(top, string_group, above_margin=30.0):
            continue
        cx_center = (min(xs) + max(xs)) / 2
        ci = _nearest_cluster(cx_center, cluster_centers, x_tolerance)
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
    hammer_ons = find_hammer_ons(page, string_group, cluster_centers)
    slides = find_slides(page, string_group, cluster_centers)
    grace_notes = find_grace_notes(page, string_group, cluster_centers)
    pull_offs = find_pull_offs(page, string_group, cluster_centers)

    # 合并所有有技法的 cluster
    all_cis = hammer_ons | slides | grace_notes | pull_offs
    result = {}
    for ci in all_cis:
        result[ci] = {
            'hammer_on': ci in hammer_ons,
            'pull_off': ci in pull_offs,
            'slide': ci in slides,
            'grace_note': ci in grace_notes,
        }
    return result
