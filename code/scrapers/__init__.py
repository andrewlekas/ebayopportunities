from .ebay import EbayScraper
from .goldin import GoldinScraper
from .fanatics_collect import FanaticsCollectScraper
from .heritage import HeritageScraper
from .yahoo_jp import YahooJpScraper

ALL_SCRAPERS = {
    "ebay": EbayScraper,
    "goldin": GoldinScraper,
    "fanatics_collect": FanaticsCollectScraper,
    "heritage": HeritageScraper,
    "yahoo_jp": YahooJpScraper,
}
