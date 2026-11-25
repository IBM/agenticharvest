#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Keep the cross-system examples honest, against the live systems they use.

``order_fulfillment`` spans a SQLite database and the Directus portal;
``hire_to_onboard`` spans Odoo, Keycloak, and Directus. These tests run each
example's observe -> mine -> analyze stages for real and assert what its
``run.py`` prints. They skip when the stack is not up (see ``examples/systems``),
and each removes every record it created on the live systems.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("httpx", reason="the cross-system examples need the 'web' extra")

from agenticharvest.core.events import OcelLog
from agenticharvest.domains.web.client import WebClient, WebRequestError
from agenticharvest.domains.web.config import WebAppConfig, WebConfigError
from agenticharvest.domains.web.tools import register_client
from agenticharvest.mining import check_trace
from agenticharvest.mining.analysis import check_rules, handoffs, retyped_values

pytestmark = pytest.mark.live

_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = _ROOT / "examples"


def _load(example: str) -> ModuleType:
    """Import ``examples/<example>/workflow.py``, with its siblings importable.

    The cross-system examples are git submodules; a clone without them skips.
    """
    if not (_EXAMPLES / example / "workflow.py").is_file():
        pytest.skip(f"examples/{example} is a submodule; run: git submodule update --init")
    folder = str(_EXAMPLES / example)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    name = f"_example_{example}_workflow"
    spec = importlib.util.spec_from_file_location(name, _EXAMPLES / example / "workflow.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _require_apps(*apps: str) -> None:
    """Register a live client per app, or skip when one is absent."""
    for app in apps:
        try:
            client = WebClient(WebAppConfig.from_env(app, env_file=_ROOT / ".env"))
            client.ir()
            client.request("get", "/")  # the server itself, not just its description
        except (WebConfigError, WebRequestError, OSError) as exc:
            pytest.skip(f"live app '{app}' unavailable: {exc}")
        register_client(client)


# ----------------------------------------------------------------------
# Order fulfillment: database + shipping portal
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def fulfillment() -> Iterator[tuple[ModuleType, OcelLog, str, Path]]:
    _require_apps("directus")
    module = _load("order_fulfillment")
    erp = sys.modules["erp"]
    tag = uuid.uuid4().hex[:8]
    workdir = Path(__import__("tempfile").mkdtemp())
    db = erp.build_erp(workdir / "erp.db")
    log = OcelLog()
    try:
        module.observe(db, log, tag=tag)
        yield module, log, tag, db
    finally:
        module.remove_shipments(tag)


def test_fulfillment_books_and_records_every_order(fulfillment: tuple) -> None:
    module, _log, tag, db = fulfillment
    erp = sys.modules["erp"]
    booked = {row["order_id"]: (row["id"], float(row["net"])) for row in module.shipments(tag)}
    want = {o: n for batch in erp.BATCHES.values() for o, n in module.expected_net(batch).items()}
    assert {o: net for o, (_id, net) in booked.items()} == want
    on_disk = erp.orders_on_disk(db)
    for order_id, (shipment_id, _net) in booked.items():
        assert on_disk[order_id] == ("shipped", str(shipment_id))


def test_fulfillment_mines_one_case_per_order_across_both_systems(fulfillment: tuple) -> None:
    module, log, _tag, _db = fulfillment
    model = module.mine(log)
    assert model.n_cases == 6
    assert [(v.sequence, v.frequency) for v in model.variants] == [([
        "query:orders",
        "send_operation:createItemsShipments",
        "call_operation:readItemsShipments",
        "execute:orders",
    ], 6)]


def test_fulfillment_analysis_finds_the_handoffs_and_carries(fulfillment: tuple) -> None:
    module, log, _tag, _db = fulfillment
    hops = {(h.source.split(":")[0], h.target.split(":")[0]): h.cases
            for h in handoffs(log, module.CASE_TYPE, module.SYSTEMS)}
    assert hops == {("database", "webapp"): 6, ("webapp", "database"): 6}
    carries = {(c.field, c.source_activity, c.target_activity): c.cases
               for c in retyped_values(log, module.CASE_TYPE, module.SYSTEMS)}
    assert carries == {
        ("customer", "query:orders", "send_operation:createItemsShipments"): 6,
        ("order_id", "query:orders", "send_operation:createItemsShipments"): 6,
        ("shipment_ref", "call_operation:readItemsShipments", "execute:orders"): 6,
    }


def test_fulfillment_redesign_drops_the_lookup(fulfillment: tuple) -> None:
    from agenticharvest.domains.sqlite.tools import sqlite_tools
    from agenticharvest.domains.web.tools import web_tools
    from agenticharvest.orchestration.compile import compile_plan
    from agenticharvest.orchestration.redesign import plan_model, redesign

    module, log, _tag, _db = fulfillment
    specs = [s for s, _ in [*sqlite_tools(), *web_tools()]]
    as_is = compile_plan(module.mine(log), specs)
    change = redesign(as_is, remove=["call_operation:readItemsShipments"])
    assert [s.activity for s in change.to_be.steps] == [
        "query:orders", "send_operation:createItemsShipments", "execute:orders",
    ]
    assert [s.requires_approval for s in change.to_be.steps] == [False, True, True]
    assert change.to_be.tools == ["query", "send_operation", "execute"]
    # An as-is trace no longer conforms to the to-be plan.
    [trace, *_] = module.case_traces(log).values()
    assert not check_trace(plan_model(change.to_be), trace).conforms


# ----------------------------------------------------------------------
# Hire to onboard: HR system + identity server + task tracker
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def onboarding() -> Iterator[tuple[ModuleType, OcelLog, dict, str, datetime]]:
    _require_apps("odoo", "keycloak", "directus")
    module = _load("hire_to_onboard")
    tag = uuid.uuid4().hex[:6]
    since = datetime.now(timezone.utc) - timedelta(seconds=5)
    log = OcelLog()
    try:
        hires = module.observe(log, tag=tag)
        yield module, log, hires, tag, since
    finally:
        module.cleanup(tag)


def test_onboarding_creates_records_in_every_system(onboarding: tuple) -> None:
    from agenticharvest.domains.web.tools import client_for

    module, _log, hires, tag, _since = onboarding
    people = [e for e in hires if not e.startswith("_")]
    assert len(people) == 4
    users = client_for("keycloak").request(
        "get", "/admin/realms/agenticharvest/users", query={"email": f"+{tag}@", "max": 50}).body
    assert sorted(u["email"] for u in users) == sorted(people)
    tasks = client_for("directus").request("get", "/items/onboarding_tasks", query={
        "filter": {"employee_ref": {"_contains": f"+{tag}@"}}, "limit": -1}).body["data"]
    assert len(tasks) == 4 * len(module.CHECKLIST)
    assert {t["status"] for t in tasks if t["task"] == "account"} == {f"done {tag}"}


def test_onboarding_mines_one_case_per_person(onboarding: tuple) -> None:
    module, log, _hires, _tag, _since = onboarding
    model = module.mine(log)
    assert model.n_cases == 4
    [main, early] = model.variants
    assert main.frequency == 3 and early.frequency == 1
    assert main.sequence[-2:] == ["send_operation:post_users",
                                  "send_operation:updateSingleItemsOnboardingTasks"]
    assert early.sequence[0] == "send_operation:post_users"


def test_onboarding_policy_flags_the_early_account(onboarding: tuple) -> None:
    module, log, _hires, _tag, _since = onboarding
    report = check_rules(log, module.CASE_TYPE, module.POLICY)
    assert report.passed == {"account only after the hire": 3,
                             "every hire gets an account": 4,
                             "checklist opened within a day of the hire": 4}
    [violation] = report.violations
    assert module.EARLY_ACCOUNT in violation.case


def test_onboarding_history_imports_from_all_three_systems(onboarding: tuple) -> None:
    module, _log, hires, _tag, since = onboarding
    imported = module.import_history(hires, since)
    sources = {e.attributes["source"] for e in imported.events}
    assert sources == {"odoo chatter", "keycloak admin events", "directus activity"}
    traces = module.case_traces(imported)
    assert len(traces) == 4
    for trace in traces.values():
        assert "hris:stage Contract Signed" in trace
        assert "identity:create:user" in trace
        assert "tracker:create:onboarding_tasks" in trace
    # The systems agree with the observed log on the policy violation: the
    # early account appears before the contract in the imported history too.
    early = next(t for c, t in traces.items() if module.EARLY_ACCOUNT in c)
    assert early.index("identity:create:user") < early.index("hris:stage Contract Signed")
