from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    home_airports: list[str] = field(default_factory=lambda: ["SCK", "SMF", "OAK", "SFO", "SJC"])
    currency: str = "USD"
    cabin: str = "economy"
    providers: list[str] = field(default_factory=lambda: ["mock"])
    call_budget: int = 40
    model: str = "claude-sonnet-5-5"
    nearby_radius_km: int = 150


def load(path: str | Path = "config.toml") -> Config:
    p = Path(path)
    if not p.exists():
        return Config()
    with p.open("rb") as f:
        data = tomllib.load(f)
    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader. Existing environment variables win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
