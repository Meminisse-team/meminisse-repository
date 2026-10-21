# Gateway architecture

The service layer accesses persistence and object storage through gateway contracts. Runtime assembly selects the implementation with `GATEWAY_BACKEND`.

| Module in `backend/app/gateways/` | Responsibility |
| --- | --- |
| `interfaces.py` | Domain-facing persistence and storage contracts |
| `dto.py` | Infrastructure-neutral transfer objects |
| `sqlalchemy_gateways.py` | PostgreSQL implementation using SQLAlchemy and pgvector |
| `s3_gateway.py` | Private object storage and short-lived presigned access |
| `mock/` | In-memory implementation for offline tests and demos |
| `factory.py` | Runtime dependency assembly, session lifecycle and commit boundary |

`GATEWAY_BACKEND=postgres` assembles PostgreSQL and S3 implementations. `GATEWAY_BACKEND=mock` assembles in-memory implementations without DB or S3 credentials; it does not provide durable storage.

Media and manuscripts persist object keys, not public URLs. Authorized API responses issue short-lived presigned GET URLs. Bucket configuration and migration requirements are documented in [Security](security.md).

Upstage query and passage embeddings use 4096 dimensions, verified by an API response and reflected in `app/models/base.py`. The current vector column uses sequential similarity search; see migration `002_event_first_class_object.py` for index constraints.

Provider references: [Upstage official documentation](https://console.upstage.ai/docs) and [Azure Image Analysis](https://learn.microsoft.com/azure/ai-services/computer-vision/overview-image-analysis).
