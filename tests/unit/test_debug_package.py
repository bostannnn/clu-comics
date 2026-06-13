"""Unit tests for core/debug_package.py helpers (no Flask, no DB)."""
import json

from core import debug_package as dp


class TestIsSensitiveKey:

    def test_marker_substrings_match(self):
        assert dp._is_sensitive_key("COMICVINE_API_KEY")
        assert dp._is_sensitive_key("METRON_PASSWORD")
        assert dp._is_sensitive_key("api_token")
        assert dp._is_sensitive_key("client_secret")

    def test_explicit_keys_match(self):
        assert dp._is_sensitive_key("METRON_USERNAME")

    def test_client_id_variants_match(self):
        # Separators are stripped before matching, so all of these are caught.
        assert dp._is_sensitive_key("CF-Access-Client-Id")
        assert dp._is_sensitive_key("client_id")
        assert dp._is_sensitive_key("ClientId")
        assert dp._is_sensitive_key("CF-Access-Client-Secret")

    def test_plain_keys_not_sensitive(self):
        assert not dp._is_sensitive_key("BOOTSTRAP_THEME")
        assert not dp._is_sensitive_key("AUTOCONVERT")
        assert not dp._is_sensitive_key("custom_headers")
        assert not dp._is_sensitive_key("")


class TestRedactBlob:

    def test_masks_nested_header_dict_in_config_value(self):
        # HEADERS-style value: top-level key is innocuous, secrets are nested.
        value = ('{"CF-Access-Client-Id": "4tyjwrtyhjdtyj.access", '
                 '"CF-Access-Client-Secret": "e5yjthyjfghjsecret"}')
        out = dp._redact_blob(value)
        assert "4tyjwrtyhjdtyj.access" not in out
        assert "e5yjthyjfghjsecret" not in out
        # Structure / key names are preserved.
        assert "CF-Access-Client-Id" in out
        assert "CF-Access-Client-Secret" in out

    def test_masks_json_encoded_db_blob_with_escapes(self):
        # Mirrors a JSON-encoded preference value (escaped inner quotes).
        value = ('"{\\"CF-Access-Client-Id\\": \\"4tyjwrtyhjdtyj.access\\",'
                 '\\"CF-Access-Client-Secret\\":\\"e5yjthyjfghjsecret\\"}"')
        out = dp._redact_blob(value)
        assert "4tyjwrtyhjdtyj.access" not in out
        assert "e5yjthyjfghjsecret" not in out

    def test_leaves_non_secret_pairs_untouched(self):
        value = '{"theme": "darkly", "limit": "10"}'
        assert dp._redact_blob(value) == value

    def test_passthrough_non_string(self):
        assert dp._redact_blob(None) is None
        assert dp._redact_blob("") == ""


class TestRedactedConfigIni:

    def test_secrets_masked_others_preserved(self, tmp_path):
        cfg = tmp_path / "config.ini"
        cfg.write_text(
            "[SETTINGS]\n"
            "COMICVINE_API_KEY = SECRET1234VALUE\n"
            "BOOTSTRAP_THEME = darkly\n"
        )
        out = dp._redacted_config_ini(str(cfg))
        assert "SECRET1234VALUE" not in out
        assert "BOOTSTRAP_THEME = darkly" in out
        assert "..." in out  # masked value present

    def test_empty_secret_left_alone(self, tmp_path):
        cfg = tmp_path / "config.ini"
        cfg.write_text("[SETTINGS]\nPIXELDRAIN_API_KEY = \n")
        out = dp._redacted_config_ini(str(cfg))
        # Empty value stays empty (nothing to mask)
        assert "PIXELDRAIN_API_KEY" in out

    def test_absolute_paths_masked_but_relative_patterns_preserved(self, tmp_path):
        cfg = tmp_path / "config.ini"
        cfg.write_text(
            "[SETTINGS]\n"
            "CACHE_DIR = /srv/clu cache\n"
            "TRASH_DIR = /opt/clu-trash\n"
            "CUSTOM_MOVE_PATTERN = {publisher}/{series_name}/v{start_year}\n"
        )
        out = dp._redacted_config_ini(str(cfg))

        assert "/srv/clu cache" not in out
        assert "/opt/clu-trash" not in out
        assert out.count("[PATH REDACTED]") == 2
        assert "CUSTOM_MOVE_PATTERN = {publisher}/{series_name}/v{start_year}" in out

    def test_missing_file_placeholder(self, tmp_path):
        out = dp._redacted_config_ini(str(tmp_path / "nope.ini"))
        assert "not found" in out


