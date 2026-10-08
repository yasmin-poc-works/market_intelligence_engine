"""File-backed scratch store. Raw source content lives here, never only in
conversation history; agents pass around the returned source_id."""
import json
import re
import uuid
from pathlib import Path
from typing import Any

from config import get_settings

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class SourceNotFoundError(KeyError):
    pass


class ScratchStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else get_settings().scratch_dir

    def _path(self, source_id: str) -> Path:
        if not _ID_RE.match(source_id):  # also blocks path traversal
            raise ValueError(f"invalid source_id: {source_id!r}")
        return self.root / f"{source_id}.json"

    def write_source(self, data: dict[str, Any]) -> str:
        """Store a source record and return its source_id."""
        source_id = uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        record = {**data, "source_id": source_id}
        tmp = self._path(source_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self._path(source_id))
        return source_id

    def read_source(self, source_id: str) -> dict[str, Any]:
        path = self._path(source_id)
        if not path.exists():
            raise SourceNotFoundError(source_id)
        return json.loads(path.read_text(encoding="utf-8"))


def write_source(data: dict[str, Any]) -> str:
    return ScratchStore().write_source(data)


def read_source(source_id: str) -> dict[str, Any]:
    return ScratchStore().read_source(source_id)
