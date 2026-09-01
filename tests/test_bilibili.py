import unittest

from bilibili import (
    BilibiliPublicClient,
    normalize_bili_uid,
    parse_space_item,
)


class NormalizeUidTests(unittest.TestCase):
    def test_accepts_numeric_uid_and_space_url(self):
        self.assertEqual(normalize_bili_uid("0010082742"), "10082742")
        self.assertEqual(
            normalize_bili_uid("https://space.bilibili.com/10082742/"),
            "10082742",
        )

    def test_rejects_invalid_uid(self):
        for value in ("", "0", "abc", "https://www.bilibili.com/video/BV1xx"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_bili_uid(value)


class ParseSpaceItemTests(unittest.TestCase):
    def test_parses_desktop_dynamic_module_list(self):
        item = {
            "id_str": "123456",
            "type": "DYNAMIC_TYPE_DRAW",
            "visible": True,
            "modules": [
                {
                    "module_type": "MODULE_TYPE_AUTHOR",
                    "module_author": {
                        "pub_ts": 1700000000,
                        "user": {
                            "mid": 42,
                            "name": "测试UP",
                            "face": "//i0.hdslb.com/avatar.jpg",
                        },
                    },
                },
                {
                    "module_type": "MODULE_TYPE_DESC",
                    "module_desc": {"text": "一条公开动态"},
                },
            ],
        }

        update = parse_space_item(item, "42")

        self.assertIsNotNone(update)
        self.assertEqual(update.kind, "dynamic")
        self.assertEqual(update.key, "123456")
        self.assertEqual(update.author, "测试UP")
        self.assertEqual(update.text, "一条公开动态")
        self.assertEqual(update.url, "https://t.bilibili.com/123456")
        self.assertEqual(update.avatar, "https://i0.hdslb.com/avatar.jpg")

    def test_parses_desktop_video_and_cover(self):
        item = {
            "id_str": "987654",
            "type": "DYNAMIC_TYPE_AV",
            "visible": True,
            "modules": [
                {
                    "module_author": {
                        "pub_ts": "1700000001",
                        "user": {"mid": 42, "name": "测试UP"},
                    }
                },
                {
                    "module_dynamic": {
                        "dyn_archive": {
                            "bvid": "BV1TEST",
                            "title": "测试视频",
                            "cover": "http://i0.hdslb.com/test.jpg",
                        }
                    }
                },
            ],
        }

        update = parse_space_item(item, "42")

        self.assertIsNotNone(update)
        self.assertEqual(update.kind, "video")
        self.assertEqual(update.key, "BV1TEST")
        self.assertEqual(update.title, "测试视频")
        self.assertEqual(update.url, "https://www.bilibili.com/video/BV1TEST")
        self.assertEqual(update.images, ("https://i0.hdslb.com/test.jpg",))

    def test_parses_dictionary_module_layout(self):
        item = {
            "id_str": "24680",
            "type": "DYNAMIC_TYPE_AV",
            "modules": {
                "module_author": {
                    "mid": 7,
                    "name": "旧结构UP",
                    "pub_ts": 1700000002,
                },
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_ARCHIVE",
                        "archive": {
                            "bvid": "BV1OLD",
                            "title": "旧结构视频",
                        },
                    }
                },
            },
        }

        update = parse_space_item(item, "7")

        self.assertIsNotNone(update)
        self.assertEqual(update.author, "旧结构UP")
        self.assertEqual(update.key, "BV1OLD")

    def test_skips_invisible_item(self):
        self.assertIsNone(
            parse_space_item(
                {
                    "id_str": "1",
                    "type": "DYNAMIC_TYPE_WORD",
                    "visible": False,
                },
                "42",
            )
        )

    def test_keeps_up_to_nine_dynamic_images(self):
        pictures = [
            {"src": f"//i0.hdslb.com/image-{index}.jpg"}
            for index in range(11)
        ]
        update = parse_space_item(
            {
                "id_str": "13579",
                "type": "DYNAMIC_TYPE_DRAW",
                "modules": {
                    "module_author": {"mid": 42, "name": "测试UP"},
                    "module_dynamic": {
                        "major": {"draw": {"items": pictures}}
                    },
                },
            },
            "42",
        )

        self.assertIsNotNone(update)
        self.assertEqual(len(update.images), 9)
        self.assertEqual(
            update.images[-1], "https://i0.hdslb.com/image-8.jpg"
        )


class PublicClientTests(unittest.TestCase):
    def test_request_headers_do_not_contain_account_cookie(self):
        headers = BilibiliPublicClient()._headers("42", "dynamic")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("SESSDATA", str(headers))
        self.assertNotIn("bili_jct", str(headers))


if __name__ == "__main__":
    unittest.main()
