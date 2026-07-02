<!--
  CodeProbe architecture rules — TEMPLATE.

  Copy this file to `architecture.md` (same folder) and edit the rules to
  match your codebase. When present, `architecture_audit` compiles these
  plain-language rules into checkable ones (requires an LLM API key) and
  reports violations with file:line evidence, alongside the built-in checks.

  Write in plain language. Name your groups and say how to recognize their
  classes (by folder, by name suffix, by namespace). Then state the rules.
  Only forbidden-dependency style rules are compiled today; other lines are
  ignored (the built-in checks — module cycles, god modules, inverted
  dependencies — always run regardless).
-->
# Architecture rules

## Groups (how to recognize each layer/module)
- **UI**       — classes in `ui/`, or names ending in `View` / `Widget` / `Dialog`
- **Service**  — classes in `service/`, or names ending in `Service`
- **Domain**   — classes in `domain/` or `model/`
- **Infra**    — classes in `infra/`, `db/`, `net/`, or names like `*Connection` / `*Client`

## Rules
- UI must not depend on Infra directly.
- Domain must not depend on UI.
- Service must not depend on UI.
