from unittest.mock import patch

from app.routers.whiteboard import MAX_IMAGE_BYTES


def test_upload_image_from_url_success(client, whiteboard_id, png_bytes):
    with patch("app.routers.whiteboard.fetch_public_url", return_value=png_bytes) as mocked:
        resp = client.post(
            f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": "https://example.com/pic.png"}
        )
    mocked.assert_called_once()
    assert mocked.call_args.args[0] == "https://example.com/pic.png"

    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith(f"/uploads/whiteboard/{whiteboard_id}/")

    img_resp = client.get(url)
    assert img_resp.status_code == 200
    assert img_resp.content == png_bytes


def test_upload_image_from_url_rejects_non_image_bytes(client, whiteboard_id):
    with patch("app.routers.whiteboard.fetch_public_url", return_value=b"not an image"):
        resp = client.post(
            f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": "https://example.com/pic.png"}
        )
    assert resp.status_code == 415


def test_upload_image_from_url_rejects_oversized(client, whiteboard_id):
    with patch("app.routers.whiteboard.fetch_public_url", return_value=b"x" * (MAX_IMAGE_BYTES + 1)):
        resp = client.post(
            f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": "https://example.com/pic.png"}
        )
    assert resp.status_code == 413


def test_upload_image_from_url_rejects_empty_body(client, whiteboard_id):
    with patch("app.routers.whiteboard.fetch_public_url", return_value=b""):
        resp = client.post(
            f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": "https://example.com/pic.png"}
        )
    assert resp.status_code == 400
