# E2.4 acceptance criteria

- **MON-01:** `fraud monitor` scores a finished run, appends exactly one history line, and `--no-append` leaves the history alone; the committed history holds the two eval runs.
- **MON-02:** any leak fails and exits 1 whatever the bands say; bands load from `config.yaml` and refuse bad values; a rate over zero cases raises no alarm.
- **MON-03:** the drift check reports PSI per numeric feature with ok, warn and alarm levels; identical batches are near 0 and shifted ones exceed 0.25.
- **MON-04:** trend data comes out in run order.
- **IMP-01:** every logged experiment names a hypothesis, a change, two different settings digests, both dev results and the verdict, kept or not.
- **IMP-02:** the rule keeps a change only when dev leakage stays 0 and auto-clear falls within tolerance, or a leak is fixed; other gains must survive the paired bootstrap with Holm; an owner verdict may differ and both are logged.
- **IMP-03:** the freeze guard demands a fresh sample when a setting changed, and does not when only the record widened.
