# Security Guidelines

Role separation (single source of truth):

- **This rule**: triggers and response protocol only
- **`security-review` skill**: full checklist, vulnerability patterns, scan commands
- **security-reviewer agent**: review workflow and report format

## Triggers

| Situation                                                                                                                            | Action                                      |
| ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------- |
| Implementing auth, user input handling, secrets, API endpoints, payments, or file uploads                                            | Follow the **security-review** skill        |
| Before a commit or PR that touches those areas, or the secrets / permission / hook config guarding them                              | Run the **security-reviewer** agent         |
| Before a commit or PR that adds or changes a mod (`.claude/mods/**`) or what loads one (`CLAUDE_CODE_PLUGIN_DIRS`, `enabledPlugins`) | Run the **security-reviewer** agent         |
| Dependency updates or a known CVE, a security-tool alert, a user-reported concern, a production incident, or before a major release  | Run the **security-reviewer** agent         |
| Security issue discovered                                                                                                            | Follow the Security Response Protocol below |

## Pre-Commit Gate

Before ANY commit, verify at minimum:

- [ ] No hardcoded secrets (API keys, passwords, tokens)
- [ ] All user inputs validated
- [ ] Parameterized queries only (SQL injection prevention)
- [ ] Error messages and logs don't leak sensitive data

Full checklist and code patterns live in the `security-review` skill — do not duplicate them here.

## Security Response Protocol

If a security issue is found:

1. STOP immediately
2. Use **security-reviewer** agent
3. Fix CRITICAL issues before continuing
4. Rotate any exposed secrets
5. Review entire codebase for similar issues
