from __future__ import annotations
import datetime
import json
import decimal
import math
import threading
import duckdb
import sqlglot
from sqlglot import exp

MAX_ROWS=200
MAX_SOURCE_ROWS=500_000
FUNCTIONS=set('TIMESTAMP_TRUNC TIME_TO_STR STR_TO_TIME SUM AVG COUNT MIN MAX MEDIAN QUANTILE_CONT QUANTILE_DISC PERCENTILE_CONT PERCENTILE_DISC STDDEV STDDEV_SAMP STDDEV_POP VARIANCE VAR_SAMP VAR_POP CORR COVAR_POP COVAR_SAMP ABS ROUND CEIL CEILING FLOOR POWER SQRT LN LOG EXP COALESCE NULLIF IF CASE CAST TRY_CAST EXTRACT DATE_TRUNC STRFTIME STRPTIME DATE_DIFF DATEDIFF DATE_ADD CURRENT_DATE YEAR MONTH DAY QUARTER LOWER UPPER TRIM LENGTH CONCAT SUBSTRING REPLACE ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE LAST_VALUE NTH_VALUE NTILE PERCENT_RANK CUME_DIST GROUP_CONCAT STRING_AGG ARRAY_AGG LIST BOOL_AND BOOL_OR ISNAN ISINF GREATEST LEAST'.split())


def validate_query(sql, tables):
    if not isinstance(sql,str) or not sql.strip() or len(sql)>16000:raise ValueError('Provide a bounded SELECT query.')
    try: statements=sqlglot.parse(sql,read='duckdb')
    except sqlglot.errors.ParseError as exc: raise ValueError('The analysis query could not be parsed.') from exc
    if len(statements)!=1 or not isinstance(statements[0],(exp.Select,exp.Union,exp.Intersect,exp.Except)):
        raise ValueError('Only one SELECT query is allowed.')
    tree=statements[0]
    ctes={x.alias for x in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this,exp.Identifier) or table.db or table.catalog:
            raise ValueError('External tables and table functions are unavailable.')
        if table.name not in tables and table.name not in ctes:
            raise ValueError(f'Table {table.name} is not in this source.')
    # All callable functions require an explicit allow-list. No filesystem/network functions.
    for func in tree.find_all(exp.Func):
        name=(func.name if isinstance(func,exp.Anonymous) else func.sql_name()).upper()
        if name not in FUNCTIONS:raise ValueError(f'Function {name} is not supported. Clarify the required analysis.')
    if any(isinstance(x,(exp.DDL,exp.DML,exp.Command)) for x in tree.walk()):
        raise ValueError('Only read-only analysis is allowed.')
    return tree.sql(dialect='duckdb')


def _quote(name):return '"'+name.replace('"','""')+'"'


def _kind(values):
    populated=[v for v in values if v is not None]
    if not populated:return 'VARCHAR'
    if all(isinstance(v,bool) for v in populated):return 'BOOLEAN'
    if all(isinstance(v,(int,float,decimal.Decimal)) and not isinstance(v,bool) for v in populated):return 'DOUBLE'
    if all(isinstance(v,(datetime.date,datetime.datetime)) for v in populated):return 'TIMESTAMP'
    return 'VARCHAR'


def execute_query(sql,metadata,raw_tables):
    tables=metadata.get('tables',{})
    validated=validate_query(sql,tables)
    if sum(len(raw_tables.get(n,{}).get('rows',[])) for n in tables)>MAX_SOURCE_ROWS:
        raise ValueError('This query exceeds the local row limit. Narrow the imported source or use a server query engine.')
    db=duckdb.connect(':memory:',config={'enable_external_access':'false','threads':'1','memory_limit':'512MB','max_temp_directory_size':'0B'})
    timer=threading.Timer(15,db.interrupt)
    try:
        for name,table in tables.items():
            cols=[c['name'] if isinstance(c,dict) else c for c in table.get('columns',[])]
            if not cols:continue
            rows=raw_tables.get(name,{}).get('rows',[])
            kinds=[_kind([r.get(c) for r in rows]) for c in cols]
            db.execute(f'CREATE TABLE {_quote(name)} ('+', '.join(f'{_quote(c)} {k}' for c,k in zip(cols,kinds))+')')
            if rows:
                def convert(v,k):
                    if v is None:return None
                    if k=='VARCHAR':return str(v)
                    if k=='DOUBLE' and not math.isfinite(float(v)):return None
                    return v
                db.executemany(f'INSERT INTO {_quote(name)} VALUES ('+','.join('?' for _ in cols)+')',
                               [[convert(r.get(c),k) for c,k in zip(cols,kinds)] for r in rows])
        timer.start()
        cursor=db.execute(f'SELECT * FROM ({validated}) AS bounded_result LIMIT {MAX_ROWS+1}')
        columns=[c[0] for c in cursor.description]
        if len(columns)>40:raise ValueError('Choose at most 40 result columns for this analysis.')
        if len(columns)!=len(set(columns)):raise ValueError('Give result columns unique aliases.')
        fetched=cursor.fetchall()
        def safe(v):
            if isinstance(v,(datetime.date,datetime.datetime,decimal.Decimal)):return str(v)
            if isinstance(v,float) and not math.isfinite(v):return None
            return v
        result = {'columns':columns,'rows':[{c:safe(v) for c,v in zip(columns,r)} for r in fetched[:MAX_ROWS]],
                'truncated':len(fetched)>MAX_ROWS,'rowLimit':MAX_ROWS,'query':validated,
                'sourceTables':sorted({t.name for t in sqlglot.parse_one(validated,read='duckdb').find_all(exp.Table) if t.name in tables})}
        if len(json.dumps(result,default=str))>100000:
            raise ValueError('The result is too large to explain. Narrow the requested fields, groups or filters.')
        return result
    except duckdb.Error as exc:
        raise ValueError('The analysis query could not execute against these fields. Clarify the calculation, types or join relationship.') from exc
    finally:
        timer.cancel();db.close()
