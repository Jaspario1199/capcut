import pytest

PIL = pytest.importorskip("PIL")

from capcut_recreate.cards import CardError, image_size, render_title  # noqa: E402


def test_render_title_fits_and_is_transparent(tmp_path):
    out = render_title("MONEYBALL|Billy Beane", tmp_path / "c.png", (400, 300))
    assert image_size(out) == (400, 300)
    im = PIL.Image.open(out).convert("RGBA")
    assert im.getpixel((2, 2))[3] == 0  # corner transparent
    # some opaque ink near the centre row
    row = [im.getpixel((x, 150))[3] for x in range(400)]
    assert max(row) == 255


def test_bad_colour(tmp_path):
    with pytest.raises(CardError):
        render_title("x", tmp_path / "c.png", (100, 100), fill="not-a-colour")
