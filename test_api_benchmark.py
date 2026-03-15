#!/usr/bin/env python3
"""
API Benchmark Test for Emergent.sh OpenAI-compatible Server
Tests opus-4.6 model for context handling and speed
"""

import requests
import time
import json
import statistics
from typing import List, Dict, Any
from datetime import datetime

# API Configuration
BASE_URL = "http://localhost:8000"
API_KEY = "sk-test"  # The server doesn't validate this currently
MODEL = "claude-opus-4-6"

# Test prompts of varying lengths
LONG_CONTEXT_PROMPT = """
Please analyze the following technical documentation and answer the question at the end.

# Distributed Systems Architecture Guide

## 1. Introduction to Distributed Systems

A distributed system is a collection of independent computers that appear to the users of the system as a single coherent system. Distributed systems are everywhere. The Internet is a distributed system. So is the World Wide Web. So are corporate intranets. Distributed systems are useful because they provide:

- Resource sharing: Users can access remote resources (printers, files, databases) as if they were local.
- Openness: Systems can be built using components from different vendors.
- Concurrency: Multiple users can access shared resources simultaneously.
- Scalability: Systems can be expanded by adding more resources.
- Fault tolerance: Systems can continue operating even when some components fail.

## 2. Communication in Distributed Systems

Communication in distributed systems is typically handled through message passing. The most common paradigms include:

### 2.1 Remote Procedure Call (RPC)
RPC allows a program to call a procedure on a remote machine as if it were local. The caller is blocked until the remote procedure returns. Key challenges:
- Parameter marshalling and unmarshalling
- Handling network failures
- Dealing with different data representations

### 2.2 Message-Oriented Middleware (MOM)
MOM provides asynchronous communication through message queues. Producers send messages to queues; consumers receive them. Benefits:
- Decoupling of sender and receiver
- Guaranteed message delivery
- Load balancing through multiple consumers

### 2.3 Publish-Subscribe Systems
In pub-sub systems, publishers send messages to topics without knowing who will receive them. Subscribers express interest in topics and receive all relevant messages. This enables:
- One-to-many communication
- Dynamic system topology
- Scalable event dissemination

## 3. Consistency Models

### 3.1 Strong Consistency
All nodes see the same data at the same time. After an update completes, all subsequent reads return the updated value. Implementation:
- Synchronous replication
- Two-phase commit protocols
- Paxos and Raft consensus algorithms

### 3.2 Eventual Consistency
Nodes may temporarily have different versions of data, but will eventually converge to the same value. Used in:
- DNS systems
- Amazon Dynamo
- Cassandra

Trade-offs:
- Higher availability
- Better partition tolerance
- Potential temporary inconsistencies

### 3.3 Causal Consistency
If process A communicates with process B, then A's operations are seen by B in order. Concurrent operations may be seen in different orders by different processes. This model:
- Captures happens-before relationships
- More intuitive than eventual consistency
- Implemented in systems like COPS and ChainReaction

## 4. Consensus Algorithms

### 4.1 Paxos
Paxos is a family of protocols for solving consensus in a network of unreliable processors. Key concepts:
- Proposers suggest values
- Acceptors choose which values to accept
- Learners learn the chosen value

Phases:
1. Prepare: Proposer asks acceptors to promise not to accept lower proposals
2. Accept: If majority promises, proposer asks acceptors to accept the value
3. Learn: Once a majority accepts, the value is chosen

### 4.2 Raft
Raft is a consensus algorithm designed to be more understandable than Paxos. It separates the consensus problem into:
- Leader election
- Log replication
- Safety

Raft uses a strong leader model:
- One server acts as leader
- Leader handles all client requests
- Leader replicates log entries to followers

### 4.3 Byzantine Fault Tolerance
Byzantine faults occur when nodes may behave arbitrarily (including maliciously). PBFT (Practical Byzantine Fault Tolerance) handles up to f faulty nodes among 3f+1 total nodes. Used in:
- Blockchain systems
- Distributed ledgers
- Mission-critical systems

## 5. Distributed Transactions

### 5.1 Two-Phase Commit (2PC)
2PC ensures atomicity across distributed resources. Phases:

Phase 1 (Voting):
1. Coordinator sends prepare requests to all participants
2. Participants vote yes (can commit) or no (must abort)

Phase 2 (Decision):
1. If all vote yes, coordinator sends commit
2. If any vote no, coordinator sends abort
3. Participants acknowledge

Problems:
- Blocking if coordinator fails
- Single point of failure
- Expensive in terms of latency

### 5.2 Three-Phase Commit (3PC)
3PC adds a pre-commit phase to reduce blocking:
1. CanCommit: Coordinator asks if participants can commit
2. PreCommit: Participants prepare to commit
3. DoCommit: Final commit or abort

### 5.3 Saga Pattern
For long-running transactions, the Saga pattern breaks operations into a sequence of local transactions. Each has a compensating transaction for rollback. Types:
- Choreography: Services react to events
- Orchestration: Central coordinator manages flow

## 6. Distributed Storage Systems

### 6.1 Distributed Hash Tables (DHT)
DHTs provide a key-value store distributed across many nodes. Examples:
- Chord: Uses consistent hashing with finger tables
- Kademlia: Employs XOR-based distance metric
- Pastry: Uses prefix-based routing

Properties:
- Scalable: O(log n) hops for lookups
- Decentralized: No single point of failure
- Fault-tolerant: Automatic data redistribution

### 6.2 Distributed File Systems
Examples include GFS, HDFS, and Ceph. Key characteristics:
- Large files stored across multiple machines
- Replication for fault tolerance
- Optimized for batch processing
- Write-once-read-many workloads

### 6.3 Distributed Databases
CAP Theorem states that a distributed data store can only guarantee two of:
- Consistency: All nodes have same data
- Availability: Every request receives a response
- Partition tolerance: System works despite network partitions

Examples:
- CP systems: HBase, MongoDB (configured), Redis Cluster
- AP systems: Cassandra, DynamoDB, Couchbase

## 7. Load Balancing and Request Routing

### 7.1 Load Balancing Algorithms
- Round Robin: Distribute requests sequentially
- Least Connections: Send to server with fewest active connections
- IP Hash: Route based on client IP
- Weighted: Assign different capacities to different servers

### 7.2 Service Discovery
Mechanisms for finding service instances:
- DNS-based: Simple but limited flexibility
- Zookeeper: Distributed coordination service
- Consul: Service mesh with health checking
- etcd: Key-value store for configuration and discovery

### 7.3 Circuit Breakers
Prevent cascade failures by:
- Monitoring failure rates
- Opening circuit when threshold exceeded
- Failing fast instead of waiting
- Periodically testing if service recovered

## 8. Monitoring and Observability

### 8.1 Metrics
Collect quantitative data:
- Request rate, latency, error rate (RED metrics)
- Utilization, saturation, errors (USE method)
- Business metrics: conversion rate, user engagement

### 8.2 Logging
Structured logging for analysis:
- Correlation IDs for request tracing
- Centralized aggregation (ELK stack, Splunk)
- Log levels and sampling

### 8.3 Distributed Tracing
Follow requests across service boundaries:
- OpenTelemetry: Vendor-neutral instrumentation
- Jaeger: Open source distributed tracing
- Zipkin: Twitter's tracing system

Trace data includes:
- Spans: Individual operations
- Tags: Key-value metadata
- Logs: Timestamped events within spans

## 9. Security in Distributed Systems

### 9.1 Authentication and Authorization
- OAuth 2.0: Token-based authorization
- OpenID Connect: Identity layer on OAuth 2.0
- Mutual TLS: Certificate-based authentication
- JWT: JSON Web Tokens for stateless auth

### 9.2 Encryption
- TLS/SSL: Transport layer security
- End-to-end encryption: Message-level security
- Key management: HSMs, key rotation

### 9.3 Network Segmentation
- VLANs and subnets
- Micro-segmentation
- Zero Trust architecture

## 10. Cloud-Native Patterns

### 10.1 Microservices
Decompose applications into small, independently deployable services:
- Single responsibility
- Independent scaling
- Technology diversity
- Organizational alignment

Challenges:
- Distributed system complexity
- Data consistency
- Testing and deployment

### 10.2 Containers and Orchestration
- Docker: Application containerization
- Kubernetes: Container orchestration
- Service mesh: Istio, Linkerd for traffic management

### 10.3 Serverless
- Function as a Service (FaaS)
- Event-driven architecture
- Auto-scaling and pay-per-use
- Cold start challenges

### 10.4 Infrastructure as Code
- Terraform: Multi-cloud provisioning
- CloudFormation: AWS-specific
- Pulumi: Programming language-based
- Ansible: Configuration management

---

QUESTION: Based on the above distributed systems architecture guide, explain the trade-offs between strong consistency and eventual consistency, and provide specific use cases where each would be appropriate. Also, describe how the CAP theorem influences this decision.
"""

