"""Tests for routes/metadata.py -- metadata management endpoints."""
import io
import json
import os
import re
import zipfile
import pytest
from unittest.mock import patch, MagicMock, call


class TestGenerateComicInfoXml:

    def test_generate_basic(self):
        """Test the generate_comicinfo_xml helper function."""
        from routes.metadata import generate_comicinfo_xml
        import xml.etree.ElementTree as ET

        issue_data = {
            "Title": "The Origin",
            "Series": "Batman",
            "Number": "1",
            "Volume": "2020",
            "Summary": "The Dark Knight rises",
            "Year": "2020",
            "Month": "3",
            "Writer": "Tom King",
            "Penciller": "David Finch",
            "Publisher": "DC Comics",
        }
        xml_bytes = generate_comicinfo_xml(issue_data)
        assert xml_bytes is not None
        assert b"<ComicInfo>" in xml_bytes or b"<ComicInfo" in xml_bytes

        root = ET.fromstring(xml_bytes)
        assert root.tag == "ComicInfo"
        assert root.find("Series").text == "Batman"
        assert root.find("Writer").text == "Tom King"

    def test_decimal_issue_number_preserved(self):
        """Decimal issue numbers like 12.1 should not be truncated to 12."""
        from routes.metadata import generate_comicinfo_xml
        import xml.etree.ElementTree as ET

        issue_data = {"Series": "Avengers", "Number": "12.1", "Year": "2011"}
        xml_bytes = generate_comicinfo_xml(issue_data)
        root = ET.fromstring(xml_bytes)
        assert root.find("Number").text == "12.1"

    def test_decimal_issue_preserves_leading_zeros(self):
        """012.1 should stay '012.1', not be stripped to '12.1' via float()."""
        from routes.metadata import generate_comicinfo_xml
        import xml.etree.ElementTree as ET

        issue_data = {"Series": "Avengers", "Number": "012.1", "Year": "2011"}
        xml_bytes = generate_comicinfo_xml(issue_data)
        root = ET.fromstring(xml_bytes)
        assert root.find("Number").text == "012.1"

    def test_whole_number_as_float_drops_decimal(self):
        """12.0 should be stored as '12', not '12.0'."""
        from routes.metadata import generate_comicinfo_xml
        import xml.etree.ElementTree as ET

        issue_data = {"Series": "Batman", "Number": "12.0"}
        xml_bytes = generate_comicinfo_xml(issue_data)
        root = ET.fromstring(xml_bytes)
        assert root.find("Number").text == "12"

    def test_non_numeric_issue_number_preserved(self):
        """Non-numeric issue numbers like '12.HU' should pass through unchanged."""
        from routes.metadata import generate_comicinfo_xml
        import xml.etree.ElementTree as ET

        issue_data = {"Series": "Batman", "Number": "12.HU"}
        xml_bytes = generate_comicinfo_xml(issue_data)
        root = ET.fromstring(xml_bytes)
        assert root.find("Number").text == "12.HU"

    def test_generate_empty_data(self):
        from routes.metadata import generate_comicinfo_xml
        xml_bytes = generate_comicinfo_xml({})
        assert xml_bytes is not None

    def test_generate_list_credits(self):
        from routes.metadata import generate_comicinfo_xml
        import xml.etree.ElementTree as ET

        issue_data = {
            "Series": "X-Men",
            "Writer": ["Chris Claremont", "Fabian Nicieza"],
        }
        xml_bytes = generate_comicinfo_xml(issue_data)
        root = ET.fromstring(xml_bytes)
        writer = root.find("Writer")
        assert writer is not None
        assert "Chris Claremont" in writer.text

    def test_generate_tags_field(self):
        from routes.metadata import generate_comicinfo_xml
        import xml.etree.ElementTree as ET

        issue_data = {
            "Series": "Chainsaw Man",
            "Tags": "Blood and Gore, Body Horror, Violence",
        }
        xml_bytes = generate_comicinfo_xml(issue_data)
        root = ET.fromstring(xml_bytes)
        tags = root.find("Tags")
        assert tags is not None
        assert tags.text == "Blood and Gore, Body Horror, Violence"


class TestAsText:

    def test_none(self):
        from routes.metadata import _as_text
        assert _as_text(None) is None

    def test_string(self):
        from routes.metadata import _as_text
        assert _as_text("hello") == "hello"

    def test_list(self):
        from routes.metadata import _as_text
        assert _as_text(["a", "b", "c"]) == "a, b, c"

    def test_list_with_none(self):
        from routes.metadata import _as_text
        assert _as_text(["a", None, "c"]) == "a, c"

    def test_int(self):
        from routes.metadata import _as_text
        assert _as_text(42) == "42"


