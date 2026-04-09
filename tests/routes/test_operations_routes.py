"""Route tests for the /api/operations endpoint."""
import core.app_state as app_state


def _clear_operations():
    with app_state._operations_lock:
        app_state._operations.clear()


def _clear_notifications():
    with app_state._notifications_lock:
        app_state._notifications.clear()


class TestOperationsRoute:
    def setup_method(self):
        _clear_operations()
        _clear_notifications()

    def teardown_method(self):
        _clear_operations()
        _clear_notifications()

    def test_no_operations(self, client):
        resp = client.get("/api/operations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["operations"] == []

    def test_with_active_operation(self, client):
        op_id = app_state.register_operation("metadata", "Batman", total=10)
        app_state.update_operation(op_id, current=3, detail="Issue #3")

        resp = client.get("/api/operations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["operations"]) == 1
        op = data["operations"][0]
        assert op["id"] == op_id
        assert op["op_type"] == "metadata"
        assert op["label"] == "Batman"
        assert op["status"] == "running"
        assert op["current"] == 3
        assert op["total"] == 10
        assert op["detail"] == "Issue #3"

    def test_non_destructive_operations_poll_preserves_notifications(self, client):
        app_state.add_notification("Background warning", level="warning")

        resp = client.get("/api/operations?include_notifications=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["notifications"] == []

        resp = client.get("/api/operations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["notifications"]) == 1
        assert data["notifications"][0]["message"] == "Background warning"

    def test_cancel_running_operation(self, client):
        op_id = app_state.register_operation("metadata", "Batman", total=10)

        resp = client.post(f"/api/operations/{op_id}/cancel")

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert app_state.is_operation_cancelled(op_id) is True

    def test_cancel_unknown_operation(self, client):
        resp = client.post("/api/operations/missing/cancel")

        assert resp.status_code == 404
        assert resp.get_json()["success"] is False
