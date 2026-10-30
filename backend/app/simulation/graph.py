"""The dependency graph and its traversal. No festival logic lives here.

A scenario supplies one rule per relationship it cares about. Each rule queries real rows
and returns the records it reached plus what happened to them. Traversal walks breadth
first from the seed, so a record's depth says how far the effect travelled: depth 1 is
direct, anything further is indirect. No model is involved at any point.
"""
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

MODULES = {
    "participants": "Participants", "events": "Events", "venues": "Venues & Resources", "volunteers": "Volunteers",
    "transportation": "Transportation", "accommodation": "Accommodation", "finance": "Finance",
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str


EDGES = (
    Edge("accommodation", "participants", "houses"),
    Edge("accommodation", "finance", "costs"),
    Edge("participants", "events", "registered for"),
    Edge("participants", "transportation", "travel with"),
    Edge("participants", "accommodation", "stay in"),
    Edge("participants", "volunteers", "are supported by"),
    Edge("events", "venues", "held at"),
    Edge("events", "participants", "attended by"),
    Edge("events", "volunteers", "staffed by"),
    Edge("venues", "events", "hosts"),
    Edge("volunteers", "events", "staff"),
    Edge("transportation", "participants", "carries"),
    Edge("finance", "accommodation", "funds"),
    Edge("finance", "events", "funds"),
    Edge("finance", "transportation", "funds"),
    Edge("finance", "venues", "funds"),
    Edge("finance", "volunteers", "funds"),
)


@dataclass
class Impact:
    module: str
    severity: str
    certainty: str  # fact: read from the database. estimate: depends on a stated assumption.
    explanation: str
    recommended_action: str | None = None
    record_ref: str | None = None
    is_direct: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"module": self.module, "severity": self.severity, "certainty": self.certainty, "explanation": self.explanation,
                "recommended_action": self.recommended_action, "record_ref": self.record_ref, "is_direct": self.is_direct}


@dataclass
class Hop:
    records: Collection[Any] = ()
    impacts: list[Impact] = field(default_factory=list)


Rule = Callable[[set[Any]], Hop]
Rules = dict[tuple[str, str], Rule]


@dataclass
class Traversal:
    depth: dict[str, int]
    records: dict[str, set[Any]]
    impacts: list[Impact]
    active: set[tuple[str, str]]


def traverse(seed_module: str, seed_records: Collection[Any], seed_impacts: list[Impact], rules: Rules) -> Traversal:
    """Walk outward from the seed. Each relationship fires at most once, so cycles end."""
    result = Traversal({seed_module: 0}, {seed_module: set(seed_records)}, list(seed_impacts), set())
    queue = deque([(seed_module, set(seed_records), 0)])
    fired: set[tuple[str, str]] = set()
    while queue:
        module, records, depth = queue.popleft()
        for edge in EDGES:
            key = (edge.source, edge.target)
            if edge.source != module or key in fired or key not in rules:
                continue
            fired.add(key)
            hop = rules[key](records)
            for impact in hop.impacts:
                impact.is_direct = depth == 0
            result.impacts.extend(hop.impacts)
            reached = set(hop.records)
            if hop.impacts or reached:
                result.active.add(key)
            if reached:
                result.depth.setdefault(edge.target, depth + 1)
                result.records.setdefault(edge.target, set()).update(reached)
                queue.append((edge.target, reached, depth + 1))
    result.impacts.sort(key=lambda i: (SEVERITY_ORDER[i.severity], not i.is_direct, i.module, i.explanation))
    return result


def graph_payload(traversal: Traversal) -> dict[str, Any]:
    nodes = []
    for module, label in MODULES.items():
        impacts = [i for i in traversal.impacts if i.module == module]
        worst = min((i.severity for i in impacts), key=SEVERITY_ORDER.__getitem__, default=None)
        nodes.append({"id": module, "label": label, "depth": traversal.depth.get(module), "severity": worst,
                      "records": len(traversal.records.get(module, ())), "impacts": len(impacts)})
    edges = [{"source": e.source, "target": e.target, "relation": e.relation, "active": (e.source, e.target) in traversal.active} for e in EDGES]
    return {"nodes": nodes, "edges": edges}
