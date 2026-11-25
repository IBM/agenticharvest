#!/usr/bin/env Rscript
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
#
# AgenticHarvest bupaR miner script.
#
# Reads a flattened, case-centric event log (CSV with columns case_id, activity,
# timestamp and an optional resource) and mines it with the MIT-licensed
# bupaverse. Writes a JSON process model that agenticharvest.mining.model.ProcessModel
# reads back.
#
#   Usage: Rscript bupar.R <input_csv> <output_json>
#
# It always returns the activities, the directly-follows relation, and the trace
# variants, using bupaR core (stable APIs). When bupaRminer + bpmnR are also
# installed it additionally discovers a BPMN model and includes it.
#
# License note: bupaR, the bupaverse, and bupaRminer are MIT. This script uses
# only bupaRminer::discover(); it never calls the pm4py-backed conformance
# helpers, so it stays license-clean by default.

suppressWarnings(suppressMessages({
  have_core <- requireNamespace("bupaR", quietly = TRUE) &&
    requireNamespace("dplyr", quietly = TRUE) &&
    requireNamespace("lubridate", quietly = TRUE) &&
    requireNamespace("jsonlite", quietly = TRUE)
}))
if (!have_core) {
  message(
    "Missing R packages. install.packages(c('bupaverse', 'jsonlite')); ",
    "for BPMN also devtools::install_github('bupaverse/bupaRminer') and ",
    "install.packages('bpmnR')."
  )
  quit(status = 2L)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2L) {
  message("usage: bupar.R <input_csv> <output_json>")
  quit(status = 2L)
}
input_csv <- args[[1L]]
output_json <- args[[2L]]

suppressWarnings(suppressMessages({
  library(bupaR)
  library(dplyr)
  library(lubridate)
}))

data <- read.csv(input_csv, stringsAsFactors = FALSE, check.names = FALSE)
data$timestamp <- lubridate::ymd_hms(data$timestamp, tz = "UTC", quiet = TRUE)
if (!("resource" %in% names(data))) {
  data$resource <- "agenticharvest"
}

# Build a bupaR event log from the flattened rows.
log <- data %>%
  bupaR::simple_eventlog(
    case_id = "case_id",
    activity_id = "activity",
    timestamp = "timestamp",
    resource_id = "resource"
  )

# --- Activities (stable bupaR core) ---------------------------------------
acts <- as.data.frame(bupaR::activities(log))
activities <- lapply(seq_len(nrow(acts)), function(i) {
  list(
    name = as.character(acts[i, "activity"]),
    frequency = as.integer(acts[i, "absolute_frequency"])
  )
})

# --- Trace variants -------------------------------------------------------
variants <- list()
tryCatch({
  tr <- as.data.frame(bupaR::traces(log))
  variants <<- lapply(seq_len(nrow(tr)), function(i) {
    steps <- strsplit(as.character(tr[i, "trace"]), ",", fixed = TRUE)[[1L]]
    list(
      sequence = as.list(trimws(steps)),
      frequency = as.integer(tr[i, "absolute_frequency"])
    )
  })
}, error = function(e) {})

# --- Directly-follows relation (robust, from the ordered log) -------------
dfg <- data %>%
  dplyr::arrange(case_id, timestamp) %>%
  dplyr::group_by(case_id) %>%
  dplyr::mutate(.next_activity = dplyr::lead(activity)) %>%
  dplyr::ungroup() %>%
  dplyr::filter(!is.na(.next_activity)) %>%
  dplyr::count(activity, .next_activity, name = "frequency")
directly_follows <- lapply(seq_len(nrow(dfg)), function(i) {
  list(
    source = as.character(dfg[i, "activity"]),
    target = as.character(dfg[i, ".next_activity"]),
    frequency = as.integer(dfg[i, "frequency"])
  )
})

n_cases <- length(unique(data$case_id))
n_events <- nrow(data)

# --- BPMN discovery via bupaRminer (optional) -----------------------------
# Only run when the discovery package and bpmnR are installed. Extraction of the
# discovered model's nodes/flows is best-effort and guarded: any failure leaves
# bpmn NULL, so the stable results above are still returned.
bpmn <- NULL
if (requireNamespace("bupaRminer", quietly = TRUE) &&
    requireNamespace("bpmnR", quietly = TRUE)) {
  tryCatch({
    model <- bupaRminer::discover(log)
    nodes <- list()
    flows <- list()
    if (!is.null(model$nodes)) {
      nd <- as.data.frame(model$nodes)
      id_col <- if ("id" %in% names(nd)) "id" else names(nd)[[1L]]
      # bpmnR names the node kind column "objectType" (values "task",
      # "exclusiveGateway", ...); fall back to "type" for other producers.
      type_col <- if ("objectType" %in% names(nd)) "objectType"
                  else if ("type" %in% names(nd)) "type" else NA_character_
      nodes <- lapply(seq_len(nrow(nd)), function(i) {
        list(
          id = as.character(nd[i, id_col]),
          name = if ("name" %in% names(nd)) as.character(nd[i, "name"]) else "",
          type = if (!is.na(type_col)) as.character(nd[i, type_col]) else ""
        )
      })
    }
    if (!is.null(model$flows)) {
      fl <- as.data.frame(model$flows)
      src_col <- if ("sourceRef" %in% names(fl)) "sourceRef" else names(fl)[[1L]]
      tgt_col <- if ("targetRef" %in% names(fl)) "targetRef" else names(fl)[[2L]]
      flows <- lapply(seq_len(nrow(fl)), function(i) {
        list(
          source = as.character(fl[i, src_col]),
          target = as.character(fl[i, tgt_col])
        )
      })
    }
    bpmn <<- list(nodes = nodes, flows = flows)
  }, error = function(e) {
    bpmn <<- NULL
  })
}

result <- list(
  miner = "bupaR",
  n_cases = n_cases,
  n_events = n_events,
  activities = activities,
  directly_follows = directly_follows,
  variants = variants,
  bpmn = bpmn
)

jsonlite::write_json(result, output_json, auto_unbox = TRUE, pretty = TRUE, null = "null")
