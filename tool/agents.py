"""
agents.py — Agent definitions
═══════════════════════════════════════
AI concept: Multi-Agent (collaborative agents)
Key insight:
  - Each Agent does one thing → "Keep them focused, context is your budget"
  - Agent inputs and outputs are explicit
  - Agents don't communicate directly → they collaborate via DB (shared memory)
  - Pipeline (Coordinator) decides who to call and in what order
═══════════════════════════════════════
"""
import json
import re
from pathlib import Path
from datetime import datetime

from .config import OUTPUTS_DIR, MAX_LINES_DIRECT, DEFAULT_MODULE
from .db import DBManager
from .reader import FileReader
from .prompts import PromptBuilder
from .llm import LLMClient


class BaseAgent:
    """
    Agent base class — all agents share LLM and DB.

    Each agent's pattern:
    1. Read input from DB (task info)
    2. Execute its specialized operation
    3. Write results back to DB
    """

    def __init__(self, llm, db, reader=None, prompts=None):
        self.llm = llm
        self.db = db
        self.reader = reader
        self.prompts = prompts

    def run(self, *args, **kwargs):
        raise NotImplementedError

    def _parse_fields(self, response, field_patterns):
        """Generic defensive field parser.

        field_patterns: dict of {key: regex_pattern_with_one_group}
        Returns dict with matched values (empty string if not found).
        """
        result = {}
        for key, pattern in field_patterns.items():
            match = re.search(pattern, response, re.IGNORECASE)
            result[key] = match.group(1).strip() if match else ''
        return result


class ScannerAgent(BaseAgent):
    """
    Agent-A: Scan source directory, extract structure + dependencies, store to DB.

    Input: source directory path
    Output: classes, dependencies, module_info — all written to DB
    No LLM needed — pure file operations + regex extraction.
    Single scan via reader.build_module_map() — no redundant passes.
    """

    def run(self, directory=None):
        """Scan directory, store classes + deps + module info. Returns class count."""
        print(f"\n  [ScannerAgent] Scanning source...")

        # 1. One-shot scan: build_module_map extracts all structure + dependencies
        module_map = self.reader.build_module_map(directory)

        if not module_map:
            print(f"  ⚠ No C++ classes found.")
            return 0

        # 2. Identify orchestrator
        orchestrator = self.reader.find_orchestrator(module_map)

        # 3. Collect all classes and dependencies
        all_classes = []
        all_deps = []
        total_classes = 0

        for entry in module_map:
            structure = entry.get('structure', {})
            if not structure:
                class_names = entry.get('classes', [])
                methods = entry.get('methods', [])
                member_vars = entry.get('member_vars', [])
            else:
                class_names = structure.get('classes', [])
                methods = structure.get('methods', [])
                member_vars = structure.get('member_vars', [])

            header_path = entry.get('file', '')
            impl_path = None
            if header_path:
                from .config import IMPL_EXTS
                for ext in IMPL_EXTS:
                    candidate = Path(header_path).with_suffix(ext)
                    if candidate.exists():
                        impl_path = str(candidate)
                        break

            for cn in class_names:
                all_classes.append({
                    'class_name': cn,
                    'header_path': header_path,
                    'impl_path': impl_path,
                    'method_count': len(methods),
                    'member_count': len(member_vars),
                    'line_count': entry.get('lines', 0),
                    'is_orchestrator': 1 if cn == orchestrator else 0,
                })
                impl_info = f" + {Path(impl_path).name}" if impl_path else ""
                deps = entry.get('dependencies', {}).get(cn, [])
                deps_info = ""
                if deps:
                    dep_names = [d['target'] for d in deps]
                    deps_info = f" \u2192 {', '.join(dep_names)}"
                print(f"    + {cn:<30} ({Path(header_path).name}{impl_info}){deps_info}")
                total_classes += 1

            for cn, deps in entry.get('dependencies', {}).items():
                for dep in deps:
                    level_str = dep['level']
                    level_num = int(level_str.split('-')[1])
                    all_deps.append({
                        'source_class': cn,
                        'target_class': dep['target'],
                        'level': level_num,
                        'level_name': level_str,
                        'source_evidence': dep.get('source', ''),
                        'target_is_external': dep.get('target_is_external', False),
                    })

        # 4. Batch write to DB (3 operations instead of N)
        self.db.save_classes_batch(all_classes)
        self.db.save_dependencies(all_deps)

        # 5. Store module info
        module_name = 'default'
        scan_dir = Path(directory) if directory else self.reader.source_root
        self.db.save_module_info(
            module_name=module_name,
            directory=str(scan_dir),
            orchestrator=orchestrator,
            file_count=len(module_map),
            class_count=total_classes,
        )

        print(f"\n  ✓ ScannerAgent: registered {total_classes} classes to database")
        if orchestrator:
            print(f"  ✓ Orchestrator: {orchestrator}")
        return total_classes


