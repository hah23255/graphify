# Google Open Knowledge Format (OKF) extractor/ingestion for graphify
#
# OKF bundles are a directory tree of markdown files (v0.1/v0.2 §3). Every
# `*.md` except the reserved `index.md` / `log.md` is a concept (§3.1); a
# concept's ID is its bundle-relative path with `.md` stripped (§2). Cross
# links are standard markdown links, in absolute bundle-relative
# (`/dir/x.md`) or relative (`./x.md`) form (§6.1).
from __future__ import annotations
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote
import yaml

_RESERVED = {"index.md", "log.md"}
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _concept_id(root: Path, file: Path) -> str:
    """Bundle-relative concept ID: leading '/', `.md` stripped (OKF §2/§6.1)."""
    return "/" + file.relative_to(root).as_posix()[:-3]


def _resolve_target(root: Path, src_file: Path, dest: str) -> str | None:
    """Resolve an OKF markdown link target to a bundle-relative concept ID (§6.1)."""
    dest = unquote(dest.split("?")[0].split("#")[0].strip())
    if not dest or dest.endswith("/"):
        return None
    if dest.startswith("/"):
        path = posixpath.normpath(dest)
    else:
        src_dir = "/" + src_file.relative_to(root).parent.as_posix()
        src_dir = "/" if src_dir == "/." else src_dir
        path = posixpath.normpath(posixpath.join(src_dir, dest))
    if path.endswith(".md"):
        path = path[:-3]
    return path


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Split an OKF document into (frontmatter, body).

    The block opens with a ``---`` line at the start of the file and closes at the
    next line that is exactly ``---`` (OKF §4). A naive ``str.split("---")`` would
    also cut at a ``---`` *inside* a value (e.g. a title containing ``--- text``),
    truncating otherwise-valid frontmatter and dropping the concept on ingest.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return None


def extract_okf_bundle_to_graph(bundle_dir: str | Path, output_graph_json: str | Path) -> None:
    """Ingest an OKF bundle directory and build a Graphify-compliant graph.json."""
    root = Path(bundle_dir)
    print(f"📥 Loading OKF Bundle from: {bundle_dir}")

    nodes: list[dict] = []
    links: list[dict] = []

    concept_files = [
        p for p in sorted(root.rglob("*.md"))
        if p.name not in _RESERVED
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    ]
    print(f"🔍 Found {len(concept_files)} concept markdown files in OKF bundle.")

    path_to_node_id: dict[str, str] = {}

    # Step 1: Read all nodes and register them
    for file in concept_files:
        try:
            content = file.read_text(encoding="utf-8")
            split = _split_frontmatter(content)
            if split is None:
                continue
            fm_text, _body = split

            frontmatter = yaml.safe_load(fm_text) or {}
            label = frontmatter.get("title", file.stem)
            nid = file.stem.lower()

            cid = _concept_id(root, file)
            path_to_node_id[cid] = nid
            path_to_node_id[cid + ".md"] = nid

            node_data = {
                "id": nid,
                "label": label,
                "file_type": "code" if frontmatter.get("type") == "Code Construct" else "document",
                "source_file": frontmatter.get("resource", "unknown"),
                "source_location": "L1",
                "community": 0,
                "community_name": (frontmatter.get("tags") or ["default"])[-1]
            }
            nodes.append(node_data)
        except Exception as exc:
            print(f"Error parsing concept {file}: {exc}")

    # Step 2: Parse body links to extract directed edges
    for file in concept_files:
        try:
            content = file.read_text(encoding="utf-8")
            split = _split_frontmatter(content)
            if split is None:
                continue
            _fm_text, body = split

            src_id = path_to_node_id.get(_concept_id(root, file))
            if not src_id:
                continue

            for text, dest in _LINK_RE.findall(body):
                target = _resolve_target(root, file, dest)
                dest_id = target and (path_to_node_id.get(target) or path_to_node_id.get(target + ".md"))

                if dest_id and src_id != dest_id:
                    relation = "references"
                    if "calls" in text or "calls" in content:
                        relation = "calls"
                    elif "contains" in text:
                        relation = "contains"

                    link_data = {
                        "source": src_id,
                        "target": dest_id,
                        "relation": relation,
                        "confidence": "EXTRACTED",
                        "weight": 1.0
                    }
                    if link_data not in links:
                        links.append(link_data)
        except Exception as exc:
            print(f"Error extracting links from {file}: {exc}")

    output_path = Path(output_graph_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    graph_out = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": nodes,
        "links": links
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(graph_out, f, indent=2)

    print(f"🎉 Success: Ingested OKF Bundle into Graphify graph.json: {output_path}")
