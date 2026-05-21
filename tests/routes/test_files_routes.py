"""Tests for routes/files.py -- file operations endpoints."""
import io
import os
import sys
import pytest
from unittest.mock import patch, MagicMock


class TestMove:

    def test_missing_params(self, client):
        resp = client.post("/move", json={"source": "/a"})
        assert resp.status_code == 400

    def test_source_not_exists(self, client):
        resp = client.post("/move",
                           json={"source": "/nonexistent", "destination": "/dest"})
        assert resp.status_code == 404

    @patch("routes.files.is_critical_path", return_value=True)
    @patch("routes.files.get_critical_path_error_message", return_value="Protected")
    def test_move_critical_source(self, mock_msg, mock_crit, client, tmp_path):
        src = tmp_path / "file.cbz"
        src.write_bytes(b"fake")
        resp = client.post("/move",
                           json={"source": str(src), "destination": "/dest"})
        assert resp.status_code == 403

    def test_move_dir_into_itself(self, client, tmp_path):
        src = tmp_path / "dir"
        src.mkdir()
        dest = str(src / "subdir")
        resp = client.post("/move",
                           json={"source": str(src), "destination": dest})
        assert resp.status_code == 400

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.app_state")
    @patch("routes.files.threading.Thread")
    def test_move_file_success(self, mock_thread, mock_app_state, mock_crit, client, tmp_path):
        src = tmp_path / "comic.cbz"
        src.write_bytes(b"comic data")
        dest = str(tmp_path / "moved.cbz")

        mock_app_state.register_operation.return_value = "op-123"

        resp = client.post("/move",
                           json={"source": str(src), "destination": dest})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["op_id"] == "op-123"
        mock_app_state.register_operation.assert_called_once_with(
            "move", "comic.cbz", total=100)
        mock_thread.assert_called_once()
        # Verify the background thread was started as daemon
        mock_thread.return_value.start.assert_called_once()

    @patch("routes.files.memory_context")
    @patch("routes.files.app_state")
    @patch("routes.files.shutil.move")
    def test_do_move_runs_mangaupdates_between_metron_and_comicvine(
        self,
        mock_move,
        mock_app_state,
        mock_memory_context,
        tmp_path,
    ):
        from routes.files import _do_move

        source = str(tmp_path / "comic.cbz")
        dest = str(tmp_path / "moved.cbz")

        mock_memory_context.return_value.__enter__.return_value = None
        mock_memory_context.return_value.__exit__.return_value = None

        call_order = []

        def _record(name, suffix):
            def _inner(path):
                call_order.append((name, path))
                return f"{path}{suffix}"
            return MagicMock(side_effect=_inner)

        mock_app = MagicMock()
        mock_app.auto_fetch_metron_metadata = _record("metron", ".metron")
        mock_app.auto_fetch_mangaupdates_metadata = _record("mangaupdates", ".mu")
        mock_app.auto_fetch_comicvine_metadata = _record("comicvine", ".cv")
        mock_app.log_file_if_in_data = MagicMock()
        mock_app.update_index_on_move = MagicMock()

        with patch.dict("sys.modules", {"app": mock_app}):
            _do_move("op-123", source, dest, True)

        assert call_order == [
            ("metron", dest),
            ("mangaupdates", f"{dest}.metron"),
            ("comicvine", f"{dest}.metron.mu"),
        ]
        mock_app.log_file_if_in_data.assert_called_once_with(f"{dest}.metron.mu.cv")
        mock_app.update_index_on_move.assert_called_once_with(
            source,
            f"{dest}.metron.mu.cv",
        )
        mock_app_state.complete_operation.assert_called_once()


