"""Ghi file an toàn (atomic), đọc/ghi CSV và băm sha256.

Chuyển thể từ ``legacy/source/video_retrieval/common.py``: ghi ra file tạm rồi
``os.replace`` để file đích không bao giờ ở trạng thái ghi dở khi chương trình
bị ngắt giữa chừng.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="")
    os.replace(temp, path)


def atomic_json(path: Path, value) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Băm theo từng chunk 1 MB: không nạp cả video vào RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def read_csv_header(path: Path) -> list[str]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as source:
        return next(csv.reader(source), [])


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping]) -> None:
    """Ghi CSV atomic, cột cố định; giá trị None ghi thành chuỗi rỗng."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
    os.replace(temp, path)
