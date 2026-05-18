from datetime import date, datetime, timezone

from src.strategy import session


def test_summer_window_edt():
    start, end = session.session_window_utc(date(2024, 6, 14))
    # EDT (UTC-4) → 09:30 EDT = 13:30 UTC, end = 17:30 UTC
    assert start == datetime(2024, 6, 14, 13, 30, tzinfo=timezone.utc)
    assert end == datetime(2024, 6, 14, 17, 30, tzinfo=timezone.utc)


def test_winter_window_est():
    start, end = session.session_window_utc(date(2024, 1, 15))
    # EST (UTC-5) → 09:30 EST = 14:30 UTC, end = 18:30 UTC
    assert start == datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
    assert end == datetime(2024, 1, 15, 18, 30, tzinfo=timezone.utc)


def test_dst_transition_spring_forward():
    # 2024 DST start: March 10
    pre = session.session_window_utc(date(2024, 3, 8))[0]
    post = session.session_window_utc(date(2024, 3, 11))[0]
    assert pre.hour == 14    # EST
    assert post.hour == 13   # EDT


def test_in_session_check():
    d = date(2024, 6, 14)
    assert session.in_session(datetime(2024, 6, 14, 14, 0, tzinfo=timezone.utc), d)
    assert not session.in_session(datetime(2024, 6, 14, 19, 0, tzinfo=timezone.utc), d)


def test_ny_time_to_utc_summer():
    # 15:30 NY in June = 19:30 UTC
    assert session.ny_time_to_utc(date(2024, 6, 14), 15, 30) == \
        datetime(2024, 6, 14, 19, 30, tzinfo=timezone.utc)


def test_session_end_21utc():
    assert session.session_end_utc(date(2024, 6, 14)) == \
        datetime(2024, 6, 14, 21, 0, tzinfo=timezone.utc)