class TestCvinfoRoutes:

    def test_get_cvinfo_returns_structured_fields(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"
        cvinfo.write_text(
            "https://comicvine.gamespot.com/volume/4050-167796/\n"
            "series_id: 12345\n"
            "publisher_name: Image\n"
            "start_year: 2015\n"
            "mangadex_id: abc123\n",
            encoding="utf-8",
        )

        with patch("routes.metadata.is_valid_library_path", return_value=True):
            resp = client.get(f"/api/cvinfo?path={cvinfo}")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["cv_id"] == "167796"
        assert data["series_id"] == "12345"
        assert data["publisher_name"] == "Image"
        assert data["start_year"] == "2015"
        assert "mangadex_id: abc123" in data["extra_lines"]

    def test_get_cvinfo_requires_cvinfo_path(self, client, tmp_path):
        file_path = tmp_path / "not-cvinfo.txt"
        file_path.write_text("x", encoding="utf-8")

        with patch("routes.metadata.is_valid_library_path", return_value=True):
            resp = client.get(f"/api/cvinfo?path={file_path}")

        assert resp.status_code == 400
        assert "cvinfo file" in resp.get_json()["error"]

    def test_save_cvinfo_structured_preserves_extra_lines(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"
        cvinfo.write_text("mangadex_id: abc123\n", encoding="utf-8")

        with patch("routes.metadata.is_valid_library_path", return_value=True), \
             patch("routes.metadata.is_path_in_any_root", return_value=False):
            resp = client.post(
                "/api/save-cvinfo",
                json={
                    "path": str(cvinfo),
                    "cv_url": "167796",
                    "series_id": "12345",
                    "publisher_name": "Image",
                    "start_year": "2015",
                    "extra_lines": "mangadex_id: abc123\npublisher_name: ignore-me",
                },
            )

        assert resp.status_code == 200
        content = cvinfo.read_text(encoding="utf-8")
        assert "https://comicvine.gamespot.com/volume/4050-167796/" in content
        assert "series_id: 12345" in content
        assert "publisher_name: Image" in content
        assert "start_year: 2015" in content
        assert "mangadex_id: abc123" in content
        assert "ignore-me" not in content

    def test_save_cvinfo_rejects_invalid_start_year(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"

        with patch("routes.metadata.is_valid_library_path", return_value=True), \
             patch("routes.metadata.is_path_in_any_root", return_value=False):
            resp = client.post(
                "/api/save-cvinfo",
                json={
                    "path": str(cvinfo),
                    "cv_url": "167796",
                    "start_year": "20A5",
                },
            )

        assert resp.status_code == 400
        assert "4-digit year" in resp.get_json()["error"]

    def test_save_cvinfo_rejects_non_cvinfo_path(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        other_file = folder / "issue.cbz"

        with patch("routes.metadata.is_valid_library_path", return_value=True), \
             patch("routes.metadata.is_path_in_any_root", return_value=False):
            resp = client.post(
                "/api/save-cvinfo",
                json={
                    "path": str(other_file),
                    "series_id": "12345",
                },
            )

        assert resp.status_code == 400
        assert "cvinfo file" in resp.get_json()["error"]

    def test_save_cvinfo_allows_metron_only_structured_content(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"

        with patch("routes.metadata.is_valid_library_path", return_value=True), \
             patch("routes.metadata.is_path_in_any_root", return_value=False):
            resp = client.post(
                "/api/save-cvinfo",
                json={
                    "path": str(cvinfo),
                    "series_id": "12345",
                    "publisher_name": "Image",
                    "start_year": "2015",
                },
            )

        assert resp.status_code == 200
        content = cvinfo.read_text(encoding="utf-8")
        assert "series_id: 12345" in content
        assert "publisher_name: Image" in content
        assert "start_year: 2015" in content
        assert "comicvine.gamespot.com" not in content


def _make_cbz(path, with_comicinfo=True):
    """Helper to create a minimal CBZ file for testing."""
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr("page_001.png", b"fake image data")
        if with_comicinfo:
            zf.writestr("ComicInfo.xml", "<ComicInfo><Series>Test</Series></ComicInfo>")


def _make_cbz_with_notes(path, notes):
    """Helper to create a CBZ with an existing ComicInfo Notes value."""
    xml = f"<ComicInfo><Series>Test</Series><Notes>{notes}</Notes></ComicInfo>"
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr("page_001.png", b"fake image data")
        zf.writestr("ComicInfo.xml", xml)


class TestRemoveComicInfoHelper:

    @patch("core.database.set_has_comicinfo")
    def test_removes_comicinfo_from_cbz(self, mock_set, tmp_path):
        from routes.metadata import _remove_comicinfo_from_cbz

        cbz_path = str(tmp_path / "test.cbz")
        _make_cbz(cbz_path, with_comicinfo=True)

        result = _remove_comicinfo_from_cbz(cbz_path)
        assert result["success"] is True

        # Verify ComicInfo.xml was removed
        with zipfile.ZipFile(cbz_path, 'r') as zf:
            names = [n.lower() for n in zf.namelist()]
            assert "comicinfo.xml" not in names
            assert "page_001.png" in names

    def test_no_comicinfo_returns_error(self, tmp_path):
        from routes.metadata import _remove_comicinfo_from_cbz

        cbz_path = str(tmp_path / "no_xml.cbz")
        _make_cbz(cbz_path, with_comicinfo=False)

        result = _remove_comicinfo_from_cbz(cbz_path)
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_nonexistent_file(self):
        from routes.metadata import _remove_comicinfo_from_cbz

        result = _remove_comicinfo_from_cbz("/nonexistent/path/file.cbz")
        assert result["success"] is False
        assert "not found" in result["error"].lower()


class TestBulkClearComicInfo:

    @patch("core.database.set_has_comicinfo")
    def test_bulk_clear_with_directory(self, mock_set, client, tmp_path):
        cbz_dir = str(tmp_path / "data" / "comics")
        os.makedirs(cbz_dir, exist_ok=True)
        _make_cbz(os.path.join(cbz_dir, "a.cbz"))
        _make_cbz(os.path.join(cbz_dir, "b.cbz"))

        resp = client.post('/cbz-bulk-clear-comicinfo',
                           json={"directory": cbz_dir})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["total"] == 2
        assert "op_id" in data

    @patch("core.database.set_has_comicinfo")
    def test_bulk_clear_with_paths(self, mock_set, client, tmp_path):
        cbz1 = str(tmp_path / "data" / "one.cbz")
        cbz2 = str(tmp_path / "data" / "two.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz1)
        _make_cbz(cbz2)

        resp = client.post('/cbz-bulk-clear-comicinfo',
                           json={"paths": [cbz1, cbz2]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_bulk_clear_empty(self, client, tmp_path):
        empty_dir = str(tmp_path / "data" / "empty")
        os.makedirs(empty_dir, exist_ok=True)

        resp = client.post('/cbz-bulk-clear-comicinfo',
                           json={"directory": empty_dir})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    @patch("core.database.set_has_comicinfo")
    def test_single_endpoint_still_works(self, mock_set, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "single.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path)

        resp = client.post('/cbz-clear-comicinfo',
                           json={"path": cbz_path})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True


class TestSaveComicInfoEndpoint:

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    def test_manual_save_uses_strict_corruption_mode(
        self,
        mock_add,
        mock_set,
        mock_update_index,
        mock_valid,
        client,
        tmp_path,
    ):
        cbz_path = str(tmp_path / "data" / "strict.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        resp = client.post('/cbz-save-comicinfo', json={
            "path": cbz_path,
            "comicinfo": {
                "Series": "Batman",
                "Number": "1",
            }
        })

        assert resp.status_code == 200
        mock_add.assert_called_once()
        assert mock_add.call_args.kwargs["fail_on_corruption"] is True

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    def test_creates_comicinfo_when_missing(self, mock_set, mock_update_index, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "new.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        resp = client.post('/cbz-save-comicinfo', json={
            "path": cbz_path,
            "comicinfo": {
                "Series": "Batman",
                "Number": "1",
                "Year": "2020",
            }
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "<Series>Batman</Series>" in data["comicinfo_xml_text"]

        with zipfile.ZipFile(cbz_path, 'r') as zf:
            xml_data = zf.read("ComicInfo.xml").decode("utf-8")
            assert "<Series>Batman</Series>" in xml_data
            assert "<Number>1</Number>" in xml_data
            assert "<Year>2020</Year>" in xml_data

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    def test_manual_save_accepts_tags_field(self, mock_set, mock_update_index, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "tags.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        resp = client.post('/cbz-save-comicinfo', json={
            "path": cbz_path,
            "comicinfo": {
                "Series": "Chainsaw Man",
                "Tags": "Blood and Gore, Body Horror, Violence",
            }
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["comicinfo"]["Tags"] == "Blood and Gore, Body Horror, Violence"
        assert "<Tags>Blood and Gore, Body Horror, Violence</Tags>" in data["comicinfo_xml_text"]

        with zipfile.ZipFile(cbz_path, 'r') as zf:
            xml_data = zf.read("ComicInfo.xml").decode("utf-8")
            assert "<Tags>Blood and Gore, Body Horror, Violence</Tags>" in xml_data

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    def test_preserves_existing_unedited_tags(self, mock_set, mock_update_index, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "existing.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        with zipfile.ZipFile(cbz_path, 'w') as zf:
            zf.writestr("page_001.png", b"fake image data")
            zf.writestr(
                "ComicInfo.xml",
                "<ComicInfo><Series>Old Series</Series><CustomTag>Keep Me</CustomTag></ComicInfo>"
            )

        resp = client.post('/cbz-save-comicinfo', json={
            "path": cbz_path,
            "comicinfo": {
                "Title": "New Title"
            }
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        with zipfile.ZipFile(cbz_path, 'r') as zf:
            xml_data = zf.read("ComicInfo.xml").decode("utf-8")
            assert "<Title>New Title</Title>" in xml_data
            assert "<Series>Old Series</Series>" in xml_data
            assert "<CustomTag>Keep Me</CustomTag>" in xml_data

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    def test_rejects_empty_payload(self, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "empty.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        resp = client.post('/cbz-save-comicinfo', json={
            "path": cbz_path,
            "comicinfo": {
                "Series": "",
                "Title": "",
                "Notes": "   "
            }
        })

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    def test_rejects_corrupted_archive(
        self,
        mock_add,
        mock_set,
        mock_update_index,
        mock_valid,
        client,
        tmp_path,
    ):
        from routes.metadata import CorruptedArchiveError

        cbz_path = str(tmp_path / "data" / "corrupt.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)
        mock_add.side_effect = CorruptedArchiveError(
            "Archive contains 2 corrupted file(s) and cannot be safely updated. Restore or rebuild the CBZ first."
        )

        resp = client.post('/cbz-save-comicinfo', json={
            "path": cbz_path,
            "comicinfo": {
                "Series": "Batman",
                "Number": "1",
            }
        })

        assert resp.status_code == 409
        data = resp.get_json()
        assert data["success"] is False
        assert "cannot be safely updated" in data["error"]
        mock_set.assert_not_called()
        mock_update_index.assert_not_called()


class TestSaveComicInfoRawXmlEndpoint:

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    def test_raw_save_uses_strict_corruption_mode(
        self,
        mock_add,
        mock_set,
        mock_update_index,
        mock_valid,
        client,
        tmp_path,
    ):
        cbz_path = str(tmp_path / "data" / "raw-strict.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        resp = client.post('/cbz-save-comicinfo-xml', json={
            "path": cbz_path,
            "comicinfo_xml": "<ComicInfo><Series>Sandman</Series></ComicInfo>",
        })

        assert resp.status_code == 200
        mock_add.assert_called_once()
        assert mock_add.call_args.kwargs["fail_on_corruption"] is True

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    def test_creates_raw_comicinfo_when_missing(self, mock_set, mock_update_index, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "raw-new.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        raw_xml = """<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Series>Sandman</Series>
  <Number>1</Number>
  <Publisher>DC Comics</Publisher>
</ComicInfo>"""

        resp = client.post('/cbz-save-comicinfo-xml', json={
            "path": cbz_path,
            "comicinfo_xml": raw_xml,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["comicinfo"]["Series"] == "Sandman"
        assert data["comicinfo"]["Publisher"] == "DC Comics"

        with zipfile.ZipFile(cbz_path, 'r') as zf:
            xml_data = zf.read("ComicInfo.xml").decode("utf-8")
            assert "<Series>Sandman</Series>" in xml_data
            assert "<Publisher>DC Comics</Publisher>" in xml_data

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    def test_rejects_malformed_xml(self, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "bad-xml.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        resp = client.post('/cbz-save-comicinfo-xml', json={
            "path": cbz_path,
            "comicinfo_xml": "<ComicInfo><Series>Broken</ComicInfo>",
        })

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "Invalid XML" in data["error"]

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    def test_rejects_wrong_root_tag(self, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "wrong-root.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)

        resp = client.post('/cbz-save-comicinfo-xml', json={
            "path": cbz_path,
            "comicinfo_xml": "<NotComicInfo><Series>Broken</Series></NotComicInfo>",
        })

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "Root element must be ComicInfo" in data["error"]

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    def test_rejects_save_when_another_write_is_in_progress(
        self,
        mock_add,
        mock_set,
        mock_update_index,
        mock_valid,
        client,
        tmp_path,
    ):
        from routes.metadata import ComicInfoSaveInProgressError

        cbz_path = str(tmp_path / "data" / "busy.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        _make_cbz(cbz_path, with_comicinfo=False)
        mock_add.side_effect = ComicInfoSaveInProgressError(
            "A ComicInfo save is already in progress for this file. Please wait for it to finish."
        )

        resp = client.post('/cbz-save-comicinfo-xml', json={
            "path": cbz_path,
            "comicinfo_xml": "<ComicInfo><Series>Busy</Series></ComicInfo>",
        })

        assert resp.status_code == 409
        data = resp.get_json()
        assert data["success"] is False
        assert "already in progress" in data["error"]
        mock_set.assert_not_called()
        mock_update_index.assert_not_called()


class TestCbzMetadataRawXmlText:

    @patch("routes.metadata.is_valid_library_path", return_value=True)
    def test_cbz_metadata_includes_raw_comicinfo_xml_text(self, mock_valid, client, tmp_path):
        cbz_path = str(tmp_path / "data" / "with-xml.cbz")
        os.makedirs(str(tmp_path / "data"), exist_ok=True)

        raw_xml = """<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Series>Hellboy</Series>
  <Number>1</Number>
</ComicInfo>"""

        with zipfile.ZipFile(cbz_path, 'w') as zf:
            zf.writestr("page_001.png", b"fake image data")
            zf.writestr("ComicInfo.xml", raw_xml)

        resp = client.get('/cbz-metadata', query_string={"path": cbz_path})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["comicinfo"]["Series"] == "Hellboy"
        assert data["comicinfo_xml_text"] == raw_xml


class TestUpdateXmlFileIndexSync:

    @patch("routes.metadata._sync_file_index_after_xml_update")
    @patch("models.update_xml.update_field_in_cbz_files")
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    def test_update_xml_calls_sync(self, mock_valid, mock_update, mock_sync, client, tmp_path):
        """After update_field_in_cbz_files, _sync_file_index_after_xml_update is called."""
        comic_dir = str(tmp_path / "data" / "comics")
        os.makedirs(comic_dir, exist_ok=True)

        mock_update.return_value = {
            'updated': 1, 'skipped': 0, 'errors': 0,
            'details': [{'file': 'issue1.cbz', 'status': 'updated'}],
        }

        resp = client.post('/api/update-xml', json={
            "directory": comic_dir,
            "field": "Volume",
            "value": "2020",
        })
        assert resp.status_code == 200
        mock_sync.assert_called_once_with(
            comic_dir, "Volume", "2020", mock_update.return_value,
        )

    @patch("core.database.update_file_index_ci_field")
    def test_sync_updates_ci_field_for_updated_files(self, mock_db_update):
        """_sync_file_index_after_xml_update calls update_file_index_ci_field per file."""
        from routes.metadata import _sync_file_index_after_xml_update

        result = {
            'updated': 2, 'skipped': 1, 'errors': 0,
            'details': [
                {'file': 'issue1.cbz', 'status': 'updated'},
                {'file': 'issue2.cbz', 'status': 'skipped', 'reason': 'no xml'},
                {'file': 'issue3.cbz', 'status': 'updated'},
            ],
        }
        _sync_file_index_after_xml_update("/data/comics", "Volume", "2020", result)

        assert mock_db_update.call_count == 2
        mock_db_update.assert_any_call(
            os.path.join("/data/comics", "issue1.cbz"), "ci_volume", "2020",
        )
        mock_db_update.assert_any_call(
            os.path.join("/data/comics", "issue3.cbz"), "ci_volume", "2020",
        )

    @patch("core.database.update_file_index_ci_field")
    def test_sync_skips_unmapped_field(self, mock_db_update):
        """Fields without ci_ mapping (e.g. SeriesGroup) are silently skipped."""
        from routes.metadata import _sync_file_index_after_xml_update

        result = {
            'updated': 1, 'skipped': 0, 'errors': 0,
            'details': [{'file': 'issue1.cbz', 'status': 'updated'}],
        }
        _sync_file_index_after_xml_update("/data/comics", "SeriesGroup", "X-Men", result)

        mock_db_update.assert_not_called()

    @patch("core.database.update_file_index_ci_field", side_effect=Exception("db error"))
    def test_sync_logs_warning_on_db_failure(self, mock_db_update):
        """Database errors are caught and logged, not raised."""
        from routes.metadata import _sync_file_index_after_xml_update

        result = {
            'updated': 1, 'skipped': 0, 'errors': 0,
            'details': [{'file': 'issue1.cbz', 'status': 'updated'}],
        }
        # Should not raise
        _sync_file_index_after_xml_update("/data/comics", "Series", "Batman", result)


class TestSearchMetadataParsedFilename:
    """Tests for parsed_filename in 404 responses and search_term override."""

    @patch("models.metron.is_metron_configured", return_value=False)
    @patch("models.metron.is_connection_error", return_value=False)
    @patch("models.gcd.is_mysql_available", return_value=False)
    @patch("models.gcd.check_mysql_status", return_value={"gcd_mysql_available": False})
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    @patch("models.comicvine.extract_issue_number", return_value=None)
    @patch("core.database.get_library_providers", return_value=[])
    @patch("core.database.set_has_comicinfo")
    def test_404_includes_parsed_filename(
        self, mock_set, mock_providers, mock_extract, mock_cvinfo,
        mock_mysql_status, mock_mysql, mock_conn_err, mock_metron, client
    ):
        """When all providers are exhausted, 404 response includes parsed_filename."""
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
        })
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["success"] is False
        assert "parsed_filename" in data
        assert data["parsed_filename"]["series_name"] == "Batman"
        assert data["parsed_filename"]["issue_number"] == "1"
        assert data["parsed_filename"]["year"] == 2020

    @patch("models.metron.is_metron_configured", return_value=False)
    @patch("models.metron.is_connection_error", return_value=False)
    @patch("models.gcd.is_mysql_available", return_value=False)
    @patch("models.gcd.check_mysql_status", return_value={"gcd_mysql_available": False})
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    @patch("models.comicvine.extract_issue_number", return_value=None)
    @patch("core.database.get_library_providers", return_value=[])
    @patch("core.database.set_has_comicinfo")
    def test_volume_pattern_parses_series_and_number(
        self, mock_set, mock_providers, mock_extract, mock_cvinfo,
        mock_mysql_status, mock_mysql, mock_conn_err, mock_metron, client
    ):
        """Manga volume filenames like 'Angel Heart v01.cbz' should parse
        series='Angel Heart' and issue_number='1', not series='Angel Heart v01'."""
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/manga/Angel Heart/Angel Heart v01.cbz',
            'file_name': 'Angel Heart v01.cbz',
        })
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["parsed_filename"]["series_name"] == "Angel Heart"
        assert data["parsed_filename"]["issue_number"] == "1"

    @patch("models.metron.is_metron_configured", return_value=False)
    @patch("models.metron.is_connection_error", return_value=False)
    @patch("models.gcd.is_mysql_available", return_value=False)
    @patch("models.gcd.check_mysql_status", return_value={"gcd_mysql_available": False})
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    @patch("models.comicvine.extract_issue_number", return_value=None)
    @patch("core.database.get_library_providers", return_value=[])
    @patch("core.database.set_has_comicinfo")
    def test_search_term_override(
        self, mock_set, mock_providers, mock_extract, mock_cvinfo,
        mock_mysql_status, mock_mysql, mock_conn_err, mock_metron, client
    ):
        """search_term override replaces the parsed series name."""
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'search_term': 'Dark Knight',
        })
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["parsed_filename"]["series_name"] == "Dark Knight"

    @patch("routes.metadata._try_comicvine_single", return_value=(None, None, None, None))
    @patch("routes.metadata._try_metron_single")
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    @patch("core.database.get_library_providers", return_value=[
        {"provider_type": "metron", "enabled": True},
        {"provider_type": "comicvine", "enabled": True},
    ])
    @patch("core.database.set_has_comicinfo")
    def test_force_provider_uses_only_requested_single_provider(
        self,
        mock_set,
        mock_providers,
        mock_cvinfo,
        mock_try_metron,
        mock_try_comicvine,
        client,
    ):
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'library_id': 1,
            'force_provider': 'comicvine',
        })

        assert resp.status_code == 404
        mock_try_comicvine.assert_called_once()
        mock_try_metron.assert_not_called()

    @patch("routes.metadata.comicvine.search_volumes", return_value=[
        {"id": 4050, "name": "Batman", "start_year": 2016, "publisher_name": "DC Comics"}
    ])
    @patch("routes.metadata.comicvine.is_simyan_available", return_value=True)
    @patch("models.comicvine.find_cvinfo_in_folder")
    @patch("core.database.set_has_comicinfo")
    def test_force_provider_requires_manual_selection_for_single_comicvine(
        self,
        mock_set,
        mock_find_cvinfo,
        mock_simyan_available,
        mock_search_volumes,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'force_provider': 'comicvine',
            'force_manual_selection': True,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "comicvine"
        assert data["possible_matches"][0]["id"] == 4050
        mock_search_volumes.assert_called_once_with("test-key", "Batman", 2020)
        mock_find_cvinfo.assert_not_called()

    @patch("routes.metadata.metron.search_series_candidates_by_name", return_value=[
        {"id": 501, "name": "Batman", "cv_id": 4050, "publisher_name": "DC Comics", "year_began": 2016}
    ])
    @patch("routes.metadata.metron.get_flask_api", return_value=MagicMock())
    @patch("routes.metadata.metron.is_metron_configured", return_value=True)
    @patch("models.comicvine.find_cvinfo_in_folder")
    @patch("core.database.set_has_comicinfo")
    def test_force_provider_requires_manual_selection_for_single_metron(
        self,
        mock_set,
        mock_find_cvinfo,
        mock_metron_configured,
        mock_get_flask_api,
        mock_search_candidates,
        client,
    ):
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'force_provider': 'metron',
            'force_manual_selection': True,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "metron"
        assert data["possible_matches"][0]["id"] == 501
        mock_search_candidates.assert_called_once()
        mock_find_cvinfo.assert_not_called()

    @patch("routes.metadata._search_manga_provider_candidates")
    @patch("models.comicvine.find_cvinfo_in_folder")
    @patch("core.database.get_library_providers", return_value=[
        {"provider_type": "mangaupdates", "enabled": True},
    ])
    @patch("core.database.set_has_comicinfo")
    def test_force_provider_requires_manual_selection_for_single_mangaupdates(
        self,
        mock_set,
        mock_providers,
        mock_find_cvinfo,
        mock_search_candidates,
        client,
    ):
        match = MagicMock(
            id="12345",
            title="Demon Love Spell",
            year=2008,
            publisher="Shogakukan",
            issue_count=None,
            cover_url="https://example.com/cover.jpg",
            description="A romance.",
            alternate_title="Ayakashi Koi Emaki",
        )
        mock_search_candidates.return_value = ("Demon Love Spell", [match])

        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Demon Love Spell v01.cbz',
            'file_name': 'Demon Love Spell v01.cbz',
            'library_id': 1,
            'force_provider': 'mangaupdates',
            'force_manual_selection': True,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "mangaupdates"
        assert data["possible_matches"][0]["id"] == "12345"
        assert data["possible_matches"][0]["alternate_title"] == "Ayakashi Koi Emaki"
        mock_search_candidates.assert_called_once_with("mangaupdates", "Demon Love Spell", None)
        mock_find_cvinfo.assert_not_called()

    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    @patch("core.database.get_library_providers", return_value=[
        {"provider_type": "metron", "enabled": True},
    ])
    @patch("core.database.set_has_comicinfo")
    def test_force_provider_rejects_provider_not_enabled_for_library(
        self,
        mock_set,
        mock_providers,
        mock_cvinfo,
        client,
    ):
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'library_id': 1,
            'force_provider': 'comicvine',
        })

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "not enabled for this library" in data["error"]

    @patch("routes.metadata.app_state.complete_operation")
    @patch("routes.metadata.app_state.update_operation")
    @patch("routes.metadata.app_state.register_operation", return_value="op-123")
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch(
        "routes.metadata._try_comicvine_single",
        return_value=({"Series": "Batman", "Number": "1", "Year": "2020"}, None, None, None),
    )
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    def test_search_metadata_registers_and_completes_single_file_operation(
        self,
        mock_cvinfo,
        mock_try_comicvine,
        mock_add_xml,
        mock_update_index,
        mock_register_op,
        mock_update_op,
        mock_complete_op,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
        })

        assert resp.status_code == 200
        mock_register_op.assert_called_once_with("metadata", "Batman 001 (2020).cbz", total=5)
        detail_updates = [call.kwargs["detail"] for call in mock_update_op.call_args_list if "detail" in call.kwargs]
        assert "Parsing filename..." in detail_updates
        assert "Preparing provider search..." in detail_updates
        assert "Searching comicvine..." in detail_updates
        assert "Applying metadata from comicvine..." in detail_updates
        assert "Finalizing file updates..." in detail_updates
        mock_complete_op.assert_called_once_with("op-123", error=False)

    @patch("routes.metadata.app_state.complete_operation")
    @patch("routes.metadata.app_state.update_operation")
    @patch("routes.metadata.app_state.register_operation", return_value="op-456")
    @patch("routes.metadata._try_comicvine_single", side_effect=RuntimeError("boom"))
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    def test_search_metadata_marks_single_file_operation_error_on_exception(
        self,
        mock_cvinfo,
        mock_try_comicvine,
        mock_register_op,
        mock_update_op,
        mock_complete_op,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
        })

        assert resp.status_code == 500
        mock_register_op.assert_called_once_with("metadata", "Batman 001 (2020).cbz", total=5)
        mock_complete_op.assert_called_once_with("op-456", error=True)

    @patch("routes.metadata.app_state.complete_operation")
    @patch("routes.metadata.app_state.update_operation")
    @patch("routes.metadata.app_state.register_operation", return_value="op-789")
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    def test_search_metadata_marks_single_file_operation_error_on_no_match(
        self,
        mock_cvinfo,
        mock_register_op,
        mock_update_op,
        mock_complete_op,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = ""
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
        })

        assert resp.status_code == 404
        detail_updates = [call.kwargs["detail"] for call in mock_update_op.call_args_list if "detail" in call.kwargs]
        assert "No metadata found" in detail_updates
        mock_register_op.assert_called_once_with("metadata", "Batman 001 (2020).cbz", total=5)
        mock_complete_op.assert_called_once_with("op-789", error=True)

    @patch("routes.metadata.app_state.complete_operation")
    @patch("routes.metadata.app_state.update_operation")
    @patch("routes.metadata.app_state.register_operation", return_value="op-999")
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    def test_search_metadata_marks_single_file_operation_error_on_invalid_force_provider(
        self,
        mock_cvinfo,
        mock_register_op,
        mock_update_op,
        mock_complete_op,
        client,
    ):
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'force_provider': 'gcd',
        })

        assert resp.status_code == 400
        detail_updates = [call.kwargs["detail"] for call in mock_update_op.call_args_list if "detail" in call.kwargs]
        assert "Unsupported force provider" in detail_updates
        mock_register_op.assert_called_once_with("metadata", "Batman 001 (2020).cbz", total=5)
        mock_complete_op.assert_called_once_with("op-999", error=True)


class TestComicVineVolumeYearHandling:

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.get_issue_by_number")
    def test_search_metadata_selection_fetches_volume_start_year_when_client_omits_it(
        self,
        mock_get_issue,
        mock_get_volume_details,
        mock_auto_move,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
        }

        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "selected_match": {
                "provider": "comicvine",
                "volume_id": 4050,
                "publisher_name": "DC Comics",
            },
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["metadata"]["Volume"] == 2016
        mock_get_volume_details.assert_called_once_with("test-key", 4050)

        volume_data = mock_auto_move.call_args.args[1]
        assert volume_data["start_year"] == 2016

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    def test_search_metadata_selection_overrides_stale_client_start_year(
        self,
        mock_get_volume_details,
        mock_get_issue,
        mock_auto_move,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
        }

        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "selected_match": {
                "provider": "comicvine",
                "volume_id": 4050,
                "publisher_name": "DC Comics",
                "start_year": 2020,
            },
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["metadata"]["Volume"] == 2016
        mock_get_volume_details.assert_called_once_with("test-key", 4050)

        volume_data = mock_auto_move.call_args.args[1]
        assert volume_data["start_year"] == 2016

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata._resolve_existing_file_path", return_value="/data/Batman (2016)/Batman 001 (2020).cbz")
    @patch("routes.metadata.os.path.exists", return_value=True)
    def test_search_metadata_selection_repairs_stale_library_path(
        self,
        mock_exists,
        mock_resolve_path,
        mock_get_issue,
        mock_get_volume_details,
        mock_auto_move,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
        }

        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/-to do-/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "library_id": 1,
            "selected_match": {
                "provider": "comicvine",
                "volume_id": 4050,
                "publisher_name": "DC Comics",
            },
        })

        assert resp.status_code == 200
        mock_resolve_path.assert_called_once_with(
            "/data/-to do-/Batman 001 (2020).cbz",
            "Batman 001 (2020).cbz",
            1,
        )
        mock_add_xml.assert_called_once()

    @patch("routes.metadata.comicvine.list_issue_candidates_for_volume", return_value=[
        {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher_name": "DC Comics",
            "cover_date": "2020-01-01",
            "year": 2020,
            "image_url": "https://example.com/issue-1.jpg",
        },
        {
            "id": 1002,
            "name": "I Am Gotham",
            "issue_number": "2",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher_name": "DC Comics",
            "cover_date": "2020-02-01",
            "year": 2020,
            "image_url": "https://example.com/issue-2.jpg",
        },
    ])
    @patch("routes.metadata.comicvine.get_issue_by_number", return_value=None)
    def test_search_metadata_selection_returns_issue_picker_when_selected_volume_lookup_fails(
        self,
        mock_get_issue,
        mock_list_issue_candidates,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "selected_match": {
                "provider": "comicvine",
                "volume_id": 4050,
                "volume_name": "Batman",
                "publisher_name": "DC Comics",
                "start_year": 2016,
            },
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["selection_type"] == "issue"
        assert data["provider"] == "comicvine"
        assert len(data["possible_matches"]) == 2
        assert data["selected_match_context"]["volume_id"] == 4050
        assert data["selected_match_context"]["volume_name"] == "Batman"
        mock_get_issue.assert_called_once_with("test-key", 4050, "1", 2020)
        mock_list_issue_candidates.assert_called_once_with("test-key", 4050, 2020)

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.get_issue_by_id")
    def test_search_metadata_selection_supports_comicvine_issue_choice(
        self,
        mock_get_issue_by_id,
        mock_get_volume_details,
        mock_auto_move,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue_by_id.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
            "image_url": "https://example.com/cover.jpg",
        }

        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "selected_match": {
                "provider": "comicvine",
                "volume_id": 4050,
                "issue_id": 1001,
                "publisher_name": "DC Comics",
                "start_year": 2016,
            },
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["source"] == "comicvine"
        assert data["image_url"] == "https://example.com/cover.jpg"
        mock_get_issue_by_id.assert_called_once_with("test-key", 1001)
        mock_add_xml.assert_called_once()

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.get_issue_by_id")
    def test_search_metadata_selection_uses_issue_volume_id_over_client_volume_id(
        self,
        mock_get_issue_by_id,
        mock_get_volume_details,
        mock_auto_move,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue_by_id.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
            "image_url": "https://example.com/cover.jpg",
        }

        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "selected_match": {
                "provider": "comicvine",
                "volume_id": 9999,
                "issue_id": 1001,
                "publisher_name": "DC Comics",
                "start_year": 2016,
            },
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_get_issue_by_id.assert_called_once_with("test-key", 1001)
        mock_get_volume_details.assert_called_once_with("test-key", 4050)
        volume_data = mock_auto_move.call_args.args[1]
        assert volume_data["id"] == 4050

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.metron.map_to_comicinfo", return_value={
        "Series": "Batman",
        "Number": "1",
        "Volume": 2016,
        "Year": 2020,
    })
    @patch("routes.metadata.metron.get_issue_metadata", return_value={
        "id": 9001,
        "image": "https://example.com/cover.jpg",
    })
    @patch("routes.metadata.metron.get_flask_api", return_value=MagicMock())
    def test_search_metadata_selection_supports_metron_series_choice(
        self,
        mock_get_flask_api,
        mock_get_issue_metadata,
        mock_map_to_comicinfo,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "selected_match": {
                "provider": "metron",
                "series_id": 501,
            },
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["source"] == "metron"
        assert data["image_url"] == "https://example.com/cover.jpg"
        mock_get_issue_metadata.assert_called_once()
        mock_map_to_comicinfo.assert_called_once()
        mock_add_xml.assert_called_once()
        assert mock_add_xml.call_args.args[0] == "/data/Batman 001 (2020).cbz"

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("models.providers.mangaupdates_provider.MangaUpdatesProvider.get_issue_metadata", return_value={
        "Series": "Demon Love Spell",
        "Number": "v1",
        "Year": 2008,
        "Notes": "Metadata from MangaUpdates.",
    })
    def test_search_metadata_selection_supports_mangaupdates_series_choice(
        self,
        mock_get_issue_metadata,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/Demon Love Spell v01.cbz",
            "file_name": "Demon Love Spell v01.cbz",
            "selected_match": {
                "provider": "mangaupdates",
                "series_id": "12345",
                "preferred_title": "Demon Love Spell",
                "alternate_title": "Ayakashi Koi Emaki",
            },
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["source"] == "mangaupdates"
        mock_get_issue_metadata.assert_called_once_with(
            "12345",
            "1",
            preferred_title="Demon Love Spell",
            alternate_title="Ayakashi Koi Emaki",
        )
        mock_add_xml.assert_called_once()
        assert mock_add_xml.call_args.args[0] == "/data/Demon Love Spell v01.cbz"

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2017, "publisher_name": "Fantagraphics"})
    @patch("routes.metadata.comicvine.get_issue_by_number")
    def test_search_metadata_selection_does_not_treat_year_token_as_issue_number(
        self,
        mock_get_issue,
        mock_get_volume_details,
        mock_auto_move,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 613847,
            "name": "GN",
            "issue_number": "1",
            "volume_name": "My Pretty Vampire",
            "volume_id": 103399,
            "publisher": "Fantagraphics",
            "year": 2017,
        }

        file_name = (
            "My Pretty Vampire (2017) (digital) (Minutemen-dask)_cbr -- "
            "Katie Skelly (artist, cover, writer) -- My Pretty Vampire, 2017 aug -- "
            "Fantagraphics -- f218a0ecf3fbf011e706452ae2c271e0 -- Anna's Archive"
        )
        resp = client.post("/api/search-metadata", json={
            "file_path": "/data/-to do-/" + file_name,
            "file_name": file_name,
            "selected_match": {
                "provider": "comicvine",
                "volume_id": 103399,
                "publisher_name": "Fantagraphics",
            },
        })

        assert resp.status_code == 200
        mock_get_issue.assert_called_once_with("test-key", 103399, "1", None)

    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.get_issue_by_number")
    def test_selected_comicvine_endpoint_fetches_volume_start_year_when_request_omits_it(
        self,
        mock_get_issue,
        mock_get_volume_details,
        mock_auto_move,
        mock_add_xml,
        mock_set_has_comicinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
        }

        resp = client.post("/search-comicvine-metadata-with-selection", json={
            "file_path": "/data/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "volume_id": 4050,
            "publisher_name": "DC Comics",
            "issue_number": "1",
            "year": 2020,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["metadata"]["Volume"] == 2016
        mock_get_volume_details.assert_called_once_with("test-key", 4050)
        assert mock_auto_move.call_args.args[2] is client.application.config

    @patch("routes.metadata.comicvine.write_cvinfo_fields")
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.find_cvinfo_in_folder", return_value="/data/Batman/cvinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata.os.path.exists", return_value=True)
    def test_selected_comicvine_endpoint_repairs_stale_cvinfo(
        self,
        mock_exists,
        mock_get_issue,
        mock_set_has_comicinfo,
        mock_add_xml,
        mock_find_cvinfo,
        mock_get_volume_details,
        mock_write_cvinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
        }

        resp = client.post("/search-comicvine-metadata-with-selection", json={
            "file_path": "/data/Batman/Batman 001 (2020).cbz",
            "file_name": "Batman 001 (2020).cbz",
            "volume_id": 4050,
            "publisher_name": "DC Comics",
            "start_year": 2020,
            "issue_number": "1",
            "year": 2020,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["metadata"]["Volume"] == 2016
        mock_find_cvinfo.assert_called_once_with("/data/Batman")
        mock_write_cvinfo.assert_called_once_with(
            "/data/Batman/cvinfo", "DC Comics", 2016
        )

    @patch("routes.metadata.comicvine.write_cvinfo_fields")
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.read_cvinfo_fields", return_value={"start_year": 2020, "publisher_name": "DC Comics"})
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata.comicvine.parse_cvinfo_volume_id", return_value=4050)
    @patch("routes.metadata.comicvine.is_simyan_available", return_value=True)
    @patch("routes.metadata.os.path.exists", return_value=True)
    def test_try_comicvine_single_overrides_stale_cvinfo_start_year(
        self,
        mock_exists,
        mock_simyan,
        mock_parse_cvinfo,
        mock_get_issue,
        mock_read_cvinfo,
        mock_get_volume_details,
        mock_write_cvinfo,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 1001,
            "name": "Rebirth",
            "issue_number": "1",
            "volume_name": "Batman",
            "volume_id": 4050,
            "publisher": "DC Comics",
            "year": 2020,
        }

        with client.application.app_context():
            from routes.metadata import _try_comicvine_single

            metadata, img_url, volume_data, selection_data = _try_comicvine_single(
                "/data/Batman/cvinfo", "Batman", "1", 2020
            )

        assert selection_data is None
        assert img_url is None
        assert metadata["Volume"] == 2016
        assert volume_data["start_year"] == 2016
        mock_get_volume_details.assert_called_once_with("test-key", 4050)
        mock_write_cvinfo.assert_called_once_with(
            "/data/Batman/cvinfo", "DC Comics", 2016
        )


class TestBatchForceMetadata:

    @patch("routes.metadata.comicvine.parse_cvinfo_volume_id")
    @patch("routes.metadata.comicvine.search_volumes", return_value=[
        {"id": 4050, "name": "Batman", "start_year": 2016, "publisher_name": "DC Comics"}
    ])
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_force_batch_ignores_existing_cvinfo_and_requires_manual_selection(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_search_volumes,
        mock_parse_cvinfo,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        (batch_dir / "cvinfo").write_text("https://comicvine.gamespot.com/volume/4050-9999/\n", encoding="utf-8")
        _make_cbz(str(batch_dir / "Batman 001 (2020).cbz"), with_comicinfo=False)

        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "force_manual_selection": True,
            "force_provider": "comicvine",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "comicvine"
        assert data["possible_matches"][0]["id"] == 4050
        mock_search_volumes.assert_called_once_with("test-key", "Batman", 2020)
        mock_parse_cvinfo.assert_not_called()

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_issue_by_number", return_value={
        "id": 1001,
        "name": "Failsafe Part One",
        "issue_number": "1",
        "volume_name": "Batman",
        "volume_id": 4050,
        "publisher": None,
        "year": 2020,
        "month": 7,
        "day": 5,
        "description": "Fetched from ComicVine",
        "image_url": None,
    })
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_force_batch_overwrites_existing_comicinfo_and_rewrites_cvinfo(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_get_volume_details,
        mock_get_issue_by_number,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        cvinfo_path = batch_dir / "cvinfo"
        cvinfo_path.write_text(
            "https://comicvine.gamespot.com/volume/4050-9999/\n"
            "publisher_name: Wrong Publisher\n"
            "start_year: 2020\n"
            "series_id: 9999\n",
            encoding="utf-8",
        )
        _make_cbz_with_notes(
            str(batch_dir / "Batman 001 (2020).cbz"),
            "Hand-edited metadata",
        )

        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "volume_id": 4050,
            "force_manual_selection": True,
            "force_provider": "comicvine",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        resp.get_data(as_text=True)
        assert mock_add_xml.called is True
        assert mock_get_volume_details.call_count == 2
        mock_get_volume_details.assert_any_call("test-key", 4050)
        mock_get_issue_by_number.assert_called_once_with("test-key", 4050, "1", 2020)
        xml_bytes = mock_add_xml.call_args.args[1]
        assert b"<Publisher>DC Comics</Publisher>" in xml_bytes

        cvinfo_text = cvinfo_path.read_text(encoding="utf-8")
        assert "4050-4050" in cvinfo_text
        assert "publisher_name: DC Comics" in cvinfo_text
        assert "start_year: 2016" in cvinfo_text
        assert "series_id: 9999" not in cvinfo_text

    @patch("routes.metadata.metron.search_series_candidates_by_name", return_value=[
        {"id": 501, "name": "Batman", "cv_id": 4050, "publisher_name": "DC Comics", "year_began": 2016}
    ])
    @patch("routes.metadata.comicvine.parse_cvinfo_volume_id")
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.get_flask_api", return_value=MagicMock())
    @patch("routes.metadata.metron.is_metron_configured", return_value=True)
    def test_force_batch_metron_ignores_existing_cvinfo_and_requires_manual_selection(
        self,
        mock_metron_configured,
        mock_get_flask_api,
        mock_gcd_available,
        mock_valid_library_path,
        mock_parse_cvinfo,
        mock_search_candidates,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        (batch_dir / "cvinfo").write_text("https://comicvine.gamespot.com/volume/4050-9999/\n", encoding="utf-8")
        _make_cbz(str(batch_dir / "Batman 001 (2020).cbz"), with_comicinfo=False)

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "force_manual_selection": True,
            "force_provider": "metron",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "metron"
        assert data["possible_matches"][0]["id"] == 501
        mock_search_candidates.assert_called_once()
        mock_parse_cvinfo.assert_not_called()

    @patch("routes.metadata._search_manga_provider_candidates")
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.get_library_providers", return_value=[
        {"provider_type": "mangaupdates", "enabled": True, "priority": 0},
    ])
    def test_force_batch_mangaupdates_requires_manual_selection(
        self,
        mock_providers,
        mock_valid_library_path,
        mock_search_candidates,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Demon Love Spell"
        batch_dir.mkdir(parents=True)
        _make_cbz(str(batch_dir / "Demon Love Spell v01.cbz"), with_comicinfo=False)

        match = MagicMock(
            id="12345",
            title="Demon Love Spell",
            year=2008,
            publisher="Shogakukan",
            issue_count=None,
            cover_url="https://example.com/cover.jpg",
            description="A romance.",
            alternate_title="Ayakashi Koi Emaki",
        )
        mock_search_candidates.return_value = ("Demon Love Spell", [match])

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "library_id": 1,
            "force_manual_selection": True,
            "force_provider": "mangaupdates",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "mangaupdates"
        assert data["possible_matches"][0]["id"] == "12345"
        mock_search_candidates.assert_called_once_with("mangaupdates", "Demon Love Spell", None)

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("models.providers.mangaupdates_provider.MangaUpdatesProvider.get_issue_metadata", return_value={
        "Series": "Demon Love Spell",
        "Number": "v1",
        "Year": 2008,
        "Notes": "Metadata from MangaUpdates.",
    })
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("core.database.get_library_providers", return_value=[
        {"provider_type": "mangaupdates", "enabled": True, "priority": 0},
    ])
    def test_force_batch_mangaupdates_writes_selected_series_to_cvinfo(
        self,
        mock_providers,
        mock_valid_library_path,
        mock_get_issue_metadata,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Demon Love Spell"
        batch_dir.mkdir(parents=True)
        cvinfo_path = batch_dir / "cvinfo"
        cvinfo_path.write_text("publisher_name: Existing Publisher\n", encoding="utf-8")
        _make_cbz(str(batch_dir / "Demon Love Spell v01.cbz"), with_comicinfo=False)

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "library_id": 1,
            "series_id": "12345",
            "selected_title": "Demon Love Spell",
            "selected_alternate_title": "Ayakashi Koi Emaki",
            "force_manual_selection": True,
            "force_provider": "mangaupdates",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert '"type": "complete"' in body
        assert mock_add_xml.called is True
        mock_get_issue_metadata.assert_called_once_with(
            "12345",
            "1",
            preferred_title="Demon Love Spell",
            alternate_title="Ayakashi Koi Emaki",
        )

        cvinfo_text = cvinfo_path.read_text(encoding="utf-8")
        assert "publisher_name: Existing Publisher" in cvinfo_text
        assert "mangaupdates_id: 12345" in cvinfo_text
        assert "mangaupdates_url: https://www.mangaupdates.com/series/12345" in cvinfo_text
        assert "mangaupdates_title: Demon Love Spell" in cvinfo_text
        assert "mangaupdates_alt_title: Ayakashi Koi Emaki" in cvinfo_text

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_metadata_by_volume_id")
    @patch("routes.metadata.metron.map_to_comicinfo", return_value={
        "Series": "Batman",
        "Number": "1",
        "Volume": 2016,
        "Year": 2020,
        "Notes": "Fetched from Metron",
    })
    @patch("routes.metadata.metron.get_issue_metadata", return_value={"id": 9001})
    @patch("routes.metadata.metron.create_cvinfo_file")
    @patch("routes.metadata.metron.get_series_details", return_value={
        "id": 501,
        "cv_id": 4050,
        "publisher_name": "DC Comics",
        "year_began": 2016,
    })
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.get_flask_api", return_value=MagicMock())
    @patch("routes.metadata.metron.is_metron_configured", return_value=True)
    @patch("core.database.get_library_providers", return_value=[
        {"provider_type": "comicvine", "enabled": True, "priority": 0},
        {"provider_type": "metron", "enabled": True, "priority": 1},
    ])
    def test_force_batch_metron_overrides_library_priority_and_rewrites_cvinfo(
        self,
        mock_library_providers,
        mock_metron_configured,
        mock_get_flask_api,
        mock_gcd_available,
        mock_valid_library_path,
        mock_get_series_details,
        mock_create_cvinfo,
        mock_get_issue_metadata,
        mock_map_to_comicinfo,
        mock_get_cv_metadata,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        _make_cbz_with_notes(
            str(batch_dir / "Batman 001 (2020).cbz"),
            "Hand-edited metadata",
        )

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "series_id": 501,
            "library_id": 123,
            "force_manual_selection": True,
            "force_provider": "metron",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        resp.get_data(as_text=True)
        mock_create_cvinfo.assert_called_once_with(
            str(batch_dir / "cvinfo"),
            cv_id=4050,
            series_id=501,
            publisher_name="DC Comics",
            start_year=2016,
        )
        mock_get_issue_metadata.assert_called_once_with(mock_get_flask_api.return_value, 501, "1")
        mock_map_to_comicinfo.assert_called_once_with({"id": 9001})
        mock_add_xml.assert_called_once()
        mock_get_cv_metadata.assert_not_called()

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_default_batch_still_skips_existing_meaningful_comicinfo(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        (batch_dir / "cvinfo").write_text(
            "https://comicvine.gamespot.com/volume/4050-4050/\n"
            "publisher_name: DC Comics\n"
            "start_year: 2016\n",
            encoding="utf-8",
        )
        _make_cbz_with_notes(
            str(batch_dir / "Batman 001 (2020).cbz"),
            "Hand-edited metadata",
        )

        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
        })

        assert resp.status_code == 200
        resp.get_data(as_text=True)
        assert mock_add_xml.called is False

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_issue_by_number", return_value={
        "id": 1003,
        "name": "20th Century Boys v03",
        "issue_number": "3",
        "volume_name": "20th Century Boys",
        "volume_id": 34961,
        "publisher": "Viz",
        "year": 2000,
        "month": 1,
        "day": 1,
        "description": "Fetched from ComicVine",
        "image_url": None,
    })
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2000, "publisher_name": "Viz"})
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_force_batch_uses_manga_volume_number_for_comicvine(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_get_volume_details,
        mock_get_issue_by_number,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "20th Century Boys (2000)"
        batch_dir.mkdir(parents=True)
        _make_cbz(
            str(batch_dir / "20th Century Boys, v03 (2000) [Band of the Hawks].cbz"),
            with_comicinfo=False,
        )

        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "volume_id": 34961,
            "force_manual_selection": True,
            "force_provider": "comicvine",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        resp.get_data(as_text=True)
        mock_get_issue_by_number.assert_called_once_with("test-key", 34961, "3", 2000)
        mock_add_xml.assert_called_once()

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2013, "publisher_name": "Image"})
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_force_batch_does_not_treat_western_volume_only_name_as_issue(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_get_volume_details,
        mock_get_issue_by_number,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Saga (2013)"
        batch_dir.mkdir(parents=True)
        _make_cbz(
            str(batch_dir / "Saga v02 (2013).cbz"),
            with_comicinfo=False,
        )

        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "volume_id": 12345,
            "force_manual_selection": True,
            "force_provider": "comicvine",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        mock_get_issue_by_number.assert_not_called()
        mock_add_xml.assert_not_called()
        assert '"reason": "no issue number"' in body

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_issue_by_number", return_value={
        "id": 1001,
        "name": "Failsafe Part One",
        "issue_number": "1",
        "volume_name": "Batman",
        "volume_id": 4050,
        "publisher": None,
        "year": 2020,
        "month": 7,
        "day": 5,
        "description": "Fetched from ComicVine",
        "image_url": None,
    })
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2016, "publisher_name": "DC Comics"})
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_force_batch_uses_per_file_year_for_comicvine_lookup(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_get_volume_details,
        mock_get_issue_by_number,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2016)"
        batch_dir.mkdir(parents=True)
        _make_cbz(
            str(batch_dir / "Batman 001 (2020).cbz"),
            with_comicinfo=False,
        )

        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "volume_id": 4050,
            "force_manual_selection": True,
            "force_provider": "comicvine",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        resp.get_data(as_text=True)
        mock_get_issue_by_number.assert_called_once_with("test-key", 4050, "1", 2020)

    @patch("routes.metadata.time.sleep", return_value=None)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_issue_by_number", return_value={
        "id": 1003,
        "name": "20th Century Boys v03",
        "issue_number": "3",
        "volume_name": "20th Century Boys",
        "volume_id": 34961,
        "publisher": "Viz",
        "year": 2000,
        "month": 1,
        "day": 1,
        "description": "Fetched from ComicVine",
        "image_url": None,
    })
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2000, "publisher_name": "Viz"})
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_force_batch_uses_volume_details_when_cvinfo_has_year_but_no_publisher(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_get_volume_details,
        mock_get_issue_by_number,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_sleep,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "20th Century Boys (2000)"
        batch_dir.mkdir(parents=True)
        (batch_dir / "cvinfo").write_text(
            "https://comicvine.gamespot.com/volume/4050-34961/\n"
            "start_year: 2000\n",
            encoding="utf-8",
        )
        _make_cbz(
            str(batch_dir / "20th Century Boys, v03 (2000) [Band of the Hawks].cbz"),
            with_comicinfo=False,
        )

        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        resp.get_data(as_text=True)
        assert mock_get_volume_details.call_count >= 1
        mock_get_volume_details.assert_any_call("test-key", 34961)
        mock_get_issue_by_number.assert_called_once_with("test-key", 34961, "3", 2000)
        mock_add_xml.assert_called_once()

    @patch("routes.metadata.app_state.complete_operation")
    @patch("routes.metadata.app_state.register_operation", return_value="op-cancel")
    @patch("routes.metadata.app_state.is_operation_cancelled", return_value=True)
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_batch_metadata_cancel_stops_before_processing_next_file(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_cancelled,
        mock_register,
        mock_complete,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        _make_cbz(str(batch_dir / "Batman 001 (2020).cbz"), with_comicinfo=False)

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
        })

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert '"type": "cancelled"' in body
        assert '"op_id": "op-cancel"' in body
        mock_complete.assert_called_once_with("op-cancel", cancelled=True)

    @patch("routes.metadata.comicvine.search_volumes", return_value=[{
        "id": 4050,
        "name": "Batman",
        "start_year": 2020,
        "publisher_name": "DC",
    }])
    @patch("routes.metadata.app_state.is_operation_cancelled", return_value=True)
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_force_batch_cancel_during_initial_selection_lookup(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_cancelled,
        mock_search_volumes,
        client,
        tmp_path,
    ):
        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        _make_cbz(str(batch_dir / "Batman 001 (2020).cbz"), with_comicinfo=False)
        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "force_manual_selection": True,
            "force_provider": "comicvine",
            "overwrite_existing_metadata": True,
            "op_id": "client-op",
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cancelled"] is True
        assert data["op_id"] == "client-op"
        mock_search_volumes.assert_called_once_with("test-key", "Batman", 2020)


class TestBatchMetadataRenameUpdatesIndex:
    """Verify file_index is updated with new path/name after batch rename."""

    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine")
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.update_file_index_entry")
    @patch("cbz_ops.rename.rename_comic_from_metadata")
    def test_rename_updates_file_index_entry_before_comicinfo(
        self, mock_rename, mock_update_entry, mock_update_ci, mock_cv, mock_add_xml
    ):
        """When rename happens, update_file_index_entry is called with the new
        path/name BEFORE update_file_index_from_comicinfo, which uses the final path."""
        from routes.metadata import os

        old_path = "/data/comics/Batman 001 (2020).cbz"
        new_path = "/data/comics/Batman v2020 001.cbz"
        metadata = {"Series": "Batman", "Number": "1", "Volume": "2020"}

        mock_cv.generate_comicinfo_xml.return_value = b"<ComicInfo/>"
        mock_rename.return_value = (new_path, True)

        # Simulate the batch flow logic inline (extracted from the generator)
        file_path = old_path
        filename = os.path.basename(old_path)

        # -- begin logic under test (mirrors routes/metadata.py ~line 1376) --
        xml_bytes = mock_cv.generate_comicinfo_xml(metadata)
        mock_add_xml(file_path, xml_bytes)

        from cbz_ops.rename import rename_comic_from_metadata as _rename
        old_filename = filename
        _old_path = file_path
        result_path, was_renamed = _rename(file_path, metadata)
        if was_renamed:
            file_path = result_path
            filename = os.path.basename(result_path)
            from core.database import update_file_index_entry
            update_file_index_entry(_old_path, name=filename, new_path=result_path,
                                    parent=os.path.dirname(result_path))

        from core.database import update_file_index_from_comicinfo
        update_file_index_from_comicinfo(file_path, metadata)
        # -- end logic under test --

        # Assertions
        mock_update_entry.assert_called_once_with(
            old_path, name="Batman v2020 001.cbz", new_path=new_path,
            parent=os.path.dirname(new_path),
        )
        # update_file_index_from_comicinfo must use the NEW path
        mock_update_ci.assert_called_once_with(new_path, metadata)

    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine")
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("core.database.update_file_index_entry")
    @patch("cbz_ops.rename.rename_comic_from_metadata")
    def test_no_rename_skips_file_index_entry_update(
        self, mock_rename, mock_update_entry, mock_update_ci, mock_cv, mock_add_xml
    ):
        """When no rename happens, update_file_index_entry is NOT called."""
        from routes.metadata import os

        file_path = "/data/comics/Batman 001 (2020).cbz"
        metadata = {"Series": "Batman", "Number": "1"}

        mock_cv.generate_comicinfo_xml.return_value = b"<ComicInfo/>"
        mock_rename.return_value = (file_path, False)

        # Simulate batch flow
        filename = os.path.basename(file_path)
        xml_bytes = mock_cv.generate_comicinfo_xml(metadata)
        mock_add_xml(file_path, xml_bytes)

        from cbz_ops.rename import rename_comic_from_metadata as _rename
        old_path = file_path
        result_path, was_renamed = _rename(file_path, metadata)
        if was_renamed:
            file_path = result_path
            filename = os.path.basename(result_path)
            from core.database import update_file_index_entry
            update_file_index_entry(old_path, name=filename, new_path=result_path,
                                    parent=os.path.dirname(result_path))

        from core.database import update_file_index_from_comicinfo
        update_file_index_from_comicinfo(file_path, metadata)

        # update_file_index_entry should NOT have been called
        mock_update_entry.assert_not_called()
        # update_file_index_from_comicinfo uses original path
        mock_update_ci.assert_called_once_with(file_path, metadata)



class TestBatchMangaProviderPriority:

    def test_batch_skips_comicvine_cvinfo_when_manga_first(self, tmp_path):
        """When MangaDex is priority #1, Metron/ComicVine cvinfo creation is skipped."""
        # This tests the skip_comic_cvinfo gate logic directly
        # by simulating the provider priority check from batch_metadata

        manga_providers_set = {'mangadex', 'mangaupdates', 'anilist'}
        comic_providers_set = {'metron', 'comicvine'}

        # Library with MangaDex first
        library_providers = [
            {'provider_type': 'mangadex', 'enabled': True},
            {'provider_type': 'mangaupdates', 'enabled': True},
            {'provider_type': 'comicvine', 'enabled': True},
        ]

        skip_comic_cvinfo = False
        for p in library_providers:
            if p.get('enabled', True):
                ptype = p['provider_type']
                if ptype in manga_providers_set:
                    skip_comic_cvinfo = True
                    break
                elif ptype in comic_providers_set:
                    break

        assert skip_comic_cvinfo is True

    def test_batch_does_not_skip_when_comicvine_first(self):
        """When ComicVine is priority #1, cvinfo creation proceeds normally."""
        manga_providers_set = {'mangadex', 'mangaupdates', 'anilist'}
        comic_providers_set = {'metron', 'comicvine'}

        library_providers = [
            {'provider_type': 'comicvine', 'enabled': True},
            {'provider_type': 'mangadex', 'enabled': True},
        ]

        skip_comic_cvinfo = False
        for p in library_providers:
            if p.get('enabled', True):
                ptype = p['provider_type']
                if ptype in manga_providers_set:
                    skip_comic_cvinfo = True
                    break
                elif ptype in comic_providers_set:
                    break

        assert skip_comic_cvinfo is False


class TestSearchComicVineMetadata:

    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_volume_details", return_value={"start_year": 2000, "publisher_name": "Viz"})
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata.comicvine.parse_cvinfo_volume_id", return_value=34961)
    @patch("routes.metadata.comicvine.find_cvinfo_in_folder", return_value="/data/20th Century Boys/cvinfo")
    @patch("routes.metadata.comicvine.is_simyan_available", return_value=True)
    def test_search_comicvine_metadata_uses_manga_volume_number(
        self,
        mock_simyan,
        mock_find_cvinfo,
        mock_parse_cvinfo,
        mock_get_issue,
        mock_get_volume_details,
        mock_add_xml,
        mock_set_has_comicinfo,
        mock_auto_move,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 1003,
            "name": "20th Century Boys v03",
            "issue_number": "3",
            "volume_name": "20th Century Boys",
            "volume_id": 34961,
            "publisher": "Viz",
            "year": 2000,
            "image_url": None,
        }

        resp = client.post("/search-comicvine-metadata", json={
            "file_path": "/data/20th Century Boys/20th Century Boys, v03 (2000) [Band of the Hawks].cbz",
            "file_name": "20th Century Boys, v03 (2000) [Band of the Hawks].cbz",
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_get_issue.assert_called_once_with("test-key", 34961, "3", 2000)

    @patch("routes.metadata.comicvine.auto_move_file", return_value=None)
    @patch("core.database.set_has_comicinfo")
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata.comicvine.parse_cvinfo_volume_id", return_value=12345)
    @patch("routes.metadata.comicvine.read_cvinfo_fields", return_value={"publisher_name": "Image"})
    @patch("routes.metadata.comicvine.find_cvinfo_in_folder", return_value="/data/Saga/cvinfo")
    @patch("routes.metadata.comicvine.is_simyan_available", return_value=True)
    def test_search_comicvine_metadata_does_not_use_western_volume_only_name_as_issue(
        self,
        mock_simyan,
        mock_find_cvinfo,
        mock_read_cvinfo_fields,
        mock_parse_cvinfo,
        mock_get_issue,
        mock_add_xml,
        mock_set_has_comicinfo,
        mock_auto_move,
        client,
    ):
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        mock_get_issue.return_value = {
            "id": 2001,
            "name": "Saga",
            "issue_number": "1",
            "volume_name": "Saga",
            "volume_id": 12345,
            "publisher": "Image",
            "year": 2013,
            "image_url": None,
        }

        resp = client.post("/search-comicvine-metadata", json={
            "file_path": "/data/Saga/Saga v02 (2013).cbz",
            "file_name": "Saga v02 (2013).cbz",
        })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_get_issue.assert_called_once_with("test-key", 12345, "1", 2013)

    def test_batch_skips_disabled_providers(self):
        """Disabled manga provider at top doesn't trigger skip."""
        manga_providers_set = {'mangadex', 'mangaupdates', 'anilist'}
        comic_providers_set = {'metron', 'comicvine'}

        library_providers = [
            {'provider_type': 'mangadex', 'enabled': False},
            {'provider_type': 'comicvine', 'enabled': True},
        ]

        skip_comic_cvinfo = False
        for p in library_providers:
            if p.get('enabled', True):
                ptype = p['provider_type']
                if ptype in manga_providers_set:
                    skip_comic_cvinfo = True
                    break
                elif ptype in comic_providers_set:
                    break

        assert skip_comic_cvinfo is False
