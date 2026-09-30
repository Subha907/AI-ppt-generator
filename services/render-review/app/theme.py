"""Visual theme: colors and fonts aligned with AWS diagram conventions.

Grounded in AWS's own guidance:
  * AWS Architecture Group Icons (Feb 2025) - container/group border colors and
    the labeled title strip used for AWS Cloud / Region / VPC / AZ / subnets.
  * AWS Networking Best Practices "Conventions" - white background, no outer
    border/shadow, >=1pt lines, short centered labels, Regular-weight fonts,
    label color #16191F, >=12px, contrast >= 4.5:1.

Shared by the python-pptx compiler and the Pillow preview so the .pptx and the
preview image look identical. Values are hex RGB strings.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContainerStyle:
    border: str            # group border color
    title_fill: str        # color of the small label strip at the top-left
    title_text: str        # label text color (on the strip)
    fill: str              # interior fill (white / very light tint)
    dashed: bool = False   # AWS draws Region + AZ groups with dashed borders
    border_width: float = 1.5  # pt


@dataclass(frozen=True)
class LeafStyle:
    fill: str
    border: str
    text: str


# --- Container color matrix (Ruleset v1.0 section 4) -----------------------
# Exact hex values, border weights, and line styles per the deployment ruleset.
CONTAINER_STYLES: dict[str, ContainerStyle] = {
    # AWS Account: transparent fill, solid 1.5pt, AWS Charcoal.
    "ACCOUNT": ContainerStyle(border="232F3E", title_fill="232F3E",
                              title_text="FFFFFF", fill="FFFFFF",
                              dashed=False, border_width=1.5),
    # Region: transparent, dashed 1.5pt, AWS Blue.
    "REGION": ContainerStyle(border="147EB3", title_fill="147EB3",
                             title_text="FFFFFF", fill="FFFFFF",
                             dashed=True, border_width=1.5),
    # VPC: transparent, solid 2.0pt, AWS Orange.
    "VPC": ContainerStyle(border="FF9900", title_fill="FF9900",
                          title_text="FFFFFF", fill="FFFFFF",
                          dashed=False, border_width=2.0),
    # Availability Zone: dashed 1.5pt (blue-ish), light interior.
    "AVAILABILITYZONE": ContainerStyle(border="147EB3", title_fill="147EB3",
                                       title_text="FFFFFF", fill="FFFFFF",
                                       dashed=True, border_width=1.5),
    # Base subnet (defaults to private styling); public/private/data chosen by
    # label keyword via container_style().
    "SUBNET": ContainerStyle(border="0073BB", title_fill="0073BB",
                             title_text="FFFFFF", fill="F2F8FC",
                             dashed=True, border_width=1.0),
    # Services general groups: tinted gray, solid thin 1.0pt, #79879C.
    "CONTAINER": ContainerStyle(border="79879C", title_fill="79879C",
                                title_text="FFFFFF", fill="F7F8FA",
                                dashed=False, border_width=1.0),
}

# Subnet tiers (Ruleset section 4) - chosen by label keyword in container_style.
# Public = tinted green #00A152; Private = tinted blue #0073BB;
# Isolated Data = tinted red/orange #D13212. All thin dashed 1.0pt.
SUBNET_PUBLIC = ContainerStyle(border="00A152", title_fill="00A152",
                               title_text="FFFFFF", fill="F0FAF4",
                               dashed=True, border_width=1.0)
SUBNET_PRIVATE = ContainerStyle(border="0073BB", title_fill="0073BB",
                                title_text="FFFFFF", fill="F2F8FC",
                                dashed=True, border_width=1.0)
SUBNET_DATA = ContainerStyle(border="D13212", title_fill="D13212",
                             title_text="FFFFFF", fill="FDF3F0",
                             dashed=True, border_width=1.0)

# --- Service icon (leaf) styles by category --------------------------------
# Fills use AWS category colors; text is white for contrast (>= 4.5:1).
# Fills use the TRUE AWS architecture-icon category colors so the legend
# swatches match the real PNG icons on the slide (remediation Issue 4):
#   Compute = Smile orange #ED7100, Database = Nebula blue #527FFF,
#   Storage = Endor green #7AA116, Networking = Loft purple #8C4FFF,
#   Security = Mars red #DD344C.
LEAF_STYLES: dict[str, LeafStyle] = {
    "COMPUTE": LeafStyle(fill="ED7100", border="C25C00", text="FFFFFF"),
    "DATABASE": LeafStyle(fill="527FFF", border="3A5FD9", text="FFFFFF"),
    "STORAGE": LeafStyle(fill="7AA116", border="587C10", text="FFFFFF"),
    "NETWORK": LeafStyle(fill="8C4FFF", border="6B2FD6", text="FFFFFF"),
    "INTEGRATION": LeafStyle(fill="E7157B", border="B50F60", text="FFFFFF"),
    "EDGE": LeafStyle(fill="8C4FFF", border="6B2FD6", text="FFFFFF"),
    "SECURITY": LeafStyle(fill="DD344C", border="AD2539", text="FFFFFF"),
    "OBSERVABILITY": LeafStyle(fill="E7157B", border="B50F60", text="FFFFFF"),
    "EXTERNAL": LeafStyle(fill="7D8998", border="5A6570", text="FFFFFF"),
    "ACTOR": LeafStyle(fill="232F3E", border="000000", text="FFFFFF"),
    "SERVICE": LeafStyle(fill="146EB4", border="0E4F82", text="FFFFFF"),
    "COMPONENT": LeafStyle(fill="146EB4", border="0E4F82", text="FFFFFF"),
    "PROCESS": LeafStyle(fill="146EB4", border="0E4F82", text="FFFFFF"),
    "CAPABILITY": LeafStyle(fill="146EB4", border="0E4F82", text="FFFFFF"),
    "DATASTORE": LeafStyle(fill="7AA116", border="587C10", text="FFFFFF"),
}

DEFAULT_LEAF = LeafStyle(fill="146EB4", border="0E4F82", text="FFFFFF")
DEFAULT_CONTAINER = CONTAINER_STYLES["CONTAINER"]

# AWS convention: labels on the background use #16191F; lines use a neutral gray.
TITLE_COLOR = "16191F"
CONNECTOR_COLOR = "7D8998"
BACKGROUND = "FFFFFF"

FONT_NAME = "Arial"


# --- Connector functional styles (Ruleset section 7) -----------------------
@dataclass(frozen=True)
class ConnectorStyle:
    color: str
    width: float   # pt
    dash: str      # "solid" | "dash" | "dot"


CONNECTOR_STYLES: dict[str, ConnectorStyle] = {
    # Operation / request / forward pipelines.
    "request": ConnectorStyle(color="232F3E", width=1.5, dash="solid"),
    # Cross-AZ DB sync / replication.
    "replication": ConnectorStyle(color="FF9900", width=1.5, dash="dash"),
    # Security / encryption / token authorizations.
    "security": ConnectorStyle(color="79879C", width=1.0, dash="dot"),
}


def connector_style(edge_class: str) -> ConnectorStyle:
    return CONNECTOR_STYLES.get(edge_class, CONNECTOR_STYLES["request"])


def container_style(node_type: str, label: str = "") -> ContainerStyle:
    if node_type == "SUBNET":
        low = label.lower()
        if "public" in low:
            return SUBNET_PUBLIC
        if any(k in low for k in ("data", "database", "isolated", "db")):
            return SUBNET_DATA
        if "private" in low:
            return SUBNET_PRIVATE
        return SUBNET_PRIVATE  # default subnet styling
    return CONTAINER_STYLES.get(node_type, DEFAULT_CONTAINER)


def leaf_style(node_type: str) -> LeafStyle:
    return LEAF_STYLES.get(node_type, DEFAULT_LEAF)


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    hex_str = hex_str.lstrip("#")
    return (
        int(hex_str[0:2], 16),
        int(hex_str[2:4], 16),
        int(hex_str[4:6], 16),
    )
