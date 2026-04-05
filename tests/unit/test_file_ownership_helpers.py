import errno
import os
import stat
from unittest.mock import patch

from helpers import capture_file_ownership, restore_file_ownership


def test_capture_file_ownership_returns_uid_gid_and_mode(tmp_path):
    file_path = tmp_path / "sample.cbz"
    file_path.write_bytes(b"cbz")
    file_path.chmod(0o640)

    ownership = capture_file_ownership(str(file_path))

    assert ownership is not None
    assert ownership["uid"] == os.stat(file_path).st_uid
    assert ownership["gid"] == os.stat(file_path).st_gid
    assert ownership["mode"] == stat.S_IMODE(os.stat(file_path).st_mode)


def test_capture_file_ownership_missing_file_returns_none(tmp_path):
    missing_path = tmp_path / "missing.cbz"

    assert capture_file_ownership(str(missing_path)) is None


@patch("helpers.os.chmod")
@patch("helpers.os.chown")
def test_restore_file_ownership_applies_mode_uid_and_gid(mock_chown, mock_chmod, tmp_path):
    file_path = tmp_path / "sample.cbz"
    file_path.write_bytes(b"cbz")

    restore_file_ownership(
        str(file_path),
        {"uid": 123, "gid": 456, "mode": 0o644},
    )

    mock_chmod.assert_called_once_with(str(file_path), 0o644)
    if os.name != "nt":
        mock_chown.assert_called_once_with(str(file_path), 123, 456)


@patch("helpers.os.chown", side_effect=PermissionError(errno.EPERM, "nope"))
@patch("helpers.os.chmod")
def test_restore_file_ownership_tolerates_permission_error(mock_chmod, mock_chown, tmp_path):
    file_path = tmp_path / "sample.cbz"
    file_path.write_bytes(b"cbz")

    restore_file_ownership(
        str(file_path),
        {"uid": 1, "gid": 1, "mode": 0o600},
    )

    mock_chmod.assert_called_once_with(str(file_path), 0o600)
    if os.name != "nt":
        mock_chown.assert_called_once()
