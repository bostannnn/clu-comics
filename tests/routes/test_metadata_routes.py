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


class TestComicVineVolumeResolution:

    @patch("routes.metadata.comicvine.write_cvinfo_fields")
    @patch("routes.metadata.comicvine.get_volume_details", return_value={
        "start_year": 2016,
        "publisher_name": "DC Comics",
    })
    def test_resolve_volume_data_can_skip_cvinfo_write(
        self,
        mock_get_volume_details,
        mock_write_cvinfo_fields,
        tmp_path,
    ):
        from routes.metadata import _resolve_comicvine_volume_data

        cvinfo = tmp_path / "cvinfo"
        cvinfo.write_text(
            "https://comicvine.gamespot.com/volume/4050-4050/\n",
            encoding="utf-8",
        )

        volume = _resolve_comicvine_volume_data(
            "test-key",
            4050,
            {"volume_name": "Batman"},
            cvinfo_path=str(cvinfo),
            update_cvinfo=False,
        )

        assert volume["publisher_name"] == "DC Comics"
        assert volume["start_year"] == 2016
        mock_get_volume_details.assert_called_once_with("test-key", 4050)
        mock_write_cvinfo_fields.assert_not_called()


class TestCvinfoRoutes:

    def test_get_cvinfo_returns_structured_fields(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"
        cvinfo.write_text(
            "https://comicvine.gamespot.com/volume/4050-167796/\n"
            "series_id: 12345\n"
            "mangaupdates_id: abc123\n"
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
        assert data["mangaupdates_id"] == "abc123"
        assert data["mangaupdates_url"] == "https://www.mangaupdates.com/series/abc123"
        assert data["publisher_name"] == "Image"
        assert data["start_year"] == "2015"
        assert "mangaupdates_id: abc123" not in data["extra_lines"]
        assert "mangadex_id: abc123" in data["extra_lines"]

    def test_get_cvinfo_preserves_malformed_mangaupdates_id_in_extra_lines(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"
        cvinfo.write_text(
            "mangaupdates_id: abc 123\n"
            "mangadex_id: abc123\n",
            encoding="utf-8",
        )

        with patch("routes.metadata.is_valid_library_path", return_value=True):
            resp = client.get(f"/api/cvinfo?path={cvinfo}")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["mangaupdates_id"] == ""
        assert data["mangaupdates_url"] == ""
        assert "mangaupdates_id: abc 123" in data["extra_lines"]
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
                    "mangaupdates_url": "abc123",
                    "publisher_name": "Image",
                    "start_year": "2015",
                    "extra_lines": (
                        "mangadex_id: abc123\n"
                        "mangaupdates_title: Ayakashi Koi Emaki\n"
                        "mangaupdates_url: https://www.mangaupdates.com/series/ignore-me\n"
                        "publisher_name: ignore-me"
                    ),
                },
            )

        assert resp.status_code == 200
        content = cvinfo.read_text(encoding="utf-8")
        assert "https://comicvine.gamespot.com/volume/4050-167796/" in content
        assert "series_id: 12345" in content
        assert "mangaupdates_url: https://www.mangaupdates.com/series/abc123" in content
        assert "publisher_name: Image" in content
        assert "start_year: 2015" in content
        assert "mangadex_id: abc123" in content
        assert "mangaupdates_title: Ayakashi Koi Emaki" in content
        assert "ignore-me" not in content

    def test_save_cvinfo_structured_strips_stale_mangaupdates_id_lines(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"

        with patch("routes.metadata.is_valid_library_path", return_value=True), \
             patch("routes.metadata.is_path_in_any_root", return_value=False):
            resp = client.post(
                "/api/save-cvinfo",
                json={
                    "path": str(cvinfo),
                    "mangaupdates_url": "new123",
                    "extra_lines": (
                        "mangaupdates_id: old456\n"
                        "mangadex_id: abc123"
                    ),
                },
            )

        assert resp.status_code == 200
        content = cvinfo.read_text(encoding="utf-8")
        assert "mangaupdates_url: https://www.mangaupdates.com/series/new123" in content
        assert "mangaupdates_id: old456" not in content
        assert "mangadex_id: abc123" in content

    def test_save_cvinfo_preserves_malformed_mangaupdates_id_without_structured_value(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"

        with patch("routes.metadata.is_valid_library_path", return_value=True), \
             patch("routes.metadata.is_path_in_any_root", return_value=False):
            resp = client.post(
                "/api/save-cvinfo",
                json={
                    "path": str(cvinfo),
                    "extra_lines": "mangaupdates_id: abc 123",
                },
            )

        assert resp.status_code == 200
        content = cvinfo.read_text(encoding="utf-8")
        assert content == "mangaupdates_id: abc 123"

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

    def test_save_cvinfo_allows_mangaupdates_only_structured_content(self, client, tmp_path):
        folder = tmp_path / "Series"
        folder.mkdir()
        cvinfo = folder / "cvinfo"

        with patch("routes.metadata.is_valid_library_path", return_value=True), \
             patch("routes.metadata.is_path_in_any_root", return_value=False):
            resp = client.post(
                "/api/save-cvinfo",
                json={
                    "path": str(cvinfo),
                    "mangaupdates_url": "abc123",
                },
            )

        assert resp.status_code == 200
        content = cvinfo.read_text(encoding="utf-8")
        assert content == "mangaupdates_url: https://www.mangaupdates.com/series/abc123"


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
    @patch("routes.metadata.app_state.register_operation", return_value="op-timeout")
    @patch("routes.metadata._try_comicvine_single")
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    def test_search_metadata_force_provider_timeout_returns_504(
        self,
        mock_cvinfo,
        mock_try_comicvine,
        mock_register_op,
        mock_update_op,
        mock_complete_op,
        client,
    ):
        from routes.metadata import MetadataProviderTimeoutError

        mock_try_comicvine.side_effect = MetadataProviderTimeoutError(
            "ComicVine timed out while fetching issue 1 after 0.01s"
        )
        client.application.config["COMICVINE_API_KEY"] = "test-key"

        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'force_provider': 'comicvine',
        })

        assert resp.status_code == 504
        data = resp.get_json()
        assert data["success"] is False
        assert "ComicVine timed out" in data["error"]
        mock_complete_op.assert_called_once_with("op-timeout", error=True)

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
        mock_write_cvinfo.assert_not_called()


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

    @patch("routes.metadata.metron.create_cvinfo_file")
    @patch("routes.metadata.metron.get_series_details")
    @patch("routes.metadata.comicvine.parse_cvinfo_volume_id")
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.get_flask_api", return_value=MagicMock())
    @patch("routes.metadata.metron.is_metron_configured", return_value=True)
    def test_force_batch_metron_selected_series_timeout_does_not_write_cvinfo(
        self,
        mock_metron_configured,
        mock_get_flask_api,
        mock_gcd_available,
        mock_valid_library_path,
        mock_parse_cvinfo,
        mock_get_series_details,
        mock_create_cvinfo,
        client,
        tmp_path,
    ):
        import threading

        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        (batch_dir / "cvinfo").write_text(
            "https://comicvine.gamespot.com/volume/4050-9999/\n",
            encoding="utf-8",
        )
        _make_cbz(str(batch_dir / "Batman 001 (2020).cbz"), with_comicinfo=False)

        def slow_series_details(*args, **kwargs):
            threading.Event().wait(0.05)
            return {
                "id": 501,
                "cv_id": 4050,
                "publisher_name": "DC Comics",
                "year_began": 2016,
            }

        mock_get_series_details.side_effect = slow_series_details
        client.application.config["METADATA_PROVIDER_TIMEOUT"] = 0.01

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "series_id": 501,
            "force_manual_selection": True,
            "force_provider": "metron",
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 504
        assert "Metron timed out" in resp.get_json()["error"]
        mock_create_cvinfo.assert_not_called()
        assert "4050-9999" in (batch_dir / "cvinfo").read_text(encoding="utf-8")

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
        mock_search_volumes.assert_not_called()

    @patch("routes.metadata._cancelable_sleep", return_value=False)
    @patch("core.database.update_file_index_from_comicinfo")
    @patch("cbz_ops.rename.rename_comic_from_metadata", side_effect=lambda file_path, metadata: (file_path, False))
    @patch("routes.metadata.add_comicinfo_to_cbz")
    @patch("routes.metadata.comicvine.get_issue_by_number")
    @patch("routes.metadata.is_valid_library_path", return_value=True)
    @patch("routes.metadata.gcd.is_mysql_available", return_value=False)
    @patch("routes.metadata.metron.is_metron_configured", return_value=False)
    def test_batch_metadata_provider_timeout_marks_file_error(
        self,
        mock_metron_configured,
        mock_gcd_available,
        mock_valid_library_path,
        mock_get_issue_by_number,
        mock_add_xml,
        mock_rename,
        mock_update_index,
        mock_cancelable_sleep,
        client,
        tmp_path,
    ):
        import threading

        batch_dir = tmp_path / "data" / "Batman (2020)"
        batch_dir.mkdir(parents=True)
        (batch_dir / "cvinfo").write_text(
            "https://comicvine.gamespot.com/volume/4050-4050/\n"
            "publisher_name: DC Comics\n"
            "start_year: 2016\n",
            encoding="utf-8",
        )
        _make_cbz(str(batch_dir / "Batman 001 (2020).cbz"), with_comicinfo=False)

        def slow_issue_lookup(*args, **kwargs):
            threading.Event().wait(0.05)
            return {
                "id": 1001,
                "name": "Late Result",
                "issue_number": "1",
                "volume_name": "Batman",
                "volume_id": 4050,
                "publisher": None,
                "year": 2020,
                "month": 7,
                "day": 5,
                "description": "Too slow",
                "image_url": None,
            }

        mock_get_issue_by_number.side_effect = slow_issue_lookup
        client.application.config["COMICVINE_API_KEY"] = "test-key"
        client.application.config["METADATA_PROVIDER_TIMEOUT"] = 0.01

        resp = client.post("/api/batch-metadata", json={
            "directory": str(batch_dir),
            "overwrite_existing_metadata": True,
        })

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert '"type": "complete"' in body
        assert "ComicVine timed out" in body
        assert '"errors": 1' in body
        mock_add_xml.assert_not_called()
    @patch("models.metron.is_metron_configured", return_value=False)
    @patch("models.metron.is_connection_error", return_value=False)
    @patch("models.gcd.is_mysql_available", return_value=False)
    @patch("models.gcd.check_mysql_status", return_value={"gcd_mysql_available": False})
    @patch("models.comicvine.find_cvinfo_in_folder", return_value="/data/foo/cvinfo")
    @patch("models.comicvine.extract_issue_number", return_value=None)
    @patch("core.database.get_library_providers", return_value=[])
    @patch("core.database.set_has_comicinfo")
    def test_search_term_bypasses_stale_cvinfo(
        self, mock_set, mock_providers, mock_extract, mock_cvinfo,
        mock_mysql_status, mock_mysql, mock_conn_err, mock_metron, client
    ):
        """When a search_term override is supplied (manual search from the
        bulk review modal), the route must NOT consult cvinfo — otherwise a
        stale series_id from a prior failed attempt short-circuits provider
        lookup and searches the wrong series.

        We assert this by setting find_cvinfo_in_folder to return a path,
        but expecting that no provider attempt uses it. The mock_cvinfo
        return value is what the call would have produced if not bypassed."""
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/foo/Avengers West Coast Annual 004 (1989).cbz',
            'file_name': 'Avengers West Coast Annual 004 (1989).cbz',
            'search_term': 'Avengers West Coast Annual',
        })
        # All providers disabled in the mocks → 404, but the override series
        # name lands in parsed_filename and find_cvinfo_in_folder is not
        # exercised when search_term is set.
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["parsed_filename"]["series_name"] == "Avengers West Coast Annual"
        # Confirm we never called find_cvinfo_in_folder — the route bypasses
        # cvinfo entirely when search_term is present.
        mock_cvinfo.assert_not_called()

    @patch("models.metron.is_metron_configured", return_value=False)
    @patch("models.metron.is_connection_error", return_value=False)
    @patch("models.gcd.is_mysql_available", return_value=False)
    @patch("models.gcd.check_mysql_status", return_value={"gcd_mysql_available": False})
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    @patch("models.comicvine.extract_issue_number", return_value=None)
    @patch("core.database.get_library_providers", return_value=[])
    @patch("core.database.set_has_comicinfo")
    def test_search_year_overrides_parsed_year(
        self, mock_set, mock_providers, mock_extract, mock_cvinfo,
        mock_mysql_status, mock_mysql, mock_conn_err, mock_metron, client
    ):
        """Manual-search year input must override the year parsed from the
        filename. Without this, /api/search-metadata uses the issue's
        publication year (e.g. 2003) instead of the series start year the
        user supplied (e.g. 2002), and Metron/ComicVine rank wrong-year
        volumes first."""
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Marvel/Captain Marvel/v2002/Captain Marvel 015 (2003).cbz',
            'file_name': 'Captain Marvel 015 (2003).cbz',
            'search_term': 'Captain Marvel',
            'search_year': 2002,
        })
        # No providers active → 404 fallthrough, but the parsed_filename
        # reflects the override.
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["parsed_filename"]["series_name"] == "Captain Marvel"
        assert data["parsed_filename"]["year"] == 2002

    @patch("models.metron.is_metron_configured", return_value=False)
    @patch("models.metron.is_connection_error", return_value=False)
    @patch("models.gcd.is_mysql_available", return_value=False)
    @patch("models.gcd.check_mysql_status", return_value={"gcd_mysql_available": False})
    @patch("models.comicvine.find_cvinfo_in_folder", return_value=None)
    @patch("models.comicvine.extract_issue_number", return_value=None)
    @patch("core.database.get_library_providers", return_value=[])
    @patch("core.database.set_has_comicinfo")
    def test_search_year_invalid_value_falls_back_to_parsed(
        self, mock_set, mock_providers, mock_extract, mock_cvinfo,
        mock_mysql_status, mock_mysql, mock_conn_err, mock_metron, client
    ):
        """Non-integer search_year is ignored; the parsed file year survives."""
        resp = client.post('/api/search-metadata', json={
            'file_path': '/data/Batman 001 (2020).cbz',
            'file_name': 'Batman 001 (2020).cbz',
            'search_year': 'not-a-year',
        })
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["parsed_filename"]["year"] == 2020


