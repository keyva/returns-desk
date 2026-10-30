# Returns Desk: design sketch

Terms are defined in `CONTEXT.md` at the repo root. This document uses them and does not
redefine them.

Why this demo: when an engine's input is structured (category, stock), a rule would do and
the audience knows it. Here the engine's input is the customer's own words and a photo. The policy around it is a table. Nobody disputes which half is code.

The test: if an intern could write the rule in an afternoon from the policy document, it is
code. If the answer depends on reading what a person wrote or looking at a picture, it is the
model. Every step below is labeled with that test in mind.

## 1. The story

Larkfield Home & Apparel, a fictitious retailer of kitchenware and clothing that sells across
the country, runs a returns desk behind its returns portal. Customers submit a return request: order number, item,
a reason code picked from a list or a statement in their own words, optionally a photo. Policy
checks the request against the order and the rules. Requests that fail never reach an engine.
Requests with only a reason code are mapped straight to a responsible party. Requests with a
statement are put to the two engines with one question: who is the responsible party. If the
auto-approval conditions hold, policy resolves the request without a person. Everything else
goes to the clerk. The validator checks every resolution before money moves.

```
request ──> policy (code): order exists? item on order? inside window from delivery date?
               │           category returnable? photo present if price over threshold?
               │           customer under per-quarter cap?
               │
               ├─ fails ──> RESOLVED: deny, resolver=policy ──> templated reply citing the rule
               │                                                no engine, nothing billed
               │
               ├─ reason code only, no statement
               │       ──> responsible party from policy.reason_codes ──> responsibility table
               │       ──> RESOLVED: resolver=policy
               │
               └─ statement ──> generative engine  ─┐ responsible party, reason, draft reply, flags
                                probabilistic engine  ─┘ probability per responsible party
                                   │
                                   ├─ auto-approval conditions hold
                                   │     ──> responsibility table ──> RESOLVED: resolver=engines
                                   │
                                   └─ else ──> CLERK confirms the responsible party,
                                               picks a money disposition from the allowed set,
                                               or denies or escalates with a note
                                                  ──> RESOLVED: resolver=clerk
                                                  │
                                                  v
                      validator (code), on every resolution whatever the resolver:
                        disposition in the responsibility table for this category and party;
                        refund == paid - fee, or partial strictly between 0 and paid;
                        replace only if SKU in stock; deny and escalate carry a note;
                        refund never exceeds paid
```

Escalated is terminal. There is no supervisor in the system. There is no customer-facing form:
requests come from the sample set, and the demo is the clerk's board only. The board badges say
requests arrive by form in production.

## 2. Layout

```
infra/            OpenTofu: Foundry resource, deployment, project, role, budget. This repo
                  stands up its own resources, in East US, so the close "every resource on screen came from these files" stays true.
infra/hosting/    Container Apps + Entra sign-in for the returns board. Reuse as is.
app/
  data/orders.json       what was bought, delivery date, amount paid
  data/catalog.json      items with category and stock
  data/policy.json       the whole rote layer: window, categories, responsibility table, auto-approval
  data/sample_set.json   forty-six hand-written labeled requests: forty with a statement, ten
                         per responsible party, and six reason-code only; the demo queue is
                         the seven marked for the stage
  data/evaluation.json   the last saved evaluation result; the board reads this, never runs one
  logs/resolutions.jsonl the resolution log
  data/photos/           small JPEGs referenced by requests
  src/returns_desk/
    catalog.py           load catalog
    orders.py            load orders, find the line item, delivery date
    policy.py            Policy interface + JsonPolicy: assess(), responsibility_table(), auto_approval()
    model.py             the only file that imports the SDK: generative engine, probabilistic engine
    resolution.py        validator: check a resolution against policy and amount paid
    evaluation.py        run both engines over labeled requests, report accuracy and cost
    cli.py               terminal walk-through and `evaluate` command
    web.py + static/     the returns board, an evaluation page, and a labeled demo-only reset
                         button behind the sign-in that empties the queue and the resolution log
  tests/                 use canned engine answers from the sample set; never call Azure
docs/                    this sketch and the photo prompts
CONTEXT.md               the glossary
```

