# Shared notification delivery policy

Updated: 2026-09-08. Policy owner: `alertnotifiers.py`, shared by provider bots and
the Python watchdogs through `verify_tools/watchdog_common.py`.

| Delivery | Local daily volume policy | Duplicate suppression |
| --- | --- | --- |
| Routine ntfy | Preserve `max(0, NTFY_DAILY_BUDGET - NTFY_URGENT_RESERVE)` | Yes |
| Urgent ntfy | No local daily cap, including after the old total is exhausted | Yes |
| Email, routine or urgent | No local daily cap | Yes |

The ntfy counter still counts all reserved attempts conservatively. The retained
reserve setting limits routine consumption to leave headroom at the provider;
it is no longer an upper bound on urgent attempts. Email's counter is telemetry
only. Legacy `EMAIL_DAILY_BUDGET` and `EMAIL_URGENT_RESERVE` overrides no longer
limit email. Existing state and counters do not need to be reset or deleted.

Urgency uses the shared title markers and watchdog source classification. The
`notify()` wrapper uses the same classification for automatic email. Routine
events do not automatically acquire an email copy; their existing opt-in remains.
Explicit email calls, including watchdog email, are not volume-capped locally.

## Provider limits and fallback

This policy cannot override ntfy or SMTP provider quotas, authentication failures,
or outages. An actual ntfy daily-quota response still blocks further ntfy requests
until the UTC daily state resets. Each urgent incident refused by that quota is
also passed to email with its original detail, including subsequent incidents
after the ntfy channel has been marked blocked. A generic quota warning is not
a substitute for forwarding the incident itself.

The phone method still returns failure when ntfy rejects it, even if the email
fallback succeeds. An email transport success means SMTP acceptance, not proof
of inbox delivery. Other phone errors keep their existing bounded retry behavior;
the shared `notify()` urgent path also attempts email independently.

Repeated identical events retain cross-process deduplication and the existing
cooldowns, so removing daily caps does not deliberately resend the same incident
on every poll. Attempt reservation precedes network I/O; this is not a durable
guaranteed-delivery queue. Existing explicit notification-disable switches remain.

## Regression coverage

`tests/test_notification_delivery_policy.py` covers exhausted legacy counters,
zero routine allowance, unlimited email with old overrides, urgent deduplication,
provider-quota fallback carrying each original incident, failed fallback reporting,
and consistent watchdog urgency. Tests mock HTTP/SMTP; they do not send live alerts.
