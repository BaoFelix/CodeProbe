"""Layer 3: Action Items — sin-based diagnostics + dependency issues."""
import re
from collections import defaultdict

from .helpers import _esc, _judge_dependency, _is_real_bypass, _LEVEL_NUM


SEVEN_SINS_URL = (
    'https://en.wikipedia.org/wiki/Anti-pattern#Object-oriented_design'
)


def _section_action_items(resps, all_deps, module_info):
    """Layer 3: Sin-based action items — grouped by class.

    Sources:
      1. sin_diagnosis from DB -> class-level pain points (Seven Sins)
      2. Dependency analysis from DB -> module-level issues (bypass, circular, missing interface)
    Weight: 3 dimensions — boundary violation + impact scope + structural role.
    Grouped by class, sorted by max weight descending. No P-labels.
    """
    orchestrator_class = module_info['orchestrator'] if module_info else ''
    is_orchestrator = {}
    if orchestrator_class:
        is_orchestrator[orchestrator_class] = True

    # Build flat deps and class sets
    all_local = set()
    flat_deps = {}
    for d in (all_deps or []):
        src = d['source_class']
        all_local.add(src)
        if not d['target_is_external']:
            all_local.add(d['target_class'])
        flat_deps.setdefault(src, []).append({
            'target': d['target_class'],
            'level': d['level_name'],
            'source': d['source_evidence'],
            'target_is_external': bool(d['target_is_external']),
        })

    resp_map = {r['class_name']: r for r in (resps or [])}

    # Pre-compute incoming/outgoing dependency counts
    incoming_count = {}
    incoming_sources = {}
    outgoing_count = {}
    for cn, dep_list in flat_deps.items():
        targets = set()
        for dep in dep_list:
            tgt = dep['target']
            if not dep.get('target_is_external', False):
                targets.add(tgt)
                incoming_sources.setdefault(tgt, set()).add(cn)
        outgoing_count[cn] = len(targets)
    for tgt, srcs in incoming_sources.items():
        incoming_count[tgt] = len(srcs)

    # items: (cn, sin_name, severity_css, description, suggestion, weight, breakdown)
    items = []

    # === Source 1: Class-level sins from DB sin_diagnosis ===
    for cn, r in resp_map.items():
        sin_diag = r['sin_diagnosis'] or ''
        if not sin_diag:
            continue
        parsed_sins = _parse_sin_diagnosis(sin_diag)

        # Data class: filter out irrelevant sins
        if _is_data_class(cn, flat_deps, incoming_count, outgoing_count, resp_map):
            parsed_sins = [
                (sn, sev, ev) for sn, sev, ev in parsed_sins
                if sn.replace(' ', '').lower() in ('hiddenstate', 'knowledgeleakage')
            ]
            if not parsed_sins:
                continue

        for sin_name, severity, evidence in parsed_sins:
            w_boundary = _SIN_BOUNDARY_WEIGHT.get(sin_name, 20)
            w_impact = _calc_impact_scope(cn, incoming_count, outgoing_count)
            w_role = _calc_structural_role(cn, is_orchestrator, incoming_count)
            weight = w_boundary + w_impact + w_role
            sev_css = _severity_css(severity)
            suggestion = _sin_suggestion(sin_name)
            breakdown = (f'{sin_name} = {w_boundary} boundary'
                         f' + {w_impact} impact + {w_role} role')
            items.append((cn, sin_name, sev_css, evidence, suggestion,
                          weight, breakdown))

    # === Source 2: Module-level dependency issues ===

    # 2a: Orchestrator bypass
    bypass_raw = {}
    bypass_max_lv = {}
    for cn, dep_list in flat_deps.items():
        for dep in dep_list:
            target = dep['target']
            level = dep['level']
            health, reason = _judge_dependency(
                cn, dep, orchestrator_class, all_local)
            if health == 'warn' and 'bypass' in reason.lower():
                if _is_real_bypass(cn, target, orchestrator_class,
                                   level, incoming_count):
                    bypass_raw.setdefault(cn, []).append((target, level))
                    lv_num = _LEVEL_NUM.get(level, 0)
                    bypass_max_lv[cn] = max(bypass_max_lv.get(cn, 0), lv_num)

    for cn, target_levels in bypass_raw.items():
        max_lv = bypass_max_lv.get(cn, 1)
        w_boundary = 40 + (10 if max_lv >= 3 else 0)
        w_impact = _calc_impact_scope(cn, incoming_count, outgoing_count)
        w_role = _calc_structural_role(cn, is_orchestrator, incoming_count)
        weight = w_boundary + w_impact + w_role
        if len(target_levels) == 1:
            tgt, lv = target_levels[0]
            evidence = f'Direct {lv} to {tgt}, bypassing orchestrator'
        else:
            names = ', '.join(f'{t} ({l})' for t, l in target_levels)
            evidence = f'Bypasses orchestrator to {len(target_levels)} classes: {names}'
        breakdown = (f'bypass = {w_boundary} boundary'
                     f' + {w_impact} impact + {w_role} role')
        items.append((cn, 'Orchestrator Bypass', 'p-warn', evidence,
                       'Route through orchestrator or extract interface',
                       weight, breakdown))

    # 2b: Missing interface (>=3 classes depend on same concrete) — skip data classes
    for target, count in incoming_count.items():
        if count >= 3 and target in all_local:
            if _is_data_class(target, flat_deps, incoming_count, outgoing_count, resp_map):
                continue
            is_iface = (target.startswith('I') and len(target) > 1
                        and target[1].isupper())
            if not is_iface:
                w_boundary = count * 10
                w_impact = _calc_impact_scope(target, incoming_count, outgoing_count)
                w_role = _calc_structural_role(target, is_orchestrator, incoming_count)
                weight = w_boundary + w_impact + w_role
                evidence = f'{count} classes depend on concrete {target}'
                breakdown = (f'{count} deps \u00d7 10 = {w_boundary} boundary'
                             f' + {w_impact} impact + {w_role} role')
                items.append((target, 'Abstraction Absence', 'p-warn', evidence,
                               'Extract interface (DIP)',
                               weight, breakdown))

    # Group by class and sort
    grouped = _group_items_by_class(items)

    if not grouped:
        return f"""\
<section>
  <h2>Layer 3: Pain Points &amp; Proposals &mdash; <a href="{SEVEN_SINS_URL}" target="_blank" style="color:var(--primary);font-size:0.75em;font-weight:400;text-decoration:none">&#x1F4D6; Seven Sins Reference &#x2197;</a></h2>
  <p style="color:var(--muted)">Pain points grouped by class, sorted by &#x26A1; refactoring priority.</p>
  <div class="card"><p class="health-good">No issues detected. Module design looks healthy.</p></div>
</section>"""

    items_html = ''
    for cn, sins, max_weight in grouped:
        if max_weight >= 100:
            wcss = 'score-bad'
        elif max_weight >= 50:
            wcss = 'score-warn'
        else:
            wcss = 'score-good'
        merged_suggestion = _merge_suggestions(sins)

        # Sin sub-items
        sin_lines = ''
        for sin_name, sev_css, evidence, suggestion, weight, breakdown in sins:
            sin_lines += (
                f'<div class="sin-item">'
                f'<span class="priority {sev_css}">{_esc(sin_name)}</span>'
                f' <span style="color:var(--muted);font-size:0.8rem" '
                f'title="{_esc(breakdown)}">&#x26A1;{weight}</span>'
                f' &mdash; {_esc(evidence)}'
                f'</div>\n')

        items_html += f"""\
  <div class="action-item">
    <span class="{wcss}" style="font-weight:700;font-size:0.85rem">&#x26A1; {max_weight}</span>
    <span class="class-name" style="margin-left:0.5rem">{_esc(cn)}</span>
    <span style="color:var(--muted);font-size:0.8rem">({len(sins)} issue{'s' if len(sins) != 1 else ''})</span>
    {sin_lines}
    <div class="action-desc" style="color:var(--primary)">&#x2192; {_esc(merged_suggestion)}</div>
  </div>\n"""

    return f"""\
<section>
  <h2>Layer 3: Pain Points &amp; Proposals &mdash; <a href="{SEVEN_SINS_URL}" target="_blank" style="color:var(--primary);font-size:0.75em;font-weight:400;text-decoration:none">&#x1F4D6; Seven Sins Reference &#x2197;</a></h2>
  <p style="color:var(--muted)">Pain points grouped by class, sorted by &#x26A1; refactoring priority.</p>
  {items_html}
</section>"""


