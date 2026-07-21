# System X Whitepaper

System X is a distributed storage-oriented analytics substrate designed for mixed object and event workloads.

## Benchmark snapshot

- Peak write throughput: 420k ops/s in 12-node benchmark
- P99 write latency: 18 ms
- Sustained mixed read/write workload remains stable below 12 nodes

## Architecture notes

- Metadata compaction runs every 20 minutes
- Cross-region write replication is technically available but not recommended for latency-sensitive workloads
- Best suited for durable ingest with large cold-to-warm datasets

## Hardware profile

- Recommended per node: 64GB RAM
- NVMe local cache strongly recommended
- 25GbE network assumed in published benchmark

## Best-fit scenarios

- Large-scale archival ingestion
- Durable event retention
- Analytics where write durability is more important than sub-5ms response
