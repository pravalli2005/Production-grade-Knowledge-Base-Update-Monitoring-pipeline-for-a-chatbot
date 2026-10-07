"""
Document Chunking and Preprocessing with PII Redaction.
"""
from typing import List, Dict, Any
from pipeline.security import mask_sensitive_data, detect_prompt_injection

class DocumentChunk:
    def __init__(self, chunk_id: str, doc_name: str, text: str, metadata: Dict[str, Any]):
        self.chunk_id = chunk_id
        self.doc_name = doc_name
        self.text = text
        self.metadata = metadata

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_name": self.doc_name,
            "text": self.text,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentChunk":
        return cls(
            chunk_id=data["chunk_id"],
            doc_name=data["doc_name"],
            text=data["text"],
            metadata=data.get("metadata", {})
        )

def chunk_document(
    doc_name: str,
    raw_content: str,
    doc_hash: str,
    chunk_size: int = 400,
    overlap: int = 50
) -> List[DocumentChunk]:
    """
    Chunks document content, strips/masks PII, and runs injection sanity check.
    """
    # 1. Mask sensitive PII before chunking
    masked_content, pii_detected = mask_sensitive_data(raw_content)
    
    # 2. Check for indirect prompt injection attempts inside document
    injection_analysis = detect_prompt_injection(raw_content)
    has_injection_risk = injection_analysis["is_injection"]

    # 3. Simple text chunking by character/word blocks
    words = masked_content.split()
    chunks: List[DocumentChunk] = []
    
    if not words:
        return chunks

    i = 0
    chunk_idx = 0
    while i < len(words):
        chunk_words = words[i : i + chunk_size]
        chunk_text = " ".join(chunk_words)
        
        chunk_id = f"{doc_name}_chunk_{chunk_idx}"
        metadata = {
            "source_doc": doc_name,
            "doc_hash": doc_hash,
            "chunk_index": chunk_idx,
            "pii_masked": pii_detected,
            "has_injection_risk": has_injection_risk,
            "word_count": len(chunk_words)
        }
        
        chunks.append(DocumentChunk(
            chunk_id=chunk_id,
            doc_name=doc_name,
            text=chunk_text,
            metadata=metadata
        ))
        
        chunk_idx += 1
        i += (chunk_size - overlap)
        if i <= 0 or i >= len(words):
            break

    return chunks
