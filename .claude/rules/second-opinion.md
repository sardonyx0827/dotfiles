# Second-Opinion Checkpoints

Role separation (single source of truth):

- **This rule**: when a checkpoint fires, the order of calls, and which backup fills an empty slot
- **`codex-consultation` skill**: the `codex exec` invocation contract (secret scan, sandbox, output
  files, follow-ups, triage)
- **gemini-consultant MCP server** (`consult_gemini`): the backup consultant

Second opinions run as a **pair — advisor + Codex** — in the main session, with
**gemini-consultant** as the backup that takes whichever slot cannot run.

| Member                                    | Vendor                        | Sees the transcript?                                                                                                                          |
| ----------------------------------------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| advisor (`advisorModel`, Opus)            | same-vendor                   | Yes, automatically — no prompt to author                                                                                                      |
| Codex (`codex exec -s read-only`)         | OpenAI — cross-vendor         | No — write the situation and the question into its prompt, with the raw evidence (diffs, error output, file paths), not only your conclusions |
| gemini-consultant (`consult_gemini`, Pro) | Google — cross-vendor, backup | No — give it the same situation and question you wrote for Codex                                                                              |

## When — checkpoints

- Before committing to an approach
- When first stuck
- Before declaring done

They apply to every nontrivial main-session task — evaluations, reviews and research included, not
only the spec / design / bug-fix topics the codex-consultation skill lists. Skip them for a trivial
or mechanical edit. They belong to the pair, not to the advisor tool, so they still apply when the
advisor is absent.

## Order — Codex first, unless the fallback table applies

**`advisor()` is never the first call at a checkpoint.**

1. **Launch Codex.** Write the prompt file and launch `codex exec -s read-only` in the background,
   per the codex-consultation skill's invocation contract. Do not put `advisor()` in the same
   assistant message: the advisor runs server-side while that message is generated, so it would
   finish before the Bash launch executes.
2. **Call the advisor as the very next tool call, in the same turn.** The background launch returns
   at once; as soon as its tool result arrives, call `advisor()` — Codex and the advisor then run
   in parallel. **Never end the turn in between**: a reply with no tool call ends the turn, which
   pushes the advisor past the user's next message and runs the two in series. "Message" here
   means one assistant response, not one turn; a short status line in the same response as the
   `advisor()` call is fine.
3. **Keep doing work Codex's answer cannot change** (reading, orientation), but wait for it before
   committing to the approach or declaring done.
4. **Weigh both answers together.**

The advisor tool's own description says to call it before substantive work. That still holds:
step 1 is a single Bash call and comes first anyway. A follow-up advisor call on the same question
and unchanged evidence reuses that checkpoint's Codex run; a new question or new evidence is a new
checkpoint.

## Fallback — which slot Gemini takes

| Situation                                                                                                                                                                                                                                                               | Pair becomes                                                                                                                   |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **advisor unavailable** — the advisor tool is absent from the session (a Fable main model with an Opus `advisorModel`: Claude Code attaches no advisor that ranks below the main model), or the call errors or declines to advise (not merely advice you disagree with) | **Codex + Gemini**                                                                                                             |
| **Codex unavailable** — `codex exec` exits non-zero or leaves the answer file empty                                                                                                                                                                                     | **advisor + Gemini**                                                                                                           |
| **Both unavailable**                                                                                                                                                                                                                                                    | **Gemini alone**. If Gemini fails as well, tell the user which checkpoint ran without a second opinion; never skip it silently |

- **A slow Codex run is not a failure**: wait for its completion notification, and if nothing else is
  left to do, end the turn saying you are waiting on Codex, so the user can see the wait and stop it.
- **A failure before Codex starts is not a Codex failure** — a secret-scan stop or a wrong prompt
  path: follow the codex-consultation skill's triage (remove a credential, fix a scanner that could
  not run, or fix the path) and resend; never route a prompt the scan stopped to Gemini instead.

## Escalation and action

Codex also stays the escalation for the heavier cases the **codex-consultation** /
**debugging-protocol** skills define (spec/design proposals, large-scale changes, test strategy, 2+
consecutive failed fixes on the same issue → root-cause). It acts (implement / rescue) only when
explicitly instructed — that path is the Codex plugin (`codex:codex-rescue` agent /
`/codex:rescue`), not `codex exec`.

## Subagents

The pairing is the main session's job. A subagent uses the advisor it inherits on its own, and
starts Codex or Gemini only when its prompt asks it to.

## Conflicting advice

Do not silently pick a side; surface the opinions to the user.
