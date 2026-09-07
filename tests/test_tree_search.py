from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.request
import zipfile
import json
from pathlib import Path

import read_mrpack_page as page


class TreeSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "sample.mrpack"
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("modrinth.index.json", '{"files": []}')
            archive.writestr("overrides/config/lantern.txt", "A bright lantern lives here.")
            archive.writestr("overrides/config/other.txt", "Nothing to see.")
            archive.writestr("assets/icon.bin", b"\x00\x01binary")
        _, self.entries = page.parse_mrpack(self.path, "server")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_folder_match_retains_descendants(self) -> None:
        result, _ = page.search_archive_tree(self.path, self.entries, "config", None)
        self.assertEqual(result["matches"][0]["path"], "overrides/config")
        self.assertIn("overrides/config/lantern.txt", result["visible_paths"])
        self.assertIn("overrides/config/other.txt", result["visible_paths"])

    def test_text_match_skips_binary_entries(self) -> None:
        result, cache = page.search_archive_tree(self.path, self.entries, "bright lantern", None)
        self.assertEqual(result["matches"], [{"path": "overrides/config/lantern.txt", "sources": ["content"]}])
        self.assertNotIn(("assets", "icon.bin"), cache.contents)

    def test_state_discards_stale_revisions(self) -> None:
        pack, entries = page.parse_mrpack(self.path, "server")
        state = page.ServerState(1024 * 1024, None)
        state.replace_current(pack, self.path, entries)
        with self.assertRaises(page.PackError):
            state.search_tree("lantern", 0)

    def test_search_endpoint_returns_content_match(self) -> None:
        pack, entries = page.parse_mrpack(self.path, "server")
        state = page.ServerState(1024 * 1024, None)
        state.replace_current(pack, self.path, entries)
        server = page.MrpackServer(("127.0.0.1", 0), state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/search-tree",
                data=b'{"query":"bright lantern","revision":1}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                payload = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(payload["revision"], 1)
        self.assertEqual(payload["matches"], [{"path": "overrides/config/lantern.txt", "sources": ["content"]}])


if __name__ == "__main__":
    unittest.main()
