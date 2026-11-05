"""Zone geometry in doubled integer coordinates (MG-DATA-001 sections 5.2-5.6). Integer arithmetic only; boundary inclusive."""
from __future__ import annotations

T, F, U = "T", "F", "U"
MAX_VERTICES = 256


class InvalidZone(ValueError):
    pass


def _cross(a, b, p) -> int:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _on_segment(a, b, p) -> bool:
    return _cross(a, b, p) == 0 and min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def segments_intersect(p1, p2, q1, q2) -> bool:
    """Closed segments, collinear overlap included."""
    d1, d2, d3, d4 = _sign(_cross(q1, q2, p1)), _sign(_cross(q1, q2, p2)), _sign(_cross(p1, p2, q1)), _sign(_cross(p1, p2, q2))
    if d1 != d2 and d3 != d4:
        return True
    return _on_segment(q1, q2, p1) or _on_segment(q1, q2, p2) or _on_segment(p1, p2, q1) or _on_segment(p1, p2, q2)


def area2(poly) -> int:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def validate_polygon(poly, width: int, height: int) -> list[list[int]]:
    """Returns the canonical polygon (positive orientation, first vertex lexicographically smallest by (y, x)) or raises InvalidZone."""
    if not isinstance(poly, (list, tuple)) or not 3 <= len(poly) <= MAX_VERTICES:
        raise InvalidZone("mg.refusal.zone_vertex_count")
    pts = []
    for v in poly:
        if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(c, int) and not isinstance(c, bool) for c in v)):
            raise InvalidZone("mg.refusal.zone_not_integer")
        if not (0 <= v[0] <= width and 0 <= v[1] <= height):
            raise InvalidZone("mg.refusal.zone_out_of_frame")
        pts.append((v[0], v[1]))
    n = len(pts)
    if len(set(pts)) != n:
        raise InvalidZone("mg.refusal.zone_duplicate_vertex")
    for i in range(n):
        a, b, c = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        if _cross(a, b, c) == 0:
            raise InvalidZone("mg.refusal.zone_collinear_vertices")
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            c, d = pts[j], pts[(j + 1) % n]
            adjacent = j == i + 1 or (i == 0 and j == n - 1)
            if adjacent:
                shared = b if j == i + 1 else a
                other_i, other_j = (a if shared is b else b), (d if j == i + 1 else c)
                # adjacent edges may only share their common vertex: the far endpoint of one must not lie on the other
                if _on_segment(c, d, other_i) or _on_segment(a, b, other_j):
                    raise InvalidZone("mg.refusal.zone_self_intersection")
            elif segments_intersect(a, b, c, d):
                raise InvalidZone("mg.refusal.zone_self_intersection")
    if area2(pts) == 0:
        raise InvalidZone("mg.refusal.zone_zero_area")
    if area2(pts) < 0:
        pts.reverse()
    k = min(range(n), key=lambda i: (pts[i][1], pts[i][0]))
    pts = pts[k:] + pts[:k]
    return [[x, y] for x, y in pts]


def point_in_polygon(poly, px2: int, py2: int) -> bool:
    """poly in ORIGINAL integer coordinates; the point is already doubled. Boundary inclusive (MG-DATA-001 5.4)."""
    d = [(x * 2, y * 2) for x, y in poly]
    p = (px2, py2)
    n = len(d)
    if any(_on_segment(d[i], d[(i + 1) % n], p) for i in range(n)):
        return True
    inside = False
    for i in range(n):
        a, b = d[i], d[(i + 1) % n]
        if a[1] <= p[1] < b[1] and _cross(a, b, p) > 0:
            inside = not inside
        elif b[1] <= p[1] < a[1] and _cross(a, b, p) < 0:
            inside = not inside
    return inside


def _segment_touches_polygon(poly, s1, s2) -> bool:
    d = [(x * 2, y * 2) for x, y in poly]
    if point_in_polygon(poly, *s1) or point_in_polygon(poly, *s2):
        return True
    n = len(d)
    return any(segments_intersect(s1, s2, d[i], d[(i + 1) % n]) for i in range(n))


def occupancy(poly, bbox: dict, truncated: dict, frame_w: int) -> tuple[str, str | None]:
    """OCC of one object for one zone: (T|F|U, cause). Reference point bbox_bottom_center in doubled coordinates (MG-DATA-001 5.3, 5.5)."""
    rx2, ry2 = 2 * bbox["x"] + bbox["w"], 2 * (bbox["y"] + bbox["h"])
    left, right, bottom = bool(truncated.get("left")), bool(truncated.get("right")), bool(truncated.get("bottom"))
    if not (left or right or bottom):
        return (T if point_in_polygon(poly, rx2, ry2) else F), None
    lo, hi = (0, 2 * frame_w) if (left and right) else (0, rx2) if left else (rx2, 2 * frame_w) if right else (rx2, rx2)
    touches = _segment_touches_polygon(poly, (lo, ry2), (hi, ry2))
    return (U, "object_truncated_at_frame_edge") if touches else (F, None)
