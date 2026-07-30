"""L10 — residue grab-bag hardening. Small correctness/consistency fixes that
don't warrant their own module. Grouped here per build-loop.md C.4.
"""

import mihomes.config as config
from mihomes.services.gateways.calendar.ical import _unescape


class TestICalUnescape:
    def test_escaped_backslash_before_n_not_mangled(self):
        # RFC 5545: "\\n" is an escaped backslash followed by a literal 'n',
        # NOT a newline. Unescaping "\\" last turned it into backslash+newline.
        assert _unescape(r"\\n") == "\\n"

    def test_newline_still_unescaped(self):
        assert _unescape(r"a\nb") == "a\nb"

    def test_comma_and_semicolon(self):
        assert _unescape(r"a\,b\;c") == "a,b;c"

    def test_literal_backslash(self):
        assert _unescape(r"path\\to") == "path\\to"


class TestEnsureDirs:
    def test_ensure_dirs_covers_logs_and_whatsapp_auth(self, tmp_path, monkeypatch):
        # L10: ensure_dirs omitted LOGS_DIR and WHATSAPP_AUTH_DIR, so the first
        # write to either (logging, WhatsApp auth persist) hit a missing dir.
        logs = tmp_path / "logs"
        wa = tmp_path / "whatsapp-auth"
        monkeypatch.setattr(config, "LOGS_DIR", logs)
        monkeypatch.setattr(config, "WHATSAPP_AUTH_DIR", wa)
        config.ensure_dirs()
        assert logs.is_dir()
        assert wa.is_dir()
