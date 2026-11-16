import json
import tempfile
import unittest
from pathlib import Path

from automation.pbir_ops import inspect_report, update_page_metadata


class PbirOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.report_path = "Sample.Report"
        pages_directory = self.root / self.report_path / "definition" / "pages"
        page_directory = pages_directory / "page-a"
        page_directory.mkdir(parents=True)
        (self.root / self.report_path / "definition" / "report.json").write_text(
            json.dumps({"settings": {"useEnhancedTooltips": True}}), encoding="utf-8"
        )
        (pages_directory / "pages.json").write_text(
            json.dumps({"pageOrder": ["page-a"]}), encoding="utf-8"
        )
        (page_directory / "page.json").write_text(
            json.dumps({"name": "page-a", "displayName": "Page 1", "width": 1280, "height": 720}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_inspect_reports_pages_in_order(self) -> None:
        result = inspect_report(self.report_path, self.root)
        self.assertEqual(result["pages"][0]["displayName"], "Page 1")
        self.assertEqual(result["pages"][0]["visualCount"], 0)

    def test_update_page_metadata_preserves_other_fields(self) -> None:
        result = update_page_metadata(self.report_path, "page-a", "Overview", 1366, 768, self.root)
        page_file = self.root / self.report_path / "definition" / "pages" / "page-a" / "page.json"
        saved_page = json.loads(page_file.read_text(encoding="utf-8"))
        self.assertEqual(result["displayName"], "Overview")
        self.assertEqual(saved_page["name"], "page-a")
        self.assertEqual(saved_page["height"], 768)

    def test_report_paths_cannot_escape_workspace(self) -> None:
        with self.assertRaises(ValueError):
            inspect_report("../outside", self.root)

    def test_page_ids_cannot_escape_pages_directory(self) -> None:
        pages_file = self.root / self.report_path / "definition" / "pages" / "pages.json"
        pages_file.write_text(json.dumps({"pageOrder": ["../outside"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            inspect_report(self.report_path, self.root)

    def test_page_dimensions_are_bounded(self) -> None:
        with self.assertRaises(ValueError):
            update_page_metadata(self.report_path, "page-a", "Overview", 100, 768, self.root)


if __name__ == "__main__":
    unittest.main()