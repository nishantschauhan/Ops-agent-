#!/usr/bin/env python3
"""Generates the fake SQLite database, ticket store, and policy knowledge base
used by the Lieferblick Internal Operations Agent demo.

Run once before starting the MCP servers or the agent:

    python seed_data.py            # skips files that already exist
    python seed_data.py --force    # regenerates everything from scratch
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.paths import data_dir

CUSTOMERS = [
    ("CUST-1001", "Anja Weber", "Weber Logistik GmbH", "a.weber@weberlogistik.de", "DE", "de", "gold"),
    ("CUST-1002", "Marco Rossi", "Rossi Trasporti SRL", "m.rossi@rossitrasporti.it", "IT", "en", "silver"),
    ("CUST-1003", "Sophie Dubois", "Dubois Freight SARL", "s.dubois@duboisfreight.fr", "FR", "en", "gold"),
    ("CUST-1004", "Jonas Becker", "Becker & Sohn Spedition", "j.becker@becker-sohn.de", "DE", "de", "platinum"),
    ("CUST-1005", "Lena Fischer", "Fischer Retail GmbH", "l.fischer@fischerretail.de", "DE", "de", "silver"),
    ("CUST-1006", "Tom van Dijk", "van Dijk Distributie BV", "t.vandijk@vandijk.nl", "NL", "en", "bronze"),
    ("CUST-1007", "Katarzyna Nowak", "Nowak Handel Sp. z o.o.", "k.nowak@nowakhandel.pl", "PL", "en", "silver"),
    ("CUST-1008", "Paul Hoffmann", "Hoffmann Baumaerkte", "p.hoffmann@hoffmann-bau.de", "DE", "de", "gold"),
]

CARRIERS = ["DPD", "DHL", "GLS", "Hermes", "UPS", "Deutsche Post"]
CITIES = ["Berlin", "Munich", "Hamburg", "Cologne", "Frankfurt", "Warsaw", "Amsterdam", "Milan", "Paris", "Lyon"]
STATUSES = ["pending", "processing", "in_transit", "delayed", "delivered", "damaged", "returned", "cancelled"]


def build_database(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            company TEXT NOT NULL,
            email TEXT NOT NULL,
            country TEXT NOT NULL,
            preferred_language TEXT NOT NULL,
            tier TEXT NOT NULL
        );
        CREATE TABLE shipments (
            shipment_id TEXT PRIMARY KEY,
            order_ref TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            status TEXT NOT NULL,
            carrier TEXT NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notes TEXT
        );
        """
    )
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", CUSTOMERS)

    now = datetime.now(timezone.utc)
    rng = random.Random(42)
    shipments = []
    for i in range(1, 41):
        customer = rng.choice(CUSTOMERS)
        created = now - timedelta(days=rng.randint(1, 60))
        updated = created + timedelta(days=rng.randint(0, 5))
        shipments.append(
            (
                f"SHP-{2000 + i}",
                f"ORD-{8850 + i}",
                customer[0],
                rng.choice(STATUSES),
                rng.choice(CARRIERS),
                rng.choice(CITIES),
                rng.choice(CITIES),
                created.isoformat(timespec="seconds"),
                updated.isoformat(timespec="seconds"),
                None,
            )
        )

    # Guaranteed demo record for the "summarize order 8890 across all systems" scenario.
    demo_created = now - timedelta(days=9)
    demo_updated = now - timedelta(days=1)
    shipments.append(
        (
            "SHP-8890",
            "ORD-8890",
            "CUST-1004",
            "damaged",
            "DPD",
            "Hamburg",
            "Munich",
            demo_created.isoformat(timespec="seconds"),
            demo_updated.isoformat(timespec="seconds"),
            "Customer reports crushed packaging on arrival; photos attached to ticket TCK-8890A1.",
        )
    )

    conn.executemany("INSERT INTO shipments VALUES (?,?,?,?,?,?,?,?,?,?)", shipments)
    conn.commit()
    conn.close()
    print(f"Seeded {len(CUSTOMERS)} customers and {len(shipments)} shipments -> {db_path}")


