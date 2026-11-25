#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The web tools against live apps: Directus, Keycloak, and Odoo.

Every test here sends real requests to a system configured in ``.env`` (bring
the stack up with ``examples/systems``) and skips when that system is absent.
Each test creates what it needs and removes it before it ends, so the systems
are left as they were.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from agenticharvest.core.events import OcelLog, OcelTap
from agenticharvest.core.tools import ToolRegistry
from agenticharvest.domains.web import WebOCEL
from agenticharvest.domains.web.client import WebClient, WebRequestError
from agenticharvest.domains.web.ocel import OBJ_OPERATION, OBJ_RESOURCE, OBJ_WEBAPP
from agenticharvest.domains.web.tools import register_client, web_tools
from agenticharvest.gateway.dispatch import ToolRequest, dispatch

pytestmark = pytest.mark.live


@pytest.fixture
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec, handler in web_tools():
        registry.register(spec, handler)
    return registry


def _call(registry: ToolRegistry, tool: str, **params: object):
    return dispatch(registry, ToolRequest(tool=tool, params=params))


@pytest.fixture
def tag() -> str:
    return uuid.uuid4().hex[:8]


# ----------------------------------------------------------------------
# Directus: an OpenAPI description served live, growing with the schema
# ----------------------------------------------------------------------


@pytest.fixture
def portal(directus: WebClient, registry: ToolRegistry, tag: str) -> Iterator[str]:
    register_client(directus)
    yield directus.config.name
    rows = directus.request("get", "/items/shipments",
                            query={"filter": {"status": {"_eq": tag}}, "fields": "id"})
    ids = [r["id"] for r in (rows.body or {}).get("data", [])]
    if ids:
        directus.request("delete", "/items/shipments", body=ids)


def test_directus_lists_its_collections_as_resources(portal: str, registry: ToolRegistry) -> None:
    result = _call(registry, "list_resources", app=portal, resource="shipments")
    assert result.ok, result.error
    [shipments] = result.output["resources"]
    effects = {op["name"]: op["effect"] for op in shipments["operations"]}
    assert effects["readItemsShipments"] == "read"
    assert effects["createItemsShipments"] == "write"


def test_directus_write_is_gated_then_applied(
    portal: str, registry: ToolRegistry, directus: WebClient, tag: str
) -> None:
    body = {"order_id": f"T-{tag}", "customer": "Test", "net": 12.5, "status": tag}
    blocked = _call(registry, "send_operation", app=portal, name="createItemsShipments", body=body)
    assert not blocked.ok and "confirm" in (blocked.error or "")
    assert not directus.request("get", "/items/shipments",
                                query={"filter": {"status": {"_eq": tag}}}).body["data"]

    created = _call(registry, "send_operation", app=portal, name="createItemsShipments",
                    body=body, confirm=True)
    assert created.ok, created.error
    row_id = created.output["body"]["data"]["id"]

    found = _call(registry, "call_operation", app=portal, name="readSingleItemsShipments",
                  path_params={"id": row_id})
    assert found.ok and found.output["body"]["data"]["order_id"] == f"T-{tag}"


def test_describe_operation_reports_method_path_and_fields(
    portal: str, registry: ToolRegistry
) -> None:
    result = _call(registry, "describe_operation", app=portal, name="updateSingleItemsShipments")
    assert result.ok, result.error
    op = result.output
    assert (op["method"], op["path"], op["effect"]) == ("patch", "/items/shipments/{id}", "write")
    assert op["path_params"] == ["id"]
    assert {"order_id", "customer", "net", "status"} <= {f["name"] for f in op["resource_fields"]}


def test_get_field_flow_lists_producers_and_consumers(portal: str, registry: ToolRegistry) -> None:
    result = _call(registry, "get_field_flow", app=portal, field="order_id")
    assert result.ok, result.error
    # Reading shipments produces order_id; creating and updating them take it.
    assert "readItemsShipments" in result.output["produced_by"]
    assert {"createItemsShipments", "updateSingleItemsShipments"} <= set(
        result.output["consumed_by"]
    )
    assert not set(result.output["produced_by"]) & {"createItemsShipments"}


