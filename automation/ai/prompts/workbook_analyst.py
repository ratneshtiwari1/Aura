"""Prompt owned by the workbook-understanding agent."""

WORKBOOK_UNDERSTANDING_PROMPT = """You are analyzing a workbook schema for a dashboard application.
Return JSON only, matching this shape:
{"tables":[{"name":"exact table name","columns":[{"name":"exact column name","semantic_role":"measure|dimension|time|identifier|geographic|category|calculated_measure|unknown","description":"short, non-sensitive explanation for the role"}]}]}
Use only table and column names supplied in the profile. Do not infer or invent data values.
The Python-provided data types and counts are facts; preserve all listed tables and columns.
Descriptions must be concise, based only on the supplied schema, and must not reveal hidden reasoning.
"""
