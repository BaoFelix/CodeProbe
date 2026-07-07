# Custom Skills

Drop a file here to tailor CodeProbe without touching code. Two are
recognized:

- **`design_critic.md`** — override the class-level review methodology
  (documented below).
- **`architecture.md`** — declare architecture rules in plain language
  (e.g. "UI must not depend on Infra"). The `architecture_audit` compiles
  them into checkable rules and reports violations with `file:line`
  evidence. Copy `architecture.example.md` to start. Only `architecture.md`
  is loaded; the `.example` is ignored.

## How it works

When you run `python run.py analyze <path>`, the design-review stage
(`DesignCriticAgent`) looks for a file named exactly **`design_critic.md`**
anywhere under this `skills/` folder. If it finds one, that file's contents
*replace* the built-in review methodology — this is how you install your
own refactoring philosophy or design-style rules (DDD, Clean Architecture,
your team's house style, the classic "Seven Sins", etc.).

If no `design_critic.md` is present, CodeProbe uses its built-in two-pass
methodology and prints nothing special. When an override is found you'll
see `✓ Using user override skill` in the analyze output.

```
skills/
├── README.md                  ← this file
├── design_critic.example.md   ← copy to design_critic.md and edit
├── design_critic.md           ← YOUR review methodology (auto-loaded)
├── architecture.example.md    ← copy to architecture.md and edit
└── architecture.md            ← YOUR architecture rules (auto-loaded)
```

> Only `design_critic.md` / `architecture.md` are loaded. The `.example.md` templates are ignored,
> so it's safe to keep around as a reference.

## Writing your override

Your file is a single prompt template. **It is rendered twice** during one
analysis run, and the `{SCOPE}` token tells you which pass you're in:

| `{SCOPE}` value | When | What you're given | What to return |
|---|---|---|---|
| `subtree` | once per workflow subtree (a bounded part of the code) | `{ROOT}`, `{CLASSES}`, `{METHODS}`, `{FIELDS}`, `{RELATIONS}` | per-subtree findings JSON |
| `module` | once at the end, over the whole codebase | `{SUBTREES}` (your Pass-1 outputs), `{CROSS_RELATIONS}` | codebase-level recommendations JSON |

### Available tokens

Tokens not relevant to the current pass come in empty, so it's fine to
reference all of them.

| Token | Meaning |
|---|---|
| `{SCOPE}` | `subtree` or `module` — branch your instructions on this |
| `{ROOT}` | the subtree's root class name (subtree pass) |
| `{CLASSES}` | classes in scope, with file:line |
| `{METHODS}` | their methods + signatures |
| `{FIELDS}` | their fields + types |
| `{RELATIONS}` | relationships among them (composes / inherits / …) |
| `{SUBTREES}` | summaries of every subtree analyzed in Pass 1 (module pass) |
| `{CROSS_RELATIONS}` | relationships that cross subtree boundaries (module pass) |

### Required output shape

The report parses your model's reply as JSON. Emit JSON only (a ```` ```json ````
fence is tolerated). Keys CodeProbe reads:

**Subtree pass** → drives the per-class "Design Review" cards:
```json
{
  "essence": "one line: what this part fundamentally does",
  "pains": [
    {"title": "God class", "category": "cohesion",
     "where": "Foo.hxx:42", "what": "Foo owns parsing AND rendering AND IO"}
  ]
}
```

**Module pass** → drives the top-level recommendations:
```json
{
  "recommendations": [
    {"priority": "high", "title": "Extract a Parser interface",
     "target": "Foo, Bar", "action": "...", "expected_impact": "...",
     "evidence": "Foo.hxx:42, Bar.hxx:88"}
  ],
  "cross_observations": [
    {"pattern": "both extractors share a 6-stage skeleton",
     "suggestion": "pull up a base class",
     "affected_subtrees": ["Foo", "Bar"]}
  ],
  "missing_abstractions": [
    {"role": "transport", "suggested_interface": "ISink",
     "current_implementations": ["TcpSink", "FileSink"]}
  ]
}
```

Any extra keys are ignored; missing keys just render as empty sections.
Keep `priority` to `high` / `medium` / `low` so sorting works.

See `design_critic.example.md` for a ready-to-edit starting point.
