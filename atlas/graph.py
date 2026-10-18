"""Native FalkorDB queries are the authority for evidence, decisions, and task state."""

import hashlib
import json
import re
import threading
from uuid import uuid4

from falkordb import FalkorDB

from .config import DB_HOST, DB_PASSWORD, DB_PORT
from .storage import now

LOCK = threading.RLock()
EDGE_TYPES = {
    "OWNS",
    "MEMBER_OF",
    "AUTHORED",
    "MODIFIES",
    "ADDRESSES",
    "SUPPORTS",
    "DEPENDS_ON",
    "CITES",
    "HAS_REVISION",
    "ASSIGNED_TO",
    "REVIEWS",
    "USES",
}
KINDS = {
    "Person",
    "Team",
    "Project",
    "Repository",
    "Module",
    "Change",
    "Ticket",
    "Document",
    "Decision",
    "Fact",
    "Source",
}


def graph_name(tenant, scope="public"):
    if scope not in {"public", "restricted"} or not re.fullmatch(r"[a-zA-Z0-9_]{1,80}", tenant):
        raise ValueError("Invalid graph scope")
    return f"atlas_{tenant}_{scope}"


def graph(tenant, scope="public"):
    return FalkorDB(host=DB_HOST, port=DB_PORT, password=DB_PASSWORD, socket_timeout=30).select_graph(
        graph_name(tenant, scope)
    )


def query(tenant, scope, cypher, params=None):
    return graph(tenant, scope).query(cypher, params or {}).result_set


def scopes(user):
    return ["public", "restricted"] if user["role"] in {"owner", "engineer"} else ["public"]


def health():
    return bool(
        FalkorDB(host=DB_HOST, port=DB_PORT, password=DB_PASSWORD, socket_timeout=3).connection.ping()
    )


def add_entity(tenant, scope, item):
    kind = item["kind"]
    if kind not in KINDS:
        raise ValueError("Unknown entity kind")
    props = item.get("properties", {})
    query(
        tenant,
        scope,
        f"MERGE (n:Entity:{kind} {{id:$id}}) SET n.kind=$kind,n.title=$title,n.content=$content,n.data=$data,n.scope=$scope",
        {
            "id": item["id"],
            "kind": kind,
            "title": item["title"],
            "content": item.get("content", ""),
            "data": json.dumps(props),
            "scope": scope,
        },
    )


def add_edge(tenant, scope, source, target, relation):
    if relation not in EDGE_TYPES:
        raise ValueError("Unsupported relationship")
    rows = query(
        tenant,
        scope,
        f"MATCH(a:Entity {{id:$source}}),(b:Entity {{id:$target}}) MERGE(a)-[r:{relation}]->(b) RETURN a.id",
        {"source": source, "target": target},
    )
    if not rows:
        raise ValueError("Both relationship endpoints must exist in the same scope")


def put_source(tenant, scope, source_id, title, content, metadata=None, expected_revision=None):
    content_hash = hashlib.sha256(
        json.dumps({"content": content, "metadata": metadata or {}}, sort_keys=True).encode()
    ).hexdigest()
    with LOCK:
        rows = query(
            tenant, scope, "MATCH(s:Entity:Source {id:$id}) RETURN s.revision,s.hash", {"id": source_id}
        )
        current = rows[0][0] if rows else 0
        if expected_revision is not None and expected_revision != current:
            raise ValueError("Source changed since it was read. Refresh before updating.")
        if rows and rows[0][1] == content_hash:
            return current, False
        revision = current + 1
        params = {
            "id": source_id,
            "title": title,
            "content": content,
            "data": json.dumps(metadata or {}),
            "hash": content_hash,
            "revision": revision,
            "rid": f"{source_id}@{revision}",
            "time": now(),
            "scope": scope,
        }
        query(
            tenant,
            scope,
            """MERGE(s:Entity:Source {id:$id})
          SET s.kind='Source',s.title=$title,s.content=$content,s.data=$data,s.revision=$revision,s.hash=$hash,s.scope=$scope,s.updated_at=$time
          CREATE(v:SourceRevision {id:$rid,source_id:$id,title:$title,content:$content,data:$data,revision:$revision,observed_at:$time})
          CREATE(s)-[:HAS_REVISION]->(v)""",
            params,
        )
        return revision, True