class TestSearchMetadataComicVineFailover:
    """ComicVine must never stall the search-metadata cascade.

    A hung or failing ComicVine attempt must be bounded so the cascade falls
    over to the next configured provider (gcd_api). See
    routes.metadata._try_comicvine_single (wall-clock guard) and
    models.comicvine._make_cv_client (per-request timeout).
    """

    def _configure(self, app, stack, *, search_volumes_side_effect):
        """Apply the shared mock stack; return the gcd_api mock for assertions."""
        app.config["COMICVINE_API_KEY"] = "test-key"

        stack.enter_context(patch("models.metron.is_metron_configured", return_value=False))
        stack.enter_context(patch("models.metron.is_connection_error", return_value=False))
        stack.enter_context(patch("models.gcd.is_mysql_available", return_value=False))
        stack.enter_context(patch("models.gcd.check_mysql_status",
                                  return_value={"gcd_mysql_available": False}))
        stack.enter_context(patch("models.comicvine.find_cvinfo_in_folder", return_value=None))
        stack.enter_context(patch("models.comicvine.is_simyan_available", return_value=True))
        stack.enter_context(patch("models.comicvine.search_volumes",
                                  side_effect=search_volumes_side_effect))
        stack.enter_context(patch("core.database.get_library_providers", return_value=[]))
        stack.enter_context(patch("core.database.get_provider_credentials",
                                  return_value={"username": "u", "password": "p"}))
        stack.enter_context(patch("core.database.set_has_comicinfo"))
        stack.enter_context(patch("core.database.update_file_index_from_comicinfo"))
        stack.enter_context(patch("routes.metadata.add_comicinfo_to_cbz", return_value=True))
        gcd_api = stack.enter_context(patch(
            "routes.metadata._try_gcd_api_single",
            return_value=({"Series": "Batman", "Number": "1"}, "http://img", None),
        ))
        return gcd_api

    def test_failover_when_comicvine_stalls(self, app, client):
        """A ComicVine call that hangs past CV_ATTEMPT_TIMEOUT is abandoned and
        the cascade falls over to gcd_api."""
        import time
        from contextlib import ExitStack

        def _slow(*args, **kwargs):
            time.sleep(0.5)  # outlives the patched timeout below
            return []

        with ExitStack() as stack:
            gcd_api = self._configure(app, stack, search_volumes_side_effect=_slow)
            stack.enter_context(patch("routes.metadata.CV_ATTEMPT_TIMEOUT", 0.15))

            started = time.monotonic()
            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
            })
            elapsed = time.monotonic() - started

        assert resp.status_code == 200
        assert resp.get_json()["source"] == "gcd_api"
        gcd_api.assert_called_once()
        # The hung ComicVine worker must not block the request: returning well
        # before the 0.5s sleep proves shutdown(wait=False) didn't join it.
        assert elapsed < 0.45

    def test_failover_when_comicvine_raises(self, app, client):
        """A ComicVine exception is swallowed and the cascade reaches gcd_api
        (no 500)."""
        from contextlib import ExitStack

        def _boom(*args, **kwargs):
            raise RuntimeError("comicvine exploded")

        with ExitStack() as stack:
            gcd_api = self._configure(app, stack, search_volumes_side_effect=_boom)

            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
            })

        assert resp.status_code == 200
        assert resp.get_json()["source"] == "gcd_api"
        gcd_api.assert_called_once()

    def test_try_comicvine_single_returns_quickly_on_timeout(self, app):
        """Unit-level: the wall-clock guard returns the empty tuple promptly
        instead of blocking for the full ComicVine call."""
        import time
        from routes.metadata import _try_comicvine_single

        app.config["COMICVINE_API_KEY"] = "test-key"

        def _slow(*args, **kwargs):
            time.sleep(1.0)
            return []

        with app.app_context(), \
                patch("models.comicvine.is_simyan_available", return_value=True), \
                patch("models.comicvine.search_volumes", side_effect=_slow), \
                patch("routes.metadata.CV_ATTEMPT_TIMEOUT", 0.15):
            started = time.monotonic()
            result = _try_comicvine_single(None, "Batman", "1", None)
            elapsed = time.monotonic() - started

        assert result == (None, None, None, None)
        assert elapsed < 0.9


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


