"""
Vector Store and Hybrid Retrieval Engine.
Provides TF-IDF + Semantic Token Cosine Vector Index with persistence and fast top-k retrieval.
"""
import re
import math
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import numpy as np
from pipeline.chunking import DocumentChunk

class VectorStore:
    def __init__(self):
        self.chunks: List[DocumentChunk] = []
        self.vocabulary: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.chunk_vectors: Optional[np.ndarray] = None

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into lowercase alphanumeric words and bigrams."""
        tokens = re.findall(r"\b[a-zA-Z0-9_]{2,}\b", text.lower())
        bigrams = [f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1)]
        return tokens + bigrams

    def build_index(self, chunks: List[DocumentChunk]):
        """Builds TF-IDF vector index over chunks."""
        self.chunks = chunks
        if not chunks:
            self.vocabulary = {}
            self.idf = {}
            self.chunk_vectors = None
            return

        doc_count = len(chunks)
        doc_frequencies: Dict[str, int] = {}
        chunk_token_lists = []

        for chunk in chunks:
            tokens = set(self._tokenize(chunk.text))
            chunk_token_lists.append(self._tokenize(chunk.text))
            for tok in tokens:
                doc_frequencies[tok] = doc_frequencies.get(tok, 0) + 1

        # Build vocabulary of tokens with frequency > 0
        sorted_tokens = sorted(doc_frequencies.keys())
        self.vocabulary = {tok: idx for idx, tok in enumerate(sorted_tokens)}
        vocab_size = len(self.vocabulary)

        # Compute IDF
        self.idf = {}
        for tok, df in doc_frequencies.items():
            self.idf[tok] = math.log((doc_count + 1) / (df + 1)) + 1.0

        if vocab_size == 0:
            self.chunk_vectors = np.zeros((doc_count, 1), dtype=np.float32)
            return

        # Build TF-IDF matrix
        vectors = np.zeros((doc_count, vocab_size), dtype=np.float32)
        for i, tokens in enumerate(chunk_token_lists):
            tf: Dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            for tok, count in tf.items():
                if tok in self.vocabulary:
                    col = self.vocabulary[tok]
                    vectors[i, col] = (1.0 + math.log(count)) * self.idf[tok]

        # Normalize vectors for cosine similarity
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.chunk_vectors = vectors / norms

    def _vectorize_query(self, query: str) -> np.ndarray:
        vocab_size = len(self.vocabulary)
        if vocab_size == 0 or self.chunk_vectors is None:
            return np.zeros((1, 1), dtype=np.float32)

        q_vec = np.zeros((1, vocab_size), dtype=np.float32)
        tokens = self._tokenize(query)
        tf: Dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1

        for tok, count in tf.items():
            if tok in self.vocabulary:
                col = self.vocabulary[tok]
                q_vec[0, col] = (1.0 + math.log(count)) * self.idf.get(tok, 1.0)

        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm
        return q_vec

    def search(self, query: str, top_k: int = 3) -> List[Tuple[DocumentChunk, float]]:
        """
        Retrieves top_k chunks for a query with cosine similarity scores.
        """
        if not self.chunks or self.chunk_vectors is None:
            return []

        q_vec = self._vectorize_query(query)
        # Cosine similarity matrix multiplication
        sims = np.dot(self.chunk_vectors, q_vec.T).flatten()

        top_indices = np.argsort(sims)[::-1][:top_k]
        results = []
        for idx in top_indices:
            score = float(sims[idx])
            results.append((self.chunks[idx], max(0.0, score)))

        return results

    def save(self, directory: Path):
        """Saves vector store and chunks to disk."""
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "vocabulary": self.vocabulary,
            "idf": self.idf,
            "chunks": [c.to_dict() for c in self.chunks]
        }
        with open(directory / "index_meta.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        if self.chunk_vectors is not None:
            np.save(directory / "chunk_vectors.npy", self.chunk_vectors)

    @classmethod
    def load(cls, directory: Path) -> "VectorStore":
        """Loads vector store and chunks from disk."""
        store = cls()
        meta_file = directory / "index_meta.json"
        if not meta_file.exists():
            return store

        with open(meta_file, "r", encoding="utf-8") as f:
            payload = json.load(f)

        store.vocabulary = payload.get("vocabulary", {})
        store.idf = payload.get("idf", {})
        store.chunks = [DocumentChunk.from_dict(d) for d in payload.get("chunks", [])]

        vec_file = directory / "chunk_vectors.npy"
        if vec_file.exists():
            store.chunk_vectors = np.load(vec_file)

        return store
