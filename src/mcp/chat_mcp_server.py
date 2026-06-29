"""
Alma Insights -- Chat MCP Tool Server

Lightweight MCP server exposing chat-specific tools for structured
function calling via ACP.  Gemini calls these natively (no text
TOOL_CALL parsing needed).

Transport: stdio JSON-RPC 2.0
Entry point: python -m src.mcp.chat_mcp_server

Environment variables:
    ALMA_DB_PATH -- Path to SQLite database
"""

import json
import logging
import os
import sys

logger = logging.getLogger("alma.chat_mcp")

TOOL_SCHEMAS = [
    {
        "name": "query_issues",
        "description": (
            "Primary tool for answering 'what issues exist' questions. Returns "
            "a ranked list of canonical issues (concepts or clusters) matching "
            "the scope filters, each with ticket_count, pct_of_scope, a handful "
            "of sample ticket_ids, and a body excerpt. ALWAYS set scope filters "
            "to whatever the user asked about (payer, TRC, state, date range) "
            "— never expand scope silently. Use group_by='concept' for "
            "prevalence questions; 'cluster' when the user wants fine-grained "
            "detail within a TRC. Supersedes query_entities, "
            "query_ticket_classifications, and query_findings for issue questions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trc": {
                    "type": "string",
                    "description": "Filter to one TRC (matches trc_code or trc_label)",
                },
                "payer": {
                    "type": "string",
                    "description": "Insurance payer substring (e.g. 'Thunderbird')",
                },
                "provider": {
                    "type": "string",
                    "description": "Provider ID exact match",
                },
                "state": {
                    "type": "string",
                    "description": "Service state (2-letter code)",
                },
                "date_range": {
                    "type": "string",
                    "description": "ISO date range 'YYYY-MM-DD/YYYY-MM-DD' (either side optional)",
                },
                "concept_id": {
                    "type": "string",
                    "description": "Drill into a specific concept by concept_id",
                },
                "tag": {
                    "type": "string",
                    "description": "Restrict to tickets carrying this tag",
                },
                "group_by": {
                    "type": "string",
                    "enum": ["concept", "cluster", "trc", "payer", "provider"],
                    "description": "Aggregation dimension (default 'concept')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max top issues to return (default 10, max 50)",
                },
                "include_samples": {
                    "type": "boolean",
                    "description": "Include sample ticket_ids + excerpts per issue (default true)",
                },
            },
        },
    },
    {
        "name": "audit_tag_correlation",
        "description": (
            "Given a tag, audit whether tagged tickets actually describe what "
            "the tag claims. Returns total tagged, concept/cluster distribution, "
            "the expected cluster (from the incidents table, if any), and a "
            "ranked list of 'likely mis-tagged' tickets — those whose canonical "
            "cluster differs from the expected one, sorted by cosine distance "
            "from the expected centroid. Tags are applied by event and are "
            "often wrong; this is how we find the errors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "The tag to audit (e.g. 'incident_portal_outage_0301')",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max mis-tagged tickets to return (default 10, max 50)",
                },
            },
            "required": ["tag"],
        },
    },
    {
        "name": "list_tickets",
        "description": (
            "List individual tickets matching filters, with issue summaries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "Optional filters (trc_codes, friction_types, etc.)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max tickets to return (default 20, max 50)",
                },
            },
        },
    },
    {
        "name": "semantic_search",
        "description": (
            "Find tickets semantically similar to a natural language query "
            "using Qwen 3 embeddings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_stats",
        "description": (
            "Query statistical engine outputs. "
            "stat_type=anomalies returns real statistical anomalies from anomaly_flags "
            "(z_score, theta_level, metric_type, date, notes). "
            "stat_type=trends returns weekly volume by dimension "
            "(trc, friction_type, sub_cluster, payer, provider). "
            "stat_type=baselines returns TRC baseline snapshots. "
            "stat_type=csat returns CSAT score bucket counts (low/mid/high/null + mean). "
            "stat_type=friction_distribution returns the ranked breakdown of "
            "ticket_index.friction_type — USE THIS for 'product bug', 'feature broken', "
            "'friction pattern', 'repeat contact', or any aggregate-over-friction question. "
            "Optional cross_dim='trc'|'payer' nests per-dimension counts inside each row."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stat_type": {
                    "type": "string",
                    "enum": [
                        "anomalies", "trends", "baselines", "csat",
                        "friction_distribution",
                    ],
                    "description": "Type of statistical data to query",
                },
                "severity": {
                    "type": "string",
                    "enum": ["severe", "minor"],
                    "description": "Anomaly severity filter (for stat_type=anomalies). "
                    "'severe' = theta_level>=2, 'minor' = theta_level=1.",
                },
                "metric_type": {
                    "type": "string",
                    "description": "Anomaly metric filter (sentiment, term_freq, volume...)",
                },
                "trc_code": {
                    "type": "string",
                    "description": "Filter anomalies to one TRC",
                },
                "date_range": {
                    "type": "string",
                    "description": "ISO range 'YYYY-MM-DD/YYYY-MM-DD' for anomaly/trend date filter",
                },
                "dimension": {
                    "type": "string",
                    "enum": [
                        "trc", "trc_code", "friction_type",
                        "sub_cluster", "cluster", "sub_pattern",
                        "payer", "insurance_payer",
                        "provider", "provider_id",
                    ],
                    "description": "Trend dimension (for stat_type=trends)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 20, max 100/500 depending on stat_type)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "(friction_distribution only) Top N friction types, default 10, max 20",
                },
                "cross_dim": {
                    "type": "string",
                    "enum": ["trc", "payer"],
                    "description": "(friction_distribution only) Nest a per-{trc|payer} breakdown inside each row",
                },
                "friction_type": {
                    "type": "string",
                    "description": (
                        "(friction_distribution only) Filter to a single friction type. "
                        "Known canonical values: incorrect_charge, feature_broken, "
                        "repeat_contact, access_blocked, policy_confusion, missing_information, "
                        "self_serve_failure, process_delay, automation_loop, communication_gap, "
                        "escalation_demand, other. Open enum — novel values return a "
                        "gate_warning rather than an error."
                    ),
                },
                "payer": {
                    "type": "string",
                    "description": "(friction_distribution / trends only) Substring filter on insurance_payer",
                },
            },
            "required": ["stat_type"],
        },
    },
    {
        "name": "read_thread",
        "description": (
            "Read the full conversation thread for a specific ticket (PII-redacted). "
            "Use when the user asks to see a specific ticket's conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket ID to read (e.g. 'TKT-12345')",
                },
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "read_threads_batch",
        "description": (
            "Read conversation threads for up to 5 tickets at once (PII-redacted)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of ticket IDs (max 5)",
                },
            },
            "required": ["ticket_ids"],
        },
    },
    {
        "name": "query_report",
        "description": (
            "Read a previously generated analysis report. "
            "Returns latest report if no report_id specified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_id": {
                    "type": "string",
                    "description": "Specific report ID (optional, latest if omitted)",
                },
                "section": {
                    "type": "string",
                    "enum": ["findings", "summary_stats", "recommendations"],
                    "description": "Return only a specific section",
                },
            },
        },
    },
    {
        "name": "search_local_documents",
        "description": (
            "Search the enablement document library stored locally — source "
            "documents pulled from Google Drive plus generated Guru card drafts. "
            "Use whenever the user asks to find, look up, or recall a document, "
            "draft, or past content by name or topic. Returns matching documents "
            "and drafts with snippets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to match names and bodies."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_business_drive",
        "description": (
            "Query the connected business Google Drive for documents. Searches the "
            "live Drive when read access is configured, otherwise the locally-"
            "indexed mirror of that Drive. Use when the user asks what's in the "
            "Drive or to find a Drive document. Returns file names, links, and snippets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for in the Drive."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "asana_discover",
        "description": (
            "Discover the user's Asana projects, custom fields, and enum-value GIDs "
            "so a non-technical operator never has to find them by hand. Optionally "
            "pass project_gid to narrow to one project. Returns projects [{gid, name}] "
            "and custom_fields with field gids + enum_options [{gid, name}]. Use BEFORE "
            "set_asana_board_config to resolve GIDs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_gid": {"type": "string", "description": "Optional — narrow to one project's custom fields."},
            },
        },
    },
    {
        "name": "set_asana_board_config",
        "description": (
            "Save an Asana board's enablement config using resolved GIDs. THE ONLY "
            "SETTING THE ASSISTANT MAY WRITE — creates/updates one Asana source in "
            "monitor_sources and touches nothing else. Call after asana_discover."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_gid": {"type": "string"},
                "project_name": {"type": "string"},
                "indicator_field_gid": {"type": "string"},
                "indicator_field_name": {"type": "string"},
                "indicator_value_gid": {"type": "string"},
                "indicator_value_name": {"type": "string"},
                "priority_field_gid": {"type": "string"},
                "assignee_field_gid": {"type": "string"},
            },
            "required": ["project_gid", "project_name", "indicator_field_gid",
                         "indicator_field_name", "indicator_value_gid", "indicator_value_name"],
        },
    },
    {
        "name": "create_card_draft",
        "description": (
            "Create a NEW Guru card draft from a title and Markdown content. The draft "
            "is saved pending review — publish it with push_guru_draft. Use this to "
            "author a card from scratch (not from a Drive document)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The card title."},
                "content": {"type": "string", "description": "The card body in Markdown."},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "revise_draft",
        "description": (
            "Revise an existing Guru card draft per an instruction (e.g. 'tighten "
            "the intro', 'add a rollout-date section') and re-render it. Pass the "
            "draft_id and the change to make."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft id to revise."},
                "instruction": {"type": "string", "description": "The change to make."},
            },
            "required": ["draft_id", "instruction"],
        },
    },
    {
        "name": "search_guru_cards",
        "description": (
            "Search LIVE Guru for existing cards by topic or title. Use this to "
            "REVIEW what cards already exist before drafting or updating — returns "
            "each card's id, title, collection, and a text snippet. Optionally scope "
            "to specific collections."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "Topic or title to search for."},
                "collections": {"type": "array", "items": {"type": "string"},
                                "description": "Optional: limit to these collection names/ids."},
                "limit": {"type": "integer", "description": "Max cards to return (default 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "index_content",
        "description": (
            "Build or refresh the SUMMARY CATALOG over PHI-free content (uploaded/Drive "
            "docs and Guru cards). Each item gets a short content summary so look-alike "
            "titles are distinguishable and search stays fast at scale. Run this when "
            "content is added or changed. scope: 'docs' (default), 'guru', or 'all'."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "scope": {"type": "string", "description": "docs | guru | all (default docs)."},
                "query": {"type": "string", "description": "For scope=guru: which cards to pull."},
                "collections": {"type": "array", "items": {"type": "string"},
                                "description": "Optional: limit Guru indexing to these collections."},
                "limit": {"type": "integer", "description": "Max items to index (default 200)."},
            },
            "required": [],
        },
    },
    {
        "name": "search_content",
        "description": (
            "Search the content catalog by topic and return ranked candidates (id, title, "
            "summary, url, score). This is the scalable, deterministic way to FIND the right "
            "doc or card — it disambiguates similarly-titled items by their content summary. "
            "Run index_content first if the catalog is empty."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "Topic to search the catalog for."},
                "limit": {"type": "integer", "description": "Max candidates to return (default 5)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "card_history",
        "description": (
            "Show the audit trail for a Guru card: every update — what changed, from what "
            "source doc, which changes were rejected as ungrounded, who approved, and when. "
            "Pass the card id or URL."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "card_ref": {"type": "string", "description": "Guru card id or app.getguru.com URL."},
                "card_id": {"type": "string", "description": "Alias for card_ref (a raw card id)."},
                "url": {"type": "string", "description": "Alias for card_ref (an app.getguru.com URL)."},
            },
            "required": [],
        },
    },
    {
        "name": "card_effectiveness",
        "description": (
            "Did-it-work feedback for a card: its update history plus the measured "
            "ticket-volume impact (pre/post volume, delta_pct, significance) when available. "
            "Until post-publish data accrues it reports the push as anchored/pending. Pass the "
            "card id or URL."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "card_ref": {"type": "string", "description": "Guru card id or app.getguru.com URL."},
                "card_id": {"type": "string", "description": "Alias for card_ref (a raw card id)."},
                "url": {"type": "string", "description": "Alias for card_ref (an app.getguru.com URL)."},
            },
            "required": [],
        },
    },
    {
        "name": "find_cards_to_update",
        "description": (
            "Call this when the user asks 'what should I work on next', 'which cards "
            "need attention', or wants the prioritized Guru-card work queue. Returns "
            "the ranked attention queue (lowest health first) — each card with id, "
            "title, score, bucket, and a short 'why' reason. Optionally filter to one "
            "bucket. Reasons over INTENT — prefer this over raw analytics for "
            "'what to update' questions."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "limit": {"type": "integer", "description": "Max cards to return (default 10)."},
                "bucket": {
                    "type": "string",
                    "enum": ["source_changed", "verification_overdue", "gap_dup", "healthy"],
                    "description": "Optional: restrict to one attention bucket.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_stale_cards",
        "description": (
            "Call this when the user asks which cards are stale, out of date, or "
            "overdue for verification. Returns cards in the verification_overdue "
            "bucket (falling back to the least-fresh cards when none are overdue), "
            "ranked staleest-first, each with id, title, score, bucket, and a "
            "'why' reason."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "limit": {"type": "integer", "description": "Max cards to return (default 10)."},
            },
            "required": [],
        },
    },
    {
        "name": "find_content_gaps",
        "description": (
            "Call this when the user asks about content gaps or duplicate cards. "
            "Returns cards flagged as a coverage gap or a near-duplicate (the gap_dup "
            "bucket), ranked, each noting the gap or the duplicated card ids in its "
            "'why' reason."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "limit": {"type": "integer", "description": "Max cards to return (default 10)."},
            },
            "required": [],
        },
    },
    {
        "name": "open_guru_card",
        "description": (
            "Open a Guru card in the operator's default web browser. Pass the card id "
            "or its app.getguru.com URL (e.g. from search_guru_cards results). Use this "
            "when the user asks to open / view / see a card in their browser."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "card_ref": {"type": "string",
                             "description": "Guru card id or app.getguru.com/card/<id> URL."},
                "card_id": {"type": "string", "description": "Alias for card_ref (a raw card id)."},
                "url": {"type": "string", "description": "Alias for card_ref (an app.getguru.com URL)."},
            },
            "required": [],
        },
    },
    {
        "name": "research_topic",
        "description": (
            "Research a topic across EVERY source Renn can reach — live Guru cards, "
            "locally-stored documents, and ticket signals — and return the consolidated "
            "reference points. Use this to gather authoritative context before writing or "
            "updating a card; then pass the relevant card_ids/doc_ids as reference_refs."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "topic": {"type": "string", "description": "The subject to research."},
                "limit": {"type": "integer", "description": "Max items per source (default 5)."},
                "collections": {"type": "array", "items": {"type": "string"},
                                "description": "Optional: scope Guru results to these collections."},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "update_cards_from_doc",
        "description": (
            "FAN-OUT update: a policy/doc change usually affects MORE THAN ONE card. "
            "This finds the SET of Guru cards a source doc affects (content-aware), and "
            "for each card that actually needs changing it stages a draft — cards with no "
            "change are reported but not drafted. Use this instead of update_card_from_doc "
            "when a change could touch several cards. Pass a task_id or doc_ref/doc_query for "
            "the source; optionally search/collections to scope, and max_cards. Staging only — "
            "review each diff and publish each with push_guru_draft once approved."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task_id": {"type": "string", "description": "Enablement task naming the source doc."},
                "doc_ref": {"type": "string", "description": "Source document id."},
                "doc_query": {"type": "string", "description": "Or find the source doc by name/topic."},
                "search": {"type": "string", "description": "Topic to find affected cards (defaults to the doc's title)."},
                "collections": {"type": "array", "items": {"type": "string"},
                                "description": "Optional: scope candidate cards to these collections."},
                "max_cards": {"type": "integer", "description": "Max cards to check (default 5)."},
            },
            "required": [],
        },
    },
    {
        "name": "update_card_from_doc",
        "description": (
            "Review a source document and update the EXISTING Guru card it relates to: "
            "find the matching card, identify what must change vs the document, write the "
            "revision, and STAGE a draft (does not publish). Pass a task_id (it reads the "
            "source doc + target card from the task's scratchpad), OR pass doc_ref/doc_query "
            "for the source and card_ref/card_name/search for the target. After it stages a "
            "draft, show the summary + diff to the user and publish with push_guru_draft only "
            "once they approve."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task_id": {"type": "string", "description": "Enablement task whose scratchpad names the source doc + target card."},
                "doc_ref": {"type": "string", "description": "Source document id (from search_local_documents)."},
                "doc_query": {"type": "string", "description": "Or find the source document by name/topic."},
                "card_ref": {"type": "string", "description": "Target Guru card id or app.getguru.com URL."},
                "card_name": {"type": "string", "description": "Or the exact target card title."},
                "search": {"type": "string", "description": "Or search Guru for the target card by topic."},
                "collections": {"type": "array", "items": {"type": "string"},
                                "description": "Optional: scope the card search to these collection names/ids."},
            },
            "required": [],
        },
    },
    {
        "name": "push_guru_draft",
        "description": (
            "Publish a card draft to Guru — creates a new card, or updates the "
            "existing card if the draft is linked to one. This is the 'push to Guru' "
            "action; only call it when the operator asked to publish. To publish into "
            "a specific sub-folder, first call list_guru_collections + list_guru_folders, "
            "then pass the chosen collection_id and folder_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft id to publish."},
                "collection_id": {"type": "string", "description": "Optional target collection for a new card."},
                "folder_id": {"type": "string", "description": "Optional target folder (sub-folder) id within the collection."},
            },
            "required": ["draft_id"],
        },
    },
    {
        "name": "list_guru_collections",
        "description": (
            "List the Guru collections (top-level knowledge areas) so you can pick where "
            "to publish a card. Returns each collection's id and name."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_guru_folders",
        "description": (
            "List a Guru collection's folders (sub-folders) so you can publish a card "
            "into the right one. Pass the collection id OR its name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Collection id or name."},
            },
            "required": ["collection"],
        },
    },
    {
        "name": "render_card_preview",
        "description": "Return a draft's current title and Markdown content for preview.",
        "inputSchema": {
            "type": "object",
            "properties": {"draft_id": {"type": "integer"}},
            "required": ["draft_id"],
        },
    },
    {
        "name": "draft_subtasks",
        "description": (
            "Attach a checklist of subtasks to a task. Decompose the work yourself "
            "and pass the steps as items."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "Ordered checklist steps."},
            },
            "required": ["task_id", "items"],
        },
    },
    {
        "name": "add_subtask",
        "description": "Add a single subtask to a task.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["task_id", "text"],
        },
    },
    {
        "name": "toggle_subtask",
        "description": "Check or uncheck a subtask.",
        "inputSchema": {
            "type": "object",
            "properties": {"subtask_id": {"type": "string"}, "done": {"type": "boolean"}},
            "required": ["subtask_id", "done"],
        },
    },
    {
        "name": "update_scratchpad",
        "description": "Write freeform operator notes on a task.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["task_id", "text"],
        },
    },
    {
        "name": "create_asana_subtask",
        "description": (
            "Add a subtask to a task AND create it back in Asana under the parent "
            "Asana task. Saves locally; syncs to Asana only for Asana-sourced tasks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"}, "text": {"type": "string"},
            },
            "required": ["task_id", "text"],
        },
    },
    {
        "name": "post_asana_comment",
        "description": "Post a comment back to the linked Asana task (Asana-sourced tasks only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"}, "text": {"type": "string"},
            },
            "required": ["task_id", "text"],
        },
    },
    {
        "name": "update_asana_due_date",
        "description": (
            "Update a task's due date locally AND push it to the linked Asana task. "
            "due_on is an ISO date (YYYY-MM-DD) or null to clear."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "due_on": {"type": "string", "description": "ISO date YYYY-MM-DD or null."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "create_task",
        "description": "Create an enablement task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "source": {"type": "string", "description": "drive|guru|asana|manual (default manual)."},
                "kind": {"type": "string", "description": "card_review|doc_due_date|product_update|request."},
                "due_date": {"type": "string", "description": "ISO date (optional)."},
                "priority": {"type": "string", "description": "low|normal|high."},
                "summary": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": "Update an enablement task's fields (status, priority, due_date, assignee, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "description": "open|in_progress|done|dismissed."},
                "priority": {"type": "string"},
                "due_date": {"type": "string"},
                "assignee": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List enablement tasks, optionally filtered by status/source/kind/due date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "source": {"type": "string"},
                "kind": {"type": "string"},
                "due_before": {"type": "string", "description": "ISO date."},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "search_drive_docs",
        "description": "Search the locally-indexed Drive documents by name/topic.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_drive_doc",
        "description": "Fetch one indexed Drive document (with full text) by its id.",
        "inputSchema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "list_style_guides",
        "description": "List the operator's stored style guides (the active one is flagged).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_style_guide",
        "description": "Read the active style guide's full text to follow it when writing a card.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_active_style_guide",
        "description": "Switch which stored style guide is active (injected into card gen/revise).",
        "inputSchema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "run_monitor_now",
        "description": "Run a one-off poll of the configured Asana/Drive monitors now.",
        "inputSchema": {
            "type": "object",
            "properties": {"source": {"type": "string", "description": "Optional: 'asana' or 'drive' to poll just one."}},
        },
    },
    {
        "name": "import_guru_card",
        "description": (
            "Import an existing Guru card (by id or app.getguru.com URL) as an "
            "editable draft. The draft stays linked, so publishing UPDATES the "
            "same card rather than creating a duplicate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "card_ref": {"type": "string", "description": "Guru card id or card URL."},
            },
            "required": ["card_ref"],
        },
    },
    {
        "name": "get_guru_analytics",
        "description": (
            "Read the locally-synced Guru analytics: metric='top_cards' (most "
            "viewed), 'verification' (queue KPIs), 'comments' (open card "
            "comments), or 'due_cards' (cards needing an update)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "description": "top_cards | verification | comments | due_cards"},
                "days": {"type": "integer", "description": "Window in days (default 30)."},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "create_task_from_comment",
        "description": (
            "Convert an open Guru card comment (see get_guru_analytics "
            "metric='comments') into an enablement task. Idempotent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "comment_id": {"type": "string", "description": "The Guru comment id."},
            },
            "required": ["comment_id"],
        },
    },
]