class ResponsibilityAgent(BaseAgent):
    """
    Agent-E: Single class responsibility analysis — with module context.

    Input: class name + dependency/module context (from DB)
    Flow:
      1. Build context from DB (dependencies, module info, other classes)
      2. Reader reads target class source
      3. prompts.build_responsibility_prompt() generates prompt with context
      4. LLM analysis
      5. Parse → store in responsibility_analysis table
    """

    def run(self, class_name):
        """Responsibility analysis with Seven Sins framework. Returns resp_id or None."""
        print(f"\n  [ResponsibilityAgent] Responsibility analysis: {class_name}")

        # Step 1: Build module context from DB
        module_info = self.db.get_module_info()
        outgoing_deps = self.db.get_dependencies(class_name)
        incoming_deps = self.db.get_dependents_of_class(class_name)
        all_classes = self.db.get_all_tasks()

        context_parts = []
        if module_info:
            context_parts.append(f"Module: {module_info['module_name']}")
            context_parts.append(f"Orchestrator: {module_info['orchestrator'] or '—'}")
            context_parts.append(f"Total classes: {module_info['class_count']}")
        if outgoing_deps:
            dep_lines = [f"  → {d['target_class']} ({d['level_name']} — {d['source_evidence']})"
                         for d in outgoing_deps]
            context_parts.append(f"Outgoing dependencies of {class_name}:\n" + "\n".join(dep_lines))
        if incoming_deps:
            dep_lines = [f"  ← {d['source_class']} ({d['level_name']} — {d['source_evidence']})"
                         for d in incoming_deps]
            context_parts.append(f"Incoming dependencies (who depends on {class_name}):\n" + "\n".join(dep_lines))
        if all_classes:
            names = [c['class_name'] for c in all_classes if c['class_name'] != class_name]
            if names:
                context_parts.append(f"Other classes in module: {', '.join(names)}")
        module_context = "\n".join(context_parts) if context_parts else "No module context available."

        # Step 2: Read source via reader.read_class_source()
        print("    [1/4] Reading source...")
        source_content, header, impl = self.reader.read_class_source(
            class_name, max_impl_lines=MAX_LINES_DIRECT
        )

        if not source_content:
            # Fallback: try reading from DB header_path
            task = self.db.get_task(class_name)
            if task and task['header_path']:
                content, lines = self.reader.read_file(task['header_path'])
                if content:
                    source_content = content

        if not source_content:
            print(f"  ✗ Cannot read source for {class_name}.")
            return None

        # Step 3: Build prompt (with token budget control)
        print("    [2/4] Building responsibility prompt...")
        prompt, tokens_est = self.prompts.build_with_budget(
            self.prompts.build_responsibility_prompt, 'source_content',
            class_name=class_name, source_content=source_content,
            module_context=module_context,
        )
        print(f"    Prompt: ~{tokens_est} tokens")

        # Step 4: Call LLM
        print("    [3/4] Getting AI responsibility analysis...")
        response = self.llm.generate(prompt, tag=f"{class_name}_resp")

        if not response:
            print("    ⚠ No response received.")
            return None

        # Step 5: Parse + store in DB (now includes sin_diagnosis)
        print("    [4/4] Parsing and storing results...")
        parsed = self._parse_fields(response, {
            'actual_responsibilities': r'ACTUAL_RESPONSIBILITIES:\s*(.+)',
            'ideal_responsibility': r'IDEAL_RESPONSIBILITY:\s*(.+)',
            'srp_violations': r'SRP_VIOLATIONS:\s*(.+)',
            'sin_diagnosis': r'SIN_DIAGNOSIS:\s*(.+)',
            'extract_candidates': r'EXTRACT_CANDIDATES:\s*(.+)',
            'responsibility_tags': r'RESPONSIBILITY_TAGS:\s*(.+)',
        })

        resp_id = self.db.save_responsibility(
            class_name=class_name,
            parsed=parsed,
            full_analysis=response,
        )

        print(f"\n  ✓ Responsibility analysis complete: {class_name}")
        print(f"  ✓ Actual responsibilities: {parsed.get('actual_responsibilities', 'N/A')[:80]}")
        print(f"  ✓ Sin diagnosis: {parsed.get('sin_diagnosis', 'N/A')[:80]}")
        return resp_id


