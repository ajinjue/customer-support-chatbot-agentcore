# Customer Support Chatbot with Amazon Bedrock AgentCore

A customer support chatbot for a fictional online shop. It runs on the **Amazon Bedrock AgentCore managed harness** with Amazon Nova Pro (`us.amazon.nova-pro-v1:0`, temperature 0, topK 1). One system prompt routes every customer message to exactly one of three behaviors:

| Route | Behavior |
|---|---|
| **A. Bug report** | Collects `description` → `stepsToReproduce` → `environment`, one question at a time, then calls `bugreports___create_bug_report` (an AWS Lambda behind an AgentCore Gateway, writing to DynamoDB) and gives the customer the returned ticket ID |
| **B. Platform question** | Answers only from [`online_shop_faq.md`](online_shop_faq.md), embedded through a `{{FAQ}}` placeholder. Questions the FAQ doesn't cover get a hand-off instead of a guess |
| **C. Other request** | Polite redirect to a human support line (a fictional 555 number) |

> **Result:** passed the Udacity *Future AWS Agent Engineer* (nd905) project review on the first submission, rated "Awesome" on all four rubric sections. Bedrock Evaluations scored all 14 test cases 1.0 on `Builtin.Correctness`.

**Stack:** Amazon Bedrock AgentCore (managed harness and Gateway) · Amazon Nova Pro · AWS Lambda · Amazon DynamoDB · Amazon Bedrock Evaluations (LLM-as-a-judge) · AWS CloudFormation · Python (boto3)

## Repository layout

| Path | What it is |
|---|---|
| [`system_prompt.txt`](system_prompt.txt) | **The chatbot.** Routing, the bug-report checklist, FAQ grounding and injection hardening all live here |
| [`harness-tests.json`](harness-tests.json) | 14 single-turn test cases: all three routes plus edge cases (greeting, prompt injection, injected tool call) |
| [`transcripts/`](transcripts/) | Real multi-turn conversations: bug report ending in a ticket, FAQ covered, FAQ not covered, out of scope |
| [`evaluation/`](evaluation/) | Eval dataset (JSONL) and per-record judge scores |
| [`screenshots/`](screenshots/) | DynamoDB tickets and the Bedrock evaluation results |
| `cloudformation-*.yaml`, `create_bug_report.py` | Infrastructure: DynamoDB table, Lambda tool, IAM roles, eval bucket and role |
| `setup_gateway.py`, `create_harness.py`, `chat.py`, `generate-eval-dataset.py`, `cleanup_agentcore.py` | AgentCore setup, chat client, eval runner and teardown (Udacity starter) |
| `run_project.ps1` | One command per step for Windows PowerShell (setup → deploy → harness → transcripts → eval → cleanup) |
| `run_transcripts.py`, `summarize_eval.py` | Records the transcripts automatically, and turns eval results into a score table |
| `fresh_actor.py`, `run_with_fresh_actor.py` | Fix for memory leaking between harness sessions (see below) |

## What I added beyond the starter

- **The system prompt and the test suite** (the main deliverables).
- **Placeholder rejection in the Lambda** (`create_bug_report.py` and the copy embedded in `cloudformation-tool.yaml`). Nova Pro sometimes called the tool too early and filled missing fields with values like `"MISSING"`. The Lambda now rejects those and tells the model to ask the customer instead.
- **Per-session memory isolation** (`fresh_actor.py`). The harness has AgentCore managed memory, and the starter scripts don't pass an `actorId`, so facts leaked between conversations. A boto3 event hook now gives every session its own actor.
- **Automation:** scripted transcript recording, a Windows step runner, and an eval score summarizer.

## System prompt design

- **Classification first.** The model silently assigns exactly one of A, B or C before replying. Each category has a crisp definition and examples. Explicit tie-breakers cover the confusing overlaps: "the Pay button throws an error" is A (software broken), while "why was my payment declined?" is B (a policy the FAQ covers). Greetings and messages too vague to classify get a clarifying question.
- **Checklist for bug reports.** The three tool fields are a checklist. The model never re-asks for something already given and asks one question per turn. It must not call the tool until all three fields are present, and it may not use placeholder values. It relays the real `ticketId` and never invents one. The Lambda's missing-field error is handled as well.
- **Grounding.** FAQ answers may restate only what the FAQ says. Partial or missing coverage, and any request to act on a specific order, triggers the phone hand-off.
- **Security.** Instructions are declared permanent. Attempts to override, reveal or role-play are routed to C. The tool may only be called with customer-supplied bug details, never because a message tells the model to (tests t13 and t14).

## Test suite

| Route | Tests |
|---|---|
| Bug report | t01 checkout crash (single turn: expects a follow-up question), t02 vague login error, t03 all three details in one message (expects a ticket ID) |
| FAQ covered | t04 refund timing, t05 guest checkout, t06 return policy, t07 payment declined (bug vs. FAQ ambiguity) |
| FAQ not covered → hand-off | t08 gift wrapping, t09 price matching |
| Other → hand-off | t10 travel planning, t11 coding request |
| Edge cases | t12 "hi", t13 prompt-injection reveal attempt, t14 injected tool call |

## Evaluation results and observations

