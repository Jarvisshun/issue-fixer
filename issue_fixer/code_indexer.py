"""Hybrid RAG code indexer: vector search (ChromaDB) + BM25 keyword search + RRF reranking."""

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import chromadb
import tiktoken

from .config import config


# ─── BM25 Implementation ────────────────────────────────────────────────────

class BM25:
    """Okapi BM25 scorer for keyword-based code search.

    Complements vector search by excelling at exact identifier matches,
    function names, error messages, and technical terms.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_tokens: list[list[str]] = []
        self.doc_freqs: list[Counter] = []
        self.doc_lengths: list[int] = []
        self.avg_dl: float = 0.0
        self.df: Counter = Counter()  # document frequency per term
        self.num_docs: int = 0

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize code: split on non-alphanumeric, lowercase, keep underscores."""
        tokens = re.findall(r'[a-zA-Z_]\w*', text.lower())
        return tokens

    def fit(self, documents: list[str]):
        """Build BM25 index from document texts."""
        self.corpus_tokens = []
        self.doc_freqs = []
        self.doc_lengths = []
        self.df = Counter()

        for doc in documents:
            tokens = self._tokenize(doc)
            self.corpus_tokens.append(tokens)
            freq = Counter(tokens)
            self.doc_freqs.append(freq)
            self.doc_lengths.append(len(tokens))
            for term in set(tokens):
                self.df[term] += 1

        self.num_docs = len(documents)
        self.avg_dl = sum(self.doc_lengths) / self.num_docs if self.num_docs else 0

    def score(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """Score all documents against query, return (doc_index, score) sorted desc."""
        query_tokens = self._tokenize(query)
        scores = []

        for i in range(self.num_docs):
            score = 0.0
            dl = self.doc_lengths[i]
            freq = self.doc_freqs[i]

            for qt in query_tokens:
                if qt not in freq:
                    continue
                tf = freq[qt]
                df = self.df.get(qt, 0)
                idf = math.log((self.num_docs - df + 0.5) / (df + 0.5) + 1.0)
                tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl))
                score += idf * tf_norm

            scores.append((i, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# ─── RRF Fusion ─────────────────────────────────────────────────────────────

def _rrf_fuse(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = 60,
    top_k: int = 10,
) -> list[dict]:
    """Reciprocal Rank Fusion: merge two ranked lists.

    RRF score = sum(1 / (k + rank_i)) across all lists.
    k=60 is the standard constant from the original RRF paper.
    """
    score_map: dict[str, float] = {}
    doc_map: dict[str, dict] = {}

    for rank, doc in enumerate(vector_results):
        doc_id = f"{doc['file']}:{doc['start_line']}"
        score_map[doc_id] = score_map.get(doc_id, 0) + 1.0 / (k + rank + 1)
        doc_map[doc_id] = doc

    for rank, doc in enumerate(bm25_results):
        doc_id = f"{doc['file']}:{doc['start_line']}"
        score_map[doc_id] = score_map.get(doc_id, 0) + 1.0 / (k + rank + 1)
        if doc_id not in doc_map:
            doc_map[doc_id] = doc

    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    results = []
    for doc_id, rrf_score in ranked[:top_k]:
        doc = doc_map[doc_id].copy()
        doc["rrf_score"] = rrf_score
        results.append(doc)

    return results


def _count_tokens(text: str) -> int:
    """Count tokens using tiktoken."""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def _file_hash(text: str) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _chunk_code(text: str, file_path: str, chunk_size: int, overlap: int) -> list[dict]:
    """Split code into overlapping chunks, trying to respect function boundaries."""
    lines = text.split("\n")
    chunks = []
    current_chunk_lines = []
    current_tokens = 0
    start_line = 1

    for i, line in enumerate(lines, 1):
        line_tokens = _count_tokens(line + "\n")

        if current_tokens + line_tokens > chunk_size and current_chunk_lines:
            chunk_text = "\n".join(current_chunk_lines)
            chunks.append({
                "text": chunk_text,
                "file": file_path,
                "start_line": start_line,
                "end_line": i - 1,
            })

            overlap_lines = []
            overlap_tokens = 0
            for ol in reversed(current_chunk_lines):
                ot = _count_tokens(ol + "\n")
                if overlap_tokens + ot > overlap:
                    break
                overlap_lines.insert(0, ol)
                overlap_tokens += ot

            current_chunk_lines = overlap_lines
            current_tokens = overlap_tokens
            start_line = i - len(overlap_lines)

        current_chunk_lines.append(line)
        current_tokens += line_tokens

    if current_chunk_lines:
        chunk_text = "\n".join(current_chunk_lines)
        chunks.append({
            "text": chunk_text,
            "file": file_path,
            "start_line": start_line,
            "end_line": len(lines),
        })

    return chunks


class CodeIndexer:
    """Hybrid RAG indexer: ChromaDB vector search + BM25 keyword search + RRF fusion."""

    def __init__(self):
        self.client = chromadb.Client()
        self.collection = None
        self._manifest: dict[str, str] = {}  # rel_path -> content_hash
        self._bm25 = BM25()
        self._all_chunks: list[dict] = []  # kept for BM25 scoring

    def index_files(self, repo_dir: Path, code_files: list[Path]) -> int:
        """Index all code files into ChromaDB + BM25. Returns number of new/updated files."""
        self.collection = self.client.create_collection(
            name="code_index",
            metadata={"hnsw:space": "cosine"},
        )

        all_chunks = []
        for file_path in code_files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel_path = str(file_path.relative_to(repo_dir))
            self._manifest[rel_path] = _file_hash(text)
            chunks = _chunk_code(text, rel_path, config.chunk_size, config.chunk_overlap)
            all_chunks.extend(chunks)

        if not all_chunks:
            return 0

        # Vector index (ChromaDB)
        self._batch_insert(all_chunks)

        # BM25 keyword index
        self._all_chunks = all_chunks
        self._bm25.fit([c["text"] for c in all_chunks])

        return len(code_files)

    def index_incremental(self, repo_dir: Path, code_files: list[Path]) -> dict:
        """Incremental index: only re-index changed files.

        Returns dict with keys: added, updated, removed, unchanged.
        """
        if not self.collection:
            return self.index_files(repo_dir, code_files), {"added": len(code_files), "updated": 0, "removed": 0, "unchanged": 0}

        # Compute current file hashes
        current_hashes: dict[str, str] = {}
        file_contents: dict[str, str] = {}
        for file_path in code_files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel_path = str(file_path.relative_to(repo_dir))
            current_hashes[rel_path] = _file_hash(text)
            file_contents[rel_path] = text

        added = set(current_hashes.keys()) - set(self._manifest.keys())
        removed = set(self._manifest.keys()) - set(current_hashes.keys())
        common = set(current_hashes.keys()) & set(self._manifest.keys())

        updated = {f for f in common if current_hashes[f] != self._manifest[f]}
        unchanged = common - updated

        # Remove old chunks for updated and removed files
        files_to_remove = updated | removed
        if files_to_remove:
            self._remove_files(files_to_remove)

        # Add new chunks for added and updated files
        files_to_add = added | updated
        new_chunks = []
        for rel_path in files_to_add:
            chunks = _chunk_code(file_contents[rel_path], rel_path, config.chunk_size, config.chunk_overlap)
            new_chunks.extend(chunks)

        if new_chunks:
            self._batch_insert(new_chunks)

        # Update manifest
        self._manifest = {k: v for k, v in current_hashes.items()}
        for f in removed:
            self._manifest.pop(f, None)

        return {
            "added": len(added),
            "updated": len(updated),
            "removed": len(removed),
            "unchanged": len(unchanged),
        }

    def _remove_files(self, file_paths: set[str]) -> None:
        """Remove all chunks belonging to the given files."""
        for rel_path in file_paths:
            try:
                self.collection.delete(where={"file": rel_path})
            except Exception:
                pass

    def _batch_insert(self, chunks: list[dict]) -> None:
        """Insert chunks into ChromaDB in batches."""
        batch_size = 500
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            self.collection.add(
                ids=[f"chunk_{i + j}" for j in range(len(batch))],
                documents=[c["text"] for c in batch],
                metadatas=[{
                    "file": c["file"],
                    "start_line": str(c["start_line"]),
                    "end_line": str(c["end_line"]),
                } for c in batch],
            )

    def search(self, query: str, top_k: int | None = None, mode: str = "hybrid") -> list[dict]:
        """Search for relevant code chunks.

        Args:
            query: Search query
            top_k: Number of results to return
            mode: "hybrid" (vector+BM25+RRF), "vector" (ChromaDB only), or "bm25" (keyword only)

        Returns:
            List of matching chunks with text, file, lines, and score.
        """
        if not self.collection:
            return []

        k = top_k or config.top_k

        if mode == "bm25":
            return self._search_bm25(query, k)
        elif mode == "vector":
            return self._search_vector(query, k)
        else:  # hybrid
            # Get more candidates from each method for better fusion
            fetch_k = min(k * 2, 30)
            vector_results = self._search_vector(query, fetch_k)
            bm25_results = self._search_bm25(query, fetch_k)
            fused = _rrf_fuse(vector_results, bm25_results, top_k=k)
            return fused

    def _search_vector(self, query: str, k: int) -> list[dict]:
        """Pure vector search via ChromaDB."""
        results = self.collection.query(query_texts=[query], n_results=k)
        matches = []
        for i in range(len(results["ids"][0])):
            matches.append({
                "text": results["documents"][0][i],
                "file": results["metadatas"][0][i]["file"],
                "start_line": int(results["metadatas"][0][i]["start_line"]),
                "end_line": int(results["metadatas"][0][i]["end_line"]),
                "distance": results["distances"][0][i] if results.get("distances") else 0,
            })
        return matches

    def _search_bm25(self, query: str, k: int) -> list[dict]:
        """Pure BM25 keyword search."""
        scored = self._bm25.score(query, top_k=k)
        results = []
        for idx, score in scored:
            if score <= 0:
                continue
            chunk = self._all_chunks[idx].copy()
            chunk["bm25_score"] = score
            chunk["distance"] = 1.0 - min(score / 10.0, 1.0)  # normalize to distance-like
            results.append(chunk)
        return results
