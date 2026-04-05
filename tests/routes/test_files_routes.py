"""Tests for routes/files.py -- file operations endpoints."""
import os
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

    @patch("routes.files.get_trash_contents", return_value=[
        {"name": "a.cbz", "path": "/trash/a.cbz", "size": 100, "is_dir": False, "mtime": 1000},
    ])
    def test_trash_list(self, mock_contents, client):
        resp = client.get("/api/trash/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "a.cbz"


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

    def test_too_few_files(self, client):
        resp = client.post("/api/combine-cbz",
                           json={"files": ["/a.cbz"], "directory": "/tmp"})
        assert resp.status_code == 400

    def test_no_directory(self, client):
        resp = client.post("/api/combine-cbz",
                           json={"files": ["/a.cbz", "/b.cbz"]})
        assert resp.status_code == 400

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
