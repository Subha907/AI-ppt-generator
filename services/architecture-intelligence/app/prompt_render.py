"""Render a resolved semantic rule pack into a type-specific prompt block.

The shared contract/base prompt lives in prompts.py. This module turns ONE
resolved rule pack (see rules.get_rule_pack) into the type-specific instruction
section that is appended to the base:

  * rule_pack.id + preferred layout header
  * authoring_rules   -> concrete "how to build it" instructions
  * required_concerns -> "your diagram MUST cover" checklist
  * conditional_concerns -> "include X only when evidence mentions Y"
  * node_vocabulary   -> allowed node types per group/lane
  * aws_vocabulary    -> canonical AWS services per concern
  * prohibited_behavior -> hard DON'Ts
  * completeness_checks -> a final self-check the model must satisfy

Rendering is defensive: every section is optional, so a thin pack (only
required_concerns) still renders a usable block.
"""
from __future__ import annotations

from typing import Any

# Human-friendly phrasing for the machine-readable prohibited_behavior tokens.
_PROHIBITED_PHRASING = {
    "invent_customer_requirements": "invent customer requirements not in the evidence",
    "represent_recommendation_as_explicit_rfp": "present your own recommendation as an explicit RFP requirement",
    "output_pptx_coordinates": "output any coordinates, sizes, or positions",
    "output_font_or_color_values": "output any font or color values",
    "invent_regions": "invent AWS regions not stated in the evidence",
    "invent_availability_zones": "invent Availability Zones beyond what HA requires",
    "invent_subnet_cidr": "invent subnet CIDR ranges",
    "invent_cidr_ranges": "invent CIDR ranges",
    "place_global_service_inside_vpc": "place a global/regional service (IAM, CloudFront, S3) inside a VPC/subnet",
    "duplicate_regional_service_per_az": "duplicate a REGIONAL/global service into each AZ (only workload compute/db/LB is duplicated per AZ)",
    "place_edge_service_in_regional_group": "place a global EDGE service (Route 53, CloudFront, WAF) in a right-hand regional group - they belong in the LEFT edge column",
    "direct_inbound_to_private_subnet": "draw a direct inbound edge into a private-app/isolated subnet from anything other than the public-tier ALB",
    "include_network_routing_details": "include network routing, VPC, subnet, or AZ details",
    "assign_database_without_design_decision": "assign a specific database product without a design rationale",
    "use_aws_service_names_for_logical_components": "use AWS service names for the logical application components",
    "include_vpc_subnet_or_az": "include VPC, subnet, or Availability Zone constructs",
    "include_aws_service_icons": "include any AWS services or icons",
    "create_capabilities_from_technology_names": "create business capabilities out of technology/product names",
    "include_vpc_subnet_az_or_servers": "include VPC, subnets, AZs, servers, or any infrastructure",
    "invent_data_classification": "invent data classification levels",
    "imply_realtime_from_batch": "imply real-time processing when the evidence only supports batch",
    "omit_direction": "omit the direction of any data-flow edge",
    "invent_direct_connect_or_vpn": "invent Direct Connect or VPN links not in the evidence",
    "show_routes_without_evidence": "show routes/route tables without supporting evidence",
    "invent_rto_or_rpo": "invent RTO or RPO values",
    "infer_active_active_without_evidence": "infer active-active topology without evidence",
    "invent_replication_direction": "invent a replication direction",
    "invent_tools": "invent specific tools not named in the evidence",
    "omit_mandatory_approval": "omit a mandatory approval step",
    "omit_rollback": "omit the rollback path",
    "invent_protocols": "invent protocols not supported by evidence",
    "represent_event_as_synchronous": "represent an event/async interaction as synchronous",
    "invent_external_systems": "invent external systems not in the evidence",
    "show_subnet_or_az_details": "show subnet or AZ details",
    "invent_process_steps": "invent process steps not in the evidence",
    "omit_decision_outcomes": "omit the outcomes of a decision point",
    "create_service_per_noun_without_domain_evidence": "create one microservice per noun without domain evidence",
    "use_shared_database_as_default": "default to a shared database across services",
    "grant_tools_without_authorization": "grant an agent tools without an authorization control",
    "invent_autonomous_actions": "invent autonomous actions beyond the evidence",
    "omit_human_approval_for_high_risk": "omit human approval for high-risk actions",
    "imply_model_training_when_only_rag": "imply model training when only RAG is in scope",
    "expose_sensitive_content": "expose sensitive content",
    "invent_vector_store": "invent a specific vector store without evidence",
    "remove_current_constraints": "drop constraints that apply to the current state",
    "present_transition_as_target": "present a transitional state as the target state",
    "invent_timeline": "invent a migration timeline",
    "claim_compliance_certification": "claim any compliance certification",
    "invent_security_products": "invent specific security products without evidence",
}


