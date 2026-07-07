"""
modules.py — ModuleBuilder: cluster classes into modules, build the
module-level dependency graph.

This is the ZOOM-OUT that makes analysis architecture-level instead of
class-level. Class-level smells (god class, deep inheritance) belong to
DesignCritic; here we only ever look at modules and the edges between them.

Strategy (decided during design):
    folder  → namespace → community(Louvain)
The default 'auto' picks the first that yields a meaningful split (≥2
modules); the user can force one, or supply explicit groups later.

Pure code, no LLM, no I/O — input is plain rows, output is a ModuleGraph.
"""
import os
import fnmatch
from dataclasses import dataclass, field

import networkx as nx
from networkx.algorithms import community as nx_comm


@dataclass
class ModuleGraph:
    graph: nx.DiGraph                       # nodes=module names; edges carry evidence+weight
    member_index: dict = field(default_factory=dict)   # class_qname -> module
    members: dict = field(default_factory=dict)        # module -> [class_qname]
    strategy: str = "auto"


# ── membership strategies (each: classes -> {qname: module}) ─────────

def _by_folder(classes):
    """Module = the class's directory path RELATIVE to the common source
    root. Using the full relative path (not the basename) keeps two
    unrelated leaf folders distinct — `geometry/util` and `io/util` must
    not merge into one phantom `util` module, or the audit can report
    cycles/god-modules that don't exist."""
    dirs = [os.path.dirname(c.get("file_path") or "") for c in classes]
    nonempty = [d for d in dirs if d]
    try:
        common = os.path.commonpath(nonempty) if len(nonempty) > 1 else ""
    except ValueError:              # mixed absolute/relative or drives
        common = ""
    idx = {}
    for c, d in zip(classes, dirs):
        rel = os.path.relpath(d, common) if common and d else d
        module = rel.replace(os.sep, "/") if rel and rel != "." else "(root)"
        idx[c["qualified_name"]] = module
    return idx


def _by_namespace(classes):
    """Group by namespace — but by the first DISTINGUISHING segment, not
    the raw first one. A whole codebase under a shared top-level namespace
    (UGS::SimulationPost::*, UGS::CaeSim::CaePost::*) would otherwise
    collapse into one useless `UGS` module; stripping the common `UGS`
    prefix yields the real modules `SimulationPost` and `CaeSim`."""
    ns_of = {c["qualified_name"]: c["qualified_name"].split("::")[:-1]
             for c in classes}
    non_empty = [ns for ns in ns_of.values() if ns]
    common = 0
    if non_empty:
        for i in range(min(len(ns) for ns in non_empty)):
            seg = non_empty[0][i]
            if all(len(ns) > i and ns[i] == seg for ns in non_empty):
                common += 1
            else:
                break
    idx = {}
    for qn, ns in ns_of.items():
        if not ns:
            idx[qn] = "(root)"
        else:
            rest = ns[common:]          # first segment past the shared prefix
            idx[qn] = rest[0] if rest else ns[-1]
    return idx


def _by_community(classes, relationships):
    """Undirected community detection when folders/namespaces don't split
    the code. Falls back gracefully across networkx versions."""
    g = nx.Graph()
    names = {c["qualified_name"] for c in classes}
    g.add_nodes_from(names)
    for r in relationships:
        s, t = r.get("source_qname"), r.get("target_qname")
        if s in names and t in names and s != t:
            g.add_edge(s, t)
    try:
        comms = nx_comm.louvain_communities(g, seed=1)
    except Exception:
        comms = nx_comm.greedy_modularity_communities(g)
    idx = {}
    for i, comm in enumerate(comms):
        # name the module after its most-connected member (readable)
        rep = max(comm, key=lambda n: g.degree(n)) if comm else f"c{i}"
        label = f"cluster:{rep.split('::')[-1]}"
        for n in comm:
            idx[n] = label
    return idx


