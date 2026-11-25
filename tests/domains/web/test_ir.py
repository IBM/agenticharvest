#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The web IR, built from a real published API description and from inline specs.

The inline documents are minimal OpenAPI 3 fragments that pin down one rule
each (resource naming, $ref resolution, body shapes); the Keycloak test runs
the same builder over the vendor's full, published Admin REST API description.
"""

from __future__ import annotations

import pytest

from agenticharvest.domains.web.ir import from_odoo, from_openapi


def _spec(paths: dict, components: dict | None = None) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": paths,
        "components": components or {},
    }


def test_resource_is_the_literal_before_the_first_parameter() -> None:
    ir = from_openapi("t", _spec({
        "/v2/pets": {"get": {}},
        "/v2/pets/{petId}": {"get": {}},
        "/v2/pets/{petId}/photos": {"post": {}},
        "/items/orders/{id}": {"patch": {}},
    }))
    assert [(op.path, op.resource) for op in ir.operations] == [
        ("/v2/pets", "pets"),
        ("/v2/pets/{petId}", "pets"),
        ("/v2/pets/{petId}/photos", "pets"),
        ("/items/orders/{id}", "orders"),
    ]


def test_prefix_filters_paths_and_is_stripped_before_naming() -> None:
    ir = from_openapi("t", _spec({
        "/admin/realms/{realm}/users": {"get": {}, "post": {}},
        "/admin/realms/{realm}/users/{user-id}/groups": {"get": {}},
        "/realms/{realm}/account": {"get": {}},
    }), prefix="/admin/realms/{realm}/")
    assert [op.name for op in ir.operations] == [
        "get_users", "post_users", "get_users_by_user_id_groups",
    ]
    assert {op.resource for op in ir.operations} == {"users"}


def test_operation_id_wins_and_duplicates_are_suffixed() -> None:
    ir = from_openapi("t", _spec({
        "/a": {"get": {"operationId": "list"}},
        "/b": {"get": {"operationId": "list"}},
    }))
    assert [op.name for op in ir.operations] == ["list", "list_2"]


def test_writes_are_the_state_changing_methods() -> None:
    ir = from_openapi("t", _spec({"/x": {m: {} for m in ("get", "post", "put", "patch", "delete")}}))
    assert {op.method: op.writes for op in ir.operations} == {
        "get": False, "post": True, "put": True, "patch": True, "delete": True,
    }


def test_body_fields_follow_refs_and_one_of_arrays() -> None:
    components = {"schemas": {"Order": {
        "type": "object",
        "required": ["order_id"],
        "properties": {"order_id": {"type": "string"}, "net": {"type": "number"}},
    }}}
    ir = from_openapi("t", _spec({"/orders": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"oneOf": [
            {"type": "array", "items": {"$ref": "#/components/schemas/Order"}},
            {"$ref": "#/components/schemas/Order"},
        ]}}}}}}}, components))
    op = ir.operations[0]
    assert op.body_kind == "object-or-array"
    assert [(f.name, f.type, f.required, f.identifier) for f in op.body_fields] == [
        ("order_id", "string", True, True), ("net", "number", False, False),
    ]
    assert not op.closed_body


def test_additional_properties_false_closes_the_body() -> None:
    ir = from_openapi("t", _spec({"/x": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"a": {"type": "string"}},
        }}}}}}}))
    assert ir.operations[0].closed_body


def test_ref_cycles_do_not_recurse_forever() -> None:
    components = {"schemas": {"Node": {
        "type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}},
    }}}
    ir = from_openapi("t", _spec({"/n": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}}}}, components))
    assert [f.name for f in ir.operations[0].body_fields] == ["next"]


def test_rejects_a_non_openapi3_document() -> None:
    with pytest.raises(ValueError, match="OpenAPI 3"):
        from_openapi("t", {"swagger": "2.0", "paths": {}})


def test_producers_and_consumers() -> None:
    ir = from_openapi("t", _spec({
        "/orders": {"post": {"requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "properties": {"customer_id": {"type": "integer"}}}}}}}},
        "/orders/{id}": {"get": {}},
    }))
    # orders carries customer_id, so reading an order produces it; creating takes it.
    assert ir.producers("customer_id") == ["get_orders_by_id"]
    assert ir.consumers("customer_id") == ["post_orders"]
    assert ir.consumers("id") == ["get_orders_by_id"]


def test_keycloak_published_spec(keycloak_spec: dict) -> None:
    ir = from_openapi("keycloak", keycloak_spec, prefix="/admin/realms/{realm}/")
    users = ir.resource("users")
    assert users is not None
    assert {"get_users", "post_users", "delete_users_by_user_id"} <= set(users.operations)
    create = ir.operation("post_users")
    assert create is not None and create.writes and create.path_params == ["realm"]
    assert {"username", "email", "enabled"} <= {f.name for f in create.body_fields}
    assert not ir.operation("get_users").writes  # type: ignore[union-attr]


def _odoo_doc() -> dict:
    return {
        "model": "hr.job",
        "name": "Job Position",
        "fields": {
            "id": {"type": "integer", "store": True},
            "name": {"type": "char", "required": True, "store": True, "string": "Job"},
            "department_id": {"type": "many2one", "relation": "hr.department", "store": True},
            "computed": {"type": "char", "store": False},
        },
        "methods": {
            "search_read": {"parameters": {"domain": {}, "fields": {}}},
            "create": {"parameters": {"vals_list": {}}},
            "write": {"parameters": {"vals": {}}},
            "action_archive": {"parameters": {}},
            "action_open_employees": {"parameters": {}},
            "action_with_args": {"parameters": {"x": {}}},
            "make_offer": {"parameters": {}},
        },
    }


def test_odoo_description_maps_orm_methods_to_effects() -> None:
    ir = from_odoo("hris", [_odoo_doc()], methods=["hr.job.make_offer"])
    ops = {op.name: op for op in ir.operations}
    assert ops["hr.job.search_read"].method == "get"
    assert ops["hr.job.create"].method == "post"
    assert ops["hr.job.write"].method == "patch"
    assert all(op.path == f"/json/2/hr.job/{op.name.split('.')[-1]}" for op in ops.values())
    # Argument-free business actions are exposed and gated; UI-only and
    # argument-taking ones are not; named extra methods are.
    assert "hr.job.action_archive" in ops and ops["hr.job.action_archive"].writes
    assert "hr.job.action_open_employees" not in ops
    assert "hr.job.action_with_args" not in ops
    assert "hr.job.make_offer" in ops


def test_odoo_fields_relations_and_closed_bodies() -> None:
    ir = from_odoo("hris", [_odoo_doc()])
    job = ir.resource("hr.job")
    assert job is not None
    assert [f.name for f in job.fields] == ["id", "name", "department_id"]
    assert next(f for f in job.fields if f.name == "department_id").relation == "hr.department"
    create = ir.operation("hr.job.create")
    assert create is not None and create.closed_body
    assert [f.name for f in create.body_fields] == ["vals_list", "context"]
    assert ir.consumers("department_id") == ["hr.job.create", "hr.job.write"]
