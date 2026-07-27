from .ebay import EbayScraper
from .goldin import GoldinScraper
from .fanatics_collect import FanaticsCollectScraper
from .alt import AltScraper
from .heritage import HeritageScraper
from .pristine import PristineScraper
from .yahoo_jp import YahooJpScraper

ALL_SCRAPERS = {
    "ebay": EbayScraper,
    "goldin": GoldinScraper,
    "fanatics_collect": FanaticsCollectScraper,
    "alt": AltScraper,
    "heritage": HeritageScraper,
    "pristine": PristineScraper,
    "yahoo_jp": YahooJpScraper,
}
