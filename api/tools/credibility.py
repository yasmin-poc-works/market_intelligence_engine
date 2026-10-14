"""Source credibility score in [0, 1]: domain tier, recency decay and byline.

score = 0.6 * domain_tier + 0.25 * recency + 0.15 * byline

Recency halves every two years; an undated source gets a low fixed recency so it cannot
outrank a dated one by default.
"""
from datetime import UTC, datetime

W_DOMAIN, W_RECENCY, W_BYLINE = 0.6, 0.25, 0.15
HALF_LIFE_DAYS = 730.0
UNDATED_RECENCY = 0.3

TIER_HIGH, TIER_MID, TIER_DEFAULT, TIER_LOW = 0.95, 0.75, 0.5, 0.2

_HIGH_SUFFIXES = (".gov", ".edu", ".mil", ".int", ".gov.uk", ".ac.uk", ".gov.au", ".edu.au", ".europa.eu")
_HIGH_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com", "nature.com", "science.org",
    "sciencedirect.com", "arxiv.org", "sec.gov", "worldbank.org", "imf.org", "oecd.org", "who.int",
    "economist.com", "nih.gov", "iea.org",
}
_MID_DOMAINS = {
    "bbc.com", "bbc.co.uk", "cnbc.com", "forbes.com", "techcrunch.com", "theverge.com", "wired.com",
    "businessinsider.com", "hbr.org", "mckinsey.com", "bcg.com", "bain.com", "deloitte.com", "pwc.com",
    "kpmg.com", "gartner.com", "forrester.com", "idc.com", "statista.com", "crunchbase.com",
    "pitchbook.com", "theguardian.com", "nytimes.com", "washingtonpost.com", "cnn.com", "zdnet.com",
    "venturebeat.com", "fortune.com", "axios.com",
}
_LOW_DOMAINS = {
    "medium.com", "blogspot.com", "wordpress.com", "substack.com", "reddit.com", "quora.com", "x.com",
    "twitter.com", "facebook.com", "pinterest.com", "tumblr.com", "youtube.com", "tiktok.com",
    "instagram.com", "yahoo.com", "answers.com",
}


def _matches(domain: str, names: set[str]) -> bool:
    return any(domain == n or domain.endswith("." + n) for n in names)


def domain_tier(domain: str) -> float:
    d = domain.lower().removeprefix("www.")
    if d.endswith(_HIGH_SUFFIXES) or _matches(d, _HIGH_DOMAINS):
        return TIER_HIGH
    if _matches(d, _LOW_DOMAINS):
        return TIER_LOW
    if _matches(d, _MID_DOMAINS):
        return TIER_MID
    return TIER_DEFAULT


def recency_factor(published: datetime | None, now: datetime | None = None) -> float:
    if published is None:
        return UNDATED_RECENCY
    now = now or datetime.now(UTC)
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    age_days = max(0.0, (now - published).total_seconds() / 86400)
    return 0.5 ** (age_days / HALF_LIFE_DAYS) if age_days else 1.0


def credibility_score(
    domain: str, published: datetime | None, byline: str | None, now: datetime | None = None
) -> float:
    score = (
        W_DOMAIN * domain_tier(domain)
        + W_RECENCY * recency_factor(published, now)
        + W_BYLINE * (1.0 if byline and byline.strip() else 0.0)
    )
    return round(min(1.0, max(0.0, score)), 2)

