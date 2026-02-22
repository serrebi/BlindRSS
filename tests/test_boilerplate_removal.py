
import unittest
import sys
import os
sys.path.append(os.getcwd())
from core import article_extractor

class TestBoilerplateRemoval(unittest.TestCase):
    def test_9to5mac(self):
        text = """
FTC: We use income earning auto affiliate links. More.
You’re reading 9to5Mac — experts who break news about Apple and its surrounding ecosystem, day after day. Be sure to check out our homepage for all the latest news, and follow 9to5Mac on Twitter, Facebook, and LinkedIn to stay in the loop. Don’t know where to start? Check out our exclusive stories, reviews, how-tos, and subscribe to our YouTube channelRyan got his start in journalism as an Editor at MacStories...
Real Content Here.
"""
        cleaned = article_extractor._postprocess_extracted_text(text, "https://9to5mac.com/some-article")
        self.assertNotIn("FTC: We use", cleaned)
        self.assertNotIn("You’re reading 9to5Mac", cleaned)
        self.assertNotIn("subscribe to our YouTube channel", cleaned)
        self.assertIn("Real Content Here", cleaned)
        # "Ryan got his start..." might remain as it was appended to the boilerplate in the user example without newline.
        # My regex handles "subscribe to our YouTube channel" which precedes "Ryan".
        
    def test_globalnews(self):
        text = """
By Staff The Canadian Press
Posted December 31, 2025 5:28 pm
1 min read
If you get Global News from Instagram or Facebook - that will be changing. Find out how you can still connect with us.
Hide message barDescrease article font size
Increase article font size
Real Article Content.
"""
        cleaned = article_extractor._postprocess_extracted_text(text, "https://globalnews.ca/news/123")
        self.assertNotIn("By Staff", cleaned)
        self.assertNotIn("Posted December", cleaned)
        self.assertNotIn("1 min read", cleaned)
        self.assertNotIn("If you get Global News", cleaned)
        self.assertNotIn("Hide message bar", cleaned)
        self.assertIn("Real Article Content", cleaned)

    def test_aljazeera(self):
        text = """
Published On 6 Jan 20266 Jan 2026
Click here to share on social media
share2Save
Real Content.
"""
        cleaned = article_extractor._postprocess_extracted_text(text, "https://www.aljazeera.com/news")
        self.assertNotIn("Published On", cleaned)
        self.assertNotIn("Click here to share", cleaned)
        self.assertNotIn("share2Save", cleaned)
        self.assertIn("Real Content", cleaned)

    def test_bbc(self):
        text = """
ShareSave
Ana Faguyon Capitol Hill
ShareSave
Real Content.
"""
        cleaned = article_extractor._postprocess_extracted_text(text, "https://www.bbc.com/news/articles/cgl8y4gx9lyo")
        self.assertNotIn("ShareSave", cleaned)
        self.assertIn("Real Content", cleaned)

    def test_canada(self):
        text = """
Advertisement 1
This advertisement has not loaded yet, but your article continues below.
Author of the article:
Randi MannPublished Jan 06, 2026 • 6 minute read
Join the conversation
Real Content.
Read More
African safari: Sleep under the stars...
Article content
Share this article in your social network
Trending
Latest National Stories
"""
        cleaned = article_extractor._postprocess_extracted_text(text, "https://o.canada.com/travel")
        self.assertNotIn("Advertisement 1", cleaned)
        self.assertNotIn("This advertisement has not loaded", cleaned)
        self.assertNotIn("Author of the article", cleaned)
        self.assertNotIn("Join the conversation", cleaned)
        self.assertNotIn("Read More", cleaned)
        self.assertNotIn("Trending", cleaned)
        self.assertIn("Real Content", cleaned)

    def test_castanet(self):
        text = """
- Child killed by three dogsNova Scotia - 10:07 am
- Urged to approve a pipelineCanada - 10:04 am
Real Content.
"""
        cleaned = article_extractor._postprocess_extracted_text(text, "http://www.castanet.net/rss/page-3.xml")
        self.assertNotIn("Child killed by three dogs", cleaned)
        self.assertIn("Real Content", cleaned)

    def test_bloomberg_page_chrome_removed(self):
        text = """
Australia Says Weighing All Options After Unjustified Tariffs - Bloomberg
===============
[Skip to content](http://www.bloomberg.com/news/articles/2026-02-22/australia-says-weighing-all-options-after-unjustified-tariffs#that-jump-content--default)
[Bloomberg the Company & Its Products The Company & its Products](https://www.bloomberg.com/company/)
US Edition
[](http://www.bloomberg.com/)
[Subscribe](https://www.bloomberg.com/subscriptions?in_source=nav-mobileweb)
[Technology](http://www.bloomberg.com/technology?source=eyebrow)
Australia Says Weighing All Options After Unjustified Tariffs
===============================================================
Gift this article
Add us on Google
[Contact us: Provide news feedback or report an error](https://www.bloomberg.com/help/question/submit-feedback-news-coverage/)
[Confidential tip? Send a tip to our reporters](https://www.bloomberg.com/tips/)
[Site feedback: Take our Survey](https://bmedia.iad1.qualtrics.com/jfe/form/xyz)
By [Angus Whitley](http://www.bloomberg.com/authors/AEaJLmK35vQ/angus-whitley)
February 22, 2026 at 4:18 AM UTC
Save
Translate
Australia's government said it will examine all options after tariffs were imposed.
Officials said they are working closely with their embassy in Washington.
[Before it's here, it's on the Bloomberg Terminal LEARN MORE](https://www.bloomberg.com/professional/solution/bloomberg-terminal-learn-more/)
### More From Bloomberg
[Home](https://www.bloomberg.com/)[BTV+](https://www.bloomberg.com/live)
[Terms of Service](http://www.bloomberg.com/news/articles/2026-02-22/australia-says-weighing-all-options-after-unjustified-tariffs)
2026 Bloomberg L.P. All Rights Reserved.
"""
        cleaned = article_extractor._postprocess_extracted_text(
            text,
            "https://www.bloomberg.com/news/articles/2026-02-22/australia-says-weighing-all-options-after-unjustified-tariffs",
        )
        self.assertIn("Australia's government said it will examine all options", cleaned)
        self.assertIn("Officials said they are working closely", cleaned)
        self.assertNotIn("Skip to content", cleaned)
        self.assertNotIn("Contact us: Provide news feedback", cleaned)
        self.assertNotIn("By [Angus Whitley]", cleaned)
        self.assertNotIn("Save", cleaned)
        self.assertNotIn("Translate", cleaned)
        self.assertNotIn("Before it's here, it's on the Bloomberg Terminal", cleaned)
        self.assertNotIn("More From Bloomberg", cleaned)
        self.assertNotIn("All Rights Reserved", cleaned)

    def test_bloomberg_takeaways_removed(self):
        text = """
Japan's Ruling Party Tax Chief Calls US Tariff Situation Messy - Bloomberg
===============================================================
By [Sakura Murakami](http://www.bloomberg.com/authors/AXPm08xOzlY/sakura-murakami)
February 22, 2026 at 5:56 AM UTC
Save
Translate
### **Takeaways** by Bloomberg AI[Subscribe](http://www.bloomberg.com/subscriptions)
A heavyweight of Japan's ruling Liberal Democratic Party called US tariffs a real mess.
Onodera said the policy response had become chaotic.
[Before it's here, it's on the Bloomberg Terminal LEARN MORE](https://www.bloomberg.com/professional/solution/bloomberg-terminal-learn-more/)
[Home](https://www.bloomberg.com/)[BTV+](https://www.bloomberg.com/live)
"""
        cleaned = article_extractor._postprocess_extracted_text(
            text,
            "https://www.bloomberg.com/news/articles/2026-02-22/japan-s-ruling-party-tax-chief-calls-us-tariff-situation-messy",
        )
        self.assertIn("A heavyweight of Japan's ruling Liberal Democratic Party", cleaned)
        self.assertIn("Onodera said the policy response", cleaned)
        self.assertNotIn("### **Takeaways** by Bloomberg AI", cleaned)
        self.assertNotIn("Save", cleaned)
        self.assertNotIn("Before it's here, it's on the Bloomberg Terminal", cleaned)

    def test_bloomberg_plain_author_and_updated_on_removed(self):
        text = """
NASA Delays Moon Mission to Fix Rocket, Rules Out March Launch - Bloomberg
===============================================================
Gift this article
Add us on Google
[Contact us: Provide news feedback or report an error](https://www.bloomberg.com/help/question/submit-feedback-news-coverage/)
[Confidential tip? Send a tip to our reporters](https://www.bloomberg.com/tips/)
[Site feedback: Take our Survey](https://bmedia.iad1.qualtrics.com/jfe/form/xyz)
By Bloomberg News
February 21, 2026 at 4:33 PM UTC
Updated on
February 21, 2026 at 5:05 PM UTC
Save
Translate
### **Takeaways** by Bloomberg AI[Subscribe](http://www.bloomberg.com/subscriptions)
NASA is preparing to remove the rocket from the launch pad.
The agency said the rollout back to the hangar is needed for repairs.
[Before it's here, it's on the Bloomberg Terminal LEARN MORE](https://www.bloomberg.com/professional/solution/bloomberg-terminal-learn-more/)
"""
        cleaned = article_extractor._postprocess_extracted_text(
            text,
            "https://www.bloomberg.com/news/articles/2026-02-21/nasa-likely-to-delay-moon-mission-after-newly-found-rocket-issue",
        )
        self.assertIn("NASA is preparing to remove the rocket", cleaned)
        self.assertIn("The agency said the rollout back to the hangar", cleaned)
        self.assertNotIn("By Bloomberg News", cleaned)
        self.assertNotIn("Updated on", cleaned)
        self.assertNotIn("Translate", cleaned)
        self.assertNotIn("Takeaways", cleaned)
        self.assertNotIn("Before it's here, it's on the Bloomberg Terminal", cleaned)

    def test_bloomberg_header_block_beyond_140_paragraphs(self):
        filler = "\n".join([f"Menu line {i}" for i in range(160)])
        text = f"""
Vietnam Says Trump Will Let Nation Access Restricted Technology - Bloomberg
===============================================================
{filler}
Gift this article
Add us on Google
[Contact us: Provide news feedback or report an error](https://www.bloomberg.com/help/question/submit-feedback-news-coverage/)
[Confidential tip? Send a tip to our reporters](https://www.bloomberg.com/tips/)
[Site feedback: Take our Survey](https://bmedia.iad1.qualtrics.com/jfe/form/xyz)
By [Nguyen Dieu Tu Uyen](http://www.bloomberg.com/authors/AOZaInH7DNQ/nguyen-dieu-tu-uyen)
February 21, 2026 at 4:31 AM UTC
Save
Translate
Main article paragraph one.
Main article paragraph two.
[Before it's here, it's on the Bloomberg Terminal LEARN MORE](https://www.bloomberg.com/professional/solution/bloomberg-terminal-learn-more/)
"""
        cleaned = article_extractor._postprocess_extracted_text(
            text,
            "https://www.bloomberg.com/news/articles/2026-02-21/vietnam-says-trump-will-let-nation-access-restricted-technology",
        )
        self.assertIn("Main article paragraph one.", cleaned)
        self.assertIn("Main article paragraph two.", cleaned)
        self.assertNotIn("Contact us: Provide news feedback", cleaned)
        self.assertNotIn("By [Nguyen Dieu Tu Uyen]", cleaned)
        self.assertNotIn("Translate", cleaned)
        self.assertNotIn("Before it's here, it's on the Bloomberg Terminal", cleaned)

if __name__ == '__main__':
    unittest.main()
