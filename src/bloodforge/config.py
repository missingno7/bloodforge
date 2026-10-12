"""Explicit local configuration; reference resources are always read-only."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Config:
    corpus_root: Path
    data_root: Path
    reference_root: Path | None = None
    observer: Path | None = None
    game_assets: Path | None = None
    nblood: Path | None = None

    @property
    def knowledge_db(self) -> Path:
        return self.data_root / "knowledge.db"

    @property
    def workspace_db(self) -> Path:
        return self.data_root / "workspace.db"


def load_config(path: Path | None = None, *, corpus_root: str | None = None,
                data_root: str | None = None) -> Config:
    config_path = path or Path("local.toml")
    settings = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    settings = settings.get("bloodforge", settings)
    base = config_path.resolve().parent

    def resolve(value: str | None) -> Path | None:
        if not value:
            return None
        result = Path(value).expanduser()
        return (base / result).resolve() if not result.is_absolute() else result.resolve()

    reference = resolve(os.environ.get("BLOODFORGE_REFERENCE_ROOT") or settings.get("reference_root"))
    if reference is None:
        sibling = Path.cwd().parent / "llmapper"
        if sibling.is_dir():
            reference = sibling.resolve()
    corpus = resolve(corpus_root or os.environ.get("BLOODFORGE_CORPUS_ROOT") or settings.get("corpus_root"))
    if corpus is None and reference:
        corpus = reference / "maps" / "blood"
    if corpus is None:
        raise ValueError("Set corpus_root in local.toml or pass --corpus-root.")
    data = resolve(data_root or os.environ.get("BLOODFORGE_DATA_ROOT") or settings.get("data_root", "data"))
    assert data is not None
    if data == corpus or corpus in data.parents or (reference and (data == reference or reference in data.parents)):
        raise ValueError("data_root must be outside the read-only reference/corpus directories")
    return Config(corpus, data, reference, resolve(settings.get("observer")),
                  resolve(settings.get("game_assets")), resolve(settings.get("nblood")))