# Mode gating: the host process may strip tools that must stay
# unreachable in the current app mode (e.g. semantic_search in
# enablement mode keeps the torch/embedding stack unloadable).
# Filtering before _MCP_ALLOWED_TOOLS derives removes both the
# advertisement (tools/list) and the dispatch path in one place.
_EXCLUDED_TOOLS = {
    name.strip()
    for name in os.environ.get("ALMA_MCP_EXCLUDE_TOOLS", "").split(",")
    if name.strip()
}
if _EXCLUDED_TOOLS:
    TOOL_SCHEMAS = [t for t in TOOL_SCHEMAS if t["name"] not in _EXCLUDED_TOOLS]


def _get_db_connection():
    import sqlite3
    db_path = os.environ.get("ALMA_DB_PATH", "")
    if not db_path:
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enables dict-style row access
    return conn


_MCP_ALLOWED_TOOLS = {t["name"] for t in TOOL_SCHEMAS}


def _read_active_session_id() -> str | None:
    """Read the chat session-id pointer file the host page maintains.

    Added 2026-05-07: previously every MCP tool dispatch was tagged with
    'adhoc_probe' because the stdio MCP protocol provides no session
    context. The chat page now writes the active session_id to a file
    whose path is passed in via the ALMA_CHAT_SESSION_FILE env var; the
    server reads it on every dispatch. Falls back to None (so the
    registry auto-tags 'adhoc_probe') when the file is unreadable.
    """
    path = os.environ.get("ALMA_CHAT_SESSION_FILE", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
        return value or None
    except OSError:
        return None


def _execute_tool(name, args):
    """Execute a chat tool and return the result dict.

    Delegates to src.data.chat_tools.registry.dispatch_tool so every call
    is logged to chat_tool_executions. The session_id is sourced from
    the ALMA_CHAT_SESSION_FILE pointer (see ``_read_active_session_id``)
    so the Drill Down monitor's tools tile actually counts the calls.
    Falls back to "adhoc_probe" when no chat session is active.

    Restricts to tool names declared in TOOL_SCHEMAS so legacy registry
    aliases (query_entities, query_tickets, etc.) don't leak through
    the MCP interface.
    """
    if name not in _MCP_ALLOWED_TOOLS:
        return {"error": f"Unknown tool: {name}"}

    conn = _get_db_connection()
    if not conn:
        return {"error": "ALMA_DB_PATH not set"}

    session_id = _read_active_session_id()

    try:
        from src.data.chat_tools.registry import dispatch_tool
        # dispatch_tool returns a JSON string; MCP expects a dict.
        result_json = dispatch_tool(
            tool_name=name,
            args=args,
            conn=conn,
            session_filters={},
            session_id=session_id,  # sourced from pointer file; None → adhoc_probe
            message_id=None,
        )
        try:
            return json.loads(result_json)
        except (json.JSONDecodeError, TypeError):
            return {"raw": result_json}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def _make_response(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _make_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def run_server():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(name)s %(levelname)s %(message)s")
    logger.info("chat-mcp-server starting (pid=%d)", os.getpid())

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(msg, dict):
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "initialize":
            response = _make_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "alma-chat-tools", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            response = _make_response(msg_id, {"tools": TOOL_SCHEMAS})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = _execute_tool(tool_name, arguments)
                response = _make_response(msg_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False, default=str)}],
                })
            except Exception as e:
                response = _make_error(msg_id, -32603, f"Tool failed: {e}")
        else:
            if msg_id is not None:
                response = _make_error(msg_id, -32601, f"Method not found: {method}")
            else:
                continue

        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()

    logger.info("chat-mcp-server exiting")


if __name__ == "__main__":
    run_server()
