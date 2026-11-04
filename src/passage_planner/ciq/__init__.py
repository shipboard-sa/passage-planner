"""Customs / immigration / quarantine reference for the arrival dossier."""

from __future__ import annotations

from .data import REGISTRY, resolve
from .model import CountryCIQ
from .render import ciq_template, render_dossier

__all__ = [
    "REGISTRY",
    "CountryCIQ",
    "ciq_template",
    "render_dossier",
    "resolve",
]
