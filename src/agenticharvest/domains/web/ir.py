#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The web app IR: what an API offers, read from the API's own description.

A web app maps onto the shapes the other domains use:

    resources    the containers of state     (a workbook's sheets, a database's tables)
    operations   what acts on them           (a workbook's macros, a database's triggers)
    fields       what an operation reads and writes, and which of them identify a record

Two sources of truth are supported, both served by the live app itself:

- **OpenAPI 3** (``from_openapi``). Each path + method is an operation; its
  operationId (or a name derived from the method and path) is its name, the
  first literal path segment after any version prefix is its resource, and the
  request-body schema gives the fields it writes.
- **Odoo's self-description** (``from_odoo``). Odoo 17+ serves ``/doc/<model>.json``
  listing a model's fields and public methods. Each model is a resource and the
  standard ORM methods (``search_read``, ``read``, ``create``, ``write``,
  ``unlink``) plus its public ``action_*`` methods are its operations, called at
  ``POST /json/2/<model>/<method>``.

Either way the result is the same :class:`WebIR`, so the tools, the OCEL mapper,
and the backend never care which format the app spoke.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

#: HTTP methods that change state. Every other method is a read.
WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})

_METHODS = ("get", "post", "put", "patch", "delete")
_VERSION_SEGMENT = re.compile(r"^v\d+(\.\d+)*$", re.IGNORECASE)
_TEMPLATE = re.compile(r"\{([^}]+)\}")


class ApiField(BaseModel):
    """One field an operation reads or writes.

    Attributes:
        name: The field name as the API spells it.
        type: The JSON type, or the API's own type name when it has one.
        required: Whether the operation requires it.
        relation: For a reference to another resource, that resource's name.
        identifier: ``True`` when the field identifies a record: a path
            parameter, an ``id``, or a reference to another record.
        description: The API's own description, when it gives one.
    """

    name: str
    type: str = ""
    required: bool = False
    relation: str = ""
    identifier: bool = False
    description: str = ""


class Operation(BaseModel):
    """One callable operation.

    Attributes:
        name: Unique within the app: the operationId, or ``<model>.<method>``.
        method: The HTTP method, lower case.
        path: The path template, e.g. ``/items/orders/{id}``.
        resource: The resource the operation acts on.
        summary: One line describing it.
        path_params: The ``{placeholders}`` in ``path``, in order.
        query_params: Query parameters the operation accepts.
        body_fields: Top-level fields of the JSON request body, when it has one.
        body_kind: ``object``, ``array``, or ``object-or-array``; empty when the
            operation takes no body.
        closed_body: ``True`` when the body accepts only ``body_fields``, so an
            unknown key is a mistake rather than an extension.
        data_fields: The resource fields the operation writes when they are
            nested inside the body rather than at its top level (an RPC
            ``create`` takes them inside ``vals_list``).
        tags: The API's own grouping, when it has one.
    """

    name: str
    method: str
    path: str
    resource: str
    summary: str = ""
    path_params: list[str] = Field(default_factory=list)
    query_params: list[ApiField] = Field(default_factory=list)
    body_fields: list[ApiField] = Field(default_factory=list)
    body_kind: str = ""
    closed_body: bool = False
    data_fields: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @property
    def writes(self) -> bool:
        """Whether calling it changes state."""
        return self.method in WRITE_METHODS


class Resource(BaseModel):
    """A container of state and the fields its records carry.

    Attributes:
        name: The resource name, e.g. ``orders`` or ``hr.employee``.
        title: A human label, when the API gives one.
        fields: The fields a record carries, when the API describes them.
        operations: The names of the operations acting on it.
    """

    name: str
    title: str = ""
    fields: list[ApiField] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)


