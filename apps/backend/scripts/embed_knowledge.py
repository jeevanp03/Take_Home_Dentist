"""Embed dental knowledge into ChromaDB for RAG retrieval.

Usage (from apps/backend/):
    python -m scripts.embed_knowledge                # default: cache if available, fetch if not
    python -m scripts.embed_knowledge --refresh      # re-embed from cached responses only
    python -m scripts.embed_knowledge --repull        # re-fetch APIs, update cache, re-embed
    python -m scripts.embed_knowledge --practice-only # embed local markdown only, skip APIs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import requests
from tqdm import tqdm

from src.vector.chroma_client import get_knowledge_collection, reset_collections

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("embed_knowledge")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_KNOWLEDGE_DIR = _BACKEND_DIR / "data" / "knowledge"
_CACHE_DIR = _KNOWLEDGE_DIR / "cache"

_PRACTICE_FILES = [
    "office_info.md",
    "insurance_policy.md",
    "procedures.md",
    "emergency_protocol.md",
    "faq.md",
    "family_booking.md",
]

# ---------------------------------------------------------------------------
# PubMed topics (20)
# ---------------------------------------------------------------------------

PUBMED_TOPICS: list[str] = [
    "dental caries",
    "periodontal disease",
    "dental implants",
    "root canal treatment",
    "tooth extraction",
    "dental anxiety",
    "teeth whitening",
    "dental crowns",
    "dental fillings",
    "oral cancer screening",
    "dental X-ray safety",
    "fluoride treatment",
    "dental sealants",
    "bruxism treatment",
    "dental pain management",
    "pregnancy and dental care",
    "TMJ TMD",
    "dry socket",
    "diabetes and oral health",
    "geriatric dentistry",
]

# ---------------------------------------------------------------------------
# MedlinePlus topics (19)
# ---------------------------------------------------------------------------

MEDLINEPLUS_TOPICS: list[str] = [
    "dental health",
    "gum disease",
    "tooth decay",
    "dental implants",
    "root canal",
    "tooth extraction",
    "dental anxiety",
    "teeth whitening",
    "oral cancer",
    "fluoride",
    "bruxism",
    "wisdom teeth",
    "dentures",
    "mouth sores",
    "toothache",
    "TMJ disorders",
    "pregnancy dental care",
    "bad breath",
    "dry mouth",
]

# ---------------------------------------------------------------------------
# Source priority (higher = keep during dedup)
# ---------------------------------------------------------------------------

_SOURCE_PRIORITY = {"practice": 3, "medlineplus": 2, "pubmed": 1}

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    """Minimal HTML tag stripper using the stdlib HTMLParser."""

    def __init__(self) -> None:
        super().__init__()
        self._buf = StringIO()

    def handle_data(self, data: str) -> None:
        self._buf.write(data)

    def get_text(self) -> str:
        return self._buf.getvalue()


def _strip_html(text: str) -> str:
    """Remove HTML tags from *text* using Python's built-in HTMLParser."""
    stripper = _HTMLStripper()
    stripper.feed(text)
    return stripper.get_text()


def _stable_id(*parts: str) -> str:
    """SHA-256 hex digest of concatenated parts — deterministic chunk ID."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _respect_rate_limit() -> None:
    """Sleep 0.5 s between external API calls."""
    time.sleep(0.5)


# =========================================================================
# 1) Practice markdown chunking
# =========================================================================


def _parse_practice_file(filepath: Path) -> list[dict[str, Any]]:
    """Split a markdown file by ## headers and return chunk dicts."""
    text = filepath.read_text(encoding="utf-8")
    filename_topic = filepath.stem  # e.g. "office_info"

    # Extract document title from first # line
    doc_title = filename_topic.replace("_", " ").title()
    title_match = re.match(r"^#\s+(.+)", text, re.MULTILINE)
    if title_match:
        doc_title = title_match.group(1).strip()

    # Split on ## headers
    sections = re.split(r"(?=^##\s)", text, flags=re.MULTILINE)
    chunks: list[dict[str, Any]] = []

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extract section header
        header_match = re.match(r"^##\s+(.+)", section)
        if header_match:
            section_title = header_match.group(1).strip()
        else:
            section_title = "Introduction"

        full_title = f"{doc_title} — {section_title}"
        # Prepend title context to chunk text
        chunk_text = f"{full_title}\n\n{section}"

        chunks.append(
            {
                "id": _stable_id("practice", filename_topic, section_title),
                "document": chunk_text,
                "metadata": {
                    "source": "practice",
                    "topic": filename_topic,
                    "title": full_title,
                },
            }
        )

    return chunks


