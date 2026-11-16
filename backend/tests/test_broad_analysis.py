import pytest
from backend.services.analytical_query import execute_query,validate_query

M={'tables':{'Sales':{'columns':['Region','Revenue','Cost']},'Regions':{'columns':['Region','Manager']}}}
R={'Sales':{'rows':[{'Region':'North','Revenue':10,'Cost':5},{'Region':'North','Revenue':20,'Cost':10},{'Region':'South','Revenue':30,'Cost':20}]},'Regions':{'rows':[{'Region':'North','Manager':'A'},{'Region':'South','Manager':'B'}]}}


def test_statistics_and_expression():
    result=execute_query('SELECT MEDIAN("Revenue") AS median, STDDEV_SAMP("Revenue") AS sd, SUM("Revenue"-"Cost") AS profit FROM "Sales"',M,R)
    assert result['rows']==[{'median':20.0,'sd':10.0,'profit':25.0}]


def test_join_and_window():
    result=execute_query('SELECT r."Manager", SUM(s."Revenue") AS revenue, RANK() OVER(ORDER BY SUM(s."Revenue") DESC) AS ranking FROM "Sales" s JOIN "Regions" r ON s."Region"=r."Region" GROUP BY r."Manager"',M,R)
    assert {row['revenue'] for row in result['rows']}=={30.0}
    assert len(result['rows'])==2


@pytest.mark.parametrize('sql',[
    "SELECT * FROM read_csv_auto('/etc/passwd')",'SELECT * FROM sqlite_master','SELECT * FROM other.Sales',
    "SELECT getenv('HOME')",'DROP TABLE Sales','SELECT 1; SELECT 2',"COPY Sales TO '/tmp/x'",
    "SELECT * FROM query('SELECT 1')","INSTALL httpfs","ATTACH '/tmp/test.db' AS x",
])
def test_external_and_mutating_queries_rejected(sql):
    with pytest.raises(ValueError): validate_query(sql,M['tables'])


def test_result_limit_is_explicit():
    raw={'Sales':{'rows':[{'Region':'N','Revenue':i,'Cost':0} for i in range(205)]}}
    result=execute_query('SELECT * FROM "Sales"',{'tables':{'Sales':M['tables']['Sales']}},raw)
    assert len(result['rows'])==200
    assert result['truncated'] is True


def test_cte_and_zero_denominator():
    result=execute_query('WITH x AS (SELECT SUM("Revenue") AS r FROM "Sales") SELECT r / NULLIF(0,0) AS ratio FROM x',M,R)
    assert result['rows']==[{'ratio':None}]


def test_ai_planned_query_is_executed_and_exposes_provenance(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from backend.services import ai_analysis_service as service
    class Provider:
        model='test'
        async def plan_data_question(self,*args):
            return {'status':'ready','intent':'analytical_query','sql':'SELECT SUM("Revenue"-"Cost") AS profit FROM "Sales"','explanation':'Revenue less cost.'}
    monkeypatch.setattr(service,'get_settings',lambda:SimpleNamespace(ai_provider='azure_openai'))
    monkeypatch.setattr(service,'get_ai_provider',lambda _:Provider())
    result=asyncio.run(service.analyze_data_question(M,R,'What is profit?'))
    assert result['findings']==[{'profit':25.0}]
    assert result['sourceFields']==['Sales.*']
    assert result['coverage']['truncated'] is False


def test_monthly_period_comparison_and_growth():
    import datetime
    metadata={'tables':{'Events':{'columns':['Day','Amount']}}}
    rows={'Events':{'rows':[{'Day':datetime.date(2026,1,1),'Amount':10},{'Day':datetime.date(2026,2,1),'Amount':15}]}}
    result=execute_query('WITH monthly AS (SELECT DATE_TRUNC(\'month\', "Day") AS month, SUM("Amount") AS amount FROM "Events" GROUP BY month) SELECT STRFTIME(month, \'%Y-%m\') AS month, amount, 100 * (amount - LAG(amount) OVER (ORDER BY month)) / NULLIF(LAG(amount) OVER (ORDER BY month),0) AS growth_percent FROM monthly ORDER BY month',metadata,rows)
    assert result['rows'][1]['growth_percent']==50.0
    assert result['rows'][0]['month']=='2026-01'