class DesignAgent(BaseAgent):
    """
    Agent-F: Design proposal generation — based on responsibility analysis.

    Input: module context + all responsibility analyses + sin diagnoses (from DB)
    Output: concrete refactoring plan, stored in design_proposals table
    """

    def run(self):
        """Generate design proposal from full DB data. Returns design_id or None."""
        print(f"\n  [DesignAgent] Generating design proposal...")

        # Step 1: Read all data from DB
        module_info = self.db.get_module_info()
        all_deps = self.db.get_dependencies()
        all_resps = self.db.get_all_responsibilities()

        if not all_resps:
            print("  ✗ No responsibility analyses. Run analyze first.")
            return None

        # Step 2: Build summaries
        dep_summary = self._summarize_deps(all_deps or [])
        resp_summary = self._summarize_resps(all_resps)
        sin_summary = self._summarize_sins(all_resps)

        print(f"    Responsibility analyses: {len(all_resps)} classes")

        # Step 3: Build prompt (with token budget control)
        print("    [1/3] Building design proposal prompt...")
        prompt, tokens_est = self.prompts.build_with_budget(
            self.prompts.build_design_prompt, 'resp_summary',
            module_info=module_info, dep_summary=dep_summary,
            resp_summary=resp_summary, sin_summary=sin_summary,
        )
        print(f"    Prompt: ~{tokens_est} tokens")

        # Step 4: Call LLM
        print("    [2/3] Getting AI design proposal...")
        response = self.llm.generate(prompt, tag="design_proposal")

        if not response:
            print("    ⚠ No response received.")
            return None

        # Step 5: Parse + store in DB
        print("    [3/3] Parsing and storing results...")
        parsed = self._parse_fields(response, {
            'phase_plan': r'PHASE_PLAN:\s*(.+)',
            'new_classes': r'NEW_CLASSES:\s*(.+)',
            'interfaces': r'INTERFACES:\s*(.+)',
            'effort_total': r'EFFORT_TOTAL:\s*(.+)',
        })

        design_id = self.db.save_design_proposal(
            parsed=parsed,
            full_analysis=response,
        )

        # Save report
        report_file = OUTPUTS_DIR / "design_proposal.md"
        report_file.write_text(
            f"# Design Proposal\n\n"
            f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**Based on**: {len(all_resps)} class responsibility analyses\n\n"
            f"## Phase Plan\n{parsed.get('phase_plan', 'N/A')}\n\n"
            f"## New Classes\n{parsed.get('new_classes', 'N/A')}\n\n"
            f"## Interface Definitions\n{parsed.get('interfaces', 'N/A')}\n\n"
            f"## Full AI Proposal\n\n{response}\n",
            encoding='utf-8'
        )

        print(f"\n  ✓ Design proposal complete (design_id={design_id})")
        print(f"  ✓ Report: {report_file.name}")
        return design_id

    @staticmethod
    def _summarize_deps(deps):
        """Build compact dependency summary from DB records."""
        if not deps:
            return "No dependencies recorded."
        by_source = {}
        for d in deps:
            by_source.setdefault(d['source_class'], []).append(
                f"{d['target_class']} ({d['level_name']})")
        lines = []
        for src, targets in by_source.items():
            lines.append(f"  {src} → {', '.join(targets)}")
        return "\n".join(lines)

    @staticmethod
    def _summarize_resps(resps, max_tokens=3000):
        """Build responsibility summary with severity-aware prioritization.

        For large modules (50+ classes): high-severity classes get detailed entries,
        low-severity classes get one-line summaries. Sorted by sin severity descending.
        """
        def severity_score(resp):
            sins = resp['sin_diagnosis'] or ''
            return sins.count('\U0001f534') * 10 + sins.count('\U0001f7e1') * 3

        sorted_resps = sorted(resps, key=severity_score, reverse=True)

        lines = []
        tokens_used = 0
        detail_budget = max_tokens * 0.6

        for resp in sorted_resps:
            cn = resp['class_name']
            ideal = (resp['ideal_responsibility'] or '')[:60]
            tags = resp['responsibility_tags'] or ''
            sins = resp['sin_diagnosis'] or ''
            extract = resp['extract_candidates'] or ''

            if tokens_used < detail_budget:
                entry = (f"### {cn}\n"
                         f"- Ideal: {ideal}\n"
                         f"- Tags: {tags}\n"
                         f"- Sins: {sins}\n"
                         f"- Extract: {extract}\n")
            else:
                entry = f"- {cn}: {ideal} [{tags}]\n"

            entry_tokens = len(entry) // 4
            if tokens_used + entry_tokens > max_tokens:
                remaining = len(sorted_resps) - len(lines)
                lines.append(f"\n... [{remaining} more classes omitted]")
                break

            lines.append(entry)
            tokens_used += entry_tokens

        return '\n'.join(lines)

    @staticmethod
    def _summarize_sins(resps):
        """Build Seven Sins diagnosis summary from DB records."""
        lines = []
        for r in resps:
            diag = r['sin_diagnosis'] or ''
            if diag:
                lines.append(f"  {r['class_name']}: {diag}")
        if not lines:
            return "No sin diagnoses available."
        return "\n".join(lines)

    def run_tradeoff(self, question):
        """Tradeoff evaluation — challenge the design proposal. Returns response or None."""
        print(f"\n  [DesignAgent] Tradeoff evaluation...")
        print(f"    Question: {question}")

        design = self.db.get_latest_design()
        if not design:
            print("  ✗ No design proposal. Run analyze first.")
            return None

        # Build context from DB
        module_info = self.db.get_module_info()
        arch_context = ""
        if module_info:
            arch_context = (f"Module: {module_info['module_name']}, "
                          f"Orchestrator: {module_info['orchestrator'] or '—'}")

        design_context = design['full_analysis'] or ''

        prompt = self.prompts.build_tradeoff_prompt(
            arch_context, design_context, question
        )
        tokens_est = self.prompts.estimate_tokens(prompt)
        print(f"    Prompt: ~{tokens_est} tokens")

        response = self.llm.generate(prompt, tag="tradeoff")

        if not response:
            print("    ⚠ No response received.")
            return None

        report_file = OUTPUTS_DIR / "tradeoff_analysis.md"
        report_file.write_text(
            f"# Tradeoff Evaluation\n\n"
            f"**Question**: {question}\n"
            f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"## AI Analysis\n\n{response}\n",
            encoding='utf-8'
        )

        print(f"\n  ✓ Tradeoff evaluation complete")
        print(f"  ✓ Report: {report_file.name}")
        return response
