def test_create_page_board_redirects_to_new_id(client):
    resp = client.post("/pages/new", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/pages/")


def test_page_board_page_loads(client, page_board_id):
    resp = client.get(f"/pages/{page_board_id}")
    assert resp.status_code == 200
    assert "Nouvelles Pages" in resp.text


def test_unknown_but_well_formed_page_board_id_returns_404(client):
    assert client.get("/pages/" + "a" * 32).status_code == 404


def test_home_page_lists_new_pages_button(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Nouvelles Pages" in resp.text


def test_rename_page_board(client, page_board_id):
    resp = client.patch(f"/api/pages/{page_board_id}", json={"name": "Documentation"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Documentation"
    assert client.get(f"/api/pages/{page_board_id}").json()["name"] == "Documentation"


def test_new_board_starts_with_no_pages(client, page_board_id):
    data = client.get(f"/api/pages/{page_board_id}").json()
    assert data["pages"] == []


def test_websocket_create_root_page(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Introduction"})
        msg = ws.receive_json()
    assert msg["op"] == "page_created"
    assert msg["page"]["title"] == "Introduction"
    assert msg["page"]["parent_id"] is None

    data = client.get(f"/api/pages/{page_board_id}").json()
    assert len(data["pages"]) == 1
    assert data["pages"][0]["title"] == "Introduction"


def test_websocket_create_nested_page(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Chapitre 1"})
        root = ws.receive_json()["page"]
        ws.send_json({"op": "create_page", "parent_id": root["id"], "title": "Section 1.1"})
        child = ws.receive_json()["page"]
    assert child["parent_id"] == root["id"]


def test_websocket_create_broadcasts_to_other_viewers(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws1:
        with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws2:
            ws1.send_json({"op": "create_page", "parent_id": None, "title": "Partagé"})
            ws1.receive_json()
            msg = ws2.receive_json()
    assert msg["op"] == "page_created"
    assert msg["page"]["title"] == "Partagé"


def test_websocket_rename_page(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Ancien titre"})
        page_id = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "rename_page", "id": page_id, "title": "Nouveau titre"})
        msg = ws.receive_json()
    assert msg["op"] == "page_renamed"
    assert msg["title"] == "Nouveau titre"


def test_websocket_reorders_siblings(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ids = []
        for title in ("A", "B", "C"):
            ws.send_json({"op": "create_page", "parent_id": None, "title": title})
            ids.append(ws.receive_json()["page"]["id"])

        # move "A" (first) to the end
        ws.send_json({"op": "move_page", "id": ids[0], "parent_id": None, "index": 2})
        msg = ws.receive_json()
        assert msg["op"] == "page_moved"

    data = client.get(f"/api/pages/{page_board_id}").json()
    order = sorted(data["pages"], key=lambda p: p["order_index"])
    assert [p["id"] for p in order] == [ids[1], ids[2], ids[0]]


def test_websocket_reparents_a_page(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent A"})
        parent_a = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent B"})
        parent_b = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "create_page", "parent_id": parent_a, "title": "Enfant"})
        child = ws.receive_json()["page"]["id"]

        ws.send_json({"op": "move_page", "id": child, "parent_id": parent_b, "index": 0})
        msg = ws.receive_json()
        assert msg["op"] == "page_moved"
        assert msg["parent_id"] == parent_b

    data = client.get(f"/api/pages/{page_board_id}").json()
    by_id = {p["id"]: p for p in data["pages"]}
    assert by_id[child]["parent_id"] == parent_b


def test_websocket_rejects_moving_a_page_under_its_own_descendant(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent"})
        parent = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "create_page", "parent_id": parent, "title": "Enfant"})
        child = ws.receive_json()["page"]["id"]

        ws.send_json({"op": "move_page", "id": parent, "parent_id": child, "index": 0})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_moving_a_page_under_itself(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Solo"})
        page_id = ws.receive_json()["page"]["id"]

        ws.send_json({"op": "move_page", "id": page_id, "parent_id": page_id, "index": 0})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_delete_leaf_page(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "À supprimer"})
        page_id = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "delete_page", "id": page_id})
        msg = ws.receive_json()
    assert msg["op"] == "page_deleted"
    assert msg["id"] == page_id
    assert client.get(f"/api/pages/{page_board_id}").json()["pages"] == []


def test_websocket_delete_page_cascades_to_its_subtree(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent"})
        parent = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "create_page", "parent_id": parent, "title": "Enfant"})
        child = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "create_page", "parent_id": child, "title": "Petit-enfant"})
        grandchild = ws.receive_json()["page"]["id"]

        ws.send_json({"op": "delete_page", "id": parent})
        msg = ws.receive_json()

    assert msg["op"] == "page_deleted"
    assert msg["id"] == parent
    assert set(msg["deleted_ids"]) == {parent, child, grandchild}
    assert client.get(f"/api/pages/{page_board_id}").json()["pages"] == []