## 3. Data model

`orders.json`

```json
{ "order_id": "R-2041", "customer_id": "c_118", "placed": "2026-10-02", "delivered": "2026-10-05",
  "lines": [ { "sku": "KET-200", "qty": 1, "paid": 64.00 }, { "sku": "MUG-012", "qty": 2, "paid": 32.00 } ] }
```

`delivered` is the delivery date: the carrier's reported date, trusted by policy.

`catalog.json`

```json
{ "sku": "KET-200", "name": "Stovetop kettle, 2 L", "category": "kitchen_hard", "in_stock": 7 }
```

`policy.json`. This is every rule the retailer has. It fits on one slide and is shown on one.

```json
{
  "window_days": 30,
  "returns_per_quarter": 3,
  "reason_codes": { "changed_mind": "customer", "wrong_size": "customer", "wrong_item": "retailer" },
  "categories": {
    "kitchen_hard": {
      "returnable": true,
      "photo_required_above": 40.00,
      "responsibility": {
        "retailer":     { "dispositions": ["refund", "replace"],  "fee": 0.00, "return_postage": "retailer" },
        "carrier":  { "dispositions": ["refund", "replace"],  "fee": 0.00, "return_postage": "none" },
        "customer": { "dispositions": ["refund", "partial"],  "fee": 0.10, "return_postage": "customer" },
        "unclear":  { "dispositions": [] }
      }
    },
    "apparel": {
      "returnable": true,
      "photo_required_above": 100.00,
      "responsibility": {
        "retailer":     { "dispositions": ["refund", "replace"],  "fee": 0.00, "return_postage": "retailer" },
        "carrier":  { "dispositions": ["refund", "replace"],  "fee": 0.00, "return_postage": "none" },
        "customer": { "dispositions": ["refund"],             "fee": 0.00, "return_postage": "customer" },
        "unclear":  { "dispositions": [] }
      }
    },
    "perishable": { "returnable": false },
    "final_sale": { "returnable": false }
  },
  "auto_approval": {
    "require_agreement": true,
    "min_probability": 0.80,
    "max_amount": 75.00,
    "block_on_flags": ["possible_abuse", "used"]
  }
}
```

Notes on the policy:

- Almost every rule is a table keyed on a date, a category, or a responsible party. Dates and
  categories are facts the system already holds. The responsible party is the one input nobody
  typed into a field. The engines exist to turn a statement into a responsible party; from
  there the policy is arithmetic.
- `partial` is clerk-only. Policies rarely say when a partial applies; in practice it means
  "customer bears the cost but we want to keep them". The clerk types the amount, the validator
  bounds it. The engines may mention it in a reason, never select it.
- The table lists only money dispositions. Deny and escalate are the clerk's exits and are
  always available with a note; policy never forbids a clerk from refusing a refund. An
  `unclear` row is empty, which is why unclear never auto-approves.
- `auto_approval` is the policy governing the engines, not the request. The retailer decides how
  much it trusts agreement, in a file, not a prompt.
- No delivery date held (a lost parcel the carrier never scanned) is not a window denial. Policy
  skips the window and the photo rule, still applies the cap, and sends the request to the clerk;
  the engines read any statement, but auto-approval never applies.
- A photo is evidence for putting the cost on the retailer or the carrier. A request with only a
  reason code that puts the cost on the customer (`changed_mind`, `wrong_size`) needs none.
- The per-quarter cap counts returns, not requests: a request the log shows denied uses none of it.
  An escalated request still counts, since it may yet be granted.
- Abuse is not a concept. The per-quarter count is policy, `possible_abuse` is a flag on the
  request, and the clerk connects them. No customer standing persists across requests.
- Left out on purpose: size exchanges, gift returns, international shipping, warranty handoff
  to the manufacturer, customer standing. All real, none adds to the point.

