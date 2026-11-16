"""Group a row-level measure and report quality exceptions without changing rows."""
import re
from collections import defaultdict
from statistics import quantiles
from backend.services.model_service import _active_relationships


def quality_analysis(metadata, raw_tables, question):
    q = question.casefold()
    if not re.search(r"\b(unusual|outliers?|anomalies|missing)\b", q):
        return None
    missing = re.search(r"\bmissing\s+(.+?)(?:\s+(?:information|values|data)\b|[.?!]|$)", q)
    group = re.search(r"\bby\s+([^.!?]+)", q)
    if not group or not missing:
        return None
    axes = [s.strip() for s in re.split(r",\s*(?:and\s+)?|\s+and\s+", group[1]) if s.strip()]
    def clarify(message):
        return {"answer":message, "question":question, "method":"Needs clarification.",
                "sourceFields":[], "findings":[], "filters":[], "nextStep":message}
    candidates = []
    for name, t in metadata["tables"].items():
        cols = {c.casefold():c for c in t["columns"]}
        if missing[1].strip() not in cols:
            continue
        numeric = [c for c in t["columns"] if t.get("columnRoles", {}).get(c) in {"measure","calculated_measure"}]
        mentioned = [c for c in numeric if re.search(rf"\b{re.escape(c.casefold())}\b",q)]
        if not mentioned and re.search(r"\bsales\b", q):
            mentioned = [c for c in numeric if re.search(r"\b(revenue|sales)\b",c,re.I) and "%" not in c]
        if mentioned:
            candidates.append((sum(a in cols for a in axes),name,cols,t,mentioned))
    if not candidates:
        return clarify("Name the transaction measure and the field to check for missing values.")
    candidates.sort(key=lambda x:x[0],reverse=True)
    if len(candidates)>1 and candidates[0][0] == candidates[1][0]:
        return clarify("Choose the transaction table for this analysis.")
    _,name,cols,t,measures = candidates[0]
    rows = raw_tables[name]["rows"]
    empty_field = cols[missing[1].strip()]
    dimensions = {}
    fields = [f"{name}.{m}" for m in measures + [empty_field]]
    for axis in axes:
        if axis in cols:
            col = cols[axis]
            dimensions[axis] = [r.get(col) for r in rows]
            fields.append(f"{name}.{col}")
        elif axis == "month":
            dates = [c for c in t["columns"] if t["columnTypes"].get(c,"").casefold() in {"date","datetime"}]
            if len(dates) != 1:
                return clarify("Choose the date field to group by calendar month.")
            dimensions[axis] = [str(r[dates[0]])[:7] if r.get(dates[0]) else None for r in rows]
            fields.append(f"{name}.{dates[0]}")
        else:
            options = []
            for rel in _active_relationships(metadata):
                if rel["source_table"] != name:
                    continue
                other = rel["target_table"]
                for c in metadata["tables"][other]["columns"]:
                    if not re.search(rf"\b{re.escape(c.casefold())}\b",axis):
                        continue
                    look = raw_tables[other]["rows"]
                    key = rel["target_column"]
                    index = {r[key]:r.get(c) for r in look if r.get(key) is not None}
                    if len(index) == len(look):
                        options.append(([index.get(r.get(rel["source_column"])) for r in rows],f"{other}.{c}"))
            if len(options)!=1:
                return clarify(f"Choose an available grouping for {axis}, or confirm a unique lookup relationship.")
            dimensions[axis],source = options[0]
            fields.append(source)
    ids = [c for c in t["columns"] if t.get("columnRoles",{}).get(c)=="identifier"
           and len({r.get(c) for r in rows})==len(rows) and all(r.get(c) is not None for r in rows)]
    def ref(i):
        return rows[i][ids[0]] if ids else i+1
    absent = [ref(i) for i,r in enumerate(rows) if r.get(empty_field) is None or str(r[empty_field]).strip()==""]
    findings = [{"missingField":empty_field,"count":len(absent),"records":absent}]
    notes = []
    for m in measures:
        values = [float(r[m]) if isinstance(r.get(m),(int,float)) else None for r in rows]
        clean = [v for v in values if v is not None]
        for axis,labels in dimensions.items():
            sums = defaultdict(float)
            for label,v in zip(labels,values):
                if v is not None:
                    sums[str(label) if label is not None else "(missing)"] += v
            findings.extend({"grouping":axis,"group":k,"measure":m,"total":v} for k,v in sorted(sums.items()))
        if len(clean)>=4 and re.search(r"\b(unusual|outliers?|anomalies)\b",q):
            q1,_,q3 = quantiles(clean,n=4,method="inclusive")
            lo,hi = q1-1.5*(q3-q1),q3+1.5*(q3-q1)
            out = [{"record":ref(i),"value":v} for i,v in enumerate(values) if v is not None and (v<lo or v>hi)]
            findings.append({"measure":m,"lowerFence":lo,"upperFence":hi,"outliers":out})
            notes.append(f"{len(out)} unusual {m} values")
    return {"answer": f"Grouped {len(rows)} rows from {name} by {', '.join(axes)}. "
                      f"{len(absent)} rows have missing {empty_field}. " + ", ".join(notes) + ".",
            "method":"Sum by each requested grouping. Outliers use inclusive quartiles and 1.5 × IQR fences across all numeric records.",
            "sourceFields":fields, "question":question, "filters":[], "findings":findings}
