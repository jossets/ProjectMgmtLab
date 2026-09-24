import pytest


@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-valid-id",
        "a" * 31,
        "a" * 33,
        "A" * 32,
        "1234-5678",
        "'; DROP TABLE gantts; --",
    ],
)
def test_malformed_gantt_id_rejected_before_db_lookup(client, bad_id):
    resp = client.get(f"/gantt/{bad_id}")
    assert resp.status_code == 422


def test_gantt_id_containing_a_slash_never_matches_a_route(client):
    # a literal "/" makes this several path segments — it must never resolve
    # to a 200 response or reflect the payload unescaped.
    resp = client.get("/gantt/<script>alert(1)</script>")
    assert resp.status_code == 404
    assert "<script>alert(1)</script>" not in resp.text


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-valid-id", "'; DROP TABLE gantts; --"],
)
def test_malformed_gantt_id_rejected_on_api_and_mutating_endpoints(client, bad_id):
    assert client.get(f"/api/gantt/{bad_id}").status_code == 422
    assert client.put(f"/api/gantt/{bad_id}/tasks", json=[]).status_code == 422
    assert client.patch(f"/api/gantt/{bad_id}", json={"name": "x"}).status_code == 422
    assert client.get(f"/api/gantt/{bad_id}/history").status_code == 422


def test_sql_injection_payload_in_task_name_is_stored_as_harmless_text(client, gantt_id):
    payload = [
        {
            "order_index": 0,
            "name": "'; DROP TABLE gantts; --",
            "start_date": None,
            "end_date": None,
            "progress_pct": 0,
        }
    ]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 200

    # the gantts table must still exist and be queryable — no injected SQL executed
    check = client.get(f"/api/gantt/{gantt_id}")
    assert check.status_code == 200
    assert check.json()["tasks"][0]["name"] == "'; DROP TABLE gantts; --"


def test_xss_payload_in_gantt_name_is_escaped_in_rendered_page(client, gantt_id):
    xss = "<script>alert(1)</script>"
    rename = client.patch(f"/api/gantt/{gantt_id}", json={"name": xss})
    assert rename.status_code == 200

    page = client.get(f"/gantt/{gantt_id}")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_xss_payload_in_task_name_never_reaches_rendered_html(client, gantt_id):
    xss = "<img src=x onerror=alert(1)>"
    payload = [{"order_index": 0, "name": xss}]
    client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)

    # task rows are rendered client-side by JS, never server-side — the raw
    # payload must not appear unescaped in the server-rendered HTML page
    page = client.get(f"/gantt/{gantt_id}")
    assert xss not in page.text


def test_task_name_over_max_length_rejected(client, gantt_id):
    payload = [{"order_index": 0, "name": "A" * 501}]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_gantt_rename_over_max_length_rejected(client, gantt_id):
    resp = client.patch(f"/api/gantt/{gantt_id}", json={"name": "A" * 201})
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_progress", [-1, 101, 999999, -999999])
def test_progress_pct_out_of_range_rejected(client, gantt_id, bad_progress):
    payload = [{"order_index": 0, "progress_pct": bad_progress}]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_negative_indent_level_rejected(client, gantt_id):
    payload = [{"order_index": 0, "indent_level": -1}]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_excessive_indent_level_rejected(client, gantt_id):
    payload = [{"order_index": 0, "indent_level": 21}]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_end_date_before_start_date_rejected(client, gantt_id):
    payload = [{"order_index": 0, "start_date": "2026-08-10", "end_date": "2026-08-01"}]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_malformed_date_format_rejected(client, gantt_id):
    payload = [{"order_index": 0, "start_date": "01/08/2026"}]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_wrong_type_for_progress_pct_rejected(client, gantt_id):
    payload = [{"order_index": 0, "progress_pct": "not-a-number"}]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_too_many_tasks_in_single_request_rejected(client, gantt_id):
    payload = [{"order_index": i} for i in range(1001)]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 422


def test_exactly_max_tasks_is_accepted(client, gantt_id):
    payload = [{"order_index": i} for i in range(1000)]
    resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert resp.status_code == 200
