import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)
sample = Path(__file__).resolve().parents[2] / "docs" / "DemoSales.xlsx"


@pytest.fixture(autouse=True)
def mock_response_agent(monkeypatch):
    # This suite uses AI_PROVIDER=mock explicitly; no live inference is expected.
    from backend.agents import graph_workflow
    async def reply(operation, prompt, payload):
        if operation == "clarification_agent":
            return {"question": payload["uncertainty"], "options":payload.get("options",[])}
        result = payload["verifiedResult"]
        if "answer" in result:
            return {"message":result["answer"], "evidenceKeys":["answer"]}
        if "message" in result:
            return {"message":result["message"], "evidenceKeys":["message"]}
        return {"message":"The report draft is ready for review.", "evidenceKeys":list(result)[:1]}
    monkeypatch.setattr(graph_workflow,"_json",reply)


def upload():
    response = client.post("/api/datasource/excel", files={"file": ("sales.xlsx", sample.read_bytes())})
    assert response.status_code == 200, response.text
    return response.json()["sourceId"]


def run(source, request, **options):
    response = client.post("/api/agent/runs", json={"sourceId": source, "request": request, **options})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("request_text, expected", [
    ("Create 4 visuals: 1. KPI card for total revenue 2. Total revenue by Region as a bar chart "
     "3. Total revenue by Product as a column chart 4. Add a slicer for Channel",
     ["card", "clusteredBarChart", "clusteredColumnChart", "slicer"]),
])
def test_visual_count_through_agent_and_download(monkeypatch, request_text, expected):
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    source = upload()
    result = run(source, request_text)
    assert result["status"] == "completed", result.get("help")
    spec = result["steps"][-1]["result"]
    assert [v["type"] for v in spec["visuals"]] == expected
    payload = {"sourceId": source, "reportSpec": spec}
    assert client.post("/api/report/approve", json=payload).status_code == 200
    generated = client.post("/api/report/generate", json=payload)
    assert generated.status_code == 200, generated.text
    assert generated.json()["validation"]["passed"], generated.json()
    data = client.get(f"/api/report/{generated.json()['reportId']}/download")
    with zipfile.ZipFile(io.BytesIO(data.content)) as archive:
        written = [json.loads(archive.read(n)) for n in archive.namelist()
                   if n.endswith("/visual.json") and "/autobi-decoration-" not in n]
    assert len(written) == 4
    assert len({item["name"] for item in written}) == 4


def test_question_after_visual_request_and_unavailable_measure(monkeypatch):
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    source = upload()
    result = run(source, "What is total revenue?", conversation=[{"role":"user","text":"Draw four visuals"}])
    assert result["steps"][0]["action"] == "answer_data_question"
    answer = result["steps"][0]["result"]
    assert "5000" in str(answer).replace(",", ""), answer
    result = run(source, "Draw four visuals for nonexistent profit")
    assert result["status"] == "needs_user"
    assert not result["results"]


def test_workbook_table_and_measure_inventory_through_agent(monkeypatch):
    from openpyxl import Workbook
    from backend.services.model_service import load_source_metadata

    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    workbook = Workbook()
    workbook.active.title = "Sales"
    workbook.active.append(["Region", "Revenue"])
    workbook.active.append(["North", 100])
    sheet = workbook.create_sheet("Costs")
    sheet.append(["Category", "Cost"])
    sheet.append(["Transport", 25])
    content = io.BytesIO()
    workbook.save(content)
    response = client.post("/api/datasource/excel", files={"file": ("data.xlsx", content.getvalue())})
    assert response.status_code == 200, response.text
    source = response.json()["sourceId"]
    metadata = load_source_metadata(source)
    result = run(source, "What tables and measures are available in this workbook?")
    assert result["status"] == "completed", result.get("help")
    assert [step["action"] for step in result["steps"]] == ["understand_workbook"]
    inventory = result["steps"][0]["result"]
    assert {table["table"]: table["measures"] for table in inventory["tables"]} == {
        name: table.get("measures", []) for name, table in metadata["tables"].items()
    }
    for table in inventory["tables"]:
        assert table["table"] in inventory["assistantMessage"]
        for measure in table["measures"]:
            assert measure in inventory["assistantMessage"]


