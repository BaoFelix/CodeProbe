# SRP Detection Rules

> Single Responsibility Principle: A class should have only one reason to change.

## Detection Methods

### 1. Description Test
Describe the class in one sentence. If you need "and" to connect two parts → too many responsibilities.
- "Manages graph data **and** computes secondary values" → violation
- "Stores graph metadata for visualization" → OK

### 2. Change-Reason Test
List all reasons someone might modify this class:
- 1 reason = healthy
- 2 reasons = warning (monitor)
- 3+ reasons = violation (split)

### 3. Method Grouping Test
Cluster the methods into groups where methods within a group call each other frequently but rarely call methods in other groups.
- 1 group = healthy
- 2 groups with some cross-calls = warning
- 3+ isolated groups = violation (each group should be its own class)

## Quantitative Thresholds

```
Responsibilities    Rating
1-2                 Healthy
3-4                 Warning — monitor, consider splitting
≥5                  God Class — must split by change reason
```

## Responsibility Counting Guide

A "responsibility" = one independent reason to change. Count by asking:
- What **different stakeholders** care about this class?
- What **different features** would cause modifications here?
- Can I describe each method group as serving a **single purpose**?

Each distinct answer = one responsibility.

## Output Format (for AI analysis)

When analyzing a class, output:
```
RESPONSIBILITY_TAGS: tag1, tag2, tag3
```
- Each tag = one independent change reason (2-4 words)
- Maximum 3 tags per class
- Tags should be reusable across classes (same tag if same responsibility)