class TestTail:

    def test_returns_last_n_lines(self, tmp_path):
        log = tmp_path / "app.log"
        log.write_text("".join(f"line {i}\n" for i in range(100)))
        out = dp._tail(str(log), lines=10)
        assert out.splitlines() == [f"line {i}" for i in range(90, 100)]

    def test_missing_file_placeholder(self, tmp_path):
        out = dp._tail(str(tmp_path / "missing.log"))
        assert "not found" in out


class TestLogRedaction:

    def test_redacts_paths_urls_and_secret_assignments(self):
        text = (
            "FILE: /Volumes/Comics/Batman/Batman 001.cbz\n"
            "Resolved -> https://example.com/download?token=SECRET1234VALUE\n"
            "COMICVINE_API_KEY=SECRET1234VALUE\n"
            "custom_headers={\"CF-Access-Client-Secret\": \"e5yjthyjfghjsecret\"}\n"
        )

        out = dp._redact_log_text(text)

        assert "/Volumes/Comics" not in out
        assert "https://example.com" not in out
        assert "SECRET1234VALUE" not in out
        assert "e5yjthyjfghjsecret" not in out
        assert "[PATH REDACTED]" in out
        assert "[URL REDACTED]" in out

    def test_redacts_full_paths_with_spaces(self):
        text = (
            "FILE: /Volumes/Comic Books/Batman/Batman 001.cbz\n"
            "Source path: /Users/me/Comic Library/Action Comics 001.cbz (exists: True)\n"
        )

        out = dp._redact_log_text(text)

        assert "/Volumes/Comic Books" not in out
        assert "Books/Batman" not in out
        assert "Batman 001.cbz" not in out
        assert "/Users/me" not in out
        assert "Comic Library" not in out
        assert "Action Comics 001.cbz" not in out
        assert out.count("[PATH REDACTED]") == 2

    def test_redacts_custom_absolute_paths_without_known_root(self):
        text = (
            "Watching: /srv/comic library/Batman/Batman 001.cbz\n"
            "Archive path: /opt/clu/incoming/Action Comics 001.cbz\n"
        )

        out = dp._redact_log_text(text)

        assert "/srv/comic library" not in out
        assert "/opt/clu" not in out
        assert "Batman 001.cbz" not in out
        assert "Action Comics 001.cbz" not in out
        assert out.count("[PATH REDACTED]") == 2

    def test_sanitize_value_redacts_user_preference_paths(self):
        out = dp._sanitize_value("watch", "/srv/downloads/temp")

        assert out == "[PATH REDACTED]"

    def test_redacted_tail_uses_tail_output(self, tmp_path):
        log = tmp_path / "app.log"
        log.write_text(
            "line 1\n"
            "FILE: /Users/me/Comics/Batman.cbz\n"
            "Resolved -> https://example.com/token\n"
        )

        out = dp._redacted_tail(str(log), lines=2)

        assert "line 1" not in out
        assert "/Users/me" not in out
        assert "https://example.com" not in out


class TestSystemInfoJson:

    def test_has_version_and_no_obvious_secret(self):
        info = json.loads(dp._system_info_json())
        assert "version" in info
        assert "paths" in info
        assert "flags" in info

    def test_paths_are_redacted(self):
        info = json.loads(dp._system_info_json())
        assert info["paths"]["config_dir"] == "[PATH REDACTED]"
        assert info["paths"]["log_dir"] == "[PATH REDACTED]"
        assert info["paths"]["config_file"] == "[PATH REDACTED]"
