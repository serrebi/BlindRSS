import unittest
from core.article_extractor import (
    _normalize_whitespace,
    _looks_like_media_url,
    _strip_zdnet_recommends_block,
    _merge_texts,
    _split_paragraphs,
    _strip_title_suffix
)

class TestArticleExtractor(unittest.TestCase):

    def test_normalize_whitespace(self):
        self.assertEqual("hello   world", _normalize_whitespace("  hello   world  "))
        self.assertEqual("hello\nworld", _normalize_whitespace("hello\r\nworld"))
        self.assertEqual("hello\n\nworld", _normalize_whitespace("hello\n\n\nworld"))

    def test_looks_like_media_url(self):
        self.assertTrue(_looks_like_media_url("http://example.com/audio.mp3"))
        self.assertTrue(_looks_like_media_url("http://example.com/video.mp4"))
        self.assertFalse(_looks_like_media_url("http://example.com/article.html"))

    def test_strip_zdnet_recommends_block(self):
        text_with_boilerplate = (
            "ZDNET Recommends\n"
            "Our goal is to deliver\n\n"
            "Some content here that is long enough to be real.\n\n"
            "Follow ZDNET on social media for more news."
        )
        expected = (
            "Some content here that is long enough to be real.\n\n"
            "Follow ZDNET on social media for more news."
        )
        self.assertEqual(expected, _strip_zdnet_recommends_block(text_with_boilerplate).strip())

    def test_merge_texts(self):
        # Test deduplication
        texts_with_dupes = [
            "This is the first paragraph which is long enough.",
            "This is the second paragraph, also long enough to be kept.",
            "This is the first paragraph which is long enough.", # dupe
            "This is the third paragraph, which is unique and long enough."
        ]
        expected_deduped = "This is the first paragraph which is long enough.\n\nThis is the second paragraph, also long enough to be kept.\n\nThis is the third paragraph, which is unique and long enough."
        self.assertEqual(expected_deduped, _merge_texts(texts_with_dupes))

    def test_split_paragraphs(self):
        text = "Paragraph 1.\n\nParagraph 2.\nParagraph 3."
        self.assertEqual(["Paragraph 1.", "Paragraph 2.", "Paragraph 3."], _split_paragraphs(text))

    def test_strip_title_suffix(self):
        self.assertEqual("Main Title", _strip_title_suffix("Main Title | Site Name"))
        self.assertEqual("Main Title", _strip_title_suffix("Main Title — Site Name"))
        self.assertEqual("Main Title", _strip_title_suffix("Main Title – Site Name"))
        self.assertEqual("A Title - With Hyphen", _strip_title_suffix("A Title - With Hyphen"))

if __name__ == '__main__':
    unittest.main()
