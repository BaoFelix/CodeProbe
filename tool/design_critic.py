"""
design_critic.py — top-down design analysis via two-pass LLM synthesis.

Pass 1 (leaf-up): for each subtree of the dominator forest, analyze it
independently to keep context tight. Emit essence + ideal decomposition
+ pain points + current-to-ideal mappings.

Pass 2 (synthesis): combine the Pass 1 outputs with cross-subtree
relationships to produce a module-level workflow, missing abstractions,
and prioritized recommendations.

If `skills/design_critic.md` exists, its contents replace the embedded
prompt template — that's how a user installs their own methodology.
Otherwise the embedded scattered templates in _critic_phases /
_critic_lenses are assembled and used.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, wait

import networkx as nx

from .config import LLM_MAX_WORKERS, LLM_REVIEW_DEADLINE
from .agents import BaseAgent
from ._critic_lenses import (
    LENS_ESSENCE, LENS_PIPELINE, LENS_COHESION, LENS_ALTITUDE,
    LENS_INTERFACE, LENS_EVIDENCE,
)
from ._critic_phases import (
    SYSTEM_PREAMBLE, SUBTREE_TEMPLATE, MODULE_TEMPLATE,
)
from .workflow import build_graph, fold_abstractions, condense, find_roots
from .model import Entity, Relationship


# ── Skill override ─────────────────────────────────────────────
# Anchored at the project's skills/ dir, not the CWD — an external MCP
# host may launch this process from an arbitrary directory.

from .config import SKILLS_DIR as _SKILL_DIR


def _load_user_override():
    """Look for a user-provided override file. The user can drop their
    own methodology at `skills/design_critic.md` (or in any subfolder)
    and we use that as the single template instead of the embedded
    scattered ones.
    """
    if not _SKILL_DIR.is_dir():
        return None
    for path in _SKILL_DIR.rglob('design_critic.md'):
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            continue
    return None


# ── Prompt assembly ────────────────────────────────────────────

def _build_subtree_prompt(root_qname, classes, methods, fields, relations):
    classes_block = _format_classes(classes)
    methods_block = _format_methods(methods)
    fields_block = _format_fields(fields)
    relations_block = _format_relations(relations)
    return SUBTREE_TEMPLATE.format(
        preamble=SYSTEM_PREAMBLE,
        root_qname=root_qname,
        classes_block=classes_block,
        methods_block=methods_block,
        fields_block=fields_block,
        relations_block=relations_block,
        lens_essence=LENS_ESSENCE,
        lens_pipeline=LENS_PIPELINE,
        lens_cohesion=LENS_COHESION,
        lens_altitude=LENS_ALTITUDE,
        lens_interface=LENS_INTERFACE,
        lens_evidence=LENS_EVIDENCE,
    )


def _build_module_prompt(subtree_results, cross_relations):
    return MODULE_TEMPLATE.format(
        preamble=SYSTEM_PREAMBLE,
        subtree_summaries=_format_subtree_summaries(subtree_results),
        cross_relations_block=_format_relations(cross_relations),
        lens_interface=LENS_INTERFACE,
        lens_evidence=LENS_EVIDENCE,
    )


def _format_classes(entities):
    lines = []
    for e in entities:
        kind = e['kind'] if isinstance(e, dict) else e.kind
        qname = e['qualified_name'] if isinstance(e, dict) else e.qualified_name
        file_path = e['file_path'] if isinstance(e, dict) else e.file_path
        start = e['start_line'] if isinstance(e, dict) else e.start_line
        end = e['end_line'] if isinstance(e, dict) else e.end_line
        lines.append(f"  - {kind} {qname}  ({file_path}:{start}-{end})")
    return '\n'.join(lines) if lines else '  (none)'


def _format_methods(methods):
    lines = []
    for m in methods[:60]:  # cap to keep prompts bounded
        qname = m['qualified_name'] if isinstance(m, dict) else m.qualified_name
        sig = m['signature'] if isinstance(m, dict) else m.signature
        lines.append(f"  - {qname}  ::  {sig}")
    if len(methods) > 60:
        lines.append(f"  ... and {len(methods) - 60} more")
    return '\n'.join(lines) if lines else '  (none)'


def _format_fields(fields):
    lines = []
    for f in fields[:40]:
        qname = f['qualified_name'] if isinstance(f, dict) else f.qualified_name
        sig = f['signature'] if isinstance(f, dict) else f.signature
        lines.append(f"  - {qname}  ::  {sig}")
    if len(fields) > 40:
        lines.append(f"  ... and {len(fields) - 40} more")
    return '\n'.join(lines) if lines else '  (none)'


def _format_relations(rels):
    lines = []
    seen = set()
    for r in rels[:80]:
        src = r['source_qname'] if isinstance(r, dict) else r.source_qname
        tgt = (r['target_qname'] if isinstance(r, dict) else r.target_qname) \
              or (r['target_name'] if isinstance(r, dict) else r.target_name)
        kind = r['kind'] if isinstance(r, dict) else r.kind
        key = (src, tgt, kind)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  - {src}  --{kind}-->  {tgt}")
    return '\n'.join(lines) if lines else '  (none)'


def _format_subtree_summaries(subtree_results):
    blocks = []
    for r in subtree_results:
        result = r['result']
        if not result:
            continue
        block = (f"  ▸ {r['root']}\n"
                 f"    essence: {result.get('essence', '?')}\n"
                 f"    pipeline: {[s.get('name') for s in result.get('pipeline', [])]}\n"
                 f"    components: {[c.get('name') for c in result.get('components', [])]}\n"
                 f"    pains: {len(result.get('pains', []))}")
        blocks.append(block)
    return '\n'.join(blocks) if blocks else '  (none)'


# ── Tree assembly ──────────────────────────────────────────────

def _collect_subtree(C, root):
    """All ORIGINAL node names reachable from a forest root, inclusive.

    Reads the condensation's own 'members' sets instead of parsing the
    display labels: a cluster label carries SHORT names ('cluster(A, B)'),
    and re-parsing those used to silently drop every namespaced class
    (Garage::Workshop became an unknown 'Workshop') from the review.
    Members are real (possibly folded-representative) qualified names.
    """
    names = set(C.nodes[root]['members'])
    for n in nx.descendants(C, root):
        names.update(C.nodes[n]['members'])
    return names


def _expand_to_concrete(folded_names, rep_map):
    """Folded representatives → all original concrete class qnames."""
    concrete = set()
    inverse = {}
    for child, par in rep_map.items():
        inverse.setdefault(par, set()).add(child)
    for n in folded_names:
        concrete.add(n)
        concrete.update(inverse.get(n, set()))
    return concrete


# ── Main agent ─────────────────────────────────────────────────

class DesignCriticAgent(BaseAgent):
    """Two-pass design analysis. Replaces the per-class Seven Sins pass
    in the default pipeline. The Seven Sins flow stays available as a
    skill (move its prompts into `skills/seven_sins/...`).
    """

    def run(self, directory=None):
        print(f"\n  [DesignCritic] Holistic design analysis…")

        # Pull entities + relationships from DB
        entities = [self._row_to_entity(r) for r in self.db.get_entities()]
        relationships = [self._row_to_rel(r) for r in self.db.get_relationships()]

        if not entities:
            print("  ⚠ Empty entity store — run `analyze` (scan step) first.")
            return False

        # Re-run the graph pipeline so we know the forest and folds.
        g = build_graph(entities, relationships)
        h, rep_map = fold_abstractions(g, mode='leaves')
        C, label = condense(h)
        # Take ALL roots, even isolated ones. For design critique a
        # class with no outgoing edges still has methods/fields worth
        # auditing (a god class in a .cxx without cross-file context
        # presents exactly that way). Sort by reach descending so the
        # bigger stories come first.
        roots = sorted(
            list(find_roots(C)),
            key=lambda r: len(nx.descendants(C, r)),
            reverse=True)
        if not roots:
            print("  ⚠ No workflow roots — nothing to analyze.")
            return False

        override = _load_user_override()
        if override:
            print(f"  ✓ Using user override skill.")

        # Index helpers
        ent_by_qname = {e.qualified_name: e for e in entities}
        children_of = {}
        for e in entities:
            if e.parent_qname:
                children_of.setdefault(e.parent_qname, []).append(e)
        rels_by_source = {}
        for r in relationships:
            rels_by_source.setdefault(r.source_qname, []).append(r)

        # ── Pass 1: subtree analysis (concurrent + resumable) ─────
        # RESUME: skip subtrees already analyzed in the DB, so a re-run
        # only attempts the ones a previous (slow / interrupted) run never
        # finished. Re-run with a bigger LLM_TIMEOUT to patiently retry
        # the stubborn stragglers.
        done_roots = {r['subtree_root'] for r in (self.db.get_design_subtrees() or [])
                      if r['parsed_json']}
        todo = [root for root in roots if label[root] not in done_roots]
        if done_roots:
            print(f"  [pass 1] {len(roots)} subtree(s); "
                  f"{len(done_roots)} already done, {len(todo)} to do")
        else:
            print(f"  [pass 1] {len(roots)} subtree(s) to analyze"
                  f" (up to {LLM_MAX_WORKERS} in parallel)…")

        def analyze_one(root):
            """Runs in a worker thread. Pure of shared mutable state:
            builds its prompt, calls the LLM, returns the tuple. The main
            thread does all DB writes, so a straggler finishing after the
            deadline can't race the report."""
            folded_names = _collect_subtree(C, root)
            concrete_qnames = _expand_to_concrete(folded_names, rep_map)
            classes = [ent_by_qname[q] for q in concrete_qnames
                       if q in ent_by_qname]
            methods, fields = [], []
            for c in classes:
                for kid in children_of.get(c.qualified_name, []):
                    if kid.kind == 'method':
                        methods.append(kid)
                    elif kid.kind == 'field':
                        fields.append(kid)
            local_relations = [r for r in relationships
                               if r.source_qname in concrete_qnames
                               and r.target_qname in concrete_qnames]
            prompt = self._build_subtree_call(
                override, label[root], classes, methods, fields, local_relations)
            response = self.llm.generate(
                prompt, tag=f"critic_subtree_{label[root][:16]}")
            return label[root], prompt, response, _safe_parse_json(response)

        completed, failed, pending = 0, 0, set()
        if todo:
            ex = ThreadPoolExecutor(max_workers=min(LLM_MAX_WORKERS, len(todo)))
            futures = {ex.submit(analyze_one, root): root for root in todo}
            deadline = LLM_REVIEW_DEADLINE or None      # 0 → wait for all
            finished, pending = wait(futures, timeout=deadline)
            # Persist ONLY what finished, on the main thread (deterministic
            # report; no background DB writes racing pass 2).
            for fut in finished:
                try:
                    lbl, prompt, response, parsed = fut.result()
                except Exception as e:            # a worker blew up — degrade it
                    print(f"    ✗ subtree worker failed: {e}")
                    failed += 1
                    continue
                self.db.save_design_subtree(lbl, prompt, response, parsed)
                if response is None:
                    failed += 1
                else:
                    completed += 1
                    print(f"    ✓ {lbl} "
                          f"({len(parsed.get('pains', [])) if parsed else 0} pains)")
            if pending:
                # Soft deadline hit. Don't block on stragglers — abandon
                # them to a resume run. Their in-flight generate() still
                # warms the LLM cache, so the resume is fast.
                print(f"  ⏱ {len(pending)} subtree(s) still running past the "
                      f"{LLM_REVIEW_DEADLINE}s deadline — proceeding with "
                      f"partial results; re-run `analyze --from=review` to "
                      f"finish them.")
            ex.shutdown(wait=False, cancel_futures=True)

        # Nothing to synthesize AND no prior progress → stop with a cause
        # that matches WHY (unreachable vs just-slow), instead of emitting
        # an empty review as if it succeeded.
        if todo and completed == 0 and not done_roots:
            if failed and not pending:
                print("  ✗ LLM unreachable — every subtree call failed. Check "
                      "LLM_API_URL / LLM_API_KEY, or raise LLM_TIMEOUT if the "
                      "endpoint is just slow.")
            else:
                print(f"  ⏱ No subtree finished within the "
                      f"{LLM_REVIEW_DEADLINE}s deadline — raise "
                      f"LLM_REVIEW_DEADLINE / LLM_TIMEOUT, or re-run "
                      f"`analyze --from=review` to resume.")
            return False

        # Assemble pass-1 results from the DB (resumed + freshly completed),
        # so pass 2 sees the full accumulated picture, not just this run's.
        subtree_results = [{'root': r['subtree_root'],
                            'result': json.loads(r['parsed_json'])
                            if r['parsed_json'] else None}
                           for r in (self.db.get_design_subtrees() or [])]

        # Pass 2 synthesizes ACROSS subtree essences; with nothing usable to
        # synthesize (every response was unparseable), skip it — the report
        # still renders whatever per-subtree pains exist. Partial, not empty.
        if not any(s['result'] for s in subtree_results):
            print("  ⚠ No usable subtree analysis — skipping module synthesis.")
            return bool(subtree_results)

        # ── Pass 2: module synthesis ──────────────────────────
        print(f"  [pass 2] module synthesis…")
        cross_relations = [r for r in relationships
                           if r.target_qname is not None
                           and _label_for(r.source_qname, C, label, rep_map)
                              != _label_for(r.target_qname, C, label, rep_map)]
        module_prompt = self._build_module_call(override, subtree_results,
                                                cross_relations)
        module_response = self.llm.generate(module_prompt, tag="critic_module")
        module_parsed = _safe_parse_json(module_response)
        # Fingerprint the graph this review was computed FROM, so the
        # design_review tool can tell a cached result from a stale one
        # after the next rescan changes the graph.
        from .db import graph_fingerprint
        self.db.save_design_module('default', module_prompt, module_response,
                                   module_parsed,
                                   graph_hash=graph_fingerprint(
                                       self.db.get_relationships()))

        if module_parsed:
            recs = module_parsed.get('recommendations', [])
            print(f"  ✓ {len(recs)} module-level recommendation(s).")
        return True

    # ── Prompt builders ─────────────────────────────────────────

    def _build_subtree_call(self, override, root_qname, classes, methods,
                            fields, relations):
        if override:
            return self._render_user_template(
                override, scope='subtree',
                root=root_qname, classes=classes,
                methods=methods, fields=fields, relations=relations,
                subtree_results=None, cross_relations=None)
        return _build_subtree_prompt(root_qname, classes, methods,
                                     fields, relations)

    def _build_module_call(self, override, subtree_results, cross_relations):
        if override:
            return self._render_user_template(
                override, scope='module',
                root=None, classes=None, methods=None, fields=None,
                relations=None,
                subtree_results=subtree_results,
                cross_relations=cross_relations)
        return _build_module_prompt(subtree_results, cross_relations)

    def _render_user_template(self, template, *, scope, root, classes, methods,
                              fields, relations, subtree_results,
                              cross_relations):
        """User templates use a small token vocabulary: {SCOPE}, {ROOT},
        {CLASSES}, {METHODS}, {FIELDS}, {RELATIONS}, {SUBTREES},
        {CROSS_RELATIONS}. Missing tokens are silently ignored.
        """
        substitutions = {
            '{SCOPE}': scope,
            '{ROOT}': root or '',
            '{CLASSES}': _format_classes(classes or []),
            '{METHODS}': _format_methods(methods or []),
            '{FIELDS}': _format_fields(fields or []),
            '{RELATIONS}': _format_relations(relations or []),
            '{SUBTREES}': _format_subtree_summaries(subtree_results or []),
            '{CROSS_RELATIONS}': _format_relations(cross_relations or []),
        }
        out = template
        for tok, val in substitutions.items():
            out = out.replace(tok, val)
        return out

    # ── DB row helpers ──────────────────────────────────────────

    @staticmethod
    def _row_to_entity(row):
        return Entity(
            kind=row['kind'], name=row['name'],
            qualified_name=row['qualified_name'],
            file_path=row['file_path'] or '',
            start_line=row['start_line'] or 0,
            end_line=row['end_line'] or 0,
            parent_qname=row['parent_qname'],
            signature=row['signature'],
            attrs=json.loads(row['attrs'] or '{}'),
        )

    @staticmethod
    def _row_to_rel(row):
        return Relationship(
            source_qname=row['source_qname'],
            target_name=row['target_name'],
            target_qname=row['target_qname'],
            kind=row['kind'],
            evidence_file=row['evidence_file'] or '',
            evidence_line=row['evidence_line'] or 0,
            evidence_text=row['evidence_text'] or '',
            attrs=json.loads(row['attrs'] or '{}'),
        )


# ── Helpers ────────────────────────────────────────────────────

def _label_for(qname, C, label, rep_map):
    """Map a concrete qname to its (possibly folded) subtree label."""
    rep = rep_map.get(qname, qname)
    for cid, lbl in label.items():
        members = C.nodes[cid].get('members', set())
        if rep in members:
            return lbl
    return rep


def _safe_parse_json(text):
    """LLMs sometimes wrap JSON in markdown fences or extra prose. Try
    a few rescues before giving up.
    """
    if not text:
        return None
    text = text.strip()
    # Strip a ```json ... ``` fence if present
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        text = m.group(1)
    # Find the first { and the matching last }
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