def _class_matches(cls, patterns):
    """A class belongs to a group if any pattern globs its file path,
    short name, or qualified name.

    Users write path patterns relative to their source tree ('ui/**'),
    but scans store ABSOLUTE paths — so path-like patterns also match as
    a '*/'-anchored suffix. The '/' anchor keeps it precise: '*/ui/**'
    matches '/proj/ui/View.h' but never 'gui/View.h'."""
    qn = cls["qualified_name"]
    short = qn.split("::")[-1]
    fp = cls.get("file_path") or ""
    for p in patterns:
        if fnmatch.fnmatch(fp, p) or fnmatch.fnmatch(short, p) \
                or fnmatch.fnmatch(qn, p):
            return True
        if "/" in p and fnmatch.fnmatch(fp, "*/" + p):
            return True
    return False


def _by_explicit(classes, groups):
    """Assign each class to the first user-defined group it matches;
    unmatched classes land in '(unassigned)' so violations involving them
    are still visible rather than silently dropped."""
    idx = {}
    for c in classes:
        placed = None
        for g in groups:
            if _class_matches(c, g.match):
                placed = g.name
                break
        idx[c["qualified_name"]] = placed or "(unassigned)"
    return idx


def _distinct(idx):
    return len(set(idx.values()))


# ── builder ──────────────────────────────────────────────────────────

class ModuleBuilder:
    """Groups classes into modules and aggregates class edges into module
    edges (preserving the underlying class-edge evidence)."""

    @staticmethod
    def build(classes, relationships, strategy="auto", groups=None) -> ModuleGraph:
        member_index, used = ModuleBuilder._assign(
            classes, relationships, strategy, groups)

        members = {}
        for qn, mod in member_index.items():
            members.setdefault(mod, []).append(qn)

        g = nx.DiGraph()
        g.add_nodes_from(members.keys())
        for r in relationships:
            s, t = r.get("source_qname"), r.get("target_qname")
            if t is None or s not in member_index or t not in member_index:
                continue
            ms, mt = member_index[s], member_index[t]
            if ms == mt:
                continue                       # internal to a module — ignore
            ev = _evidence_str(r)
            kind = r.get("kind")
            if g.has_edge(ms, mt):
                d = g[ms][mt]
                d["weight"] += 1
                d["evidence"].append(ev)
                d["kinds"].add(kind)
                d["kind_counts"][kind] = d["kind_counts"].get(kind, 0) + 1
            else:
                # weight = how many class references back this module edge;
                # kind_counts breaks that down per relationship kind so the
                # decoupling planner can price a cut by WHAT it severs
                # (an inherits reference is a heavier refactor than a
                # depends reference), not just how many lines it touches.
                g.add_edge(ms, mt, weight=1, evidence=[ev],
                           kinds={kind}, kind_counts={kind: 1})
        return ModuleGraph(graph=g, member_index=member_index,
                           members=members, strategy=used)

    @staticmethod
    def _assign(classes, relationships, strategy, groups=None):
        """Return (member_index, strategy_actually_used)."""
        # Explicit user groups win when provided (that's the whole point of
        # the user declaring them).
        if groups:
            return _by_explicit(classes, groups), "explicit"
        if strategy in ("auto", "folder"):
            folder = _by_folder(classes)
            if strategy == "folder" or _distinct(folder) >= 2:
                return folder, "folder"
        if strategy in ("auto", "namespace"):
            ns = _by_namespace(classes)
            if strategy == "namespace" or _distinct(ns) >= 2:
                return ns, "namespace"
        return _by_community(classes, relationships), "community"


def _evidence_str(r):
    s = r.get("source_qname")
    t = r.get("target_qname") or r.get("target_name")
    loc = ""
    if r.get("evidence_file"):
        loc = f" ({os.path.basename(r['evidence_file'])}:{r.get('evidence_line')})"
    return f"{s} --{r.get('kind')}--> {t}{loc}"
