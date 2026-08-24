"""QR code rendering for onboarding/Connections pages.

Generates plain-text QR codes (not URL-formatted -- iOS Camera shows a
"Copy" prompt for plain text vs. an "Open" prompt for a URL, and we want
the former since the point is getting a long string onto the phone's
clipboard, not navigating anywhere) as inline SVG, so no extra image
route or file storage is needed -- the SVG markup is embedded directly
in the page.
"""
import io

import qrcode
import qrcode.image.svg


def render_qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=2)
    buffer = io.BytesIO()
    img.save(buffer)
    return buffer.getvalue().decode("utf-8")


def apple_health_shortcut_qr_svg(api_token: str, apple_health_upload_url: str) -> str:
    payload = (
        "Athlytics iOS Shortcut setup\n"
        f"URL: {apple_health_upload_url}\n"
        f"Header: Authorization: Bearer {api_token}"
    )
    return render_qr_svg(payload)
