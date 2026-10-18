"""Synthetic launch scenario; deterministic rules are separate from model explanations."""

from . import graph as g


def entity(id, kind, title, **properties):
    return {"id": id, "kind": kind, "title": title, "properties": properties}


def seed(tenant):
    with g.LOCK:
        if g.query(tenant, "public", "MATCH(n:Decision {id:'decision-launch'}) RETURN n.id"):
            return
        entities = [
            entity("person-maya", "Person", "Maya Chen", role="Engineering lead", initials="MC"),
            entity("person-noah", "Person", "Noah Williams", role="Backend engineer", initials="NW"),
            entity("person-zara", "Person", "Zara Patel", role="Product lead", initials="ZP"),
            entity("person-leo", "Person", "Leo Kim", role="Platform engineer", initials="LK"),
            entity("team-platform", "Team", "Platform engineering"),
            entity("team-product", "Team", "Product & launch"),
            entity("project-orbit", "Project", "Orbit launch", milestone="October release"),
            entity("repo-orbit", "Repository", "orbit-api", language="Python", path="demo/orbit-api"),
            entity("module-auth", "Module", "Authentication service", path="auth.py"),
            entity("module-billing", "Module", "Billing service", path="billing.py"),
            entity("change-auth", "Change", "Fix refresh-token rotation", commit="synthetic-4f8a2c1"),
            entity(
                "decision-launch",
                "Decision",
                "Approve Orbit launch",
                description="Ship once authentication is resolved and verification passes.",
            ),
            entity(
                "decision-billing",
                "Decision",
                "Enable annual billing",
                description="Enable annual billing when billing verification passes.",
            ),
            entity(
                "fact-auth-closed",
                "Fact",
                "Authentication issue is resolved",
                field="status",
                equals="closed",
            ),
            entity(
                "fact-auth-tested",
                "Fact",
                "Authentication verification passes",
                field="result",
                equals="passed",
            ),
            entity(
                "fact-billing-tested", "Fact", "Billing verification passes", field="result", equals="passed"
            ),
        ]
        for item in entities:
            g.add_entity(tenant, "public", item)
        sources = [
            (
                "AUTH-42",
                "AUTH-42 · Refresh-token rotation",
                "Noah fixed token rotation in auth.py. Maya reviewed PR #128. The issue is closed after verification.",
                {
                    "status": "closed",
                    "assignee": "Noah Williams",
                    "url": "https://example.com/synthetic/AUTH-42",
                    "type": "ticket",
                },
            ),
            (
                "VERIFY-AUTH",
                "Authentication verification",
                "Refresh tokens rotate successfully. Session replay is rejected. Verification passed.",
                {"result": "passed", "type": "verification"},
            ),
            (
                "PR-128",
                "PR #128 · Rotate refresh tokens",
                "Auth change by Noah Williams, reviewed by Maya Chen. Addresses AUTH-42 and modifies auth.py.",
                {"author": "Noah Williams", "reviewer": "Maya Chen", "type": "pull_request"},
            ),
            (
                "LAUNCH-PLAN",
                "Orbit launch plan",
                "Zara owns the Orbit launch. Approval depends on AUTH-42 being closed and authentication verification passing. Platform engineering owns authentication.",
                {"type": "document", "owner": "Zara Patel"},
            ),
            (
                "VERIFY-BILLING",
                "Billing verification",
                "Annual billing verification passed. Billing is independent of refresh-token rotation.",
                {"result": "passed", "type": "verification"},
            ),
            (
                "TEAM-NOTES",
                "Weekly team notes",
                "Maya reviews authentication. Noah owns the refresh-token fix. Leo owns billing verification. Zara coordinates the launch.",
                {"type": "document"},
            ),
        ]
        for args in sources:
            g.put_source(tenant, "public", *args)
        edges = [
            ("person-maya", "team-platform", "MEMBER_OF"),
            ("person-noah", "team-platform", "MEMBER_OF"),
            ("person-zara", "team-product", "MEMBER_OF"),
            ("person-leo", "team-platform", "MEMBER_OF"),
            ("team-platform", "module-auth", "OWNS"),
            ("person-maya", "module-auth", "OWNS"),
            ("person-leo", "module-billing", "OWNS"),
            ("person-zara", "decision-launch", "OWNS"),
            ("person-leo", "decision-billing", "OWNS"),
            ("person-noah", "change-auth", "AUTHORED"),
            ("person-noah", "AUTH-42", "ASSIGNED_TO"),
            ("person-maya", "PR-128", "REVIEWS"),
            ("change-auth", "module-auth", "MODIFIES"),
            ("PR-128", "AUTH-42", "ADDRESSES"),
            ("PR-128", "change-auth", "CITES"),
            ("module-auth", "project-orbit", "SUPPORTS"),
            ("repo-orbit", "module-auth", "USES"),
            ("repo-orbit", "module-billing", "USES"),
            ("decision-launch", "fact-auth-closed", "DEPENDS_ON"),
            ("decision-launch", "fact-auth-tested", "DEPENDS_ON"),
            ("fact-auth-closed", "AUTH-42", "CITES"),
            ("fact-auth-tested", "VERIFY-AUTH", "CITES"),
            ("decision-billing", "fact-billing-tested", "DEPENDS_ON"),
            ("fact-billing-tested", "VERIFY-BILLING", "CITES"),
        ]
        for edge in edges:
            g.add_edge(tenant, "public", *edge)
        g.add_entity(
            tenant, "restricted", entity("decision-security", "Decision", "Approve security rollout")
        )
        g.add_entity(
            tenant,
            "restricted",
            entity("fact-security", "Fact", "Security audit passes", field="result", equals="passed"),
        )
        g.put_source(
            tenant,
            "restricted",
            "SECURITY-AUDIT",
            "Restricted security audit",
            "Synthetic confidential audit: deployment key rotation requires review.",
            {"result": "pending", "type": "audit"},
        )
        g.add_edge(tenant, "restricted", "decision-security", "fact-security", "DEPENDS_ON")
        g.add_edge(tenant, "restricted", "fact-security", "SECURITY-AUDIT", "CITES")
        for scope in ["public", "restricted"]:
            try:
                g.graph(tenant, scope).create_node_fulltext_index("Entity", "title", "content")
            except Exception:
                pass
            g.evaluate(tenant, scope)