def test_list_resources_refresh_picks_up_a_new_collection(
    directus: WebClient, registry: ToolRegistry, tag: str
) -> None:
    # The live description grows with the schema: a collection created after
    # the IR was loaded appears once the tool is asked to refresh.
    register_client(directus)
    name = f"probe_{tag}"
    directus.ir()
    created = directus.request("post", "/collections", body={
        "collection": name, "schema": {},
        "fields": [{"field": "id", "type": "integer", "meta": {"hidden": True},
                    "schema": {"is_primary_key": True, "has_auto_increment": True}}],
    })
    assert created.ok, created.body
    try:
        stale = _call(registry, "list_resources", app=directus.config.name, resource=name)
        assert stale.ok and stale.output["resources"] == []
        fresh = _call(registry, "list_resources", app=directus.config.name, resource=name,
                      refresh=True)
        assert [r["name"] for r in fresh.output["resources"]] == [name]
    finally:
        directus.request("delete", f"/collections/{name}")
        directus.ir(refresh=True)


def test_write_tool_refuses_a_read_operation(portal: str, registry: ToolRegistry) -> None:
    result = _call(registry, "send_operation", app=portal, name="readItemsShipments",
                   confirm=True)
    assert not result.ok and "call_operation" in (result.error or "")


def test_path_params_and_query_must_be_objects(portal: str, registry: ToolRegistry) -> None:
    bad_path = _call(registry, "call_operation", app=portal, name="readSingleItemsShipments",
                     path_params=["1"])
    assert not bad_path.ok and "path_params" in (bad_path.error or "")
    bad_query = _call(registry, "call_operation", app=portal, name="readItemsShipments",
                      query="limit=1")
    assert not bad_query.ok and "query" in (bad_query.error or "")


def test_header_and_no_auth_against_the_live_portal(directus: WebClient) -> None:
    from pydantic import SecretStr

    from agenticharvest.domains.web.config import AuthKind

    token = directus.config.token
    assert token is not None
    header = directus.config.model_copy(update={
        "auth": AuthKind.HEADER, "auth_header": "Authorization",
        "token": SecretStr(f"Bearer {token.get_secret_value()}"),
    })
    with_header = WebClient(header)
    try:
        assert with_header.request("get", "/items/shipments", query={"limit": 1}).status == 200
    finally:
        with_header.close()
    anonymous = WebClient(directus.config.model_copy(update={"auth": AuthKind.NONE}))
    try:
        # No credentials: the protected collection is refused, a public route is not.
        assert anonymous.request("get", "/items/shipments").status == 403
        assert anonymous.request("get", "/server/ping").status == 200
    finally:
        anonymous.close()


def test_results_never_carry_credentials(portal: str, registry: ToolRegistry,
                                         directus: WebClient) -> None:
    token = directus.config.token
    assert token is not None
    result = _call(registry, "call_operation", app=portal, name="readItemsShipments",
                   query={"limit": 1})
    assert result.ok
    assert token.get_secret_value() not in str(result.output)
    assert "set-cookie" not in result.output["headers"]


def test_read_tool_refuses_a_write_operation(portal: str, registry: ToolRegistry) -> None:
    result = _call(registry, "call_operation", app=portal, name="createItemsShipments")
    assert not result.ok and "send_operation" in (result.error or "")


def test_unknown_operation_names_the_way_out(portal: str, registry: ToolRegistry) -> None:
    result = _call(registry, "call_operation", app=portal, name="nope")
    assert not result.ok and "list_resources" in (result.error or "")


def test_missing_path_parameter_is_an_error(portal: str, registry: ToolRegistry) -> None:
    result = _call(registry, "call_operation", app=portal, name="readSingleItemsShipments")
    assert not result.ok and "id" in (result.error or "")


