import csv
import threading
from dataclasses import asdict, fields


def write_rows(path: str, rows: list) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[x.name for x in fields(rows[0])])
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)


class RowSink:
    """Appends each row to the CSV as soon as it finishes, so a crash keeps what ran."""

    def __init__(self, path: str):
        self.path, self._lock, self._writer = path, threading.Lock(), None

    def __enter__(self):
        self._f = open(self.path, "w", newline="")
        return self

    def __call__(self, row) -> None:
        with self._lock:
            if self._writer is None:
                self._writer = csv.DictWriter(self._f, fieldnames=[x.name for x in fields(row)])
                self._writer.writeheader()
            self._writer.writerow(asdict(row))
            self._f.flush()

    def __exit__(self, *exc):
        self._f.close()


def read_rows(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))
