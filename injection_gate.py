# injection_gate.py
import re
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple


@dataclass
class GateDecision:
    allowed: bool
    reason: str
    attack_detected: bool
    source_doc_id: str = ""


class TaintScanner:
    INJECTION_PATTERNS = [
        (r"ignore\s+previous\s+instructions", "BLATANT_SYSTEM_OVERRIDE"),
        (r"system\s+override", "BLATANT_SYSTEM_OVERRIDE"),
        (r"send_seller_whatsapp", "TOOL_INVOCATION_IN_DATA"),
        (r"system\s+se\s+polite\s+request", "SUBTLE_HINGLISH_TOOL_HIJACK"),
        (r"credit\s+approved", "UNAUTHORIZED_PAYOUT_INSTRUCTION"),
    ]

    @classmethod
    def scan_document(cls, doc: Dict[str, Any]) -> Tuple[bool, List[str]]:
        text = doc.get("text", "")
        hits = []
        for pattern, label in cls.INJECTION_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                hits.append(label)
        return (len(hits) > 0), hits


class SideEffectGate:
    ALLOWED_SELLER_IDS = {"10101", "20202", "30303"}
    FORBIDDEN_MESSAGE_TERMS = {"credit approved", "5000 credit", "5,000 credit", "override"}

    def __init__(self, log_path: str = "whatsapp_dispatches.log"):
        self.log_path = log_path
        self.blocked_attempts: List[Dict[str, Any]] = []
        self.successful_dispatches: List[Dict[str, Any]] = []

    def check_and_execute(
        self,
        raw_user_query: str,
        seller_id: str,
        message: str,
        triggered_by_doc_id: str = "",
    ) -> GateDecision:
        q_lower = raw_user_query.lower()
        msg_lower = message.lower()
        seller_str = str(seller_id).strip()

        user_requested_whatsapp = (
            ("whatsapp" in q_lower or "message seller" in q_lower or "notify seller" in q_lower)
            and ("send" in q_lower or "bhej" in q_lower or "dispatch" in q_lower)
        )
        if not user_requested_whatsapp:
            decision = GateDecision(
                allowed=False,
                reason=(
                    f"ORIGIN_VIOLATION: Side-effect send_seller_whatsapp(seller_id='{seller_str}') "
                    f"was triggered by retrieved data ({triggered_by_doc_id or 'document'}), "
                    f"NOT by the user's message."
                ),
                attack_detected=True,
                source_doc_id=triggered_by_doc_id,
            )
            self.blocked_attempts.append({
                "seller_id": seller_str,
                "message": message,
                "source_doc_id": triggered_by_doc_id,
                "reason": decision.reason,
            })
            return decision

        if seller_str not in q_lower:
            decision = GateDecision(
                allowed=False,
                reason=f"TARGET_MISMATCH: Seller ID '{seller_str}' was not mentioned in the user's prompt.",
                attack_detected=True,
                source_doc_id=triggered_by_doc_id,
            )
            self.blocked_attempts.append({"seller_id": seller_str, "reason": decision.reason})
            return decision

        if seller_str not in self.ALLOWED_SELLER_IDS:
            decision = GateDecision(
                allowed=False,
                reason=f"ALLOWLIST_DENIED: Seller ID '{seller_str}' is not on the WhatsApp allow-list.",
                attack_detected=True,
                source_doc_id=triggered_by_doc_id,
            )
            self.blocked_attempts.append({"seller_id": seller_str, "reason": decision.reason})
            return decision

        for bad_term in self.FORBIDDEN_MESSAGE_TERMS:
            if bad_term in msg_lower:
                decision = GateDecision(
                    allowed=False,
                    reason=f"PAYLOAD_BLOCKED: Message contains restricted financial phrase '{bad_term}'.",
                    attack_detected=True,
                    source_doc_id=triggered_by_doc_id,
                )
                self.blocked_attempts.append({"seller_id": seller_str, "reason": decision.reason})
                return decision

        entry = f"SENT WhatsApp -> seller_id={seller_str} | message={message}\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry)
        self.successful_dispatches.append({"seller_id": seller_str, "message": message})
        return GateDecision(allowed=True, reason="AUTHORIZED_BY_USER", attack_detected=False)