class WebIR(BaseModel):
    """Everything one web app exposes.

    Attributes:
        app: The configured app name.
        title: The API's own title.
        version: The API's own version string.
        source: Which description it was read from (``openapi`` or ``odoo``).
        resources: The resources, in first-seen order.
        operations: The operations, in the order the description lists them.
    """

    app: str
    title: str = ""
    version: str = ""
    source: str = ""
    resources: list[Resource] = Field(default_factory=list)
    operations: list[Operation] = Field(default_factory=list)

    def operation(self, name: str) -> Operation | None:
        """Return the operation called ``name``, or ``None``."""
        return next((op for op in self.operations if op.name == name), None)

    def resource(self, name: str) -> Resource | None:
        """Return the resource called ``name``, or ``None``."""
        return next((r for r in self.resources if r.name == name), None)

    def producers(self, field: str) -> list[str]:
        """Operations whose resource carries ``field``: where a value can come from."""
        holders = {r.name for r in self.resources if any(f.name == field for f in r.fields)}
        return [op.name for op in self.operations if op.resource in holders and not op.writes]

    def consumers(self, field: str) -> list[str]:
        """Operations that take ``field`` as input: where a value goes."""
        return [
            op.name
            for op in self.operations
            if field in op.path_params
            or field in op.data_fields
            or any(f.name == field for f in op.body_fields + op.query_params)
        ]


# ======================================================================
# OpenAPI 3
# ======================================================================


def from_openapi(app: str, document: dict[str, Any], *, prefix: str = "") -> WebIR:
    """Build the IR from an OpenAPI 3 document.

    Args:
        app: The configured app name.
        document: The parsed OpenAPI document.
        prefix: When set, only paths starting with it are included.

    Raises:
        ValueError: If ``document`` is not OpenAPI 3.
    """
    version = str(document.get("openapi", ""))
    if not version.startswith("3"):
        raise ValueError(f"Expected an OpenAPI 3 document, got openapi={version or 'none'}.")
    resolver = _Resolver(document)
    info = document.get("info") or {}
    ir = WebIR(
        app=app,
        title=str(info.get("title", "")),
        version=str(info.get("version", "")),
        source="openapi",
    )
    resources: dict[str, Resource] = {}
    taken: set[str] = set()

    for path, item in (document.get("paths") or {}).items():
        if prefix and not path.startswith(prefix):
            continue
        item = resolver.deref(item)
        shared = [resolver.deref(p) for p in item.get("parameters", [])]
        for method in _METHODS:
            raw = item.get(method)
            if not isinstance(raw, dict):
                continue
            params = shared + [resolver.deref(p) for p in raw.get("parameters", [])]
            body_kind, body_fields, closed = _request_body(resolver, raw.get("requestBody"))
            name = _unique(raw.get("operationId") or _derive_name(method, path, prefix), taken)
            resource = _resource_of(path, prefix)
            operation = Operation(
                name=name,
                method=method,
                path=path,
                resource=resource,
                summary=_one_line(raw.get("summary") or raw.get("description") or ""),
                path_params=_TEMPLATE.findall(path),
                query_params=[
                    _param_field(resolver, p) for p in params if p.get("in") == "query"
                ],
                body_fields=body_fields,
                body_kind=body_kind,
                closed_body=closed,
                tags=list(raw.get("tags") or []),
            )
            ir.operations.append(operation)
            entry = resources.setdefault(resource, Resource(name=resource))
            entry.operations.append(name)
            for field in body_fields:
                if all(f.name != field.name for f in entry.fields):
                    entry.fields.append(field)
    ir.resources = list(resources.values())
    return ir


class _Resolver:
    """Follows local ``$ref`` pointers, guarding against cycles."""

    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document

    def deref(self, node: Any, _seen: frozenset[str] = frozenset()) -> Any:
        if isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if ref in _seen or not ref.startswith("#/"):
                return {}
            target: Any = self._document
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                target = target.get(part, {}) if isinstance(target, dict) else {}
            return self.deref(target, _seen | {ref})
        return node


