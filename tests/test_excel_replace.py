from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.connections.xlsx_connection import replace_excel_file


class ExcelReplaceTests(unittest.TestCase):
    def test_transient_permission_error_retries_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'existing.xlsx'
            temporary = Path(directory) / 'new.xlsx'
            target.write_bytes(b'old workbook')
            temporary.write_bytes(b'new workbook')
            original_replace = Path.replace
            attempts = []

            def temporarily_locked(path, destination):
                attempts.append(1)
                if len(attempts) < 3:
                    raise PermissionError('reader has not released the file')
                return original_replace(path, destination)

            with patch.object(Path, 'replace', temporarily_locked), \
                 patch('src.connections.xlsx_connection.time.sleep') as sleep:
                replace_excel_file(temporary, target)
            self.assertEqual(target.read_bytes(), b'new workbook')
            self.assertFalse(temporary.exists())
            self.assertEqual(len(attempts), 3)
            self.assertEqual(sleep.call_count, 2)

    def test_persistent_lock_raises_without_changing_existing_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'existing.xlsx'
            temporary = Path(directory) / 'new.xlsx'
            target.write_bytes(b'old workbook')
            temporary.write_bytes(b'new workbook')
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')) as replace, \
                 patch('src.connections.xlsx_connection.time.sleep'):
                with self.assertRaises(PermissionError):
                    replace_excel_file(temporary, target)
            self.assertEqual(replace.call_count, 10)
            self.assertEqual(target.read_bytes(), b'old workbook')
            self.assertEqual(temporary.read_bytes(), b'new workbook')
