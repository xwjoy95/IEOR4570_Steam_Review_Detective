"""Evidence store: every citable item (review, patch note, player-count row) gets an id like "R12".

One store per request, shared by every agent and tool in that run. Tools add raw data here and
hand the model only the numbered summary; the Writer may cite only ids that exist here.

Owner: Jiashu (orchestration). Keep it small: it's infrastructure, not agent logic.
"""

import re
from dataclasses import asdict, dataclass, field

CITATION = re.compile(r"\[(R\d+)\]")


@dataclass
class Evidence:
    source: str  # "review" | "news" | "player_history" | ...
    raw_id: str  # recommendationid, gid, month, ...
    url: str
    date: str  # YYYY-MM-DD
    text: str
    meta: dict = field(default_factory=dict)  # e.g. a review's language, hours_at_review, positive


class EvidenceStore:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}

    def add(self, source: str, raw_id: str, url: str, date: str, text: str, meta: dict | None = None) -> str:
        """Store one item and return its id. The same (source, raw_id) keeps its first id.

        meta: optional facts the Writer should see next to the text, e.g. for a review
        {"language": "schinese", "hours_at_review": 96.1, "positive": False}."""
        for rid, item in self._items.items():
            if item.source == source and item.raw_id == str(raw_id):
                return rid
        rid = f"R{len(self._items) + 1}"
        self._items[rid] = Evidence(source, str(raw_id), url, date, text, meta or {})
        return rid

    def get(self, rid: str) -> Evidence | None:
        return self._items.get(rid)

    def items(self) -> dict[str, dict]:
        """All evidence as plain dicts, for the /chat response (the page opens a citation from it)."""
        return {rid: asdict(item) for rid, item in self._items.items()}

    def validate(self, report: str) -> list[str]:
        """Ids the report cites that don't exist. An empty list means every citation is real."""
        cited = set(CITATION.findall(report))
        return sorted(cited - self._items.keys(), key=lambda r: int(r[1:]))

    def __len__(self) -> int:
        return len(self._items)