# Short prompts for speed testing
SHORT_PROMPTS = [
    "What is the capital of France?",
    "Explain quantum computing in one sentence.",
    "Write a Python function to calculate fibonacci numbers.",
    "What are the main benefits of microservices architecture?",
    "Describe the difference between TCP and UDP.",
    "What is the time complexity of quicksort?",
    "Explain how blockchain achieves consensus.",
    "What is the purpose of an index in a database?",
    "How does HTTPS work?",
    "What is the difference between compilation and interpretation?"
]

class APIBenchmark:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.results: List[Dict[str, Any]] = []

    def test_context_handling(self) -> Dict[str, Any]:
        """Test if the model can handle long context"""
        print("\n" + "="*80)
        print("TEST 1: Context Handling Test")
        print("="*80)
        print(f"Sending prompt with ~{len(LONG_CONTEXT_PROMPT)} characters...")
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": LONG_CONTEXT_PROMPT}
            ],
            "stream": False
        }
        
        start_time = time.time()
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=120
            )
            end_time = time.time()
            
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                
                # Check if response mentions key concepts from the context
                key_concepts = ["CAP theorem", "eventual consistency", "strong consistency", 
                              "distributed systems", "trade-offs", "availability"]
                concepts_found = sum(1 for concept in key_concepts if concept.lower() in content.lower())
                
                result = {
                    "test": "context_handling",
                    "success": True,
                    "response_time": round(end_time - start_time, 2),
                    "response_length": len(content),
                    "concepts_found": f"{concepts_found}/{len(key_concepts)}",
                    "preview": content[:300] + "..." if len(content) > 300 else content
                }
                
                print(f"✅ Success! Response time: {result['response_time']}s")
                print(f"✅ Response length: {result['response_length']} chars")
                print(f"✅ Key concepts found: {result['concepts_found']}")
                print(f"\nPreview:\n{result['preview']}\n")
                
                return result
            else:
                result = {
                    "test": "context_handling",
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text}"
                }
                print(f"❌ Failed: {result['error']}")
                return result
                
        except Exception as e:
            result = {
                "test": "context_handling",
                "success": False,
                "error": str(e)
            }
            print(f"❌ Failed: {result['error']}")
            return result

    def test_speed_benchmark(self) -> List[Dict[str, Any]]:
        """Test speed over 10 Q&A rounds"""
        print("\n" + "="*80)
        print("TEST 2: Speed Benchmark (10 Q&A rounds)")
        print("="*80)
        
        results = []
        
        for i, prompt in enumerate(SHORT_PROMPTS, 1):
            print(f"\nRound {i}/10: {prompt[:50]}...")
            
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            }
            
            start_time = time.time()
            try:
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=60
                )
                end_time = time.time()
                
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    response_time = end_time - start_time
                    
                    # Estimate tokens (rough approximation: 4 chars = 1 token)
                    estimated_tokens = len(content) / 4
                    tokens_per_second = estimated_tokens / response_time if response_time > 0 else 0
                    
                    result = {
                        "round": i,
                        "success": True,
                        "response_time": round(response_time, 2),
                        "response_length": len(content),
                        "estimated_tokens": int(estimated_tokens),
                        "tokens_per_second": round(tokens_per_second, 2)
                    }
                    print(f"  ✅ {result['response_time']}s ({result['tokens_per_second']} tok/s)")
                else:
                    result = {
                        "round": i,
                        "success": False,
                        "error": f"HTTP {response.status_code}"
                    }
                    print(f"  ❌ Failed: {result['error']}")
                    
            except Exception as e:
                result = {
                    "round": i,
                    "success": False,
                    "error": str(e)
                }
                print(f"  ❌ Failed: {result['error']}")
            
            results.append(result)
            
            # Small delay between requests
            time.sleep(0.5)
        
        return results

    def analyze_results(self, speed_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze benchmark results"""
        successful = [r for r in speed_results if r.get("success")]
        failed = [r for r in speed_results if not r.get("success")]
        
        if not successful:
            return {"error": "All requests failed"}
        
        response_times = [r["response_time"] for r in successful]
        tokens_per_sec = [r["tokens_per_second"] for r in successful]
        
        analysis = {
            "total_requests": len(speed_results),
            "successful_requests": len(successful),
            "failed_requests": len(failed),
            "success_rate": f"{len(successful)/len(speed_results)*100:.1f}%",
            "response_time": {
                "avg": round(statistics.mean(response_times), 2),
                "min": round(min(response_times), 2),
                "max": round(max(response_times), 2),
                "median": round(statistics.median(response_times), 2)
            },
            "throughput": {
                "avg_tok_per_sec": round(statistics.mean(tokens_per_sec), 2),
                "min_tok_per_sec": round(min(tokens_per_sec), 2),
                "max_tok_per_sec": round(max(tokens_per_sec), 2)
            }
        }
        
        return analysis

    def print_summary(self, context_result: Dict[str, Any], speed_results: List[Dict[str, Any]], analysis: Dict[str, Any]):
        """Print final summary"""
        print("\n" + "="*80)
        print("BENCHMARK SUMMARY")
        print("="*80)
        
        print("\n📊 Context Handling Test:")
        if context_result.get("success"):
            print(f"  ✅ Status: PASSED")
            print(f"  ⏱️  Response Time: {context_result['response_time']}s")
            print(f"  📝 Response Length: {context_result['response_length']} chars")
            print(f"  🎯 Context Understanding: {context_result['concepts_found']} key concepts")
        else:
            print(f"  ❌ Status: FAILED")
            print(f"  💥 Error: {context_result.get('error', 'Unknown')}")
        
        print("\n📊 Speed Benchmark (10 Rounds):")
        print(f"  📈 Success Rate: {analysis['success_rate']}")
        print(f"  ⏱️  Average Response Time: {analysis['response_time']['avg']}s")
        print(f"  ⚡ Min/Max Time: {analysis['response_time']['min']}s / {analysis['response_time']['max']}s")
        print(f"  🚀 Throughput: {analysis['throughput']['avg_tok_per_sec']} tokens/sec (avg)")
        print(f"  🚀 Min/Max Throughput: {analysis['throughput']['min_tok_per_sec']} / {analysis['throughput']['max_tok_per_sec']} tokens/sec")
        
        print("\n📋 Detailed Round Results:")
        for r in speed_results:
            if r.get("success"):
                print(f"  Round {r['round']:2d}: {r['response_time']:5.2f}s | {r['tokens_per_second']:6.2f} tok/s | ✅")
            else:
                print(f"  Round {r['round']:2d}: FAILED - {r.get('error', 'Unknown error')} | ❌")
        
        print("\n" + "="*80)
        print("Benchmark completed!")
        print("="*80)

def main():
    print("🚀 Starting API Benchmark Test")
    print(f"Target: {BASE_URL}")
    print(f"Model: {MODEL}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    benchmark = APIBenchmark(BASE_URL, API_KEY, MODEL)
    
    # Test 1: Context handling
    context_result = benchmark.test_context_handling()
    
    # Test 2: Speed benchmark
    speed_results = benchmark.test_speed_benchmark()
    
    # Analyze results
    analysis = benchmark.analyze_results(speed_results)
    
    # Print summary
    benchmark.print_summary(context_result, speed_results, analysis)
    
    # Save results to file
    results = {
        "timestamp": datetime.now().isoformat(),
        "model": MODEL,
        "base_url": BASE_URL,
        "context_test": context_result,
        "speed_benchmark": {
            "rounds": speed_results,
            "analysis": analysis
        }
    }
    
    with open("benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print("\n💾 Results saved to benchmark_results.json")

if __name__ == "__main__":
    main()
