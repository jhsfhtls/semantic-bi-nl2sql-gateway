"""Client Path Shield preventing local lakehouse data from leaking into the public repository."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

SourceMode = Literal["synthetic_embedded", "lakehouse_readonly"]


class ClientPathShieldError(PermissionError):
    """Raised when an output path violates repository isolation or POP_T_154 shielding."""


class ClientPathShield:
    """Enforces safe export destinations across synthetic and local read-only lakehouse modes."""

    _PROTECTED_RELATIVE_DIRS = (
        ".git",
        "src",
        "catalog",
        "docs",
        "dbt/models",
        "dbt/seeds",
        "state",
        "tests",
    )

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    @property
    def repo_root(self) -> Path:
        """Return resolved repository root protected by this shield."""
        return self._repo_root

    def validate_output_path(
        self,
        output_path: Path,
        source_mode: SourceMode = "synthetic_embedded",
    ) -> Path:
        """Validate that output_path does not overwrite source files or leak local lakehouse data."""
        raw_str = str(output_path)
        if not raw_str.strip() or "\x00" in raw_str:
            raise ClientPathShieldError("Output path must be a non-empty valid path.")

        resolved = output_path.expanduser().resolve()

        for rel in self._PROTECTED_RELATIVE_DIRS:
            protected = (self._repo_root / rel).resolve()
            if resolved == protected or resolved.is_relative_to(protected):
                raise ClientPathShieldError(
                    f"Write blocked inside protected repository directory: {protected}"
                )

        if source_mode == "lakehouse_readonly" and (
            resolved == self._repo_root or resolved.is_relative_to(self._repo_root)
        ):
            raise ClientPathShieldError(
                "Outputs from local sibling lakehouses must stay outside the public repository "
                f"({self._repo_root}) per POP_T_154."
            )

        return resolved