class TestFolderSize:

    def test_valid_path(self, client, tmp_path):
        d = tmp_path / "comics"
        d.mkdir()
        (d / "a.cbz").write_bytes(b"x" * 100)
        (d / "b.pdf").write_bytes(b"y" * 200)

        resp = client.get(f"/folder-size?path={d}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["size"] == 300
        assert data["comic_count"] == 1
        assert data["magazine_count"] == 1

    def test_invalid_path(self, client):
        resp = client.get("/folder-size?path=/nonexistent")
        assert resp.status_code == 400


class TestRename:

    @patch("routes.files.is_critical_path", return_value=False)
    def test_rename_success(self, mock_crit, client, tmp_path):
        old = tmp_path / "old.cbz"
        old.write_bytes(b"data")
        new = str(tmp_path / "new.cbz")

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/rename",
                               json={"old": str(old), "new": new})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_rename_missing_params(self, client):
        resp = client.post("/rename", json={"old": "/a"})
        assert resp.status_code == 400

    def test_rename_source_not_exists(self, client):
        resp = client.post("/rename",
                           json={"old": "/nonexistent", "new": "/new"})
        assert resp.status_code == 404

    @patch("routes.files.is_critical_path", return_value=True)
    @patch("routes.files.get_critical_path_error_message", return_value="Nope")
    def test_rename_critical(self, mock_msg, mock_crit, client, tmp_path):
        f = tmp_path / "file.cbz"
        f.write_bytes(b"data")
        resp = client.post("/rename",
                           json={"old": str(f), "new": "/new.cbz"})
        assert resp.status_code == 403


class TestCustomRename:

    @patch("routes.files.is_critical_path", return_value=False)
    def test_custom_rename(self, mock_crit, client, tmp_path):
        old = tmp_path / "Comic (2020) (Digital).cbz"
        old.write_bytes(b"data")
        new = str(tmp_path / "Comic (2020).cbz")

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/custom-rename",
                               json={"old": str(old), "new": new})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_dest_exists(self, client, tmp_path):
        old = tmp_path / "old.cbz"
        old.write_bytes(b"data")
        new = tmp_path / "existing.cbz"
        new.write_bytes(b"other")

        with patch("routes.files.is_critical_path", return_value=False):
            resp = client.post("/custom-rename",
                               json={"old": str(old), "new": str(new)})
        assert resp.status_code == 400


class TestApplyRenamePattern:

    def test_missing_path(self, client):
        resp = client.post("/apply-rename-pattern", json={})
        assert resp.status_code == 400

    def test_source_not_exists(self, client):
        resp = client.post("/apply-rename-pattern", json={"path": "/nonexistent.cbz"})
        assert resp.status_code == 404

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("cbz_ops.rename.rename_file_using_custom_pattern")
    def test_success(self, mock_rename, mock_crit, client, tmp_path):
        old = tmp_path / "old.cbz"
        old.write_bytes(b"data")
        new = tmp_path / "new.cbz"
        mock_rename.return_value = (str(new), True)

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-rename-pattern", json={"path": str(old)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["renamed"] is True
        assert data["new_path"] == str(new)
        assert data["new_name"] == "new.cbz"

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("cbz_ops.rename.rename_file_using_custom_pattern")
    def test_no_change_needed(self, mock_rename, mock_crit, client, tmp_path):
        old = tmp_path / "already.cbz"
        old.write_bytes(b"data")
        mock_rename.return_value = (str(old), False)

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-rename-pattern", json={"path": str(old)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["renamed"] is False
        assert "already matches" in data["message"]

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("cbz_ops.rename.rename_file_using_custom_pattern", side_effect=ValueError("No usable local metadata found"))
    def test_validation_error(self, mock_rename, mock_crit, client, tmp_path):
        old = tmp_path / "unknown.cbz"
        old.write_bytes(b"data")

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-rename-pattern", json={"path": str(old)})

        assert resp.status_code == 400
        assert "No usable local metadata found" in resp.get_json()["error"]

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("cbz_ops.rename.rename_file_using_custom_pattern")
    def test_directory_success_with_mixed_results(self, mock_rename, mock_crit, client, tmp_path):
        folder = tmp_path / "incoming"
        folder.mkdir()
        file1 = folder / "one.cbz"
        file2 = folder / "two.cbr"
        file3 = folder / "three.zip"
        ignored = folder / "notes.txt"
        file1.write_bytes(b"1")
        file2.write_bytes(b"2")
        file3.write_bytes(b"3")
        ignored.write_text("ignore")

        renamed1 = folder / "Series A 001.cbz"
        renamed3 = folder / "Series C 003.zip"
        mock_rename.side_effect = [
            (str(renamed1), True),
            (str(file2), False),
            ValueError("No usable local metadata found"),
        ]

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-rename-pattern", json={"path": str(folder)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["bulk"] is True
        assert data["processed_count"] == 3
        assert data["renamed_count"] == 1
        assert data["skipped_count"] == 1
        assert data["failed_count"] == 1
        assert len(data["results"]) == 3
        assert "1 renamed, 1 skipped, 1 failed" in data["message"]
        assert mock_rename.call_count == 3
        mock_app.update_index_on_move.assert_called_once_with(str(file1), str(renamed1))

    @patch("routes.files.is_critical_path", return_value=False)
    def test_directory_without_comics(self, mock_crit, client, tmp_path):
        folder = tmp_path / "incoming"
        folder.mkdir()
        (folder / "notes.txt").write_text("ignore")

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-rename-pattern", json={"path": str(folder)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["bulk"] is True
        assert data["processed_count"] == 0
        assert data["renamed_count"] == 0
        assert data["failed_count"] == 0
        assert "No comic files found" in data["message"]


