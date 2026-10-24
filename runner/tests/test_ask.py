from fv.cli import format_hit


def test_format_hit_trims_text():
    s = format_hit(1, "Código del Trabajo", "a " * 200)
    assert s.startswith("1. Código del Trabajo\n   ") and len(s.splitlines()[1]) <= 104  # 3 indent + 100 chars + ellipsis


def test_format_hit_keeps_short_text_whole():
    assert format_hit(2, "T", "  uno\n dos ") == "2. T\n   uno dos"
