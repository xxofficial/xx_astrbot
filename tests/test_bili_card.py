import unittest

from bili_card import build_bili_card_context, load_bili_card_template
from bilibili import BiliUpdate


class BiliCardContextTests(unittest.TestCase):
    def test_builds_dynamic_gallery_and_escapes_content(self):
        update = BiliUpdate(
            key="12345",
            uid="42",
            author='<测试 & "UP">',
            kind="dynamic",
            title="图文动态",
            text="今天也很开心 <script>alert(1)</script>",
            published_at=1700000000,
            url="https://t.bilibili.com/12345",
            images=(
                "https://i0.hdslb.com/1.jpg?x=1&y=2",
                "javascript:alert(1)",
                "https://example.com/not-a-bilibili-image.jpg",
                "https://i0.hdslb.com/2.jpg",
            ),
            raw_type="DYNAMIC_TYPE_DRAW",
            avatar="https://i0.hdslb.com/avatar.jpg",
        )

        context = build_bili_card_context(update, "2026-09-02 12:00")

        self.assertFalse(context["is_video"])
        self.assertEqual(context["image_count"], 2)
        self.assertEqual(context["gallery_class"], "two")
        self.assertIn("&lt;script&gt;", context["content"])
        self.assertNotIn("<script>", context["content"])
        self.assertEqual(context["author_initial"], "测")
        self.assertEqual(context["kind_label"], "动态更新")

    def test_video_uses_only_the_cover(self):
        update = BiliUpdate(
            key="BV1TEST",
            uid="42",
            author="测试UP",
            kind="video",
            title="蓝色视频",
            text="视频简介",
            published_at=1700000000,
            url="https://www.bilibili.com/video/BV1TEST",
            images=(
                "https://i0.hdslb.com/cover.jpg",
                "https://i0.hdslb.com/unused.jpg",
            ),
            raw_type="DYNAMIC_TYPE_AV",
        )

        context = build_bili_card_context(update, "2026-09-02 12:00")

        self.assertTrue(context["is_video"])
        self.assertEqual(context["kind_label"], "视频投稿")
        self.assertEqual(context["images"], ["https://i0.hdslb.com/cover.jpg"])

    def test_template_contains_blue_video_and_dynamic_layouts(self):
        template = load_bili_card_template()

        self.assertIn("--blue-650", template)
        self.assertIn("{% if is_video %}", template)
        self.assertIn('class="gallery {{ gallery_class }}"', template)
        self.assertIn("B站订阅小助手", template)
        self.assertIn("--card-scale", template)
        self.assertIn("root.clientWidth", template)
        self.assertIn("availableWidth / baseCardWidth", template)


if __name__ == "__main__":
    unittest.main()
