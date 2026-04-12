"""
MangaUpdates Provider Adapter.

Uses the MangaUpdates public REST API for manga metadata.
API Documentation: https://api.mangaupdates.com
"""
import html
import re
import time
import requests
from typing import Optional, List, Dict, Any

from core.app_logging import app_logger
from .base import BaseProvider, ProviderType, ProviderCredentials, SearchResult, IssueResult
from . import register_provider


@register_provider
class MangaUpdatesProvider(BaseProvider):
    """MangaUpdates metadata provider using the public REST API.

    The MangaUpdates API is public and does not require authentication.
    It is series-based and does not provide reliable per-volume issue lists.
    """

    provider_type = ProviderType.MANGAUPDATES
    display_name = "MangaUpdates"
    requires_auth = False
    auth_fields = []
    rate_limit = 30

    API_BASE = "https://api.mangaupdates.com/v1"
    MAX_CATEGORY_TAGS = 20

    # Class-level rate limiting
    _last_request_time = 0.0

    def __init__(self, credentials: Optional[ProviderCredentials] = None):
        super().__init__(credentials)

    @classmethod
    def _clean_text(cls, value: Any) -> str:
        """Strip HTML, decode entities, and normalize whitespace."""
        text = re.sub(r'<[^>]+>', '', str(value or ""))
        text = html.unescape(text)
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _is_all_caps_token(token: str) -> bool:
        letters = [char for char in token if char.isalpha()]
        return bool(letters) and all(char.isupper() for char in letters)

    @classmethod
    def _normalize_caps_token(cls, token: str) -> str:
        """Title-case all-uppercase alpha chunks while preserving punctuation."""
        return re.sub(
            r"[A-Z][A-Z]+(?:['-][A-Z][A-Z]+)*",
            lambda match: match.group(0).title(),
            token,
        )

    @classmethod
    def _normalize_author_name(cls, name: Any) -> str:
        """Normalize MangaUpdates author casing while preserving short pen names."""
        cleaned = cls._clean_text(name)
        if not cleaned:
            return ""

        tokens = cleaned.split()
        if len(tokens) == 1 and cls._is_all_caps_token(tokens[0]):
            return cleaned

        normalized_tokens = [
            cls._normalize_caps_token(token) if cls._is_all_caps_token(token) else token
            for token in tokens
        ]
        return " ".join(normalized_tokens)

    @classmethod
    def _dedupe_names(cls, names: List[str]) -> List[str]:
        """Deduplicate names case-insensitively while preserving order."""
        unique = []
        seen = set()
        for name in names:
            normalized = cls._normalize_author_name(name)
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(normalized)
        return unique

    @classmethod
    def _extract_creators(cls, authors: Any) -> Dict[str, List[str]]:
        """Split MangaUpdates author credits into writer/penciller lists."""
        if not isinstance(authors, list):
            return {"Writer": [], "Penciller": []}

        writers = []
        pencillers = []
        fallback = []

        for author in authors:
            if not isinstance(author, dict):
                continue

            name = cls._normalize_author_name(author.get("name"))
            if not name:
                continue

            role = cls._clean_text(author.get("type")).lower()
            is_writer = any(term in role for term in ("author", "writer", "story"))
            is_artist = any(term in role for term in ("artist", "art", "illustrator"))

            if is_writer:
                writers.append(name)
            if is_artist:
                pencillers.append(name)
            if not is_writer and not is_artist:
                fallback.append(name)

        writers = cls._dedupe_names(writers)
        pencillers = cls._dedupe_names(pencillers)
        fallback = cls._dedupe_names(fallback)

        if fallback:
            if not writers:
                writers = list(fallback)
            if not pencillers:
                pencillers = list(fallback)

        return {"Writer": writers, "Penciller": pencillers}

    @classmethod
    def _extract_top_category_tags(cls, categories: Any) -> List[str]:
        """Return top-voted category names from a MangaUpdates series payload."""
        if not isinstance(categories, list):
            return []

        ranked = []
        for item in categories:
            if not isinstance(item, dict):
                continue

            name = str(item.get("category", "") or "").strip()
            if not name:
                continue

            try:
                votes = int(item.get("votes", 0) or 0)
            except (TypeError, ValueError):
                votes = 0

            if votes <= 0:
                continue

            ranked.append((votes, name))

        ranked.sort(key=lambda entry: (-entry[0], entry[1].lower()))
        return [name for _, name in ranked[: cls.MAX_CATEGORY_TAGS]]

    def _make_request(self, method: str, endpoint: str, json_data: Dict = None) -> Optional[Dict]:
        """Make an HTTP request to the MangaUpdates API with rate limiting."""
        # Rate limiting: minimum 1.5s between requests
        now = time.monotonic()
        elapsed = now - MangaUpdatesProvider._last_request_time
        if elapsed < 1.5:
            time.sleep(1.5 - elapsed)
        MangaUpdatesProvider._last_request_time = time.monotonic()

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ComicUtils/1.0 (comic-utils metadata provider)",
        }

        url = f"{self.API_BASE}{endpoint}"

        try:
            response = requests.request(
                method,
                url,
                json=json_data,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            app_logger.error(f"MangaUpdates request failed: {e}")
            return None

    def test_connection(self) -> bool:
        """Test connection to MangaUpdates API."""
        try:
            result = self._make_request("POST", "/series/search", {"search": "test", "perpage": 1})
            return result is not None
        except Exception as e:
            app_logger.error(f"MangaUpdates connection test failed: {e}")
            return False

    def search_series(self, query: str, year: Optional[int] = None) -> List[SearchResult]:
        """Search for manga series on MangaUpdates.

        Note: The year parameter is accepted for interface compatibility but
        is NOT used for filtering. MangaUpdates 'year' is the series start
        year, which rarely matches the volume publication year from filenames.
        The API's own relevance ranking is used instead.
        """
        try:
            data = self._make_request("POST", "/series/search", {"search": query, "perpage": 20})
            if not data:
                return []

            results = []
            for item in data.get("results", []):
                record = item.get("record", {})
                series_id = str(record.get("series_id", ""))
                if not series_id:
                    continue

                title = record.get("title", "Unknown Title")
                title = self._clean_text(title)

                # Prefer hit_title (the matched English title) over native title
                hit_title = item.get("hit_title", "")
                alternate_title = None
                if hit_title:
                    hit_title = self._clean_text(hit_title)
                    if hit_title != title:
                        alternate_title = title
                        title = hit_title

                series_year = record.get("year")
                if isinstance(series_year, str):
                    try:
                        series_year = int(series_year)
                    except (ValueError, TypeError):
                        series_year = None

                description = record.get("description", "")
                if description:
                    description = self._clean_text(description)
                    if len(description) > 500:
                        description = description[:500] + "..."

                cover_url = None
                image = record.get("image", {})
                if isinstance(image, dict):
                    cover_url = image.get("url", {}).get("original")

                results.append(SearchResult(
                    provider=self.provider_type,
                    id=series_id,
                    title=title,
                    year=series_year,
                    publisher=None,
                    issue_count=None,
                    cover_url=cover_url,
                    description=description,
                    alternate_title=alternate_title
                ))

            return results
        except Exception as e:
            app_logger.error(f"MangaUpdates search_series failed: {e}")
            return []

    def get_series(self, series_id: str) -> Optional[SearchResult]:
        """Get manga details by MangaUpdates series ID."""
        try:
            data = self._make_request("GET", f"/series/{series_id}")
            if not data:
                return None

            title = data.get("title", "Unknown Title")
            title = self._clean_text(title)

            series_year = data.get("year")
            if isinstance(series_year, str):
                try:
                    series_year = int(series_year)
                except (ValueError, TypeError):
                    series_year = None

            description = data.get("description", "")
            if description:
                description = self._clean_text(description)

            cover_url = None
            image = data.get("image", {})
            if isinstance(image, dict):
                cover_url = image.get("url", {}).get("original")

            # Extract publisher from publishers array
            publisher = None
            publishers = data.get("publishers", [])
            if publishers:
                for pub in publishers:
                    pub_name = self._clean_text(pub.get("publisher_name"))
                    if pub_name:
                        publisher = pub_name
                        break

            return SearchResult(
                provider=self.provider_type,
                id=series_id,
                title=title,
                year=series_year,
                publisher=publisher,
                issue_count=None,
                cover_url=cover_url,
                description=description
            )
        except Exception as e:
            app_logger.error(f"MangaUpdates get_series failed: {e}")
            return None

    def get_issues(self, series_id: str) -> List[IssueResult]:
        """
        MangaUpdates does not provide a reliable per-volume list.

        We intentionally do not synthesize volumes from latest_chapter,
        because that field reflects chapter progress, not volume count.
        """
        try:
            return []
        except Exception as e:
            app_logger.error(f"MangaUpdates get_issues failed: {e}")
            return []

    def get_issue(self, issue_id: str) -> Optional[IssueResult]:
        """
        Get volume details by synthetic ID.

        Parses the synthetic ID format "series_id-volume_number".
        """
        try:
            if "-" not in issue_id:
                return None

            parts = issue_id.rsplit("-", 1)
            if len(parts) != 2:
                return None

            series_id, vol_num = parts

            series = self.get_series(series_id)
            if not series:
                return None

            return IssueResult(
                provider=self.provider_type,
                id=issue_id,
                series_id=series_id,
                issue_number=vol_num,
                title=None,
                cover_date=None,
                store_date=None,
                cover_url=series.cover_url,
                summary=None
            )
        except Exception as e:
            app_logger.error(f"MangaUpdates get_issue failed: {e}")
            return None

    def get_issue_metadata(self, series_id: str, issue_number: str, preferred_title: str = None, alternate_title: str = None) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific volume in a series.

        Follows the ComicTagger approach:
        1. Fetch series info from the API for title, authors, genres, etc.
        2. Inject the volume number from the filename (passed as issue_number).
        3. The API is series-based — there are no per-volume endpoints.
        """
        try:
            # Single API call for all series data
            detail = self._make_request("GET", f"/series/{series_id}")
            if not detail:
                return None

            title = detail.get("title", "Unknown Title")
            title = self._clean_text(title)

            # Prefix with 'v' for volume (manga convention)
            volume_number = f"v{issue_number}" if not issue_number.startswith('v') else issue_number

            # Parse year
            series_year = detail.get("year")
            if isinstance(series_year, str):
                try:
                    series_year = int(series_year)
                except (ValueError, TypeError):
                    series_year = None

            # Clean description
            description = detail.get("description", "")
            if description:
                description = self._clean_text(description)

            # Extract publisher from publishers array
            publisher = None
            publishers = detail.get("publishers", [])
            if publishers:
                for pub in publishers:
                    pub_name = self._clean_text(pub.get("publisher_name"))
                    if pub_name:
                        publisher = pub_name
                        break

            # Use preferred_title (e.g. hit_title from search) if provided
            series_name = preferred_title if preferred_title else title

            # Build metadata
            metadata = {
                "Series": series_name,
                "Number": volume_number,
                "Web": f"https://www.mangaupdates.com/series/{series_id}",
            }

            # Year
            if series_year:
                metadata["Year"] = series_year

            # Summary (cleaned)
            if description:
                metadata["Summary"] = description

            # Publisher
            if publisher:
                metadata["Publisher"] = publisher

            # Authors -> both Writer and Penciller (mangaka convention)
            creators = self._extract_creators(detail.get("authors"))
            if creators["Writer"]:
                metadata["Writer"] = ", ".join(creators["Writer"])
            if creators["Penciller"]:
                metadata["Penciller"] = ", ".join(creators["Penciller"])

            # Genres
            genres = detail.get("genres", [])
            if genres:
                genre_names = []
                for genre in genres:
                    gname = genre.get("genre") if isinstance(genre, dict) else str(genre)
                    if gname:
                        genre_names.append(self._clean_text(gname))
                if genre_names:
                    metadata["Genre"] = ", ".join(genre_names)

            # Categories -> ComicInfo Tags (ranked by MU votes)
            category_tags = self._extract_top_category_tags(detail.get("categories"))
            if category_tags:
                metadata["Tags"] = ", ".join(category_tags)

            # Alternate series: start with the native title if we used a preferred title,
            # then append associated titles from the API, deduplicating
            alt_titles = []
            seen = set()

            # Add alternate_title (native title when hit_title was preferred)
            native_for_alt = alternate_title if alternate_title else (title if title != series_name else None)
            if native_for_alt:
                alt_titles.append(native_for_alt)
                seen.add(native_for_alt.lower())

            associated = detail.get("associated", [])
            if associated:
                for assoc in associated:
                    at = assoc.get("title") if isinstance(assoc, dict) else str(assoc)
                    if at:
                        at = self._clean_text(at)
                        if at.lower() not in seen:
                            alt_titles.append(at)
                            seen.add(at.lower())
            if alt_titles:
                metadata["AlternateSeries"] = "; ".join(alt_titles)

            # Manga type detection
            series_type = detail.get("type", "")
            if isinstance(series_type, str) and series_type.lower() in ("manga", "manhwa", "manhua"):
                metadata["Manga"] = "Yes"

            # Status in Notes
            status = detail.get("status", "")
            if status:
                metadata["Notes"] = f"Status: {status}. Metadata from MangaUpdates."
            else:
                metadata["Notes"] = "Metadata from MangaUpdates."

            return metadata
        except Exception as e:
            app_logger.error(f"MangaUpdates get_issue_metadata failed: {e}")
            return None

    def to_comicinfo(self, issue: IssueResult, series: Optional[SearchResult] = None) -> Dict[str, Any]:
        """Convert MangaUpdates data to ComicInfo.xml fields."""
        try:
            kwargs = {}
            if series:
                kwargs['preferred_title'] = series.title
                kwargs['alternate_title'] = getattr(series, 'alternate_title', None)
            return self.get_issue_metadata(issue.series_id, issue.issue_number, **kwargs) or {}
        except Exception as e:
            app_logger.error(f"MangaUpdates to_comicinfo failed: {e}")
            return {}
