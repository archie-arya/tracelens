"""Tiny in-memory knowledge base + keyword retriever used by the RAG examples (no vector DB needed)."""
import re

DOCS = [
    {"id": "refunds", "title": "Refund policy", "text": "Acme Cloud offers a full refund within 30 days of purchase for annual plans. Monthly plans can be cancelled anytime but are not refunded for the current month. Refunds are processed in 5-7 business days to the original payment method."},
    {"id": "pricing", "title": "Pricing", "text": "Acme Cloud has three plans: Starter at $9/month (2 vCPU, 4 GB RAM), Pro at $29/month (4 vCPU, 16 GB RAM, daily backups) and Enterprise with custom pricing, SSO and a 99.99% SLA. Annual billing saves 20%."},
    {"id": "sla", "title": "Uptime SLA", "text": "Pro plans include a 99.9% monthly uptime SLA. If uptime falls below the SLA, customers receive service credits of 10% for each 0.1% below target, up to 50% of the monthly bill. Enterprise plans have a 99.99% SLA."},
    {"id": "regions", "title": "Regions", "text": "Acme Cloud runs data centers in Mumbai, Singapore, Frankfurt, Virginia and Sao Paulo. Customers can choose the region at signup; data never leaves the chosen region unless replication is enabled."},
    {"id": "backups", "title": "Backups", "text": "Daily automated backups are included on Pro and Enterprise. Backups are kept for 14 days on Pro and 90 days on Enterprise. Restores can be triggered from the console or the API and usually complete within 15 minutes."},
    {"id": "support", "title": "Support", "text": "Starter customers get email support with a 48-hour response time. Pro customers get 24/7 chat support with a 4-hour response time. Enterprise includes a dedicated technical account manager and 30-minute response for critical incidents."},
    {"id": "security", "title": "Security", "text": "All data is encrypted at rest with AES-256 and in transit with TLS 1.3. Acme Cloud is SOC 2 Type II and ISO 27001 certified. Two-factor authentication is available for all accounts and enforced on Enterprise."},
    {"id": "gpu", "title": "GPU instances", "text": "GPU instances with NVIDIA L4 GPUs are available in Mumbai and Virginia at $0.80 per hour, billed per second. They are intended for inference workloads; training clusters require an Enterprise contract."},
]


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", s.lower())) - {"the", "a", "is", "of", "and", "to", "for", "do", "i", "my", "what", "how", "in", "on", "are", "can", "you"}


def retrieve(query: str, k: int = 2) -> list:
    q = _tokens(query)
    scored = sorted(DOCS, key=lambda d: -len(q & _tokens(d["title"] + " " + d["text"])))
    return [{"id": d["id"], "title": d["title"], "content": d["text"], "score": len(q & _tokens(d["text"]))} for d in scored[:k]]