class TestApplyFolderRenamePattern:

    def test_missing_path(self, client):
        resp = client.post("/apply-folder-rename-pattern", json={})
        assert resp.status_code == 400

    def test_source_not_exists(self, client):
        resp = client.post("/apply-folder-rename-pattern", json={"path": "/nonexistent.cbz"})
        assert resp.status_code == 404

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("cbz_ops.rename.move_and_rename_file_using_custom_patterns")
    def test_success(self, mock_move_and_rename, mock_crit, client, tmp_path):
        old = tmp_path / "old.cbz"
        old.write_bytes(b"data")
        new = tmp_path / "Publisher" / "Series (2024)" / "Series 007 (2024).cbz"
        mock_move_and_rename.return_value = (str(new), True)

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-folder-rename-pattern", json={"path": str(old)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["updated"] is True
        assert data["new_path"] == str(new)
        assert data["new_name"] == "Series 007 (2024).cbz"

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("cbz_ops.rename.move_and_rename_file_using_custom_patterns")
    def test_no_change_needed(self, mock_move_and_rename, mock_crit, client, tmp_path):
        old = tmp_path / "already.cbz"
        old.write_bytes(b"data")
        mock_move_and_rename.return_value = (str(old), False)

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-folder-rename-pattern", json={"path": str(old)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["updated"] is False
        assert "already matches" in data["message"]

    @patch("routes.files.is_critical_path", return_value=False)
    @patch(
        "cbz_ops.rename.move_and_rename_file_using_custom_patterns",
        side_effect=ValueError("Custom folder pattern is not configured."),
    )
    def test_validation_error(self, mock_move_and_rename, mock_crit, client, tmp_path):
        old = tmp_path / "unknown.cbz"
        old.write_bytes(b"data")

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-folder-rename-pattern", json={"path": str(old)})

        assert resp.status_code == 400
        assert "Custom folder pattern is not configured." in resp.get_json()["error"]

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("cbz_ops.rename.move_and_rename_file_using_custom_patterns")
    def test_directory_success_with_mixed_results(self, mock_move_and_rename, mock_crit, client, tmp_path):
        folder = tmp_path / "incoming"
        folder.mkdir()
        file1 = folder / "one.cbz"
        file2 = folder / "two.cbr"
        file3 = folder / "three.zip"
        ignored = folder / "notes.txt"
        file1.write_bytes(b"1")
        file2.write_bytes(b"2")
        file3.write_bytes(b"3")
        ignored.write_text("ignore")

        moved1 = tmp_path / "Publisher" / "Series A 001.cbz"
        moved3 = tmp_path / "Publisher" / "Series C 003.zip"
        mock_move_and_rename.side_effect = [
            (str(moved1), True),
            (str(file2), False),
            ValueError("No usable local metadata found"),
        ]

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-folder-rename-pattern", json={"path": str(folder)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["bulk"] is True
        assert data["processed_count"] == 3
        assert data["updated_count"] == 1
        assert data["skipped_count"] == 1
        assert data["failed_count"] == 1
        assert len(data["results"]) == 3
        assert "1 updated, 1 skipped, 1 failed" in data["message"]
        assert mock_move_and_rename.call_count == 3
        mock_app.update_index_on_move.assert_called_once_with(str(file1), str(moved1))

    @patch("routes.files.is_critical_path", return_value=False)
    def test_directory_without_comics(self, mock_crit, client, tmp_path):
        folder = tmp_path / "incoming"
        folder.mkdir()
        (folder / "notes.txt").write_text("ignore")

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/apply-folder-rename-pattern", json={"path": str(folder)})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["bulk"] is True
        assert data["processed_count"] == 0
        assert data["updated_count"] == 0
        assert data["failed_count"] == 0
        assert "No comic files found" in data["message"]


