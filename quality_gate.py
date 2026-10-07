"""
Quality Gate Engine: Evaluates Candidate Knowledge Base on Golden QA Dataset.
Enforces Grounding and Accuracy Thresholds, rejecting regressions.
"""
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from config import (
    DATA_DIR,
    MIN_GROUNDING_SCORE,
    MIN_ACCURACY_SCORE,
    MAX_ALLOWED_ACCURACY_DROP
)
from pipeline.vector_store import VectorStore

GOLDEN_DATASET_FILE = DATA_DIR / "golden_dataset.json"

class QualityEvaluationReport:
    def __init__(self):
        self.total_tests: int = 0
        self.passed_tests: int = 0
        self.accuracy_score: float = 0.0
        self.grounding_score: float = 0.0
        self.relevance_score: float = 0.0
        self.baseline_accuracy: Optional[float] = None
        self.baseline_grounding: Optional[float] = None
        self.decision: str = "PENDING" # "APPROVED" or "REJECTED"
        self.rejection_reasons: List[str] = []
        self.detailed_results: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "accuracy_score": round(self.accuracy_score, 4),
            "grounding_score": round(self.grounding_score, 4),
            "relevance_score": round(self.relevance_score, 4),
            "baseline_accuracy": round(self.baseline_accuracy, 4) if self.baseline_accuracy is not None else None,
            "baseline_grounding": round(self.baseline_grounding, 4) if self.baseline_grounding is not None else None,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "rejection_reasons": self.rejection_reasons,
            "detailed_results": self.detailed_results
        }

class QualityGate:
    def __init__(self, golden_dataset_path: Path = GOLDEN_DATASET_FILE):
        self.golden_dataset_path = golden_dataset_path
        self._load_golden_dataset()

    def _load_golden_dataset(self):
        if not self.golden_dataset_path.exists():
            self.test_cases = []
            return
        with open(self.golden_dataset_path, "r", encoding="utf-8") as f:
            self.test_cases = json.load(f)

    def evaluate_store(self, vector_store: VectorStore) -> Tuple[float, float, float, List[Dict[str, Any]]]:
        """
        Runs evaluation queries against the given vector store.
        Returns: (accuracy, grounding, relevance, detailed_results)
        """
        if not self.test_cases or not vector_store or not vector_store.chunks:
            return 0.0, 0.0, 0.0, []

        accuracy_hits = 0
        grounding_scores = []
        relevance_scores = []
        detailed_results = []

        for item in self.test_cases:
            qid = item.get("id")
            question = item.get("question")
            expected_answer = item.get("expected_answer", "")
            keywords = item.get("ground_truth_context_keywords", [])
            topics = item.get("required_topics", [])

            # Search top 3 candidate chunks
            retrieved = vector_store.search(question, top_k=3)
            retrieved_texts = [chunk.text for chunk, score in retrieved]
            combined_context = " ".join(retrieved_texts).lower()

            # 1. Retrieval Accuracy: Did retrieved context contain the required topic keywords?
            matched_keywords = [
                kw for kw in keywords 
                if all(word in combined_context for word in kw.lower().split())
            ]
            kw_coverage = len(matched_keywords) / len(keywords) if keywords else 1.0
            
            # Matched topics
            matched_topics = [
                t for t in topics 
                if all(word in combined_context for word in t.lower().split())
            ]
            topic_coverage = len(matched_topics) / len(topics) if topics else 1.0

            # Overall item hit
            is_accurate = (kw_coverage >= 0.5 or topic_coverage >= 0.5)
            if is_accurate:
                accuracy_hits += 1

            # 2. Grounding (Faithfulness) Score:
            # Proportion of key informative terms in the reference answer supported by retrieved context
            ans_tokens = [w.lower() for w in expected_answer.split() if len(w) > 3]
            grounded_tokens = [w for w in ans_tokens if w in combined_context]
            grounding = len(grounded_tokens) / len(ans_tokens) if ans_tokens else 1.0
            grounding_scores.append(grounding)

            # 3. Context Relevance: Top retrieval score
            top_score = retrieved[0][1] if retrieved else 0.0
            relevance_scores.append(top_score)

            detailed_results.append({
                "id": qid,
                "question": question,
                "is_accurate": is_accurate,
                "keyword_coverage": round(kw_coverage, 2),
                "grounding_score": round(grounding, 2),
                "top_retrieval_similarity": round(top_score, 3),
                "retrieved_sources": [chunk.doc_name for chunk, _ in retrieved]
            })

        total = len(self.test_cases)
        overall_accuracy = accuracy_hits / total if total > 0 else 0.0
        avg_grounding = sum(grounding_scores) / total if total > 0 else 0.0
        avg_relevance = sum(relevance_scores) / total if total > 0 else 0.0

        return overall_accuracy, avg_grounding, avg_relevance, detailed_results

    def evaluate_candidate(
        self,
        candidate_store: VectorStore,
        baseline_store: Optional[VectorStore] = None
    ) -> QualityEvaluationReport:
        """
        Evaluates candidate update and applies quality gate rules.
        Rejects if accuracy drops or grounding fails threshold.
        """
        report = QualityEvaluationReport()
        report.total_tests = len(self.test_cases)

        # Evaluate candidate
        cand_acc, cand_grd, cand_rel, details = self.evaluate_store(candidate_store)
        report.accuracy_score = cand_acc
        report.grounding_score = cand_grd
        report.relevance_score = cand_rel
        report.passed_tests = sum(1 for d in details if d["is_accurate"])
        report.detailed_results = details

        # Baseline evaluation if baseline store exists
        if baseline_store and len(baseline_store.chunks) > 0:
            base_acc, base_grd, _, _ = self.evaluate_store(baseline_store)
            report.baseline_accuracy = base_acc
            report.baseline_grounding = base_grd

        # Quality Gate Decision Rules
        reasons = []

        # Check 1: Minimum Grounding Threshold
        if cand_grd < MIN_GROUNDING_SCORE:
            reasons.append(
                f"Grounding score {cand_grd:.2%} is below required minimum of {MIN_GROUNDING_SCORE:.2%}."
            )

        # Check 2: Minimum Accuracy Threshold
        if cand_acc < MIN_ACCURACY_SCORE:
            reasons.append(
                f"Accuracy score {cand_acc:.2%} is below required minimum of {MIN_ACCURACY_SCORE:.2%}."
            )

        # Check 3: Regression vs Baseline
        if report.baseline_accuracy is not None:
            accuracy_diff = cand_acc - report.baseline_accuracy
            if accuracy_diff < -MAX_ALLOWED_ACCURACY_DROP:
                reasons.append(
                    f"Accuracy regression detected: candidate accuracy {cand_acc:.2%} dropped from baseline {report.baseline_accuracy:.2%} (drop: {abs(accuracy_diff):.2%}, max allowed: {MAX_ALLOWED_ACCURACY_DROP:.2%})."
                )

        if report.baseline_grounding is not None:
            if cand_grd < report.baseline_grounding - 0.05:
                reasons.append(
                    f"Grounding regression detected: candidate grounding {cand_grd:.2%} dropped from baseline {report.baseline_grounding:.2%}."
                )

        if reasons:
            report.decision = "REJECTED"
            report.rejection_reasons = reasons
        else:
            report.decision = "APPROVED"

        return report