def build_tickets(tickets_path: Path) -> None:
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=1, hours=2)).isoformat(timespec="seconds")
    updated = (now - timedelta(hours=6)).isoformat(timespec="seconds")
    tickets = [
        {
            "ticket_id": "TCK-8890A1",
            "customer_id": "CUST-1004",
            "order_ref": "ORD-8890",
            "subject": "Damaged goods on arrival - order ORD-8890",
            "description": (
                "Customer Jonas Becker (Becker & Sohn Spedition) reports the shipment arrived "
                "with crushed packaging and visibly damaged contents. Requesting refund per "
                "damaged-goods policy."
            ),
            "status": "in_progress",
            "priority": "high",
            "assigned_to": "support-team-de@lieferblick.internal",
            "created_at": created,
            "updated_at": updated,
            "history": [
                {"at": created, "event": "created"},
                {"at": updated, "event": "status -> in_progress"},
                {"at": updated, "event": "assigned -> support-team-de@lieferblick.internal"},
            ],
        }
    ]
    tickets_path.write_text(json.dumps(tickets, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Seeded {len(tickets)} ticket(s) -> {tickets_path}")


POLICIES = [
    {
        "doc_id": "POL-001",
        "title": "Damaged Goods Refund Policy / Richtlinie fuer beschaedigte Ware",
        "category": "refunds",
        "source": "Lieferblick Policy Handbook, Section 4.2 (Damaged Goods)",
        "text": (
            "Customers reporting goods damaged in transit are entitled to a full refund or a "
            "reshipment at no additional cost, provided the claim is filed within 14 calendar days "
            "of the delivery date. Photographic evidence of the damaged packaging or contents must "
            "be attached to the support ticket before a refund can be approved.\n\n"
            "Refunds for damaged goods are processed within 5 business days of ticket approval. "
            "Support engineers should always create a ticket referencing the affected order number "
            "and attach carrier claim details when available.\n\n"
            "Kunden, die waehrend des Transports beschaedigte Ware melden, haben Anspruch auf volle "
            "Rueckerstattung oder kostenlosen Ersatzversand, sofern die Meldung innerhalb von 14 "
            "Kalendertagen nach Zustellung erfolgt. Fotografische Nachweise der beschaedigten "
            "Verpackung oder des Inhalts muessen dem Support-Ticket beigefuegt werden, bevor eine "
            "Rueckerstattung genehmigt werden kann."
        ),
    },
    {
        "doc_id": "POL-002",
        "title": "Standard SLA & Response Times / Standard-SLA und Reaktionszeiten",
        "category": "sla",
        "source": "Lieferblick Policy Handbook, Section 2.1 (Service Levels)",
        "text": (
            "Support tickets are triaged by priority. Urgent tickets (e.g. damaged goods, lost "
            "shipments above 500 EUR value) must receive a first response within 2 business hours. "
            "High-priority tickets must receive a first response within 8 business hours. Normal "
            "priority tickets must receive a first response within 1 business day. Low priority "
            "tickets must receive a first response within 3 business days.\n\n"
            "Gold and platinum tier customers receive first response within half the standard time "
            "for their ticket's priority level.\n\n"
            "Support-Tickets werden nach Prioritaet eingestuft. Dringende Tickets (z. B. beschaedigte "
            "Ware, verlorene Sendungen ueber 500 EUR Warenwert) muessen innerhalb von 2 "
            "Geschaeftsstunden eine erste Antwort erhalten."
        ),
    },
    {
        "doc_id": "POL-003",
        "title": "Lost Shipment Investigation Procedure",
        "category": "shipments",
        "source": "Lieferblick Policy Handbook, Section 4.5 (Lost Shipments)",
        "text": (
            "A shipment is classified as 'lost' if its tracking status has not updated for more than "
            "10 calendar days and the carrier confirms no scan activity. Support engineers must open "
            "a formal carrier claim before offering the customer a refund or reshipment, unless the "
            "customer is platinum tier, in which case a reshipment may be offered immediately while "
            "the carrier claim is pursued in parallel.\n\n"
            "All lost-shipment tickets must be escalated to the logistics operations lead if "
            "unresolved after 5 business days."
        ),
    },
    {
        "doc_id": "POL-004",
        "title": "Returns & Exchanges Policy / Rueckgabe- und Umtauschrichtlinie",
        "category": "returns",
        "source": "Lieferblick Policy Handbook, Section 5.1 (Returns)",
        "text": (
            "Customers may return unopened goods within 30 calendar days of delivery for a full "
            "refund, minus original shipping costs, unless the shipment arrived damaged or incorrect, "
            "in which case shipping costs are also refunded. Return shipping labels are provided "
            "free of charge to gold and platinum tier customers; all other tiers are responsible for "
            "return shipping costs.\n\n"
            "Kunden koennen unbenutzte Ware innerhalb von 30 Kalendertagen nach Lieferung gegen volle "
            "Rueckerstattung zurueckgeben, abzueglich der urspruenglichen Versandkosten."
        ),
    },
    {
        "doc_id": "POL-005",
        "title": "Customs & Cross-Border Duties Guidance",
        "category": "customs",
        "source": "Lieferblick Policy Handbook, Section 6.3 (Customs)",
        "text": (
            "Shipments crossing EU external borders may be subject to import VAT and customs duties, "
            "which are the responsibility of the recipient unless the order was placed under a "
            "delivered-duty-paid (DDP) contract. Support engineers should never promise a refund of "
            "customs charges without first confirming the shipment's Incoterm in the shipment notes."
        ),
    },
    {
        "doc_id": "POL-006",
        "title": "GDPR & Customer Data Handling / DSGVO und Umgang mit Kundendaten",
        "category": "privacy",
        "source": "Lieferblick Policy Handbook, Section 9.0 (Data Protection)",
        "text": (
            "All customer personal data (name, address, email, order history) is processed under "
            "GDPR Article 6(1)(b) as necessary for contract performance. Support engineers must never "
            "share a customer's personal data with a third party without written consent, and must "
            "never paste raw customer data into external tools that are not part of the approved "
            "support stack.\n\n"
            "Alle personenbezogenen Kundendaten werden gemaess Art. 6 Abs. 1 lit. b DSGVO "
            "verarbeitet, soweit dies fuer die Vertragserfuellung erforderlich ist."
        ),
    },
    {
        "doc_id": "POL-007",
        "title": "Ticket Escalation Procedure / Eskalationsverfahren",
        "category": "process",
        "source": "Lieferblick Policy Handbook, Section 3.4 (Escalations)",
        "text": (
            "A ticket should be escalated to a team lead when: (1) the customer is platinum tier and "
            "the ticket has been open for more than 24 hours, (2) the ticket involves a potential "
            "legal or safety issue, or (3) the customer explicitly requests escalation. Escalated "
            "tickets must be reassigned using the assign_ticket action and marked with priority "
            "'urgent'."
        ),
    },
    {
        "doc_id": "POL-008",
        "title": "Service Credit Policy for Delayed Shipments",
        "category": "sla",
        "source": "Lieferblick Policy Handbook, Section 4.7 (Delays)",
        "text": (
            "Shipments delayed by more than 3 business days past their originally quoted delivery "
            "window qualify the customer for a service credit equal to 10% of the shipment's "
            "declared value, up to a maximum of 100 EUR, applied to their next invoice. This is "
            "distinct from a refund and does not require the goods to be damaged or lost - delay "
            "alone is sufficient."
        ),
    },
]


def build_policies(policies_path: Path) -> None:
    policies_path.write_text(json.dumps(POLICIES, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Seeded {len(POLICIES)} polic{'y' if len(POLICIES) == 1 else 'ies'} -> {policies_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Overwrite existing seed data")
    args = parser.parse_args()

    d = data_dir()
    db_path = d / "lieferblick.db"
    tickets_path = d / "tickets.json"
    policies_path = d / "policies.json"

    if db_path.exists() and not args.force:
        print(f"{db_path} already exists, skipping (use --force to regenerate).")
    else:
        build_database(db_path)

    if tickets_path.exists() and not args.force:
        print(f"{tickets_path} already exists, skipping (use --force to regenerate).")
    else:
        build_tickets(tickets_path)

    if policies_path.exists() and not args.force:
        print(f"{policies_path} already exists, skipping (use --force to regenerate).")
    else:
        build_policies(policies_path)

    print("\nSeed complete.")


if __name__ == "__main__":
    main()
