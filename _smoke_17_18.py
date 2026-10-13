import os
os.environ["POTLUCK_NO_DOTENV"] = "1"
os.environ.pop("DATABASE_URL", None)
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
b = "smokeboard"
good = {"title": " Biryani ", "added_by": " Asha ", "employee_id": " EMP-SECRET1 ", "bring_date": "2026-10-12"}
r = c.post(f"/board/{b}/items", json=good)
print("add", r.status_code, r.json())
assert r.status_code == 201 and "employee_id" not in r.json() and "SECRET" not in r.text
assert r.json()["title"] == "Biryani" and r.json()["added_by"] == "Asha"
iid = r.json()["id"]

for bad in ({}, {"title": 5}, {**good, "bring_date": "2026-02-30"}, {**good, "employee_id": "x" * 31}):
    rr = c.post(f"/board/{b}/items", json=bad)
    print("bad add", rr.status_code, rr.json())
    assert rr.status_code == 400
rr = c.post(f"/board/{b}/items", content="not json", headers={"content-type": "application/json"})
print("malformed", rr.status_code, rr.json()); assert rr.status_code == 400
assert c.post("/board/nobody/items", json={}).status_code == 400
assert c.get("/board/nobody/state").json()["items"] == []

with c.websocket_connect(f"/board/{b}/ws") as ws:
    print("ws", ws.receive_text())
    r2 = c.post(f"/board/{b}/items/{iid}/delete", json={"employee_id": "WRONG-ID-123"})
    print("wrong", r2.status_code, r2.json())
    assert r2.status_code == 403 and "WRONG-ID-123" not in r2.text and "SECRET" not in r2.text
    for kw in ({}, {"content": ""}):
        assert c.post(f"/board/{b}/items/{iid}/delete", **kw).status_code == 403
    assert c.post(f"/board/{b}/items/{iid}/delete?employee_id=EMP-SECRET1").status_code == 403

    # claimed -> 409 (correct id), 403 (wrong id)
    assert c.post(f"/board/{b}/items/{iid}/claim", json={"name": "Ravi"}).status_code == 200
    ws.receive_text()
    r3 = c.post(f"/board/{b}/items/{iid}/delete", json={"employee_id": "emp-secret1"})
    print("claimed", r3.status_code, r3.json())
    assert r3.status_code == 409 and r3.json()["current_claimer"] == "Ravi"
    assert c.post(f"/board/{b}/items/{iid}/delete", json={"employee_id": "no"}).status_code == 403
    assert c.post(f"/board/{b}/items/{iid}/unclaim", json={"name": "Ravi"}).status_code == 200
    ws.receive_text()

    r4 = c.post(f"/board/{b}/items/{iid}/delete", json={"employee_id": "  emp-SECRET1 "})
    print("right", r4.status_code, r4.json())
    assert r4.status_code == 200 and r4.json()["id"] == iid
    frame = ws.receive_text()
    print("frame", frame)
    assert "ItemDeleted" in frame and "employee_id" not in frame and "SECRET" not in frame.upper()

assert c.post(f"/board/{b}/items/{iid}/delete", json={"employee_id": "x"}).status_code == 404
assert c.post("/board/other/items/zzz/delete", json={}).status_code == 404
rr = c.post(f"/board/{b}/items/{iid}/claim", json={"nope": 1})
print("claim bad body", rr.status_code, rr.json()); assert rr.status_code == 400
print("ALL OK")
