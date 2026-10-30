# Returns Desk

The returns desk of Larkfield Home & Apparel, a fictitious retailer, built to show the line between work a rule can do and work
that needs a model to read what a person wrote. Everything a table can answer is answered by
policy; the one question no table holds, who is the responsible party, is put to engines; a
clerk decides what is left.

## Language

### The request

**Return Request**:
A customer's ask to send an item back, naming an order, an item, a reason, and optionally a
photo. Shortened to Request inside this context.
_Avoid_: Claim, case, ticket, return

**Reason Code**:
A reason the customer picked from the retailer's fixed list. Policy maps it straight to a
responsible party.
_Avoid_: Reason type, category

**Statement**:
The customer's own account of why the item is coming back: their words and any photo. Only a
generative engine can turn a statement into a responsible party.
_Avoid_: Comment, description, note, reason text

**Customer**:
The person who placed the order and submitted the request.
_Avoid_: User, account, client, buyer

### The judgment

**Responsible Party**:
The party that bears the cost of a return: retailer, carrier, customer, or unclear. A statement of
who pays, not of blame. The one question the engines are asked.
_Avoid_: Fault, blame, liability, cause

**Engine**:
A component asked the responsible-party question about a request. Its answer is an input to
policy and the clerk, never a decision.
_Avoid_: Model, AI, agent, bot

**Generative Engine**:
The engine that reads a statement and answers with one responsible party, one reason, a draft
reply to the customer, and any flags.
_Avoid_: Reading engine, LLM engine, chat model, the LLM

**Probabilistic Engine**:
The engine that answers with a probability for every responsible party and no prose.
_Avoid_: Scoring engine, decision engine, decision model, ranker, classifier


**Agreement**:
The state where both engines named the same responsible party for a request. Policy depends
on it.
_Avoid_: Consensus, match

**Flag**:
Something the generative engine noticed about a request, such as damage in transit or possible
abuse. An observation with no authority of its own; policy may react to it, the clerk may
ignore it.
_Avoid_: Label, tag, signal, warning

### The rules

**Policy**:
The retailer's rules for returns: the window, which categories come back, when a photo is needed,
the per-quarter cap, the responsibility table, and how far engines are trusted. Costs nothing
to apply and can resolve a request on its own.
_Avoid_: Rules engine, business logic, config

**Responsibility Table**:
The part of policy that says, for a category and a responsible party, which dispositions are
allowed, the fee, and who pays return postage.
_Avoid_: Fault table, matrix

**Auto-Approval**:
A resolution made by the engines resolver without a clerk. Allowed only when every condition in
policy holds: the engines agree, the probabilistic engine's probability for that party is at or above
policy's floor, the refund is at or below policy's limit, no blocking flag is present, and the
customer is under the per-quarter cap.
_Avoid_: Auto-accept, straight-through, fast path, automation

**Delivery Date**:
The date the carrier reported the parcel delivered, held on the order. Policy counts the return
window from it and trusts it; a customer disputing it is a statement for the generative engine.
When no delivery date is held, policy cannot count the window and sends the request to the clerk.
_Avoid_: Ship date, arrival, received

**Validator**:
The check applied to every resolution before it takes effect: the disposition is in the
responsibility table, the arithmetic is right, and the refund never exceeds what was paid.
Nothing bypasses it.
_Avoid_: Guard, gate, sanity check

### The result

**Disposition**:
The kind of resolution: refund, partial, replace, deny, or escalate. Refund, partial and
replace are chosen from the set the responsibility table allows; deny and escalate are always
open to the clerk and need a note. Never chosen by an engine. Partial is clerk-only: the clerk
types the amount and the validator bounds it.
_Avoid_: Action, outcome, verdict, decision

**Resolution**:
The full result of a request: disposition, responsible party, amount, fee, postage, and who
resolved it. Escalated is terminal; what happens after is outside this context.
_Avoid_: Outcome, result, decision, settlement

**Resolution Log**:
The record of every resolution the desk has made, whatever the resolver. Clerk resolutions in
it are labeled requests, so an evaluation can read from it as well as from the sample set.
_Avoid_: Audit log, history, ledger

**Resolver**:
Who resolved a request: policy alone, engines under policy's conditions, or the clerk.
_Avoid_: Tier, lane, route, path

**Clerk**:
The person at the returns desk. The only thing in the system that decides. There is no
supervisor role; escalation leaves the system.
_Avoid_: Agent, operator, reviewer, approver

### Measuring the engines

**Labeled Request**:
A request whose responsible party is already known, either because the clerk confirmed it in
production or because it was written that way for the sample set. Evaluation treats both alike.
_Avoid_: Ground truth, gold set, truth, test case

**Sample Set**:
The hand-written labeled requests that ship with the project. The demo queue is the handful
walked on stage; the rest exist so an evaluation has enough to measure.
_Avoid_: Fixtures, seed data, test data, demo data

**Evaluation**:
A run of both engines over a set of labeled requests, reporting for each engine how often it
named the known responsible party, a grid of known party against named party, and cost per
request. Run on demand, never live on the board.
_Avoid_: Benchmark, scoring, test, eval
