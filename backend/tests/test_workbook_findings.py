"""Regressions found while validating the user's six-sheet workbook."""
import json
import os
import subprocess
import sys
from datetime import datetime,timedelta
from pathlib import Path

from openpyxl import Workbook
from automation.excel import ExcelAnalyzer


def test_merged_annotation_excluded_but_note_category_retained(tmp_path):
    book=Workbook();sheet=book.active
    sheet.append(['Category','Units']);sheet.append(['Notes',4]);sheet.append(['Other',6])
    sheet.append(['Note: source annotation']);sheet.merge_cells('A4:B4')
    path=tmp_path/'notes.xlsx';book.save(path)
    tables=ExcelAnalyzer().analyze(path).to_metadata()['tables']
    table=next(t for t in tables.values() if any(c['name']=='Units' for c in t['columns']))
    assert table['rows']==[{'Category':'Notes','Units':4},{'Category':'Other','Units':6}]


def test_integer_prices_and_rates_do_not_receive_sum_defaults(tmp_path):
    book=Workbook();sheet=book.active
    sheet.append(['Region','Revenue','Avg Selling Price','Margin %'])
    sheet.append(['North',100,10,.2]);sheet.append(['South',200,20,.3])
    sheet['D2'].number_format='0.0%';sheet['D3'].number_format='0.0%'
    path=tmp_path/'rates.xlsx';book.save(path)
    table=next(iter(ExcelAnalyzer().analyze(path).to_metadata()['tables'].values()))
    assert 'Total Revenue' in table['measures']
    assert 'Total Margin %' not in table['measures']
    assert 'Total Avg Selling Price' not in table['measures']
    assert table['measureDefinitions']['Average Avg Selling Price']['aggregation']=='AVERAGE'
    assert table['measureDefinitions']['Average Margin %']['aggregation']=='AVERAGE'


def test_daily_preview_preserves_dates_and_blank_categories(monkeypatch):
    import asyncio
    from backend.api import report
    rows=[{'Date':datetime(2026,1,1)+timedelta(days=i),'Region':None if i==0 else 'North','Amount':i+1} for i in range(120)]
    metadata={'tables':{'Facts':{'columns':['Date','Region','Amount'],'measures':['Total Amount'],
        'measureDefinitions':{'Total Amount':{'column':'Amount','aggregation':'SUM'}}}},'relationships':[]}
    monkeypatch.setattr(report,'load_source_metadata',lambda _:metadata)
    monkeypatch.setattr(report,'load_source_payload',lambda _:{'metadata':{'tables':{'Facts':{'rows':rows}}}})
    binding=lambda field,kind='column':{'table':'Facts','field':field,'fieldType':kind}
    result=asyncio.run(report.preview_report({'sourceId':'test','reportSpec':{'page':'Test','visuals':[
        {'type':'lineChart','category':binding('Date'),'value':binding('Total Amount','measure')},
        {'type':'clusteredBarChart','category':binding('Region'),'value':binding('Total Amount','measure')} ]}}))
    line,bars=result['visuals']
    assert len(line['data'])==120
    assert line['data'][0]['value']==1
    assert line['data'][-1]['value']==120
    assert sum(p['value'] for p in bars['data'])==sum(range(1,121))
    assert next(p['value'] for p in bars['data'] if p['label']=='(Blank)')==1


def test_table_preview_displays_all_columns_and_measures_with_filters(monkeypatch):
    import asyncio
    from backend.api import report
    rows = [
        {'KPI': 'Sales', 'Value': 5, 'Region': 'North'},
        {'KPI': 'Sales', 'Value': 5, 'Region': 'North'},
        {'KPI': 'Sales', 'Value': 7, 'Region': 'South'},
        {'KPI': 'Profit', 'Value': None, 'Region': 'North'},
    ]
    metadata = {'tables': {'KPI Summary': {'columns': ['KPI', 'Value', 'Region'],
        'measures': ['Total Value'], 'measureDefinitions': {'Total Value': {'column': 'Value', 'aggregation': 'SUM'}}}}, 'relationships': []}
    monkeypatch.setattr(report, 'load_source_metadata', lambda _: metadata)
    monkeypatch.setattr(report, 'load_source_payload', lambda _: {'metadata': {'tables': {'KPI Summary': {'rows': rows}}}})
    binding = lambda field, kind='column': {'table': 'KPI Summary', 'field': field, 'fieldType': kind}
    spec = {'page': 'Details', 'visuals': [
        {'type': 'table', 'rows': [binding('KPI'), binding('Value')]},
        {'type': 'table', 'rows': [binding('KPI'), binding('Value')], 'values': [binding('Total Value', 'measure')]},
    ]}
    result = asyncio.run(report.preview_report({'sourceId': 'test', 'reportSpec': spec, 'filters': {'KPI Summary.Region': ['North']}}))
    assert result['visuals'][0]['tableRows'] == [['Sales', 5], ['Profit', None]]
    assert result['visuals'][1]['tableRows'] == [['Sales', 5, 10.0], ['Profit', None, 0.0]]


def test_table_preview_bounds_distinct_groups_without_losing_late_members(monkeypatch):
    import asyncio
    from backend.api import report
    rows = [{'KPI': f'Item {i}', 'Value': 1} for i in range(report.MAX_PREVIEW_POINTS + 1)]
    rows.append({'KPI': 'Item 0', 'Value': 2})
    metadata = {'tables': {'KPI': {'columns': ['KPI', 'Value'], 'measures': ['Total Value'],
        'measureDefinitions': {'Total Value': {'column': 'Value', 'aggregation': 'SUM'}}}}, 'relationships': []}
    monkeypatch.setattr(report, 'load_source_metadata', lambda _: metadata)
    monkeypatch.setattr(report, 'load_source_payload', lambda _: {'metadata': {'tables': {'KPI': {'rows': rows}}}})
    binding = lambda field, kind: {'table': 'KPI', 'field': field, 'fieldType': kind}
    spec = {'page': 'Details', 'visuals': [{'type': 'table', 'rows': [binding('KPI', 'column')],
                                             'values': [binding('Total Value', 'measure')]}]}
    result = asyncio.run(report.preview_report({'sourceId': 'test', 'reportSpec': spec}))
    visual = result['visuals'][0]
    assert len(visual['tableRows']) == report.MAX_PREVIEW_POINTS
    assert visual['tableRows'][0] == ['Item 0', 3.0]
    assert str(report.MAX_PREVIEW_POINTS) in visual['previewNotice']


def test_uploaded_workbook_end_to_end(tmp_path):
    project=Path(__file__).resolve().parents[2]
    env={**os.environ,'AURA_PROJECT':str(project),'AURA_VALIDATION_OUTPUT':str(tmp_path),
        'AURA_TEST_WORKBOOK':str(project/'backend/tests/fixtures/Test_data.xlsx'),'APP_DATA_DIR':str(tmp_path/'runtime')}
    result=subprocess.run([sys.executable,str(project/'scripts/validate_workbook.py')],env=env,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,(result.stdout+'\n'+result.stderr)[-8000:]
    evidence=json.loads((tmp_path/'results.json').read_text())
    assert evidence['checks']>=96
    assert evidence['failed']==0