def _group_items_by_class(items):
    """Group flat items by class_name. Returns [(cn, sins, max_weight), ...] sorted by max_weight desc.

    sins = [(sin_name, sev_css, evidence, suggestion, weight, breakdown), ...]
    """
    by_class = defaultdict(list)
    for cn, sin_name, sev_css, evidence, suggestion, weight, breakdown in items:
        by_class[cn].append((sin_name, sev_css, evidence, suggestion, weight, breakdown))

    grouped = []
    for cn, sins in by_class.items():
        max_weight = max(w for _, _, _, _, w, _ in sins)
        # Sort sins within class by weight descending
        sins.sort(key=lambda x: -x[4])
        grouped.append((cn, sins, max_weight))

    grouped.sort(key=lambda x: -x[2])
    return grouped


def _merge_suggestions(sins):
    """Merge suggestions from multiple sins into a combined string (max 2 unique)."""
    seen = []
    for _, _, _, suggestion, _, _ in sins:
        if suggestion not in seen:
            seen.append(suggestion)
        if len(seen) >= 2:
            break
    return '; '.join(seen)


# --- Sin diagnosis parser and weight tables ---

_SIN_BOUNDARY_WEIGHT = {
    'God Class': 50, 'GodClass': 50,
    'Inheritance Hell': 40, 'InheritanceHell': 40,
    'Abstraction Absence': 30, 'AbstractionAbsence': 30,
    'Feature Envy': 25, 'FeatureEnvy': 25,
    'Circular Entanglement': 40, 'CircularEntanglement': 40,
    'Hidden State': 35, 'HiddenState': 35,
    'Knowledge Leakage': 20, 'KnowledgeLeakage': 20,
    'Orchestrator Bypass': 40, 'OrchestratorBypass': 40,
}