def source_history(user, source_id):
    for scope in scopes(user):
        rows = query(
            user["tenant"],
            scope,
            "MATCH(s:Source {id:$id})-[:HAS_REVISION]->(v:SourceRevision) RETURN properties(v) ORDER BY v.revision DESC",
            {"id": source_id},
        )
        if rows:
            return [
                {**row[0], "metadata": json.loads(row[0].get("data", "{}")), "scope": scope} for row in rows
            ]
    raise LookupError("Source not found")


def snapshot(user):
    nodes, edges = [], []
    for scope in scopes(user):
        for (p,) in query(user["tenant"], scope, "MATCH(n:Entity) RETURN properties(n)"):
            p["properties"] = json.loads(p.pop("data", "{}"))
            p["scope"] = scope
            nodes.append(p)
        for a, b, kind in query(
            user["tenant"], scope, "MATCH(a:Entity)-[r]->(b:Entity) RETURN a.id,b.id,type(r)"
        ):
            edges.append({"id": f"{a}:{kind}:{b}", "source": a, "target": b, "kind": kind, "scope": scope})
    return {"nodes": nodes, "edges": edges}


def evaluate(tenant, scope, changed_source=None):
    params = {"id": changed_source} if changed_source else {}
    condition = (
        "MATCH(d:Decision)-[:DEPENDS_ON*1..8]->(f:Fact)-[:CITES]->(s:Source {id:$id})"
        if changed_source
        else "MATCH(d:Decision)"
    )
    ids = [r[0] for r in query(tenant, scope, condition + " RETURN DISTINCT d.id", params)]
    changed = []
    for decision_id in ids:
        rules = query(
            tenant,
            scope,
            "MATCH(d:Decision {id:$id})-[:DEPENDS_ON*1..8]->(f:Fact) OPTIONAL MATCH(f)-[:CITES]->(s:Source) RETURN DISTINCT properties(f),properties(s)",
            {"id": decision_id},
        )
        checks = []
        for fact, source in rules:
            rule = json.loads(fact["data"])
            data = json.loads(source["data"]) if source else {}
            source = source or {}
            actual = data.get(rule["field"])
            checks.append(
                {
                    "fact_id": fact["id"],
                    "condition": fact["title"],
                    "source_id": source.get("id"),
                    "source_title": source.get("title", "Missing source"),
                    "revision": source.get("revision"),
                    "field": rule["field"],
                    "expected": rule["equals"],
                    "actual": actual,
                    "missing": not source or rule["field"] not in data,
                    "passed": bool(source) and rule["field"] in data and actual == rule["equals"],
                }
            )
        checks.sort(key=lambda c: (c["fact_id"], c["source_id"] or ""))
        limited = bool(
            query(
                tenant,
                scope,
                "MATCH(d:Decision {id:$id})-[:DEPENDS_ON*8]->(n)-[:DEPENDS_ON]->(next) RETURN next.id LIMIT 1",
                {"id": decision_id},
            )
        )
        status = "current" if checks and all(c["passed"] for c in checks) and not limited else "needs_review"
        previous = query(
            tenant,
            scope,
            "MATCH(d:Decision {id:$id}) RETURN d.status,d.checks,d.traversal_limit_reached",
            {"id": decision_id},
        )
        if previous and previous[0][0] == "superseded":
            continue
        checks_json = json.dumps(checks)
        if not previous or previous[0] != [status, checks_json, limited]:
            query(
                tenant,
                scope,
                """MATCH(d:Decision {id:$id}) SET d.status=$status,d.checks=$checks,d.checked_at=$time,d.traversal_limit_reached=$limited
              CREATE(v:DecisionReview {id:$review_id,status:$status,checks:$checks,observed_at:$time,traversal_limit_reached:$limited})
              CREATE(d)-[:HAS_REVIEW]->(v)""",
                {
                    "id": decision_id,
                    "status": status,
                    "checks": checks_json,
                    "time": now(),
                    "review_id": uuid4().hex,
                    "limited": limited,
                },
            )
        changed.append(
            {"id": decision_id, "status": status, "checks": checks, "traversal_limit_reached": limited}
        )
    return changed


