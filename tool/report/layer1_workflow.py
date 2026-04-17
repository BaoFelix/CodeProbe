"""Layer 1: Module Workflow — responsibility map (bird's eye view)."""
import re

from .helpers import _esc, _mermaid_id


def _section_responsibility_map(resps, tasks, module_info, all_deps):
    """Layer 1: Module Workflow — deterministic, all data from DB.

    Rule 1: Orchestrator from module_info (computed during scan)
    Rule 2: Cluster by orchestrator's outgoing deps as seeds
    Rule 3: Representative class by dependency analysis
    Rule 4: Recursive expansion based on class count and design issues
    Rule 5: No naming-based inference
    """
    all_task_names = [t['class_name'] for t in (tasks or [])]
    if not all_task_names:
        return '<section><h2>Layer 1: Module Workflow</h2><p>No classes registered.</p></section>'

    module_classes = set(all_task_names)

    # Build class_deps from DB
    class_deps = {}
    for d in (all_deps or []):
        src = d['source_class']
        class_deps.setdefault(src, []).append({
            'target': d['target_class'],
            'level': d['level_name'],
            'source': d['source_evidence'],
            'target_is_external': bool(d['target_is_external']),
        })

    orchestrator = module_info['orchestrator'] if module_info else None

    workflow = build_workflow_from_db(class_deps, resps or [], module_classes, orchestrator)

    return _render_workflow_diagram(workflow, resps or [], tasks)


def build_workflow_from_db(class_deps, resps, module_classes, orchestrator=None):
    """Build workflow from DB data. No reader, no file system."""
    if not orchestrator:
        orchestrator = _find_orchestrator(class_deps, module_classes)

    steps = _cluster_by_deps(module_classes, orchestrator, class_deps)

    for step in steps:
        step['representative'] = _find_representative(
            step['classes'], orchestrator, class_deps
        )

    for step in steps:
        step['responsibility'] = _make_step_name(step['representative'], resps)

    for step in steps:
        step['should_expand'] = _should_expand(step, class_deps, resps)

    steps = _sort_steps_by_call_order(steps, orchestrator, class_deps)

    for step in steps:
        step['classes'] = [c for c in step['classes'] if c != orchestrator]
        step['class_count'] = len(step['classes'])
    steps = [s for s in steps if s['classes']]

    for i, step in enumerate(steps):
        step['step_number'] = i + 1

    return {
        'orchestrator': orchestrator,
        'steps': steps,
    }


def _find_orchestrator(class_deps, module_classes):
    """Rule 1: Class with most outgoing deps to other module-internal classes."""
    module_set = set(module_classes)
    best = None
    max_out = -1
    max_total = -1
    for cls in module_classes:
        deps = class_deps.get(cls, [])
        internal = [d for d in deps
                    if d['target'] in module_set
                    and d['target'] != cls
                    and not d.get('target_is_external', False)]
        total = len(deps)
        if (len(internal) > max_out
                or (len(internal) == max_out and total > max_total)):
            max_out = len(internal)
            max_total = total
            best = cls
    return best


def _cluster_by_deps(module_classes, orchestrator, class_deps):
    """Rule 2: Orchestrator's direct call targets as seeds, absorb by dependency."""
    module_set = set(module_classes)

    orch_deps = class_deps.get(orchestrator, [])
    seeds = []
    for dep in orch_deps:
        target = dep['target']
        if (target in module_set
                and target != orchestrator
                and not dep.get('target_is_external', False)
                and target not in seeds):
            seeds.append(target)

    steps = []
    assigned = {orchestrator}
    seed_set = set(seeds)

    for seed in seeds:
        if seed in assigned:
            continue
        step_classes = {seed}
        assigned.add(seed)

        # Inheritance absorption (Lv-5)
        for cls in module_classes:
            if cls in assigned:
                continue
            for dep in class_deps.get(cls, []):
                if dep['target'] == seed and dep.get('level') == 'Lv-5':
                    step_classes.add(cls)
                    break
            else:
                for dep in class_deps.get(seed, []):
                    if dep['target'] == cls and dep.get('level') == 'Lv-5':
                        step_classes.add(cls)
                        break

        # Composition absorption (Lv-4)
        for cls in module_classes:
            if cls in assigned or cls in step_classes or cls in seed_set:
                continue
            for dep in class_deps.get(seed, []):
                if dep['target'] == cls and dep.get('level') == 'Lv-4':
                    step_classes.add(cls)
                    break
            else:
                for dep in class_deps.get(cls, []):
                    if dep['target'] == seed and dep.get('level') == 'Lv-4':
                        step_classes.add(cls)
                        break

        assigned.update(step_classes)
        steps.append({
            'seed': seed,
            'classes': sorted(step_classes),
            'class_count': len(step_classes),
        })

    # Remaining classes
    remaining = [c for c in module_classes if c not in assigned]
    for cls in remaining:
        cls_targets = {d['target'] for d in class_deps.get(cls, [])}
        cls_dependents = set()
        for src, deps in class_deps.items():
            for d in deps:
                if d['target'] == cls:
                    cls_dependents.add(src)

        step_scores = []
        for step in steps:
            step_set = set(step['classes'])
            score = len(cls_targets & step_set) + len(cls_dependents & step_set)
            if score > 0:
                step_scores.append((step, score))

        if len(step_scores) >= 2:
            steps.append({
                'seed': cls,
                'classes': [cls],
                'class_count': 1,
            })
        elif step_scores:
            best_step = max(step_scores, key=lambda x: x[1])[0]
            best_step['classes'].append(cls)
            best_step['classes'].sort()
            best_step['class_count'] = len(best_step['classes'])
        else:
            steps.append({
                'seed': cls,
                'classes': [cls],
                'class_count': 1,
            })

    return steps


