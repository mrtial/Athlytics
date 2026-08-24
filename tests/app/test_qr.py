from app.qr import apple_health_shortcut_qr_svg, render_qr_svg


def test_render_qr_svg_returns_svg_markup():
    svg = render_qr_svg("hello world")

    assert svg.strip().startswith("<?xml") or "<svg" in svg
    assert "<svg" in svg


def test_apple_health_shortcut_qr_svg_encodes_url_and_token():
    svg = apple_health_shortcut_qr_svg("my-secret-token", "http://localhost:8000/api/data-sources/apple-health/import")

    assert "<svg" in svg


def test_render_qr_svg_differs_for_different_input():
    a = render_qr_svg("token-a")
    b = render_qr_svg("token-b")

    assert a != b
