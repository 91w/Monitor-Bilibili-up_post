import unittest
from unittest.mock import Mock
import relay as r
from history_test import fetch_day, select_samples, category


def item(i, uid='u_one', html='<div class="content">正文</div>'):
    return {'id': str(i), 'uid': uid, 'html': html, 'type': 'text'}


class HistoryTest(unittest.TestCase):
    def test_pagination_reads_all_pages(self):
        source = Mock()
        source.page.side_effect = [dict(total=61, day={'chat_key': 'g'}, items=[item(i) for i in range(60)]), dict(total=61, day={'chat_key': 'g'}, items=[item(60)])]
        self.assertEqual(len(fetch_day(source, '2026-11-11', 'u_one')), 61)
        self.assertEqual(source.page.call_args.args[-1], 60)

    def test_empty_midpage_is_failure(self):
        source = Mock()
        source.page.side_effect = [dict(total=61, day={'chat_key': 'g'}, items=[item(i) for i in range(60)]), dict(total=61, day={'chat_key': 'g'}, items=[])]
        with self.assertRaises(r.Failure):
            fetch_day(source, '2026-11-11', 'u_one')

    def test_selection_covers_categories_and_caps_count(self):
        url = 'serve.php?record=x&path=a.png'
        items = [item(1), item(2, html='<div class="content"><div class="reply-content">引用</div>回复</div>'), item(3, html=f'<div class="content"><img src="{url}"></div>'), item(4, html=f'<div class="content"><div class="reply-content">引用</div><img src="{url}"></div>'), item(5, uid='u_two'), item(6)]
        chosen = select_samples(items)
        self.assertEqual(len(chosen), 5)
        self.assertEqual(len({category(i) for i in chosen}), 4)
        self.assertIn('u_two', {i['uid'] for i in chosen})
        self.assertEqual(len(select_samples(items, 2)), 2)

    def test_duplicate_ids_and_empty_day(self):
        self.assertEqual(len(select_samples([item(1), item(1)])), 1)
        self.assertEqual(select_samples([]), [])


if __name__ == '__main__':
    unittest.main()
