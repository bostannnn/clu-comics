import io
import zipfile
from unittest.mock import patch

from flask import Flask
from PIL import Image

from cbz_ops.edit import process_cbz_file, save_cbz


def _make_jpeg_bytes(color="blue"):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 18), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _write_cbz(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)


class TestCbzEditFlow:

    def test_process_cbz_file_keeps_root_when_comicinfo_exists_at_archive_root(self, tmp_path):
        cbz_path = tmp_path / "comic.cbz"
        _write_cbz(
            cbz_path,
            {
                "ComicInfo.xml": "<ComicInfo/>",
                "pages/page01.jpg": _make_jpeg_bytes(),
            },
        )

        result = process_cbz_file(str(cbz_path))

        extraction_root = tmp_path / "comic_folder"
        assert result["folder_name"] == str(extraction_root)
        assert (extraction_root / "ComicInfo.xml").exists()
        assert (extraction_root / "pages" / "page01.jpg").exists()

    def test_save_cbz_preserves_root_level_comicinfo_for_nested_edit_sessions(self, tmp_path):
        app = Flask(__name__)
        original_file_path = tmp_path / "comic.cbz"
        zip_file_path = tmp_path / "comic.zip"
        extraction_root = tmp_path / "comic_folder"
        nested_pages = extraction_root / "pages"

        original_file_path.write_bytes(b"original")
        zip_file_path.write_bytes(b"zip-backup")
        nested_pages.mkdir(parents=True)
        (extraction_root / "ComicInfo.xml").write_text("<ComicInfo/>", encoding="utf-8")
        (nested_pages / "page01.jpg").write_bytes(_make_jpeg_bytes())

        with app.test_request_context(
            "/save",
            method="POST",
            data={
                "folder_name": str(nested_pages),
                "zip_file_path": str(zip_file_path),
                "original_file_path": str(original_file_path),
            },
        ):
            with patch("cbz_ops.edit.capture_file_ownership", return_value=None), patch(
                "cbz_ops.edit.restore_file_ownership"
            ):
                response = save_cbz()

        assert response.get_json()["success"] is True

        with zipfile.ZipFile(original_file_path, "r") as zf:
            names = sorted(zf.namelist())

        assert "ComicInfo.xml" in names
        assert "pages/page01.jpg" in names