def _find_representative(step_classes, orchestrator, class_deps):
    """Rule 3: Find representative class (dependency-based, not naming)."""
    if not step_classes:
        return ''
    if len(step_classes) == 1:
        return step_classes[0]

    orch_targets = {d['target'] for d in class_deps.get(orchestrator, [])}
    entries = [c for c in step_classes if c in orch_targets]
    if len(entries) == 1:
        return entries[0]

    if len(entries) > 1:
        best, max_score = None, -1
        for c in entries:
            score = 0
            for other in step_classes:
                if other == c:
                    continue
                for dep in class_deps.get(other, []):
                    if dep['target'] == c:
                        score += 1
                        if dep.get('level') == 'Lv-5':
                            score += 5
            if score > max_score:
                max_score = score
                best = c
        if best:
            return best

    candidates = entries if entries else step_classes
    step_set = set(step_classes)
    best = max(candidates, key=lambda c: len([
        d for d in class_deps.get(c, [])
        if d['target'] in step_set and d['target'] != c
    ]), default=step_classes[0])
    return best


def _make_step_name(representative, resps):
    """Extract short step name from representative's ideal_responsibility."""
    resp_map = {r['class_name']: r for r in (resps or [])}
    ideal = ''
    r = resp_map.get(representative)
    if r:
        try:
            ideal = r['ideal_responsibility'] or ''
        except (KeyError, IndexError):
            pass

    if not ideal:
        return representative

    for prefix in ['This class should ', 'This class ', 'Should ',
                    'Responsible for ', 'Manages ', 'Handles ',
                    'Orchestrates ', 'Represents ']:
        if ideal.startswith(prefix):
            ideal = ideal[len(prefix):]
            break

    ideal = ideal[0].upper() + ideal[1:] if ideal else representative

    for sep in '.,:;':
        if sep in ideal:
            ideal = ideal[:ideal.index(sep)]
            break
    if len(ideal) > 50:
        cut = ideal[:47].rsplit(' ', 1)[0]
        ideal = cut + '...'

    return ideal


def _should_expand(step, class_deps, resps):
    """Rule 4: >=3 classes in step AND design issues -> True."""
    classes = step.get('classes', [])
    if len(classes) < 3:
        return False

    resp_map = {r['class_name']: r for r in resps}
    class_set = set(classes)

    for cls in classes:
        r = resp_map.get(cls)
        if r:
            raw = ''
            try:
                raw = r['actual_responsibilities'] or ''
            except (KeyError, IndexError):
                pass
            n = len([x for x in raw.split('|') if x.strip()]) if raw else 0
            if n >= 5:
                return True

    for cls in classes:
        for dep in class_deps.get(cls, []):
            if (dep['target'] in class_set
                    and dep['target'] != cls
                    and dep.get('level', '') not in ('Lv-0', 'Lv-5')):
                return True

    return False


def _sort_steps_by_call_order(steps, orchestrator, class_deps):
    """Sort steps by orchestrator's call order to representative class."""
    orch_targets = [d['target'] for d in class_deps.get(orchestrator, [])]

    def sort_key(step):
        rep = step.get('representative', '')
        if rep in orch_targets:
            return orch_targets.index(rep)
        return 999
    return sorted(steps, key=sort_key)


