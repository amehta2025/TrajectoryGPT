Meta Hackathon Project

## Known limitations

(The full list from SPEC §16 is added on Day 7.)

- **Plan steps share one set of approved parameters.** The plan's dataset, groups, cutoff, windows, stride, sequence exclusion and robustness cutoffs apply to every step, and the backend enforces them. Steps do not carry parameters of their own. The agent picks per-call details within those limits, such as which residue pair to examine.
