"""Create a consistent SQLite backup; never overwrite an existing backup."""
import os
from pathlib import Path
import sqlite3
import sys


def backup(source, destination):
    # Opening the source read-only prevents a typo from creating an empty database.
    source = Path(source).resolve()
    destination = Path(destination)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed')
    except Exception:
        destination.unlink()
        raise


if __name__ == '__main__':
    backup(os.environ.get('DATABASE_PATH', '/data/db.sqlite3'), sys.argv[1])
