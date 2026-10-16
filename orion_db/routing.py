"""Conservative hybrid routing: company data stays grounded in approved documents.

Database connection / live record intents are handled by the server before this
router is consulted. Unrecognized topics use general Qwen3 unless document relevance is verified upstream.
"""
import re

COMPANY_SIGNALS = re.compile(
    r'\b(our|ours|we|us|my company|this company|internal|'
    r'company|organization|organisation|office|'
    r'policy|policies|regulation|regulations|handbook|'
    r'payroll|approval|procedure|procedures|'
    r'finance_reporting|hospital_ops)\b', re.I)

GENERAL_OPENERS = re.compile(
    r'^\s*(what (?:is|are|does|do|was|were)\b|who (?:is|was|are)\b|'
    r'why\b|how (?:does|do|is|are|can|to)\b|'
    r'explain\b|define\b|describe\b|teach me\b|'
    r'give (?:me )?(?:an? )?(?:overview|definition|explanation)\b)', re.I)


def route_question(question, config):
    """Return 'general' or 'company'. Database intents have precedence upstream."""
    if config.get('ai', {}).get('mode') != 'hybrid':
        return 'company'  # Preserve older v2 behavior for legacy configuration.
    if COMPANY_SIGNALS.search(question):
        return 'company'
    if GENERAL_OPENERS.search(question):
        return 'general'
    return 'general'  # Do not route every unfamiliar question to TXT documents.