def load_practice_chunks() -> list[dict[str, Any]]:
    """Load and chunk all practice markdown files."""
    all_chunks: list[dict[str, Any]] = []
    for fname in _PRACTICE_FILES:
        fpath = _KNOWLEDGE_DIR / fname
        if not fpath.exists():
            logger.warning("Practice file missing: %s", fpath)
            continue
        chunks = _parse_practice_file(fpath)
        all_chunks.extend(chunks)
        logger.info("  %s: %d chunks", fname, len(chunks))
    return all_chunks


# =========================================================================
# 2) PubMed ingestion
# =========================================================================

_PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _pubmed_search(topic: str, retmax: int = 8) -> list[str]:
    """Return up to retmax PubMed IDs for a topic."""
    params = {
        "db": "pubmed",
        "term": topic,
        "retmax": retmax,
        "retmode": "json",
        "sort": "relevance",
    }
    resp = requests.get(_PUBMED_SEARCH_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("esearchresult", {}).get("idlist", [])


def _pubmed_fetch_abstracts(pmids: list[str]) -> list[dict[str, str]]:
    """Fetch abstracts for a list of PubMed IDs. Returns list of dicts."""
    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
    }
    resp = requests.get(_PUBMED_FETCH_URL, params=params, timeout=30)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    articles: list[dict[str, str]] = []

    for article_el in root.findall(".//PubmedArticle"):
        # Title
        title_el = article_el.find(".//ArticleTitle")
        title = title_el.text if title_el is not None and title_el.text else ""

        # Abstract — may have multiple AbstractText elements
        abstract_parts: list[str] = []
        for abs_el in article_el.findall(".//AbstractText"):
            label = abs_el.get("Label", "")
            # Include child text (handles inline tags like <i>)
            text = "".join(abs_el.itertext())
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
        abstract = " ".join(abstract_parts)

        # PMID
        pmid_el = article_el.find(".//PMID")
        pmid = pmid_el.text if pmid_el is not None and pmid_el.text else ""

        articles.append({"title": title, "abstract": abstract, "pmid": pmid})

    return articles


