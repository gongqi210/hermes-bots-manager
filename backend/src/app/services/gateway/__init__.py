"""Phase 4 gateway services — Supervisor + BroadcastHub + pairing pipeline.

The package wraps four cooperating modules:

* ``pairing_extractor`` — pure regex; turns a log line into a
  :class:`PairingCandidate` or ``None``. Source of truth for the pattern
  is captured in ``backend/tests/fixtures/hermes-cli/HERMES_V08_FINDINGS.md``
  FINDING-01.
* ``broadcast_hub`` — multi-subscriber fan-out with bounded per-subscriber
  queues + drop-newest semantics + dropped_count (D-05 / D-07 / GATEWAY-09).
* ``pairing_writer`` — DB persistence layer; sha256 hashing + 10-min TTL
  + IntegrityError dedupe backstop on the partial unique index.
* ``supervisor`` — long-lived per-Bot async task that reads a shared
  :class:`LogTailer`, filters per FINDING-03, calls ``extract_pairing`` and
  ``write_pairing``, and publishes to the per-Bot :class:`BroadcastHub`.
"""
