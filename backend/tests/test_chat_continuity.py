from backend.agents.router import route_agent_request


def test_acknowledgment_does_not_repeat_introduction():
    thanks = route_agent_request('Thank you!', False)
    assert thanks['action'] == 'respond'
    assert thanks['message'] != route_agent_request('Hello', False)['message']


def test_agent_run_fails_explicitly_when_ai_is_disabled(monkeypatch):
    import io
    from openpyxl import Workbook
    from fastapi.testclient import TestClient
    from backend.main import app

    monkeypatch.setenv('USE_AI', 'false')
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Sales'
    sheet.append(['Region', 'Revenue'])
    sheet.append(['North', 100])
    sheet.append(['North', 25])
    sheet.append(['South', 300])
    content = io.BytesIO()
    workbook.save(content)

    with TestClient(app) as client:
        uploaded = client.post('/api/datasource/excel', files={'file': ('sales.xlsx', content.getvalue())})
        assert uploaded.status_code == 200
        source_id = uploaded.json()['sourceId']
        result = client.post('/api/agent/runs', json={
            'sourceId': source_id,
            'request': 'What is total revenue?',
            'conversation': [
                {'role': 'user', 'text': 'What is total revenue?'},
            ],
        })

    assert result.status_code == 503
    assert "AI mode is disabled" in result.json()["detail"]