def decisions(user):
    result = []
    for scope in scopes(user):
        rows = query(
            user["tenant"],
            scope,
            "MATCH(d:Decision) OPTIONAL MATCH(p:Person)-[:OWNS]->(d) RETURN properties(d),p.title",
            {},
        )
        for d, owner in rows:
            history = query(
                user["tenant"],
                scope,
                "MATCH(d:Decision {id:$id})-[:HAS_REVIEW]->(r:DecisionReview) RETURN properties(r) ORDER BY r.observed_at DESC LIMIT 15",
                {"id": d["id"]},
            )
            result.append(
                {
                    **d,
                    "properties": json.loads(d.pop("data", "{}")),
                    "checks": json.loads(d.get("checks", "[]")),
                    "owner": owner,
                    "scope": scope,
                    "history": [{**x[0], "checks": json.loads(x[0]["checks"])} for x in history],
                }
            )
    return result


def context(user, question):
    state = snapshot(user)
    sources = [n for n in state["nodes"] if n["kind"] == "Source"]
    from .integrations import code_subgraph

    repositories = []
    for record in [n for n in state["nodes"] if n["kind"] == "Repository" and n["properties"].get("graph")][
        :2
    ]:
        try:
            structure = code_subgraph(user, record["id"])
            nodes = structure["nodes"][:25]
            ids = {n["id"] for n in nodes}
            repositories.append(
                {
                    "repository": structure["repository"],
                    "repository_id": record["id"],
                    "graph": record["properties"]["graph"],
                    "commit": record["properties"].get("commit"),
                    "nodes": [
                        {
                            "id": n["id"],
                            "kind": n["kind"],
                            "title": n["title"],
                            "properties": {
                                k: str(v)[:250]
                                for k, v in n["properties"].items()
                                if k
                                in {
                                    "name",
                                    "file_path",
                                    "path",
                                    "start_line",
                                    "end_line",
                                    "src_start",
                                    "src_end",
                                    "docstring",
                                }
                            },
                        }
                        for n in nodes
                    ],
                    "edges": [e for e in structure["edges"] if e["source"] in ids and e["target"] in ids][
                        :60
                    ],
                    "bounded": True,
                }
            )
        except LookupError:
            repositories.append({"repository": record["title"], "unavailable": True})
    # Native full-text lookup seeds graph expansion; the small company fixture fits in the bounded context.
    terms = re.findall(r"[a-zA-Z0-9_]+", question)[:10]
    matched = []
    if terms:
        for scope in scopes(user):
            try:
                matched.extend(
                    query(
                        user["tenant"],
                        scope,
                        "CALL db.idx.fulltext.queryNodes('Entity',$query) YIELD node,score RETURN node.id,score LIMIT 12",
                        {"query": " | ".join(terms)},
                    )
                )
            except Exception:
                pass
    matched_ids = {row[0] for row in matched}
    explicit_ids = set(re.findall(r"[A-Za-z0-9_-]+", question))
    sources.sort(key=lambda source: (source["id"] not in explicit_ids, source["id"] not in matched_ids))
    return {
        "entities": [{k: v for k, v in n.items() if k != "content"} for n in state["nodes"][:100]],
        "relationships": state["edges"][:180],
        "decisions": decisions(user),
        "sources": [
            {
                **s,
                "content": s.get("content", "")[:2200],
                "excerpt_truncated": len(s.get("content", "")) > 2200,
            }
            for s in sources[:20]
        ],
        "search_matches": matched,
        "repository_structure": repositories,
    }


def task_graph(user):
    # Private state is physically separate from company knowledge and other users' conversations.
    suffix = hashlib.sha256(user["uid"].encode()).hexdigest()[:20]
    return FalkorDB(host=DB_HOST, port=DB_PORT, password=DB_PASSWORD, socket_timeout=30).select_graph(
        f"atlas_tasks_{user['tenant']}_{suffix}"
    )


def list_tasks(user):
    rows = (
        task_graph(user)
        .query(
            "MATCH(t:TaskRun {owner:$uid}) RETURN properties(t) ORDER BY t.updated_at DESC",
            {"uid": user["uid"]},
        )
        .result_set
    )
    states = [json.loads(r[0]["state"]) for r in rows]
    return [s for s in states if set(s.get("required_scopes", ["public"])).issubset(scopes(user))]


