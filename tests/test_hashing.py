from app.hashing import compute_sync_hash


def test_same_payload_gives_same_hash() -> None:
    payload = {
        "title": "Task A",
        "date": {"start": "2026-04-29", "end": "2026-04-30"},
        "status": "In Progress",
    }

    assert compute_sync_hash(payload) == compute_sync_hash(payload)


def test_different_fields_change_hash() -> None:
    base = {
        "title": "Task A",
        "date": {"start": "2026-04-29", "end": "2026-04-30"},
        "status": "In Progress",
    }

    assert compute_sync_hash(base) != compute_sync_hash({**base, "title": "Task B"})
    assert compute_sync_hash(base) != compute_sync_hash({**base, "status": "Done"})
    assert compute_sync_hash(base) != compute_sync_hash({**base, "date": {"start": "2026-04-30", "end": "2026-05-01"}})


def test_json_key_order_does_not_change_hash() -> None:
    left = {"b": 2, "a": 1, "nested": {"z": "last", "m": "middle"}}
    right = {"nested": {"m": "middle", "z": "last"}, "a": 1, "b": 2}

    assert compute_sync_hash(left) == compute_sync_hash(right)

