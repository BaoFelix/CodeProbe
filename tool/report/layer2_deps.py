"""Layer 2: Dependency Health Diagnosis."""
from .helpers import _esc, _mermaid_id, _judge_dependency


def _section_dependency_health(all_deps, resps, module_info):
    """Layer 2: Select lines — diagnose each dependency's health from DB data.

    Principle: show all relationships, zero filtering. classDiagram draws every dependency.
    """
    if not all_deps:
        return '<section><h2>Layer 2: Dependency Health</h2><p>No dependency data. Run <code>analyze</code> first.</p></section>'

    orchestrator_class = module_info['orchestrator'] if module_info else ''

    all_local_classes = set()
    all_ext = set()
    for d in all_deps:
        all_local_classes.add(d['source_class'])
        if d['target_is_external']:
            all_ext.add(d['target_class'])
        else:
            all_local_classes.add(d['target_class'])

    # Health judgment for each dep
    judged_deps = []
    for d in all_deps:
        dep_dict = {
            'target': d['target_class'],
            'level': d['level_name'],
            'source': d['source_evidence'],
            'target_is_external': bool(d['target_is_external']),
        }
        health, reason = _judge_dependency(
            d['source_class'], dep_dict,
            orchestrator_class, all_local_classes)
        judged_deps.append((d['source_class'], dep_dict, health, reason))

    # Mermaid classDiagram
    cd_lines = ['classDiagram']

    for cn in sorted(all_local_classes):
        safe = _mermaid_id(cn)
        cd_lines.append(f'    class {safe}')

    for ext_cn in sorted(all_ext):
        safe = _mermaid_id(ext_cn)
        cd_lines.append(f'    class {safe}["{ext_cn}\\n(external)"]')
        cd_lines.append(f'    cssClass "{safe}" external')

    for cn, dep, health, reason in judged_deps:
        target = dep['target']
        level = dep['level']
        src_id = _mermaid_id(cn)
        tgt_id = _mermaid_id(target)
        arrow, label_text, reverse = _dep_mermaid_arrow(level, health)
        if reverse:
            cd_lines.append(f'    {tgt_id} {arrow} {src_id} : {label_text}')
        else:
            cd_lines.append(f'    {src_id} {arrow} {tgt_id} : {label_text}')

    mermaid_text = '\n'.join(cd_lines)

    _verify_all_deps_rendered(judged_deps, mermaid_text)

    dep_rows = _render_dep_rows(
        sorted(judged_deps, key=lambda x: _health_sort_key(x[2])))
    if not dep_rows:
        dep_rows = '<tr><td colspan="5">No dependencies detected</td></tr>'

    legend_html = """\
  <div class="card">
    <div class="legend">
      <div class="legend-item"><span class="dep-level lv0">Lv-0</span> Dependency (include only)</div>
      <div class="legend-item"><span class="dep-level lv1">Lv-1</span> Association (params)</div>
      <div class="legend-item"><span class="dep-level lv2">Lv-2</span> Realization (interface)</div>
      <div class="legend-item"><span class="dep-level lv3">Lv-3</span> Aggregation (ptr)</div>
      <div class="legend-item"><span class="dep-level lv4">Lv-4</span> Composition (value)</div>
      <div class="legend-item"><span class="dep-level lv5">Lv-5</span> Inheritance</div>
    </div>
    <div class="legend" style="margin-top:0.3rem">
      <div class="legend-item"><span class="health-good">&#x2705;</span> Healthy</div>
      <div class="legend-item"><span class="health-warn">&#x26A0;&#xFE0F;</span> Over-strong</div>
      <div class="legend-item"><span class="health-bad">&#x274C;</span> Violation</div>
    </div>
  </div>"""

    return f"""\
<section>
  <h2>Layer 2: Dependency Health Diagnosis</h2>
  <p style="color:var(--muted)">Dependency Pyramid (Lv-0~Lv-5): use the weakest level that satisfies the need. Dashed grey boxes = external classes. Showing all <strong>{len(judged_deps)}</strong> dependencies.</p>
  {legend_html}
  <div class="card">
    <div class="mermaid">
{mermaid_text}
    </div>
  </div>
  <div class="card">
    <h3>Dependency Details ({len(judged_deps)} total)</h3>
    <table class="dep-table">
      <tr><th>Source</th><th>Target</th><th>Level</th><th>Health</th><th>Reason</th></tr>
      {dep_rows}
    </table>
  </div>
</section>"""


def _dep_mermaid_arrow(level, health):
    """Return (arrow, label, reverse) for Mermaid classDiagram."""
    if level == 'Lv-5':
        return '<|--', level, True
    if level == 'Lv-4':
        return '*--', level, False
    if level == 'Lv-3':
        return 'o--', level, False
    if level == 'Lv-2':
        return '<|..', level, True
    if level == 'Lv-1':
        return '-->', level, False
    return '..>', level, False


def _render_dep_rows(deps_list):
    """Render dependency table <tr> rows."""
    rows = ''
    for cn, dep, health, reason in deps_list:
        level = dep['level']
        target = dep['target']
        is_ext = dep.get('target_is_external', False)
        lv_css = level.lower().replace('-', '')
        health_css = {'good': 'health-good', 'warn': 'health-warn',
                      'bad': 'health-bad'}[health]
        health_icon = {'good': '&#x2705;', 'warn': '&#x26A0;&#xFE0F;',
                       'bad': '&#x274C;'}[health]
        target_display = (
            f'{_esc(target)} <small style="color:var(--muted)">(ext)</small>'
            if is_ext else _esc(target))
        rows += f"""\
      <tr>
        <td>{_esc(cn)}</td>
        <td>{target_display}</td>
        <td><span class="dep-level {lv_css}">{level}</span></td>
        <td><span class="{health_css}">{health_icon}</span></td>
        <td style="color:var(--muted)">{_esc(reason)}</td>
      </tr>"""
    return rows


def _verify_all_deps_rendered(all_deps, mermaid_text):
    """Verify all dependencies have corresponding edges in Mermaid diagram."""
    missing = []
    for cn, dep, health, reason in all_deps:
        src_id = _mermaid_id(cn)
        tgt_id = _mermaid_id(dep['target'])
        if src_id not in mermaid_text or tgt_id not in mermaid_text:
            missing.append(f"{cn} -> {dep['target']} ({dep['level']})")
            continue
        found = False
        for line in mermaid_text.split('\n'):
            if src_id in line and tgt_id in line:
                found = True
                break
        if not found:
            missing.append(f"{cn} -> {dep['target']} ({dep['level']})")
    if missing:
        print(f"  [verify] MISSING {len(missing)} deps in diagram: {missing}")
    else:
        print(f"  [verify] OK: All {len(all_deps)} dependencies rendered in diagram")
    return missing


def _health_sort_key(health):
    """Sort key: bad < warn < good."""
    return {'bad': 0, 'warn': 1, 'good': 2}.get(health, 3)
