"""The one record shape every sanctions/PEP source gets normalized into.

OFAC, the UN and the EU each publish their own XML schema, their own idea of
what a "name" is (OFAC splits first/last; the UN has up to three name parts;
the EU gives one whole-name string), and their own notion of how many dates
of birth or countries one entry can carry (real designees often have several
of each — an approximate DOB, an alternate one from a different document; a
nationality and a residence). ``WatchlistRecord`` is the normal form every
parser in :mod:`crr.screening.parsers` produces, so the rest of the system
(matching, persistence, the UI) never has to know which source a record came
from.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WatchlistRecord:
    """One sanctioned/designated party, normalized from a source list.

    ``source_id`` is that source's own stable identifier for the entry (OFAC's
    ``uid``, the UN's ``REFERENCE_NUMBER``, the EU's entity id) — used as the
    natural key for a source's replace-on-refresh, so re-ingesting the same
    list updates existing rows instead of duplicating them. ``dates_of_birth``
    and ``countries`` are lists rather than single values because a real entry
    commonly has more than one of either; matching treats any element as a
    corroborating hit rather than requiring a single canonical value."""

    source: str  # "ofac" | "un" | "eu" (crr.screening.parsers.SOURCES)
    source_id: str
    name: str
    category: str  # "sanctions" | "pep" | "adverse_media"
    aliases: tuple[str, ...] = field(default_factory=tuple)
    dates_of_birth: tuple[str, ...] = field(default_factory=tuple)
    countries: tuple[str, ...] = field(default_factory=tuple)
    program: str = ""
    remarks: str = ""

    @property
    def reason(self) -> str:
        """The single line render_watchlist_panel shows under a hit."""
        return self.program or self.remarks or f"Listed on the {self.source.upper()} sanctions list."
