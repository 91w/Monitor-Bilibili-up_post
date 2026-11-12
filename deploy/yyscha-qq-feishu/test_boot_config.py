import importlib.util
from pathlib import Path
import shutil
import unittest
import uuid

spec = importlib.util.spec_from_file_location('yyscha_boot', Path(__file__).parent / 'h616/boot_config.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class BootTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / '.test-work' / uuid.uuid4().hex
        self.base, self.sbin = self.root / 'yyscha', self.root / 'sbin'
        (self.base / 'app').mkdir(parents=True)
        self.sbin.mkdir()
        for name in ('yyscha-service', 'yyscha-update-service'):
            (self.base / 'app' / name).write_text('# supervisor')
        self.inittab = self.root / 'inittab'
        self.original = '# existing services\nnull::respawn:/usr/sbin/b-up-service\n'
        self.inittab.write_text(self.original)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_preserves_existing_services_and_is_idempotent(self):
        self.assertTrue(boot.configure(self.base, self.inittab, self.sbin))
        result = self.inittab.read_text()
        self.assertTrue(result.startswith(self.original))
        self.assertEqual(result.count('null::respawn:'), 3)
        self.assertFalse(boot.configure(self.base, self.inittab, self.sbin))
        self.assertEqual(self.inittab.read_text(), result)
        self.assertEqual((self.base / 'backups/inittab-before-yyscha-supervisors').read_text(), self.original)

    def test_foreign_wrapper_is_not_overwritten(self):
        target = self.sbin / 'yyscha-service'
        target.write_text('foreign-content')
        with self.assertRaises(RuntimeError):
            boot.configure(self.base, self.inittab, self.sbin)
        self.assertEqual(target.read_text(), 'foreign-content')
        self.assertEqual(self.inittab.read_text(), self.original)