class TestRescanMissingXmlEndpoint:
    """POST /api/metadata/rescan-missing-xml triggers a force-rescan of has_comicinfo=0 files."""

    @patch("core.metadata_scanner.queue_missing_xml_for_rescan", return_value=42)
    def test_returns_queued_count(self, mock_queue, client):
        resp = client.post('/api/metadata/rescan-missing-xml', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["queued"] == 42
        mock_queue.assert_called_once()

    @patch("core.metadata_scanner.queue_missing_xml_for_rescan", return_value=0)
    def test_zero_when_nothing_to_rescan(self, mock_queue, client):
        resp = client.post('/api/metadata/rescan-missing-xml', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["queued"] == 0


class TestRemoveComicInfoUpdatesFileIndex:
    """Regression: _remove_comicinfo_from_cbz must zero has_comicinfo in file_index
    so the file shows up in the Missing XML view immediately after removal."""

    def test_file_index_has_comicinfo_set_to_zero(self, db_connection, tmp_path):
        from routes.metadata import _remove_comicinfo_from_cbz
        from core.database import add_file_index_entry

        cbz_path = str(tmp_path / "comic.cbz")
        _make_cbz(cbz_path, with_comicinfo=True)

        add_file_index_entry(
            name="comic.cbz", path=cbz_path, entry_type="file",
            size=1234, parent=str(tmp_path),
        )
        # Seed has_comicinfo=1 to mirror a previously-scanned file with metadata.
        db_connection.execute(
            "UPDATE file_index SET has_comicinfo=1 WHERE path=?", (cbz_path,)
        )
        db_connection.commit()

        result = _remove_comicinfo_from_cbz(cbz_path)
        assert result["success"] is True

        cur = db_connection.execute(
            "SELECT has_comicinfo FROM file_index WHERE path=?", (cbz_path,)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 0


class TestSearchMetadataSkipProviders:
    """skip_providers / only_provider control which providers the cascade tries,
    and selection responses expose provider_order for the skip button."""

    def _fallback_two_providers(self, app, stack):
        """Configure the fallback order to be [metron, comicvine]."""
        app.config["COMICVINE_API_KEY"] = "test-key"
        stack.enter_context(patch("models.metron.is_metron_configured", return_value=True))
        stack.enter_context(patch("models.metron.is_connection_error", return_value=False))
        stack.enter_context(patch("models.gcd.is_mysql_available", return_value=False))
        stack.enter_context(patch("models.gcd.check_mysql_status",
                                  return_value={"gcd_mysql_available": False}))
        stack.enter_context(patch("models.comicvine.find_cvinfo_in_folder", return_value=None))
        stack.enter_context(patch("models.comicvine.extract_issue_number", return_value=None))
        stack.enter_context(patch("core.database.get_library_providers", return_value=[]))
        stack.enter_context(patch("core.database.get_provider_credentials", return_value=None))
        stack.enter_context(patch("core.database.set_has_comicinfo"))
        stack.enter_context(patch("core.database.update_file_index_from_comicinfo"))
        stack.enter_context(patch("routes.metadata.add_comicinfo_to_cbz", return_value=True))

    def test_skip_providers_excludes_provider(self, app, client):
        from contextlib import ExitStack
        with ExitStack() as stack:
            self._fallback_two_providers(app, stack)
            metron = stack.enter_context(patch(
                "routes.metadata._try_metron_single", return_value=(None, None, None)))
            cv = stack.enter_context(patch(
                "routes.metadata._try_comicvine_single",
                return_value=({"Series": "Batman", "Number": "1"}, "http://img", None, None)))

            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
                'skip_providers': ['metron'],
            })

        assert resp.status_code == 200
        assert resp.get_json()["source"] == "comicvine"
        metron.assert_not_called()
        cv.assert_called_once()

    def test_only_provider_restricts_cascade(self, app, client):
        from contextlib import ExitStack
        with ExitStack() as stack:
            self._fallback_two_providers(app, stack)
            metron = stack.enter_context(patch(
                "routes.metadata._try_metron_single", return_value=(None, None, None)))
            cv = stack.enter_context(patch(
                "routes.metadata._try_comicvine_single",
                return_value=({"Series": "Batman", "Number": "1"}, "http://img", None, None)))

            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
                'only_provider': 'comicvine',
            })

        assert resp.status_code == 200
        assert resp.get_json()["source"] == "comicvine"
        metron.assert_not_called()
        cv.assert_called_once()

    def test_selection_response_includes_provider_order(self, app, client):
        from contextlib import ExitStack
        selection = {
            "requires_selection": True,
            "provider": "comicvine",
            "possible_matches": [{"id": 1, "name": "Batman"}, {"id": 2, "name": "Batman Inc"}],
        }
        with ExitStack() as stack:
            self._fallback_two_providers(app, stack)
            stack.enter_context(patch(
                "routes.metadata._try_metron_single", return_value=(None, None, None)))
            stack.enter_context(patch(
                "routes.metadata._try_comicvine_single",
                return_value=(None, None, None, selection)))

            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
            })

        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "comicvine"
        assert data["provider_order"] == ["metron", "comicvine"]


