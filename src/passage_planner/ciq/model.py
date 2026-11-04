"""Customs / immigration / quarantine reference data for one country."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CountryCIQ:
    key: str
    name: str
    ports_of_entry: str
    pre_arrival: str
    on_arrival: str
    documents: str
    official_urls: tuple[str, ...]      # primary sources — verify every fact here
    fees: str = ""                      # fee schedule, amounts + what each covers
    offices: str = ""                   # clearance office locations / phone / hours
    verified: str = ""                  # ISO date these facts were checked at official_urls
    notes: str = ""
    secondary_urls: tuple[str, ...] = field(default_factory=tuple)  # Noonsite etc.
