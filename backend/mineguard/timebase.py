"""Frame-table time arithmetic (MG-DATA-001 section 4). Integer-only; rounding applied per frame, never accumulated."""


def round_div(n: int, d: int) -> int:
    """Round half up for n >= 0, d > 0."""
    if n < 0 or d <= 0:
        raise ValueError("round_div requires n >= 0 and d > 0")
    return (2 * n + d) // (2 * d)


def pts_us(pts_ticks: list[int], index: int, num: int, den: int) -> int:
    return round_div((pts_ticks[index] - pts_ticks[0]) * num * 1_000_000, den)


def check_strictly_increasing(pts_ticks: list[int]) -> None:
    """V-16: P0 refuses a source whose frame table is not strictly increasing."""
    for a, b in zip(pts_ticks, pts_ticks[1:]):
        if b <= a:
            raise ValueError("mg.refusal.frame_table_not_increasing")


def frame_for_time_us(pts_us_list: list[int], t_us: int) -> int:
    """Index of the frame covering t_us: the last frame with pts_us <= t_us (frame i covers [pts_us(i), pts_us(i+1)))."""
    lo, hi = 0, len(pts_us_list) - 1
    if t_us < pts_us_list[0]:
        return 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if pts_us_list[mid] <= t_us:
            lo = mid
        else:
            hi = mid - 1
    return lo
