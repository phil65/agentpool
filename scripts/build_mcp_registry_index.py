"""Build a LanceDB index of MCP registry servers for semantic search.

This script fetches all servers from the MCP registry (with pagination),
creates embeddings for their metadata, and stores them in a LanceDB database.

Usage:
    uv run scripts/build_mcp_registry_index.py [--output PATH]

The resulting database can be used for semantic search over MCP servers.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx
from pydantic import BaseModel


# Output path for the parquet file - ships with the package
DEFAULT_OUTPUT = (
    Path(__file__).parent.parent
    / "src"
    / "agentpool_toolsets"
    / "mcp_discovery"
    / "data"
    / "mcp_servers.parquet"
)

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


class MCPServerRecord(BaseModel):
    """Record for a single MCP server in the index."""

    name: str
    description: str
    version: str
    title: str | None = None
    website_url: str | None = None
    has_remote: bool = False
    remote_types: list[str] = []
    package_types: list[str] = []
    repository_url: str | None = None

    @property
    def search_text(self) -> str:
        """Combined text for embedding."""
        parts = [self.name, self.description]
        if self.title:
            parts.append(self.title)
        return " ".join(parts)


async def fetch_all_servers(client: httpx.AsyncClient) -> list[dict]:
    """Fetch all servers from registry with pagination."""
    all_servers = []
    cursor = None
    page = 0

    while True:
        params = {"cursor": cursor} if cursor else {}
        response = await client.get(REGISTRY_URL, params=params)
        response.raise_for_status()
        data = response.json()

        servers = data.get("servers", [])
        all_servers.extend(servers)

        page += 1
        print(f"  Page {page}: {len(servers)} servers (total: {len(all_servers)})")

        cursor = data.get("metadata", {}).get("nextCursor")
        if not cursor or not servers:
            break

    return all_servers


def parse_server(wrapper: dict) -> MCPServerRecord:
    """Parse a server wrapper into a record."""
    server = wrapper.get("server", {})

    # Extract remote types
    remotes = server.get("remotes", [])
    remote_types = [r.get("type", "") for r in remotes if r.get("type")]

    # Extract package types
    packages = server.get("packages", [])
    package_types = [p.get("registryType", "") for p in packages if p.get("registryType")]

    # Repository URL
    repo = server.get("repository", {})
    repo_url = repo.get("url") if repo else None

    return MCPServerRecord(
        name=server.get("name", ""),
        description=server.get("description", ""),
        version=server.get("version", ""),
        title=server.get("title"),
        website_url=server.get("websiteUrl"),
        has_remote=bool(remotes),
        remote_types=remote_types,
        package_types=package_types,
        repository_url=repo_url,
    )


async def main(output_path: Path) -> None:
    """Build the LanceDB index."""
    print("Fetching MCP registry servers...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        raw_servers = await fetch_all_servers(client)

    print(f"\nParsing {len(raw_servers)} servers...")
    records = []
    seen_names = set()  # Deduplicate by name

    for wrapper in raw_servers:
        try:
            record = parse_server(wrapper)
            if record.name and record.name not in seen_names:
                seen_names.add(record.name)
                records.append(record)
        except Exception as e:  # noqa: BLE001
            print(f"  Warning: Failed to parse server: {e}")

    print(f"Parsed {len(records)} unique servers")

    # Stats
    with_remotes = sum(1 for r in records if r.has_remote)
    print(f"  - With remote endpoints: {with_remotes}")
    print(f"  - Stdio only: {len(records) - with_remotes}")

    # Create embeddings and save as parquet
    print(f"\nCreating parquet index at {output_path}...")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load FastEmbed model
    from fastembed import TextEmbedding

    print("Loading embedding model...")
    model = TextEmbedding("BAAI/bge-small-en-v1.5")

    # Prepare data
    data = []
    texts = [r.search_text for r in records]

    # Generate embeddings in batches
    print("Generating embeddings...")
    embeddings = list(model.embed(texts))

    for r, embedding in zip(records, embeddings, strict=False):
        data.append({
            "name": r.name,
            "description": r.description,
            "version": r.version,
            "title": r.title or "",
            "website_url": r.website_url or "",
            "has_remote": r.has_remote,
            "remote_types": ",".join(r.remote_types),
            "package_types": ",".join(r.package_types),
            "repository_url": r.repository_url or "",
            "text": r.search_text,
            "vector": embedding.tolist(),
        })

    # Write to parquet
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(data)
    pq.write_table(table, output_path)

    print(f"\nDone! Index created at {output_path}")
    print(f"Total records: {len(data)}")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")

    # Quick test - load into LanceDB and search
    print("\nTest search for 'github':")
    import tempfile

    import lancedb

    with tempfile.TemporaryDirectory() as tmpdir:
        db = lancedb.connect(tmpdir)
        test_table = db.create_table("servers", table)
        query_vec = next(iter(model.embed(["github repository issues"]))).tolist()
        results = test_table.search(query_vec).limit(3).to_arrow()
        for i, results in enumerate(results):
            name = results["name"][i].as_py()
            desc = results["description"][i].as_py()[:60]
            print(f"  - {name}: {desc}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build MCP registry parquet index")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path for parquet (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    asyncio.run(main(args.output))
