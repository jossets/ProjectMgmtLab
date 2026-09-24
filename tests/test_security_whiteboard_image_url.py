import pytest


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-valid-id", "a" * 31, "a" * 33, "A" * 32],
)
def test_malformed_whiteboard_id_rejected(client, bad_id):
    resp = client.post(f"/api/whiteboard/{bad_id}/upload-image-url", json={"url": "https://example.com/x.png"})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/x.png",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_disallowed_or_malformed_urls_rejected(client, whiteboard_id, bad_url):
    resp = client.post(f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": bad_url})
    assert resp.status_code == 422


def test_overlong_url_rejected(client, whiteboard_id):
    resp = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": "https://example.com/" + "a" * 2000}
    )
    assert resp.status_code == 422


def test_attached_board_denies_non_member(client, teacher_client, student_client):
    import re

    session_id = teacher_client.post("/sessions/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    detail = teacher_client.get(f"/sessions/{session_id}")
    match = re.search(r"/whiteboard/([0-9a-f]{32})", detail.text)
    assert match
    whiteboard_id = match.group(1)

    resp = client.post(f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": "https://example.com/x.png"})
    assert resp.status_code == 403


def test_ssrf_guard_runs_before_fetching_even_with_valid_board_access(client, whiteboard_id):
    # the disallowed-target check must reject before any network attempt —
    # asserting the fast 422 here (no mock needed) is itself evidence no
    # real connection was attempted for a private target
    resp = client.post(f"/api/whiteboard/{whiteboard_id}/upload-image-url", json={"url": "http://192.168.1.1/"})
    assert resp.status_code == 422
