"""OKF bundle ingest (`graphify extract --okf`) round-trip tests.

Covers the OKF v0.1/v0.2 bundle layout (§3): concepts are any ``*.md`` except the
reserved ``index.md``/``log.md``, at any depth; concept IDs are bundle-relative
paths with ``.md`` stripped; cross-links resolve in both absolute
(``/dir/x.md``) and relative (``./x.md``) form (§6.1).
"""
import json
from pathlib import Path

from graphify.extractors.okf import extract_okf_bundle_to_graph


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_extract_okf_resolves_absolute_and_relative_links(tmp_path):
    bundle = tmp_path / "bundle"
    _write(bundle / "A.md", '---\ntype: Metric\ntitle: "Alpha"\n---\n\nSee [Beta](/tables/B.md).\n')
    _write(bundle / "tables" / "B.md", '---\ntype: BigQuery Table\ntitle: "Beta"\n---\n\nBack to [Alpha](../A.md).\n')
    out = tmp_path / "out" / "graph.json"

    extract_okf_bundle_to_graph(bundle, out)

    graph = json.loads(out.read_text(encoding="utf-8"))
    assert {n["id"] for n in graph["nodes"]} == {"a", "b"}
    pairs = {(link["source"], link["target"]) for link in graph["links"]}
    assert ("a", "b") in pairs
    assert ("b", "a") in pairs


def test_extract_okf_skips_reserved_filenames(tmp_path):
    bundle = tmp_path / "bundle"
    _write(bundle / "A.md", '---\ntype: Metric\ntitle: "Alpha"\n---\n\nbody\n')
    _write(bundle / "index.md", "# Index\n\n* [Alpha](A.md) - the only concept\n")
    _write(bundle / "log.md", "# Log\n\n## 2026-05-22\n* **Creation**: [Alpha](A.md).\n")
    out = tmp_path / "out" / "graph.json"

    extract_okf_bundle_to_graph(bundle, out)

    graph = json.loads(out.read_text(encoding="utf-8"))
    assert {n["id"] for n in graph["nodes"]} == {"a"}
    assert graph["links"] == []