def _request_body(resolver: _Resolver, body: Any) -> tuple[str, list[ApiField], bool]:
    """Return the body's shape, its top-level fields, and whether it is closed.

    A body is closed when its schema sets ``additionalProperties: false``; JSON
    Schema's default is open, so most bodies are.
    """
    body = resolver.deref(body)
    if not isinstance(body, dict):
        return "", [], False
    content = body.get("content") or {}
    media = content.get("application/json") or next(iter(content.values()), None)
    if not isinstance(media, dict):
        return "", [], False
    schema = resolver.deref(media.get("schema") or {})
    kind = "object"
    variants = schema.get("oneOf") or schema.get("anyOf")
    if variants:
        resolved = [resolver.deref(v) for v in variants]
        objects = [v for v in resolved if v.get("type") != "array"]
        arrays = [v for v in resolved if v.get("type") == "array"]
        kind = "object-or-array" if objects and arrays else ("array" if arrays else "object")
        schema = objects[0] if objects else resolver.deref(arrays[0].get("items") or {})
    elif schema.get("type") == "array":
        kind = "array"
        schema = resolver.deref(schema.get("items") or {})
    closed = schema.get("additionalProperties") is False
    return kind, _schema_fields(resolver, schema), closed


def _schema_fields(resolver: _Resolver, schema: dict[str, Any]) -> list[ApiField]:
    """The top-level properties of an object schema, merging ``allOf`` parts."""
    parts = [schema] + [resolver.deref(p) for p in schema.get("allOf", [])]
    required: set[str] = set()
    fields: dict[str, ApiField] = {}
    for part in parts:
        required.update(part.get("required") or [])
        for name, prop in (part.get("properties") or {}).items():
            prop = resolver.deref(prop)
            fields[name] = ApiField(
                name=name,
                type=str(prop.get("type") or ("object" if "properties" in prop else "")),
                identifier=name == "id" or name.endswith(("_id", "Id")),
                description=_one_line(prop.get("description") or ""),
            )
    for name in required:
        if name in fields:
            fields[name].required = True
    return list(fields.values())


def _param_field(resolver: _Resolver, param: dict[str, Any]) -> ApiField:
    schema = resolver.deref(param.get("schema") or {})
    return ApiField(
        name=str(param.get("name", "")),
        type=str(schema.get("type", "")),
        required=bool(param.get("required")),
        description=_one_line(param.get("description") or ""),
    )


def _segments(path: str, prefix: str) -> list[str]:
    """The path's segments after ``prefix`` and any version segment."""
    rest = path[len(prefix):] if prefix and path.startswith(prefix) else path
    return [s for s in rest.split("/") if s and not _VERSION_SEGMENT.match(s)]


def _resource_of(path: str, prefix: str) -> str:
    """The resource a path acts on: the literal just before its first parameter.

    A collection is addressed as ``.../<resource>`` and one of its records as
    ``.../<resource>/{id}``; anything deeper is a sub-resource of that record and
    belongs to the same resource. With no parameter, the last literal is it::

        /items/orders/{id}                      (prefix /items/)   -> orders
        /v2/pets/{petId}/photos                                    -> pets
        /admin/realms/{realm}/users/{id}/groups (prefix .../{realm}/) -> users
    """
    resource = ""
    for segment in _segments(path, prefix):
        if _TEMPLATE.fullmatch(segment):
            if resource:
                return resource
            continue
        resource = segment
    return resource or "root"


def _derive_name(method: str, path: str, prefix: str = "") -> str:
    """A readable name for an operation without an operationId."""
    words = []
    for segment in _segments(path, prefix):
        if not segment:
            continue
        match = _TEMPLATE.fullmatch(segment)
        words.append(f"by_{_slug(match.group(1))}" if match else _slug(segment))
    return "_".join([method, *words]) or method


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()


def _unique(name: str, taken: set[str]) -> str:
    """``name``, suffixed until it is unused; records the result in ``taken``."""
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}_{n}", n + 1
    taken.add(candidate)
    return candidate


def _one_line(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(text))
    return " ".join(text.split())[:240]


# ======================================================================
# Odoo self-description (/doc/<model>.json)
# ======================================================================

#: ORM methods exposed for every model, with their HTTP-level effect.
_ODOO_ORM: dict[str, tuple[str, list[str]]] = {
    "search_read": ("get", ["domain", "fields", "offset", "limit", "order"]),
    "read": ("get", ["ids", "fields"]),
    "create": ("post", ["vals_list"]),
    "write": ("patch", ["ids", "vals"]),
    "unlink": ("delete", ["ids"]),
}

#: Field types that reference another record.
_ODOO_RELATIONAL = frozenset({"many2one", "one2many", "many2many"})

