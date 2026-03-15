#!/usr/bin/env python3
import requests
import time

BASE_URL = "http://localhost:8000"
MODEL = "claude-opus-4-6"

# Test 1: Short context (1k tokens)
short_context = """
The quick brown fox jumps over the lazy dog. " + "This sentence contains every letter of the alphabet. " * 30

Question: Does this text contain all letters of the alphabet?
"""

# Test 2: Medium context (4k tokens) - technical documentation
medium_context = """Distributed Systems Guide

1. Introduction
A distributed system is a collection of independent computers that appear to users as a single coherent system.
Key characteristics include resource sharing, openness, concurrency, scalability, and fault tolerance.

2. CAP Theorem
The CAP theorem states that a distributed data store can only guarantee two of three properties:
- Consistency: All nodes have the same data at the same time
- Availability: Every request receives a response
- Partition tolerance: System continues to work despite network failures

Trade-offs:
- CP systems: Sacrifice availability for consistency (HBase, MongoDB)
- AP systems: Sacrifice consistency for availability (Cassandra, DynamoDB)

3. Consistency Models

Strong Consistency:
All nodes see the same data at the same time. After an update, all subsequent reads return the updated value.
Implementation: Synchronous replication, two-phase commit, Paxos, Raft.

Eventual Consistency:
Nodes may temporarily have different data versions but will eventually converge.
Used in: DNS systems, Amazon Dynamo, Cassandra.
Trade-offs: Higher availability, better partition tolerance, but temporary inconsistencies.

Causal Consistency:
If process A communicates with B, A's operations are seen by B in order.
Captures happens-before relationships.

4. Consensus Algorithms

Paxos:
Family of protocols for consensus in unreliable networks.
Roles: Proposers suggest values, Acceptors choose values, Learners learn chosen values.
Phases: Prepare, Accept, Learn.

Raft:
Designed to be more understandable than Paxos.
Components: Leader election, Log replication, Safety.
Uses strong leader model.

5. Distributed Transactions

Two-Phase Commit (2PC):
Phase 1: Coordinator asks participants to prepare
Phase 2: If all agree, commit; otherwise abort
Problem: Blocking if coordinator fails

Three-Phase Commit (3PC):
Adds pre-commit phase to reduce blocking
Phases: CanCommit, PreCommit, DoCommit

Saga Pattern:
For long-running transactions
Sequence of local transactions with compensating operations
Types: Choreography (services react to events), Orchestration (central coordinator)

6. Distributed Storage

Distributed Hash Tables (DHT):
Provide key-value storage across many nodes
Examples: Chord, Kademlia, Pastry
Properties: O(log n) lookups, decentralized, fault-tolerant

Distributed File Systems:
GFS, HDFS, Ceph characteristics:
Large files across multiple machines
Replication for fault tolerance
Optimized for batch processing

7. Load Balancing

Algorithms:
- Round Robin: Sequential distribution
- Least Connections: Fewest active connections
- IP Hash: Route based on client IP
- Weighted: Different capacities per server

Service Discovery:
- DNS-based: Simple but limited
- Zookeeper: Distributed coordination
- Consul: Service mesh with health checks
- etcd: Configuration and discovery

Circuit Breakers:
Prevent cascade failures
Monitor failure rates, open circuit when threshold exceeded
Fail fast, periodically test recovery

8. Monitoring

RED Metrics:
- Request rate
- Error rate
- Duration

USE Method:
- Utilization
- Saturation
- Errors

Distributed Tracing:
Follow requests across service boundaries
Tools: OpenTelemetry, Jaeger, Zipkin

9. Security

Authentication:
- OAuth 2.0: Token-based authorization
- OpenID Connect: Identity layer
- Mutual TLS: Certificate-based
- JWT: JSON Web Tokens

Encryption:
- TLS/SSL for transport
- End-to-end encryption for messages
- Key management with HSMs

10. Cloud-Native Patterns

Microservices:
Small, independently deployable services
Benefits: Single responsibility, independent scaling, technology diversity
Challenges: Distributed complexity, data consistency

Containers and Orchestration:
Docker for containerization
Kubernetes for orchestration
Service mesh: Istio, Linkerd

Serverless:
Function as a Service (FaaS)
Event-driven, auto-scaling, pay-per-use
Challenge: Cold starts

Infrastructure as Code:
Terraform: Multi-cloud provisioning
CloudFormation: AWS-specific
Pulumi: Programming language-based
Ansible: Configuration management

QUESTION: Based on this guide, explain the trade-offs between strong consistency and eventual consistency, and provide specific use cases where each would be appropriate. Also explain how the CAP theorem relates to this decision.
""" * 4  # Repeat to get ~4k tokens

# Test 3: Long context (8k+ tokens)  
long_context = medium_context * 2

print("🧪 Testing Context Handling for opus-4.6\n")
print("="*80)

def test_context(name, context, timeout=180):
    print(f"\n📋 {name}")
    print(f"   Context size: ~{len(context)} chars (~{len(context)//4} tokens)")
    
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": context}],
        "stream": False
    }
    
    start = time.time()
    try:
        resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=timeout)
        elapsed = time.time() - start
        
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            
            # Check for understanding
            key_terms = ["CAP", "consistency", "availability", "partition", "trade"]
            found = sum(1 for term in key_terms if term.lower() in content.lower())
            
            print(f"   ✅ Success! Time: {elapsed:.1f}s")
            print(f"   📝 Response: {len(content)} chars")
            print(f"   🎯 Understanding: {found}/{len(key_terms)} key terms")
            print(f"   Preview: {content[:200]}...")
            return True
        else:
            print(f"   ❌ Failed: HTTP {resp.status_code}")
            print(f"   Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"   ❌ Error: {str(e)[:100]}")
        return False

# Run tests
results = []

# Test 1: Short
results.append(("Short Context (~1k tokens)", test_context("Test 1: Short Context", short_context, 120)))
time.sleep(1)

# Test 2: Medium
results.append(("Medium Context (~4k tokens)", test_context("Test 2: Medium Context", medium_context, 180)))
time.sleep(1)

# Test 3: Long
results.append(("Long Context (~8k tokens)", test_context("Test 3: Long Context", long_context, 240)))

print("\n" + "="*80)
print("\n📊 CONTEXT TEST SUMMARY:")
for name, passed in results:
    status = "✅ PASSED" if passed else "❌ FAILED"
    print(f"  {status}: {name}")

print("\n" + "="*80)
