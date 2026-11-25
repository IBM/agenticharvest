#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The bupaR miner — mine an OCEL with the R bupaverse, via a subprocess.

bupaR (and the wider `bupaverse <https://bupaverse.github.io/>`_) is an MIT-
licensed process-mining ecosystem written in R. Rather than embed an R runtime,
this miner shells out to ``Rscript`` with a bundled, self-contained script
(:mod:`bupar.R`): it flattens the object-centric log to the case-centric CSV
bupaR expects, runs the script, and reads back a JSON process model.

The engine degrades gracefully. With bupaR core present it always returns the
activities, the directly-follows relation, and the trace variants. When
``bupaRminer`` (also MIT) and ``bpmnR`` are additionally installed, the script
also discovers a BPMN model and attaches it. When ``Rscript`` is not on the
``PATH`` at all, :meth:`available` returns ``False`` and :meth:`mine` raises a
:class:`MinerError` with install guidance — so tests and callers skip cleanly on
a machine without R.

Only the discovery path (``discover()``) of ``bupaRminer`` is used; its optional
conformance helpers pull in pm4py, whose license AgenticHarvest deliberately avoids, so
they are never invoked.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from agenticharvest.core.events.model import OcelLog, _iso
from agenticharvest.mining.log import flatten, infer_case_type
from agenticharvest.mining.miners.base import MinerError
from agenticharvest.mining.model import ProcessModel

# The bundled R script lives beside this module inside the bupar package.
_SCRIPT = Path(__file__).with_name("bupar.R")

_INSTALL_HINT = (
    "install R (https://www.r-project.org/) and the bupaR packages: in R run "
    'install.packages(c("bupaR", "dplyr", "lubridate", "jsonlite")); for BPMN '
    "discovery also install the bupaverse GitHub packages: "
    'install.packages("remotes"); remotes::install_github(c('
    '"bupaverse/petrinetR", "bupaverse/bpmnR", "bupaverse/bupaRminer")).'
)


class BupaRMiner:
    """Mine an OCEL log with bupaR by invoking ``Rscript`` on a bundled script.

    Args:
        rscript: The ``Rscript`` executable name or path.
        case_type: The object type to treat as the case. Defaults to the type
            the most events relate to (see :func:`agenticharvest.mining.log.flatten`).
        resource_type: An optional object type to record as the event resource.
        script_path: Override for the bundled R script (mainly for testing).
        timeout: Seconds to allow the R subprocess before giving up.
    """

    name = "bupar"

    def __init__(
        self,
        rscript: str = "Rscript",
        case_type: str | None = None,
        resource_type: str | None = None,
        script_path: Path | None = None,
        timeout: float = 180.0,
    ) -> None:
        self._rscript = rscript
        self._case_type = case_type
        self._resource_type = resource_type
        self._script = Path(script_path) if script_path else _SCRIPT
        self._timeout = timeout

    def available(self) -> bool:
        """Return whether ``Rscript`` and bupaR core are installed and loadable."""
        if shutil.which(self._rscript) is None:
            return False
        try:
            probe = subprocess.run(
                [
                    self._rscript,
                    "-e",
                    'quit(status = as.integer(!requireNamespace("bupaR", quietly = TRUE)))',
                ],
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return probe.returncode == 0

    def mine(self, log: OcelLog) -> ProcessModel:
        """Mine ``log`` into a :class:`ProcessModel`.

        Raises:
            MinerError: if ``Rscript`` is not installed, or the R script fails or
                returns output that cannot be parsed.
        """
        case_type = self._case_type or infer_case_type(log)
        events = flatten(log, case_type=case_type, resource_type=self._resource_type)
        if not events:
            # Nothing relates to a case object; there is no process to mine, and
            # no reason to require R to say so.
            return ProcessModel(miner=self.name)

        if shutil.which(self._rscript) is None:
            raise MinerError(f"'{self._rscript}' not found on PATH — {_INSTALL_HINT}")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "log.csv"
            out_path = Path(tmp) / "model.json"
            self._write_csv(csv_path, events)
            try:
                proc = subprocess.run(
                    [
                        self._rscript,
                        "--vanilla",
                        str(self._script),
                        str(csv_path),
                        str(out_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise MinerError(f"failed to run bupaR: {exc}") from exc

            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).strip()
                raise MinerError(
                    f"bupaR mining failed (exit {proc.returncode}). {detail}"
                )
            try:
                data = json.loads(out_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                detail = (proc.stderr or "").strip()
                raise MinerError(
                    f"could not read bupaR output: {exc}. {detail}"
                ) from exc

        return ProcessModel.from_bupar(data)

    @staticmethod
    def _write_csv(path: Path, events: list) -> None:
        """Write the flattened events as the CSV the R script reads."""
        has_resource = any(e.resource for e in events)
        fieldnames = ["case_id", "activity", "timestamp"]
        if has_resource:
            fieldnames.append("resource")
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for event in events:
                row = {
                    "case_id": event.case_id,
                    "activity": event.activity,
                    "timestamp": _iso(event.timestamp),
                }
                if has_resource:
                    row["resource"] = event.resource or ""
                writer.writerow(row)