`sample_set.json`, one labeled request:

```json
{ "request_id": "RQ-301", "order_id": "R-2041", "sku": "KET-200",
  "submitted": "2026-10-14",
  "reason_code": null,
  "statement": "Box arrived crushed, the handle was snapped clean off. I have not used it.",
  "photo": "photos/kettle-crushed.jpg",
  "label": { "responsible_party": "carrier", "source": "author" },
  "demo": true }
```

`reason_code` is a value from the retailer's list, or null. `statement` is the customer's own words.
`label` is what makes it a labeled request; `source` is `author` for the sample set and
`clerk` for requests confirmed in production. `demo` marks the handful walked on stage. The
engines are called only when there is a statement or a photo to read.

## 4. Engine boundary (`model.py`)

Two engines, Entra auth, and usage accounting against a price table.
The engines are asked one question: who is the responsible party.

Generative engine, structured output:

```python
class Reading(BaseModel):
    responsible_party: Literal["retailer", "carrier", "customer", "unclear"]
    reason: str          # one sentence the clerk reads
    reply_draft: str     # three sentences max, to the customer, in the retailer's voice
    flags: list[Literal["damage_in_transit", "defect", "remorse", "wrong_item",
                        "used", "possible_abuse"]]
```

If a photo is attached it goes in as an image part. The reply draft stays in the schema and
is shown on the card behind a toggle, folded until the script calls for it.

Probabilistic engine: same request, returns a probability for each of the four responsible parties
and a confidence. No prose. Billed on input only. The probability is what makes the
auto-approval floor possible; this is the job the generative engine cannot do.

Agreement is shown on the board as a chip: green when both engines named the same party, amber
otherwise.

## 5. Resolvers

| Resolver | Who decides | Cost | Examples in the demo queue |
|---|---|---|---|
| policy | code | nothing | cheese box, late jacket, changed-mind t-shirt |
| engines | code, when the auto-approval conditions hold | two engine calls | crushed kettle at $64 |
| clerk | the clerk | two engine calls | blender where the engines split; headphones flagged; anything over $75 |

Policy denials carry a templated reply citing the rule. A reason-code-only request has nothing
to read and nothing to confirm. Auto-approval is where the probabilistic engine earns its keep. The
clerk sees only what is left.

## 6. Validator (`resolution.py`)

Code, no model, runs on every resolution whatever the resolver:

- a money disposition is in the responsibility table for this category and party; deny and
  escalate are always allowed to the clerk
- refund equals paid minus fee, or for partial is strictly between zero and paid
- replacement only if the SKU is in stock
- deny and escalate carry the reply draft or a clerk note
- the refund never exceeds `paid`, whatever the engines or the clerk typed

The line to say out loud: the engines read, the policy decides what is possible, the clerk
chooses, and the arithmetic is never the model's job.

## 7. Evaluation (`evaluation.py`)

Runs both engines over every labeled request, from the sample set and from clerk resolutions
in the resolution log, on the real deployment. Reports per engine: how often it named the known
responsible party, a four-by-four grid of known party against named party, and cost per request
from the usage accounting. Runs from `cli.py evaluate` and writes `data/evaluation.json`; the
board's evaluation page reads that file and never runs an evaluation itself, since forty
statements times two engines is too slow for a stage and costs real tokens. Calibration of the
probabilistic engine's probabilities is left out: forty requests is too few to say anything honest.

The sample set is forty-six requests written by hand. Forty have a statement, ten per
responsible party, varied the way real customers are; these are what an evaluation measures. The
other six carry only a reason code, have nothing to read, and are resolved by policy. Generated statements were considered and set aside: they risk the evaluation
measuring how well one model reads another model's prose.

## 8. Demo queue (seven requests, one per branch)