def test_partial_model_response_is_rejected_without_fallback(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from backend.agents import tools
    from backend.services.model_service import load_source_payload
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")

    source = upload()
    payload = load_source_payload(source)
    payload.update(workbookProfile={"sheets": []}, aiUnderstanding={"tables": []})
    monkeypatch.setattr(tools, "load_source_payload", lambda _: payload)
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(use_ai=True))

    class PartialProvider:
        async def plan_dashboard(self, *args, **kwargs):
            return {"page": "Partial", "visuals": [{"type": "card", "value": {
                "table": "Sales", "field": "Total Revenue", "fieldType": "measure"
            }}]}

    monkeypatch.setattr(tools, "get_ai_provider", lambda _: PartialProvider())
    with pytest.raises(ValueError):
        asyncio.run(tools.execute_tool("plan_dashboard", source, "Draw four visuals for revenue", {}))


def test_add_four_keeps_existing_visual(monkeypatch):
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    from backend.services.model_service import load_source_metadata
    from backend.services.report_edit_service import edit_report_spec
    from backend.services.report_planner import build_report_plan

    metadata = load_source_metadata(upload())
    original = build_report_plan("Create a slicer for Region", metadata)
    edited, _ = edit_report_spec(original, "Add 4 visuals for revenue", metadata)
    assert len(original["visuals"]) == 1
    assert edited["visuals"][:1] == original["visuals"]
    assert len(edited["visuals"]) == 5