def apply_scenario(user, scenario):
    definitions = {
        "regression": (
            "AUTH-42",
            "AUTH-42 · Refresh-token regression",
            "Regression: refresh tokens can be replayed after rotation. AUTH-42 is reopened. Noah is investigating. Previous launch evidence must be reviewed.",
            {"status": "open", "assignee": "Noah Williams", "type": "ticket"},
        ),
        "unrelated": (
            "TEAM-NOTES",
            "Weekly team notes",
            "The team stand-up moves to 10:30. Ownership and launch conditions are unchanged.",
            {"type": "document"},
        ),
        "verification_failed": (
            "VERIFY-AUTH",
            "Authentication verification",
            "Verification failed: replayed refresh tokens remain valid.",
            {"result": "failed", "type": "verification"},
        ),
        "resolved": (
            "AUTH-42",
            "AUTH-42 · Refresh-token rotation",
            "The regression is fixed. AUTH-42 is closed following a new review.",
            {"status": "closed", "assignee": "Noah Williams", "type": "ticket"},
        ),
    }
    if scenario not in definitions:
        raise ValueError("Unknown scenario")
    args = definitions[scenario]
    revision, changed = g.put_source(user["tenant"], "public", *args)
    reviews = g.evaluate(user["tenant"], "public", args[0]) if changed else []
    return {
        "scenario": scenario,
        "source_id": args[0],
        "revision": revision,
        "changed": changed,
        "affected_decisions": reviews,
    }
