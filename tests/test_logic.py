"""Pure logic: no helper tools needed."""

from riffcore import WaveView, clean_stem, fmt_time, pitch_scale, song_subtitle


def test_fmt_time():
    assert fmt_time(None) == "–"
    assert fmt_time(75.5) == "1:15"
    assert fmt_time(75.5, precise=True) == "1:15.50"


def test_clean_stem():
    assert clean_stem("/x/Blackbird Cover [T9jMYfDFyP8].opus") == "Blackbird Cover"
    assert clean_stem("/x/My Song.mp3") == "My Song"


def test_pitch_scale():
    assert abs(pitch_scale(12, 0) - 2.0) < 1e-12
    assert abs(pitch_scale(0, -100) - 2 ** (-1 / 12)) < 1e-12


def test_song_subtitle():
    s = {"artist": "Marty", "duration": 163.7, "speed": 75, "semis": 2, "cents": 0}
    assert song_subtitle(s) == "Marty · 2:43 · 75% · +2 st"


def make_view():
    w = WaveView()
    w.width = 1000
    w.set_peaks(bytes([128]) * 10000, 100.0)
    w.set_loop(10, 20, True)
    return w


def test_drag_b_handle():
    w = make_view()
    w.drag_begin(w.t2x(20))
    w.drag_update(50)
    assert w.drag_end() == ("loop", 10, 25.0)


def test_click_seeks():
    w = make_view()
    w.drag_begin(500)
    w.drag_update(1)
    assert w.drag_end() == ("seek", 50.0)


def test_drag_new_loop():
    w = make_view()
    w.drag_begin(600)
    w.drag_update(100)
    assert w.drag_end() == ("loop", 60.0, 70.0)


def test_too_short_drag_snaps_back():
    w = make_view()
    w.width = 100000  # 4 px ≈ 4 ms: below the 50 ms minimum loop
    w.drag_begin(60000)
    w.drag_update(4)
    assert w.drag_end() is None and (w.a, w.b) == (10, 20)


def test_zoom_and_follow():
    w = make_view()
    w.zoom(0.5, 50)
    assert w.view() == (25.0, 75.0)
    w.set_pos(90)
    v0, v1 = w.view()
    assert v0 <= 90 <= v1
    w.zoom_fit()
    assert w.view() == (0.0, 100.0)


def test_columns_and_grid():
    w = make_view()
    assert len(w.columns()) == 1000
    interval, ticks = w.grid()
    assert interval == 10 and ticks[0] == 0
