# Google Open Knowledge Format (OKF) exporter for graphify
from __future__ import annotations
import json
import re
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import networkx as nx

from graphify.analyze import _node_community_map
from graphify.build import edge_data

def _yaml_str(s: str) -> str:
    if s is None:
        return ""
    out: list[str] = []
    for ch in str(s):
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\0")
        elif cp == 0x2028:
            out.append("\\L")
        elif cp == 0x2029:
            out.append("\\P")
        elif cp < 0x20 or cp == 0x7F:
            out.append(f"\\x{cp:02x}")
        else:
            out.append(ch)
    return "".join(out)

def _obsidian_tag(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-/]", "", name.replace(" ", "_"))

def _cap_filename(s: str, limit: int = 200) -> str:
    b = s.encode("utf-8")
    if len(b) <= limit:
        return s
    digest = hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]
    keep = limit - 9
    truncated = b[:keep].decode("utf-8", "ignore")
    return f"{truncated}_{digest}"

def _dedup_node_filenames(G: nx.Graph, safe_name) -> dict[str, str]:
    node_filenames: dict[str, str] = {}
    used: set[str] = set()
    for node_id, data in G.nodes(data=True):
        base = safe_name(data.get("label", node_id))
        candidate = base
        n = 1
        while candidate.lower() in used:
            candidate = f"{base}_{n}"
            n += 1
        used.add(candidate.lower())
        node_filenames[node_id] = candidate
    return node_filenames