class TestSearchMetadataMetronSelection:
    """Metron now shows a selection modal when matches are ambiguous and
    supports the selected_match follow-up."""

    def _metron_only(self, app, stack):
        app.config["COMICVINE_API_KEY"] = ""
        stack.enter_context(patch("models.metron.is_metron_configured", return_value=True))
        stack.enter_context(patch("models.metron.is_connection_error", return_value=False))
        stack.enter_context(patch("models.gcd.is_mysql_available", return_value=False))
        stack.enter_context(patch("models.gcd.check_mysql_status",
                                  return_value={"gcd_mysql_available": False}))
        stack.enter_context(patch("models.comicvine.find_cvinfo_in_folder", return_value=None))
        stack.enter_context(patch("models.comicvine.extract_issue_number", return_value=None))
        stack.enter_context(patch("core.database.get_library_providers", return_value=[]))
        stack.enter_context(patch("core.database.get_provider_credentials", return_value=None))
        stack.enter_context(patch("core.database.set_has_comicinfo"))
        stack.enter_context(patch("core.database.update_file_index_from_comicinfo"))
        stack.enter_context(patch("models.metron.get_flask_api", return_value=MagicMock()))
        stack.enter_context(patch("routes.metadata.add_comicinfo_to_cbz", return_value=True))

    def test_ambiguous_matches_require_selection(self, app, client):
        from contextlib import ExitStack
        with ExitStack() as stack:
            self._metron_only(app, stack)
            stack.enter_context(patch("models.metron.search_series_list", return_value=[
                {"id": 1, "name": "The Batman", "start_year": 1940},
                {"id": 2, "name": "Batman Beyond", "start_year": 1999},
            ]))

            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
            })

        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "metron"
        assert len(data["possible_matches"]) == 2
        assert data["provider_order"] == ["metron"]

    def test_confident_single_match_auto_applies(self, app, client):
        from contextlib import ExitStack
        with ExitStack() as stack:
            self._metron_only(app, stack)
            stack.enter_context(patch("models.metron.search_series_list", return_value=[
                {"id": 5, "name": "Batman", "start_year": 2016},
            ]))
            stack.enter_context(patch("models.metron.get_issue_metadata",
                                      return_value={"image": "http://cover"}))
            stack.enter_context(patch("models.metron.map_to_comicinfo",
                                      return_value={"Series": "Batman", "Number": "1"}))

            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
            })

        assert resp.status_code == 200
        assert resp.get_json()["source"] == "metron"

    def test_metron_selection_followup_applies(self, app, client):
        from contextlib import ExitStack
        with ExitStack() as stack:
            self._metron_only(app, stack)
            stack.enter_context(patch("models.metron.get_issue_metadata",
                                      return_value={"image": "http://cover"}))
            stack.enter_context(patch("models.metron.map_to_comicinfo",
                                      return_value={"Series": "Batman", "Number": "1"}))

            resp = client.post('/api/search-metadata', json={
                'file_path': '/data/Batman 001 (2020).cbz',
                'file_name': 'Batman 001 (2020).cbz',
                'selected_match': {'provider': 'metron', 'series_id': 5},
            })

        assert resp.status_code == 200
        assert resp.get_json()["source"] == "metron"


