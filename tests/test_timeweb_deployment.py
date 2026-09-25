import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from health_server import _handle_health_request
from scripts.backup_sqlite import backup_database


class TimewebDeploymentTests(unittest.TestCase):
    def test_sqlite_backup_is_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "allocator.db"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
                connection.execute("INSERT INTO sample VALUES ('saved')")
            destination = backup_database(source, root / "backups")
            with sqlite3.connect(destination) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM sample").fetchone()[0],
                    "saved",
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )

    def test_health_server_answers_without_database_or_telegram(self):
        async def scenario():
            class Writer:
                def __init__(self):
                    self.response = bytearray()

                def write(self, data):
                    self.response.extend(data)

                async def drain(self):
                    return None

                def close(self):
                    return None

                async def wait_closed(self):
                    return None

            reader = asyncio.StreamReader()
            reader.feed_data(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            reader.feed_eof()
            writer = Writer()
            await _handle_health_request(reader, writer)
            self.assertIn(b"200 OK", writer.response)
            self.assertIn(b'{"status":"ok"}', writer.response)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