def test_path_parameters_cannot_add_segments(portal: str, registry: ToolRegistry) -> None:
    result = _call(registry, "call_operation", app=portal, name="readSingleItemsShipments",
                   path_params={"id": "../../server/info"})
    assert not result.ok  # quoted into one segment, so it is just a missing item


def test_requests_never_leave_the_apps_origin(directus: WebClient) -> None:
    with pytest.raises(WebRequestError, match="origin"):
        directus.request("get", "https://example.com/steal")


def test_responses_are_bounded_unless_asked_otherwise(directus: WebClient) -> None:
    spec = directus.request("get", "/server/specs/oas", max_chars=200)
    assert spec.truncated and isinstance(spec.body, str) and len(spec.body) == 200
    whole = directus.request("get", "/server/specs/oas", max_chars=None)
    assert not whole.truncated and whole.body["openapi"].startswith("3")


def test_web_ocel_records_app_resource_operation_and_records(
    portal: str, directus: WebClient, tag: str
) -> None:
    log = OcelLog()
    registry = ToolRegistry(taps=[OcelTap(mapper=WebOCEL(qualify_operations=True), log=log)])
    for spec, handler in web_tools():
        registry.register(spec, handler)
    created = _call(registry, "send_operation", app=portal, name="createItemsShipments",
                    body={"order_id": f"T-{tag}", "status": tag}, confirm=True)
    assert created.ok, created.error

    [event] = log.events
    assert event.activity == "send_operation:createItemsShipments"
    types = {o.type for o in log.objects}
    assert {OBJ_WEBAPP, OBJ_OPERATION, OBJ_RESOURCE} <= types
    roles = {r.qualifier for r in event.relationships}
    assert {"target", "invokes", "writes"} <= roles
    assert event.attributes["records"] == [str(created.output["body"]["data"]["id"])]
    assert event.attributes["status"] == 200


# ----------------------------------------------------------------------
# Keycloak: the vendor's published description, OAuth password login
# ----------------------------------------------------------------------


def test_keycloak_account_lifecycle(keycloak: WebClient, registry: ToolRegistry, tag: str) -> None:
    register_client(keycloak)
    app, realm = keycloak.config.name, {"realm": "agenticharvest"}
    username = f"test-{tag}"
    created = _call(registry, "send_operation", app=app, name="post_users", path_params=realm,
                    body={"username": username, "enabled": True}, confirm=True)
    assert created.ok, created.error
    assert created.output["status"] == 201
    assert "/users/" in created.output["headers"]["location"]
    try:
        found = _call(registry, "call_operation", app=app, name="get_users", path_params=realm,
                      query={"username": username, "exact": True})
        assert found.ok and [u["username"] for u in found.output["body"]] == [username]
    finally:
        user_id = created.output["headers"]["location"].rsplit("/", 1)[-1]
        deleted = _call(registry, "send_operation", app=app, name="delete_users_by_user_id",
                        path_params={**realm, "user-id": user_id}, confirm=True)
        assert deleted.ok, deleted.error


def test_keycloak_wrong_password_is_reported(keycloak: WebClient) -> None:
    from pydantic import SecretStr

    wrong = WebClient(keycloak.config.model_copy(update={"password": SecretStr("wrong")}))
    try:
        with pytest.raises(WebRequestError, match="refused the login"):
            wrong.request("get", "/admin/realms/agenticharvest/users")
    finally:
        wrong.close()


def test_keycloak_token_is_reused_until_it_expires(keycloak: WebClient) -> None:
    client = WebClient(keycloak.config)
    try:
        client.request("get", "/admin/realms/agenticharvest")
        first = client._token
        client.request("get", "/admin/realms/agenticharvest")
        assert first is not None and client._token == first
        client._token_expiry = 0.0  # as if it had expired
        client.request("get", "/admin/realms/agenticharvest")
        assert client._token != first
    finally:
        client.close()


