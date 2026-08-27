"""Merge the OA cache and interdisciplinary retrieval migration heads."""

from collections.abc import Sequence

revision: str = "fa1b2c3d4e5f"
down_revision: str | Sequence[str] | None = ("d1e2f3a4b5c6", "f0a1b2c3d4e5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
