import tempfile
import unittest
from pathlib import Path

from automation.semantic_model_inspector import inspect_semantic_model


class SemanticModelInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temporary_directory.name) / "sample.SemanticModel"
        self.tables_path = self.model_path / "definition" / "tables"
        self.tables_path.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_discovers_table_columns_and_measures_from_tmdl(self) -> None:
        (self.tables_path / "Transactions.tmdl").write_text(
            "table Transactions\n"
            "\tmeasure 'Net Sales' = SUM(Transactions[Amount])\n"
            "\tcolumn Region\n"
            "\t\tdataType: string\n"
            "\tcolumn Amount\n"
            "\tpartition Transactions = m\n"
            "\t\tmode: import\n",
            encoding="utf-8",
        )

        result = inspect_semantic_model(self.model_path)

        self.assertEqual(
            result,
            {"tables": {"Transactions": {"columns": ["Region", "Amount"], "measures": ["Net Sales"]}}, "relationships": []},
        )

    def test_discovers_tables_recursively_and_unquotes_names(self) -> None:
        nested_directory = self.tables_path / "Folder"
        nested_directory.mkdir()
        (nested_directory / "Store.tmdl").write_text(
            "table 'Store Data'\n\tcolumn 'Store''s Region'\n", encoding="utf-8"
        )

        result = inspect_semantic_model(self.model_path / "definition")

        self.assertEqual(result["tables"]["Store Data"]["columns"], ["Store's Region"])

    def test_missing_tables_directory_fails_clearly(self) -> None:
        with self.assertRaises(FileNotFoundError):
            inspect_semantic_model(Path(self.temporary_directory.name) / "missing.SemanticModel")


if __name__ == "__main__":
    unittest.main()