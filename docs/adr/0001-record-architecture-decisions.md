# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Four people build this project in about two days, partly in parallel and partly with AI agents. Many decisions are made quickly: which data service to trust, what runs on the server versus in the browser, how frames are aligned and grouped. Without a record, the reasons get lost, teammates relitigate settled questions, and judges see code without the reasoning behind it.

## Decision

Keep lightweight Architecture Decision Records in `docs/adr/`, one Markdown file per decision, numbered `NNNN-short-title.md`, using [`template.md`](template.md) (Status, Date, Context, Decision, Consequences, Alternatives considered). Accepted ADRs are not rewritten; a changed decision gets a new ADR that supersedes the old one. Every ADR cites the measurement or failure that motivated it when there is one.

## Consequences

- A few minutes of writing per decision.
- New teammates can read the index in [`README.md`](README.md) to learn how the system works and why.
- The index doubles as a list of trade-offs to revisit after the hackathon.

## Alternatives considered

- **Decisions only in chat or commit messages:** fast, but scattered and invisible to people who join later.
- **One long design document:** harder to keep current and to see which decision changed when.