class TestSmartRenamePreview:

    def test_missing_directory(self, client):
        resp = client.post("/smart-rename/preview", json={})
        assert resp.status_code == 400

    def test_nonexistent_directory(self, client):
        resp = client.post("/smart-rename/preview",
                           json={"directory": "/nope/abs/path"})
        assert resp.status_code == 404

    @patch("routes.files.is_critical_path", return_value=True)
    @patch("routes.files.get_critical_path_error_message", return_value="Protected")
    def test_critical_path_rejected(self, mock_msg, mock_crit, client, tmp_path):
        resp = client.post("/smart-rename/preview",
                           json={"directory": str(tmp_path)})
        assert resp.status_code == 403

    @patch("routes.files.is_critical_path", return_value=False)
    def test_returns_plan(self, mock_crit, client, tmp_path):
        # Build a directory with cvinfo + series.json + one file
        d = tmp_path / "Sandman"
        d.mkdir()
        (d / "cvinfo").write_text("https://comicvine.gamespot.com/volume/4050-1/\n")
        (d / "series.json").write_text(
            '{"metadata": {"name": "Sandman", "volume": 2, "year": 1989}}'
        )
        (d / "Sandman 1.cbz").write_bytes(b"x")

        with patch("core.database.get_user_preference",
                   side_effect=lambda key, default=None: {
                       "enable_custom_rename": True,
                       "custom_rename_pattern": "{series_name} {volume_number} {issue_number} ({year})",
                       "smart_rename_recursive": False,
                   }.get(key, default)):
            resp = client.post("/smart-rename/preview",
                               json={"directory": str(d), "recursive": False})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["plan"]["directories"]) == 1
        dir_entry = data["plan"]["directories"][0]
        assert dir_entry["status"] == "ok"
        # The file should still exist (preview does not rename)
        assert (d / "Sandman 1.cbz").exists()


class TestSmartRenameApply:

    @patch("routes.files.is_critical_path", return_value=False)
    def test_apply_renames_files(self, mock_crit, client, tmp_path):
        d = tmp_path / "Sandman"
        d.mkdir()
        (d / "cvinfo").write_text("https://comicvine.gamespot.com/volume/4050-1/\n")
        (d / "series.json").write_text(
            '{"metadata": {"name": "Sandman", "volume": 2, "year": 1989}}'
        )
        (d / "Sandman 1.cbz").write_bytes(b"x")

        with patch("core.database.get_user_preference",
                   side_effect=lambda key, default=None: {
                       "enable_custom_rename": True,
                       "custom_rename_pattern": "{series_name} {volume_number} {issue_number} ({year})",
                   }.get(key, default)):
            resp = client.post("/smart-rename",
                               json={"directory": str(d), "recursive": False})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["summary"]["renamed"] == 1
        assert (d / "Sandman v2 001 (1989).cbz").exists()

    def test_apply_with_invalid_plan(self, client):
        resp = client.post("/smart-rename",
                           json={"plan": {"root": "/nonexistent/path"}})
        assert resp.status_code == 400


class TestDelete:

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.move_to_trash", return_value={"trashed": True, "path": "/trash/delete_me.cbz"})
    def test_delete_file(self, mock_trash, mock_crit, client, tmp_path):
        f = tmp_path / "delete_me.cbz"
        f.write_bytes(b"data")

        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/delete", json={"target": str(f)})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["trashed"] is True
        mock_trash.assert_called_once_with(str(f))

    def test_delete_missing_target(self, client):
        resp = client.post("/delete", json={})
        assert resp.status_code == 400

    def test_delete_nonexistent(self, client):
        resp = client.post("/delete", json={"target": "/nonexistent"})
        assert resp.status_code == 404

    @patch("routes.files.is_critical_path", return_value=True)
    @patch("routes.files.get_critical_path_error_message", return_value="No")
    def test_delete_critical(self, mock_msg, mock_crit, client, tmp_path):
        f = tmp_path / "critical.cbz"
        f.write_bytes(b"data")
        resp = client.post("/delete", json={"target": str(f)})
        assert resp.status_code == 403