def _phrase_prohibited(token: str) -> str:
    return _PROHIBITED_PHRASING.get(token, token.replace("_", " "))


def _humanize_concern(token: str) -> str:
    return token.replace("_", " ")


def _render_authoring_rules(rules: dict[str, Any]) -> list[str]:
    """authoring_rules values are either a string or a list of strings."""
    lines: list[str] = []
    for key, val in rules.items():
        title = key.replace("_", " ").upper()
        if isinstance(val, list):
            lines.append(f"- {title}:")
            for item in val:
                lines.append(f"    * {item}")
        else:
            lines.append(f"- {title}: {val}")
    return lines


def render_type_block(pack: dict) -> str:
    """Return the type-specific prompt section for a resolved rule pack."""
    rp = pack.get("rule_pack", {})
    pack_id = rp.get("id", "unknown")
    layout = pack.get("renderer_hints", {}).get("preferred_layout", "n/a")

    parts: list[str] = []
    parts.append(f"=== DIAGRAM RULE PACK: {pack_id} (layout: {layout}) ===")

    # 1. Authoring rules - the concrete "how to build it".
    authoring = pack.get("authoring_rules") or {}
    if authoring:
        parts.append("\nHOW TO BUILD THIS DIAGRAM:")
        parts.extend(_render_authoring_rules(authoring))

    # 2. Required concerns - the must-cover checklist.
    required = pack.get("required_concerns") or []
    if required:
        parts.append(
            "\nYour diagram MUST cover every one of these concerns "
            "(omit none):"
        )
        parts.append("  " + ", ".join(_humanize_concern(c) for c in required))

    # 3. Conditional concerns - include only when evidence triggers.
    conditional = pack.get("conditional_concerns") or {}
    if conditional:
        parts.append("\nInclude these ONLY when the evidence supports them:")
        for name, spec in conditional.items():
            when = (spec or {}).get("include_when", {})
            triggers = when.get("evidence_contains_any", [])
            otherwise = (spec or {}).get("otherwise")
            trig = ", ".join(str(t) for t in triggers) if triggers else "relevant evidence"
            line = f"- {_humanize_concern(name)}: include when evidence mentions [{trig}]"
            if otherwise == "omit":
                line += "; otherwise omit it entirely"
            parts.append(line)

    # 4. Node vocabulary - allowed node types per group/lane.
    node_vocab = pack.get("node_vocabulary") or {}
    if node_vocab:
        parts.append("\nUse these node types per group/lane:")
        for group, types in node_vocab.items():
            parts.append(f"- {_humanize_concern(group)}: {', '.join(types)}")

    # 5. AWS service vocabulary - canonical services per concern.
    aws_vocab = pack.get("aws_vocabulary") or {}
    if aws_vocab:
        parts.append(
            "\nPreferred AWS services per concern (use these unless the "
            "evidence names something specific; put the name in "
            "properties.awsService):"
        )
        for concern, svcs in aws_vocab.items():
            parts.append(f"- {_humanize_concern(concern)}: {', '.join(svcs)}")

    # 6. Prohibited behavior - hard DON'Ts.
    prohibited = pack.get("prohibited_behavior") or []
    if prohibited:
        parts.append("\nYou MUST NOT (these will cause rejection):")
        for tok in prohibited:
            parts.append(f"- Do NOT {_phrase_prohibited(tok)}.")

    # 7. Completeness self-check - what will be validated.
    checks = pack.get("completeness_checks") or []
    if checks:
        parts.append(
            "\nBefore returning, self-check that your model satisfies "
            "(these are validated and a failure is rejected):"
        )
        for c in checks:
            cid = c.get("id", "check")
            sev = c.get("severity", "error")
            parts.append(f"- [{sev}] {_humanize_concern(cid)}")

    return "\n".join(parts) + "\n"
