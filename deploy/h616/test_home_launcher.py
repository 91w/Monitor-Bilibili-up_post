import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('launcher',str(Path(__file__).with_name('home-web-launcher.py')))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
    def test_cache_lifetime_and_identity(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cache.json'
            self.assertIsNone(m.load_cache(p,100))
            p.write_text(json.dumps({'ip':'10.23.45.67','mac':m.MAC,'verified_at':100}))
            self.assertEqual(m.load_cache(p,100),'10.23.45.67')
            self.assertEqual(m.load_cache(p,100+m.TTL-1),'10.23.45.67')
            self.assertIsNone(m.load_cache(p,100+m.TTL))
            self.assertIsNone(m.load_cache(p,99))
            p.write_text('{broken');self.assertIsNone(m.load_cache(p,100))
            p.write_text(json.dumps({'ip':'10.23.45.67','mac':'other','verified_at':100}));self.assertIsNone(m.load_cache(p,100))
    def test_ip_validation(self):
        self.assertEqual(m.validate_ip(' 10.23.45.67 '),'10.23.45.67')
        for bad in ('127.0.0.1','8.8.8.8','::1','not an ip','0.0.0.0','169.254.1.1'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):m.validate_ip(bad)
if __name__=='__main__':unittest.main()