def _quality_filter(articles: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only articles where title or abstract contains 'dental' or 'oral'."""
    filtered: list[dict[str, str]] = []
    for art in articles:
        combined = (art["title"] + " " + art["abstract"]).lower()
        if "dental" in combined or "oral" in combined:
            filtered.append(art)
    return filtered


def fetch_pubmed_topic(topic: str) -> list[dict[str, str]]:
    """Search + fetch + filter for one PubMed topic."""
    pmids = _pubmed_search(topic, retmax=8)
    _respect_rate_limit()
    if not pmids:
        return []
    articles = _pubmed_fetch_abstracts(pmids)
    _respect_rate_limit()
    return _quality_filter(articles)


def load_pubmed_chunks(use_cache: bool, repull: bool) -> list[dict[str, Any]]:
    """Fetch PubMed abstracts (with caching) and return chunk dicts."""
    cache_file = _CACHE_DIR / "pubmed_cache.json"
    cached_data: dict[str, list[dict[str, str]]] = {}

    if cache_file.exists() and not repull:
        cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
        if use_cache:
            logger.info("Using cached PubMed data (%d topics)", len(cached_data))

    all_chunks: list[dict[str, Any]] = []
    topics_to_fetch = []

    for topic in PUBMED_TOPICS:
        if topic in cached_data and not repull:
            pass  # will use cache
        else:
            topics_to_fetch.append(topic)

    # Fetch missing/stale topics
    if topics_to_fetch:
        logger.info("Fetching PubMed abstracts for %d topics ...", len(topics_to_fetch))
        for topic in tqdm(topics_to_fetch, desc="PubMed fetch", unit="topic"):
            try:
                articles = fetch_pubmed_topic(topic)
                cached_data[topic] = articles
                logger.info("  %s: %d articles after filter", topic, len(articles))
            except Exception:
                logger.warning("PubMed fetch failed for '%s', skipping", topic, exc_info=True)

        # Update cache
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cached_data, indent=2), encoding="utf-8")
        logger.info("PubMed cache updated at %s", cache_file)

    # Convert cached data to chunks (deduplicate by PMID across topics)
    seen_pmids: set[str] = set()
    for topic, articles in cached_data.items():
        for art in articles:
            if not art.get("abstract"):
                continue
            pmid = art.get("pmid", "")
            if pmid and pmid in seen_pmids:
                continue  # Same article already added from another topic
            if pmid:
                seen_pmids.add(pmid)
            doc_text = f"{art['title']}\n\n{art['abstract']}"
            all_chunks.append(
                {
                    "id": _stable_id("pubmed", topic, pmid, art["title"]),
                    "document": doc_text,
                    "metadata": {
                        "source": "pubmed",
                        "topic": topic,
                        "title": art["title"],
                        "pmid": pmid,
                    },
                }
            )

    return all_chunks


# =========================================================================
# 3) MedlinePlus ingestion
# =========================================================================

_MEDLINEPLUS_URL = "https://wsearch.nlm.nih.gov/ws/query"

# Curated fallback summaries in case the API is unreliable
_MEDLINEPLUS_FALLBACK: dict[str, str] = {
    "dental health": (
        "Good dental health is important for your overall well-being. Brush your "
        "teeth twice a day with fluoride toothpaste, floss daily, and visit your "
        "dentist regularly for checkups and cleanings. A balanced diet and limiting "
        "sugary snacks also help prevent cavities and gum disease."
    ),
    "gum disease": (
        "Gum disease (periodontal disease) is an infection of the tissues that hold "
        "your teeth in place. It is typically caused by poor brushing and flossing "
        "habits that allow plaque to build up. In advanced stages, it can lead to "
        "sore, bleeding gums, painful chewing, and tooth loss."
    ),
    "tooth decay": (
        "Tooth decay (cavities) is damage to a tooth's surface caused by acids "
        "produced by bacteria in plaque. Risk factors include frequent snacking, "
        "sipping sugary drinks, and not cleaning teeth well. Treatment includes "
        "fluoride, fillings, crowns, and root canals."
    ),
    "dental implants": (
        "Dental implants are metal posts surgically placed into the jawbone beneath "
        "your gums. They provide a permanent base for replacement teeth. Implants "
        "look and feel like natural teeth and can last a lifetime with proper care."
    ),
    "root canal": (
        "Root canal treatment repairs and saves a badly infected or damaged tooth "
        "instead of removing it. The procedure involves removing the damaged pulp, "
        "cleaning and shaping the inside of the root canal, then filling and sealing "
        "the space. Modern root canals are relatively comfortable."
    ),
    "tooth extraction": (
        "Tooth extraction is the removal of a tooth from its socket in the bone. "
        "Extractions are performed for many reasons including severe decay, infection, "
        "crowding, and impacted wisdom teeth. After extraction, follow your dentist's "
        "instructions for care to prevent dry socket and infection."
    ),
    "dental anxiety": (
        "Dental anxiety or fear is very common and can range from mild nervousness to "
        "severe dental phobia. Strategies to manage it include discussing fears with "
        "your dentist, relaxation techniques, distraction, sedation dentistry, and "
        "cognitive behavioral therapy."
    ),
    "teeth whitening": (
        "Teeth whitening lightens teeth and removes stains and discoloration. Options "
        "include in-office bleaching, at-home trays from your dentist, and over-the-counter "
        "products. Results vary and whitening is not permanent. Consult your dentist "
        "before whitening to ensure it is safe for your teeth."
    ),
    "oral cancer": (
        "Oral cancer includes cancers of the lips, tongue, cheeks, floor of the mouth, "
        "and hard palate. Risk factors include tobacco use, heavy alcohol use, HPV, and "
        "excessive sun exposure. Early detection through regular dental screenings "
        "significantly improves outcomes."
    ),
    "fluoride": (
        "Fluoride is a mineral that helps prevent tooth decay by making teeth more "
        "resistant to acid attacks from plaque bacteria and sugars. It can also "
        "reverse early decay. Fluoride is found in many water supplies, toothpastes, "
        "and mouth rinses. Professional fluoride treatments are available at the dentist."
    ),
    "bruxism": (
        "Bruxism is the habit of grinding, gnashing, or clenching your teeth, often "
        "during sleep. It can cause jaw pain, headaches, and worn or damaged teeth. "
        "Treatment may include mouth guards, stress management, and treating underlying "
        "sleep disorders."
    ),
    "wisdom teeth": (
        "Wisdom teeth are the third set of molars that usually come in between ages 17 "
        "and 25. They may need to be removed if they are impacted, cause pain, crowd "
        "other teeth, or lead to infection. Not everyone needs their wisdom teeth removed."
    ),
    "dentures": (
        "Dentures are removable replacements for missing teeth and surrounding tissues. "
        "Complete dentures replace all teeth, while partial dentures replace some teeth. "
        "Modern dentures look natural and are more comfortable than ever. Proper care "
        "includes daily cleaning and regular dental checkups."
    ),
    "mouth sores": (
        "Mouth sores can be caused by infections, irritation, injuries, or other "
        "conditions. Common types include canker sores, cold sores, and oral thrush. "
        "Most mouth sores heal on their own within 10-14 days. See a dentist if sores "
        "persist longer than two weeks."
    ),
    "toothache": (
        "A toothache is pain in or around a tooth. Common causes include cavities, "
        "cracked teeth, gum disease, and exposed roots. Home remedies like salt water "
        "rinses and over-the-counter pain relievers can provide temporary relief, but "
        "you should see a dentist to treat the underlying cause."
    ),
    "TMJ disorders": (
        "TMJ disorders affect the temporomandibular joint that connects your jaw to "
        "your skull. Symptoms include jaw pain, difficulty chewing, clicking sounds, "
        "and locking of the jaw. Treatment ranges from self-care practices and physical "
        "therapy to medications and, in rare cases, surgery."
    ),
    "pregnancy dental care": (
        "Dental care is important during pregnancy. Hormonal changes can increase the "
        "risk of gum disease, which has been linked to premature birth. Routine dental "
        "cleanings and necessary treatments are safe during pregnancy. Tell your dentist "
        "you are pregnant so they can adjust care accordingly."
    ),
    "bad breath": (
        "Bad breath (halitosis) can be caused by foods, poor dental hygiene, dry mouth, "
        "tobacco, or medical conditions. Prevention includes brushing twice daily, "
        "flossing, cleaning the tongue, staying hydrated, and regular dental visits. "
        "Persistent bad breath may indicate gum disease or other health issues."
    ),
    "dry mouth": (
        "Dry mouth (xerostomia) occurs when salivary glands do not produce enough "
        "saliva. It can be caused by medications, medical conditions, radiation therapy, "
        "or dehydration. Dry mouth increases the risk of cavities and gum disease. "
        "Treatment includes sipping water, chewing sugar-free gum, and saliva substitutes."
    ),
}


def _fetch_medlineplus_topic(topic: str) -> list[dict[str, str]]:
    """Fetch MedlinePlus content for a topic via the web search API."""
    params = {"db": "healthTopics", "term": topic}
    resp = requests.get(_MEDLINEPLUS_URL, params=params, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    results: list[dict[str, str]] = []

    # The NLM search API returns <document> elements with <content> children
    for doc_el in root.findall(".//{http://nlm.nih.gov/medlineplus/ws/}document"):
        title = ""
        summary = ""
        for content_el in doc_el.findall("{http://nlm.nih.gov/medlineplus/ws/}content"):
            name = content_el.get("name", "")
            text = "".join(content_el.itertext()).strip()
            if name == "title":
                title = text
            elif name == "FullSummary" or name == "snippet":
                summary = text

        if summary:
            # Strip HTML tags from summary
            clean_summary = _strip_html(summary)
            results.append({"title": title or topic.title(), "text": clean_summary})

    return results


def _chunk_medlineplus_text(
    topic: str, title: str, text: str
) -> list[dict[str, Any]]:
    """Split MedlinePlus text by paragraph/section breaks into chunks."""
    # Split on double newlines or heading patterns
    paragraphs = re.split(r"\n{2,}|\r\n{2,}", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip() and len(p.strip()) > 30]

    if not paragraphs:
        # If no good splits, use the whole text as one chunk
        paragraphs = [text.strip()]

    chunks: list[dict[str, Any]] = []
    for i, para in enumerate(paragraphs):
        section_title = f"{title} — Section {i + 1}" if len(paragraphs) > 1 else title
        chunk_text = f"{topic.title()} — {section_title}\n\n{para}"
        chunks.append(
            {
                "id": _stable_id("medlineplus", topic, str(i)),
                "document": chunk_text,
                "metadata": {
                    "source": "medlineplus",
                    "topic": topic,
                    "title": section_title,
                },
            }
        )
    return chunks


def load_medlineplus_chunks(use_cache: bool, repull: bool) -> list[dict[str, Any]]:
    """Fetch MedlinePlus content (with caching and fallback) and return chunk dicts."""
    cache_file = _CACHE_DIR / "medlineplus_cache.json"
    cached_data: dict[str, list[dict[str, str]]] = {}

    if cache_file.exists() and not repull:
        cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
        if use_cache:
            logger.info("Using cached MedlinePlus data (%d topics)", len(cached_data))

    topics_to_fetch = []
    for topic in MEDLINEPLUS_TOPICS:
        if topic in cached_data and not repull:
            pass
        else:
            topics_to_fetch.append(topic)

    if topics_to_fetch:
        logger.info("Fetching MedlinePlus content for %d topics ...", len(topics_to_fetch))
        for topic in tqdm(topics_to_fetch, desc="MedlinePlus fetch", unit="topic"):
            try:
                results = _fetch_medlineplus_topic(topic)
                _respect_rate_limit()
                if results:
                    cached_data[topic] = results
                    logger.info("  %s: %d results", topic, len(results))
                else:
                    # Fallback to curated summary
                    fallback = _MEDLINEPLUS_FALLBACK.get(topic)
                    if fallback:
                        cached_data[topic] = [
                            {"title": topic.title(), "text": fallback}
                        ]
                        logger.info("  %s: using curated fallback", topic)
                    else:
                        logger.warning("  %s: no results and no fallback", topic)
            except Exception:
                logger.warning(
                    "MedlinePlus fetch failed for '%s', using fallback", topic, exc_info=True
                )
                fallback = _MEDLINEPLUS_FALLBACK.get(topic)
                if fallback:
                    cached_data[topic] = [{"title": topic.title(), "text": fallback}]

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cached_data, indent=2), encoding="utf-8")
        logger.info("MedlinePlus cache updated at %s", cache_file)

    # Convert to chunks
    all_chunks: list[dict[str, Any]] = []
    for topic, results in cached_data.items():
        for result in results:
            chunks = _chunk_medlineplus_text(
                topic, result.get("title", topic.title()), result["text"]
            )
            all_chunks.extend(chunks)

    return all_chunks


# =========================================================================
# 4) Deduplication
# =========================================================================


def deduplicate_chunks(
    collection: Any,
    chunks: list[dict[str, Any]],
    threshold: float = 0.92,
) -> int:
    """Remove near-duplicate chunks within each topic cluster.

    Uses embed_batch() to get raw vectors, computes pairwise cosine similarity
    within each topic, and removes lower-priority duplicates from the collection.

    Returns the number of duplicates removed.
    """
    logger.info("Running post-ingestion deduplication (threshold=%.2f) ...", threshold)

    # Group chunk IDs by topic
    topic_groups: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        topic = chunk["metadata"]["topic"]
        topic_groups.setdefault(topic, []).append(chunk)

    ids_to_remove: set[str] = set()

    for topic, group in tqdm(topic_groups.items(), desc="Dedup topics", unit="topic"):
        if len(group) < 2:
            continue

        # Get embeddings from ChromaDB (already computed at add() time)
        chunk_ids = [c["id"] for c in group]
        result = collection.get(ids=chunk_ids, include=["embeddings"])
        embeddings = result.get("embeddings")

        if embeddings is None or len(embeddings) == 0:
            continue

        vecs = np.array(embeddings, dtype=np.float32)
        # Normalize for fast cosine via dot product
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs_normed = vecs / norms

        # Pairwise cosine similarity matrix
        sim_matrix = vecs_normed @ vecs_normed.T

        for i in range(len(group)):
            if group[i]["id"] in ids_to_remove:
                continue
            for j in range(i + 1, len(group)):
                if group[j]["id"] in ids_to_remove:
                    continue
                if sim_matrix[i, j] > threshold:
                    # Remove the lower-priority one
                    pri_i = _SOURCE_PRIORITY.get(group[i]["metadata"]["source"], 0)
                    pri_j = _SOURCE_PRIORITY.get(group[j]["metadata"]["source"], 0)
                    if pri_i >= pri_j:
                        ids_to_remove.add(group[j]["id"])
                    else:
                        ids_to_remove.add(group[i]["id"])

    # Remove from ChromaDB collection
    if ids_to_remove:
        remove_list = list(ids_to_remove)
        # ChromaDB delete in batches of 500
        for start in range(0, len(remove_list), 500):
            batch = remove_list[start : start + 500]
            collection.delete(ids=batch)
        logger.info("Removed %d duplicate chunks from collection", len(ids_to_remove))
    else:
        logger.info("No duplicates found")

    return len(ids_to_remove)


# =========================================================================
# 5) Main embedding pipeline
# =========================================================================


def _add_chunks_to_collection(
    collection: Any, chunks: list[dict[str, Any]], desc: str
) -> int:
    """Add chunks to ChromaDB collection in batches. Returns count added."""
    if not chunks:
        return 0

    batch_size = 100
    added = 0

    for start in tqdm(
        range(0, len(chunks), batch_size),
        desc=f"Embedding {desc}",
        unit="batch",
    ):
        batch = chunks[start : start + batch_size]
        collection.add(
            ids=[c["id"] for c in batch],
            documents=[c["document"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
        )
        added += len(batch)

    return added


def run(
    refresh: bool = False,
    repull: bool = False,
    practice_only: bool = False,
) -> None:
    """Execute the full embedding pipeline."""
    logger.info("=" * 60)
    logger.info("Starting knowledge embedding pipeline")
    logger.info(
        "  flags: refresh=%s, repull=%s, practice_only=%s",
        refresh,
        repull,
        practice_only,
    )
    logger.info("=" * 60)

    # Reset collections for a clean embed
    logger.warning("Resetting ChromaDB collections — all existing embeddings will be deleted")
    reset_collections()
    collection = get_knowledge_collection()

    all_chunks: list[dict[str, Any]] = []

    # --- Practice markdown ---
    logger.info("Loading practice markdown files ...")
    practice_chunks = load_practice_chunks()
    logger.info("Practice: %d chunks total", len(practice_chunks))
    _add_chunks_to_collection(collection, practice_chunks, "practice")
    all_chunks.extend(practice_chunks)

    if not practice_only:
        # --- PubMed ---
        logger.info("Loading PubMed abstracts ...")
        use_cache = not repull  # use cache unless repull
        pubmed_chunks = load_pubmed_chunks(use_cache=use_cache, repull=repull)
        logger.info("PubMed: %d chunks total", len(pubmed_chunks))
        _add_chunks_to_collection(collection, pubmed_chunks, "pubmed")
        all_chunks.extend(pubmed_chunks)

        # --- MedlinePlus ---
        logger.info("Loading MedlinePlus content ...")
        medline_chunks = load_medlineplus_chunks(use_cache=use_cache, repull=repull)
        logger.info("MedlinePlus: %d chunks total", len(medline_chunks))
        _add_chunks_to_collection(collection, medline_chunks, "medlineplus")
        all_chunks.extend(medline_chunks)

    # --- Deduplication ---
    if len(all_chunks) > 1:
        removed = deduplicate_chunks(collection, all_chunks)
    else:
        removed = 0

    # --- Summary ---
    final_count = collection.count()
    logger.info("=" * 60)
    logger.info("Embedding pipeline complete")
    logger.info("  Total chunks embedded: %d", len(all_chunks))
    logger.info("  Duplicates removed:    %d", removed)
    logger.info("  Final collection size: %d", final_count)
    logger.info("=" * 60)


# =========================================================================
# CLI
# =========================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed dental knowledge into ChromaDB for RAG retrieval."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-embed from cached API responses (skip API calls).",
    )
    parser.add_argument(
        "--repull",
        action="store_true",
        help="Re-fetch from APIs, update cache, then re-embed.",
    )
    parser.add_argument(
        "--practice-only",
        action="store_true",
        help="Only embed local practice markdown files (skip external APIs).",
    )
    args = parser.parse_args()

    if args.refresh and args.repull:
        logger.error("Cannot use both --refresh and --repull. Pick one.")
        sys.exit(1)

    run(
        refresh=args.refresh,
        repull=args.repull,
        practice_only=args.practice_only,
    )


if __name__ == "__main__":
    main()
