"""Function-level, cross-language code graph (spec 4A).

Static analysis only — no LLM. Extractors emit FunctionDef/CallSite records,
the resolver links them into a CodeGraph, chains.py assembles entry-point
rooted call chains for the LLM analysis stage (plan 4B)."""
