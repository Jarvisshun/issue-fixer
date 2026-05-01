"""Code indexer using ChromaDB for RAG-based code search, with incremental indexing."""

import hashlib
import json
from pathlib import Path

import chromadb
import tiktoken

from .config import config


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
    """Index code files into ChromaDB for semantic search, with incremental updates."""

    def __init__(self):
        self.client = chromadb.Client()
        self.collection = None
        self._manifest: dict[str, str] = {}  # rel_path -> content_hash

    def index_files(self, repo_dir: Path, code_files: list[Path]) -> int:
        """Index all code files into ChromaDB. Returns number of new/updated files."""
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

        self._batch_insert(all_chunks)
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

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """Search for relevant code chunks."""
        if not self.collection:
            return []

        k = top_k or config.top_k
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