def test_websocket_delete_page_with_subtree_cascades_its_blocks(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        parent = _create_page(ws, "Parent")
        child = _create_page(ws, "Enfant", parent_id=parent)
        ws.send_json({"op": "create_block", "page_id": child, "block": _sample_text_block()})
        ws.receive_json()

        ws.send_json({"op": "delete_page", "id": parent})
        ws.receive_json()

    assert client.get(f"/api/pages/{page_board_id}").json()["blocks"] == []


def test_websocket_restore_recreates_a_deleted_leaf_page(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent"})
        parent = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "create_page", "parent_id": parent, "title": "À restaurer"})
        page_id = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "delete_page", "id": page_id})
        ws.receive_json()

        entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
        log_id = next(e["id"] for e in entries if e["can_restore"])
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()

    assert msg["op"] == "subtree_restored"
    assert len(msg["pages"]) == 1
    assert msg["pages"][0]["title"] == "À restaurer"
    assert msg["pages"][0]["parent_id"] == parent


def test_websocket_restore_recreates_a_whole_deleted_subtree(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        parent = _create_page(ws, "Parent")
        child = _create_page(ws, "Enfant", parent_id=parent)
        ws.send_json({"op": "create_block", "page_id": child, "block": _sample_text_block("Contenu enfant")})
        ws.receive_json()

        ws.send_json({"op": "delete_page", "id": parent})
        ws.receive_json()

        entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
        log_id = next(e["id"] for e in entries if e["can_restore"])
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()

    assert msg["op"] == "subtree_restored"
    restored_titles = {p["title"] for p in msg["pages"]}
    assert restored_titles == {"Parent", "Enfant"}
    assert len(msg["blocks"]) == 1
    assert msg["blocks"][0]["data"]["paragraphs"][0]["runs"][0]["text"] == "Contenu enfant"

    data = client.get(f"/api/pages/{page_board_id}").json()
    assert len(data["pages"]) == 2
    new_parent = next(p for p in data["pages"] if p["title"] == "Parent")
    new_child = next(p for p in data["pages"] if p["title"] == "Enfant")
    assert new_child["parent_id"] == new_parent["id"]


def test_websocket_restore_falls_back_to_root_if_original_parent_is_gone(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent temporaire"})
        parent = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "create_page", "parent_id": parent, "title": "Orphelin"})
        page_id = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "delete_page", "id": page_id})
        ws.receive_json()
        ws.send_json({"op": "delete_page", "id": parent})
        ws.receive_json()

        entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
        log_id = next(e["id"] for e in entries if e["can_restore"] and "Orphelin" in e["summary"])
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()

    assert msg["op"] == "subtree_restored"
    assert msg["pages"][0]["parent_id"] is None