# Regex patterns for different AI output formats
_SIN_PATTERNS = [
    # Format 1a: Sin1(SinName, severity, evidence) — numbered prefix
    re.compile(r'Sin\d+\(([A-Za-z ]+?),\s*([^,)]+),?\s*(.*?)\)\s*$'),
    # Format 1b: SinName(severity, evidence)
    re.compile(r'([A-Za-z ]+?)\s*\(([^,)]+),?\s*(.*?)\)\s*$'),
    # Format 2: SinName = severity — evidence
    re.compile(r'([A-Za-z ]+?)\s*=\s*([^—\-]+?)\s*[—\-]\s*(.*)$'),
    # Format 3: SinName: severity, evidence
    re.compile(r'([A-Za-z ]+?):\s*([^,]+),?\s*(.*)$'),
]

_CLEAN_PATTERNS = re.compile(
    r'^(clean|n/?a|none|no\s+sins?|healthy|—|\*\*|no\s+issues?)$',
    re.IGNORECASE)


def _parse_sin_diagnosis(sin_diag):
    """Parse sin_diagnosis field from DB. Supports multiple AI output formats.

    Filters out Clean/N/A/None entries. Returns [(sin_name, severity, evidence), ...].
    """
    results = []
    for part in sin_diag.split('|'):
        part = part.strip()
        if not part:
            continue
        # Filter known clean/empty markers
        if _CLEAN_PATTERNS.match(part):
            continue

        matched = False
        for pattern in _SIN_PATTERNS:
            m = pattern.match(part)
            if m:
                sin_name = m.group(1).strip()
                severity = m.group(2).strip()
                evidence = m.group(3).strip()
                # Skip if sin_name itself looks like a clean marker
                if _CLEAN_PATTERNS.match(sin_name):
                    matched = True
                    break
                results.append((sin_name, severity, evidence))
                matched = True
                break

        if not matched:
            # Fallback: treat whole part as sin name
            if not _CLEAN_PATTERNS.match(part):
                results.append((part, 'unknown', ''))

    return results


def _severity_css(severity):
    """Map severity string to CSS class."""
    sev_lower = severity.lower()
    if '\U0001f534' in severity or 'critical' in sev_lower or 'crit' in sev_lower:
        return 'p-crit'
    if '\U0001f7e1' in severity or 'major' in sev_lower or 'warn' in sev_lower:
        return 'p-warn'
    if '\U0001f7e2' in severity or 'minor' in sev_lower:
        return 'p-info'
    return 'p-warn'


def _sin_suggestion(sin_name):
    """One-line refactoring suggestion per sin type."""
    name = sin_name.replace(' ', '').lower()
    suggestions = {
        'godclass': 'Split by change reason (vertical cut)',
        'inheritancehell': 'Replace inheritance with composition + interface',
        'abstractionabsence': 'Extract interface at dependency boundary (DIP)',
        'featureenvy': 'Move methods to the class they access most',
        'circularentanglement': 'Break cycle with interface or mediator',
        'hiddenstate': 'Make state explicit via parameters or value objects',
        'knowledgeleakage': 'Encapsulate implementation details behind interface',
        'orchestratorbypass': 'Route through orchestrator or extract interface',
    }
    return suggestions.get(name, 'Review and refactor')


def _calc_impact_scope(cn, incoming_count, outgoing_count):
    """Impact scope = incoming x 8 + outgoing x 5."""
    return incoming_count.get(cn, 0) * 8 + outgoing_count.get(cn, 0) * 5


def _calc_structural_role(cn, is_orchestrator, incoming_count):
    """Structural role bonus: orchestrator +15, hub (>=3 dependents) +10."""
    bonus = 0
    if is_orchestrator.get(cn):
        bonus += 15
    if incoming_count.get(cn, 0) >= 3:
        bonus += 10
    return bonus


def _is_data_class(cn, flat_deps, incoming_count, outgoing_count, resp_map):
    """Detect pure data classes (not functional classes).

    Data class traits: many dependents (high incoming), few outgoing deps,
    and AI's ideal_responsibility mentions data-related keywords.
    """
    inc = incoming_count.get(cn, 0)
    out = outgoing_count.get(cn, 0)

    # Structural: high incoming, low outgoing
    is_structural_data = inc >= 3 and out <= 2

    # Semantic: AI considers its responsibility data-related
    resp = resp_map.get(cn)
    if not resp:
        return False
    ideal = (resp['ideal_responsibility'] or '').lower()
    data_keywords = ['data', 'store', 'structure', 'hold', 'represent',
                     'container', 'record']
    is_semantic_data = any(kw in ideal for kw in data_keywords)

    return is_structural_data and is_semantic_data
