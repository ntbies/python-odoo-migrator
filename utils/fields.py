# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, List, Set, Iterable

from rpc.jsonrpc import OdooClient


def _get_stored_fields(client: OdooClient, model: str) -> Dict[str, Dict[str, Any]]:
    fields = client.fields_get(model)
    return {name: info for name, info in fields.items() if info.get('store')}


def compare_model_fields(source: OdooClient, target: OdooClient, model: str) -> Dict[str, Any]:
    src = _get_stored_fields(source, model)
    tgt = _get_stored_fields(target, model)

    src_names: Set[str] = set(src.keys())
    tgt_names: Set[str] = set(tgt.keys())

    common = sorted(src_names & tgt_names)
    source_only = sorted(src_names - tgt_names)
    target_only = sorted(tgt_names - src_names)

    common_details: List[Dict[str, Any]] = []
    for name in common:
        s = src[name]
        t = tgt[name]
        common_details.append({
            'name': name,
            'source': {'type': s.get('type'), 'relation': s.get('relation')},
            'target': {'type': t.get('type'), 'relation': t.get('relation')},
            'compatible': s.get('type') == t.get('type') and s.get('relation') == t.get('relation'),
        })

    return {
        'model': model,
        'source_only': source_only,
        'target_only': target_only,
        'common_stored': common_details,
    }


# ---------- Model specification helpers (for idmap guidance) ----------
def model_fields_spec(client: OdooClient, model: str) -> Dict[str, Any]:
    """Return a normalized specification of stored fields for a model.

    Focuses on simplifying to what's useful for migrations and idmap selection:
    - stored_fields: {field: {type, relation}}
    - m2o_relations: {field: relation_model}
    """
    fields = _get_stored_fields(client, model)
    stored: Dict[str, Dict[str, Any]] = {}
    m2o: Dict[str, str] = {}
    for name, info in fields.items():
        ftype = info.get("type")
        rel = info.get("relation")
        stored[name] = {
            "type": ftype,
            "relation": rel,
        }
        if ftype == "many2one" and rel:
            m2o[name] = rel
    return {
        "model": model,
        "stored_fields": stored,
        "m2o_relations": m2o,
    }


def collect_models_spec(source: OdooClient, target: OdooClient, models: Iterable[str]) -> Dict[str, Any]:
    """Build a models specification for both source and target environments.

    Output structure:
    {
      "source": {model: {stored_fields, m2o_relations}},
      "target": {model: {stored_fields, m2o_relations}},
    }
    """
    src_specs: Dict[str, Any] = {}
    tgt_specs: Dict[str, Any] = {}
    for m in models:
        try:
            src_specs[m] = model_fields_spec(source, m)
        except Exception:
            # Ignore failures per model; proceed with others
            pass
        try:
            tgt_specs[m] = model_fields_spec(target, m)
        except Exception:
            pass
    return {"source": src_specs, "target": tgt_specs}


def _make_table(headers: List[str], rows: List[List[Any]]) -> str:
    str_rows: List[List[str]] = [["" if c is None else str(c) for c in row] for row in rows]
    widths = [len(str(h)) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
            else:
                widths.append(len(cell))
    def fmt_row(row: List[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"
    def sep() -> str:
        return "+-" + "-+-".join("-" * w for w in widths) + "-+"

    out: List[str] = []
    out.append(sep())
    out.append(fmt_row([str(h) for h in headers]))
    out.append(sep())
    for row in str_rows:
        padded = row + [""] * (len(headers) - len(row))
        out.append(fmt_row(padded))
    out.append(sep())
    return "\n".join(out)


def format_comparison_report_table(report: Dict[str, Any]) -> str:
    model = report.get("model", "")
    src_only: List[str] = report.get("source_only", [])
    tgt_only: List[str] = report.get("target_only", [])
    common: List[Dict[str, Any]] = report.get("common_stored", [])

    lines: List[str] = []
    title = f"Field comparison for model: {model}"
    lines.append(title)
    lines.append("=" * len(title))

    lines.append("\nSource-only stored fields")
    if src_only:
        src_rows = [[name] for name in src_only]
        lines.append(_make_table(["field"], src_rows))
    else:
        lines.append("(none)")

    lines.append("\nTarget-only stored fields")
    if tgt_only:
        tgt_rows = [[name] for name in tgt_only]
        lines.append(_make_table(["field"], tgt_rows))
    else:
        lines.append("(none)")

    lines.append("\nCommon stored fields")
    if common:
        headers = [
            "field",
            "src.type",
            "src.rel",
            "tgt.type",
            "tgt.rel",
            "compatible",
        ]
        rows: List[List[Any]] = []
        for item in common:
            s = item.get("source", {})
            t = item.get("target", {})
            rows.append([
                item.get("name", ""),
                s.get("type", ""),
                s.get("relation", "") or "",
                t.get("type", ""),
                t.get("relation", "") or "",
                "yes" if item.get("compatible") else "no",
            ])
        lines.append(_make_table(headers, rows))
    else:
        lines.append("(none)")

    return "\n".join(lines)
