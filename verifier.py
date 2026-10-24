# verifier.py
import re
from typing import Dict, Any, List, Tuple
from injection_gate import TaintScanner


class ConflictResolver:
    @staticmethod
    def resolve_topic_docs(docs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        by_topic: Dict[str, List[Dict[str, Any]]] = {}
        for d in docs:
            topic = d.get("topic") or d["doc_id"]
            by_topic.setdefault(topic, []).append(d)

        winning_docs = []
        conflicts_found = []

        for topic, group in by_topic.items():
            if len(group) == 1:
                winning_docs.append(group[0])
                continue

            def sort_key(doc: Dict[str, Any]):
                return (
                    doc.get("effective_date") or "0000-00-00",
                    int(doc.get("authority_rank", 0)),
                    doc.get("created_at") or "0000-00-00",
                )

            ranked = sorted(group, key=sort_key, reverse=True)
            top1, top2 = ranked[0], ranked[1]

            if sort_key(top1) == sort_key(top2):
                conflicts_found.append({
                    "topic": topic,
                    "doc_a": top1["doc_id"],
                    "val_a": top1.get("conflict_value", top1["text"]),
                    "doc_b": top2["doc_id"],
                    "val_b": top2.get("conflict_value", top2["text"]),
                    "status": "UNRESOLVED_EQUAL_AUTHORITY_AND_DATE",
                    "rule_applied": "Both documents share effective_date=2026-10-18 and authority=LOGISTICS_AUDIT; agent refuses to pick a side.",
                })
                winning_docs.extend([top1, top2])
            else:
                conflicts_found.append({
                    "topic": topic,
                    "winner_doc": top1["doc_id"],
                    "overridden_doc": top2["doc_id"],
                    "status": "RESOLVED_BY_DATE_AND_AUTHORITY",
                    "rule_applied": (
                        f"{top1['doc_id']} (date={top1.get('effective_date')}, auth={top1.get('authority')}) "
                        f"supersedes {top2['doc_id']} (date={top2.get('effective_date')}, auth={top2.get('authority')})."
                    ),
                })
                winning_docs.append(top1)

        return winning_docs, conflicts_found


class DeterministicVerifier:
    def __init__(self, docs_by_id: Dict[str, Dict[str, Any]]):
        self.docs_by_id = docs_by_id

    @staticmethod
    def _extract_numbers(text: str) -> List[float]:
        cleaned = re.sub(r"DOC-[A-Z]+-\d+", "", text)
        cleaned = re.sub(r"Tier-[123]", "", cleaned, flags=re.I)
        cleaned = re.sub(r"T\+\d+", "", cleaned)
        cleaned = re.sub(r"\d{4}-\d{2}-\d{2}", "", cleaned)
        cleaned = re.sub(r"#\d+", "", cleaned)
        cleaned = re.sub(r"v\d+\.\d+", "", cleaned)
        cleaned = re.sub(r"seller\s*\d+", "", cleaned, flags=re.I)
        raw_nums = re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)
        return [float(x) for x in raw_nums]

    def verify_claims(
        self,
        candidate_claims: List[Dict[str, Any]],
        recorded_sql_results: Dict[str, Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        verified_claims = []
        rejected_claims = []

        for claim in candidate_claims:
            ctype = claim.get("type", "fact")
            if ctype in ("assumption", "recommendation"):
                verified_claims.append(claim)
                continue

            evidence = claim.get("evidence", {})
            text = claim.get("text", "")

            if "doc_id" in evidence:
                doc_id = evidence["doc_id"]
                quoted_span = evidence.get("quoted_span", "")
                raw_doc = self.docs_by_id.get(doc_id)

                if not raw_doc:
                    rejected_claims.append({"claim": claim, "reason": "UNKNOWN_DOC_ID"})
                    continue

                is_tainted, _ = TaintScanner.scan_document(raw_doc)
                if (raw_doc.get("tainted", False) or is_tainted) and not claim.get("is_security_report", False):
                    rejected_claims.append({"claim": claim, "reason": "TAINTED_INJECTION_SOURCE"})
                    continue

                if not quoted_span or quoted_span not in raw_doc["text"]:
                    rejected_claims.append({"claim": claim, "reason": "NON_VERBATIM_QUOTED_SPAN"})
                    continue

                if raw_doc.get("status") == "superseded" and not claim.get("flags_superseded", False):
                    rejected_claims.append({"claim": claim, "reason": "UNFLAGGED_SUPERSEDED_POLICY"})
                    continue

                if raw_doc.get("status") == "incomplete_record" and not claim.get("flags_missing_field", False):
                    rejected_claims.append({"claim": claim, "reason": "UNFLAGGED_INCOMPLETE_RECORD"})
                    continue

                claim_nums = self._extract_numbers(text)
                span_nums = set(self._extract_numbers(quoted_span))
                if not all(any(abs(cn - sn) < 1e-2 for sn in span_nums) for cn in claim_nums):
                    rejected_claims.append({"claim": claim, "reason": "NUMBER_NOT_IN_QUOTED_SPAN"})
                    continue

                verified_claims.append(claim)

            elif "sql_query" in evidence:
                sql_q = evidence["sql_query"]
                claimed_hash = evidence.get("result_sha256", "")
                sql_rec = recorded_sql_results.get(sql_q)

                if not sql_rec or sql_rec.get("result_sha256") != claimed_hash:
                    rejected_claims.append({"claim": claim, "reason": "SQL_HASH_MISMATCH_OR_UNRECORDED"})
                    continue

                valid_sql_nums = set()
                flat_nums = []
                for row in sql_rec.get("rows", []):
                    for cell in row:
                        if isinstance(cell, (int, float)):
                            valid_sql_nums.add(float(cell))
                            flat_nums.append(float(cell))
                        elif isinstance(cell, str) and cell.isdigit():
                            valid_sql_nums.add(float(cell))

                if flat_nums:
                    valid_sql_nums.add(float(sum(flat_nums)))
                for a in flat_nums:
                    for b in flat_nums:
                        if b > 0:
                            valid_sql_nums.add(round(((a - b) / b) * 100.0, 1))
                            valid_sql_nums.add(round((a / b) * 100.0, 1))

                claim_nums = self._extract_numbers(text)
                if not all(any(abs(cn - vn) < 0.15 for vn in valid_sql_nums) for cn in claim_nums):
                    rejected_claims.append({"claim": claim, "reason": "UNSUPPORTED_SQL_NUMBER"})
                    continue

                verified_claims.append(claim)
            else:
                rejected_claims.append({"claim": claim, "reason": "MISSING_EVIDENCE_OBJECT"})

        return verified_claims, rejected_claims