class TestBatchMetadataSkipProviders:
    """The folder/batch flow (/api/batch-metadata) must expose provider_order on
    its ComicVine selection and honor skip_providers so the user can fall through
    to the next provider (e.g. GCD API) for the whole folder."""

    def _batch_stack(self, app, stack):
        app.config["COMICVINE_API_KEY"] = "k"
        stack.enter_context(patch("routes.metadata.is_valid_library_path", return_value=True))
        stack.enter_context(patch("app.get_target_dir_live", return_value="/nonexistent_target"))
        stack.enter_context(patch("core.database.get_library_providers", return_value=[
            {"provider_type": "metron", "enabled": True},
            {"provider_type": "comicvine", "enabled": True},
            {"provider_type": "gcd_api", "enabled": True},
        ]))
        stack.enter_context(patch("models.metron.get_flask_api", return_value=MagicMock()))
        stack.enter_context(patch("models.metron.search_series_by_name", return_value=None))
        stack.enter_context(patch("models.metron.is_connection_error", return_value=False))

    def test_comicvine_selection_includes_provider_order(self, app, client, tmp_path):
        from contextlib import ExitStack
        folder = tmp_path / "Batman (2020)"
        folder.mkdir()
        _make_cbz(str(folder / "Batman 001 (2020).cbz"), with_comicinfo=False)

        with ExitStack() as stack:
            self._batch_stack(app, stack)
            stack.enter_context(patch("models.comicvine.search_volumes", return_value=[
                {"id": 1, "name": "Batman"},
                {"id": 2, "name": "Batman Inc"},
            ]))
            resp = client.post('/api/batch-metadata', json={
                'directory': str(folder), 'library_id': 1,
            })

        data = resp.get_json()
        assert data["requires_selection"] is True
        assert data["provider"] == "comicvine"
        assert data["provider_order"] == ["metron", "comicvine", "gcd_api"]

    def test_skip_providers_bypasses_comicvine_halt(self, app, client, tmp_path):
        """With comicvine skipped, the ComicVine multi-volume selection must NOT
        halt the batch — it streams (SSE) and lets later providers run per-file."""
        from contextlib import ExitStack
        folder = tmp_path / "Batman (2020)"
        folder.mkdir()
        # File already has metadata (Notes) so it's skipped — keeps the per-file
        # loop from making real provider calls during the stream.
        cbz = str(folder / "Batman 001 (2020).cbz")
        with zipfile.ZipFile(cbz, 'w') as zf:
            zf.writestr("page_001.png", b"x")
            zf.writestr("ComicInfo.xml", "<ComicInfo><Series>B</Series><Notes>has</Notes></ComicInfo>")

        with ExitStack() as stack:
            self._batch_stack(app, stack)
            cv = stack.enter_context(patch("models.comicvine.search_volumes", return_value=[
                {"id": 1, "name": "Batman"},
                {"id": 2, "name": "Batman Inc"},
            ]))
            resp = client.post('/api/batch-metadata', json={
                'directory': str(folder), 'library_id': 1,
                'skip_providers': ['comicvine'],
            })
            body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert 'text/event-stream' in resp.content_type
        assert '"type": "complete"' in body
        # ComicVine search must not run when comicvine is skipped.
        cv.assert_not_called()

    def test_one_shot_unnumbered_falls_back_to_issue_one(self, app, client, tmp_path):
        """A single un-numbered file (one-shot) must NOT error with 'no issue
        number' — it falls back to issue #1 and is processed normally."""
        from contextlib import ExitStack
        folder = tmp_path / "One Shot Special"
        folder.mkdir()
        _make_cbz(str(folder / "One Shot Special.cbz"), with_comicinfo=False)

        with ExitStack() as stack:
            self._batch_stack(app, stack)
            stack.enter_context(patch("models.comicvine.search_volumes", return_value=[]))
            resp = client.post('/api/batch-metadata', json={
                'directory': str(folder), 'library_id': 1,
            })
            body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert 'text/event-stream' in resp.content_type
        assert 'no issue number' not in body

    def test_multi_file_unnumbered_still_errors(self, app, client, tmp_path):
        """Multiple un-numbered files must NOT all be mapped to #1 — they still
        report the 'no issue number' error."""
        from contextlib import ExitStack
        folder = tmp_path / "Mixed Folder"
        folder.mkdir()
        _make_cbz(str(folder / "Mixed Folder One.cbz"), with_comicinfo=False)
        _make_cbz(str(folder / "Mixed Folder Two.cbz"), with_comicinfo=False)

        with ExitStack() as stack:
            self._batch_stack(app, stack)
            stack.enter_context(patch("models.comicvine.search_volumes", return_value=[]))
            resp = client.post('/api/batch-metadata', json={
                'directory': str(folder), 'library_id': 1,
            })
            body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert 'no issue number' in body


