"""Resolve paired column groups from a request and the selected table schema."""
import re


def paired_comparison(request, metadata):
    match = re.search(r"\b(?:comparing|compare)\s+([\w-]+)\s+(?:versus|vs\.?)\s+([\w-]+)\s+(.+?)\s+by\s+([^.?!]+)", request, re.I)
    if not match:
        return None
    first, second, metrics, axis = match.groups()
    fields = [x.strip() for x in re.split(r",\s*|\s+and\s+", metrics, flags=re.I) if x.strip()]
    for name, table in metadata["tables"].items():
        columns = {c.casefold(): c for c in table["columns"]}
        if axis.strip().casefold() not in columns:
            continue
        def binding(column, role):
            return {"table": name, "field": column, "fieldType": role}
        def measure(column):
            prefer_average = "%" in table.get("columnFormats", {}).get(column, "")
            for prefix in (["Average ", "Total "] if prefer_average else ["Total "]):
                for m in table["measures"]:
                    if m.casefold() == (prefix + column).casefold():
                        return binding(m, "measure")
            raise ValueError(f"Choose a measure for {column} in {name}.")
        category = binding(columns[axis.strip().casefold()], "column")
        visuals = []
        for metric in fields:
            names = [columns.get(f"{prefix} {metric}".casefold()) for prefix in (first, second)]
            if not all(names):
                break
            visuals.append({"type":"clusteredColumnChart", "title":f"{metric} by {category['field']}",
                            "category":category, "comparison":True, "values":[measure(c) for c in names]})
        if len(visuals) != len(fields):
            continue
        extras = re.search(r"\.\s*(?:show|include)\s+(.+?)[.!]?$", request, re.I)
        if extras:
            for phrase in re.split(r",\s*|\s+and\s+", extras[1], flags=re.I):
                words = re.findall(r"\w+", phrase.casefold())
                words = [w for w in words if w not in {"percentage","percent"}]
                acronym = "".join(w[0] for w in words)
                candidates = [c for c in columns.values() if c != category["field"] and
                              (c.casefold() == " ".join(words) or (len(acronym)>1 and
                               acronym in re.findall(r"\w+", c.casefold())))]
                if len(candidates) != 1:
                    raise ValueError(f"Which numeric field in {name} represents {phrase}?")
                visuals.append({"type":"clusteredColumnChart", "title":f"{phrase.title()} by {category['field']}",
                                "category":category, "value":measure(candidates[0])})
        return {"page":f"{first.title()} vs {second.title()}", "visuals":visuals}
    return None
