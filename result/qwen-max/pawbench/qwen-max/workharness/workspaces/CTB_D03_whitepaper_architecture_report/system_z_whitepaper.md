# System Z Whitepaper

System Z is a graph-query-optimized database intended for relationship-heavy exploration workloads.

## Benchmark snapshot

- Peak write throughput: 160k ops/s
- Traversal latency: 7 ms on graph-heavy read queries
- Write-path degradation appears beyond 6 shards because edge reordering cost rises

## Architecture notes

- Excellent for multi-hop graph traversal
- High-concurrency write-heavy workloads trigger edge rebalancing overhead
- Not recommended as the primary engine for real-time write-dense analytics

## Hardware profile

- Recommended per node: 128GB RAM
- High IOPS SSD required for adjacency rebuild
- CPU profile favors graph query acceleration over bulk write ingest

## Best-fit scenarios

- Knowledge graph exploration
- Recommendation path discovery
- Investigative workloads where graph traversal matters more than write concurrency
