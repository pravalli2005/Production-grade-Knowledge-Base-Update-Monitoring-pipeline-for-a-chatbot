"""
Security Module: Role-Based Access Control (RBAC), Prompt-Injection Protection, and PII Masking.
"""
import re
import hmac
import time
import json
from typing import Dict, Any, Tuple, Optional, List
from config import API_KEYS, ROLE_PERMISSIONS, LOGS_DIR

# --- RBAC Implementation ---

class SecurityContext:
    def __init__(self, user: str, role: str, permissions: List[str]):
        self.user = user
        self.role = role
        self.permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

def authenticate_key(api_key: Optional[str]) -> Optional[SecurityContext]:
    """Authenticates API key and returns SecurityContext if valid."""
    if not api_key:
        return None
    
    clean_key = api_key.strip()
    if clean_key.startswith("Bearer "):
        clean_key = clean_key[7:].strip()
        
    for valid_key, details in API_KEYS.items():
        if hmac.compare_digest(clean_key, valid_key):
            role = details["role"]
            perms = ROLE_PERMISSIONS.get(role, [])
            return SecurityContext(user=details["user"], role=role, permissions=perms)
            
    return None

def enforce_permission(security_context: Optional[SecurityContext], required_permission: str) -> Tuple[bool, str]:
    """Checks whether the security context has the required permission."""
    if not security_context:
        return False, "401 Unauthorized: Missing or invalid API key."
    if not security_context.has_permission(required_permission):
        return False, f"403 Forbidden: User role '{security_context.role}' does not have '{required_permission}' permission."
    return True, "Authorized"


# --- Prompt-Injection Protection ---

INJECTION_PATTERNS = [
    # Direct instruction overrides
    r"(?i)\bignore\s+(all\s+)?(previous|prior|above|system)\s+(instructions|prompts|directives|rules)\b",
    r"(?i)\bdisregard\s+(all\s+)?(previous|prior|above|system)\s+(instructions|prompts|rules)\b",
    r"(?i)\bforget\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)\b",
    
    # Jailbreaks & Persona Hijacking
    r"(?i)\byou\s+are\s+now\s+(DAN|unfiltered|jailbroken|an\s+adversary|root|developer\s+mode)\b",
    r"(?i)\benter\s+developer\s+mode\b",
    r"(?i)\bdo\s+anything\s+now\b",
    r"(?i)\bbypass\s+(all\s+)?(safety|security|content|filter|policy)\s+(protocols|rules|guidelines|guardrails)\b",
    
    # System role delimiter faking
    r"(?i)(<\|im_start\|>|<\|im_end\|>|\[SYSTEM\]|\[ADMIN\]|<<SYS>>)",
    r"(?i)(---\s*BEGIN\s+SYSTEM\s+PROMPT\s*---|---\s*END\s+SYSTEM\s+PROMPT\s*---)",
    
    # Exfiltration attempts
    r"(?i)\brepeat\s+(all\s+)?(your|the)\s+(system\s+prompt|initial\s+instructions|secret\s+key)\b",
    r"(?i)\bprint\s+(your|the)\s+(system\s+prompt|hidden\s+prompt|internal\s+instructions)\b",
    r"(?i)\boutput\s+the\s+system\s+prompt\b"
]

def detect_prompt_injection(text: str) -> Dict[str, Any]:
    """
    Scans query or document content for potential prompt injection or jailbreak patterns.
    Returns:
        {
            "is_injection": bool,
            "confidence": float,
            "matched_patterns": list of matched regexes,
            "sanitized_text": sanitized version if applicable
        }
    """
    matched = []
    for pattern in INJECTION_PATTERNS:
        match = re.search(pattern, text)
        if match:
            matched.append(match.group(0))
            
    is_injection = len(matched) > 0
    confidence = min(1.0, len(matched) * 0.5) if is_injection else 0.0
    
    # Neutralize matched delimiter or dangerous directives if not completely rejected
    sanitized = text
    for m in matched:
        sanitized = sanitized.replace(m, "[BLOCKED_INJECTION_DIRECTIVE]")
        
    return {
        "is_injection": is_injection,
        "confidence": confidence,
        "matched_patterns": matched,
        "sanitized_text": sanitized
    }


# --- Sensitive Data (PII) Masking ---

# Regex patterns for sensitive entities (Order matters: SSN and Credit Cards before Phone)
PII_PATTERNS = {
    "SSN": (
        r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",
        "[REDACTED_SSN]"
    ),
    "CREDIT_CARD": (
        r"\b(?:\d{4}[-\s]){3}\d{4}\b|\b\d{16}\b|\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b",
        "[REDACTED_CREDIT_CARD]"
    ),
    "EMAIL": (
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b",
        "[REDACTED_EMAIL]"
    ),
    "API_SECRET": (
        r"(?i)\b(?:bearer\s+[A-Za-z0-9\-_]{20,}|sk-[a-zA-Z0-9]{16,}|api[_-]?key[:=]\s*['\"]?[A-Za-z0-9\-_]{16,}['\"]?)\b",
        "[REDACTED_SECRET]"
    ),
    "PHONE": (
        r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b",
        "[REDACTED_PHONE]"
    )
}

def mask_sensitive_data(text: str) -> Tuple[str, Dict[str, int]]:
    """
    Detects and masks PII / sensitive data in document or query text.
    Returns:
        (masked_text, detection_counts)
    """
    masked_text = text
    counts = {}
    
    for pii_type, (regex, replacement) in PII_PATTERNS.items():
        matches = re.findall(regex, masked_text)
        if matches:
            counts[pii_type] = len(matches)
            masked_text = re.sub(regex, replacement, masked_text)
            
    return masked_text, counts


# --- Audit Logging ---

AUDIT_LOG_FILE = LOGS_DIR / "security_audit.jsonl"

def log_security_event(event_type: str, actor: str, status: str, details: Dict[str, Any]):
    """Appends security and operational event to audit log."""
    event = {
        "timestamp": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type": event_type,
        "actor": actor,
        "status": status,
        "details": details
    }
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
