# Research lenses for planning

Use only the lenses that can change the decision. Each lens should produce a fact, constraint, risk, alternative, or validation step; otherwise stop using it.

## User and workflow

- Who performs the workflow and under what time or attention pressure?
- What is the current path, including loading, empty, error, retry, and recovery states?
- Which errors are silent, irreversible, or expensive for users?
- What is the smallest complete capability that solves the actual job rather than the requested mechanism?

Evidence: repository flows, analytics, support tickets, user research, comparable product behavior, and direct user input.

## Technical mechanism

- What must be true for each candidate to work?
- Which state, data, or computation moves across a boundary?
- What are latency, throughput, memory, storage, and failure characteristics at 1x and 10x scale?
- Which parts are canonical and which are open design choices?

Evidence: versioned official docs, source, tests, benchmarks, standards, implementations, and issue discussions.

## Reliability and operations

- What happens during dependency failure, partial failure, retries, duplicate delivery, timeout, deploy, restart, and rollback?
- Can support diagnose the problem from logs and metrics without special access?
- What is the recovery time and the blast radius?
- What will page an on-call engineer at 3 a.m.?

Evidence: runbooks, postmortems, incident reports, provider status/history, production telemetry, and failure tests.

## Security and permission boundaries

- What is the authorization boundary at every query, mutation, action, endpoint, webhook, and background job?
- Can an attacker exploit input, replay, confused deputy behavior, cross-tenant access, secrets, or provider callbacks?
- What data is retained, logged, exported, or sent to a third party?
- Which security assumptions require current official documentation or a review?

Evidence: security docs, threat models, standards, code paths, dependency advisories, provider guidance, and adversarial tests.

## Cost and maintainability

- What new compute, storage, network, provider, support, migration, and coordination costs appear?
- What is the switching cost if the candidate is wrong?
- Will the abstraction remain understandable and modular under future change?
- Does a dependency create lock-in or a new operational owner?

Evidence: pricing/limits, manifests, maintenance history, issue activity, team capacity, and measured resource use.

## Alternatives and counterevidence

- What are at least two structurally different ways to solve the job?
- What would make the recommended approach fail?
- Which attractive option is a trap, and what mechanism makes it fail?
- What does an adversary, competitor, regulator, or future maintainer see that the happy path misses?

Evidence: ADHD decision exploration, official limitations, negative results, issue trackers, postmortems, and local falsification tests.

## Stop test

A lens is complete when its findings are attached to a decision or phase. If another pass through the lens produces only synonyms, generic advice, or non-decision facts, mark it saturated and move on.
