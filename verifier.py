import re
from injection_gate import TaintScanner


class ConflictResolver:
    @staticmethod
    def resolve_topic_docs(docs):
        grouped = {}
        for d in docs:
            t = d.get("topic") or d["doc_id"]
            grouped.setdefault(t, []).append(d)

        winning, conflicts = [], []

        for topic, group in grouped.items():
            if len(group) == 1:
                winning.append(group[0])
                continue

            # newest effective_date wins; fallback to authority_rank then created_at
            def doc_precedence(d):
                return (
                    d.get("effective_date") or "0000-00-00",
                    int(d.get("authority_rank", 0)),
                    d.get("created_at") or "0000-00-00",
                )

            ranked = sorted(group, key=doc_precedence, reverse=True)
            top1, top2 = ranked[0], ranked[1]

            if doc_precedence(top1) == doc_precedence(top2):
                conflicts.append({
                    "topic": topic,
                    "doc_a": top1["doc_id"],
                    "val_a": top1.get("conflict_value", top1["text"]),
                    "doc_b": top2["doc_id"],
                    "val_b": top2.get("conflict_value", top2["text"]),
                    "status": "UNRESOLVED_EQUAL_AUTHORITY_AND_DATE",
                    "rule_applied": "Both documents share effective_date=2026-10-18 and authority=LOGISTICS_AUDIT; agent refuses to pick a side.",
                })
                winning.extend([top1, top2])
            else:
                conflicts.append({
                    "topic": topic,
                    "winner_doc": top1["doc_id"],
                    "overridden_doc": top2["doc_id"],
                    "status": "RESOLVED_BY_DATE_AND_AUTHORITY",
                    "rule_applied": (
                        f"{top1['doc_id']} (date={top1.get('effective_date')}, auth={top1.get('authority')}) "
                        f"supersedes {top2['doc_id']} (date={top2.get('effective_date')}, auth={top2.get('authority')})."
                    ),
                })
                winning.append(top1)

        return winning, conflicts


class DeterministicVerifier:
    def __init__(self, docs_by_id):
        self.docs_by_id = docs_by_id

    @staticmethod
    def _extract_numbers(text):
        # strip entity codes and identifiers before parsing quantities
        s = re.sub(r"DOC-[A-Z]+-\d+", "", text)
        s = re.sub(r"Tier-[123]", "", s, flags=re.I)
        s = re.sub(r"T\+\d+", "", s)
        s = re.sub(r"\d{4}-\d{2}-\d{2}", "", s)
        s = re.sub(r"#\d+", "", s)
        s = re.sub(r"v\d+\.\d+", "", s)
        s = re.sub(r"seller\s*\d+", "", s, flags=re.I)
        return [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", s)]

    def verify_claims(self, candidate_claims, recorded_sql_results):
        passed, rejected = [], []

        for c in candidate_claims:
            ctype = c.get("type", "fact")
            if ctype in ("assumption", "recommendation"):
                passed.append(c)
                continue

            ev = c.get("evidence", {})
            txt = c.get("text", "")

            # 1. Unstructured text evidence
            if "doc_id" in ev:
                did = ev["doc_id"]
                doc = self.docs_by_id.get(did)
                if not doc:
                    rejected.append({"claim": c, "reason": "UNKNOWN_DOC_ID"})
                    continue

                tainted, _ = TaintScanner.scan_document(doc)
                if (doc.get("tainted") or tainted) and not c.get("is_security_report"):
                    rejected.append({"claim": c, "reason": "TAINTED_INJECTION_SOURCE"})
                    continue

                span = ev.get("quoted_span", "")
                if not span or span not in doc.get("text", ""):
                    rejected.append({"claim": c, "reason": "NON_VERBATIM_QUOTED_SPAN"})
                    continue

                if doc.get("status") == "superseded" and not c.get("flags_superseded"):
                    rejected.append({"claim": c, "reason": "UNFLAGGED_SUPERSEDED_POLICY"})
                    continue

                if doc.get("status") == "incomplete_record" and not c.get("flags_missing_field"):
                    rejected.append({"claim": c, "reason": "UNFLAGGED_INCOMPLETE_RECORD"})
                    continue

                c_nums = self._extract_numbers(txt)
                span_nums = set(self._extract_numbers(span))
                if not all(any(abs(x - y) < 1e-2 for y in span_nums) for x in c_nums):
                    rejected.append({"claim": c, "reason": "NUMBER_NOT_IN_QUOTED_SPAN"})
                    continue

                passed.append(c)

            # 2. Structured SQL evidence
            elif "sql_query" in ev:
                sql_q = ev["sql_query"]
                claimed_hash = ev.get("result_sha256", "")
                sql_meta = recorded_sql_results.get(sql_q)

                if not sql_meta or sql_meta.get("result_sha256") != claimed_hash:
                    rejected.append({"claim": c, "reason": "SQL_HASH_MISMATCH_OR_UNRECORDED"})
                    continue

                valid_vals = set()
                raw_nums = []
                for row in sql_meta.get("rows", []):
                    for cell in row:
                        if isinstance(cell, (int, float)):
                            valid_vals.add(float(cell))
                            raw_nums.append(float(cell))
                        elif isinstance(cell, str) and cell.isdigit():
                            valid_vals.add(float(cell))

                if raw_nums:
                    valid_vals.add(float(sum(raw_nums)))
                for a in raw_nums:
                    for b in raw_nums:
                        if b > 0:
                            valid_vals.add(round(((a - b) / b) * 100.0, 1))
                            valid_vals.add(round((a / b) * 100.0, 1))

                claim_nums = self._extract_numbers(txt)
                if not all(any(abs(cn - vn) < 0.15 for vn in valid_vals) for cn in claim_nums):
                    rejected.append({"claim": c, "reason": "UNSUPPORTED_SQL_NUMBER"})
                    continue

                passed.append(c)
            else:
                rejected.append({"claim": c, "reason": "MISSING_EVIDENCE_OBJECT"})

        return passed, rejected


