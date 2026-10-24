import re


class GateDecision:
    def __init__(self, allowed, reason, attack_detected=False, source_doc_id=""):
        self.allowed = allowed
        self.reason = reason
        self.attack_detected = attack_detected
        self.source_doc_id = source_doc_id


class TaintScanner:
    PATTERNS = [
        (re.compile(r"ignore\s+previous\s+instructions", re.I), "BLATANT_SYSTEM_OVERRIDE"),
        (re.compile(r"system\s+override", re.I), "BLATANT_SYSTEM_OVERRIDE"),
        (re.compile(r"send_seller_whatsapp", re.I), "TOOL_INVOCATION_IN_DATA"),
        (re.compile(r"system\s+se\s+polite\s+request", re.I), "SUBTLE_HINGLISH_TOOL_HIJACK"),
        (re.compile(r"credit\s+approved", re.I), "UNAUTHORIZED_PAYOUT_INSTRUCTION"),
    ]

    @classmethod
    def scan_document(cls, doc):
        txt = doc.get("text", "")
        hits = [label for rx, label in cls.PATTERNS if rx.search(txt)]
        return len(hits) > 0, hits


class SideEffectGate:
    ALLOWED_SELLERS = {"10101", "20202", "30303"}
    FORBIDDEN_TERMS = {"credit approved", "5000 credit", "5,000 credit", "override"}

    def __init__(self, log_path="whatsapp_dispatches.log"):
        self.log_path = log_path
        self.blocked_attempts = []
        self.successful_dispatches = []

    def check_and_execute(self, raw_user_query, seller_id, message, triggered_by_doc_id=""):
        q = raw_user_query.lower()
        msg = message.lower()
        sid = str(seller_id).strip()

        # user must explicitly instruct whatsapp dispatch in their query
        has_channel = any(k in q for k in ("whatsapp", "message seller", "notify seller"))
        has_intent = any(k in q for k in ("send", "bhej", "dispatch"))
        if not (has_channel and has_intent):
            doc_label = triggered_by_doc_id or "document"
            err = (
                f"ORIGIN_VIOLATION: Side-effect send_seller_whatsapp(seller_id='{sid}') "
                f"was triggered by retrieved data ({doc_label}), NOT by the user's message."
            )
            self.blocked_attempts.append({
                "seller_id": sid, "message": message, "source_doc_id": triggered_by_doc_id, "reason": err
            })
            return GateDecision(False, err, attack_detected=True, source_doc_id=triggered_by_doc_id)

        if sid not in q:
            err = f"TARGET_MISMATCH: Seller ID '{sid}' was not mentioned in the user's prompt."
            self.blocked_attempts.append({"seller_id": sid, "reason": err})
            return GateDecision(False, err, attack_detected=True, source_doc_id=triggered_by_doc_id)

        if sid not in self.ALLOWED_SELLERS:
            err = f"ALLOWLIST_DENIED: Seller ID '{sid}' is not on the WhatsApp allow-list."
            self.blocked_attempts.append({"seller_id": sid, "reason": err})
            return GateDecision(False, err, attack_detected=True, source_doc_id=triggered_by_doc_id)

        for bad in self.FORBIDDEN_TERMS:
            if bad in msg:
                err = f"PAYLOAD_BLOCKED: Message contains restricted financial phrase '{bad}'."
                self.blocked_attempts.append({"seller_id": sid, "reason": err})
                return GateDecision(False, err, attack_detected=True, source_doc_id=triggered_by_doc_id)

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"SENT WhatsApp -> seller_id={sid} | message={message}\n")
        except OSError:
            pass

        self.successful_dispatches.append({"seller_id": sid, "message": message})
        return GateDecision(True, "AUTHORIZED_BY_USER", attack_detected=False)


