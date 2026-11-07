import re
import ollama
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine

GUARD_MODEL = "llama-guard3:1b"

# =====================================================================
# 1. INITIALIZE LOCAL MICROSOFT PRESIDIO PII REDACTION ENGINE
# =====================================================================
analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

# Custom recognizer for your personal Vault PIN format (e.g., 9942-ALPHA)
vault_pin_pattern = Pattern(name="vault_pin_pattern", regex=r"\b\d{4}-[A-Z]{4,10}\b", score=0.95)
vault_recognizer = PatternRecognizer(supported_entity="VAULT_SECRET_PIN", patterns=[vault_pin_pattern])
analyzer.registry.add_recognizer(vault_recognizer)

# Deterministic high-risk prompt injection & jailbreak signatures
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"disregard\s+(all\s+)?(previous|system)\s+(instructions|prompts)",
    r"you\s+are\s+now\s+(in\s+)?(developer|dan|unrestricted|root)\s+mode",
    r"bypass\s+(rbac|security|clearance|guardrails)",
    r"print\s+(your\s+)?(system\s+prompt|initial\s+instructions)",
]

# Categories allowed for a Personal Assistant 
ALLOWED_PERSONAL_CATEGORIES = {"S1", "S2", "S6", "S8"}


# =====================================================================
# 2. INPUT GUARDRAIL: PROMPT INJECTION & JAILBREAK DETECTION
# =====================================================================
def check_input_guardrail(text: str) -> dict:
    """
    Two-stage local input guardrail:
    Stage A: Instant regex signature check for prompt-injection overrides.
    Stage B: Local Llama-Guard-3-1B classifier on RTX 3070 for malicious payloads.
    """
    # Stage A: Fast Prompt Injection Check
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return {
                "allowed": False,
                "reason": f"PROMPT INJECTION BLOCKED: Detected instruction-override pattern ('{pattern}').",
            }

    # Stage B: Local Llama-Guard-3-1B Safety Classifier
    res = ollama.chat(
        model=GUARD_MODEL,
        messages=[{"role": "user", "content": text}],
    )
    verdict = res["message"]["content"].strip()

    if verdict.lower().startswith("unsafe"):
        lines = verdict.splitlines()
        raw_cats = lines[1].strip() if len(lines) > 1 else "S1"
        flagged_cats = {c.strip() for c in raw_cats.split(",")}

        # Filter out allowed categories so personal fitness/contract/finance queries aren't false-flagged
        blocking_cats = flagged_cats - ALLOWED_PERSONAL_CATEGORIES
        if blocking_cats:
            return {
                "allowed": False,
                "reason": f"LLAMA-GUARD-3 BLOCKED: Input flagged as unsafe ({', '.join(blocking_cats)}).",
            }
        else:
            # Log when we intentionally bypass a false positive
            print(f"[*] Ignored Llama-Guard false positive for categories: {', '.join(flagged_cats)}")

    return {"allowed": True, "reason": "SAFE"}


# =====================================================================
# 3. OUTPUT GUARDRAIL: PII & SECRET REDACTION (PRESIDIO)
# =====================================================================
def redact_sensitive_output(text: str, user_role: str = "standard") -> str:
    """
    Scans LLM output using Microsoft Presidio and masks PII/secrets.
    - 'standard' role: Redacts emails, phone numbers, IBANs, credit cards, and Vault PINs.
    - 'vault_admin' role: Allows personal facts through, but still masks raw credit cards.
    """
    if user_role == "vault_admin":
        entities_to_redact = ["CREDIT_CARD"]
    else:
        entities_to_redact = [
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "IBAN_CODE",
            "CREDIT_CARD",
            "US_SSN",
            "VAULT_SECRET_PIN",
        ]

    results = analyzer.analyze(
        text=text,
        entities=entities_to_redact,
        language="en",
    )

    if not results:
        return text

    anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
    return anonymized.text