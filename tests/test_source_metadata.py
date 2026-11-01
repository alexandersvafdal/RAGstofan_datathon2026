"""Source definitions must reach compatibility checks without reading numeric arrays."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hagbot.semantics import profile
from hagbot.sources import LocalPxSource, _px_metadata

from test_answer_integrity import CatalogStub, entry


class PxMetadataTests(unittest.TestCase):
    def test_translated_source_definitions_and_notes_are_preserved(self):
        header = ('CODEPAGE="windows-1252";\nUNITS="Fjöldi";\nUNITS[en]="Persons";\n'
                  'CONTENTS[en]="Population by municipality";\nSOURCE[en]="Statistics Iceland";\n'
                  'NOTE[en]="Reference date; "\n"1 January";\nDATA=\n1 2;\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.px"
            path.write_bytes(header.encode("cp1252"))
            fields = _px_metadata(path, "en")
        self.assertEqual(fields, {"unit": "Persons", "source": "Statistics Iceland",
                                  "description": "Population by municipality", "note": "Reference date; 1 January"})

    def test_data_marker_across_chunk_boundary_stops_large_array_reads(self):
        prefix = b'UNITS="Persons";\n'
        header = prefix + b" " * (65534 - len(prefix)) + b"\nDATA=\n"
        stream = io.BytesIO(header + b'UNITS="wrong";\n' + b"0 " * 1000000)
        class TrackedStream(io.BytesIO):
            bytes_read = 0
            def read(self, size=-1):
                self.assert_bounded(size)
                result = super().read(size)
                self.bytes_read += len(result)
                return result
            def assert_bounded(self, size):
                if not 0 < size <= 65536:
                    raise AssertionError("metadata read must be bounded")
        tracked = TrackedStream(stream.getvalue())
        with patch.object(Path, "open", return_value=tracked):
            fields = _px_metadata(Path("unused.px"), "en")
        self.assertEqual(fields["unit"], "Persons")
        self.assertLessEqual(tracked.bytes_read, 131072)


class LocalMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_px_definitions_are_citable_cached_and_isolated_from_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "metadata.json").write_text(json.dumps({"title": "Population", "variables": []}))
            (path / "data.px").write_text('UNITS[en]="Persons";\nSOURCE[en]="Statistics Iceland";\n'
                                          'NOTE[en]="Reference date 1 January";\nDATA=1;\n')
            source = LocalPxSource.__new__(LocalPxSource)
            source.catalog, source.lang, source._metadata = CatalogStub(entry()), "en", {}
            source._folder = lambda _: path
            with patch("hagbot.sources._px_metadata", wraps=_px_metadata) as read:
                first = await source.meta("TEST001")
                first.notes.clear()
                second = await source.meta("TEST001")
            self.assertEqual(read.call_count, 1)
            fields = profile(second).fields
            self.assertEqual(fields["unit"], "Persons")
            self.assertEqual(fields["source"], "Statistics Iceland")
            self.assertEqual(fields["note:0"], "Reference date 1 January")


if __name__ == "__main__":
    unittest.main()