| Request | What the customer sent | Resolver | Engines? | Expected resolution |
|---|---|---|---|---|
| RQ-303 | Cheese box, "tasted off" | policy | no | deny: perishable. Nothing billed. |
| RQ-304 | Jacket, 47 days after delivery | policy | no | deny: window. Nothing billed. |
| RQ-302 | T-shirt, reason code changed_mind, no statement | policy | no | customer from the code, refund, no fee. |
| RQ-301 | Kettle $64, crushed box, handle snapped, photo | engines | yes | both say carrier, p=0.85, under $75, no flags. Auto-approval, refund $64. |
| RQ-307 | Same story, a $140 stand mixer | clerk | yes | engines agree, over the limit. Clerk confirms carrier, picks refund. |
| RQ-305 | Blender, "grinding noise after two months, worked fine before" | clerk | yes | generative engine says retailer; probabilistic engine splits retailer 0.48 / customer 0.44. Amber. Clerk decides. |
| RQ-306 | Headphones, "never arrived", delivery date says delivered, third request this quarter | clerk | yes | possible_abuse flag, unclear. Blocked from auto-approval. Clerk escalates. |

RQ-305 is the one to linger on: two engines disagreeing on a judgment call where neither is
wrong, which is the whole reason a clerk sits in the loop.

## 9. Demo script (five minutes)

1. Open the board. Badges: Foundry, model, auth mode per engine. Point at `policy.json` on the
   side panel: "this is every rule the retailer has".
2. Pull RQ-303 and RQ-304. Both denied by policy. Cost tiles do not move. "A rule knew."
3. Pull RQ-302. Reason code, nothing to read. Mapped and refunded. Still no engine.
4. Pull RQ-301. A statement and a photo. Left card: the generative engine's party, reason, draft
   reply. Right card: the probabilistic engine's probabilities. Green chip. Auto-approval. Show the
   refund arithmetic and the cost tiles moving by a fraction of a cent.
5. Pull RQ-307. Same story, bigger number. Engines agree, policy sends it to the clerk anyway.
   Confirm carrier, pick refund. Then raise `max_amount` in the policy file to 150, press the
   demo reset, and pull RQ-307 again: auto-approval. Nobody touched the model.
6. Pull RQ-305. Amber chip. Read both reasons aloud. Pick one. The validator ran regardless.
7. Pull RQ-306. Flag, unclear. Escalate. Nobody refunded anything.
8. Open the evaluation page: accuracy and cost per request for each engine over the whole
   sample set. End on `infra/`.

## 10. Notes on the models and the design

- The photos are AI-generated; the prompts are in `PHOTO-PROMPTS.md` and
  `app/data/photos/PROMPTS-USED.md`. Both engines get the photo along with the statement.
  Microsoft Learn's Foundry model table (checked 2026-10-30) lists gpt-6-luna version
  2026-09-22 as "text and image input with text output" for chat completions, and the
  Decisions API page says it "evaluates text, images, or both", taking PNG, JPG, WebP or GIF,
  with gpt-6-luna currently the only supported model. The part names differ: chat completions
  use `text` and `image_url`, Decisions uses `input_text` and `input_image`.
  Sources: https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure#gpt-6
  and https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/decisions
- East US 2 is excluded from the Decisions rollout, so this demo uses East US.
- One user-reported caveat on Microsoft Q&A, not official: the Azure build tokenizes images at
  roughly 2.4 times fewer tokens than the OpenAI API and has dropped digits from scanned
  documents. Irrelevant for photos of a broken kettle, worth knowing if a receipt ever goes in.
- The desk's date defaults to 2026-10-30, the sample set's date, not the real one. The
  per-quarter count is taken over today's quarter, so on a real date after December 2026 every
  sample customer would read zero returns this quarter and the demo would stop matching section 8.
  `RETURNS_DESK_TODAY` overrides it.
- Open Policy Agent was considered for the policy layer and set aside: it makes a second point
  (policy owned and tested outside the app) that competes with the first for the same five
  minutes, and a thirty-line policy does not justify its own runtime. The `Policy` interface in
  `policy.py` is where it would drop in if a platform audience asks. One sentence on stage at
  most. The choice is easy to reverse.
