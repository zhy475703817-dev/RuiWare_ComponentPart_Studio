from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.app.cleanup_store import CleanupStore, _atomic_bytes


class CleanupStoreTests(unittest.TestCase):
    def test_verified_backup_works_when_windows_denies_directory_rename(self):
        replace = Path.replace

        def windows_replace(path, target):
            if path.is_dir():
                raise PermissionError(13, 'Windows directory rename denied')
            return replace(path, target)

        with TemporaryDirectory() as root:
            store = CleanupStore(Path(root))
            with patch.object(Path, 'replace', windows_replace):
                manifest = store.write_backup({'publicationId': 'old'}, b'package', b'metadata')
            self.assertEqual(store.backup(manifest['backupId']), (manifest, b'package', b'metadata'))
            self.assertEqual(store.list_backups(), [manifest])

    def test_incomplete_backup_is_never_listed_even_during_write(self):
        with TemporaryDirectory() as root:
            store = CleanupStore(Path(root))

            def fail_metadata(path, content):
                self.assertEqual(store.list_backups(), [])
                if path.name == 'metadata.json':
                    raise OSError('disk full')
                return _atomic_bytes(path, content)

            with patch('backend.app.cleanup_store._atomic_bytes', side_effect=fail_metadata):
                with self.assertRaises(OSError):
                    store.write_backup({'publicationId': 'old'}, b'package', b'metadata')
            self.assertEqual(store.list_backups(), [])
            self.assertEqual(list(store.backups.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