def to_okf(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_dir: str,
    community_labels: dict[int, str] | None = None,
    cohesion: dict[int, float] | None = None,
) -> int:
    """Export graph in Google's Open Knowledge Format (OKF) - a directory of
    Markdown files with YAML frontmatter linked via standard Markdown links,
    plus an okf.json manifest index file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    _manifest_path = out / ".graphify_okf_manifest.json"
    try:
        _owned: set[str] = set(json.loads(_manifest_path.read_text(encoding="utf-8")).get("files", []))
    except (OSError, ValueError):
        _owned = set()
    _written: list[str] = []
    _skipped: list[str] = []

    def _owned_write(rel_name: str, content: str) -> bool:
        target = out / rel_name
        if target.exists() and rel_name not in _owned:
            _skipped.append(rel_name)
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _written.append(rel_name)
        return True

    node_community = _node_community_map(communities)

    def safe_name(label: str) -> str:
        cleaned = re.sub(r'[\\/*?:"<>|#^[\]]', "", label.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")).strip()
        cleaned = re.sub(r"\.(md|mdx|qmd|markdown)$", "", cleaned, flags=re.IGNORECASE)
        if not re.search(r"\w", cleaned, flags=re.UNICODE):
            return "unnamed"
        return _cap_filename(cleaned)

    node_filename = _dedup_node_filenames(G, safe_name)

    def _dominant_confidence(node_id: str) -> str:
        confs = []
        for u, v, edata in G.edges(node_id, data=True):
            confs.append(edata.get("confidence", "EXTRACTED"))
        if not confs:
            return "EXTRACTED"
        return Counter(confs).most_common(1)[0][0]

    _FTYPE_TAG = {
        "code": "graphify/code",
        "document": "graphify/document",
        "paper": "graphify/paper",
        "image": "graphify/image",
    }

    current_time = datetime.now(timezone.utc).isoformat()

    concepts_written = 0
    concepts_manifest = []

    for node_id, data in G.nodes(data=True):
        label = data.get("label", node_id)
        cid = node_community.get(node_id)
        community_name = (
            community_labels.get(cid, f"Community {cid}")
            if community_labels and cid is not None
            else f"Community {cid}"
        )

        ftype = data.get("file_type", data.get("type", ""))
        ftype_tag = _FTYPE_TAG.get(ftype, f"graphify/{ftype}" if ftype else "graphify/document")
        dom_conf = _dominant_confidence(node_id)
        conf_tag = f"graphify/{dom_conf}"
        comm_tag = f"community/{_obsidian_tag(community_name)}"
        node_tags = [ftype_tag, conf_tag, comm_tag]

        lines = [
            "---",
            f'type: "{_yaml_str(ftype or "concept")}"',
            f'title: "{_yaml_str(label)}"',
            f'description: "Graphify concept from source: {_yaml_str(data.get("source_file", ""))}"',
            f'resource: "{_yaml_str(data.get("source_file", ""))}{":" + _yaml_str(str(data["source_location"])) if data.get("source_location") else ""}"',
        ]
        
        lines.append("tags:")
        for tag in node_tags:
            lines.append(f"  - {tag}")
        lines.append(f'timestamp: "{current_time}"')
        lines.append("---")
        lines.append("")
        lines.append(f"# {label}")
        lines.append("")

        neighbors = list(G.neighbors(node_id))
        if neighbors:
            lines.append("## Connections")
            for neighbor in sorted(neighbors, key=lambda n: G.nodes[n].get("label", n)):
                edata = edge_data(G, node_id, neighbor)
                neighbor_label = G.nodes[neighbor].get("label", neighbor)
                neighbor_fname = node_filename[neighbor] + ".md"
                relation = edata.get("relation", "")
                confidence = edata.get("confidence", "EXTRACTED")
                lines.append(f"- [{neighbor_label}]({neighbor_fname}) - `{relation}` [{confidence}]")
            lines.append("")

        fname = node_filename[node_id] + ".md"
        if _owned_write(fname, "\n".join(lines)):
            concepts_written += 1
            concepts_manifest.append({
                "file": fname,
                "type": ftype or "concept",
                "title": label
            })

    def _community_name(cid) -> str:
        return (
            community_labels.get(cid, f"Community {cid}")
            if community_labels and cid is not None
            else f"Community {cid}"
        )

    community_filename = {}
    used_community = set()
    for cid in communities:
        base = f"Community_{safe_name(_community_name(cid))}"
        candidate = base
        n = 1
        while candidate.lower() in used_community:
            candidate = f"{base}_{n}"
            n += 1
        used_community.add(candidate.lower())
        community_filename[cid] = candidate

    for cid, all_members in communities.items():
        community_name = _community_name(cid)
        members = [m for m in all_members if m in G and m in node_filename]
        n_members = len(members)
        coh_value = cohesion.get(cid) if cohesion else None

        lines = [
            "---",
            'type: "community"',
            f'title: "{_yaml_str(community_name)}"',
            f'description: "Graphify community containing {n_members} members"',
            f'resource: "community/{cid}"',
            "tags:",
            f"  - community/{_obsidian_tag(community_name)}",
        ]
        if coh_value is not None:
            lines.append(f"cohesion: {coh_value:.2f}")
        lines.append(f"members: {n_members}")
        lines.append(f'timestamp: "{current_time}"')
        lines.append("---")
        lines.append("")
        lines.append(f"# {community_name}")
        lines.append("")

        if coh_value is not None:
            cohesion_desc = (
                "tightly connected" if coh_value >= 0.7
                else "moderately connected" if coh_value >= 0.4
                else "loosely connected"
            )
            lines.append(f"**Cohesion:** {coh_value:.2f} - {cohesion_desc}")
        lines.append(f"**Members:** {n_members} nodes")
        lines.append("")

        lines.append("## Members")
        for node_id in sorted(members, key=lambda n: G.nodes[n].get("label", n)):
            data = G.nodes[node_id]
            node_label = data.get("label", node_id)
            node_fname = node_filename[node_id] + ".md"
            ftype = data.get("file_type", "")
            source = data.get("source_file", "")
            entry = f"- [{node_label}]({node_fname})"
            if ftype:
                entry += f" - {ftype}"
            if source:
                entry += f" - {source}"
            lines.append(entry)
        lines.append("")

        fname = community_filename[cid] + ".md"
        if _owned_write(fname, "\n".join(lines)):
            concepts_written += 1
            concepts_manifest.append({
                "file": fname,
                "type": "community",
                "title": community_name
            })

    okf_manifest = {
        "format": "okf-v0.1",
        "generator": "graphify",
        "timestamp": current_time,
        "concepts": concepts_manifest
    }
    _owned_write("okf.json", json.dumps(okf_manifest, indent=2))

    try:
        _manifest_path.write_text(json.dumps({"files": sorted(set(_written))}, indent=2), encoding="utf-8")
    except OSError:
        pass

    if _skipped:
        shown = ", ".join(_skipped[:5]) + (f" (+{len(_skipped) - 5} more)" if len(_skipped) > 5 else "")
        print(
            f"[graphify] WARNING: skipped {len(_skipped)} pre-existing file(s) graphify "
            f"did not create, to avoid overwriting your notes: {shown}. "
            f"Export into an empty directory to get the full OKF bundle.",
            file=sys.stderr,
        )

    return concepts_written
