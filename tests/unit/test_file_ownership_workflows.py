import os
from unittest.mock import patch

from cbz_ops.convert import convert_single_rar_file
from cbz_ops.pdf import create_cbz_file


@patch("cbz_ops.convert.restore_file_ownership")
@patch("cbz_ops.convert.capture_file_ownership", return_value={"uid": 123, "gid": 456, "mode": 0o644})
@patch("cbz_ops.convert.extract_rar_with_unar")
def test_convert_single_rar_file_restores_output_ownership(mock_extract, mock_capture, mock_restore, tmp_path):
    rar_path = tmp_path / "sample.cbr"
    rar_path.write_bytes(b"rar")
    temp_dir = tmp_path / "extract"
    cbz_path = tmp_path / "sample.cbz"

    def fake_extract(_rar_path, extraction_dir):
        os.makedirs(extraction_dir, exist_ok=True)
        with open(os.path.join(extraction_dir, "001.jpg"), "wb") as handle:
            handle.write(b"image")
        return True, 0

    mock_extract.side_effect = fake_extract

    assert convert_single_rar_file(str(rar_path), str(cbz_path), str(temp_dir)) is True

    mock_capture.assert_called_once_with(str(rar_path))
    mock_restore.assert_called_once_with(str(cbz_path), {"uid": 123, "gid": 456, "mode": 0o644})


@patch("cbz_ops.pdf.restore_file_ownership")
def test_create_cbz_file_restores_output_ownership(mock_restore, tmp_path):
    output_folder = tmp_path / "pages"
    output_folder.mkdir()
    (output_folder / "001.jpg").write_bytes(b"page")
    cbz_path = tmp_path / "sample.cbz"
    ownership = {"uid": 123, "gid": 456, "mode": 0o600}

    create_cbz_file(str(output_folder), str(cbz_path), ownership=ownership)

    assert cbz_path.exists()
    mock_restore.assert_called_once_with(str(cbz_path), ownership)
