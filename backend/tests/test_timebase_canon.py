import pytest

from mineguard import canon, timebase


def test_pts_us_doc_vectors_30fps():
    ticks = list(range(0, 1000))
    assert timebase.pts_us(ticks, 0, 1, 30) == 0
    assert timebase.pts_us(ticks, 400, 1, 30) == 13_333_333
    assert timebase.pts_us(ticks, 359, 1, 30) == 11_966_667
    assert timebase.pts_us(ticks, 372, 1, 30) == 12_400_000


def test_pts_us_29_97_timebase_is_not_accumulated():
    ticks = [i * 1001 for i in range(742)]  # 1/30000 time base, 1001 ticks/frame
    assert timebase.pts_us(ticks, 741, 1, 30000) == timebase.round_div(741 * 1001 * 1_000_000, 30000)
    assert timebase.pts_us(ticks, 1, 1, 30000) == 33_367


def test_round_div_half_up_and_domain():
    assert timebase.round_div(1, 2) == 1 and timebase.round_div(3, 2) == 2 and timebase.round_div(1, 3) == 0
    with pytest.raises(ValueError):
        timebase.round_div(-1, 2)


def test_non_increasing_frame_table_refused():
    timebase.check_strictly_increasing([0, 1, 2])
    with pytest.raises(ValueError, match="frame_table_not_increasing"):
        timebase.check_strictly_increasing([0, 2, 2])


def test_frame_for_time_us_half_open_intervals():
    t = [0, 33_367, 66_733]
    assert [timebase.frame_for_time_us(t, x) for x in (0, 33_366, 33_367, 66_732, 66_733, 10**9)] == [0, 0, 1, 1, 2, 2]


def test_canonical_json_rejects_floats_and_non_nfc():
    with pytest.raises(ValueError):
        canon.canonical_bytes({"a": 0.5})
    with pytest.raises(ValueError):
        canon.canonical_bytes({"a": "é"})
    assert canon.canonical_bytes({"b": 1, "a": [True, None]}) == b'{"a":[true,null],"b":1}'
    assert canon.digest({"b": 1, "a": 2}) == canon.digest({"a": 2, "b": 1})