class TestDeleteMultiple:

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.move_to_trash", return_value={"trashed": True, "path": "/trash/x"})
    def test_bulk_delete(self, mock_trash, mock_crit, client, tmp_path):
        f1 = tmp_path / "a.cbz"
        f2 = tmp_path / "b.cbz"
        f1.write_bytes(b"data")
        f2.write_bytes(b"data")

        with patch("core.database.delete_file_index_entries"):
            resp = client.post("/api/delete-multiple",
                               json={"targets": [str(f1), str(f2)]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert all(r["success"] for r in data["results"])
        assert all(r["trashed"] for r in data["results"])
        assert mock_trash.call_count == 2

    def test_empty_targets(self, client):
        resp = client.post("/api/delete-multiple", json={"targets": []})
        assert resp.status_code == 400


class TestTrashInfo:

    @patch("routes.files.get_trash_contents", return_value=[])
    @patch("routes.files.get_trash_size", return_value=0)
    @patch("routes.files.get_trash_max_size_bytes", return_value=1073741824)
    @patch("routes.files.get_trash_dir", return_value="/cache/trash")
    def test_trash_info(self, mock_dir, mock_max, mock_size, mock_contents, client):
        resp = client.get("/api/trash/info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True
        assert data["item_count"] == 0
        assert data["size"] == 0


class TestTrashList:

    @patch("routes.files.get_trash_manifest", return_value={
        "a.cbz": {"original_path": "/data/comics/a.cbz", "deleted_at": 1000},
    })
    @patch("routes.files.get_trash_contents", return_value=[
        {"name": "a.cbz", "path": "/trash/a.cbz", "size": 100, "is_dir": False, "mtime": 1000},
    ])
    def test_trash_list(self, mock_contents, mock_manifest, client):
        resp = client.get("/api/trash/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "a.cbz"
        assert data["items"][0]["original_path"] == "/data/comics/a.cbz"

    @patch("routes.files.get_trash_manifest", return_value={})
    @patch("routes.files.get_trash_contents", return_value=[
        {"name": "old.cbz", "path": "/trash/old.cbz", "size": 50, "is_dir": False, "mtime": 900},
    ])
    def test_trash_list_no_manifest_entry(self, mock_contents, mock_manifest, client):
        """Items without manifest entry have original_path=None."""
        resp = client.get("/api/trash/list")
        data = resp.get_json()
        assert data["items"][0]["original_path"] is None


class TestTrashEmpty:

    @patch("routes.files.do_empty_trash", return_value={"count": 3, "size_freed": 500})
    def test_empty_trash(self, mock_empty, client):
        resp = client.post("/api/trash/empty")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["count"] == 3
        assert data["size_freed"] == 500


class TestTrashDeleteItem:

    @patch("routes.files.permanently_delete_from_trash", return_value={"success": True, "size_freed": 100})
    def test_delete_item(self, mock_del, client):
        resp = client.post("/api/trash/delete", json={"name": "file.cbz"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_del.assert_called_once_with("file.cbz")

    def test_missing_name(self, client):
        resp = client.post("/api/trash/delete", json={})
        assert resp.status_code == 400

    @patch("routes.files.permanently_delete_from_trash",
           return_value={"success": False, "size_freed": 0, "error": "Item not found in trash"})
    def test_not_found(self, mock_del, client):
        resp = client.post("/api/trash/delete", json={"name": "nope.cbz"})
        assert resp.status_code == 404


class TestTrashRestore:

    @patch("routes.files.restore_from_trash",
           return_value={"success": True, "restored_path": "/data/DC/Batman/issue1.cbz"})
    def test_restore_item(self, mock_restore, client):
        resp = client.post("/api/trash/restore", json={"name": "issue1.cbz"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["restored_path"] == "/data/DC/Batman/issue1.cbz"
        mock_restore.assert_called_once_with("issue1.cbz")

    def test_missing_name(self, client):
        resp = client.post("/api/trash/restore", json={})
        assert resp.status_code == 400

    @patch("routes.files.restore_from_trash",
           return_value={"success": False, "error": "A file already exists at the original location",
                         "conflict": True, "original_path": "/data/DC/issue1.cbz"})
    def test_conflict(self, mock_restore, client):
        resp = client.post("/api/trash/restore", json={"name": "issue1.cbz"})
        assert resp.status_code == 409
        data = resp.get_json()
        assert data.get("conflict") is True

    @patch("routes.files.restore_from_trash",
           return_value={"success": False,
                         "error": "No original path recorded for this item. Use drag-and-drop to restore it manually.",
                         "no_manifest": True})
    def test_no_manifest(self, mock_restore, client):
        resp = client.post("/api/trash/restore", json={"name": "orphan.cbz"})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("no_manifest") is True


class TestCreateFolder:

    @patch("routes.files.is_critical_path", return_value=False)
    def test_create_folder(self, mock_crit, client, tmp_path):
        new_dir = str(tmp_path / "new_folder")
        mock_app = MagicMock()
        with patch.dict("sys.modules", {"app": mock_app}):
            resp = client.post("/create-folder", json={"path": new_dir})
        assert resp.status_code == 200
        assert os.path.isdir(new_dir)

    def test_no_path(self, client):
        resp = client.post("/create-folder", json={})
        assert resp.status_code == 400


class TestCombineCbz:

    def test_invalid_body(self, client):
        resp = client.post("/api/combine-cbz", json=[])
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid request body"

    def test_missing_body(self, client):
        resp = client.post("/api/combine-cbz")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid request body"

    def test_too_few_files(self, client):
        resp = client.post("/api/combine-cbz",
                           json={"files": ["/a.cbz"], "directory": "/tmp"})
        assert resp.status_code == 400

    def test_no_directory(self, client):
        resp = client.post("/api/combine-cbz",
                           json={"files": ["/a.cbz", "/b.cbz"]})
        assert resp.status_code == 400

    def test_files_must_be_list(self, client):
        resp = client.post("/api/combine-cbz",
                           json={"files": 123, "directory": "/tmp"})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Files must be a list of paths"

    def test_files_must_be_strings(self, client):
        resp = client.post("/api/combine-cbz",
                           json={"files": [["/a.cbz"], "/b.cbz"], "directory": "/tmp"})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Files must be a list of paths"

    def test_directory_must_be_string(self, client):
        resp = client.post("/api/combine-cbz",
                           json={"files": ["/a.cbz", "/b.cbz"], "directory": []})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Directory not specified"

    def test_output_name_traversal_rejected(self, client):
        resp = client.post("/api/combine-cbz", json={
            "files": ["/a.cbz", "/b.cbz"],
            "output_name": "../outside",
            "directory": "/tmp",
        })

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid output name"

    def test_output_name_non_string_rejected(self, client):
        resp = client.post("/api/combine-cbz", json={
            "files": ["/a.cbz", "/b.cbz"],
            "output_name": [],
            "directory": "/tmp",
        })

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid output name"

    def test_output_name_nul_rejected(self, client):
        resp = client.post("/api/combine-cbz", json={
            "files": ["/a.cbz", "/b.cbz"],
            "output_name": "bad\u0000name",
            "directory": "/tmp",
        })

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid output name"

    def test_file_path_nul_rejected(self, client):
        resp = client.post("/api/combine-cbz", json={
            "files": ["/tmp/a\u0000.cbz", "/tmp/b.cbz"],
            "output_name": "Combined",
            "directory": "/tmp",
        })

        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Access denied"

    @patch("routes.files.is_valid_library_path", side_effect=[True, True])
    def test_directory_nul_rejected(self, mock_valid, client):
        resp = client.post("/api/combine-cbz", json={
            "files": ["/tmp/a.cbz", "/tmp/b.cbz"],
            "output_name": "Combined",
            "directory": "/tmp/bad\u0000dir",
        })

        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Access denied"

    @patch("routes.files.is_path_in_any_root", return_value=False)
    @patch("routes.files.is_valid_library_path", side_effect=[True, True, False])
    def test_output_directory_outside_allowed_roots_blocked(
        self,
        mock_valid,
        mock_any_root,
        client,
        tmp_path,
    ):
        cbz1 = str(tmp_path / "part1.cbz")
        cbz2 = str(tmp_path / "part2.cbz")
        outside = str(tmp_path / "outside")

        resp = client.post("/api/combine-cbz", json={
            "files": [cbz1, cbz2],
            "output_name": "Combined",
            "directory": outside,
        })

        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Access denied"

    @patch("routes.files.is_valid_library_path", return_value=True)
    @patch("routes.files.config")
    def test_combine_success(self, mock_config, mock_valid, client, create_cbz, tmp_path):
        mock_config.get.return_value = str(tmp_path)
        cbz1 = create_cbz("part1.cbz", num_images=2)
        cbz2 = create_cbz("part2.cbz", num_images=2)

        resp = client.post("/api/combine-cbz", json={
            "files": [cbz1, cbz2],
            "output_name": "Combined",
            "directory": str(tmp_path),
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["total_images"] == 4


class TestCrop:

    def test_missing_params(self, client):
        resp = client.post("/crop", json={})
        assert resp.status_code == 400

    def test_invalid_crop_type(self, client):
        resp = client.post("/crop",
                           json={"target": "/img.jpg", "cropType": "invalid"})
        assert resp.status_code == 400


class TestGetImageData:

    def test_missing_path(self, client):
        resp = client.post("/get-image-data", json={})
        assert resp.status_code == 400

    def test_file_not_found(self, client):
        resp = client.post("/get-image-data", json={"target": "/nonexistent.jpg"})
        assert resp.status_code == 404

    def test_valid_image(self, client, tmp_path):
        from PIL import Image
        img_path = str(tmp_path / "test.jpg")
        Image.new("RGB", (10, 10), "blue").save(img_path)

        resp = client.post("/get-image-data", json={"target": img_path})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["imageData"].startswith("data:image/jpeg")

class TestReplaceImage:

    @staticmethod
    def _make_upload(color="red", image_format="PNG"):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (12, 8), color).save(buffer, format=image_format)
        buffer.seek(0)
        return buffer

    def test_missing_target_file(self, client):
        data = {
            "replacement_image": (self._make_upload(), "replacement.png"),
        }

        resp = client.post("/replace-image", data=data, content_type="multipart/form-data")

        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_missing_replacement_image(self, client, tmp_path):
        target = tmp_path / "page01.jpg"
        target.write_bytes(b"not-an-image")

        resp = client.post(
            "/replace-image",
            data={"target_file": str(target)},
            content_type="multipart/form-data",
        )

        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.is_valid_library_path", return_value=True)
    def test_target_not_found(self, mock_valid, mock_critical, client):
        data = {
            "target_file": "/nonexistent/page01.jpg",
            "replacement_image": (self._make_upload(), "replacement.png"),
        }

        resp = client.post("/replace-image", data=data, content_type="multipart/form-data")

        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.is_path_in_any_root", return_value=False)
    @patch("routes.files.is_valid_library_path", return_value=False)
    def test_access_denied_outside_allowed_roots(self, mock_valid, mock_any_root, mock_critical, client, tmp_path):
        from PIL import Image

        target = tmp_path / "page01.jpg"
        Image.new("RGB", (10, 10), "blue").save(target)

        data = {
            "target_file": str(target),
            "replacement_image": (self._make_upload(), "replacement.png"),
        }

        resp = client.post("/replace-image", data=data, content_type="multipart/form-data")

        assert resp.status_code == 403
        assert resp.get_json()["success"] is False

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.is_valid_library_path", return_value=True)
    def test_invalid_replacement_extension(self, mock_valid, mock_critical, client, tmp_path):
        from PIL import Image

        target = tmp_path / "page01.jpg"
        Image.new("RGB", (10, 10), "blue").save(target)

        data = {
            "target_file": str(target),
            "replacement_image": (io.BytesIO(b"plain text"), "replacement.txt"),
        }

        resp = client.post("/replace-image", data=data, content_type="multipart/form-data")

        assert resp.status_code == 400
        assert "not allowed" in resp.get_json()["error"]

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.is_valid_library_path", return_value=True)
    def test_replace_success(self, mock_valid, mock_critical, client, tmp_path):
        from PIL import Image

        target = tmp_path / "page01.jpg"
        Image.new("RGB", (10, 10), "blue").save(target)

        data = {
            "target_file": str(target),
            "replacement_image": (self._make_upload(color="red"), "replacement.png"),
        }

        resp = client.post("/replace-image", data=data, content_type="multipart/form-data")

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["success"] is True
        assert payload["path"] == str(target)
        assert payload["imageData"].startswith("data:image/png;base64,")

        with Image.open(target) as replaced:
            assert replaced.size == (12, 8)
            pixel = replaced.convert("RGB").getpixel((0, 0))
            assert pixel[0] > pixel[2]


class TestConvertPreview:

    def test_missing_directory(self, client):
        resp = client.get("/api/convert/preview")
        assert resp.status_code == 400

    @patch("routes.files.is_valid_library_path", return_value=False)
    def test_outside_library(self, mock_valid, client, tmp_path):
        resp = client.get(f"/api/convert/preview?directory={tmp_path}")
        assert resp.status_code == 403
        assert resp.get_json()["success"] is False

    @patch("routes.files.is_valid_library_path", return_value=True)
    def test_nonexistent_directory(self, mock_valid, client):
        resp = client.get("/api/convert/preview?directory=/nonexistent/path")
        assert resp.status_code == 404

    @patch("routes.files.is_valid_library_path", return_value=True)
    def test_counts_recursively(self, mock_valid, client, tmp_path):
        # Top-level .cbr
        (tmp_path / "top.cbr").write_bytes(b"x")
        # Nested .rar two levels deep — must be found because the endpoint
        # forces recursion regardless of the CONVERT_SUBDIRECTORIES flag.
        nested = tmp_path / "pubA" / "series"
        nested.mkdir(parents=True)
        (nested / "issue.rar").write_bytes(b"x")
        (nested / "ignored.cbz").write_bytes(b"x")

        resp = client.get(f"/api/convert/preview?directory={tmp_path}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["count"] == 2
        assert data["directory"] == str(tmp_path)

    @patch("routes.files.is_valid_library_path", return_value=True)
    def test_zero_count(self, mock_valid, client, tmp_path):
        (tmp_path / "already.cbz").write_bytes(b"x")
        resp = client.get(f"/api/convert/preview?directory={tmp_path}")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0


class TestUploadToFolderOpsIntegration:
    """The upload route should register an operation with app_state so the
    Global Operations Indicator in the navbar can show progress."""

    def test_upload_registers_operation_and_returns_op_id(self, client, tmp_path):
        from io import BytesIO

        target = tmp_path / "uploads"
        target.mkdir()

        # PNG signature is the only thing the route inspects (via extension).
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        data = {
            "target_dir": str(target),
            "files": [(BytesIO(png_bytes), "a.png"), (BytesIO(png_bytes), "b.png")],
        }

        resp = client.post(
            "/upload-to-folder",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["total_uploaded"] == 2
        assert body.get("op_id"), "upload response must include op_id"

        ops = client.get("/api/operations").get_json()["operations"]
        match = next((o for o in ops if o["id"] == body["op_id"]), None)
        assert match is not None, "registered op should appear in /api/operations"
        assert match["op_type"] == "upload"
        # On success the framework clamps current to total
        assert match["current"] == match["total"] == 2
        assert match["status"] == "completed"

    def test_upload_no_target_dir_does_not_register_op(self, client):
        # Validation failure should not register an op, regardless of any ops
        # left over from prior tests in the session.
        before = {o["id"] for o in client.get("/api/operations").get_json()["operations"]}

        resp = client.post(
            "/upload-to-folder",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

        after = {o["id"] for o in client.get("/api/operations").get_json()["operations"]}
        assert after.issubset(before), "validation failure must not register a new op"


class TestCropCover:

    def test_missing_target(self, client):
        resp = client.post("/crop-cover", json={})
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_file_not_found(self, client):
        resp = client.post("/crop-cover", json={"target": "/nope/missing.cbz"})
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    def test_non_cbz_rejected(self, client, tmp_path):
        f = tmp_path / "not_a_cbz.txt"
        f.write_text("hello")
        resp = client.post("/crop-cover", json={"target": str(f)})
        assert resp.status_code == 400
        assert "not a CBZ" in resp.get_json()["error"]

    @patch("routes.files.is_critical_path", return_value=True)
    @patch("routes.files.get_critical_path_error_message", return_value="Protected")
    def test_critical_path_blocked(self, mock_msg, mock_crit, client, tmp_path):
        f = tmp_path / "comic.cbz"
        f.write_bytes(b"fake")
        resp = client.post("/crop-cover", json={"target": str(f)})
        assert resp.status_code == 403

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.is_path_in_any_root", return_value=False)
    @patch("routes.files.is_valid_library_path", return_value=False)
    def test_outside_allowed_roots_blocked(
        self,
        mock_valid,
        mock_any_root,
        mock_crit,
        client,
        tmp_path,
    ):
        f = tmp_path / "comic.cbz"
        f.write_bytes(b"fake")
        resp = client.post("/crop-cover", json={"target": str(f)})
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Access denied"

    @patch("routes.files.is_critical_path", return_value=False)
    @patch("routes.files.is_valid_library_path", return_value=True)
    @patch("cbz_ops.crop.handle_cbz_file")
    def test_crop_success(self, mock_handle, mock_valid, mock_crit, client, tmp_path):
        f = tmp_path / "comic.cbz"
        f.write_bytes(b"fake")
        resp = client.post("/crop-cover", json={"target": str(f)})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_handle.assert_called_once_with(str(f))
