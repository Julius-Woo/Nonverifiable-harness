"""Render paired 4k/8k evidence without ratifying allowance decisions."""

import json

from scripts.calibrate import ALLOWANCE_CONFIGS, ROOT


def exhaustion(row):
    """Keep terminal token, step, and budget stops separate."""
    trials = row["trials"]
    return [
        sum(t[key] for t in trials)
        for key in ("token_exhaustion", "step_exhaustion", "budget_exhaustion")
    ]


def no_action_tokens(row):
    return sum(t["token_exhaustion"] and t["no_action"] for t in row["trials"])


def guard_usage(row):
    return sum(t["budget_used_usd"] for t in row["trials"])


def render_allowance(results):
    from scripts.calibration_report import table

    by_name = {r["configuration"]: r for r in results}
    pairs = [
        (by_name[label.removesuffix("-8k")], by_name[label])
        for label in ALLOWANCE_CONFIGS
        if label in by_name and label.removesuffix("-8k") in by_name
    ]
    if not pairs:
        return []
    text = [
        "## Completion allowance experiment: AD12 evidence",
        "",
        "Each comparison below is 4,096 → 8,192 reasoning-plus-output "
        "tokens per call, with the same task split, common JSON prompt, "
        "reasoning effort, 24-call limit, and $1 rollout guard. Each side "
        "contains 60 finalized attempts. Sampling seed 260910 selects "
        "the split and bootstrap; API generation seed and temperature "
        "remain omitted. The jobs ran sequentially on a shared host; "
        "Terra also changes nominal concurrency from 4 to 3, as required "
        "for coexistence with W9. These are descriptive allowance "
        "comparisons with provider-sampling and host-load variation, "
        "not an AD12 decision.",
        "",
    ]
    text += table(
        [
            "Configuration (4k → 8k)",
            "L1 passes",
            "L2 passes",
            "No action",
            "Token exhaustion (no action)",
            "24-call cap",
            "USD cap",
            "All exhaustion",
        ],
        [
            [
                new["configuration"],
                *[
                    f"{old['labels'][key]['successes']} → "
                    f"{new['labels'][key]['successes']}"
                    for key in ("pass_l1", "pass_l2")
                ],
                f"{old['no_action_count']} → {new['no_action_count']}",
                " → ".join(
                    f"{exhaustion(row)[0]} ({no_action_tokens(row)})"
                    for row in (old, new)
                ),
                *[
                    f"{exhaustion(old)[i]} → {exhaustion(new)[i]}"
                    for i in (1, 2)
                ],
                f"{sum(exhaustion(old))} → {sum(exhaustion(new))}",
            ]
            for old, new in pairs
        ],
    )
    text += table(
        [
            "Configuration (4k → 8k)",
            "Mean known USD / rollout",
            "Max known USD / rollout",
            "USD / 2,800",
            "Mean calls",
            "Mean agent s",
            "Batch wall s",
        ],
        [
            [
                new["configuration"],
                f"{old['mean_usd']:.6f} → {new['mean_usd']:.6f}",
                f"{old['max_usd']:.6f} → {new['max_usd']:.6f}",
                f"{2800 * old['mean_usd']:.2f} → {2800 * new['mean_usd']:.2f}",
                f"{old['mean_steps']:.2f} → {new['mean_steps']:.2f}",
                f"{old['mean_agent_s']:.2f} → {new['mean_agent_s']:.2f}",
                f"{old['summary'].get('wall_s', 0):.2f} → "
                f"{new['summary'].get('wall_s', 0):.2f}",
            ]
            for old, new in pairs
        ],
    )
    text += [
        "All projected costs use known response charges. Unknown-cost "
        "requests remain separately visible, with conservative "
        "reservations retained in cohort accounting; their actual "
        "charges are unresolved. API timeouts are trial exceptions "
        "under W5e, distinct from token exhaustion and Harbor timeouts.",
        "",
    ]
    text += table(
        [
            "8k configuration",
            "Known USD / 2,800",
            "Retained reserves in 60 USD",
            "USD / 2,800 with reservations",
        ],
        [
            [
                new["configuration"],
                f"{2800 * new['mean_usd']:.2f}",
                f"{max(0, guard_usage(new) - new['known_usd']):.6f}",
                f"{2800 / 60 * guard_usage(new):.2f}",
            ]
            for _, new in pairs
        ],
    )
    text += [
        "The reservation-inclusive figure scales finalized rollout guard "
        "usage by 2,800/60. It is a conservative planning proxy under the "
        "same price assumptions, not an invoice or a claim that retained "
        "reservations were billed. Both projections exclude other pilot "
        "components.",
        "",
    ]
    for old, new in pairs:
        name = new["configuration"]
        counts = new.get("no_action_details", new["no_action_reasons"])
        reasons = (
            ", ".join(
                f"{key.replace('_', ' ')}: {value}"
                for key, value in sorted(counts.items())
            )
            or "none"
        )
        text += [
            f"**{name}:** token exhaustion changed from "
            f"{exhaustion(old)[0]} to {exhaustion(new)[0]}; no action "
            f"changed from {old['no_action_count']}/60 to "
            f"{new['no_action_count']}/60 (8k reasons: {reasons}). "
            f"L1 changed from {old['labels']['pass_l1']['pass_rate']:.1%} "
            f"to {new['labels']['pass_l1']['pass_rate']:.1%}; L2 from "
            f"{old['labels']['pass_l2']['pass_rate']:.1%} to "
            f"{new['labels']['pass_l2']['pass_rate']:.1%}. "
            "Attempts with protocol errors changed from "
            f"{sum(bool(t['protocol_errors']) for t in old['trials'])} to "
            f"{sum(bool(t['protocol_errors']) for t in new['trials'])}; "
            f"24-call stops changed from {exhaustion(old)[1]} to "
            f"{exhaustion(new)[1]}. "
            f"Mean known cost changed by "
            f"{100 * (new['mean_usd'] / old['mean_usd'] - 1):+.1f}%; "
            f"the 8k solver-only pilot projection is "
            f"${2800 * new['mean_usd']:.2f}.",
            "",
        ]
    text += ["### Operational checks by configuration", ""]
    text += table(
        [
            "Configuration",
            "HTTP 400",
            "HTTP 429",
            "HTTP 5xx",
            "Harbor timeouts",
            "Command timeouts",
            "API timeout trials",
            "Null-cost records",
            "Environment failed",
        ],
        [
            [
                r["configuration"],
                r["http_400s"],
                r["http_429s"],
                r["http_5xx"],
                r["harbor_timeouts"],
                r["command_timeouts"],
                sum(
                    "Backend failed: TimeoutError"
                    in (t["exception_message"] or "")
                    for t in r["trials"]
                ),
                r["unknown_cost_calls"],
                sum(t["environment_failed"] for t in r["trials"]),
            ]
            for r in results
        ],
    )
    rejections = []
    for _, new in pairs:
        for trial in new["trials"]:
            calls = (
                ROOT
                / "logs/harbor"
                / trial["job_name"]
                / trial["trial"]
                / "agent/calls"
            )
            for path in sorted(calls.glob("*/http_error_*.json")):
                error = json.loads(path.read_text())
                body = error.get("body")
                detail = (
                    body.get("error", {}) if isinstance(body, dict) else {}
                )
                rejections.append(
                    [
                        new["configuration"],
                        trial["task"],
                        error["status_code"],
                        detail.get("code") or "unspecified",
                        "no" if trial["no_action"] else "yes",
                        f"[saved error](../{path.relative_to(ROOT)})",
                    ]
                )
    if rejections:
        text += [
            "Saved 8k HTTP rejections are reported separately from "
            "historical native-protocol rejection evidence. The common "
            "prompts were retained and no rejected calls were retried.",
            "",
        ]
        text += table(
            [
                "Configuration",
                "Task",
                "HTTP status",
                "Saved error code",
                "Action executed in attempt",
                "Evidence",
            ],
            rejections,
        )
    audit_path = (
        ROOT / "logs/calibration-8k-followup-260910/operational_audit.json"
    )
    if audit_path.exists():
        audits = json.loads(audit_path.read_text())
        text += [
            "The [8k operational audit]"
            "(../logs/calibration-8k-followup-260910/operational_audit.json) "
            "records the following admission samples. Container counts "
            "include W9; pending local trial startups are reserved before "
            "admission. Periodic samples complement per-trial admission "
            "checks and do not constitute continuous host monitoring.",
            "",
        ]
        text += table(
            [
                "Job",
                "Min MemAvailable GiB",
                "Memory pause samples",
                "Max containers in admission samples",
                "Max admitted total slots",
            ],
            [
                [
                    r["job_name"],
                    f"{r['minimum_memavailable_gib']:.2f}",
                    r["memory_pause_samples"],
                    r["maximum_observed_alexgshaw_containers"],
                    r["maximum_admitted_foreign_plus_reserved"],
                ]
                for r in audits
            ],
        )
    failures = [
        t["task"]
        for _, new in pairs
        for t in new["trials"]
        if t["environment_failed"]
    ]
    text += [
        "8k environment-start failures: "
        + (", ".join(sorted(set(failures))) if failures else "none")
        + ". No finalized attempts were excluded.",
        "",
    ]
    return text
