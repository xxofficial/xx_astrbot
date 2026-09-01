import unittest

from bilibili import (
    BiliUpdate,
    BilibiliPublicClient,
    normalize_bili_uid,
    parse_space_item,
    select_latest_update,
    should_at_all_subscription_target,
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


class SubscriptionTargetTests(unittest.TestCase):
    def test_at_all_is_enabled_for_aiocqhttp_group(self):
        self.assertTrue(
            should_at_all_subscription_target(
                "aiocqhttp:GroupMessage:498908616"
            )
        )

    def test_at_all_is_disabled_for_private_or_other_platform_targets(self):
        targets = (
            "aiocqhttp:PrivateMessage:123456",
            "other:GroupMessage:498908616",
            "aiocqhttp:GroupMessage:",
            "invalid",
            None,
        )
        for target in targets:
            with self.subTest(target=target):
                self.assertFalse(should_at_all_subscription_target(target))


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
                            "desc": "测试视频简介",
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
        self.assertEqual(update.text, "测试视频简介")
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

    def test_parses_modern_opus_content(self):
        update = parse_space_item(
            {
                "id_str": "112233",
                "type": "DYNAMIC_TYPE_DRAW",
                "modules": {
                    "module_author": {
                        "mid": 42,
                        "name": "测试UP",
                        "pub_ts": 1700000003,
                    },
                    "module_dynamic": {
                        "major": {
                            "opus": {
                                "title": "新版图文标题",
                                "summary": {"text": "新版图文正文"},
                                "pics": [
                                    {"url": "//i0.hdslb.com/opus-1.jpg"},
                                    {"url": "http://i0.hdslb.com/opus-2.jpg"},
                                ],
                            }
                        }
                    },
                    "module_tag": {"text": "置顶"},
                },
            },
            "42",
        )

        self.assertIsNotNone(update)
        self.assertEqual(update.title, "新版图文标题")
        self.assertEqual(update.text, "新版图文正文")
        self.assertTrue(update.is_pinned)
        self.assertEqual(
            update.images,
            (
                "https://i0.hdslb.com/opus-1.jpg",
                "https://i0.hdslb.com/opus-2.jpg",
            ),
        )


class PublicClientTests(unittest.TestCase):
    def test_request_headers_do_not_contain_account_cookie(self):
        headers = BilibiliPublicClient()._headers("42", "dynamic")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("SESSDATA", str(headers))
        self.assertNotIn("bili_jct", str(headers))


class SelectLatestUpdateTests(unittest.TestCase):
    def _update(
        self,
        key: str,
        kind: str,
        published_at: int,
        is_pinned: bool = False,
    ) -> BiliUpdate:
        return BiliUpdate(
            key=key,
            uid="42",
            author="测试UP",
            kind=kind,
            title=key,
            text="",
            published_at=published_at,
            url=f"https://t.bilibili.com/{key}",
            images=(),
            raw_type=(
                "DYNAMIC_TYPE_AV"
                if kind == "video"
                else "DYNAMIC_TYPE_WORD"
            ),
            is_pinned=is_pinned,
        )

    def test_selects_by_publish_time_instead_of_feed_position(self):
        pinned_old_dynamic = self._update(
            "old-pinned", "dynamic", 100, is_pinned=True
        )
        newest_video = self._update("new-video", "video", 300)
        newer_dynamic = self._update("new-dynamic", "dynamic", 200)
        updates = [pinned_old_dynamic, newest_video, newer_dynamic]

        self.assertIs(
            select_latest_update(updates, {"dynamic", "video"}),
            newest_video,
        )
        self.assertIs(
            select_latest_update(updates, {"dynamic"}), newer_dynamic
        )
        self.assertIs(select_latest_update(updates, {"video"}), newest_video)

    def test_returns_none_when_no_kind_matches(self):
        update = self._update("dynamic", "dynamic", 100)
        self.assertIsNone(select_latest_update([update], {"video"}))

    def test_client_pages_past_pin_to_find_latest_regular_item(self):
        pinned_old_dynamic = self._update(
            "old-pinned", "dynamic", 100, is_pinned=True
        )
        newer_video = self._update("new-video", "video", 300)
        newer_dynamic = self._update("new-dynamic", "dynamic", 200)

        class StubClient(BilibiliPublicClient):
            def __init__(self):
                self.offsets = []

            def _fetch_space_page(self, normalized_uid, offset=""):
                self.offsets.append(offset)
                if not offset:
                    return [pinned_old_dynamic, newer_video], "page-2", True
                return [newer_dynamic], "", False

        client = StubClient()
        latest = client.fetch_latest_update("42", {"dynamic"})

        self.assertIs(latest, newer_dynamic)
        self.assertEqual(client.offsets, ["", "page-2"])


if __name__ == "__main__":
    unittest.main()
