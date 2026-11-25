#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""A sample support-ticket database — a real triage workflow.

The database a support team works out of. New tickets arrive ``open``; a triager
routes each to the agent whose skill matches the ticket's product and who has
the lightest load, and the assignment is recorded. A trigger keeps the audit
trail, exactly as a production schema would:

    tickets        (input)   id, product, priority, subject, status, assignee
    agents         (input)   id, name, skill, open_load
    assignments    (output)  ticket -> agent, written by the triager
    audit_log      (output)  written by the ``log_assignment`` trigger

    trigger log_assignment   AFTER INSERT ON assignments
                             -> UPDATE tickets, UPDATE agents, INSERT audit_log
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE agents (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    skill      TEXT    NOT NULL,
    open_load  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE tickets (
    id        INTEGER PRIMARY KEY,
    product   TEXT NOT NULL,
    priority  TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high')),
    subject   TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'open',
    assignee  INTEGER REFERENCES agents(id)
);
CREATE TABLE assignments (
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id),
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    PRIMARY KEY (ticket_id)
);
CREATE TABLE audit_log (
    id        INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    note      TEXT    NOT NULL
);
CREATE TRIGGER log_assignment AFTER INSERT ON assignments
BEGIN
    UPDATE tickets SET status = 'assigned', assignee = NEW.agent_id
        WHERE id = NEW.ticket_id;
    UPDATE agents SET open_load = open_load + 1 WHERE id = NEW.agent_id;
    INSERT INTO audit_log (ticket_id, note)
        VALUES (NEW.ticket_id, 'assigned to agent ' || NEW.agent_id);
END;
"""

#: The support team: (id, name, skill, current open load).
AGENTS: list[tuple[int, str, str, int]] = [
    (1, "Priya", "billing", 2),
    (2, "Marco", "billing", 0),
    (3, "Jun", "mobile", 1),
    (4, "Ines", "api", 3),
    (5, "Theo", "api", 1),
]

#: Each queue is one case: (id, product, priority, subject).
TICKET_QUEUES: dict[str, list[tuple[int, str, str, str]]] = {
    "monday": [
        (1001, "billing", "high", "Charged twice for October"),
        (1002, "api", "normal", "429s on the search endpoint"),
        (1003, "mobile", "low", "Dark mode toggle missing"),
    ],
    "tuesday": [
        (2001, "api", "high", "Webhook signatures fail to verify"),
        (2002, "billing", "normal", "Invoice PDF has the wrong address"),
    ],
    "wednesday": [
        (3001, "mobile", "high", "App crashes on launch after update"),
        (3002, "billing", "low", "How do I change my card?"),
        (3003, "api", "normal", "Pagination cursor skips records"),
        (3004, "billing", "high", "Refund not received"),
    ],
}


def build_support_db(
    dest: str | Path, tickets: list[tuple[int, str, str, str]] | None = None
) -> Path:
    """Write a support-ticket database to ``dest`` and return its path.

    Args:
        dest: Where to write the ``.db`` file (replaced if it exists).
        tickets: The open tickets in this queue, as ``(id, product, priority,
            subject)``. Defaults to the first queue in :data:`TICKET_QUEUES`.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    rows = tickets if tickets is not None else next(iter(TICKET_QUEUES.values()))
    connection = sqlite3.connect(dest)
    try:
        connection.executescript(SCHEMA)
        connection.executemany("INSERT INTO agents VALUES (?, ?, ?, ?)", AGENTS)
        connection.executemany(
            "INSERT INTO tickets (id, product, priority, subject) VALUES (?, ?, ?, ?)", rows
        )
        connection.commit()
    finally:
        connection.close()
    return dest


__all__ = ["AGENTS", "SCHEMA", "TICKET_QUEUES", "build_support_db"]
