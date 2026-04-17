"""Shared helpers used across all report layers."""


def _esc(text):
    """HTML escape."""
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def _mermaid_id(name):
    """Convert class name to safe Mermaid node ID."""
    return ''.join(c if c.isalnum() or c == '_' else '_' for c in name)


def _judge_dependency(source_class, dep, orchestrator_class, all_local):
    """Deterministic rules to judge a dependency's health. Returns (health, reason).

    Based on dependency pyramid analysis:
    - Lv-0: always healthy (weakest)
    - Lv-2: always healthy (interface, recommended)
    - Lv-5: inheritance — ok within module
    - Lv-4: composition — ok within module
    - Lv-1/3: check for orchestrator bypass
    """
    target = dep['target']
    level = dep['level']
    is_ext = dep.get('target_is_external', target not in all_local)

    # External references always healthy
    if is_ext:
        return 'good', 'External reference'

    # Lv-0 always healthy (weakest dep)
    if level == 'Lv-0':
        return 'good', 'Lightweight include dependency'

    # Lv-5 always healthy within module
    if level == 'Lv-5':
        return 'good', 'Inheritance relationship'

    # Lv-4 always healthy within module
    if level == 'Lv-4':
        return 'good', 'Composition (lifecycle-bound)'

    # Lv-2 always healthy (recommended: DIP core mechanism)
    if level == 'Lv-2':
        return 'good', 'Interface realization (recommended)'

    # Lv-1 / Lv-3: check bypass
    if level in ('Lv-1', 'Lv-3'):
        source_is_orch = (source_class == orchestrator_class)
        if source_is_orch:
            dep_type = 'Association' if level == 'Lv-1' else 'Aggregation'
            return 'good', f'{dep_type} via orchestrator'

        dep_type = 'association' if level == 'Lv-1' else 'aggregation'
        return 'warn', f'Direct {dep_type} between non-orchestrators (bypass?)'

    return 'good', 'OK'


def _is_real_bypass(source, target, orchestrator, dep_level,
                    incoming_count=None):
    """Determine if a non-orchestrator dependency is a real orchestrator bypass.

    Excludes (not bypass):
    - All orchestrator dependencies
    - Inheritance (Lv-5) — compile-time binding, cannot route through orchestrator
    - Lv-0 include-only — too weak, not bypass
    """
    if source == orchestrator:
        return False
    if dep_level == 'Lv-5':
        return False
    if dep_level == 'Lv-0':
        return False
    return True


_LEVEL_NUM = {'Lv-0': 0, 'Lv-1': 1, 'Lv-2': 2, 'Lv-3': 3, 'Lv-4': 4, 'Lv-5': 5}
