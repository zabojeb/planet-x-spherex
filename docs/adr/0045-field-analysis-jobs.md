# ADR-0045: Analyze a whole field as one cancellable job stored next to the sequence

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The challenge asks for tools that find things without a person watching every blink. A full-field analysis reads one finished sequence (co-adds, aligned exposures, flags) and takes seconds to a few minutes, mostly in remote catalogue and JPL calls. The sequence job queue (ADR-0033) is typed to `SequenceParams` and runs builds on workers.

## Decision

`spherex_core.field` runs every search over all sources of a built sequence: detection, cross-match, motions, shift-and-stack, intra-visit moving objects, variability, colours. Heavy work runs through `field.jobs.FieldJobs`, which keeps the sequence queue's rules: one job per id `<sequence>-<kind><hash of options>` (kinds `f` field, `k` known objects, `s` stack search), every status poll is a heartbeat, `DELETE` withdraws a client, a job with no heartbeat for 60 s or no clients is cancelled (checked between sources and steps) and answers 410. Results are written atomically to `<sequence>/field/<key>.json`, immutable for that id.

## Consequences

One request answers "what is interesting in this field"; repeat visits cost a file read. The runner lives in the API process: in the Redis deployment a field job runs on the API replica that received it and other replicas see the result only through a shared disk or after it is copied to the bucket (not done yet). Cancellation inside a JPL request waits for that request's timeout.

## Alternatives considered

Extending the Redis sequence queue to arbitrary job kinds (larger change while ADR-0033 settles); a synchronous endpoint (minutes-long requests, no cancellation).
