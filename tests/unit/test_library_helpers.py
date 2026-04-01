"""Tests for helpers/library.py path boundary handling."""
import os
from unittest.mock import patch


class TestPathIsWithinRoot:

    def test_matches_root_and_descendants(self, tmp_path):
        from helpers.library import path_is_within_root

        root = tmp_path / "library"
        child = root / "Series" / "Issue.cbz"
        child.parent.mkdir(parents=True)
        child.write_text("x")

        assert path_is_within_root(str(root), str(root)) is True
        assert path_is_within_root(str(child), str(root)) is True

    def test_rejects_sibling_prefix(self, tmp_path):
        from helpers.library import path_is_within_root

        root = tmp_path / "processed"
        sibling = tmp_path / "processed_evil" / "Issue.cbz"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("x")

        assert path_is_within_root(str(sibling), str(root)) is False

    def test_rejects_symlink_escape(self, tmp_path):
        from helpers.library import path_is_within_root

        root = tmp_path / "library"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        escape = root / "escape"
        escape.symlink_to(outside, target_is_directory=True)

        escaped_child = escape / "Issue.cbz"
        assert path_is_within_root(str(escaped_child), str(root)) is False


class TestIsValidLibraryPath:

    def test_rejects_symlink_escape_from_library_root(self, tmp_path):
        from helpers.library import is_valid_library_path

        library = tmp_path / "library"
        outside = tmp_path / "outside"
        library.mkdir()
        outside.mkdir()
        escape = library / "escape"
        escape.symlink_to(outside, target_is_directory=True)

        with patch("helpers.library.get_library_roots", return_value=[str(library)]):
            assert is_valid_library_path(str(escape / "Issue.cbz")) is False


class TestIsPathInAnyRoot:

    def test_accepts_when_in_any_allowed_root(self, tmp_path):
        from helpers.library import is_path_in_any_root

        watch = tmp_path / "watch"
        target = tmp_path / "processed"
        candidate = target / "Issue.cbz"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("x")

        assert is_path_in_any_root(str(candidate), [str(watch), str(target)]) is True