def test_keycloak_http_errors_are_tool_errors(keycloak: WebClient, registry: ToolRegistry) -> None:
    register_client(keycloak)
    result = _call(registry, "call_operation", app=keycloak.config.name, name="get_users",
                   path_params={"realm": "no-such-realm"})
    assert not result.ok and "404" in (result.error or "")


# ----------------------------------------------------------------------
# Odoo: no OpenAPI, its own /doc self-description, JSON-2 with an API key
# ----------------------------------------------------------------------


def test_odoo_description_is_read_from_the_live_server(odoo: WebClient) -> None:
    ir = odoo.ir()
    assert ir.source == "odoo" and ir.version
    employee = ir.resource("hr.employee")
    assert employee is not None and any(f.name == "work_email" for f in employee.fields)
    assert ir.operation("hr.employee.search_read") is not None


def test_odoo_record_lifecycle(odoo: WebClient, registry: ToolRegistry, tag: str) -> None:
    register_client(odoo)
    app = odoo.config.name
    created = _call(registry, "send_operation", app=app, name="hr.job.create",
                    body={"vals_list": [{"name": f"Test {tag}"}]}, confirm=True)
    assert created.ok, created.error
    [job_id] = created.output["body"]
    try:
        read = _call(registry, "call_operation", app=app, name="hr.job.read",
                     body={"ids": [job_id], "fields": ["name"]})
        assert read.ok and read.output["body"][0]["name"] == f"Test {tag}"
        written = _call(registry, "send_operation", app=app, name="hr.job.write",
                        body={"ids": [job_id], "vals": {"name": f"Renamed {tag}"}}, confirm=True)
        assert written.ok and written.output["body"] is True
    finally:
        removed = _call(registry, "send_operation", app=app, name="hr.job.unlink",
                        body={"ids": [job_id]}, confirm=True)
        assert removed.ok, removed.error


def test_odoo_unknown_model_is_reported(odoo: WebClient) -> None:
    missing = WebClient(odoo.config.model_copy(update={"models": ["no.such.model"]}))
    try:
        with pytest.raises(WebRequestError, match="no description for model 'no.such.model'"):
            missing.ir()
    finally:
        missing.close()


def test_odoo_business_method_creates_the_employee(
    odoo: WebClient, registry: ToolRegistry, tag: str
) -> None:
    # A named business method (not plain CRUD) runs through send_operation and
    # has the effect Odoo documents: the applicant gets an employee.
    register_client(odoo)
    app = odoo.config.name

    def rpc(model: str, method: str, **body: object) -> object:
        return odoo.request("post", f"/json/2/{model}/{method}", body=body).body

    [job] = rpc("hr.job", "create", vals_list=[{"name": f"Test {tag}"}])  # type: ignore[misc]
    [applicant] = rpc("hr.applicant", "create",  # type: ignore[misc]
                      vals_list=[{"partner_name": f"Test {tag}", "job_id": job}])
    employee = None
    try:
        hired = _call(registry, "send_operation", app=app,
                      name="hr.applicant.create_employee_from_applicant",
                      body={"ids": [applicant]}, confirm=True)
        assert hired.ok, hired.error
        [record] = rpc("hr.applicant", "read", ids=[applicant],  # type: ignore[misc]
                       fields=["employee_id"])
        employee = record["employee_id"][0]
        assert employee
    finally:
        if employee:
            rpc("hr.employee", "unlink", ids=[employee])
        rpc("hr.applicant", "unlink", ids=[applicant])
        rpc("hr.job", "unlink", ids=[job])


def test_odoo_closed_body_catches_a_silent_no_op(odoo: WebClient, registry: ToolRegistry) -> None:
    # Odoo answers a write without ids with HTTP 200 and changes nothing; the
    # body check turns that into an error naming the expected fields.
    register_client(odoo)
    result = _call(registry, "send_operation", app=odoo.config.name, name="hr.job.write",
                   body={"id": 1, "vals": {"name": "x"}}, confirm=True)
    assert not result.ok
    assert "missing ids" in (result.error or "") and "unknown id" in (result.error or "")
