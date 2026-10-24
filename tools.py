import re
import json
import hashlib
import random
import sqlite3
from data_prep import DB_PATH, CORPUS_PATH
from injection_gate import SideEffectGate, TaintScanner


class ToolFaultError(Exception):
    pass


class WarRoomTools:
    def __init__(self, db_path=DB_PATH, corpus_path=CORPUS_PATH):
        self.db_path = db_path
        with open(corpus_path, "r", encoding="utf-8") as f:
            self.corpus = json.load(f)
        self.docs_by_id = {d["doc_id"]: d for d in self.corpus}
        self.gate = SideEffectGate()

    def run_sql(self, read_only_query):
        q = read_only_query.strip()
        if not q.upper().startswith("SELECT"):
            raise ValueError("SECURITY_ERROR: run_sql only permits read-only SELECT queries.")
        if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|PRAGMA)\b", q, re.I):
            raise ValueError("SECURITY_ERROR: Mutating SQL keywords are prohibited.")

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(q)
        cols = [col[0] for col in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        conn.close()

        # sha256 checksum for verifier handshake
        payload = json.dumps({"columns": cols, "rows": rows}, sort_keys=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

        return {
            "tool": "run_sql",
            "sql_query": q,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "result_sha256": digest,
        }

    def search_docs(self, query, top_k=5):
        words = set(re.findall(r"[a-z0-9_]+", query.lower()))
        hits = []

        for d in self.corpus:
            content = f"{d['doc_id']} {d['title']} {d.get('topic', '')} {d['text']}".lower()
            d_words = set(re.findall(r"[a-z0-9_]+", content))
            score = len(words.intersection(d_words))
            if d["doc_id"].lower() in query.lower():
                score += 20

            if score > 0:
                tainted, flags = TaintScanner.scan_document(d)
                copy_d = dict(d)
                copy_d["tainted"] = tainted
                copy_d["taint_flags"] = flags
                hits.append((score, copy_d))

        hits.sort(key=lambda x: (-x[0], x[1]["doc_id"]))
        return {
            "tool": "search_docs",
            "query": query,
            "results": [item[1] for item in hits[:top_k]],
        }

    def get_policy(self, name, as_of_date="2026-10-24"):
        target = name.lower()
        matched = []

        for d in self.corpus:
            if d.get("doc_type") not in ("policy", "circular", "memo"):
                continue
            pol_name = (d.get("policy_name") or "").lower()
            title = d.get("title", "").lower()
            topic = d.get("topic", "").lower()
            if target in pol_name or target in title or target in topic:
                matched.append(dict(d))

        matched.sort(key=lambda x: (x.get("effective_date") or "0000-00-00", x.get("authority_rank", 0)))
        return {
            "tool": "get_policy",
            "policy_name": name,
            "as_of_date": as_of_date,
            "versions": matched,
        }

    def send_seller_whatsapp(self, raw_user_query, seller_id, message, triggered_by_doc_id=""):
        res = self.gate.check_and_execute(
            raw_user_query=raw_user_query,
            seller_id=seller_id,
            message=message,
            triggered_by_doc_id=triggered_by_doc_id,
        )
        return {
            "tool": "send_seller_whatsapp",
            "allowed": res.allowed,
            "reason": res.reason,
            "attack_detected": res.attack_detected,
            "source_doc_id": res.source_doc_id,
        }


class SeededFaultWrapper:
    def __init__(self, tools, seed=101, fail_rate=0.15):
        self.tools = tools
        self.rng = random.Random(seed)
        self.fail_rate = fail_rate
        self.total_calls = 0
        self.failed_calls = 0
        self.force_fail_from_call = None

    def execute(self, tool_name, **kwargs):
        self.total_calls += 1

        if tool_name == "send_seller_whatsapp":
            return self.tools.send_seller_whatsapp(**kwargs)

        if self.force_fail_from_call is not None and self.total_calls >= self.force_fail_from_call:
            self.failed_calls += 1
            raise ToolFaultError(f"TIMEOUT_ERROR: {tool_name} timed out after 3000ms (Simulated Outage).")

        # simulate flaky gateway timeouts or dropouts
        if (self.total_calls % 6 == 0) or (self.rng.random() < self.fail_rate * 0.4):
            self.failed_calls += 1
            if self.total_calls % 2 == 0:
                raise ToolFaultError(f"TIMEOUT_ERROR: {tool_name} failed on 6th-call courier/DB gateway timeout.")
            return {"tool": tool_name, "error": "EMPTY_RESPONSE_FAULT", "rows": [], "results": [], "versions": []}

        if tool_name == "run_sql":
            return self.tools.run_sql(kwargs["read_only_query"])
        if tool_name == "search_docs":
            return self.tools.search_docs(kwargs["query"], top_k=kwargs.get("top_k", 5))
        if tool_name == "get_policy":
            return self.tools.get_policy(kwargs["name"], as_of_date=kwargs.get("as_of_date", "2026-10-24"))

        raise ValueError(f"Unknown tool: {tool_name}")


