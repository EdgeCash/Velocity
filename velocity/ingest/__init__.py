"""Ingest adapters — normalize each data provider into the canonical store.

Each adapter has two layers, kept strictly separate so the test gate stays
offline:

* ``normalize_*`` — **pure** functions that map a provider's raw frame onto a
  canonical :mod:`velocity.store.schema` model. No network, fully deterministic,
  unit-tested against small frozen raw samples.
* ``load_*`` — thin wrappers that fetch live data from the provider (network)
  and hand it to the matching ``normalize_*``. Not part of the per-commit gate.

Keeping the mapping pure is what lets us prove, offline, that real provider data
lands in the store correctly typed and correctly point-in-time.
"""
