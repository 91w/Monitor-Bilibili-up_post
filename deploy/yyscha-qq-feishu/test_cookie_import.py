import unittest
from unittest.mock import patch
from pathlib import Path
from cookie_import import cookie_from_txt, parse_expiry


class CookieImportTest(unittest.TestCase):
    def parse(self, text):
        with patch.object(Path, 'stat') as stat, patch.object(Path, 'read_text', return_value=text):
            stat.return_value.st_size = 1000
            return cookie_from_txt('local-only.txt', now=100)

    def test_domain_scope_httponly_expiry_and_path(self):
        value, count = self.parse('# Netscape HTTP Cookie File\n'
            '#HttpOnly_.yyscha.cn\tTRUE\t/\tTRUE\t200\tsession\tlocal\n'
            'www.yyscha.cn\tFALSE\t/application/QQ\tFALSE\t0\tpref\ta\n'
            'other.test\tFALSE\t/\tTRUE\t0\tforeign\tb\n'
            'yyscha.cn.evil.test\tTRUE\t/\tTRUE\t0\tforeign2\tc\n'
            'www.yyscha.cn\tFALSE\t/\tTRUE\t99\told\td\n'
            'www.yyscha.cn\tFALSE\t/application/QQx\tTRUE\t0\tbadpath\te\n'
            'yyscha.cn\tFALSE\t/\tTRUE\t0\thostonly\tf\n')
        self.assertEqual(value, 'pref=a; session=local')
        self.assertEqual(count, 2)

    def test_empty_unrelated_expired_malformed_rejected(self):
        for text in ['', '# header\n', 'session=local', 'evil.test\tTRUE\t/\tTRUE\t0\tx\ty', 'www.yyscha.cn\tFALSE\t/\tTRUE\t50\tx\ty']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.parse(text)

    def test_injection_and_bad_expiry_rejected(self):
        for expiry, name, value in [('0', 'session', 'x;y=z'), ('0', 'bad name', 'x'), ('unknown', 's', 'x')]:
            with self.assertRaises(ValueError):
                self.parse(f'www.yyscha.cn\tFALSE\t/\tTRUE\t{expiry}\t{name}\t{value}')

    def test_session_empty_value_and_exact_path(self):
        self.assertEqual(self.parse('www.yyscha.cn\tFALSE\t/application/QQ/api.php\tTRUE\t0\tflag\t')[0], 'flag=')

    def test_decimal_expiry_and_session_markers(self):
        for expiry in ['200.75', '1.8e9', '', 'Session', 'session', 'null', 'undefined', '�Ự', '-1', '0.0']:
            with self.subTest(expiry=expiry):
                self.assertEqual(self.parse(f'www.yyscha.cn\tFALSE\t/\tTRUE\t{expiry}\tsession\tlocal')[0], 'session=local')
        with self.assertRaises(ValueError):
            self.parse('www.yyscha.cn\tFALSE\t/\tTRUE\t99.9\tsession\tlocal')

    def test_milliseconds_and_iso_date(self):
        self.assertEqual(parse_expiry('1800000000123'), parse_expiry('1800000000.123'))
        self.assertEqual(parse_expiry('2027-01-15T08:00:00+08:00'), parse_expiry('2027-01-15T00:00:00Z'))

    def test_invalid_nonfinite_or_ambiguous_dates(self):
        for expiry in ['NaN', 'Infinity', '-Infinity', '-2', '2027-01-15T00:00:00', 'unknown']:
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                parse_expiry(expiry)

    def test_diagnostic_has_line_number_without_values(self):
        with self.assertRaises(ValueError) as error:
            self.parse('# header\nwww.yyscha.cn\tFALSE\t/\tTRUE\tprivate-unknown\tsession\tprivate-cookie')
        self.assertIn('��2��', str(error.exception))
        self.assertNotIn('private-', str(error.exception))


if __name__ == '__main__':
    unittest.main()
