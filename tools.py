# tools.py
import re
import json
import hashlib
import random
import sqlite3
from typing import Dict, Any, List, Optional
from data_prep import DB_PATH, CORPUS_PATH
from injection_gate import SideEffectGate, TaintScanner


class ToolFaultError(Exception):
    pass


class WarRoomTools:
    def __init__(self, db_path: str = DB_PATH, corpus_path: str = CORPUS_PATH):
        self.db_path = db_path
        with open(corpus_path, "r", encoding="utf-8") as f:
            self.corpus: List[Dict[str, Any]] = json.load(f)
        self.docs_by_id: Dict[str, Dict[str, Any]] = {d["doc_id"]: d for d in self.corpus}
        self.gate = SideEffectGate()

    def run_sql(self, read_only_query: str) -> Dict[str, Any]:
        q_clean = read_only_query.strip()
        if not q_clean.upper().startswith("SELECT"):
            raise ValueError("SECURITY_ERROR: run_sql only permits read-only SELECT queries.")
        forbidden = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|PRAGMA)\b", re.I)
        if forbidden.search(q_clean):
            raise ValueError("SECURITY_ERROR: Mutating SQL keywords are prohibited.")

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(q_clean)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        conn.close()

        canonical = json.dumps({"columns": columns, "rows": rows}, sort_keys=True)
        sha256_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

        return {
            "tool": "run_sql",
            "sql_query": q_clean,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "result_sha256": sha256_hex,
        }

    def search_docs(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        q_tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
        scored = []
        for doc in self.corpus:
            haystack = f"{doc['doc_id']} {doc['title']} {doc.get('topic', '')} {doc['text']}".lower()
            doc_tokens = set(re.findall(r"[a-z0-9_]+", haystack))
            overlap = len(q_tokens.intersection(doc_tokens))
            if doc["doc_id"].lower() in query.lower():
                overlap += 20
            if overlap > 0:
                is_tainted, taint_flags = TaintScanner.scan_document(doc)
                doc_copy = dict(doc)
                doc_copy["tainted"] = is_tainted
                doc_copy["taint_flags"] = taint_flags
                scored.append((overlap, doc_copy))

        scored.sort(key=lambda x: (-x[0], x[1]["doc_id"]))
        results = [item[1] for item in scored[:top_k]]
        return {
            "tool": "search_docs",
            "query": query,
            "results": results,
        }

    def get_policy(self, name: str, as_of_date: str = "2026-10-24") -> Dict[str, Any]:
        name_lower = name.lower()
        matches = []
        for doc in self.corpus:
            if doc["doc_type"] in ("policy", "circular", "memo"):
                p_name = (doc.get("policy_name") or "").lower()
                if name_lower in p_name or name_lower in doc["title"].lower() or name_lower in doc.get("topic", "").lower():
                    matches.append(dict(doc))

        matches.sort(key=lambda d: (d.get("effective_date") or "0000-00-00", d.get("authority_rank", 0)))
        return {
            "tool": "get_policy",
            "policy_name": name,
            "as_of_date": as_of_date,
            "versions": matches,
        }

    def send_seller_whatsapp(
        self, raw_user_query: str, seller_id: str, message: str, triggered_by_doc_id: str = ""
    ) -> Dict[str, Any]:
        decision = self.gate.check_and_execute(
            raw_user_query=raw_user_query,
            seller_id=seller_id,
            message=message,
            triggered_by_doc_id=triggered_by_doc_id,
        )
        return {
            "tool": "send_seller_whatsapp",
            "allowed": decision.allowed,
            "reason": decision.reason,
            "attack_detected": decision.attack_detected,
            "source_doc_id": decision.source_doc_id,
        }


class SeededFaultWrapper:
    def __init__(self, tools: WarRoomTools, seed: int = 101, fail_rate: float = 0.15):
        self.tools = tools
        self.rng = random.Random(seed)
        self.fail_rate = fail_rate
        self.total_calls = 0
        self.failed_calls = 0
        self.force_fail_from_call: Optional[int] = None

    def execute(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        self.total_calls += 1

        if tool_name == "send_seller_whatsapp":
            return self.tools.send_seller_whatsapp(**kwargs)

        if self.force_fail_from_call is not None and self.total_calls >= self.force_fail_from_call:
            self.failed_calls += 1
            raise ToolFaultError(f"TIMEOUT_ERROR: {tool_name} timed out after 3000ms (Simulated Outage).")

        if (self.total_calls % 6 == 0) or (self.rng.random() < self.fail_rate * 0.4):
            self.failed_calls += 1
            if self.total_calls % 2 == 0:
                raise ToolFaultError(f"TIMEOUT_ERROR: {tool_name} failed on 6th-call courier/DB gateway timeout.")
            else:
                return {"tool": tool_name, "error": "EMPTY_RESPONSE_FAULT", "rows": [], "results": [], "versions": []}

        if tool_name == "run_sql":
            return self.tools.run_sql(kwargs["read_only_query"])
        elif tool_name == "search_docs":
            return self.tools.search_docs(kwargs["query"], top_k=kwargs.get("top_k", 5))
        elif tool_name == "get_policy":
            return self.tools.get_policy(kwargs["name"], as_of_date=kwargs.get("as_of_date", "2026-10-24"))
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