#: Action prefixes that only return a client-side window or reminder, so they do
#: nothing useful over the API.
_ODOO_UI_ACTIONS = ("action_open_", "action_reschedule_", "action_view_")


def from_odoo(
    app: str,
    models: list[dict[str, Any]],
    *,
    version: str = "",
    methods: list[str] | None = None,
) -> WebIR:
    """Build the IR from Odoo ``/doc/<model>.json`` documents.

    Every operation is ``POST /json/2/<model>/<method>``. The ORM methods map to
    the HTTP verb that matches their effect (``search_read`` is a read, ``write``
    a patch), which is what the web tools gate on; the backend always sends POST,
    as Odoo requires.

    Args:
        app: The configured app name.
        models: One parsed ``/doc/<model>.json`` document per model.
        version: The server version, when known.
        methods: Further argument-free business methods to expose, as
            ``model.method`` (``sale.order.action_confirm`` is automatic;
            ``account.move.post`` would be named here).
            Odoo does not mark which public methods are business verbs, so
            beyond ``action_*`` they are named here. All are gated as writes.
    """
    extra = set(methods or [])
    ir = WebIR(app=app, title="Odoo", version=version, source="odoo")
    for doc in models:
        model = str(doc.get("model", ""))
        if not model:
            continue
        fields = [
            ApiField(
                name=name,
                type=str(spec.get("type", "")),
                required=bool(spec.get("required")),
                relation=str(spec.get("relation") or ""),
                identifier=name == "id" or spec.get("type") in _ODOO_RELATIONAL,
                description=_one_line(spec.get("string") or ""),
            )
            for name, spec in (doc.get("fields") or {}).items()
            if spec.get("store") or name == "id"
        ]
        resource = Resource(name=model, title=str(doc.get("name", "")), fields=fields)
        doc_methods = doc.get("methods") or {}
        for method, (verb, params) in _ODOO_ORM.items():
            if method not in doc_methods:
                continue
            _add_odoo_operation(ir, resource, method, verb, params, doc_methods[method])
        for method, spec in doc_methods.items():
            named = f"{model}.{method}" in extra
            if named or (
                method.startswith("action_")
                and not method.startswith(_ODOO_UI_ACTIONS)
                and not (spec.get("parameters") or {})
            ):
                # Public, argument-free actions are the app's business verbs
                # (action_archive, action_create_user, ...). Their effect is not
                # declared, so they are treated as writes and gated.
                _add_odoo_operation(ir, resource, method, "post", ["ids"], spec)
        ir.resources.append(resource)
    return ir


def _add_odoo_operation(
    ir: WebIR,
    resource: Resource,
    method: str,
    verb: str,
    params: list[str],
    spec: dict[str, Any],
) -> None:
    name = f"{resource.name}.{method}"
    docs = spec.get("parameters") or {}
    # create / write take the model's own fields inside vals_list / vals, so
    # those are what the operation consumes for data-flow purposes.
    consumed = (
        [f.name for f in resource.fields if f.name != "id"] if method in {"create", "write"} else []
    )
    body = [
        ApiField(
            name=p,
            type=str((docs.get(p) or {}).get("annotation", "")),
            required=p in {"ids", "vals", "vals_list"},
            identifier=p == "ids",
            description=_one_line((docs.get(p) or {}).get("doc", "")),
        )
        for p in params
    ]
    # Every JSON-2 call also takes an optional context dict.
    body.append(ApiField(name="context", type="dict", description="Odoo call context."))
    ir.operations.append(
        Operation(
            name=name,
            method=verb,
            path=f"/json/2/{resource.name}/{method}",
            resource=resource.name,
            summary=_one_line(spec.get("doc") or f"{method} on {resource.title or resource.name}"),
            body_fields=body,
            body_kind="object",
            # Odoo binds the body to the method's parameters, so any other key
            # is an error.
            closed_body=True,
            data_fields=consumed,
        )
    )
    resource.operations.append(name)


__all__ = [
    "WRITE_METHODS",
    "ApiField",
    "Operation",
    "Resource",
    "WebIR",
    "from_odoo",
    "from_openapi",
]
