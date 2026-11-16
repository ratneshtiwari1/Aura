import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from automation.report_automation import (
    _split_visual_instructions,
    _page_canvas_size,
    _visual_positions,
    create_pbip_project_file,
    create_report_spec,
    generate_pbir,
    package_pbip,
    validate_pbir,
    validate_report_spec,
)
from automation.design_system import THEME_PRESETS, build_theme, layout_positions, normalize_design_spec, register_custom_theme
from backend.services.pbir_service import ensure_report_layout
from backend.services.report_planner import check_visual_request


MODEL_METADATA = {
    "tables": {
        "Orders": {
            "columns": ["Territory", "Item"],
            "measures": ["Order Value"],
        }
    }
}


def _write_test_semantic_model(model_path: Path) -> None:
    definition = model_path / "definition"
    tables = definition / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    (model_path / "definition.pbism").write_text(json.dumps({"version": "4.0"}), encoding="utf-8")
    (definition / "database.tmdl").write_text("database Sales\n\tcompatibilityLevel: 1600\n", encoding="utf-8")
    (definition / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    (tables / "Sales.tmdl").write_text(
        "table Sales\n\tcolumn Region\n\tmeasure 'Total Revenue' = 1\n",
        encoding="utf-8",
    )


class ReportAutomationTests(unittest.TestCase):
    def test_report_layout_embeds_and_selects_the_dar_theme(self) -> None:
        """Power BI Desktop must be able to resolve the default DAR theme on open."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            report_dir = Path(temporary_directory) / "Sales.Report"
            ensure_report_layout(report_dir, "../Sales.SemanticModel")

            report_definition = json.loads(
                (report_dir / "definition" / "report.json").read_text(encoding="utf-8")
            )
            theme_path = report_dir / "StaticResources" / "SharedResources" / "BaseThemes" / "DAR Light.json"
            embedded_theme = json.loads(theme_path.read_text(encoding="utf-8"))

            self.assertEqual(report_definition["themeCollection"]["baseTheme"]["name"], "DAR Light")
            self.assertEqual(
                report_definition["resourcePackages"][0]["items"][0],
                {"name": "DAR Light", "path": "BaseThemes/DAR Light.json", "type": "BaseTheme"},
            )
            self.assertEqual(
                embedded_theme,
                build_theme(normalize_design_spec({"visuals": [], "design": {}})),
            )
            custom = report_definition["themeCollection"]["customTheme"]
            self.assertEqual(custom["type"], "RegisteredResources")
            self.assertEqual(custom["reportVersionAtImport"], report_definition["themeCollection"]["baseTheme"]["reportVersionAtImport"])
            name = custom["name"]
            self.assertTrue(name.startswith("DAR Light-"))
            self.assertTrue(name.endswith(".json"))
            package = report_definition["resourcePackages"][1]
            self.assertEqual(package["items"], [{"name": name, "path": name, "type": "CustomTheme"}])
            registered = json.loads((report_dir / "StaticResources" / "RegisteredResources" / name).read_text(encoding="utf-8"))
            self.assertEqual(registered, {**embedded_theme, "name": name})

    def test_custom_theme_registration_preserves_other_resources_and_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            report_dir = Path(temporary_directory)
            image = {"name": "logo.png", "path": "logo.png", "type": "Image"}
            definition = {"resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources", "items": [image]}]}
            theme = build_theme(normalize_design_spec({"visuals": []}))
            register_custom_theme(report_dir, definition, theme)
            first = definition["themeCollection"]["customTheme"]["name"]
            register_custom_theme(report_dir, definition, theme)
            self.assertEqual(first, definition["themeCollection"]["customTheme"]["name"])
            theme["dataColors"][0] = "#123456"
            register_custom_theme(report_dir, definition, theme)
            second = definition["themeCollection"]["customTheme"]["name"]
            self.assertNotEqual(first, second)
            self.assertEqual(definition["resourcePackages"][0]["items"], [
                image, {"name": second, "path": second, "type": "CustomTheme"},
            ])

    def test_validation_reports_missing_or_unregistered_custom_theme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            ensure_report_layout(report, "../Sales.SemanticModel")
            _write_test_semantic_model(workspace / "Sales.SemanticModel")
            definition_path = report / "definition" / "report.json"
            definition = json.loads(definition_path.read_text(encoding="utf-8"))
            name = definition["themeCollection"]["customTheme"]["name"]
            (report / "StaticResources" / "RegisteredResources" / name).unlink()
            definition["resourcePackages"][1]["items"].clear()
            definition_path.write_text(json.dumps(definition), encoding="utf-8")
            validation = validate_pbir("Sales.Report", root=workspace)
            self.assertTrue(any("Missing custom report theme resource" in error for error in validation["errors"]))
            self.assertTrue(any("not registered exactly once" in error for error in validation["errors"]))

    def test_request_fields_are_resolved_from_metadata(self) -> None:
        spec = create_report_spec("Create orders dashboard showing order value by territory.", MODEL_METADATA)
        self.assertEqual(spec["visuals"][0]["category"]["field"], "Territory")
        self.assertEqual(spec["visuals"][0]["value"]["field"], "Order Value")
        self.assertEqual(spec["design"]["theme"], "dar_light")

    def test_explicit_visual_count_includes_a_listed_slicer(self) -> None:
        metadata = {
            "tables": {
                "Revenue": {
                    "columns": ["Fiscal Year", "Business Unit", "Scenario"],
                    "measures": ["Total Net Revenue"],
                }
            },
            "preferredTable": "Revenue",
            "relationships": [],
        }
        prompts = [
            "Create an executive net revenue dashboard with 4 visuals: "
            "1. KPI card for total net revenue "
            "2. Total net revenue by Fiscal Year as a line chart "
            "3. Total net revenue by Business Unit as a bar chart "
            "4. Add a slicer for Scenario",
            "Create an executive net revenue dashboard with 4 visuals: "
            "KPI card for total net revenue, "
            "Total net revenue by Fiscal Year as a line chart, "
            "Total net revenue by Business Unit as a bar chart, "
            "Add a slicer for Scenario",
        ]

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                spec = create_report_spec(prompt, metadata)
                self.assertEqual(
                    [visual["type"] for visual in spec["visuals"]],
                    ["card", "lineChart", "clusteredBarChart", "slicer"],
                )

    def test_semicolon_separated_visual_list_is_preserved_and_validated(self) -> None:
        metadata = {
            "tables": {
                "SalesTransactions": {
                    "columns": ["Order Date", "Region", "Customer Segment"],
                    "columnTypes": {"Order Date": "date", "Region": "string", "Customer Segment": "string"},
                    "columnRoles": {"Order Date": "time", "Region": "geographic", "Customer Segment": "category"},
                    "columnProfiles": {},
                    "measures": ["Total Net Revenue"],
                    "measureDefinitions": {"Total Net Revenue": {"aggregation": "SUM"}},
                }
            },
            "preferredTable": "SalesTransactions",
            "relationships": [],
        }
        request = (
            "KPI card for Total Net Revenue; bar chart of Total Net Revenue by Region; "
            "line chart of Total Net Revenue by Order Date; slicer for Customer Segment"
        )

        assert _split_visual_instructions(request) == [
            "KPI card for Total Net Revenue",
            "bar chart of Total Net Revenue by Region",
            "line chart of Total Net Revenue by Order Date",
            "slicer for Customer Segment",
        ]
        reference = create_report_spec(request, metadata)
        assert [visual["type"] for visual in reference["visuals"]] == [
            "card", "clusteredBarChart", "lineChart", "slicer",
        ]
        check_visual_request(reference, request, metadata)

        incomplete = {**reference, "visuals": reference["visuals"][:2]}
        with self.assertRaisesRegex(ValueError, "differ from the requested list"):
            check_visual_request(incomplete, request, metadata)

    def test_chart_introduction_is_not_a_legend_in_explicit_visual_list(self) -> None:
        metadata = {
            "tables": {
                "Forecast": {
                    "columns": ["Year", "Scenario", "GDP Growth %"],
                    "measures": ["Average GDP Growth %"],
                    "columnRoles": {"Year": "time"},
                }
            },
            "preferredTable": "Forecast",
            "relationships": [],
        }
        request = (
            "\u201cCreate a forecast dashboard with a line chart of Average GDP Growth % "
            "by Year and a slicer for Scenario."
        )
        chart = {
            "type": "lineChart",
            "category": {"table": "Forecast", "field": "Year", "fieldType": "column"},
            "value": {"table": "Forecast", "field": "Average GDP Growth %", "fieldType": "measure"},
        }
        slicer = {
            "type": "slicer",
            "field": {"table": "Forecast", "field": "Scenario", "fieldType": "column"},
        }
        plan = {"page": "Forecast", "visuals": [chart, slicer]}
        self.assertEqual(create_report_spec(request, metadata)["visuals"], [chart, slicer])
        check_visual_request(plan, request, metadata)
        list_chart = {key: value for key, value in chart.items() if key != "value"}
        list_chart["values"] = [chart["value"]]
        list_chart["legend"] = None
        list_chart["categoryHierarchy"] = []
        check_visual_request({"page": "Forecast", "visuals": [list_chart, slicer]}, request, metadata)
        for visuals in (
            [chart],
            [{**chart, "legend": slicer["field"]}, slicer],
            [{**chart, "category": slicer["field"]}, slicer],
            [chart, {**slicer, "field": chart["category"]}],
            [{**list_chart, "values": [chart["value"], chart["value"]]}, slicer],
        ):
            with self.subTest(visuals=visuals):
                with self.assertRaisesRegex(ValueError, "differ from the requested list"):
                    check_visual_request({"page": "Forecast", "visuals": visuals}, request, metadata)

        with self.assertRaises(ValueError) as error:
            check_visual_request(
                {"page": "Forecast", "visuals": [{**chart, "category": slicer["field"]}, slicer]},
                request,
                metadata,
            )
        self.assertIn('"category": {"table": "Forecast", "field": "Year"', str(error.exception))
        self.assertNotIn('"field": "Scenario"', str(error.exception))

        with_legend = request.replace("by Year and a slicer", "by Year with Scenario as the legend and a slicer")
        expected_chart = {**chart, "legend": slicer["field"]}
        self.assertEqual(create_report_spec(with_legend, metadata)["visuals"], [expected_chart, slicer])
        check_visual_request({"page": "Forecast", "visuals": [expected_chart, slicer]}, with_legend, metadata)

    def test_design_presets_and_overrides_produce_valid_themes(self) -> None:
        for theme_name in THEME_PRESETS:
            with self.subTest(theme=theme_name):
                design = normalize_design_spec({
                    "visuals": [],
                    "design": {"theme": theme_name},
                })
                theme = build_theme(design)
                self.assertTrue(theme["dataColors"])
                self.assertTrue(theme["background"].startswith("#"))
                self.assertTrue(theme["visualStyles"]["*"]["*"]["valueAxis"])

        design = normalize_design_spec({
            "visuals": [],
            "design": {"colors": {"primary": "#123456"}},
        })
        self.assertEqual(build_theme(design)["tableAccent"], "#123456")

    def test_dar_light_preset_uses_the_attached_power_bi_theme(self) -> None:
        source_theme = json.loads(
            (Path(__file__).resolve().parents[2] / "docs" / "DAR_PowerBI_Light.json")
            .read_text(encoding="utf-8")
        )
        design = normalize_design_spec({"visuals": [], "design": {"theme": "dar_light"}})
        self.assertEqual(build_theme(design), source_theme)

    def test_design_validation_rejects_unknown_presets_styles_and_colors(self) -> None:
        from automation.report_automation import validate_report_spec

        base = {"page": "Overview", "visuals": [{
            "type": "card",
            "value": {"table": "Orders", "field": "Order Value", "fieldType": "measure"},
        }]}
        for invalid_design, message in (
            ({"theme": "unlisted"}, "Unsupported theme"),
            ({"layout": "unlisted"}, "Unsupported dashboard layout"),
            ({"visual_style": "unlisted"}, "Unsupported visual style"),
            ({"colors": {"primary": "red"}}, "six-digit hex"),
        ):
            with self.subTest(design=invalid_design), self.assertRaisesRegex(ValueError, message):
                validate_report_spec({**base, "design": invalid_design}, MODEL_METADATA)

    def test_design_layout_orders_filters_cards_then_charts_without_overlap(self) -> None:
        visuals = [
            {"type": "lineChart"},
            {"type": "card"},
            {"type": "slicer"},
            {"type": "clusteredColumnChart"},
            {"type": "card"},
            {"type": "slicer"},
        ]
        positions, width, height = layout_positions(visuals, "kpi_grid_4_plus_2x2")
        self.assertLess(positions[2]["y"], positions[1]["y"])
        self.assertLess(positions[1]["y"], positions[0]["y"])
        for index, first in enumerate(positions):
            self.assertLessEqual(first["x"] + first["width"], width)
            self.assertLessEqual(first["y"] + first["height"], height)
            for second in positions[index + 1:]:
                self.assertTrue(
                    first["x"] + first["width"] <= second["x"]
                    or second["x"] + second["width"] <= first["x"]
                    or first["y"] + first["height"] <= second["y"]
                    or second["y"] + second["height"] <= first["y"]
                )

    def test_executive_layout_fills_canvas_with_filters_kpis_and_four_charts(self) -> None:
        visuals = [
            {"type": "slicer"},
            {"type": "slicer"},
            *({"type": "card"} for _ in range(4)),
            *({"type": "lineChart"} for _ in range(4)),
        ]
        positions, width, height = layout_positions(visuals, "kpi_4_plus_2x2")
        self.assertEqual(height, 1080)
        filters = positions[:2]
        kpis = positions[2:6]
        charts = positions[6:]
        self.assertEqual(filters[0]["y"], filters[1]["y"])
        self.assertLess(filters[0]["height"], kpis[0]["height"])
        self.assertEqual(len({position["y"] for position in charts}), 2)
        self.assertEqual(len({position["x"] for position in charts[:2]}), 2)
        self.assertEqual(charts[0]["width"], charts[1]["width"])
        self.assertEqual(charts[0]["height"], charts[1]["height"])
        self.assertGreaterEqual(min(position["y"] for position in charts), max(
            position["y"] + position["height"] for position in kpis
        ))
        self.assertTrue(all(position["x"] + position["width"] <= width for position in positions))

    def test_kpi_comparison_and_icon_are_bound_and_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            (report / "definition").mkdir(parents=True)
            pages.mkdir()
            model = workspace / "Sales.SemanticModel"
            model_tables = model / "definition" / "tables"
            model_tables.mkdir(parents=True)
            (model / "definition.pbism").write_text(json.dumps({"version": "4.0"}), encoding="utf-8")
            (model / "definition" / "database.tmdl").write_text(
                "database Sales\n\tcompatibilityLevel: 1600\n",
                encoding="utf-8",
            )
            (model / "definition" / "model.tmdl").write_text("model Model\n", encoding="utf-8")
            (model_tables / "Orders.tmdl").write_text(
                "table Orders\n\tcolumn Territory\n\tcolumn Year\n\tcolumn Scenario\n\tcolumn ScenarioYear\n"
                "\tmeasure 'Order Value' = 1\n\tmeasure 'Total Units' = 1\n"
                "\tmeasure 'Average Basket' = 1\n\tmeasure 'Order Count' = 1\n",
                encoding="utf-8",
            )
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (pages / "pages.json").write_text(json.dumps({"pageOrder": []}), encoding="utf-8")
            report_definition = {
                "themeCollection": {"baseTheme": {"name": "AURA"}},
                "resourcePackages": [{"items": [{"name": "AURA", "path": "BaseThemes/AURA.json", "type": "BaseTheme"}]}],
            }
            (report / "definition" / "report.json").write_text(json.dumps(report_definition), encoding="utf-8")
            (report / "definition" / "version.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
            theme_dir = report / "StaticResources" / "SharedResources" / "BaseThemes"
            theme_dir.mkdir(parents=True)
            (theme_dir / "AURA.json").write_text(json.dumps(build_theme(normalize_design_spec({
                "visuals": [],
                "design": {},
            }))), encoding="utf-8")
            card = {
                "type": "card",
                "value": {"table": "Orders", "field": "Order Value", "fieldType": "measure"},
                "comparison": {
                    "value": {"table": "Orders", "field": "Total Units", "fieldType": "measure"},
                    "label": "target",
                    "direction": "positive",
                },
                "icon": "chart",
            }
            chart_measures = ["Order Value", "Total Units", "Average Basket", "Order Count"]
            chart_types = ["lineChart", "clusteredColumnChart", "areaChart", "lineChart"]
            charts = [{
                "type": chart_types[index],
                "category": {
                    "table": "Orders",
                    "field": "ScenarioYear" if index == 0 else "Year",
                    "fieldType": "column",
                },
                "value": {"table": "Orders", "field": measure, "fieldType": "measure"},
                **({"comparison": True} if index == 0 else {}),
            } for index, measure in enumerate(chart_measures)]
            metadata = {"tables": {"Orders": {
                "columns": ["Territory", "Year", "Scenario", "ScenarioYear"],
                "measures": chart_measures,
            }}}

            from unittest.mock import patch

            with patch("automation.report_automation.WORKSPACE_ROOT", workspace):
                result = generate_pbir("Sales.Report", {
                    "page": "Overview",
                    "visuals": [card, *charts],
                    "design": {"layout": "analytics_dashboard", "auto_kpi_cards": True},
                }, metadata)
            validation = validate_pbir("Sales.Report", metadata, workspace)
            self.assertTrue(validation["passed"], validation["errors"])
            visual_file = (
                pages / result["pageId"] / "visuals" / result["visualIds"][0] / "visual.json"
            )
            visual = json.loads(visual_file.read_text(encoding="utf-8"))
            projections = visual["visual"]["query"]["queryState"]["Values"]["projections"]
            self.assertEqual(len(projections), 2)
            self.assertIn("▥", visual["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"])
            generated_files = [
                json.loads((
                    pages / result["pageId"] / "visuals" / visual_id / "visual.json"
                ).read_text(encoding="utf-8"))
                for visual_id in result["visualIds"]
            ]
            generated_visuals = [item["visual"] for item in generated_files]
            self.assertEqual(sum(item["visualType"] == "card" for item in generated_visuals), 4)
            self.assertEqual(
                [item["visualType"] for item in generated_visuals if item["visualType"] in chart_types],
                chart_types,
            )
            slicers = [item for item in generated_visuals if item["visualType"] == "slicer"]
            self.assertEqual(len(slicers), 2)
            self.assertEqual(
                {
                    item["query"]["queryState"]["Values"]["projections"][0]["nativeQueryRef"]
                    for item in slicers
                },
                {"Year", "Scenario"},
            )
            slicer_positions = [item["position"] for item in generated_files if item["visual"]["visualType"] == "slicer"]
            self.assertTrue(all(position["width"] >= 900 for position in slicer_positions))
            self.assertTrue(all(position["height"] >= 72 for position in slicer_positions))
            self.assertTrue(all(position["y"] >= 136 for position in slicer_positions))
            report_definition = json.loads((report / "definition" / "report.json").read_text(encoding="utf-8"))
            theme_name = report_definition["themeCollection"]["customTheme"]["name"]
            dar_theme = json.loads((report / "StaticResources" / "RegisteredResources" / theme_name).read_text(encoding="utf-8"))
            self.assertEqual(dar_theme["visualStyles"]["slicer"]["*"]["items"][0]["outline"], "None")
            for item in slicers:
                self.assertEqual(
                    item["objects"]["selection"][0]["properties"]["singleSelect"]["expr"]["Literal"]["Value"],
                    "false",
                )
                style = item["visualContainerObjects"]
                self.assertEqual(
                    style["title"][0]["properties"]["show"]["expr"]["Literal"]["Value"],
                    "false",
                )
                self.assertEqual(
                    style["border"][0]["properties"]["show"]["expr"]["Literal"]["Value"],
                    "false",
                )
                dropdown = item["objects"]["dropdown"][0]["properties"]
                self.assertTrue(dropdown["accentBarShow"]["expr"]["Literal"]["Value"] == "false")
                self.assertEqual(dropdown["borderColor"]["solid"]["color"], THEME_PRESETS["dar_light"]["cardBackground"])
            chart_positions = [
                item["position"] for item in generated_files
                if item["visual"]["visualType"] in chart_types
            ]
            card_positions = [
                item["position"] for item in generated_files
                if item["visual"]["visualType"] == "card"
            ]
            self.assertEqual(len({position["x"] for position in card_positions}), 4)
            self.assertEqual(len({position["y"] for position in card_positions}), 1)
            self.assertEqual(len({position["x"] for position in chart_positions}), 2)
            self.assertEqual(len({position["y"] for position in chart_positions}), 2)
            self.assertEqual(len({(position["width"], position["height"]) for position in chart_positions}), 1)
            for item in generated_visuals:
                if item["visualType"] in chart_types:
                    state = item["query"]["queryState"]
                    self.assertEqual(state["Category"]["projections"][0]["nativeQueryRef"], "Year")
                    self.assertEqual(state["Series"]["projections"][0]["nativeQueryRef"], "Scenario")
                    self.assertEqual(
                        item["visualContainerObjects"]["border"][0]["properties"]["show"]["expr"]["Literal"]["Value"],
                        "false",
                    )
            decoration_visuals = [
                json.loads(path.read_text(encoding="utf-8"))["visual"]["visualType"]
                for path in (pages / result["pageId"] / "visuals").glob(
                    "autobi-decoration-*/visual.json"
                )
            ]
            self.assertIn("textbox", decoration_visuals)
            self.assertGreaterEqual(decoration_visuals.count("shape"), 5)
            project_file = create_pbip_project_file("Sales.Report", workspace)
            package_file = package_pbip("Sales.Report", workspace)
            self.assertTrue(project_file.is_file())
            self.assertTrue(package_file.is_file())
            with zipfile.ZipFile(package_file) as package:
                self.assertIn("Sales.pbip", package.namelist())
                self.assertIn("Sales.Report/definition/pages/pages.json", package.namelist())
            self.assertEqual(
                visual["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"],
                "'▥  Order Value · Target ▲'",
            )
            self.assertEqual(
                visual["visual"]["visualContainerObjects"]["title"][0]["properties"]["fontColor"]["solid"]["color"],
                "#6F8558",
            )
            visual_types = [
                json.loads(path.read_text(encoding="utf-8"))["visual"]["visualType"]
                for path in (pages / result["pageId"] / "visuals").glob("autobi-decoration-*/visual.json")
            ]
            self.assertIn("textbox", visual_types)
            self.assertIn("shape", visual_types)

    def test_requested_chart_types_are_detected(self) -> None:
        for phrase, expected in (
            ("line chart", "lineChart"),
            ("horizontal bar chart", "clusteredBarChart"),
            ("pie chart", "pieChart"),
            ("donut chart", "donutChart"),
            ("area chart", "areaChart"),
            ("column chart", "clusteredColumnChart"),
        ):
            with self.subTest(phrase=phrase):
                spec = create_report_spec(
                    f"Create orders dashboard showing order value by territory as a {phrase}.",
                    MODEL_METADATA,
                )
                self.assertEqual(spec["visuals"][0]["type"], expected)

    def test_request_with_multiple_categories_creates_multiple_visuals(self) -> None:
        metadata = {
            "tables": {
                "Orders": {
                    "columns": ["Territory", "Item"],
                    "measures": ["Order Value"],
                }
            }
        }
        spec = create_report_spec(
            "Create orders dashboard showing order value by territory and item as a bar chart.",
            metadata,
        )
        self.assertEqual([visual["category"]["field"] for visual in spec["visuals"]], ["Territory", "Item"])
        self.assertEqual(len(spec["visuals"]), 2)
        self.assertTrue(all(visual["type"] == "clusteredBarChart" for visual in spec["visuals"]))

    def test_multiple_dashboard_instructions_create_independently_typed_visuals(self) -> None:
        metadata = {
            "tables": {
                "Sales": {
                    "columns": ["Region", "Month", "Units"],
                    "measures": ["Total Revenue"],
                }
            }
        }
        spec = create_report_spec(
            "Create a sales dashboard showing total revenue by region in line chart, "
            "Create a sales dashboard showing total revenue by month in pie chart, "
            "Create a sales dashboard showing total revenue by units.",
            metadata,
        )
        self.assertEqual([visual["type"] for visual in spec["visuals"]], ["lineChart", "pieChart", "clusteredColumnChart"])
        self.assertEqual([visual["category"]["field"] for visual in spec["visuals"]], ["Region", "Month", "Units"])

    def test_numbered_visual_request_creates_the_requested_four_visuals(self) -> None:
        metadata = {
            "tables": {
                "Sales": {
                    "columns": ["Region", "Month", "Product"],
                    "measures": ["Total Revenue", "Total Units"],
                }
            }
        }
        request = (
            "Create a sales executive dashboard with 4 visuals:\n"
            "1. KPI card for total revenue\n"
            "2. Total revenue by region as a column chart\n"
            "3. Total units by month as a line chart\n"
            "4. Total revenue by product as a horizontal bar chart"
        )

        spec = create_report_spec(request, metadata)

        self.assertEqual(spec["page"], "Sales Executive Dashboard")
        self.assertEqual(
            [visual["type"] for visual in spec["visuals"]],
            ["card", "clusteredColumnChart", "lineChart", "clusteredBarChart"],
        )
        self.assertEqual(len(spec["visuals"]), 4)

    def test_numbered_visual_request_rejects_a_count_mismatch(self) -> None:
        request = (
            "Create a sales dashboard with 4 visuals:\n"
            "1. Order value by territory\n"
            "2. Order value by item"
        )
        with self.assertRaisesRegex(ValueError, "asks for 4 visuals but specifies 2"):
            create_report_spec(request, MODEL_METADATA)

    def test_requested_count_of_six_is_supported(self) -> None:
        request = (
            "Create an orders dashboard with six visuals:\n"
            "1. Order value by territory\n"
            "2. Order value by item\n"
            "3. Order value by territory as a line chart\n"
            "4. Order value by item as a bar chart\n"
            "5. Order value by territory as an area chart\n"
            "6. Order value by item as a donut chart"
        )

        spec = create_report_spec(request, MODEL_METADATA)

        self.assertEqual(len(spec["visuals"]), 6)

    def test_unmatched_or_ambiguous_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Could not resolve category"):
            create_report_spec("Create orders dashboard showing order value by country.", MODEL_METADATA)

    def test_report_validation_rejects_incomplete_or_unsupported_visuals(self) -> None:
        from automation.report_automation import validate_report_spec

        with self.assertRaisesRegex(ValueError, "requires a category"):
            validate_report_spec(
                {"page": "Sales", "visuals": [{"type": "lineChart", "value": {"table": "Orders", "field": "Order Value"}}]},
                MODEL_METADATA,
            )
        with self.assertRaisesRegex(ValueError, "Unsupported visual type"):
            validate_report_spec(
                {"page": "Sales", "visuals": [{"type": "unsupported"}]},
                MODEL_METADATA,
            )

    def test_slicer_request_resolves_a_column(self) -> None:
        spec = create_report_spec("Create orders dashboard with a slicer for territory.", MODEL_METADATA)
        self.assertEqual(spec["visuals"][0]["type"], "slicer")
        self.assertEqual(spec["visuals"][0]["field"]["field"], "Territory")

    def test_hierarchy_request_binds_multiple_drilldown_levels(self) -> None:
        metadata = {
            "tables": {
                "Orders": {
                    "columns": ["Territory", "Item"],
                    "measures": ["Order Value"],
                }
            }
        }
        spec = create_report_spec(
            "Create orders dashboard showing order value by territory then item drilldown as a column chart.",
            metadata,
        )
        self.assertEqual(
            [level["field"] for level in spec["visuals"][0]["categoryHierarchy"]],
            ["Territory", "Item"],
        )

    def test_visual_type_is_selected_from_time_role_and_category_cardinality(self) -> None:
        metadata = {
            "tables": {
                "Orders": {
                    "columns": ["Year", "Customer"],
                    "columnRoles": {"Year": "time", "Customer": "category"},
                    "columnProfiles": {"Customer": {"distinct_count": 48}},
                    "measures": ["Order Value"],
                }
            }
        }
        year = create_report_spec("Show order value by year", metadata)
        customers = create_report_spec("Show order value by customer", metadata)
        self.assertEqual(year["visuals"][0]["type"], "lineChart")
        self.assertEqual(customers["visuals"][0]["type"], "clusteredBarChart")

    def test_drilldown_levels_are_projected_as_a_category_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            pages.mkdir(parents=True)
            model = workspace / "Sales.SemanticModel"
            model.mkdir()
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (pages / "pages.json").write_text(json.dumps({"pageOrder": []}), encoding="utf-8")
            metadata = {"tables": {"Orders": {"columns": ["Territory", "Item"], "measures": ["Order Value"]}}}
            spec = {
                "page": "Orders",
                "visuals": [{
                    "type": "clusteredColumnChart",
                    "category": {"table": "Orders", "field": "Territory", "fieldType": "column"},
                    "categoryHierarchy": [
                        {"table": "Orders", "field": "Territory", "fieldType": "column"},
                        {"table": "Orders", "field": "Item", "fieldType": "column"},
                    ],
                    "value": {"table": "Orders", "field": "Order Value", "fieldType": "measure"},
                }],
            }

            from unittest.mock import patch

            with patch("automation.report_automation.WORKSPACE_ROOT", workspace):
                result = generate_pbir("Sales.Report", spec, metadata)

            visual = json.loads(
                (pages / result["pageId"] / "visuals" / result["visualIds"][0] / "visual.json").read_text(encoding="utf-8")
            )
        projections = visual["visual"]["query"]["queryState"]["Category"]["projections"]
        self.assertEqual([projection["field"]["Column"]["Property"] for projection in projections], ["Territory", "Item"])

    def test_mixed_dashboard_layout_places_slicers_before_charts(self) -> None:
        visuals = [
            {"type": "slicer"},
            {"type": "lineChart"},
            {"type": "clusteredBarChart"},
        ]
        positions = _visual_positions(visuals)
        self.assertLess(positions[0]["y"] + positions[0]["height"], positions[1]["y"])
        self.assertEqual(positions[1]["y"], positions[2]["y"])

    def test_dashboard_layout_groups_slicers_kpis_and_charts_without_overlap(self) -> None:
        visuals = [
            {"type": "slicer"},
            {"type": "slicer"},
            {"type": "card"},
            {"type": "card"},
            {"type": "card"},
            {"type": "card"},
            {"type": "lineChart"},
            {"type": "lineChart"},
            {"type": "lineChart"},
            {"type": "lineChart"},
        ]
        positions = _visual_positions(visuals)
        page_width, page_height, _, _ = _page_canvas_size(len(visuals), visuals)

        self.assertEqual(positions[0]["y"], positions[1]["y"])
        self.assertLess(positions[0]["y"], positions[2]["y"])
        self.assertLess(positions[2]["y"], positions[6]["y"])
        for index, first in enumerate(positions):
            self.assertLessEqual(first["x"] + first["width"], page_width)
            self.assertLessEqual(first["y"] + first["height"], page_height)
            for second in positions[index + 1:]:
                self.assertTrue(
                    first["x"] + first["width"] <= second["x"]
                    or second["x"] + second["width"] <= first["x"]
                    or first["y"] + first["height"] <= second["y"]
                    or second["y"] + second["height"] <= first["y"]
                )

    def test_generated_page_preserves_existing_page_and_binds_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            (report / "definition").mkdir(parents=True)
            pages.mkdir()
            model = workspace / "Sales.SemanticModel"
            (model / "definition" / "tables").mkdir(parents=True)
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (pages / "pages.json").write_text(
                json.dumps({"pageOrder": ["existing"], "activePageName": "existing"}), encoding="utf-8"
            )
            existing_page = pages / "existing"
            existing_page.mkdir()
            (existing_page / "page.json").write_text(json.dumps({"name": "existing"}), encoding="utf-8")
            spec = create_report_spec("Create orders dashboard showing order value by territory.", MODEL_METADATA)

            from unittest.mock import patch

            with patch("automation.report_automation.WORKSPACE_ROOT", workspace):
                result = generate_pbir("Sales.Report", spec, MODEL_METADATA)

            page_metadata = json.loads((pages / "pages.json").read_text(encoding="utf-8"))
            generated_page = pages / result["pageId"]
            page = json.loads((generated_page / "page.json").read_text(encoding="utf-8"))
            visual_path = generated_page / "visuals" / result["visualIds"][0] / "visual.json"
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            self.assertEqual(page_metadata["pageOrder"], ["existing", result["pageId"]])
            self.assertNotIn("background", page["objects"])
            dar_theme = build_theme(normalize_design_spec({"visuals": [], "design": {}}))
            self.assertEqual(
                dar_theme["visualStyles"]["page"]["*"]["background"][0]["image"]["name"],
                "DAR Header",
            )
            self.assertEqual(visual["visual"]["query"]["queryState"]["Category"]["projections"][0]["field"]["Column"]["Property"], "Territory")
            self.assertEqual(visual["visual"]["query"]["queryState"]["Y"]["projections"][0]["field"]["Measure"]["Property"], "Order Value")
            self.assertEqual(
                visual["visual"]["visualContainerObjects"]["background"][0]["properties"]["color"]["solid"]["color"],
                "#FAF7F0",
            )
            self.assertEqual(
                visual["visual"]["visualContainerObjects"]["border"][0]["properties"]["show"]["expr"]["Literal"]["Value"],
                "false",
            )
            self.assertTrue(visual["visual"]["drillFilterOtherVisuals"])

    def test_four_visuals_use_a_non_overlapping_two_by_two_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            pages.mkdir(parents=True)
            model = workspace / "Sales.SemanticModel"
            model.mkdir()
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (pages / "pages.json").write_text(json.dumps({"pageOrder": []}), encoding="utf-8")
            metadata = {
                "tables": {
                    "Sales": {
                        "columns": ["Region", "Month", "Product"],
                        "measures": ["Total Revenue", "Total Units"],
                    }
                }
            }
            spec = create_report_spec(
                "Create a sales dashboard with 4 visuals:\n"
                "1. KPI card for total revenue\n"
                "2. Total revenue by region as a column chart\n"
                "3. Total units by month as a line chart\n"
                "4. Total revenue by product as a bar chart",
                metadata,
            )

            from unittest.mock import patch

            with patch("automation.report_automation.WORKSPACE_ROOT", workspace):
                result = generate_pbir("Sales.Report", spec, metadata)

            page_dir = pages / result["pageId"]
            visuals = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (page_dir / "visuals").glob("*/visual.json")
                if not path.parent.name.startswith("autobi-decoration-")
            ]
            positions = [
                visual["position"]
                for visual in sorted(visuals, key=lambda item: item["position"]["tabOrder"])
            ]
            self.assertEqual(positions[0]["y"], 100)
            self.assertEqual(positions[1]["y"], positions[2]["y"])
            self.assertLess(positions[1]["x"], positions[2]["x"])
            self.assertLess(positions[2]["y"], positions[3]["y"])
            self.assertLess(positions[0]["y"] + positions[0]["height"], positions[1]["y"])
            page = json.loads(
                (page_dir / "page.json").read_text(encoding="utf-8")
            )
            for position in positions:
                self.assertGreaterEqual(position["x"], 0)
                self.assertGreaterEqual(position["y"], 0)
                self.assertLessEqual(position["x"] + position["width"], page["width"])
                self.assertLessEqual(position["y"] + position["height"], page["height"])

    def test_six_visuals_expand_the_page_without_overlapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            pages.mkdir(parents=True)
            model = workspace / "Sales.SemanticModel"
            model.mkdir()
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (pages / "pages.json").write_text(json.dumps({"pageOrder": []}), encoding="utf-8")
            base_visual = {
                "type": "clusteredColumnChart",
                "category": {"table": "Orders", "field": "Territory", "fieldType": "column"},
                "value": {"table": "Orders", "field": "Order Value", "fieldType": "measure"},
            }
            spec = {"page": "Orders Dashboard", "visuals": [base_visual.copy() for _ in range(6)]}

            from unittest.mock import patch

            with patch("automation.report_automation.WORKSPACE_ROOT", workspace):
                result = generate_pbir("Sales.Report", spec, MODEL_METADATA)

            page_dir = pages / result["pageId"]
            page = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
            visuals = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (page_dir / "visuals").glob("*/visual.json")
                if not path.parent.name.startswith("autobi-decoration-")
            ]
            positions = [
                visual["position"]
                for visual in sorted(visuals, key=lambda item: item["position"]["tabOrder"])
            ]
            self.assertEqual((page["width"], page["height"]), (1920, 1124))
            self.assertEqual(
                [(position["x"], position["y"]) for position in positions],
                [(48, 100), (982, 100), (48, 440), (982, 440), (48, 780), (982, 780)],
            )
            for position in positions:
                self.assertLessEqual(position["x"] + position["width"], page["width"])
                self.assertLessEqual(position["y"] + position["height"], page["height"])

    def test_different_visual_definitions_are_retained_as_separate_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            (report / "definition").mkdir(parents=True)
            pages.mkdir()
            model = workspace / "Sales.SemanticModel"
            (model / "definition" / "tables").mkdir(parents=True)
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (pages / "pages.json").write_text(
                json.dumps({"pageOrder": [], "activePageName": ""}), encoding="utf-8"
            )
            column_spec = create_report_spec(
                "Create orders dashboard showing order value by territory.", MODEL_METADATA
            )
            bar_spec = {**column_spec, "visuals": [{**column_spec["visuals"][0], "type": "clusteredBarChart"}]}

            from unittest.mock import patch

            with patch("automation.report_automation.WORKSPACE_ROOT", workspace):
                column_result = generate_pbir("Sales.Report", column_spec, MODEL_METADATA)
                bar_result = generate_pbir("Sales.Report", bar_spec, MODEL_METADATA)
                rerun_result = generate_pbir("Sales.Report", column_spec, MODEL_METADATA)

            page_metadata = json.loads((pages / "pages.json").read_text(encoding="utf-8"))
            self.assertNotEqual(column_result["pageId"], bar_result["pageId"])
            self.assertEqual(column_result["pageId"], rerun_result["pageId"])
            self.assertEqual(page_metadata["pageOrder"], [column_result["pageId"], bar_result["pageId"]])

    def test_validate_pbir_accepts_valid_generated_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            pages.mkdir(parents=True)
            (report / "definition").mkdir(exist_ok=True)
            _write_test_semantic_model(workspace / "Sales.SemanticModel")
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (report / "definition" / "report.json").write_text(json.dumps({"settings": {}}), encoding="utf-8")
            (report / "definition" / "version.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
            (pages / "pages.json").write_text(json.dumps({"pageOrder": ["page-1"], "activePageName": "page-1"}), encoding="utf-8")
            page_dir = pages / "page-1"
            page_dir.mkdir()
            (page_dir / "page.json").write_text(json.dumps({"name": "page-1", "displayName": "Sales Dashboard"}), encoding="utf-8")
            visuals_dir = page_dir / "visuals"
            visuals_dir.mkdir()
            (visuals_dir / "visual-1").mkdir()
            (visuals_dir / "visual-1" / "visual.json").write_text(
                json.dumps(
                    {
                        "visual": {
                            "query": {
                                "queryState": {
                                    "Category": {
                                        "projections": [
                                            {
                                                "field": {
                                                    "Column": {
                                                        "Expression": {"SourceRef": {"Entity": "Sales"}},
                                                        "Property": "Region",
                                                    }
                                                }
                                            }
                                        ]
                                    },
                                    "Y": {
                                        "projections": [
                                            {
                                                "field": {
                                                    "Measure": {
                                                        "Expression": {"SourceRef": {"Entity": "Sales"}},
                                                        "Property": "Total Revenue",
                                                    }
                                                }
                                            }
                                        ]
                                    },
                                }
                            }
                        }
                    },
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            metadata = {"tables": {"Sales": {"columns": ["Region"], "measures": ["Total Revenue"]}}}

            result = validate_pbir("Sales.Report", metadata, workspace)

            self.assertTrue(result["passed"])
            self.assertEqual(result["errors"], [])

    def test_validate_pbir_reports_unknown_field_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            pages = report / "definition" / "pages"
            pages.mkdir(parents=True)
            (report / "definition").mkdir(exist_ok=True)
            _write_test_semantic_model(workspace / "Sales.SemanticModel")
            (report / "definition.pbir").write_text(
                json.dumps({"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}),
                encoding="utf-8",
            )
            (report / "definition" / "report.json").write_text(json.dumps({"settings": {}}), encoding="utf-8")
            (report / "definition" / "version.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
            (pages / "pages.json").write_text(json.dumps({"pageOrder": ["page-1"], "activePageName": "page-1"}), encoding="utf-8")
            page_dir = pages / "page-1"
            page_dir.mkdir()
            (page_dir / "page.json").write_text(json.dumps({"name": "page-1", "displayName": "Sales Dashboard"}), encoding="utf-8")
            visuals_dir = page_dir / "visuals"
            visuals_dir.mkdir()
            (visuals_dir / "visual-1").mkdir()
            (visuals_dir / "visual-1" / "visual.json").write_text(
                json.dumps(
                    {
                        "visual": {
                            "query": {
                                "queryState": {
                                    "Category": {
                                        "projections": [
                                            {
                                                "field": {
                                                    "Column": {
                                                        "Expression": {"SourceRef": {"Entity": "Sales"}},
                                                        "Property": "Country",
                                                    }
                                                }
                                            }
                                        ]
                                    },
                                    "Y": {
                                        "projections": [
                                            {
                                                "field": {
                                                    "Measure": {
                                                        "Expression": {"SourceRef": {"Entity": "Sales"}},
                                                        "Property": "Total Revenue",
                                                    }
                                                }
                                            }
                                        ]
                                    },
                                }
                            }
                        }
                    },
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            metadata = {"tables": {"Sales": {"columns": ["Region"], "measures": ["Total Revenue"]}}}

            result = validate_pbir("Sales.Report", metadata, workspace)

            self.assertFalse(result["passed"])
            self.assertTrue(any("Country" in error for error in result["errors"]))

    def test_cross_fact_comparison_creates_valid_per_table_visuals(self) -> None:
        metadata = {
            "tables": {
                "Revenue": {"columns": ["Business Unit"], "measures": ["Net Revenue"]},
                "Costs": {"columns": ["Business Unit"], "measures": ["COGS"]},
                "Margin": {"columns": ["Business Unit"], "measures": ["Gross Profit"]},
            },
            "relationships": [],
        }

        plan = create_report_spec(
            "Create a gross margin comparison dashboard showing Net Revenue, COGS, and Gross Profit by Business Unit.",
            metadata,
        )

        self.assertEqual(len(plan["visuals"]), 3)
        self.assertTrue(all(
            visual["category"]["table"] == visual["value"]["table"]
            for visual in plan["visuals"]
        ))

    def test_package_pbip_creates_zip_for_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            report = workspace / "Sales.Report"
            (report / "definition").mkdir(parents=True)
            (report / "definition" / "report.json").write_text(json.dumps({"settings": {}}), encoding="utf-8")
            zip_path = package_pbip("Sales.Report", workspace)

            self.assertTrue(zip_path.exists())
            self.assertEqual(zip_path.parent, workspace / "generated-pbip")
            self.assertTrue(zip_path.name.endswith(".pbip.zip"))
            with zipfile.ZipFile(zip_path) as archive:
                project = json.loads(archive.read("Sales.pbip"))
            self.assertEqual(project["artifacts"][0]["report"]["path"], "Sales.Report")


if __name__ == "__main__":
    unittest.main()
