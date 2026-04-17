# Refactoring Design Guide

> Two cuts make good design:
> 1. Vertical cut: group by change reason (same reason → same class)
> 2. Horizontal cut: layer by abstraction level (each layer says "what", pushes "how" to next layer)

## Vertical Cut (Split by Change Reason)

When splitting a God Class, ask for each method group:
- "What requirement change would cause me to modify this group?"
- Each unique answer = one new class

Example:
```
GraphCreation (God Class, 5 responsibilities)
  → GraphCreation (orchestration only)
  → WeightFactorCalculator (weight computation)
  → PostGraphErrorHandler (error mapping)
  → GraphMetadataFormatter (display options)
```

## Horizontal Cut (Layer by Abstraction)

When a responsibility group has mixed abstraction levels:
- High-level policy (what to do) → stays in orchestrator
- Mid-level logic (how to do it) → domain service class
- Low-level detail (data access, formatting) → infrastructure class

## Interface Extraction Conditions

Extract an interface ONLY when:
1. ≥ 3 classes depend on the same concrete class, AND
2. The concrete class has (or will have) multiple implementations

Do NOT extract interfaces just because something is depended on.
Over-abstraction is as bad as no abstraction.

## Phase Planning

Break refactoring into independently shippable phases:
```
Phase 1: Extract stateless utilities (LOW risk, no API changes)
Phase 2: Introduce interfaces at module boundaries (MEDIUM risk)
Phase 3: Split God Classes by change reason (HIGH risk)
Phase 4: Restructure workflow / orchestration (HIGHEST risk)
```

Each phase must:
- Be backward compatible (deprecate, don't delete)
- Have a clear rollback path
- Be testable independently

## Cross-Boundary Rules

- Cross-module: only Lv-0 (dependency) / Lv-1 (association) / Lv-2 (interface)
- Cross-module forbidden: Lv-4 (composition) / Lv-5 (inheritance)
- Within module: Lv-0~5 all allowed, but prefer weakest sufficient level