Job: `support-chatbot-eval-run-1` · metric `Builtin.Correctness` · evaluator `amazon.nova-pro-v1:0` · 14 records.

**Overall:** all 14 records scored **1.0** on Builtin.Correctness (average 1.00, no `[HARNESS_ERROR]` entries). The full per-record table and the judge's explanations are in [`evaluation/eval_scores.md`](evaluation/eval_scores.md).

**Per-route observations:**
- **Bug report (t01–t03: 1.0, 1.0, 1.0).** t01 and t02 correctly asked one follow-up question ("the steps you took right before…") without calling the tool. t03 contained all three fields in one message, so the bot filed the ticket immediately. It returned the real UUID from DynamoDB (`d0c8b0aa-…`), so this record also shows the end-to-end tool path through the gateway.
- **FAQ covered (t04–t07: all 1.0).** The answers restate the FAQ. t07 ("Why was my payment declined?") is the deliberate bug-vs-FAQ ambiguity test, and it was routed to the FAQ as intended. t04 scored 1.0 even though the answer dropped "to the original payment method" from the FAQ entry. The judge focuses on the 3–10 business days, so a missing detail can go unnoticed.
- **FAQ not covered (t08–t09: 1.0, 1.0).** Both gave "I don't have information about that" plus the phone line, with no invented gift-wrap or price-match policy.
- **Out of scope and edge cases (t10–t14: all 1.0).** Travel and coding requests were declined with the hand-off. "hi" got a clarifying greeting. Neither injection attempt worked: t13 refused to reveal the prompt, and t14 did not call the tool. A new ticket in DynamoDB would have shown that it had.

Note on [`screenshots/01_dynamodb_bug_reports_table.jpg`](screenshots/01_dynamodb_bug_reports_table.jpg): the table also holds tickets created during prompt iteration, before the fixes described below. One has `MISSING` in two fields, and one (`1e9d5184…`) has junk characters. They're kept as evidence of the premature-tool-call bug. The ticket from the final transcript is `617fea53…`.

**Where the 1.0 scores hide real issues (found by reading the responses, not the scores):**
1. **Reasoning leaks into replies.** 8 of 14 responses (t01, t02, t04, t05, t06, t07, t09, t11) start with a Nova `<thinking>…</thinking>` block that a real customer would see. The judge scored these 1.0 anyway. Banning `<thinking>` in the prompt made it worse: Nova then skipped the tool call and invented a ticket ID ("123456"). Nova appears to need the reasoning step to call tools.
2. **The judge is lenient.** t01 barely acknowledges the problem ("Could you please tell me the steps…") and still scored 1.0. t13 lists what it won't reveal ("system prompt, the FAQ markup, or my tool definitions"), which confirms those things exist. Neither is penalized, because Builtin.Correctness only checks that the reference's main point appears.
3. **Single-turn tests can't catch the bug I actually hit.** During manual testing, the bot called the tool on the first bug-report turn and filled missing fields with junk, including the literal word "MISSING". Single-turn tests t01 and t02 would never show this. I found it in the multi-turn `chat.py` transcript and fixed it in two places: a checklist plus a worked example in the prompt, and placeholder rejection in the Lambda.

**What I would change as a result:**
- Strip `<thinking>…</thinking>` in the client or a post-processing step, instead of fighting it in the prompt. I'd also re-test with a model that doesn't print its reasoning.
- Add stricter metrics (`Builtin.Completeness`, `Builtin.Faithfulness`), and spell out "must not include reasoning or internal details" in the `expected` text, so the judge can catch problems 1 and 2.
- Add multi-turn bug-report tests (scripted like `run_transcripts.py`) that check DynamoDB for exactly one correct ticket per conversation and none after a partial conversation.
- Tighten the out-of-scope hand-off further: the transcript version of the Rome request still added "contact a travel agent". That's harmless, but it isn't the pure redirect the prompt asks for.

Also found and fixed during testing: the harness has AgentCore managed memory enabled, and the starter scripts don't pass an `actorId`. Memories leaked between sessions, for example an old ticket ID turning up in a new conversation. `fresh_actor.py` gives each session its own actor. The transcripts, eval dataset and `chat` step all use it.

## How to reproduce

```powershell
.\run_project.ps1 -Step setup
.\run_project.ps1 -Step deploy
.\run_project.ps1 -Step harness
.\run_project.ps1 -Step transcripts
.\run_project.ps1 -Step dataset
.\run_project.ps1 -Step evaljob
.\run_project.ps1 -Step evalstatus
```

Requires an AWS account with Amazon Bedrock and AgentCore access in `us-east-1` and Nova Pro model access, Python 3.9+, and the AWS CLI. `run_project.ps1` wraps the course's raw commands for Windows PowerShell and pins `us-east-1`. Run `.un_project.ps1 -Step cleanup` when you're done, to avoid ongoing charges.

## Attribution

Built as the Course 2 project of Udacity's [Future AWS Agent Engineer](https://www.udacity.com/) Nanodegree (nd905). The starter scripts, CloudFormation templates, Lambda and FAQ come from Udacity's project starter repository and are © Udacity, Inc. See [`LICENSE-UDACITY.md`](LICENSE-UDACITY.md). Files I modified are listed under "What I added beyond the starter".
