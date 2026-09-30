"""Generate a realistic sample RFP PDF for testing the ingestion pipeline.

Produces a multi-page, text-rich RFP document that the Bedrock Knowledge Base
can chunk and embed meaningfully (unlike the tiny placeholder used in the smoke
test). Output: sample_rfp.pdf in the service root.

Usage:
    python -m scripts.make_sample_rfp
    python -m scripts.make_sample_rfp --out C:\\path\\to\\my_rfp.pdf
"""
from __future__ import annotations

import argparse
import pathlib

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

DEFAULT_OUT = pathlib.Path(__file__).resolve().parents[1] / "sample_rfp.pdf"


def build(out_path: pathlib.Path) -> None:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Body", parent=styles["Normal"], alignment=TA_JUSTIFY, spaceAfter=8,
            leading=15,
        )
    )
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    body = styles["Body"]

    def bullets(items):
        return ListFlowable(
            [ListItem(Paragraph(t, body), leftIndent=12) for t in items],
            bulletType="bullet",
            start="circle",
        )

    story = []
    story.append(Paragraph("Request for Proposal (RFP)", styles["Title"]))
    story.append(
        Paragraph(
            "Cloud Modernization of the GIA Customer Servicing Platform", h2
        )
    )
    story.append(Paragraph("RFP Reference: GIA-2026-CLOUD-014", body))
    story.append(Paragraph("Issued: September 2026", body))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("1. Background and Objective", h1))
    story.append(
        Paragraph(
            "Global Insurance Associates (GIA) operates a legacy on-premises "
            "customer servicing platform that handles policy administration, "
            "claims intake, and customer self-service. The current system runs on "
            "aging virtual machines in a single data center and cannot meet peak "
            "seasonal demand. GIA seeks a partner to design and implement a "
            "modernized, cloud-native platform on Amazon Web Services (AWS). The "
            "solution must improve availability, elasticity, security posture, and "
            "developer velocity while controlling operating cost.",
            body,
        )
    )

    story.append(Paragraph("2. Scope of Work", h1))
    story.append(
        Paragraph(
            "The selected vendor is expected to deliver a target architecture and a "
            "phased migration plan covering the following capabilities:",
            body,
        )
    )
    story.append(
        bullets(
            [
                "A public-facing web and mobile self-service portal for policyholders.",
                "A policy administration service and a claims intake service, exposed "
                "through well-defined APIs.",
                "An event-driven integration layer connecting servicing, billing, and "
                "notification workflows.",
                "A data platform for operational reporting and near-real-time analytics.",
                "Identity, authentication, and fine-grained authorization for internal "
                "and external users.",
            ]
        )
    )

    story.append(Paragraph("3. Functional Requirements", h1))
    story.append(
        bullets(
            [
                "Policyholders can view policies, download documents, and submit claims "
                "online with file attachments.",
                "Claims submitted online must be validated, persisted durably, and "
                "routed to the appropriate processing queue.",
                "Customer service representatives can search and update policy records "
                "with a full audit trail.",
                "The platform must send email and SMS notifications for claim status "
                "changes.",
                "Reporting users can run daily and monthly operational reports without "
                "impacting transactional workloads.",
            ]
        )
    )

    story.append(Paragraph("4. Non-Functional Requirements", h1))
    story.append(
        bullets(
            [
                "Availability target of 99.95% for customer-facing services, with no "
                "single point of failure.",
                "The architecture must span multiple Availability Zones and support a "
                "documented disaster-recovery approach.",
                "Elastic scaling to handle a 5x traffic increase during open-enrollment "
                "periods.",
                "All data encrypted at rest and in transit; secrets managed centrally "
                "and never stored in code.",
                "End-to-end request tracing, centralized logging, and actionable alarms "
                "for each major component.",
                "Infrastructure defined as code and deployed through an automated "
                "pipeline.",
            ]
        )
    )

    story.append(Paragraph("5. Security and Compliance", h1))
    story.append(
        Paragraph(
            "GIA handles personally identifiable information and is subject to "
            "regulatory audit. The solution must enforce least-privilege access, "
            "network segmentation between public and private tiers, and continuous "
            "monitoring. Public entry points should be protected by a web "
            "application firewall and a content delivery network. Administrative "
            "access must require multi-factor authentication.",
            body,
        )
    )

    story.append(Paragraph("6. Preferred AWS Building Blocks", h1))
    story.append(
        Paragraph(
            "GIA has an existing AWS footprint and prefers managed services to reduce "
            "operational overhead. Vendors may propose the most appropriate services, "
            "but the following are considered a good baseline:",
            body,
        )
    )
    story.append(
        bullets(
            [
                "Containerized microservices running on a managed container platform, "
                "fronted by an application load balancer.",
                "A managed relational database for policy and claims data, with "
                "read replicas for reporting.",
                "Object storage for uploaded claim documents and static web assets.",
                "A managed message queue and event bus for asynchronous integration.",
                "A content delivery network and web application firewall in front of "
                "the public portal.",
                "Centralized identity, secrets management, encryption keys, monitoring, "
                "and log aggregation.",
            ]
        )
    )

    story.append(Paragraph("7. Deliverables", h1))
    story.append(
        bullets(
            [
                "A target Business, Application, and AWS Deployment architecture.",
                "A phased migration roadmap with milestones and rollback strategy.",
                "Infrastructure-as-code templates and a CI/CD pipeline.",
                "An operational runbook covering monitoring, scaling, and incident "
                "response.",
            ]
        )
    )

    story.append(Paragraph("8. Evaluation Criteria", h1))
    story.append(
        Paragraph(
            "Proposals will be evaluated on architectural soundness, security, "
            "resilience, total cost of ownership, delivery approach, and relevant "
            "experience. Vendors should clearly state assumptions and highlight any "
            "trade-offs in their proposed design.",
            body,
        )
    )

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        title="RFP - GIA Cloud Modernization",
        author="Global Insurance Associates",
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
    )
    doc.build(story)
    size = out_path.stat().st_size
    print(f"Wrote {out_path} ({size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    build(pathlib.Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
