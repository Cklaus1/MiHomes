"""Billing — the Stripe seam (SPEC-004 Phase 3).

Two halves and one rule between them (`BILLING` §4.1, D2, N5):

    adapter  = vendor I/O + normalization   (`provider.py`, `stripe_provider.py`)
    service  = state + business rules       (`service.py`)

**The adapter never touches the database.** `NormalizedEvent` carries *provider* identifiers, so
mapping `provider_customer_id -> account` is the service's job. That is what keeps the adapter
swappable and testable — and it is why `FakeBillingProvider` can satisfy the Protocol with no
database at all, which in turn is why every criterion in this phase is provable without a live
Stripe account.
"""
