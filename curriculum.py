"""12-week curriculum: Cloud-agnostic Data Architect, Azure/Databricks/Snowflake hands-on.

Mon-Fri  -> one lesson per day (5 per week, 60 total)
Saturday -> weekly assessment (weeks 4, 8, 12: architecture design challenge instead)
Sunday   -> revision of weak areas
"""

WEEKS = [
    {
        "title": "Relational foundations",
        "lessons": [
            "Conceptual vs logical vs physical models; reading and drawing ER diagrams (Chen vs crow's foot)",
            "Keys: natural, surrogate, composite, alternate; choosing primary keys in OLTP and in the lakehouse",
            "Cardinality and relationships: 1:1, 1:N, M:N, bridge/associative tables, optional vs mandatory",
            "Normalization 1NF-3NF and BCNF with a messy real-world order spreadsheet",
            "When to denormalize: read vs write workloads, OLTP vs OLAP, and the cost of each choice",
        ],
    },
    {
        "title": "Constraints, integrity and modeling patterns",
        "lessons": [
            "Constraints in practice: PK/FK/UNIQUE/CHECK; which ones Snowflake, Databricks (Delta) and BigQuery actually enforce",
            "Modeling hierarchies: adjacency list, path enumeration, closure tables, ragged and recursive hierarchies",
            "Supertype/subtype patterns and handling polymorphic entities",
            "Temporal data: valid time vs transaction time, bitemporal modeling",
            "Reverse-engineering an existing OLTP schema and spotting design smells",
        ],
    },
    {
        "title": "Dimensional modeling (Kimball) I",
        "lessons": [
            "Kimball's 4-step design process: business process, grain, dimensions, facts",
            "Fact table types: transaction, periodic snapshot, accumulating snapshot, factless facts",
            "Measures: additive, semi-additive, non-additive; designing for correct aggregation",
            "Conformed dimensions and the enterprise bus matrix",
            "Date and time dimensions, role-playing dimensions, degenerate dimensions",
        ],
    },
    {
        "title": "Dimensional modeling (Kimball) II",
        "lessons": [
            "Slowly Changing Dimensions types 0,1,2,3,4,6 with SQL and Delta MERGE implementations",
            "Junk dimensions, mini-dimensions, outriggers and dimension explosion",
            "Many-to-many in star schemas: bridge tables, weighting factors, multivalued dimensions",
            "Late-arriving facts and dimensions, inferred members, unknown-member rows",
            "Star vs snowflake vs One Big Table (OBT): trade-offs on modern columnar engines",
        ],
    },
    {
        "title": "Data Vault 2.0",
        "lessons": [
            "Why Data Vault: auditability, agility, parallel loading; when NOT to use it",
            "Hubs, links and satellites; business keys and hash keys vs sequence keys",
            "Satellite design: splitting by rate of change and source; multi-active and effectivity satellites",
            "Business Vault: PIT tables, bridge tables, computed satellites",
            "From Raw Vault to information marts (star schemas on top of the vault)",
        ],
    },
    {
        "title": "Change data and incremental modeling",
        "lessons": [
            "CDC patterns: log-based vs query-based vs triggers; Debezium, ADF/Fabric mirroring, Snowflake streams, Delta CDF",
            "Idempotent incremental loads: watermarks, MERGE, deduplication, late data",
            "Historization in the lakehouse: SCD2 with Delta MERGE, APPLY CHANGES / AUTO CDC in Databricks",
            "Snapshot vs event-sourced modeling; append-only logs and reconstructing state",
            "Backfills, reprocessing and schema evolution without breaking consumers",
        ],
    },
    {
        "title": "Lakehouse and Medallion architecture",
        "lessons": [
            "Lakehouse fundamentals: open table formats (Delta, Iceberg, Hudi) and what ACID on object storage means",
            "Medallion (bronze/silver/gold): what belongs in each layer and common anti-patterns",
            "Where modeling happens: Data Vault in silver vs Kimball in gold vs OBT; mapping methods to layers",
            "Physical design: partitioning, clustering, liquid clustering, Z-order, file sizing, Snowflake micro-partitions",
            "Cross-cloud equivalents: Databricks/Fabric/Synapse vs AWS (Glue, Redshift, S3 Tables) vs GCP (BigQuery, BigLake)",
        ],
    },
    {
        "title": "Transformation and semantic layers",
        "lessons": [
            "dbt modeling conventions: staging, intermediate, marts; tests, contracts and versions",
            "Metrics and semantic layers: dbt Semantic Layer/MetricFlow, Databricks metric views, Power BI/Fabric semantic models",
            "Modeling for BI performance: aggregates, materialized views, star schemas for Power BI",
            "Wide tables and feature tables for ML; modeling for AI/RAG use cases",
            "Activity schema and other emerging patterns: what's proven vs experimental",
        ],
    },
    {
        "title": "Governance, quality and contracts",
        "lessons": [
            "Data contracts: schema, semantics, SLAs; producer vs consumer ownership",
            "Data quality: dimensions of quality, expectations (DLT/Lakeflow, Great Expectations, dbt tests, Soda)",
            "Lineage and catalogs: Unity Catalog, Microsoft Purview, OpenLineage, AWS/GCP equivalents",
            "Master data and reference data management; golden records and entity resolution",
            "Metadata-driven pipelines and naming standards that scale across teams",
        ],
    },
    {
        "title": "Security, privacy and cost",
        "lessons": [
            "Security modeling: RBAC vs ABAC, row-level security, column masking, tags",
            "PII and privacy: GDPR/DPDP Act right-to-erasure in append-only/SCD2 tables, tokenization",
            "Cost-aware modeling: storage vs compute trade-offs, Snowflake credits, Databricks DBUs, BigQuery slots",
            "Performance trade-offs: denormalization, clustering and caching vs maintainability",
            "Multi-tenant and multi-region data models",
        ],
    },
    {
        "title": "Architecture at scale",
        "lessons": [
            "Data mesh vs centralized platform vs hub-and-spoke: organizational and modeling consequences",
            "Streaming data models: event design, keys, ordering, windowing, Kappa vs Lambda",
            "Choosing a modeling approach: decision framework for 3NF vs Kimball vs Data Vault vs OBT",
            "Architecture Decision Records (ADRs): documenting and defending trade-offs",
            "Reading a reference architecture critically (Azure, AWS, GCP well-architected guidance)",
        ],
    },
    {
        "title": "Capstone: enterprise data platform",
        "lessons": [
            "Capstone part 1: requirements and source analysis for a retail/fintech company",
            "Capstone part 2: conceptual and logical model, bus matrix",
            "Capstone part 3: layer design (bronze/silver/gold), Vault vs Kimball choice, CDC strategy",
            "Capstone part 4: governance, security, quality, cost plan",
            "Capstone part 5: write ADRs and defend the design to a mock architecture review board",
        ],
    },
]

TOTAL_LESSONS = sum(len(w["lessons"]) for w in WEEKS)
CHALLENGE_WEEKS = {4, 8, 12}  # 1-based week numbers that end in an architecture challenge


def lesson_at(index: int):
    """Return (week_number, day_in_week, week_title, lesson_topic) for a 0-based lesson index, or None if done."""
    if index >= TOTAL_LESSONS:
        return None
    i = index
    for w, week in enumerate(WEEKS, start=1):
        if i < len(week["lessons"]):
            return w, i + 1, week["title"], week["lessons"][i]
        i -= len(week["lessons"])
    return None


def week_of(index: int) -> int:
    """Week number (1-based) the lesson index belongs to; past the end returns the last week."""
    found = lesson_at(min(index, TOTAL_LESSONS - 1))
    return found[0]


def roadmap_text() -> str:
    lines = ["12-week roadmap (Mon-Fri lessons, Sat assessment, Sun revision):"]
    for w, week in enumerate(WEEKS, start=1):
        tag = "  + architecture challenge" if w in CHALLENGE_WEEKS else ""
        lines.append(f"Week {w}: {week['title']}{tag}")
    return "\n".join(lines)