def test_multi_page_report_through_download(monkeypatch):
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    source = upload()
    prompt = ("Create a two-page report. Page 1 Sales Overview: Create 4 visuals: "
              "1. KPI card for total revenue 2. Total revenue by Region as a bar chart "
              "3. Total revenue by Product as a column chart 4. Add a slicer for Channel; "
              "Page 2 called Product Detail with Total revenue by Product as a bar chart")
    result = run(source, prompt)
    assert result["status"] == "completed", result.get("help")
    spec = result["steps"][-1]["result"]
    payload = {"sourceId": source, "reportSpec": spec}
    preview = client.post("/api/report/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["visuals"]) == 4
    assert len(preview.json()["additionalPages"][0]["visuals"]) == 1
    assert client.post("/api/report/approve", json=payload).status_code == 200
    from copy import deepcopy
    changed = deepcopy(payload)
    changed["reportSpec"]["additionalPages"][0]["page"] = "Changed"
    assert client.post("/api/report/generate", json=changed).status_code == 409
    gen = client.post("/api/report/generate", json=payload)
    assert gen.status_code == 200 and gen.json()["validation"]["passed"], gen.text
    data = client.get(f"/api/report/{gen.json()['reportId']}/download")
    with zipfile.ZipFile(io.BytesIO(data.content)) as archive:
        index = next(n for n in archive.namelist() if n.endswith("/pages/pages.json"))
        manifest = json.loads(archive.read(index))
        ids = manifest["pageOrder"]
        assert len(ids) == 2 and manifest["activePageName"] == ids[0]
        for page_id, name, count in zip(ids, ("Sales Overview", "Product Detail"), (4, 1)):
            root = index.removesuffix("pages.json") + page_id + "/"
            assert json.loads(archive.read(root+"page.json"))["displayName"] == name
            visuals = [n for n in archive.namelist() if n.startswith(root+"visuals/") and
                       n.endswith("/visual.json") and "/autobi-decoration-" not in n]
            assert len(visuals) == count
        assert sum(n.endswith(".pbip") for n in archive.namelist()) == 1


def test_broad_multi_page_request_through_generation(monkeypatch):
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    source = upload()
    result = run(source, "Can you draw multi page dashboard using this workbook, which cover all the scenario?")
    assert result["status"] == "completed", result.get("help")
    spec = result["steps"][-1]["result"]
    assert len(spec["additionalPages"]) >= 1
    direct = client.post("/api/report/plan", json={"sourceId": source, "request": "Create a three-page dashboard"})
    assert direct.status_code == 200, direct.text
    assert len(direct.json()["additionalPages"]) == 2
    payload = {"sourceId": source, "reportSpec": spec}
    preview = client.post("/api/report/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert client.post("/api/report/approve", json=payload).status_code == 200
    generated = client.post("/api/report/generate", json=payload)
    assert generated.status_code == 200, generated.text
    assert generated.json()["validation"]["passed"], generated.json()


def test_requested_two_page_sales_report_preserves_every_visual(monkeypatch):
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SalesTransactions"
    sheet.append(["Order ID", "Order Date", "Region", "Customer Segment", "Net Revenue"])
    sheet.append([1, "2025-01-01", "North", "Consumer", 125])
    sheet.append([2, "2025-02-01", "South", "Corporate", 240])
    content = io.BytesIO()
    workbook.save(content)
    workbook.close()

    upload_response = client.post(
        "/api/datasource/excel",
        files={"file": ("sales-transactions.xlsx", content.getvalue())},
    )
    assert upload_response.status_code == 200, upload_response.text
    source = upload_response.json()["sourceId"]
    prompt = (
        "Create a two-page report. Page 1 - Regional Performance: KPI card for Total Net Revenue; "
        "bar chart of Total Net Revenue by Region; line chart of Total Net Revenue by Order Date; "
        "slicer for Customer Segment. Page 2 called Customer Mix with a bar chart of Total Net "
        "Revenue by Customer Segment and a slicer for Region."
    )

    result = run(source, prompt)
    assert result["status"] == "completed", result.get("help")
    plan = result["steps"][-1]["result"]
    assert plan["page"] == "Regional Performance"
    assert [visual["type"] for visual in plan["visuals"]] == [
        "card", "clusteredBarChart", "lineChart", "slicer",
    ]
    assert [
        (visual.get("category") or visual.get("field"))["field"]
        for visual in plan["visuals"][1:]
    ] == ["Region", "Order Date", "Customer Segment"]
    assert len(plan["additionalPages"]) == 1
    second_page = plan["additionalPages"][0]
    assert second_page["page"] == "Customer Mix"
    assert [visual["type"] for visual in second_page["visuals"]] == [
        "clusteredBarChart", "slicer",
    ]
    assert [
        (visual.get("category") or visual.get("field"))["field"]
        for visual in second_page["visuals"]
    ] == ["Customer Segment", "Region"]

    report_payload = {"sourceId": source, "reportSpec": plan}
    preview = client.post("/api/report/preview", json=report_payload)
    assert preview.status_code == 200, preview.text
    assert [len(preview.json()["visuals"]), len(preview.json()["additionalPages"][0]["visuals"])] == [4, 2]
    assert client.post("/api/report/approve", json=report_payload).status_code == 200
    generated = client.post("/api/report/generate", json=report_payload)
    assert generated.status_code == 200, generated.text
    assert generated.json()["validation"]["passed"], generated.json()["validation"]
    download = client.get(f"/api/report/{generated.json()['reportId']}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        pages_file = next(name for name in archive.namelist() if name.endswith("/pages/pages.json"))
        manifest = json.loads(archive.read(pages_file))
        page_ids = manifest["pageOrder"]
        assert len(page_ids) == 2
        page_root = pages_file.removesuffix("pages.json")
        for page_id, expected_name, expected_count in zip(
            page_ids, ["Regional Performance", "Customer Mix"], [4, 2],
        ):
            root = f"{page_root}{page_id}/"
            page_json = json.loads(archive.read(f"{root}page.json"))
            assert page_json["displayName"] == expected_name
            visuals = [
                name for name in archive.namelist()
                if name.startswith(f"{root}visuals/")
                and name.endswith("/visual.json")
                and "/autobi-decoration-" not in name
            ]
            assert len(visuals) == expected_count
            slicer_visuals = [
                json.loads(archive.read(name))["visual"]
                for name in visuals
                if json.loads(archive.read(name))["visual"]["visualType"] == "slicer"
            ]
            assert len(slicer_visuals) == 1
            assert all(
                visual["objects"]["selection"][0]["properties"]["singleSelect"]["expr"]["Literal"]["Value"]
                == "false"
                for visual in slicer_visuals
            )