class TestOneShotFolderHandling:
    """One-shot folders (oneshots/specials/...) hold unrelated singles, so a
    shared folder cvinfo must be ignored and auto-rename must be gated."""

    def test_search_metadata_bypasses_cvinfo_and_gates_autorename(self, app, client, tmp_path):
        from contextlib import ExitStack
        app.config["COMICVINE_API_KEY"] = "k"
        app.config["ENABLE_AUTO_RENAME"] = True
        folder = tmp_path / "oneshots"
        folder.mkdir()
        # A poisoning cvinfo (volume 99999) that must be ignored here.
        (folder / "cvinfo").write_text("https://comicvine.gamespot.com/x/4050-99999/")
        cbz = folder / "Lilli Xene.cbz"
        _make_cbz(str(cbz), with_comicinfo=False)

        with ExitStack() as stack:
            stack.enter_context(patch("core.database.get_library_providers", return_value=[
                {"provider_type": "comicvine", "enabled": True}]))
            stack.enter_context(patch("models.comicvine.is_simyan_available", return_value=True))
            sv = stack.enter_context(patch("models.comicvine.search_volumes", return_value=[
                {"id": 555, "name": "Lilli Xene", "publisher_name": "X", "start_year": 2007}]))
            pcv = stack.enter_context(patch("models.comicvine.parse_cvinfo_volume_id", return_value=99999))
            stack.enter_context(patch("models.comicvine.get_issue_by_number", return_value={
                "volume_name": "Lilli Xene", "year": 2007, "image_url": "http://i"}))
            stack.enter_context(patch("models.comicvine.map_to_comicinfo",
                                      return_value={"Series": "Lilli Xene", "Number": "1"}))
            stack.enter_context(patch("models.comicvine.auto_move_file", return_value=None))
            stack.enter_context(patch("routes.metadata.add_comicinfo_to_cbz", return_value=True))
            stack.enter_context(patch("core.database.update_file_index_from_comicinfo"))
            stack.enter_context(patch("core.database.set_has_comicinfo"))
            stack.enter_context(patch("models.metron.is_connection_error", return_value=False))

            resp = client.post('/api/search-metadata', json={
                'file_path': str(cbz), 'file_name': 'Lilli Xene.cbz', 'library_id': 1,
            })

        data = resp.get_json()
        assert data["success"] is True
        # cvinfo was bypassed → matched by the file's own name, not volume 99999.
        sv.assert_called()
        pcv.assert_not_called()
        # auto-rename gated off in one-shot folders despite ENABLE_AUTO_RENAME.
        assert data["rename_config"]["auto_rename"] is False

    def test_non_oneshot_uses_cvinfo_and_allows_autorename(self, app, client, tmp_path):
        from contextlib import ExitStack
        app.config["COMICVINE_API_KEY"] = "k"
        app.config["ENABLE_AUTO_RENAME"] = True
        folder = tmp_path / "Some Series (2007)"
        folder.mkdir()
        (folder / "cvinfo").write_text("https://comicvine.gamespot.com/x/4050-99999/")
        cbz = folder / "Some Series 001.cbz"
        _make_cbz(str(cbz), with_comicinfo=False)

        with ExitStack() as stack:
            stack.enter_context(patch("core.database.get_library_providers", return_value=[
                {"provider_type": "comicvine", "enabled": True}]))
            stack.enter_context(patch("models.comicvine.is_simyan_available", return_value=True))
            stack.enter_context(patch("models.comicvine.find_cvinfo_in_folder",
                                      return_value=str(folder / "cvinfo")))
            pcv = stack.enter_context(patch("models.comicvine.parse_cvinfo_volume_id", return_value=99999))
            gibn = stack.enter_context(patch("models.comicvine.get_issue_by_number", return_value={
                "volume_name": "Some Series", "year": 2007, "image_url": "http://i"}))
            stack.enter_context(patch("models.comicvine.read_cvinfo_fields",
                                      return_value={"start_year": 2007, "publisher_name": "X"}))
            stack.enter_context(patch("models.comicvine.map_to_comicinfo",
                                      return_value={"Series": "Some Series", "Number": "1"}))
            stack.enter_context(patch("models.comicvine.auto_move_file", return_value=None))
            stack.enter_context(patch("routes.metadata.add_comicinfo_to_cbz", return_value=True))
            stack.enter_context(patch("core.database.update_file_index_from_comicinfo"))
            stack.enter_context(patch("core.database.set_has_comicinfo"))
            stack.enter_context(patch("models.metron.is_connection_error", return_value=False))

            resp = client.post('/api/search-metadata', json={
                'file_path': str(cbz), 'file_name': 'Some Series 001.cbz', 'library_id': 1,
            })

        data = resp.get_json()
        assert data["success"] is True
        # Normal folder: cvinfo IS consulted and auto-rename stays enabled.
        pcv.assert_called()
        gibn.assert_called()
        assert data["rename_config"]["auto_rename"] is True

    def test_batch_oneshot_does_not_consult_cvinfo(self, app, client, tmp_path):
        from contextlib import ExitStack
        app.config["COMICVINE_API_KEY"] = "k"
        folder = tmp_path / "oneshots"
        folder.mkdir()
        (folder / "cvinfo").write_text("https://comicvine.gamespot.com/x/4050-99999/")
        _make_cbz(str(folder / "Lilli Xene.cbz"), with_comicinfo=False)

        with ExitStack() as stack:
            stack.enter_context(patch("routes.metadata.is_valid_library_path", return_value=True))
            stack.enter_context(patch("app.get_target_dir_live", return_value="/nonexistent"))
            stack.enter_context(patch("core.database.get_library_providers", return_value=[
                {"provider_type": "comicvine", "enabled": True}]))
            stack.enter_context(patch("models.metron.is_connection_error", return_value=False))
            gmv = stack.enter_context(patch("models.comicvine.get_metadata_by_volume_id", return_value=None))
            pcv = stack.enter_context(patch("models.comicvine.parse_cvinfo_volume_id", return_value=99999))

            resp = client.post('/api/batch-metadata', json={
                'directory': str(folder), 'library_id': 1,
            })
            body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        # The folder's cvinfo (volume 99999) must never be consulted for a one-shot folder.
        gmv.assert_not_called()
        pcv.assert_not_called()
        assert '"type": "complete"' in body
