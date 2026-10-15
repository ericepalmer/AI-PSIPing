import tempfile
import unittest
from pathlib import Path

from app.db import apply_layout, connect, delete_folder, insert_folder, insert_monitor, layout_snapshot


class FolderLayoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.directory.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.directory.cleanup()

    def add_site(self, name):
        return insert_monitor(
            self.conn,
            name=name,
            url=f"https://{name}.example",
            interval_seconds=60,
            timeout_seconds=30,
            alert_screen=True,
        )

    def test_reorder_folders_and_sites_together(self):
        first = self.add_site("one")
        second = self.add_site("two")
        folder = insert_folder(self.conn, "Apps")
        apply_layout(
            self.conn,
            [
                {"type": "folder", "id": folder["id"], "children": [second["id"]]},
                {"type": "monitor", "id": first["id"], "children": []},
            ],
        )
        self.assertEqual(
            layout_snapshot(self.conn),
            [
                {"type": "folder", "id": folder["id"], "children": [second["id"]]},
                {"type": "monitor", "id": first["id"], "children": []},
            ],
        )

    def test_delete_folder_keeps_its_sites_in_place(self):
        first = self.add_site("one")
        second = self.add_site("two")
        folder = insert_folder(self.conn, "Apps")
        apply_layout(
            self.conn,
            [
                {"type": "monitor", "id": first["id"], "children": []},
                {"type": "folder", "id": folder["id"], "children": [second["id"]]},
            ],
        )
        self.assertTrue(delete_folder(self.conn, folder["id"]))
        self.assertEqual(
            layout_snapshot(self.conn),
            [
                {"type": "monitor", "id": first["id"], "children": []},
                {"type": "monitor", "id": second["id"], "children": []},
            ],
        )

    def test_layout_rejects_a_missing_site(self):
        site = self.add_site("one")
        with self.assertRaises(ValueError):
            apply_layout(self.conn, [])
        self.assertEqual(layout_snapshot(self.conn)[0]["id"], site["id"])


if __name__ == "__main__":
    unittest.main()