def test_activity_log_records_tree_operations(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Suivi"})
        ws.receive_json()

    entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
    assert any(e["action"] == "created" and "Suivi" in e["summary"] for e in entries)


def test_websocket_rejects_pages_beyond_max(client, page_board_id):
    # PROJECTMGR_MAX_PAGES_PER_BOARD is set to 5 for tests (see conftest.py)
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        for i in range(5):
            ws.send_json({"op": "create_page", "parent_id": None, "title": f"Page {i}"})
            assert ws.receive_json()["op"] == "page_created"

        ws.send_json({"op": "create_page", "parent_id": None, "title": "Une de trop"})
        msg = ws.receive_json()
    assert msg["op"] == "error"
    assert len(client.get(f"/api/pages/{page_board_id}").json()["pages"]) == 5


def test_websocket_rejects_nesting_beyond_max_depth(client, page_board_id):
    # PROJECTMGR_MAX_PAGE_NESTING_DEPTH is set to 3 for tests (see conftest.py)
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        parent_id = None
        for _ in range(3):
            ws.send_json({"op": "create_page", "parent_id": parent_id, "title": "Niveau"})
            parent_id = ws.receive_json()["page"]["id"]

        ws.send_json({"op": "create_page", "parent_id": parent_id, "title": "Trop profond"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_restore_subtree_rejects_when_it_would_exceed_max_pages(client, page_board_id):
    # PROJECTMGR_MAX_PAGES_PER_BOARD is set to 5 for tests (see conftest.py)
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        parent = _create_page(ws, "Parent")
        _create_page(ws, "Enfant", parent_id=parent)  # 2 pages used
        ws.send_json({"op": "delete_page", "id": parent})
        ws.receive_json()  # back to 0 pages, snapshot covers both

        entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
        log_id = next(e["id"] for e in entries if e["can_restore"])

        # fill the board back up to 4 pages — restoring the 2-page subtree
        # would need 4 + 2 = 6 > 5
        for i in range(4):
            _create_page(ws, f"Remplissage {i}")

        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"
    assert len(client.get(f"/api/pages/{page_board_id}").json()["pages"]) == 4


def test_websocket_restore_subtree_rejects_when_it_would_exceed_max_depth(client, page_board_id, monkeypatch):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        parent = _create_page(ws, "Parent")
        _create_page(ws, "Enfant", parent_id=parent)
        ws.send_json({"op": "delete_page", "id": parent})
        ws.receive_json()

        entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
        log_id = next(e["id"] for e in entries if e["can_restore"])

        # simulate the nesting cap having been lowered since the snapshot
        # was taken (e.g. a config change) — the restore must re-check it,
        # not just trust that it was fine when deleted
        import app.routers.pages as pages_module

        monkeypatch.setattr(pages_module, "MAX_NESTING_DEPTH", 1)

        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"
    assert client.get(f"/api/pages/{page_board_id}").json()["pages"] == []


def _sample_text_block(text="Bonjour"):
    return {
        "type": "text",
        "data": {"paragraphs": [{"bullet": False, "runs": [{"text": text}]}]},
    }


def _create_page(ws, title="Page", parent_id=None):
    ws.send_json({"op": "create_page", "parent_id": parent_id, "title": title})
    return ws.receive_json()["page"]["id"]


def test_websocket_create_text_block(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        msg = ws.receive_json()
    assert msg["op"] == "block_created"
    assert msg["block"]["page_id"] == page_id
    assert msg["block"]["type"] == "text"
    assert msg["block"]["data"]["paragraphs"][0]["runs"][0]["text"] == "Bonjour"

    data = client.get(f"/api/pages/{page_board_id}").json()
    assert len(data["blocks"]) == 1


def test_websocket_create_text_block_defaults_to_no_heading_or_frame(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        msg = ws.receive_json()
    assert msg["block"]["data"]["paragraphs"][0]["heading"] == 0
    assert msg["block"]["data"]["framed"] is False


def test_websocket_update_text_block_with_heading_and_frame(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        block_id = ws.receive_json()["block"]["id"]

        block = {
            "type": "text",
            "data": {
                "paragraphs": [{"bullet": False, "heading": 2, "runs": [{"text": "Titre"}]}],
                "framed": True,
            },
        }
        ws.send_json({"op": "update_block", "id": block_id, "block": block})
        msg = ws.receive_json()
    assert msg["block"]["data"]["paragraphs"][0]["heading"] == 2
    assert msg["block"]["data"]["framed"] is True


def test_websocket_create_block_defaults_fill_in_missing_fields(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": "text", "data": {}}})
        msg = ws.receive_json()
    paragraph = msg["block"]["data"]["paragraphs"][0]
    assert paragraph["runs"][0]["text"] == ""
    assert paragraph["runs"][0]["font_size"] == 16


def test_websocket_update_block(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        block_id = ws.receive_json()["block"]["id"]

        ws.send_json({"op": "update_block", "id": block_id, "block": _sample_text_block("Modifié")})
        msg = ws.receive_json()
    assert msg["op"] == "block_updated"
    assert msg["block"]["data"]["paragraphs"][0]["runs"][0]["text"] == "Modifié"


def test_websocket_reorders_blocks_within_a_page(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ids = []
        for text in ("A", "B", "C"):
            ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block(text)})
            ids.append(ws.receive_json()["block"]["id"])

        ws.send_json({"op": "move_block", "id": ids[0], "index": 2})
        msg = ws.receive_json()
        assert msg["op"] == "block_moved"

    data = client.get(f"/api/pages/{page_board_id}").json()
    order = sorted(data["blocks"], key=lambda b: b["order_index"])
    assert [b["id"] for b in order] == [ids[1], ids[2], ids[0]]


def test_websocket_delete_block(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        block_id = ws.receive_json()["block"]["id"]

        ws.send_json({"op": "delete_block", "id": block_id})
        msg = ws.receive_json()
    assert msg["op"] == "block_deleted"
    assert client.get(f"/api/pages/{page_board_id}").json()["blocks"] == []


def test_websocket_restore_recreates_a_deleted_block(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block("À restaurer")})
        block_id = ws.receive_json()["block"]["id"]
        ws.send_json({"op": "delete_block", "id": block_id})
        ws.receive_json()

        entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
        log_id = next(e["id"] for e in entries if e["can_restore"])
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()

    assert msg["op"] == "block_created"
    assert msg["block"]["page_id"] == page_id
    assert msg["block"]["data"]["paragraphs"][0]["runs"][0]["text"] == "À restaurer"


def test_activity_log_records_block_operations(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        ws.receive_json()

    entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
    assert any(e["action"] == "created" and "Bloc texte ajouté" in e["summary"] for e in entries)


def test_upload_valid_png_returns_url_and_serves_it(client, page_board_id, png_bytes):
    resp = client.post(
        f"/api/pages/{page_board_id}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith(f"/uploads/pages/{page_board_id}/")
    assert url.endswith(".png")

    served = client.get(url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == png_bytes


def test_websocket_create_image_block(client, page_board_id, png_bytes):
    upload = client.post(
        f"/api/pages/{page_board_id}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    url = upload.json()["url"]

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json(
            {"op": "create_block", "page_id": page_id, "block": {"type": "image", "data": {"src": url, "caption": "Photo"}}}
        )
        msg = ws.receive_json()
    assert msg["op"] == "block_created"
    assert msg["block"]["data"]["src"] == url
    assert msg["block"]["data"]["caption"] == "Photo"
    assert msg["block"]["data"]["width"] is None


def test_websocket_resize_image_block(client, page_board_id, png_bytes):
    upload = client.post(
        f"/api/pages/{page_board_id}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    url = upload.json()["url"]

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": "image", "data": {"src": url}}})
        block_id = ws.receive_json()["block"]["id"]

        ws.send_json(
            {"op": "update_block", "id": block_id, "block": {"type": "image", "data": {"src": url, "caption": "", "width": 320}}}
        )
        msg = ws.receive_json()
    assert msg["block"]["data"]["width"] == 320


def test_websocket_create_table_block_with_defaults(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": "table", "data": {}}})
        msg = ws.receive_json()
    rows = msg["block"]["data"]["rows"]
    assert len(rows) == 3
    assert len(rows[0]) == 2


def test_websocket_create_code_block(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "code", "data": {"code": "print('hi')", "language": "python"}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["block"]["data"]["code"] == "print('hi')"
    assert msg["block"]["data"]["language"] == "python"


def test_websocket_create_empty_link_block_then_fill_it_in(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": "link", "data": {}}})
        block_id = ws.receive_json()["block"]["id"]

        link = {"type": "link", "data": {"url": "https://example.com/doc", "title": "Doc", "description": "Un lien"}}
        ws.send_json({"op": "update_block", "id": block_id, "block": link})
        msg = ws.receive_json()
    assert msg["block"]["data"]["url"] == "https://example.com/doc"
    assert msg["block"]["data"]["title"] == "Doc"


def test_websocket_rejects_blocks_beyond_max(client, page_board_id):
    # PROJECTMGR_MAX_BLOCKS_PER_PAGE is set to 5 for tests (see conftest.py)
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        for _ in range(5):
            ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
            assert ws.receive_json()["op"] == "block_created"

        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        msg = ws.receive_json()
    assert msg["op"] == "error"
    assert len(client.get(f"/api/pages/{page_board_id}").json()["blocks"]) == 5