def get_task(user, task_id):
    rows = (
        task_graph(user)
        .query("MATCH(t:TaskRun {id:$id,owner:$uid}) RETURN t.state", {"id": task_id, "uid": user["uid"]})
        .result_set
    )
    if not rows:
        raise LookupError("Task not found")
    state = json.loads(rows[0][0])
    if not set(state.get("required_scopes", ["public"])).issubset(scopes(user)):
        raise LookupError("Task not found")
    return state


def save_task(user, state, expected_revision=None):
    with LOCK:
        if expected_revision is None:
            state = {**state, "id": uuid4().hex, "revision": 1, "updated_at": now(), "created_at": now()}
            task_graph(user).query(
                "CREATE(t:TaskRun {id:$id,owner:$uid,state:$state,revision:1,updated_at:$time})",
                {
                    "id": state["id"],
                    "uid": user["uid"],
                    "state": json.dumps(state),
                    "time": state["updated_at"],
                },
            )
        else:
            state = {**state, "revision": expected_revision + 1, "updated_at": now()}
            rows = (
                task_graph(user)
                .query(
                    "MATCH(t:TaskRun {id:$id,owner:$uid,revision:$expected}) SET t.state=$state,t.revision=$revision,t.updated_at=$time RETURN t.id",
                    {
                        "id": state["id"],
                        "uid": user["uid"],
                        "expected": expected_revision,
                        "revision": state["revision"],
                        "state": json.dumps(state),
                        "time": state["updated_at"],
                    },
                )
                .result_set
            )
            if not rows:
                raise ValueError("A newer checkpoint exists. Reload this task before continuing.")
    return state


def revalidate(user, state):
    nodes = snapshot(user)["nodes"]
    visible = {s["id"]: s for s in nodes if s["kind"] == "Source"}
    changes = []
    for evidence in state.get("evidence", []):
        current = visible.get(evidence["source_id"])
        if not current:
            changes.append(
                {
                    "source_id": evidence["source_id"],
                    "reason": "unavailable",
                    "previous_revision": evidence["revision"],
                }
            )
        elif current["revision"] != evidence["revision"]:
            changes.append(
                {
                    "source_id": evidence["source_id"],
                    "reason": "updated",
                    "previous_revision": evidence["revision"],
                    "current_revision": current["revision"],
                }
            )
    repositories = {n["id"]: n for n in nodes if n["kind"] == "Repository"}
    for reference in state.get("repository_evidence", []):
        current = repositories.get(reference["repository_id"])
        if not current:
            changes.append({"source_id": reference["repository_id"], "reason": "unavailable"})
        elif (current["properties"].get("graph"), current["properties"].get("commit")) != (
            reference.get("graph"),
            reference.get("commit"),
        ):
            changes.append(
                {
                    "source_id": reference["repository_id"],
                    "reason": "repository_updated",
                    "previous_commit": reference.get("commit"),
                    "current_commit": current["properties"].get("commit"),
                }
            )
    return changes


def approve_action(user, task_id, action_id, expected_revision):
    with LOCK:
        state = get_task(user, task_id)
        existing = next((a for a in state.get("actions", []) if a["id"] == action_id), None)
        if not existing:
            raise LookupError("Proposal not found")
        if existing.get("outcome"):
            return state, True
        if state["revision"] != expected_revision:
            raise ValueError("A newer checkpoint exists. Reload before approving.")
        if existing.get("status") == "superseded" or (
            existing.get("evidence") and revalidate(user, existing)
        ):
            raise ValueError("Evidence changed. Resume and review the task before approval.")
        if revalidate(user, state):
            raise ValueError("Evidence changed. Resume and review the task before approval.")
        existing["status"] = "completed"
        existing["outcome"] = {
            "id": f"local-{action_id}",
            "type": "atlas_investigation",
            "created_at": now(),
            "title": existing["title"],
        }
        state["completed_steps"].append("Approved local task: " + existing["title"])
        return save_task(user, state, expected_revision), False