def _render_workflow_diagram(workflow, resps, tasks):
    """Render Layer 1 vertical flowchart + expand cards + details table + summary bar."""
    orchestrator = workflow.get('orchestrator', '')
    steps = workflow.get('steps', [])
    resp_map = {r['class_name']: r for r in (resps or [])}
    all_task_names = {t['class_name'] for t in (tasks or [])}

    lines = ['graph TD']

    if orchestrator:
        orch_id = _mermaid_id(orchestrator)
        lines.append(f'    {orch_id}(["{_mermaid_label(orchestrator)}"])')

    prev_id = _mermaid_id(orchestrator) if orchestrator else None

    for step in steps:
        sid = f"S{step['step_number']}"
        rep = step['representative']
        resp_name = step.get('responsibility', '')
        count = step['class_count']
        expand = step.get('should_expand', False)

        label_parts = [f"<b>Step {step['step_number']}: {_mermaid_label(resp_name)}</b>"]
        label_parts.append(f'{_mermaid_label(rep)}')
        if expand:
            label_parts.append(f"<i>\u26a0 {count} classes \u2014 detail below</i>")
        else:
            label_parts.append(f"<i>({count} classes)</i>")

        label = '<br/>'.join(label_parts)
        lines.append(f'    {sid}["{label}"]')

        if prev_id:
            lines.append(f'    {prev_id} --> {sid}')
        prev_id = sid

    if orchestrator:
        lines.append(
            f'    style {_mermaid_id(orchestrator)} fill:#1f6feb,stroke:#1f6feb,color:#fff')
    for step in steps:
        sid = f"S{step['step_number']}"
        if step.get('should_expand'):
            lines.append(
                f'    style {sid} fill:#fef3c7,stroke:#d97706,stroke-width:3px,color:#1a1a1a')
        else:
            lines.append(
                f'    style {sid} fill:#161b22,stroke:#30363d,color:#c9d1d9')

    mermaid_text = '\n'.join(lines)

    # Expand detail cards
    expand_html = ''
    for step in steps:
        if not step.get('should_expand'):
            continue
        rep = step['representative']
        rows = ''
        for cls in step['classes']:
            is_rep = cls == rep
            r = resp_map.get(cls, {})
            ideal = ''
            try:
                ideal = r.get('ideal_responsibility', '') or ''
            except (AttributeError, KeyError):
                pass
            name_html = f"<strong>{_esc(cls)} \u2605</strong>" if is_rep else _esc(cls)
            rows += f"""\
        <tr>
          <td>{name_html}</td>
          <td style="color:var(--muted);font-size:0.8rem">{_esc(ideal[:80])}</td>
        </tr>"""
        expand_html += f"""\
  <div class="card">
    <h3>Step {step['step_number']} Detail: {_esc(step.get('responsibility', ''))} ({step['class_count']} classes)</h3>
    <table>
      <tr><th>Class</th><th>Ideal Responsibility</th></tr>
      {rows}
    </table>
  </div>"""

    # Details table
    table_rows = ''
    for step in steps:
        rep = step['representative']
        all_classes_text = ', '.join(_esc(c) for c in step['classes'])
        expand_flag = f"\u26a0 Yes ({step['class_count']} classes)" if step.get('should_expand') else 'No'
        ideal = ''
        r = resp_map.get(rep, {})
        try:
            ideal = r.get('ideal_responsibility', '') or ''
        except (AttributeError, KeyError):
            pass

        table_rows += f"""\
      <tr>
        <td>Step {step['step_number']}</td>
        <td>{_esc(step.get('responsibility', ''))}</td>
        <td><strong>{_esc(rep)}</strong></td>
        <td>{all_classes_text}</td>
        <td>{expand_flag}</td>
        <td style="color:var(--muted);font-size:0.8rem">{_esc(ideal[:80])}</td>
      </tr>"""

    # Summary bar
    total_groups = len(steps)
    total_classes = len(all_task_names)
    expand_count = sum(1 for s in steps if s.get('should_expand'))

    summary_html = f"""\
  <div class="summary-bar">
    <span><strong>{_esc(orchestrator or '?')}</strong> orchestrates</span>
    <span><strong>{total_groups}</strong> responsibility groups</span>
    <span><strong>{total_classes}</strong> classes</span>
    <span class="{'health-bad' if expand_count else 'health-good'}"><strong>{expand_count}</strong> expanded (design issues)</span>
  </div>"""

    return f"""\
<section>
  <h2>Layer 1: Module Workflow</h2>
  <p style="color:var(--muted)">Orchestrator = most outgoing dependencies (code). Steps = AI responsibility_tags clustering. Representative = dependency-based entry point.</p>
  <div class="card">
    <div class="mermaid">
{mermaid_text}
    </div>
  </div>
{expand_html}
  <div class="card">
    <h3>Workflow Details</h3>
    <table>
      <tr><th>Step</th><th>Responsibility</th><th>Representative</th><th>All Classes</th><th>Expand</th><th>Ideal Responsibility</th></tr>
      {table_rows}
    </table>
  </div>
  {summary_html}
</section>"""


def _mermaid_label(text):
    """Escape text for Mermaid node labels — safe for graph TD."""
    text = str(text)
    text = text.replace('"', "'").replace('`', "'")
    safe_tags = {'<br/>', '<b>', '</b>', '<i>', '</i>'}
    parts = re.split(r'(</?(?:br/|b|i)>)', text)
    result = []
    for part in parts:
        if part in safe_tags:
            result.append(part)
        else:
            result.append(part.replace('<', '(').replace('>', ')'))
    return ''.join(result)
