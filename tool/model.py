"""
model.py — entity & relationship value objects

These are the in-memory representation of what the parser extracts.
Pure data containers: no parsing logic, no DB knowledge. The parser
produces them; db.py persists them; downstream (agents, prompts,
report) consumes them.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


# ── kinds (string enums — keep simple, no Enum class) ──────────────
ENTITY_KINDS = {'class', 'struct', 'interface', 'enum',
                'method', 'field', 'namespace'}

# weak → strong (matches what reader.py used: Lv-0..Lv-5)
RELATION_KINDS = ('depends',      # Lv-0  include-only / param-or-return type
                  'associates',   # Lv-1  raw pointer / shared_ptr field
                  'implements',   # Lv-2  inherits an interface (I-prefixed / pure virtual)
                  'aggregates',   # Lv-3  container of pointers (vector<X*>)
                  'composes',     # Lv-4  value field / unique_ptr field
                  'inherits',     # Lv-5  class : public Base
                  'calls')        # method → method  (Phase B+ — schema reserved)

LEVEL_OF = {'depends': 0, 'associates': 1, 'implements': 2,
            'aggregates': 3, 'composes': 4, 'inherits': 5, 'calls': 0}


@dataclass
class Entity:
    kind: str               # one of ENTITY_KINDS
    name: str               # short name, e.g. 'Plot'
    qualified_name: str     # fully qualified, e.g. 'UGS::Plot' or 'UGS::Plot::draw'
    file_path: str
    start_line: int
    end_line: int
    parent_qname: Optional[str] = None   # containing entity's qualified_name
    signature: Optional[str] = None      # method full sig / field type / None
    attrs: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in ENTITY_KINDS:
            raise ValueError(f"bad entity kind: {self.kind}")

    def attrs_json(self) -> str:
        return json.dumps(self.attrs, sort_keys=True)


@dataclass
class Relationship:
    source_qname: str             # qualified_name of source entity
    target_name: str              # always populated — even when target is external
    kind: str                     # one of RELATION_KINDS
    evidence_file: str
    evidence_line: int
    evidence_text: str = ''       # original source line — used directly in prompts
    target_qname: Optional[str] = None   # filled in resolve pass when target found
    attrs: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in RELATION_KINDS:
            raise ValueError(f"bad relation kind: {self.kind}")

    @property
    def level(self) -> int:
        return LEVEL_OF[self.kind]

    def attrs_json(self) -> str:
        return json.dumps(self.attrs, sort_keys=True)
